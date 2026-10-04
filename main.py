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
# 1. CONFIGURATION & DATABASE
# ==========================================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID", "YOUR_CHAT_ID")

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
        
        # Seed default pairs if not present
        if not conn.execute("SELECT 1 FROM liq_settings WHERE key='pairs_seeded'").fetchone():
            default_pairs = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'XRP/USDT']
            conn.executemany("INSERT OR IGNORE INTO liq_pairs (symbol) VALUES (?)", [(s,) for s in default_pairs])
            conn.execute("INSERT INTO liq_settings (key, value) VALUES ('pairs_seeded', '1')")

init_db()

# ==========================================
# 2. TECHNICAL ANALYSIS ENGINE
# ==========================================
async def get_market_data(exch, symbol):
    try:
        bars = await exch.fetch_ohlcv(symbol, timeframe='1h', limit=100)
        if not bars: return None
        
        df = pd.DataFrame(bars, columns=['t', 'o', 'h', 'l', 'c', 'v'])
        
        delta = df['c'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        df['rsi'] = 100 - (100 / (1 + (gain / loss)))
        
        df['ema50'] = df['c'].ewm(span=50).mean()
        df['ema200'] = df['c'].ewm(span=200).mean()

        funding = 0
        if exch.has.get('fetchFundingRate'):
            try:
                funding_data = await exch.fetch_funding_rate(symbol)
                funding = funding_data.get('fundingRate', 0)
            except: pass

        ob = await exch.fetch_order_book(symbol, limit=20)
        
        return {
            'price': df['c'].iloc[-1],
            'rsi': df['rsi'].iloc[-1],
            'ema50': df['ema50'].iloc[-1],
            'ema200': df['ema200'].iloc[-1],
            'vol_24h_avg': df['v'].tail(24).mean(),
            'curr_vol': df['v'].iloc[-1],
            'funding': funding,
            'bid_wall': sum([b[1] for b in ob['bids']]),
            'ask_wall': sum([a[1] for a in ob['asks']])
        }
    except Exception as e:
        print(f"Data Error for {symbol}: {e}")
        return None

# ==========================================
# 3. MONITORING LOOPS
# ==========================================
async def monitor_loop(application):
    while True:
        exch = ccxt.mexc({'enableRateLimit': True})
        try:
            while True:
                # 1. Fetch alerts (Close DB immediately)
                with sqlite3.connect('alerts.db') as conn:
                    conn.row_factory = sqlite3.Row
                    active_alerts = conn.execute("SELECT * FROM alerts WHERE status='ACTIVE'").fetchall()
                
                if not active_alerts:
                    await asyncio.sleep(60)
                    continue

                symbols = list(set([a['symbol'] for a in active_alerts]))
                market_cache = {}
                
                # 2. Process Network calls
                for s in symbols:
                    market_cache[s] = await get_market_data(exch, s)
                    await asyncio.sleep(0.1)

                # 3. Evaluate Logic & Update DB
                with sqlite3.connect('alerts.db') as conn:
                    for a in active_alerts:
                        data = market_cache.get(a['symbol'])
                        if not data: continue
                        
                        triggered, msg = False, ""
                        
                        if a['type'] == 'price':
                            if (a['direction'] == 'above' and data['price'] >= a['target']) or \
                               (a['direction'] == 'below' and data['price'] <= a['target']):
                                triggered, msg = True, f"Price hit target ${a['target']}"

                        elif a['type'] == 'trail':
                            if data['price'] > a['peak_price']:
                                conn.execute("UPDATE alerts SET peak_price=? WHERE id=?", (data['price'], a['id']))
                            elif data['price'] <= a['peak_price'] * (1 - (a['target']/100)):
                                triggered, msg = True, f"Trailing Stop triggered at {a['target']}% drop"

                        elif a['type'] == 'rsi':
                            if data['rsi'] >= a['target'] or data['rsi'] <= (100 - a['target']):
                                triggered, msg = True, f"RSI Alert: RSI is {data['rsi']:.2f}"

                        if triggered:
                            await application.bot.send_message(CHAT_ID, f"🔔 **{a['symbol']} ALERT**\n{msg}\nPrice: ${data['price']}", parse_mode='Markdown')
                            conn.execute("UPDATE alerts SET status='TRIGGERED' WHERE id=?", (a['id'],))
                            
                await asyncio.sleep(60)
        except Exception as e:
            print(f"Monitor Loop Crash: {e}")
            await asyncio.sleep(30)
        finally:
            await exch.close()

# ==========================================
# 4. LIQUIDITY STRATEGY ENGINE
# ==========================================
LIQ_TF_MS = 60 * 60 * 1000
LIQ_PAGE_LIMIT = 500
liq_seen = {}

def liq_now_ms():
    return ccxt.Exchange.milliseconds()

def fmt_price(p):
    return f"{p:,.2f}" if p >= 100 else (f"{p:,.4f}" if p >= 1 else f"{p:.8f}".rstrip('0'))

def liq_get_anchor():
    with sqlite3.connect('alerts.db') as conn:
        row = conn.execute("SELECT value FROM liq_settings WHERE key='anchor_ms'").fetchone()
        if row: return int(row[0])
        anchor = (liq_now_ms() // LIQ_TF_MS) * LIQ_TF_MS - (1000 * LIQ_TF_MS)
        conn.execute("INSERT INTO liq_settings (key, value) VALUES ('anchor_ms', ?)", (str(anchor),))
        return anchor

async def liq_sync_symbol(exch, symbol, anchor):
    with sqlite3.connect('alerts.db') as conn:
        last = conn.execute("SELECT MAX(t) FROM liq_candles WHERE symbol=?", (symbol,)).fetchone()[0]
    
    since = anchor if last is None else last + LIQ_TF_MS
    now_ms = liq_now_ms()
    candles = {}

    while since < now_ms:
        batch = await exch.fetch_ohlcv(symbol, '1h', since=since, limit=LIQ_PAGE_LIMIT)
        if not batch: break
        for c in batch: candles[c[0]] = c
        next_since = batch[-1][0] + LIQ_TF_MS
        if next_since <= since: break
        since = next_since

    rows = [(symbol, *c[:6]) for t, c in sorted(candles.items()) if t + LIQ_TF_MS <= now_ms]
    if rows:
        with sqlite3.connect('alerts.db') as conn:
            conn.executemany("INSERT OR REPLACE INTO liq_candles (symbol, t, o, h, l, c, v) VALUES (?,?,?,?,?,?,?)", rows)

def liq_build_levels(df, atr_period=14, atr_mult=0.5, swing_len=15, min_touches=2):
    """Calculates valid support and resistance zones based on dynamic ATR clustering"""
    if len(df) < swing_len * 2: return []

    df = df.copy().reset_index(drop=True)
    df['prev_c'] = df['c'].shift(1)
    df['tr'] = df[['h', 'l', 'prev_c']].apply(
        lambda x: max(x['h'] - x['l'], abs(x['h'] - x['prev_c']), abs(x['l'] - x['prev_c'])), axis=1
    )
    tol = (df['tr'].rolling(atr_period).mean().iloc[-1]) * atr_mult

    win = swing_len * 2 + 1
    piv_highs = df[df['h'] == df['h'].rolling(win, center=True).max()]['h']
    piv_lows = df[df['l'] == df['l'].rolling(win, center=True).min()]['l']

    def cluster_pivots(pivots, kind):
        if pivots.empty: return []
        
        clusters = []
        curr = []
        
        for idx, price in pivots.sort_values().items():
            if not curr:
                curr.append((idx, price))
            else:
                avg = sum(p for _, p in curr) / len(curr)
                if abs(price - avg) <= tol:
                    curr.append((idx, price))
                else:
                    clusters.append(curr)
                    curr = [(idx, price)]
        if curr: clusters.append(curr)
            
        levels = []
        for c in clusters:
            if len(c) >= min_touches:
                avg_price = sum(p for _, p in c) / len(c)
                last_idx = max(idx for idx, _ in c)
                
                # Verify if the level has been breached since it formed
                if kind == 'high': # Resistance
                    swept = df['h'].iloc[last_idx + 1:].max() > avg_price if last_idx + 1 < len(df) else False
                else: # Support
                    swept = df['l'].iloc[last_idx + 1:].min() < avg_price if last_idx + 1 < len(df) else False
                    
                levels.append({'kind': kind, 'price': avg_price, 'touches': len(c), 'swept': swept, 'tol': tol})
        return levels

    return cluster_pivots(piv_highs, 'high') + cluster_pivots(piv_lows, 'low')

def liq_detect_signal(df):
    if len(df) < 50: return None
    last = df.iloc[-1]
    levels = [lv for lv in liq_build_levels(df.iloc[:-1]) if not lv['swept']]

    # Bearish Sweep: Wick above Resistance, close below it -> SELL Signal
    res_sweep = [lv for lv in levels if lv['kind'] == 'high' and last['h'] > lv['price'] > last['c']]
    
    # Bullish Sweep: Wick below Support, close above it -> BUY Signal
    sup_sweep = [lv for lv in levels if lv['kind'] == 'low' and last['l'] < lv['price'] < last['c']]
    
    if bool(res_sweep) == bool(sup_sweep): return None 

    if sup_sweep:
        side, swept_levels = 'BUY', sup_sweep
        ahead = [lv['price'] for lv in levels if lv['kind'] == 'high' and lv['price'] > last['c']]
        target = min(ahead) if ahead else None
    else:
        side, swept_levels = 'SELL', res_sweep
        ahead = [lv['price'] for lv in levels if lv['kind'] == 'low' and lv['price'] < last['c']]
        target = max(ahead) if ahead else None

    return {
        'side': side,
        'level': max(swept_levels, key=lambda lv: lv['touches']),
        'high': last['h'],
        'low': last['l'],
        'close': last['c'],
        'target': target
    }

async def liq_strategy_loop(application):
    while True:
        exch = ccxt.mexc({'enableRateLimit': True})
        try:
            while True:
                anchor = liq_get_anchor()
                with sqlite3.connect('alerts.db') as conn:
                    pairs = [r[0] for r in conn.execute("SELECT symbol FROM liq_pairs").fetchall()]
                
                for symbol in pairs:
                    await liq_sync_symbol(exch, symbol, anchor)
                    with sqlite3.connect('alerts.db') as conn:
                        df = pd.read_sql_query("SELECT * FROM liq_candles WHERE symbol=? ORDER BY t", conn, params=(symbol,))
                    
                    if df.empty: continue
                    
                    last_t = int(df['t'].iloc[-1])
                    if symbol in liq_seen and last_t > liq_seen[symbol]:
                        sig = liq_detect_signal(df)
                        if sig:
                            buy = sig['side'] == 'BUY'
                            pool = "Support / Buy Zone" if buy else "Resistance / Sell Zone"
                            text = (f"{'🟢' if buy else '🔴'} **{sig['side']} SIGNAL** - {symbol}\n"
                                    f"{pool} rejected!\n"
                                    f"Level: ${fmt_price(sig['level']['price'])}\n"
                                    f"Close: ${fmt_price(sig['close'])}")
                            await application.bot.send_message(CHAT_ID, text, parse_mode='Markdown')
                    liq_seen[symbol] = last_t
                    await asyncio.sleep(0.1)
                
                # Wake up near the start of the next 1h candle
                now_sec = liq_now_ms() / 1000
                tf_sec = LIQ_TF_MS / 1000
                await asyncio.sleep(tf_sec - (now_sec % tf_sec) + 5)
        except Exception as e:
            print(f"Liquidity Loop Crash: {e}")
            await asyncio.sleep(30)
        finally:
            await exch.close()

# ==========================================
# 5. BOT COMMANDS & SERVER
# ==========================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🚀 **System Active.**\nCommands: `/price`, `/trail`, `/liqadd`, `/liqlevels`", parse_mode='Markdown')

async def show_liq_levels(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.message.reply_text("❌ Use: `/liqlevels BTC/USDT`", parse_mode='Markdown')
    
    symbol = context.args[0].upper()
    with sqlite3.connect('alerts.db') as conn:
        df = pd.read_sql_query("SELECT * FROM liq_candles WHERE symbol=? ORDER BY t", conn, params=(symbol,))
    
    if len(df) < 50:
        return await update.message.reply_text(f"Not enough data for {symbol}.")
        
    close = df['c'].iloc[-1]
    levels = liq_build_levels(df)
    
    above = sorted([lv for lv in levels if lv['kind'] == 'high' and lv['price'] > close], key=lambda x: x['price'])[:3]
    below = sorted([lv for lv in levels if lv['kind'] == 'low' and lv['price'] < close], key=lambda x: -x['price'])[:3]

    def line(lv):
        pct = (lv['price'] / close - 1) * 100
        return f"• ${fmt_price(lv['price'])} ({pct:+.2f}%) - {lv['touches']} touches {'💧' if not lv['swept'] else ''}"

    res = f"📊 **{symbol} Levels**\nPrice: ${fmt_price(close)}\n\n"
    res += "**Major Resistance (Sell Zones)**\n" + ("\n".join(line(lv) for lv in reversed(above)) or "None") + "\n\n"
    res += "**Major Support (Buy Zones)**\n" + ("\n".join(line(lv) for lv in below) or "None")
    await update.message.reply_text(res, parse_mode='Markdown')

async def post_init(application):
    asyncio.create_task(monitor_loop(application))
    asyncio.create_task(liq_strategy_loop(application))
    
    server = web.Application()
    server.router.add_get("/", lambda r: web.Response(text="Running!"))
    runner = web.AppRunner(server)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", int(os.environ.get("PORT", 8080))).start()

if __name__ == '__main__':
    app = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("liqlevels", show_liq_levels))
    app.run_polling(drop_pending_updates=True)
