import os
import ccxt
import pandas as pd
import pandas_ta as ta
import asyncio
from telegram import Bot
from flask import Flask
from threading import Thread

# ==========================================
# CONFIGURATION
# ==========================================
# The full list of pairs from your image
SYMBOLS = [
    'BTC/USDT', 'BNB/USDT', 'ETH/USDT', 'XRP/USDT', 
    'SOL/USDT', 'ZEC/USDT', 'XMR/USDT', 'LTC/USDT', 
    'XLM/USDT', 'DOGE/USDT', 'XAG/USDT', 'XAU/USDT'
]

TIMEFRAME = '1h'       # 1 Hour Timeframe
EXCHANGE = ccxt.kucoin() 

# Securely get keys from Render Environment Variables
BOT_TOKEN = os.environ.get("BOT_TOKEN") 
CHAT_ID = os.environ.get("CHAT_ID")

# ==========================================
# 1. THE FAKE WEB SERVER (To keep Render awake)
# ==========================================
app = Flask('')

@app.route('/')
def home():
    return "I am alive! The Multi-Pair Bot is running."

def run_http():
    port = int(os.environ.get("PORT", 5000)) 
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_http)
    t.start()

# ==========================================
# 2. TRADING LOGIC
# ==========================================
async def check_market(symbol):
    """
    Checks a specific symbol for the EMA 9/21 Cross
    """
    try:
        # Fetch data for the specific symbol passed to the function
        bars = EXCHANGE.fetch_ohlcv(symbol, timeframe=TIMEFRAME, limit=100)
        df = pd.DataFrame(bars, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        
        # Calculate Indicators
        df['ema9'] = df.ta.ema(length=9)
        df['ema21'] = df.ta.ema(length=21)

        # Get the last two completed candles
        prev_ema9 = df['ema9'].iloc[-3]
        prev_ema21 = df['ema21'].iloc[-3]
        
        curr_ema9 = df['ema9'].iloc[-2]
        curr_ema21 = df['ema21'].iloc[-2]
        
        close_price = df['close'].iloc[-2]

        # Check Conditions
        if prev_ema9 < prev_ema21 and curr_ema9 > curr_ema21:
            return "BUY", close_price
        elif prev_ema9 > prev_ema21 and curr_ema9 < curr_ema21:
            return "SELL", close_price
        else:
            return None, None

    except Exception as e:
        print(f"Error fetching {symbol}: {e}")
        return None, None

# ==========================================
# 3. MAIN BOT LOOP
# ==========================================
async def main():
    bot = Bot(token=BOT_TOKEN)
    print(f"Bot started for {len(SYMBOLS)} pairs on {TIMEFRAME}...")
    
    # Dictionary to store the last signal for EACH pair separately
    # Example: {'BTC/USDT': 'BUY', 'ETH/USDT': 'SELL'}
    last_signals = {} 

    while True:
        # Loop through every symbol in our list
        for symbol in SYMBOLS:
            
            signal, price = await check_market(symbol)
            
            if signal:
                # Check if this specific pair's signal has changed
                # We use .get() in case the symbol isn't in the dictionary yet
                if signal != last_signals.get(symbol):
                    
                    # Create the alert message
                    message = f"🚨 **SIGNAL ALERT** 🚨\n\n" \
                              f"Pair: **{symbol}**\n" \
                              f"Direction: **{signal}**\n" \
                              f"Price: {price}\n" \
                              f"Timeframe: {TIMEFRAME}"
                    
                    try:
                        await bot.send_message(chat_id=CHAT_ID, text=message, parse_mode='Markdown')
                        print(f"Sent Alert: {symbol} -> {signal}")
                        
                        # Update the "memory" for this specific symbol
                        last_signals[symbol] = signal
                        
                    except Exception as e:
                        print(f"Telegram Error: {e}")
                else:
                    print(f"{symbol}: Signal {signal} exists (Already sent).")
            else:
                print(f"{symbol}: No Signal")

            # PAUSE: Wait 2 seconds between coins to avoid Binance "Rate Limit" bans
            await asyncio.sleep(2)

        print("Cycle complete. Waiting before next check...")
        # Wait 5 minutes before checking the whole list again
        # (Since it's a 1H timeframe, checking every 5 mins is plenty)
        await asyncio.sleep(300) 

if __name__ == '__main__':
    keep_alive()
    asyncio.run(main())
