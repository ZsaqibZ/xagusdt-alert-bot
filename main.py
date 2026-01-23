import os
import ccxt
import pandas as pd
import pandas_ta as ta
import asyncio
from telegram import Bot
from flask import Flask
from threading import Thread
import time

# ==========================================
# CONFIGURATION
# ==========================================
SYMBOL = 'XAG/USDT'     # Silver vs Tether
TIMEFRAME = '1h'       # Timeframe (15m, 1h, 4h, etc.)
EXCHANGE = ccxt.binance() # Using Binance for data

# Get these from your Environment Variables (for security)
BOT_TOKEN = os.environ.get("BOT_TOKEN") 
CHAT_ID = os.environ.get("CHAT_ID")

# ==========================================
# 1. THE FAKE WEB SERVER (To keep Render awake)
# ==========================================
app = Flask('')

@app.route('/')
def home():
    return "I am alive! The XAG bot is running."

def run_http():
    # Run on port 10000 or whatever Render assigns
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run_http)
    t.start()

# ==========================================
# 2. TRADING LOGIC
# ==========================================
async def check_market():
    print(f"Checking market for {SYMBOL}...")
    
    try:
        # 1. Fetch OHLCV Data (Open, High, Low, Close, Volume)
        bars = EXCHANGE.fetch_ohlcv(SYMBOL, timeframe=TIMEFRAME, limit=100)
        df = pd.DataFrame(bars, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        
        # 2. Calculate Indicators (EMA 9 and EMA 21)
        df['ema9'] = df.ta.ema(length=9)
        df['ema21'] = df.ta.ema(length=21)

        # Get the last two completed candles (Current and Previous)
        # We use -2 (previous) and -1 (current closed candle) logic 
        # But usually in live bots, we look at the last completed candle (-2) vs the one before it (-3) 
        # to ensure the candle has actually closed.
        
        prev_ema9 = df['ema9'].iloc[-3]
        prev_ema21 = df['ema21'].iloc[-3]
        
        curr_ema9 = df['ema9'].iloc[-2]
        curr_ema21 = df['ema21'].iloc[-2]
        
        close_price = df['close'].iloc[-2]

        # 3. Check Conditions (Crossover logic)
        
        # BUY: EMA 9 crosses OVER EMA 21
        # (Previous 9 was below 21) AND (Current 9 is above 21)
        if prev_ema9 < prev_ema21 and curr_ema9 > curr_ema21:
            return "BUY", close_price

        # SELL: EMA 9 crosses UNDER EMA 21
        # (Previous 9 was above 21) AND (Current 9 is below 21)
        elif prev_ema9 > prev_ema21 and curr_ema9 < curr_ema21:
            return "SELL", close_price
        
        else:
            return None, None

    except Exception as e:
        print(f"Error fetching data: {e}")
        return None, None

# ==========================================
# 3. MAIN BOT LOOP
# ==========================================
async def main():
    bot = Bot(token=BOT_TOKEN)
    print("Bot started...")
    
    # State variable to ensure we don't spam the same signal
    last_signal = None 

    while True:
        signal, price = await check_market()
        
        if signal:
            # Only send if the signal is different from the last one
            if signal != last_signal:
                message = f"🚨 **SIGNAL ALERT: {SYMBOL}** 🚨\n\n" \
                          f"Direction: **{signal}**\n" \
                          f"Price: {price}\n" \
                          f"Strategy: EMA 9/21 Crossover\n" \
                          f"Timeframe: {TIMEFRAME}"
                
                await bot.send_message(chat_id=CHAT_ID, text=message, parse_mode='Markdown')
                print(f"Signal Sent: {signal}")
                last_signal = signal
            else:
                print("Signal detected, but already sent.")
        else:
            print("No signal.")

        # Sleep for 60 seconds before checking again
        await asyncio.sleep(60)

if __name__ == '__main__':
    # Start the web server in the background
    keep_alive()
    
    # Start the bot
    asyncio.run(main())