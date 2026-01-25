import os
import ccxt
import pandas as pd
import pandas_ta as ta
import asyncio
from telegram import Bot, Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from flask import Flask
from threading import Thread

# ==========================================
# CONFIGURATION
# ==========================================
# All pairs from your list (Formatted for MEXC)
SYMBOLS = [
    'BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'XRP/USDT', 'BNB/USDT', 'DOGE/USDT',
    'ADA/USDT', 'AVAX/USDT', 'SHIB/USDT', 'DOT/USDT', 'LTC/USDT', 'LINK/USDT',
    'TRX/USDT', 'BCH/USDT', 'NEAR/USDT', 'MATIC/USDT', 'UNI/USDT', 'ICP/USDT',
    'FIL/USDT', 'APT/USDT', 'XMR/USDT', 'LDO/USDT', 'HBAR/USDT', 'IMX/USDT',
    'ARB/USDT', 'OP/USDT', 'RNDR/USDT', 'ATOM/USDT', 'GRT/USDT', 'STX/USDT',
    'RUNE/USDT', 'INJ/USDT', 'FTM/USDT', 'VET/USDT', 'MKR/USDT', 'SEI/USDT',
    'XAG/USDT', 'XAU/USDT', 'ENA/USDT', 'WIF/USDT', 'PEPE/USDT', 'FLOKI/USDT',
    'RIVER/USDT', 'ZEC/USDT', 'ASTER/USDT', 'HYPE/USDT', 'PIPPIN/USDT', 
    'PUMP/USDT', 'DASH/USDT', 'LIGHT/USDT', 'FARTCOIN/USDT', 'ZEN/USDT',
    'WLFI/USDT', 'BEAT/USDT', 'CHZ/USDT', 'SPX/USDT', 'X/USDT', 'LIT/USDT',
    'STRK/USDT', 'COAI/USDT', 'GIGGLE/USDT', 'JELLY/USDT', 'ETHFI/USDT',
    'ETC/USDT', 'NIGHT/USDT', 'H/USDT'
]

# Default Timeframe
current_timeframe = '1h'

# Exchange: MEXC (Since you want to trade there)
EXCHANGE = ccxt.mexc()

# Securely get keys from Render Environment Variables
BOT_TOKEN = os.environ.get("BOT_TOKEN") 
CHAT_ID = os.environ.get("CHAT_ID")

# Global Dictionary to remember signals
last_signals = {}

# ==========================================
# 1. KEEP ALIVE SERVER
# ==========================================
app = Flask('')

@app.route('/')
def home():
    return f"I am alive! Monitoring {len(SYMBOLS)} pairs on {current_timeframe}."

def run_http():
    port = int(os.environ.get("PORT", 5000)) 
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_http)
    t.start()

# ==========================================
# 2. STRATEGY & CALCULATIONS
# ==========================================
async def check_market(symbol):
    try:
        # Fetch OHLCV Data
        bars = EXCHANGE.fetch_ohlcv(symbol, timeframe=current_timeframe, limit=100)
        df = pd.DataFrame(bars, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        
        # --- STRATEGY INDICATORS ---
        df['ema9'] = df.ta.ema(length=9)
        df['ema21'] = df.ta.ema(length=21)
        df['atr'] = df.ta.atr(length=14) # Volatility Indicator

        # Get Current & Previous Values
        prev_ema9 = df['ema9'].iloc[-3]
        prev_ema21 = df['ema21'].iloc[-3]
        curr_ema9 = df['ema9'].iloc[-2]
        curr_ema21 = df['ema21'].iloc[-2]
        
        close_price = df['close'].iloc[-2]
        atr_value = df['atr'].iloc[-2]

        # --- LOGIC ---
        
        # BUY SIGNAL
        if prev_ema9 < prev_ema21 and curr_ema9 > curr_ema21:
            sl = close_price - (2 * atr_value) # Stop Loss is 2x ATR below
            tp = close_price + (4 * atr_value) # Take Profit is 4x ATR above
            return "BUY", close_price, sl, tp

        # SELL SIGNAL
        elif prev_ema9 > prev_ema21 and curr_ema9 < curr_ema21:
            sl = close_price + (2 * atr_value) # Stop Loss is 2x ATR above
            tp = close_price - (4 * atr_value) # Take Profit is 4x ATR below
            return "SELL", close_price, sl, tp
        
        else:
            return None, None, None, None

    except Exception as e:
        # Quietly fail for obscure pairs that might not be on Spot API
        return None, None, None, None

# ==========================================
# 3. TELEGRAM COMMANDS (INTERACTIVE)
# ==========================================
async def set_timeframe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global current_timeframe, last_signals
    
    # Get the user's message (e.g., "/timeframe 15m")
    try:
        new_tf = context.args[0]
        if new_tf in ['1m', '5m', '15m', '30m', '1h', '4h', '1d']:
            current_timeframe = new_tf
            last_signals = {} # Reset memory so we get fresh signals immediately
            await update.message.reply_text(f"✅ Timeframe updated to: **{current_timeframe}**")
        else:
            await update.message.reply_text("❌ Invalid Timeframe. Use: 1m, 5m, 15m, 1h, 4h")
    except (IndexError, ValueError):
        await update.message.reply_text(f"ℹ️ Current Timeframe is: **{current_timeframe}**\nTo change, type: /timeframe 15m")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"🟢 Bot is Running!\nExchange: MEXC\nPairs: {len(SYMBOLS)}\nTimeframe: {current_timeframe}")

# ==========================================
# 4. MAIN LOOP
# ==========================================
async def scan_market(app):
    """Background task to scan the market"""
    global last_signals
    print("Market Scanner Started...")
    
    while True:
        for symbol in SYMBOLS:
            signal, price, sl, tp = await check_market(symbol)
            
            if signal:
                # Unique ID for this signal (Symbol + Direction)
                signal_id = f"{symbol}_{signal}_{current_timeframe}"
                
                if last_signals.get(symbol) != signal_id:
                    
                    # Calculate formatting
                    emoji = "🟢" if signal == "BUY" else "🔴"
                    
                    msg = (
                        f"{emoji} **NEW SIGNAL: {symbol}**\n"
                        f"-----------------------------\n"
                        f"**Direction:** {signal}\n"
                        f"**Entry:** {price:.4f}\n\n"
                        f"🎯 **TP:** {tp:.4f}\n"
                        f"🛑 **SL:** {sl:.4f}\n"
                        f"-----------------------------\n"
                        f"Timeframe: {current_timeframe}"
                    )
                    
                    try:
                        # Send to the fixed Chat ID
                        await app.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode='Markdown')
                        print(f"Sent: {symbol} {signal}")
                        last_signals[symbol] = signal_id
                    except Exception as e:
                        print(f"Telegram Fail: {e}")

            # Anti-Ban Pause (MEXC is sensitive)
            await asyncio.sleep(1)

        print("Scan complete. Waiting...")
        await asyncio.sleep(60) # Wait 1 min before next full scan

# ==========================================
# 5. STARTUP
# ==========================================
if __name__ == '__main__':
    keep_alive()
    
    # Initialize Telegram Application
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    
    # Add Command Handlers
    application.add_handler(CommandHandler("timeframe", set_timeframe))
    application.add_handler(CommandHandler("status", status))

    # Run the Scanner in the background loop
    loop = asyncio.get_event_loop()
    loop.create_task(scan_market(application))
    
    # Start the Bot (Blocking)
    application.run_polling()
