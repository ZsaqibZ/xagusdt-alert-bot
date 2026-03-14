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
    return "Pine Script Translation Bot is Running!"

def run_http(): 
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))

def keep_alive(): 
    Thread(target=run_http, daemon=True).start()

# ==========================================
# 3. QUANT INDICATOR MATH
# ==========================================

def calculate_indicators(df):
    # 1. EMAs (50 and 200)
    df['ema50'] = df['close'].ewm(span=50, adjust=False).mean()
    df['ema200'] = df['close'].ewm(span=200, adjust=False).mean()

    # 2. RSI 14
    delta = df['close'].diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    avg_gain = gain.ewm(alpha=1/14, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/14, adjust=False).mean()
    rs = avg_gain / avg_loss
    df['rsi'] = 100 - (100 / (1 + rs))

    # 3. ATR 14
    df['tr0'] = abs(df['high'] - df['low'])
    df['tr1'] = abs(df['high'] - df['close'].shift(1))
    df['tr2'] = abs(df['low'] - df['close'].shift(1))
    df['tr'] = df[['tr0', 'tr1', 'tr2']].max(axis=1)
    df['atr'] = df['tr'].rolling(window=14).mean()
    
    return df

# ==========================================
# 4. STRATEGY LOGIC (PINE SCRIPT TRANSLATION)
# ==========================================

def analyze_quant_gold(df):
    try:
        # Need at least 205 candles to accurately calculate the 200 EMA
        if len(df) < 205: 
            return None
            
        df = calculate_indicators(df)
        
        # Look at the last fully closed candle (curr) and the one before it (prev)
        curr = df.iloc[-2]
        prev = df.iloc[-3]
        
        close_price = curr['close']
        prev_close = prev['close']
        
        ema50 = curr['ema50']
        prev_ema50 = prev['ema50']
        ema200 = curr['ema200']
        
        rsi = curr['rsi']
        atr = curr['atr']
        c_time_ms = curr['time']

        # --- TIME SESSION FILTER ---
        # Pine Script: input.session("0300-1200", "UTC")
        candle_dt_utc = datetime.fromtimestamp(c_time_ms / 1000, tz=pytz.UTC)
        in_session = 3 <= candle_dt_utc.hour < 12 
        
        if not in_session:
            return None # Ignore setups outside of the London/Early NY session

        # --- ENTRY TRIGGERS ---
        # Did the price cross the 50 EMA on this exact candle?
        cross_above_ema50 = (prev_close <= prev_ema50) and (close_price > ema50)
        cross_below_ema50 = (prev_close >= prev_ema50) and (close_price < ema50)

        # LONG: 50 > 200 (Uptrend), crosses above 50 EMA, RSI > 50
        if (ema50 > ema200) and cross_above_ema50 and (rsi > 50):
            entry = close_price
            tp = entry + (atr * 1.0) # 1.0 ATR Multiplier
            sl = entry - (atr * 3.5) # 3.5 ATR Multiplier
            return ("LONG", entry, sl, tp, rsi, atr, c_time_ms)

        # SHORT: 50 < 200 (Downtrend), crosses below 50 EMA, RSI < 50
        if (ema50 < ema200) and cross_below_ema50 and (rsi < 50):
            entry = close_price
            tp = entry - (atr * 1.0) # 1.0 ATR Multiplier
            sl = entry + (atr * 3.5) # 3.5 ATR Multiplier
            return ("SHORT", entry, sl, tp, rsi, atr, c_time_ms)

    except Exception as e:
        print(f"[Strategy Error] {e}")
        
    return None

# ==========================================
# 5. BOT SCANNER LOOP
# ==========================================

async def quant_scanner(application):
    global last_signal_time
    print(f"⚙️ Pine Script Strategy Started. Scanning {GOLD_SYMBOL} on {TIMEFRAME}...")
    
    pkt_tz = pytz.timezone('Asia/Karachi')
    
    while True:
        try:
            bars = await exchange.fetch_ohlcv(GOLD_SYMBOL, timeframe=TIMEFRAME, limit=1000)
            if not bars:
                await asyncio.sleep(20)
                continue
                
            df = pd.DataFrame(bars, columns=['time', 'open', 'high', 'low', 'close', 'volume'])
            
            signal = analyze_quant_gold(df)
            
            if signal:
                direction, entry, sl, tp, rsi, atr, sig_time = signal
                
                # Prevent duplicate alerts
                if last_signal_time != sig_time:
                    sig_datetime = datetime.fromtimestamp(sig_time / 1000, pkt_tz).strftime('%Y-%m-%d %I:%M %p PKT')
                    emoji = "🔴" if direction == "SHORT" else "🟢"
                    
                    msg = (f"{emoji} **GOLD TREND PULLBACK** {emoji}\n\n"
                           f"**Asset:** {GOLD_SYMBOL}\n"
                           f"**Action:** {direction}\n"
                           f"**Time:** {sig_datetime}\n\n"
                           f"📊 **Strategy Confluences:**\n"
                           f"• Setup: Price Snap-Back (50 EMA)\n"
                           f"• Trend Guard: 200 EMA Passed\n"
                           f"• Momentum: RSI {rsi:.1f}\n\n"
                           f"⚡ **Trade Execution:**\n"
                           f"• **Entry:** `${entry:.2f}`\n"
                           f"• **Take Profit (1.0x ATR):** `${tp:.2f}`\n"
                           f"• **Stop Loss (3.5x ATR):** `${sl:.2f}`\n")
                           
                    await application.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode='Markdown')
                    last_signal_time = sig_time
                    print(f"[ALERT] {direction} triggered at {sig_datetime}")
                    
        except Exception as e:
            print(f"[Scanner Error] {e}")
            
        await asyncio.sleep(30)

# ==========================================
# 6. TELEGRAM COMMANDS
# ==========================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "⚙️ **Pine Script Gold Bot Online.**\nScanning the 15m chart during London/NY sessions.",
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
        f"🔹 Strategy: 50/200 EMA Pullback\n"
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

    asyncio.create_task(quant_scanner(application))

    await application.initialize()
    await application.start()
    await application.updater.start_polling(drop_pending_updates=True)
    await asyncio.Event().wait()

if __name__ == '__main__':
    asyncio.run(main())
