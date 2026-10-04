import os
import asyncio
import sqlite3
import uuid
import pandas as pd
from datetime import datetime, timedelta
import ccxt.async_support as ccxt
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes

# ==========================================
# 1. CONFIGURATION & DATABASE
# ==========================================
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

def init_db():
    conn = sqlite3.connect('alerts.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS alerts
                 (id TEXT PRIMARY KEY, symbol TEXT, type TEXT, target REAL, 
                  direction TEXT, peak_price REAL, start_price REAL,
                  expiry_time REAL, status TEXT)''')
    conn.commit()
    conn.close()

init_db()

# ==========================================
# 2. TECHNICAL ANALYSIS ENGINE
# ==========================================
async def get_market_data(exch, symbol):
    """Fetches OHLCV, Orderbook, and Funding for comprehensive analysis"""
    try:
        # 1. Price & Indicators (OHLCV)
        bars = await exch.fetch_ohlcv(symbol, timeframe='1h', limit=100)
        df = pd.DataFrame(bars, columns=['t', 'o', 'h', 'l', 'c', 'v'])
        
        # RSI
        delta = df['c'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        df['rsi'] = 100 - (100 / (1 + (gain / loss)))
        
        # EMA
        df['ema50'] = df['c'].ewm(span=50).mean()
        df['ema200'] = df['c'].ewm(span=200).mean()

        # 2. Funding Rate (TIT 4)
        funding = 0
        try:
            funding_data = await exch.fetch_funding_rate(symbol)
            funding = funding_data['fundingRate']
        except: pass

        # 3. Order Book Walls (TIT 5)
        ob = await exch.fetch_order_book(symbol, limit=20)
        top_bid_vol = sum([b[1] for b in ob['bids']])
        top_ask_vol = sum([a[1] for a in ob['asks']])

        return {
            'price': df['c'].iloc[-1],
            'rsi': df['rsi'].iloc[-1],
            'ema50': df['ema50'].iloc[-1],
            'ema200': df['ema200'].iloc[-1],
            'vol_24h_avg': df['v'].tail(24).mean(),
            'curr_vol': df['v'].iloc[-1],
            'funding': funding,
            'bid_wall': top_bid_vol,
            'ask_wall': top_ask_vol
        }
    except Exception as e:
        print(f"Data Error for {symbol}: {e}")
        return None

# ==========================================
# 3. MONITORING LOOP
# ==========================================
async def monitor_loop(application):
    exch = ccxt.mexc({'enableRateLimit': True})
    while True:
        try:
            conn = sqlite3.connect('alerts.db')
            conn.row_factory = sqlite3.Row
            active_alerts = conn.execute("SELECT * FROM alerts WHERE status='ACTIVE'").fetchall()
            
            # Group by symbol to save API calls
            symbols = list(set([a['symbol'] for a in active_alerts]))
            market_cache = {}
            for s in symbols:
                market_cache[s] = await get_market_data(exch, s)
                await asyncio.sleep(0.1)

            for a in active_alerts:
                data = market_cache.get(a['symbol'])
                if not data: continue
                
                triggered = False
                msg = ""

                # APVA 5: Expiry Check
                if a['expiry_time'] and datetime.utcnow().timestamp() > a['expiry_time']:
                    conn.execute("UPDATE alerts SET status='EXPIRED' WHERE id=?", (a['id'],))
                    await application.bot.send_message(CHAT_ID, f"⏳ Alert for {a['symbol']} expired.")
                    continue

                # --- APVA & TIT LOGIC ---
                if a['type'] == 'price':
                    if (a['direction'] == 'above' and data['price'] >= a['target']) or \
                       (a['direction'] == 'below' and data['price'] <= a['target']):
                        triggered, msg = True, f"Price hit target ${a['target']}"

                elif a['type'] == 'trail': # APVA 2
                    if data['price'] > a['peak_price']:
                        conn.execute("UPDATE alerts SET peak_price=? WHERE id=?", (data['price'], a['id']))
                    elif data['price'] <= a['peak_price'] * (1 - (a['target']/100)):
                        triggered, msg = True, f"Trailing Stop triggered at {a['target']}% drop"

                elif a['type'] == 'volatility': # APVA 3
                    pct_change = ((data['price'] - a['start_price']) / a['start_price']) * 100
                    if abs(pct_change) >= a['target']:
                        triggered, msg = True, f"Volatility Alert: Price moved {pct_change:.2f}%"

                elif a['type'] == 'rsi': # TIT 1
                    if data['rsi'] >= a['target'] or data['rsi'] <= (100 - a['target']):
                        triggered, msg = True, f"RSI Alert: RSI is {data['rsi']:.2f}"

                elif a['type'] == 'ema': # TIT 2
                    if (a['direction'] == 'cross_up' and data['ema50'] > data['ema200']) or \
                       (a['direction'] == 'cross_down' and data['ema50'] < data['ema200']):
                        triggered, msg = True, "EMA 50/200 Crossover detected!"

                elif a['type'] == 'spike': # TIT 3
                    if data['curr_vol'] > data['vol_24h_avg'] * a['target']:
                        triggered, msg = True, f"Volume Spike: {a['target']}x higher than avg"

                elif a['type'] == 'funding': # TIT 4
                    if abs(data['funding']) >= a['target']:
                        triggered, msg = True, f"Funding Warning: Rate is {data['funding']:.4f}"

                if triggered:
                    full_msg = f"🔔 **{a['symbol']} ALERT**\n{msg}\nCurrent Price: ${data['price']}"
                    await application.bot.send_message(CHAT_ID, full_msg, parse_mode='Markdown')
                    conn.execute("UPDATE alerts SET status='TRIGGERED' WHERE id=?", (a['id'],))
            
            conn.commit()
            conn.close()
            await asyncio.sleep(60)
        except Exception as e:
            print(f"Loop Error: {e}")
            await asyncio.sleep(30)

# ==========================================
# 3B. LIQUIDITY STRATEGY (1H, GROWING LOOKBACK)
# ==========================================
# Settings
LIQ_TIMEFRAME     = '1h'               # strategy runs natively on 1h candles
LIQ_TF_MS         = 60 * 60 * 1000     # one 1h candle in milliseconds
LIQ_BASE_LOOKBACK = 1000               # candles at first start, +1 for every new closed candle
LIQ_PIVOT_LEN     = 5                  # candles on each side that confirm a swing high / low
LIQ_ATR_LEN       = 14                 # ATR length that sizes the cluster tolerance
LIQ_CLUSTER_ATR   = 0.3                # swing points within 0.3 x ATR form one level
LIQ_MIN_TOUCHES   = 2                  # swing points needed for a level (2 = equal highs / lows)
LIQ_MIN_CANDLES   = 100                # minimum history before signals are produced
LIQ_LEVELS_SHOWN  = 3                  # levels per side shown by /liqlevels
LIQ_PAGE_LIMIT    = 500                # candles per exchange request
LIQ_SCAN_DELAY    = 5                  # seconds to wait after a candle closes

# Starting pairs (seeded once on first run, then managed with /liqadd and /liqdel)
LIQ_DEFAULT_PAIRS = [
    'BTC/USDT',  'ETH/USDT',  'BNB/USDT',  'SOL/USDT',  'XRP/USDT',
    'DOGE/USDT', 'ADA/USDT',  'AVAX/USDT', 'TRX/USDT',  'LINK/USDT',
    'DOT/USDT',  'LTC/USDT',  'BCH/USDT',  'ATOM/USDT', 'NEAR/USDT',
    'UNI/USDT',  'SUI/USDT',  'APT/USDT',  'ARB/USDT',  'OP/USDT',
]

liq_seen = {}   # symbol -> open time of the last candle already analysed

def liq_now_ms():
    return ccxt.Exchange.milliseconds()

def init_liq_db():
    conn = sqlite3.connect('alerts.db')
    conn.execute("CREATE TABLE IF NOT EXISTS liq_settings (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS liq_pairs (symbol TEXT PRIMARY KEY)")
    conn.execute('''CREATE TABLE IF NOT EXISTS liq_candles
                    (symbol TEXT, t INTEGER, o REAL, h REAL, l REAL, c REAL, v REAL,
                     PRIMARY KEY (symbol, t))''')
    if not conn.execute("SELECT 1 FROM liq_settings WHERE key='pairs_seeded'").fetchone():
        conn.executemany("INSERT OR IGNORE INTO liq_pairs (symbol) VALUES (?)", [(s,) for s in LIQ_DEFAULT_PAIRS])
        conn.execute("INSERT INTO liq_settings (key, value) VALUES ('pairs_seeded', '1')")
    conn.commit()
    conn.close()

init_liq_db()

def fmt_price(p):
    if p >= 100: return f"{p:,.2f}"
    if p >= 1:   return f"{p:,.4f}"
    return f"{p:.8f}".rstrip('0')

def liq_get_anchor():
    """Start of the lookback window. Saved on the first run so restarts never shrink it."""
    conn = sqlite3.connect('alerts.db')
    row = conn.execute("SELECT value FROM liq_settings WHERE key='anchor_ms'").fetchone()
    if row:
        anchor = int(row[0])
    else:
        anchor = (liq_now_ms() // LIQ_TF_MS) * LIQ_TF_MS - LIQ_BASE_LOOKBACK * LIQ_TF_MS
        conn.execute("INSERT INTO liq_settings (key, value) VALUES ('anchor_ms', ?)", (str(anchor),))
        conn.commit()
    conn.close()
    return anchor

def liq_get_pairs():
    conn = sqlite3.connect('alerts.db')
    pairs = [r[0] for r in conn.execute("SELECT symbol FROM liq_pairs ORDER BY rowid").fetchall()]
    conn.close()
    return pairs

async def liq_sync_symbol(exch, symbol, anchor):
    """Stores every closed 1h candle since the anchor. Only fetches what is missing."""
    conn = sqlite3.connect('alerts.db')
    last = conn.execute("SELECT MAX(t) FROM liq_candles WHERE symbol=?", (symbol,)).fetchone()[0]
    since = anchor if last is None else last + LIQ_TF_MS
    now_ms = liq_now_ms()
    candles = {}

    try:
        while since < now_ms:
            batch = await exch.fetch_ohlcv(symbol, LIQ_TIMEFRAME, since=since, limit=LIQ_PAGE_LIMIT)
            if not batch:
                break
            for c in batch:
                candles[c[0]] = c
            next_since = batch[-1][0] + LIQ_TF_MS
            if next_since <= since:
                break
            since = next_since

        # Drop the candle that is still forming
        rows = [(symbol, *c[:6]) for t, c in sorted(candles.items()) if t + LIQ_TF_MS <= now_ms]
        conn.executemany("INSERT OR REPLACE INTO liq_candles (symbol, t, o, h, l, c, v) VALUES (?,?,?,?,?,?,?)", rows)
        conn.commit()
    finally:
        conn.close()

def liq_load_df(symbol, anchor):
    conn = sqlite3.connect('alerts.db')
    df = pd.read_sql_query("SELECT t, o, h, l, c, v FROM liq_candles WHERE symbol=? AND t>=? ORDER BY t",
                           conn, params=(symbol, anchor))
    conn.close()
    return df

def liq_build_levels(df):
    """Support / resistance levels from clustered swing points.
    kind   : 'high' = resistance / buy-side liquidity, 'low' = support / sell-side liquidity
    price  : outer edge of the cluster (where the resting stops sit)
    touches: swing points in the cluster
    swept  : price has already traded through the level since its last touch"""
    df    = df.reset_index(drop=True)
    n     = len(df)
    highs = df['h'].to_numpy()
    lows  = df['l'].to_numpy()

    # Cluster tolerance from ATR (Wilder smoothing)
    prev_c = df['c'].shift(1)
    tr     = pd.concat([df['h'] - df['l'], (df['h'] - prev_c).abs(), (df['l'] - prev_c).abs()], axis=1).max(axis=1)
    tol    = LIQ_CLUSTER_ATR * tr.ewm(alpha=1 / LIQ_ATR_LEN, adjust=False).mean().iloc[-1]

    # Confirmed swing points (LIQ_PIVOT_LEN candles on both sides; equal extremes all count)
    win      = 2 * LIQ_PIVOT_LEN + 1
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
            if kind == 'high':
                price = max(p for _, p in g)
                swept = last_idx + 1 < n and highs[last_idx + 1:].max() > price
            else:
                price = min(p for _, p in g)
                swept = last_idx + 1 < n and lows[last_idx + 1:].min() < price
            out.append({'kind': kind, 'price': price, 'touches': len(g), 'swept': bool(swept)})
        return out

    return cluster(piv_high, 'high') + cluster(piv_low, 'low')

def liq_detect_signal(df):
    """Liquidity sweep on the last closed candle: wick through a resting level, close back inside.
    Sell-side liquidity swept (wick below support, close above)    -> BUY
    Buy-side liquidity swept (wick above resistance, close below) -> SELL"""
    if len(df) < LIQ_MIN_CANDLES:
        return None

    last   = df.iloc[-1]
    levels = [lv for lv in liq_build_levels(df.iloc[:-1]) if not lv['swept']]

    up   = [lv for lv in levels if lv['kind'] == 'high' and last['h'] > lv['price'] > last['c']]
    down = [lv for lv in levels if lv['kind'] == 'low'  and last['l'] < lv['price'] < last['c']]
    if bool(up) == bool(down):
        return None  # nothing swept, or both sides swept (indecisive candle)

    if down:
        side, swept = 'BUY', down
        ahead  = [lv['price'] for lv in levels if lv['kind'] == 'high' and lv['price'] > last['c']]
        target = min(ahead) if ahead else None
    else:
        side, swept = 'SELL', up
        ahead  = [lv['price'] for lv in levels if lv['kind'] == 'low' and lv['price'] < last['c']]
        target = max(ahead) if ahead else None

    return {
        'side':   side,
        'level':  max(swept, key=lambda lv: lv['touches']),
        'count':  len(swept),
        'high':   last['h'],
        'low':    last['l'],
        'close':  last['c'],
        'target': target,
    }

def format_liq_signal(symbol, sig):
    buy  = sig['side'] == 'BUY'
    icon = "🟢" if buy else "🔴"
    pool = "Sell-side" if buy else "Buy-side"
    wick = f"Wick low: ${fmt_price(sig['low'])}" if buy else f"Wick high: ${fmt_price(sig['high'])}"
    lv   = sig['level']

    text = (
        f"{icon} {sig['side']} - {symbol} ({LIQ_TIMEFRAME})\n"
        f"{pool} liquidity swept\n\n"
        f"Level: ${fmt_price(lv['price'])} ({lv['touches']} touches)\n"
        f"{wick} | Close: ${fmt_price(sig['close'])}"
    )
    if sig['count'] > 1:
        text += f"\nLevels swept: {sig['count']}"
    if sig['target']:
        pct = (sig['target'] / sig['close'] - 1) * 100
        text += f"\nNext {'buy' if buy else 'sell'}-side liquidity: ${fmt_price(sig['target'])} ({pct:+.2f}%)"
    return text

def format_liq_levels(symbol, df):
    close  = df['c'].iloc[-1]
    levels = liq_build_levels(df)
    above  = sorted([lv for lv in levels if lv['price'] > close],  key=lambda lv: lv['price'])[:LIQ_LEVELS_SHOWN]
    below  = sorted([lv for lv in levels if lv['price'] <= close], key=lambda lv: -lv['price'])[:LIQ_LEVELS_SHOWN]

    def line(lv):
        pct  = (lv['price'] / close - 1) * 100
        mark = "" if lv['swept'] else " 💧"
        return f"• ${fmt_price(lv['price'])} ({pct:+.2f}%) - {lv['touches']} touches{mark}"

    return (
        f"📊 {symbol} | {LIQ_TIMEFRAME} | {len(df):,} candles\n\n"
        f"Resistance / Buy-side liquidity\n" + ("\n".join(line(lv) for lv in reversed(above)) or "• none") + "\n\n"
        f"Price: ${fmt_price(close)}\n\n"
        f"Support / Sell-side liquidity\n" + ("\n".join(line(lv) for lv in below) or "• none") + "\n\n"
        f"💧 = liquidity not yet swept"
    )

async def liq_scan(exch, application):
    anchor = liq_get_anchor()
    for symbol in liq_get_pairs():
        try:
            await liq_sync_symbol(exch, symbol, anchor)
            df = liq_load_df(symbol, anchor)
            if df.empty:
                continue

            # A symbol seen for the first time only sets the baseline (no stale signals)
            last_t = int(df['t'].iloc[-1])
            if symbol in liq_seen and last_t > liq_seen[symbol]:
                sig = liq_detect_signal(df)
                if sig:
                    await application.bot.send_message(CHAT_ID, format_liq_signal(symbol, sig))
            liq_seen[symbol] = last_t
        except Exception as e:
            print(f"Liquidity Scan Error for {symbol}: {e}")
        await asyncio.sleep(0.1)

async def liq_strategy_loop(application):
    exch = ccxt.mexc({'enableRateLimit': True})
    tf_sec = LIQ_TF_MS // 1000
    while True:
        try:
            await liq_scan(exch, application)
        except Exception as e:
            print(f"Liquidity Loop Error: {e}")
        # Levels only change when a candle closes, so wake up right after each close
        now_sec = liq_now_ms() / 1000
        await asyncio.sleep(tf_sec - (now_sec % tf_sec) + LIQ_SCAN_DELAY)

# ==========================================
# 4. BOT COMMANDS (THE NEW GUI)
# ==========================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🚀 **Pro Crypto Alert Bot**\n\n"
        "**Usage Examples:**\n"
        "• `/price BTC/USDT 70000` (Simple Alert)\n"
        "• `/trail SOL/USDT 5` (5% Trailing Stop)\n"
        "• `/vol ETH/USDT 3` (3% Change Alert)\n"
        "• `/rsi BTC/USDT 70` (RSI Extreme Alert)\n"
        "• `/spike PEPE/USDT 2` (2x Volume Spike)\n"
        "• `/list` (Manage Alerts)"
    )
    await update.message.reply_text(text, parse_mode='Markdown')

async def add_price_alert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        symbol, target = context.args[0].upper(), float(context.args[1])
        exch = ccxt.mexc()
        ticker = await exch.fetch_ticker(symbol)
        direction = 'above' if target > ticker['last'] else 'below'
        
        conn = sqlite3.connect('alerts.db')
        conn.execute("INSERT INTO alerts (id, symbol, type, target, direction, status) VALUES (?,?,?,?,?,'ACTIVE')",
                     (str(uuid.uuid4())[:8], symbol, 'price', target, direction))
        conn.commit()
        await update.message.reply_text(f"✅ Price alert set for {symbol} at {target}")
    except:
        await update.message.reply_text("❌ Use: `/price BTC/USDT 70000`")

async def add_trail_alert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        symbol, pct = context.args[0].upper(), float(context.args[1])
        exch = ccxt.mexc()
        ticker = await exch.fetch_ticker(symbol)
        
        conn = sqlite3.connect('alerts.db')
        conn.execute("INSERT INTO alerts (id, symbol, type, target, peak_price, status) VALUES (?,?,?,?,?,'ACTIVE')",
                     (str(uuid.uuid4())[:8], symbol, 'trail', pct, ticker['last']))
        conn.commit()
        await update.message.reply_text(f"✅ Trailing stop set for {symbol} at {pct}% drop")
    except:
        await update.message.reply_text("❌ Use: `/trail BTC/USDT 5` (5% trail)")

async def list_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect('alerts.db')
    conn.row_factory = sqlite3.Row
    alerts = conn.execute("SELECT * FROM alerts WHERE status='ACTIVE'").fetchall()
    
    if not alerts:
        await update.message.reply_text("No active alerts.")
        return

    for a in alerts:
        keyboard = [[InlineKeyboardButton("🗑 Delete", callback_data=f"del_{a['id']}")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(f"📍 {a['symbol']} - {a['type'].upper()}\nTarget: {a['target']}", reply_markup=reply_markup)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data.startswith("del_"):
        aid = query.data.split("_")[1]
        conn = sqlite3.connect('alerts.db')
        conn.execute("DELETE FROM alerts WHERE id=?", (aid,))
        conn.commit()
        await query.edit_message_text("✅ Alert deleted.")

# ==========================================
# 4B. LIQUIDITY STRATEGY COMMANDS
# ==========================================
async def add_liq_pair(update: Update, context: ContextTypes.DEFAULT_TYPE):
    exch = ccxt.mexc({'enableRateLimit': True})
    try:
        symbol = context.args[0].upper()
        anchor = liq_get_anchor()
        await update.message.reply_text(f"⏳ Loading {LIQ_TIMEFRAME} candles for {symbol}...")

        await liq_sync_symbol(exch, symbol, anchor)
        df = liq_load_df(symbol, anchor)
        if df.empty:
            raise ValueError("no candle data")

        conn = sqlite3.connect('alerts.db')
        conn.execute("INSERT OR IGNORE INTO liq_pairs (symbol) VALUES (?)", (symbol,))
        conn.commit()
        conn.close()

        liq_seen[symbol] = int(df['t'].iloc[-1])
        await update.message.reply_text(f"✅ {symbol} added to the liquidity strategy ({len(df):,} candles loaded)")
    except Exception:
        await update.message.reply_text("❌ Use: `/liqadd BTC/USDT` (pair must exist on MEXC spot)", parse_mode='Markdown')
    finally:
        await exch.close()

async def remove_liq_pair(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        symbol = context.args[0].upper()
    except IndexError:
        await update.message.reply_text("❌ Use: `/liqdel BTC/USDT`", parse_mode='Markdown')
        return

    conn = sqlite3.connect('alerts.db')
    removed = conn.execute("DELETE FROM liq_pairs WHERE symbol=?", (symbol,)).rowcount
    conn.execute("DELETE FROM liq_candles WHERE symbol=?", (symbol,))
    conn.commit()
    conn.close()
    liq_seen.pop(symbol, None)

    if removed:
        await update.message.reply_text(f"✅ {symbol} removed from the liquidity strategy")
    else:
        await update.message.reply_text(f"{symbol} is not in the liquidity strategy.")

async def list_liq_pairs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pairs   = liq_get_pairs()
    anchor  = liq_get_anchor()
    candles = liq_now_ms() // LIQ_TF_MS - anchor // LIQ_TF_MS
    since   = pd.to_datetime(anchor, unit='ms').strftime('%d %b %Y %H:%M')

    await update.message.reply_text(
        f"📈 Liquidity Strategy | {LIQ_TIMEFRAME}\n"
        f"Lookback: {candles:,} candles (since {since} UTC)\n\n"
        f"Pairs ({len(pairs)}):\n" + "\n".join(f"• {s}" for s in pairs)
    )

async def show_liq_levels(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        symbol = context.args[0].upper()
    except IndexError:
        await update.message.reply_text("❌ Use: `/liqlevels BTC/USDT`", parse_mode='Markdown')
        return

    df = liq_load_df(symbol, liq_get_anchor())
    if len(df) < LIQ_MIN_CANDLES:
        await update.message.reply_text(f"No data for {symbol} yet. Add it with /liqadd {symbol}")
        return
    await update.message.reply_text(format_liq_levels(symbol, df))

# ==========================================
# 5. MAIN
# ==========================================
if __name__ == '__main__':
    bot_app = ApplicationBuilder().token(BOT_TOKEN).build()
    
    bot_app.add_handler(CommandHandler("start", start))
    bot_app.add_handler(CommandHandler("price", add_price_alert))
    bot_app.add_handler(CommandHandler("trail", add_trail_alert))
    bot_app.add_handler(CommandHandler("list", list_alerts))
    bot_app.add_handler(CommandHandler("liqadd", add_liq_pair))
    bot_app.add_handler(CommandHandler("liqdel", remove_liq_pair))
    bot_app.add_handler(CommandHandler("liqlist", list_liq_pairs))
    bot_app.add_handler(CommandHandler("liqlevels", show_liq_levels))
    bot_app.add_handler(CallbackQueryHandler(button_handler))
    
    loop = asyncio.get_event_loop()
    loop.create_task(monitor_loop(bot_app))
    loop.create_task(liq_strategy_loop(bot_app))
    
    print("Bot is starting...")
    bot_app.run_polling()
