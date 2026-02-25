import os
import ccxt.async_support as ccxt
import pandas as pd
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

# Store the date of the last signal to prevent spamming multiple alerts a day
last_signal_date = None 

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
    return "NY Open Gold Bot is Running!"

def run_http(): 
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))

def keep_alive(): 
    Thread(target=run_http, daemon=True).start()

# ==========================================
# 3. NY OPEN STRATEGY LOGIC
# ==========================================

async def analyze_ny_open():
    try:
        ny_tz = pytz.timezone('America/New_York')
        now_ny = datetime.now(ny_tz)
        current_date_str = now_ny.strftime('%Y-%m-%d')
        
        # 1. Ensure we are past 09:00 AM NY time (The 08:00 candle must be fully closed)
        if now_ny.hour < 9:
            return None
            
        # 2. Fetch Daily Data to determine CCT (Candle Continuity Theory) Bias
        daily_bars = await exchange.fetch_ohlcv(GOLD_SYMBOL, timeframe='1d', limit=2)
        if not daily_bars: return None
        
        yesterday = daily_bars[-2] # [time, open, high, low, close, vol]
        daily_bias = "BULLISH" if yesterday[4] > yesterday[1] else "BEARISH"

        # 3. Fetch 1H Data to find today's 08:00 AM NY Candle High/Low
        hourly_bars = await exchange.fetch_ohlcv(GOLD_SYMBOL, timeframe='1h', limit=24)
        df_1h = pd.DataFrame(hourly_bars, columns=['time', 'open', 'high', 'low', 'close', 'volume'])
        
        # Convert UTC timestamps to NY Time to locate the exact 08:00 candle
        df_1h['ny_time'] = pd.to_datetime(df_1h['time'], unit='ms').dt.tz_localize('UTC').dt.tz_convert(ny_tz)
        
        # Filter for today's 08:00 AM candle
        target_candle = df_1h[(df_1h['ny_time'].dt.date == now_ny.date()) & (df_1h['ny_time'].dt.hour == 8)]
        
        if target_candle.empty:
            return None
            
        ny_high = target_candle.iloc[0]['high']
        ny_low = target_candle.iloc[0]['low']

        # 4. Fetch 5m Data to look for the sweep and displacement
        five_min_bars = await exchange.fetch_ohlcv(GOLD_SYMBOL, timeframe='5m', limit=30)
        df_5m = pd.DataFrame(five_min_bars, columns=['time', 'open', 'high', 'low', 'close', 'volume'])
        
        # We look at the last fully closed 5m candle (prev_5m) and the one right before it (sweep_5m)
        sweep_5m = df_5m.iloc[-3]
        confirm_5m = df_5m.iloc[-2]
        
        # --- BEARISH REVERSAL SETUP (Sweeps High, Closes Below) ---
        if sweep_5m['high'] > ny_high and confirm_5m['close'] < ny_high:
            entry = confirm_5m['close']
            sl = max(sweep_5m['high'], confirm_5m['high'])
            risk = abs(sl - entry)
            tp = entry - (risk * 2) # Target 1:2 RR
            
            # Filter: Check if velocity is aggressive (happening within a few hours of the open)
            if now_ny.hour <= 12: 
                return ("SHORT", entry, sl, tp, daily_bias, ny_high, ny_low, current_date_str)

        # --- BULLISH REVERSAL SETUP (Sweeps Low, Closes Above) ---
        if sweep_5m['low'] < ny_low and confirm_5m['close'] > ny_low:
            entry = confirm_5m['close']
            sl = min(sweep_5m['low'], confirm_5m['low'])
            risk = abs(entry - sl)
            tp = entry + (risk * 2) # Target 1:2 RR
            
            if now_ny.hour <= 12:
                return ("LONG", entry, sl, tp, daily_bias, ny_high, ny_low, current_date_str)

    except Exception as e:
        print(f"[Strategy Error] {e}")
        
    return None

# ==========================================
# 4. BOT SCANNER LOOP
# ==========================================

async def gold_scanner(application):
    global last_signal_date
    print(f"🗽 NY Open Gold Strategy Started. Monitoring {GOLD_SYMBOL}...")
    
    while True:
        try:
            signal = await analyze_ny_open()
            
            if signal:
                direction, entry, sl, tp, daily_bias, ny_high, ny_low, sig_date = signal
                
                # Only send one signal per day to avoid spamming the same setup
                if last_signal_date != sig_date:
                    emoji = "🔴" if direction == "SHORT" else "🟢"
                    
                    # CCT Confluence Check
                    confluence = "✅ Matches Daily Bias" if (direction == "SHORT" and daily_bias == "BEARISH") or (direction == "LONG" and daily_bias == "BULLISH") else "⚠️ Against Daily Bias"

                    msg = (f"{emoji} **NY OPEN GOLD SWEEP** {emoji}\n\n"
                           f"**{direction}** Reversal Triggered\n"
                           f"**Daily Bias (CCT):** {daily_bias} ({confluence})\n\n"
                           f"🏦 **08:00 AM NY Range:**\n"
                           f"High: `${ny_high:.2f}`\n"
                           f"Low: `${ny_low:.2f}`\n\n"
                           f"⚡ **5m Displacement Entry:**\n"
                           f"Entry: `${entry:.2f}`\n"
                           f"Stop Loss: `${sl:.2f}`\n"
                           f"Take Profit (1:2): `${tp:.2f}`\n")
                           
                    await application.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode='Markdown')
                    last_signal_date = sig_date
                    print(f"[ALERT] Sent {direction} signal for {sig_date}")
                    
        except Exception as e:
            print(f"[Scanner Error] {e}")
            
        # Check every 1 minute
        await asyncio.sleep(60)

# ==========================================
# 5. TELEGRAM COMMANDS
# ==========================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🗽 **NY Open Gold Bot Online.**\nMonitoring the 08:00 AM EST candle for liquidity sweeps.",
        parse_mode='Markdown'
    )

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ny_tz = pytz.timezone('America/New_York')
    now_ny = datetime.now(ny_tz).strftime('%H:%M EST')
    
    msg = (
        f"📊 **SYSTEM STATUS**\n"
        f"------------------------\n"
        f"🕒 NY Time: {now_ny}\n"
        f"🔹 Target Asset: {GOLD_SYMBOL}\n"
        f"🔹 Target Candle: 08:00 AM EST\n"
        f"🔹 Strategy: 5m Sweep & Reversal\n"
        f"🔹 Status: ✅ ACTIVE"
    )
    await update.message.reply_text(msg, parse_mode='Markdown')

# ==========================================
# 6. MAIN EXECUTION
# ==========================================

async def main():
    keep_alive()

    application = ApplicationBuilder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("status", status))

    # Launch the dedicated Gold scanner
    asyncio.create_task(gold_scanner(application))

    await application.initialize()
    await application.start()
    await application.updater.start_polling(drop_pending_updates=True)
    await asyncio.Event().wait()

if __name__ == '__main__':
    asyncio.run(main())
