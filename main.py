import os
import ccxt.async_support as ccxt
import pandas as pd
import numpy as np
import asyncio
import pytz
from datetime import datetime
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from flask import Flask
from threading import Thread

# ==========================================
# 1. CONFIGURATION
# ==========================================

GOLD_SYMBOL = 'XAUT/USDT:USDT'
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

# Changed to 5m to generate the requested 10-20 scalping signals per day
TIMEFRAME = '15m' 
last_signal_time = None

# Exchange Setup
exchange = ccxt.mexc({
    'enableRateLimit': True,
    'options': {'defaultType': 'swap'}
})

# ==========================================
# 2. RENDER KEEP-ALIVE SERVER
# ==========================================

app = Flask('')

@app.route('/')
def home(): 
    return "Golden RSI Scalp Bot is Running!"

def run_http(): 
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))

def keep_alive(): 
    Thread(target=run_http, daemon=True).start()

# ==========================================
# 3. QUANT INDICATOR MATH (RSI & ATR ONLY)
# ==========================================

def calculate_indicators(df):
    # 1. RSI 14
    delta = df['close'].diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    avg_gain = gain.ewm(alpha=1/14, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/14, adjust=False).mean()
    rs = avg_gain / avg_loss
    df['rsi'] = 100 - (100 / (1 + rs))

    # 2. ATR 14 (Dynamic Risk Management)
    df['tr0'] = abs(df['high'] - df['low'])
    df['tr1'] = abs(df['high'] - df['close'].shift(1))
    df['tr2'] = abs(df['low'] - df['close'].shift(1))
    df['tr'] = df[['tr0', 'tr1', 'tr2']].max(axis=1)
    df['atr'] = df['tr'].rolling(window=14).mean()
    
    return df

# ==========================================
# 4. STRATEGY LOGIC (PURE RSI SCALP)
# ==========================================

def analyze_quant_gold(df):
    try:
        # We only need enough candles to calculate the 14-period RSI and ATR
        if len(df) < 20: 
            return None
            
        df = calculate_indicators(df)
        
        # Look at the last fully closed candle
        curr = df.iloc[-2]
        
        close_price = curr['close']
        curr_rsi = curr['rsi']
        atr = curr['atr']
        sig_time = curr['time']

        # --- LONG ENTRY CONDITIONS (Zone Scalp) ---
        # Alert if RSI is anywhere between 20 and 40
        if 10 <= curr_rsi <= 30:
            entry = close_price
            sl = entry - (1.5 * atr)
            tp = entry + (3.0 * atr)
            return ("LONG", entry, sl, tp, curr_rsi, atr, sig_time)

        # --- SHORT ENTRY CONDITIONS (Zone Scalp) ---
        # Alert if RSI is anywhere between 60 and 80
        if 70 <= curr_rsi <= 90:
            entry = close_price
            sl = entry + (1.5 * atr)
            tp = entry - (3.0 * atr)
            return ("SHORT", entry, sl, tp, curr_rsi, atr, sig_time)

    except Exception as e:
        print(f"[Strategy Error] {e}")
        
    return None

# ==========================================
# 5. BOT SCANNER LOOP
# ==========================================

async def quant_scanner(application):
    global last_signal_time
    print(f"⚙️ Golden RSI Scalp Strategy Started. Scanning {GOLD_SYMBOL} on {TIMEFRAME}...")
    
    # Set timezone to Pakistan Standard Time (PKT) for logging
    pkt_tz = pytz.timezone('Asia/Karachi')
    
    while True:
        try:
            bars = await exchange.fetch_ohlcv(GOLD_SYMBOL, timeframe=TIMEFRAME, limit=100)
            if not bars:
                await asyncio.sleep(20)
                continue
                
            df = pd.DataFrame(bars, columns=['time', 'open', 'high', 'low', 'close', 'volume'])
            
            signal = analyze_quant_gold(df)
            
            if signal:
                direction, entry, sl, tp, rsi, atr, sig_time = signal
                
                # Prevent duplicate alerts for the exact same closed candle
                if last_signal_time != sig_time:
                    sig_datetime = datetime.fromtimestamp(sig_time / 1000, pkt_tz).strftime('%Y-%m-%d %I:%M %p PKT')
                    emoji = "🔴" if direction == "SHORT" else "🟢"
                    
                    msg = (f"{emoji} **GOLDEN RSI SCALP** {emoji}\n\n"
                           f"**Asset:** {GOLD_SYMBOL}\n"
                           f"**Action:** {direction}\n"
                           f"**Time:** {sig_datetime}\n\n"
                           f"📊 **Indicators:**\n"
                           f"• RSI: {rsi:.1f} (Zone Entry)\n"
                           f"• Volatility (ATR): {atr:.2f}\n\n"
                           f"⚡ **Trade Execution:**\n"
                           f"• **Entry:** `${entry:.2f}`\n"
                           f"• **Stop Loss:** `${sl:.2f}`\n"
                           f"• **Take Profit:** `${tp:.2f}`\n"
                           f"• **R:R:** 1:2")
                           
                    await application.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode='Markdown')
                    last_signal_time = sig_time
                    print(f"[ALERT] {direction} triggered at {sig_datetime}")
                    
        except Exception as e:
            print(f"[Scanner Error] {e}")
            
        # Scan every 15 seconds to stay sharp on smaller timeframes
        await asyncio.sleep(15)

# ==========================================
# 6. TELEGRAM COMMANDS
# ==========================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "⚙️ **Golden RSI Scalp Bot Online.**\nScanning the 5m chart for RSI overbought/oversold zones.",
        parse_mode='Markdown'
    )

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pkt_tz = pytz.timezone('Asia/Karachi')
    now_pkt = datetime.now(pkt_tz).strftime('%I:%M %p PKT')
    
    msg = (
        f"📊 **SYSTEM STATUS**\n"
        f"------------------------\n"
        f"🕒 Local Time: {now_pkt}\n"
        f"🔹 Asset: {GOLD_SYMBOL}\n"
        f"🔹 Timeframe: {TIMEFRAME}\n"
        f"🔹 Strategy: Pure RSI Zone Scalper\n"
        f"🔹 Status: ✅ ACTIVE"
    )
    await update.message.reply_text(msg, parse_mode='Markdown')

# ==========================================
# 7. MAIN EXECUTION
# ==========================================

async def main():
    keep_alive()

    application = ApplicationBuilder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("status", status))

    # Launch the active quant scanner
    asyncio.create_task(quant_scanner(application))

    await application.initialize()
    await application.start()
    
    # drop_pending_updates prevents conflict crashes on Render restarts
    await application.updater.start_polling(drop_pending_updates=True)
    await asyncio.Event().wait()

if __name__ == '__main__':
    asyncio.run(main())
