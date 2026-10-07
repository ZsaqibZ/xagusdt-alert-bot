import os
import asyncio
import sqlite3
import uuid
import pandas as pd
from datetime import datetime
import ccxt.async_support as ccxt
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes
from aiohttp import web

# ==========================================
# 1. CONFIGURATION & DEFAULT SETTINGS
# ==========================================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID", "YOUR_CHAT_ID")

LIQ_TIMEFRAME     = '1h'               # Runs natively on 1h candles
LIQ_TF_MS         = 60 * 60 * 1000     # 1h in milliseconds
LIQ_BASE_LOOKBACK = 2000               # Base historical lookback window (1000 candles)
LIQ_PIVOT_LEN     = 12                 # Bars on each side to confirm a swing fractal
LIQ_ATR_LEN       = 14                 # ATR period for cluster sizing
LIQ_CLUSTER_ATR   = 0.35               # Tolerance multiplier: cluster width = 0.35 * ATR
LIQ_MIN_TOUCHES   = 2                  # Minimum swing touches required to validate a level
LIQ_LEVELS_SHOWN  = 5                  # Top levels displayed in /liqlevels
LIQ_PAGE_LIMIT    = 500                # MEXC pagination batch size
LIQ_SCAN_DELAY    = 5                  # Delay (seconds) after candle close before scanning

# Exactly 20 default crypto pairs seeded on initial launch
LIQ_DEFAULT_PAIRS = [
    'BTC/USDT',  'ETH/USDT',  'SOL/USDT',  'BNB/USDT',  'XRP/USDT',
    'DOGE/USDT', 'ADA/USDT',  'SUI/USDT',  'LINK/USDT','NEAR/USDT', 
    'APT/USDT',  'LTC/USDT',  'BCH/USDT','TRX/USDT',  'GOLD(XAUT)USDT',
]

liq_seen = {}

# ==========================================
# 2. DATABASE INITIALIZATION
# ==========================================
def init_db():
    with sqlite3.connect('alerts.db') as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS alerts
                     (id TEXT PRIMARY KEY, symbol TEXT, type TEXT, target REAL, 
                      direction TEXT, peak_price REAL, start_price REAL,
                      expiry_time REAL, status TEXT)''')
        conn.execute("CREATE TABLE IF NOT EXISTS liq_settings (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute("CREATE TABLE IF NOT EXISTS liq_pairs (symbol TEXT PRIMARY KEY)")
        conn.execute('''CREATE TABLE IF NOT EXISTS liq_candles
                        (symbol TEXT, t INTEGER, o REAL, h REAL, l REAL, c REAL, v REAL,
                         PRIMARY KEY (symbol, t))''')
        conn.execute("CREATE TABLE IF NOT EXISTS liq_signals (symbol TEXT, side TEXT, price REAL, t INTEGER)")
        
        # Seed the 20 pairs on first run
        if not conn.execute("SELECT 1 FROM liq_settings WHERE key='pairs_seeded'").fetchone():
            conn.executemany("INSERT OR IGNORE INTO liq_pairs (symbol) VALUES (?)", [(s,) for s in LIQ_DEFAULT_PAIRS])
            conn.execute("INSERT INTO liq_settings (key, value) VALUES ('pairs_seeded', '1')")

init_db()

def liq_now_ms():
    return ccxt.Exchange.milliseconds()

def fmt_price(p):
    if p >= 100: return f"{p:,.2f}"
    if p >= 1:   return f"{p:,.4f}"
    return f"{p:.8f}".rstrip('0')

def liq_get_anchor():
    """Returns fixed start timestamp. Saved on first boot so lookback grows monotonically."""
    with sqlite3.connect('alerts.db') as conn:
        row = conn.execute("SELECT value FROM liq_settings WHERE key='anchor_ms'").fetchone()
        if row:
            return int(row[0])
        anchor = (liq_now_ms() // LIQ_TF_MS) * LIQ_TF_MS - (LIQ_BASE_LOOKBACK * LIQ_TF_MS)
        conn.execute("INSERT INTO liq_settings (key, value) VALUES ('anchor_ms', ?)", (str(anchor),))
        return anchor

def liq_get_pairs():
    with sqlite3.connect('alerts.db') as conn:
        return [r[0] for r in conn.execute("SELECT symbol FROM liq_pairs ORDER BY rowid").fetchall()]

# ==========================================
# 3. DATA SYNC & TECHNICAL ANALYSIS ENGINE
# ==========================================
async def liq_sync_symbol(exch, symbol, anchor):
    """Syncs missing closed candles from exchange directly into SQLite."""
    with sqlite3.connect('alerts.db') as conn:
        last = conn.execute("SELECT MAX(t) FROM liq_candles WHERE symbol=?", (symbol,)).fetchone()[0]
    
    since = anchor if last is None else last + LIQ_TF_MS
    now_ms = liq_now_ms()
    candles = {}

    while since < now_ms:
        try:
            batch = await exch.fetch_ohlcv(symbol, LIQ_TIMEFRAME, since=since, limit=LIQ_PAGE_LIMIT)
        except Exception as e:
            print(f"Error fetching OHLCV for {symbol}: {e}")
            break
        if not batch:
            break
        for c in batch:
            candles[c[0]] = c
        next_since = batch[-1][0] + LIQ_TF_MS
        if next_since <= since:
            break
        since = next_since

    # Only persist closed candles
    rows = [(symbol, *c[:6]) for t, c in sorted(candles.items()) if t + LIQ_TF_MS <= now_ms]
    if rows:
        with sqlite3.connect('alerts.db') as conn:
            conn.executemany("INSERT OR REPLACE INTO liq_candles (symbol, t, o, h, l, c, v) VALUES (?,?,?,?,?,?,?)", rows)

def liq_load_df(symbol, anchor):
    with sqlite3.connect('alerts.db') as conn:
        return pd.read_sql_query(
            "SELECT t, o, h, l, c, v FROM liq_candles WHERE symbol=? AND t>=? ORDER BY t ASC",
            conn, params=(symbol, anchor)
        )

def liq_build_levels(df):
    """Calculates Support (Buy Zone) and Resistance (Sell Zone) levels with touch verification."""
    if len(df) < (LIQ_PIVOT_LEN * 2 + 1):
        return []

    df = df.reset_index(drop=True)
    n = len(df)
    highs = df['h'].to_numpy()
    lows = df['l'].to_numpy()

    # Wilder's ATR
    prev_c = df['c'].shift(1)
    tr = pd.concat([df['h'] - df['l'], (df['h'] - prev_c).abs(), (df['l'] - prev_c).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / LIQ_ATR_LEN, adjust=False).mean().iloc[-1]
    tol = LIQ_CLUSTER_ATR * atr

    # Swing detection
    win = 2 * LIQ_PIVOT_LEN + 1
    piv_high = df['h'][df['h'] == df['h'].rolling(win, center=True).max()]
    piv_low  = df['l'][df['l'] == df['l'].rolling(win, center=True).min()]

    def cluster(pivots, kind):
        groups, cur = [], []
        for idx, price in sorted(pivots.items(), key=lambda kv: kv[1]):
            if cur and price - cur[0][1] > tol:
                groups.append(cur)
                cur = []
            cur.append((idx, price))
        if cur:
            groups.append(cur)

        out = []
        for g in groups:
            if len(g) < LIQ_MIN_TOUCHES:
                continue
            last_idx = max(i for i, _ in g)
            if kind == 'resistance':
                price = max(p for _, p in g)
                # Swept if any subsequent close or wick broke cleanly above the resistance zone
                swept = (last_idx + 1 < n) and (highs[last_idx + 1:].max() > price)
            else:
                price = min(p for _, p in g)
                # Swept if any subsequent close or wick broke cleanly below the support zone
                swept = (last_idx + 1 < n) and (lows[last_idx + 1:].min() < price)
            out.append({'kind': kind, 'price': price, 'touches': len(g), 'swept': bool(swept), 'tol': tol})
        return out

    return cluster(piv_high, 'resistance') + cluster(piv_low, 'support')

def liq_detect_signal(df):
    """Detects liquidity sweeps and rejections.
    - Support swept (wick below support floor, close back above) -> BUY
    - Resistance swept (wick above resistance ceiling, close back below) -> SELL
    """
    if len(df) < 50:
        return None

    last = df.iloc[-1]
    levels = [lv for lv in liq_build_levels(df.iloc[:-1]) if not lv['swept']]

    # Bullish: price pierced Support, but closed above it
    support_sweeps = [lv for lv in levels if lv['kind'] == 'support' and last['l'] < lv['price'] < last['c']]
    
    # Bearish: price pierced Resistance, but closed below it
    resistance_sweeps = [lv for lv in levels if lv['kind'] == 'resistance' and last['h'] > lv['price'] > last['c']]

    if bool(support_sweeps) == bool(resistance_sweeps):
        return None

    if support_sweeps:
        side = 'BUY'
        swept = support_sweeps
        ahead = [lv['price'] for lv in levels if lv['kind'] == 'resistance' and lv['price'] > last['c']]
        target = min(ahead) if ahead else None
    else:
        side = 'SELL'
        swept = resistance_sweeps
        ahead = [lv['price'] for lv in levels if lv['kind'] == 'support' and lv['price'] < last['c']]
        target = max(ahead) if ahead else None

    return {
        'side': side,
        'level': max(swept, key=lambda lv: lv['touches']),
        'count': len(swept),
        'high': last['h'],
        'low': last['l'],
        'close': last['c'],
        'target': target,
    }

# ==========================================
# 4. MESSAGE FORMATTERS
# ==========================================
def format_liq_signal(symbol, sig):
    buy = sig['side'] == 'BUY'
    icon = "🟢" if buy else "🔴"
    zone = "Major Support / Buy Zone" if buy else "Major Resistance / Sell Zone"
    wick = f"Wick Low: ${fmt_price(sig['low'])}" if buy else f"Wick High: ${fmt_price(sig['high'])}"
    lv = sig['level']

    text = (
        f"{icon} **{sig['side']} SIGNAL: {symbol}** ({LIQ_TIMEFRAME})\n"
        f"Sweep & Rejection at {zone}\n\n"
        f"**Level:** ${fmt_price(lv['price'])} ({lv['touches']} confirmed touches)\n"
        f"{wick} | **Close:** ${fmt_price(sig['close'])}"
    )
    if sig['target']:
        pct = (sig['target'] / sig['close'] - 1) * 100
        text += f"\n**Target ({'Resistance' if buy else 'Support'}):** ${fmt_price(sig['target'])} ({pct:+.2f}%)"
    return text

def format_liq_levels(symbol, df):
    close = df['c'].iloc[-1]
    levels = liq_build_levels(df)
    
    # Resistance = Sell Zones (above current price)
    resistances = sorted([lv for lv in levels if lv['kind'] == 'resistance' and lv['price'] > close], key=lambda lv: lv['price'])
    # Support = Buy Zones (below current price)
    supports = sorted([lv for lv in levels if lv['kind'] == 'support' and lv['price'] < close], key=lambda lv: -lv['price'])
    
    above = resistances[:LIQ_LEVELS_SHOWN]
    below = supports[:LIQ_LEVELS_SHOWN]

    def line(lv):
        pct = (lv['price'] / close - 1) * 100
        mark = " 💧 (Active Pool)" if not lv['swept'] else ""
        return f"• ${fmt_price(lv['price'])} ({pct:+.2f}%) — {lv['touches']} touches{mark}"

    return (
        f"📊 **{symbol} Levels Overview** | {LIQ_TIMEFRAME}\n"
        f"Historical Memory: {len(df):,} candles\n\n"
        f"🔴 **Major Resistance / Sell Zones** ({len(above)} shown):\n" +
        ("\n".join(line(lv) for lv in reversed(above)) or "• None detected") + "\n\n"
        f"💵 **Current Price:** ${fmt_price(close)}\n\n"
        f"🟢 **Major Support / Buy Zones** ({len(below)} shown):\n" +
        ("\n".join(line(lv) for lv in below) or "• None detected") + "\n\n"
        f"💧 = Unswept resting liquidity pool"
    )

# ==========================================
# 5. BACKGROUND ENGINE LOOPS
# ==========================================
async def liq_strategy_loop(application):
    exch = ccxt.mexc({'enableRateLimit': True})
    tf_sec = LIQ_TF_MS // 1000
    try:
        while True:
            anchor = liq_get_anchor()
            pairs = liq_get_pairs()
            for symbol in pairs:
                try:
                    await liq_sync_symbol(exch, symbol, anchor)
                    df = liq_load_df(symbol, anchor)
                    if df.empty:
                        continue

                    last_t = int(df['t'].iloc[-1])
                    if symbol in liq_seen and last_t > liq_seen[symbol]:
                        sig = liq_detect_signal(df)
                        if sig:
                            with sqlite3.connect('alerts.db') as conn:
                                row = conn.execute(
                                    "SELECT 1 FROM liq_signals WHERE symbol=? AND side=? AND ABS(price - ?) <= ? AND t >= ?",
                                    (symbol, sig['side'], sig['level']['price'], sig['level']['tol'], last_t - 24 * LIQ_TF_MS)
                                ).fetchone()
                            if not row:
                                await application.bot.send_message(CHAT_ID, format_liq_signal(symbol, sig), parse_mode='Markdown')
                                with sqlite3.connect('alerts.db') as conn:
                                    conn.execute("INSERT INTO liq_signals VALUES (?,?,?,?)", (symbol, sig['side'], sig['level']['price'], last_t))
                    liq_seen[symbol] = last_t
                except Exception as e:
                    print(f"Error scanning {symbol}: {e}")
                await asyncio.sleep(0.1)

            # Wait until next 1h candle completion
            now_sec = liq_now_ms() / 1000
            await asyncio.sleep(tf_sec - (now_sec % tf_sec) + LIQ_SCAN_DELAY)
    finally:
        await exch.close()

# ==========================================
# 6. TELEGRAM COMMAND HANDLERS
# ==========================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "🚀 **Pro Liquidity & Support/Resistance System**\n\n"
        "**Commands:**\n"
        "• `/liqlevels BTC/USDT` — Show nearest Support & Resistance zones\n"
        "• `/liqlist` — List all monitored trading pairs\n"
        "• `/liqadd SOL/USDT` — Add a new pair to tracking\n"
        "• `/liqdel PEPE/USDT` — Remove a pair from tracking\n"
        "• `/price BTC/USDT 70000` — Simple price alert\n"
        "• `/trail ETH/USDT 5` — 5% Trailing stop alert\n"
        "• `/list` — View active price & trail alerts"
    )
    await update.message.reply_text(msg, parse_mode='Markdown')

async def show_liq_levels(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.message.reply_text("❌ Usage: `/liqlevels BTC/USDT`", parse_mode='Markdown')

    symbol = context.args[0].upper()
    df = liq_load_df(symbol, liq_get_anchor())
    if len(df) < 50:
        return await update.message.reply_text(f"⚠️ Insufficient data for {symbol}. Add and sync it first using `/liqadd {symbol}`.")

    await update.message.reply_text(format_liq_levels(symbol, df), parse_mode='Markdown')

async def list_liq_pairs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pairs = liq_get_pairs()
    anchor = liq_get_anchor()
    candles = (liq_now_ms() - anchor) // LIQ_TF_MS
    since_date = pd.to_datetime(anchor, unit='ms').strftime('%d %b %Y %H:%M')

    msg = (
        f"📈 **Active Pairs Monitored ({len(pairs)}):**\n"
        f"**Base History:** {candles:,} candles loaded (since {since_date} UTC)\n"
        f"**Timeframe:** 1 Hour (Continuous growth)\n\n" +
        "\n".join(f"• `{p}`" for p in pairs)
    )
    await update.message.reply_text(msg, parse_mode='Markdown')

async def add_liq_pair(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.message.reply_text("❌ Usage: `/liqadd SUI/USDT`", parse_mode='Markdown')

    symbol = context.args[0].upper()
    anchor = liq_get_anchor()
    await update.message.reply_text(f"⏳ Synchronizing {symbol} historical candles...")

    exch = ccxt.mexc({'enableRateLimit': True})
    try:
        await liq_sync_symbol(exch, symbol, anchor)
        df = liq_load_df(symbol, anchor)
        if df.empty:
            raise ValueError("No data returned from exchange.")

        with sqlite3.connect('alerts.db') as conn:
            conn.execute("INSERT OR IGNORE INTO liq_pairs (symbol) VALUES (?)", (symbol,))
        
        liq_seen[symbol] = int(df['t'].iloc[-1])
        await update.message.reply_text(f"✅ `{symbol}` added successfully with {len(df):,} historical candles.", parse_mode='Markdown')
    except Exception as e:
        await update.message.reply_text(f"❌ Failed to add {symbol}: {e}")
    finally:
        await exch.close()

async def remove_liq_pair(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.message.reply_text("❌ Usage: `/liqdel SUI/USDT`", parse_mode='Markdown')

    symbol = context.args[0].upper()
    with sqlite3.connect('alerts.db') as conn:
        deleted = conn.execute("DELETE FROM liq_pairs WHERE symbol=?", (symbol,)).rowcount
        conn.execute("DELETE FROM liq_candles WHERE symbol=?", (symbol,))

    liq_seen.pop(symbol, None)
    if deleted:
        await update.message.reply_text(f"🗑️ `{symbol}` and its candle history removed.", parse_mode='Markdown')
    else:
        await update.message.reply_text(f"⚠️ `{symbol}` was not in the monitoring list.", parse_mode='Markdown')

async def add_price_alert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        symbol, target = context.args[0].upper(), float(context.args[1])
        exch = ccxt.mexc()
        ticker = await exch.fetch_ticker(symbol)
        await exch.close()
        
        direction = 'above' if target > ticker['last'] else 'below'
        with sqlite3.connect('alerts.db') as conn:
            conn.execute("INSERT INTO alerts (id, symbol, type, target, direction, status) VALUES (?,?,?,?,?,'ACTIVE')",
                         (str(uuid.uuid4())[:8], symbol, 'price', target, direction))
        await update.message.reply_text(f"✅ Price alert set for {symbol} at ${target}")
    except Exception:
        await update.message.reply_text("❌ Usage: `/price BTC/USDT 70000`")

async def add_trail_alert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        symbol, pct = context.args[0].upper(), float(context.args[1])
        exch = ccxt.mexc()
        ticker = await exch.fetch_ticker(symbol)
        await exch.close()

        with sqlite3.connect('alerts.db') as conn:
            conn.execute("INSERT INTO alerts (id, symbol, type, target, peak_price, status) VALUES (?,?,?,?,?,'ACTIVE')",
                         (str(uuid.uuid4())[:8], symbol, 'trail', pct, ticker['last']))
        await update.message.reply_text(f"✅ Trailing alert set for {symbol} at {pct}% drop")
    except Exception:
        await update.message.reply_text("❌ Usage: `/trail SOL/USDT 5`")

async def list_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with sqlite3.connect('alerts.db') as conn:
        conn.row_factory = sqlite3.Row
        alerts = conn.execute("SELECT * FROM alerts WHERE status='ACTIVE'").fetchall()

    if not alerts:
        return await update.message.reply_text("No active alerts.")

    for a in alerts:
        keyboard = [[InlineKeyboardButton("🗑 Delete", callback_data=f"del_{a['id']}")]]
        await update.message.reply_text(
            f"📍 `{a['symbol']}` — {a['type'].upper()}\nTarget: {a['target']}",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data.startswith("del_"):
        aid = query.data.split("_")[1]
        with sqlite3.connect('alerts.db') as conn:
            conn.execute("DELETE FROM alerts WHERE id=?", (aid,))
        await query.edit_message_text("✅ Alert deleted.")

# ==========================================
# 7. LIFECYCLE & SERVER STARTUP
# ==========================================
async def health_check(request):
    return web.Response(text="Bot is running!")

async def post_init(application):
    asyncio.create_task(liq_strategy_loop(application))
    
    port = int(os.environ.get("PORT", 8080))
    server = web.Application()
    server.router.add_get("/", health_check)
    runner = web.AppRunner(server)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"Health check server active on port {port}")

if __name__ == '__main__':
    bot_app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    bot_app.add_handler(CommandHandler("start", start))
    bot_app.add_handler(CommandHandler("price", add_price_alert))
    bot_app.add_handler(CommandHandler("trail", add_trail_alert))
    bot_app.add_handler(CommandHandler("list", list_alerts))
    bot_app.add_handler(CommandHandler("liqlevels", show_liq_levels))
    bot_app.add_handler(CommandHandler("liqlist", list_liq_pairs))
    bot_app.add_handler(CommandHandler("liqadd", add_liq_pair))
    bot_app.add_handler(CommandHandler("liqdel", remove_liq_pair))
    bot_app.add_handler(CallbackQueryHandler(button_handler))

    print("Bot is starting...")
    bot_app.run_polling(drop_pending_updates=True)
