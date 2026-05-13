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
        top_ask_vol = sum([a[1] for a in ob['asks'])

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
# 5. MAIN
# ==========================================
if __name__ == '__main__':
    bot_app = ApplicationBuilder().token(BOT_TOKEN).build()
    
    bot_app.add_handler(CommandHandler("start", start))
    bot_app.add_handler(CommandHandler("price", add_price_alert))
    bot_app.add_handler(CommandHandler("trail", add_trail_alert))
    bot_app.add_handler(CommandHandler("list", list_alerts))
    bot_app.add_handler(CallbackQueryHandler(button_handler))
    
    loop = asyncio.get_event_loop()
    loop.create_task(monitor_loop(bot_app))
    
    print("Bot is starting...")
    bot_app.run_polling()
