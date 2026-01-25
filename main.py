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

EXCHANGE = ccxt.mexc()
BOT_TOKEN = os.environ.get("BOT_TOKEN") 
CHAT_ID = os.environ.get("CHAT_ID")

# Global Settings
current_timeframe = '1h'
active_mode = 'both'  # Options: 'ema', 'sweep', 'both'
last_signals = {}     # Memory to prevent spam

# ==========================================
# 1. KEEP ALIVE SERVER
# ==========================================
app = Flask('')

@app.route('/')
def home():
    return f"Alive! Mode: {active_mode} | TF: {current_timeframe}"

def run_http():
    port = int(os.environ.get("PORT", 5000)) 
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_http)
    t.start()

# ==========================================
# 2. STRATEGY A: EMA CROSSOVER
# ==========================================
def strategy_ema_cross(df):
    """
    Checks for EMA 9 crossing EMA 21
    """
    try:
        prev_ema9 = df['ema9'].iloc[-3]
        prev_ema21 = df['ema21'].iloc[-3]
        curr_ema9 = df['ema9'].iloc[-2]
        curr_ema21 = df['ema21'].iloc[-2]
        close_price = df['close'].iloc[-2]
        atr = df['atr'].iloc[-2]

        if prev_ema9 < prev_ema21 and curr_ema9 > curr_ema21:
            sl = close_price - (2 * atr)
            tp = close_price + (4 * atr)
            return "BUY", "EMA Cross", close_price, sl, tp

        elif prev_ema9 > prev_ema21 and curr_ema9 < curr_ema21:
            sl = close_price + (2 * atr)
            tp = close_price - (4 * atr)
            return "SELL", "EMA Cross", close_price, sl, tp
            
    except:
        pass
        
    return None, None, None, None, None

# ==========================================
# 3. STRATEGY B: LIQUIDATION SWEEP (SFP)
# ==========================================
def strategy_liquidation_sweep(df, lookback=20):
    """
    Checks if price grabbed liquidity (High/Low) and reversed close.
    """
    try:
        # Get data EXCLUDING the current candle to find the range
        past_data = df.iloc[-lookback-2:-2] 
        range_high = past_data['high'].max()
        range_low = past_data['low'].min()

        # Current Candle (The one that just closed)
        curr_high = df['high'].iloc[-2]
        curr_low = df['low'].iloc[-2]
        curr_close = df['close'].iloc[-2]
        atr = df['atr'].iloc[-2]

        # BEARISH SWEEP (Short)
        # Price went ABOVE range high (grabbed stops) but CLOSED BELOW it
        if curr_high > range_high and curr_close < range_high:
            sl = curr_high + (0.5 * atr) # Stop just above the wick
            tp = curr_close - (3 * atr)  # Target lower
            return "SELL", "Liquidity Sweep", curr_close, sl, tp

        # BULLISH SWEEP (Long)
        # Price went BELOW range low (grabbed stops) but CLOSED ABOVE it
        if curr_low < range_low and curr_close > range_low:
            sl = curr_low - (0.5 * atr)  # Stop just below the wick
            tp = curr_close + (3 * atr)  # Target higher
            return "BUY", "Liquidity Sweep", curr_close, sl, tp

    except:
        pass

    return None, None, None, None, None

# ==========================================
# 4. MASTER ANALYSIS FUNCTION
# ==========================================
async def analyze_market(symbol):
    try:
        bars = EXCHANGE.fetch_ohlcv(symbol, timeframe=current_timeframe, limit=100)
        df = pd.DataFrame(bars, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        
        # Calculate Indicators
        df['ema9'] = df.ta.ema(length=9)
        df['ema21'] = df.ta.ema(length=21)
        df['atr'] = df.ta.atr(length=14)

        results = []

        # Check Strategies based on 'active_mode'
        if active_mode in ['ema', 'both']:
            res = strategy_ema_cross(df)
            if res[0]: results.append(res)

        if active_mode in ['sweep', 'both']:
            res = strategy_liquidation_sweep(df)
            if res[0]: results.append(res)
            
        return results

    except Exception as e:
        return []

# ==========================================
# 5. TELEGRAM COMMANDS
# ==========================================
async def set_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global active_mode
    try:
        mode = context.args[0].lower()
        if mode in ['ema', 'sweep', 'both']:
            active_mode = mode
            await update.message.reply_text(f"✅ Mode changed to: **{mode.upper()}**")
        else:
            await update.message.reply_text("❌ Invalid. Use: /mode ema, /mode sweep, /mode both")
    except:
        await update.message.reply_text(f"ℹ️ Current Mode: {active_mode}\nChange: /mode both")

async def set_timeframe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global current_timeframe, last_signals
    try:
        tf = context.args[0]
        current_timeframe = tf
        last_signals = {} 
        await update.message.reply_text(f"✅ Timeframe: **{tf}**")
    except:
        await update.message.reply_text(f"ℹ️ Current TF: {current_timeframe}")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"🟢 Running\nTF: {current_timeframe}\nMode: {active_mode}\nPairs: {len(SYMBOLS)}")

# ==========================================
# 6. SCANNER LOOP
# ==========================================
async def scan_market(app):
    global last_signals
    print("Scanner Started...")
    
    while True:
        for symbol in SYMBOLS:
            signals = await analyze_market(symbol)
            
            for (direction, strat_name, price, sl, tp) in signals:
                
                # Unique ID: Symbol + Strategy + Direction + Timeframe
                sig_id = f"{symbol}_{strat_name}_{direction}_{current_timeframe}"
                
                # If we haven't sent this exact signal yet...
                if last_signals.get(symbol) != sig_id:
                    
                    emoji = "🚀" if direction == "BUY" else "🔻"
                    
                    msg = (
                        f"{emoji} **{strat_name.upper()} ALERT**\n"
                        f"Coin: **{symbol}**\n"
                        f"Side: **{direction}**\n"
                        f"Entry: {price:.4f}\n\n"
                        f"🎯 TP: {tp:.4f}\n"
                        f"🛑 SL: {sl:.4f}\n"
                        f"Timeframe: {current_timeframe}"
                    )
                    
                    try:
                        await app.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode='Markdown')
                        print(f"Sent: {symbol} ({strat_name})")
                        last_signals[symbol] = sig_id
                    except:
                        print("Msg Failed")

            await asyncio.sleep(1) # Rate limit

        print("Scan cycle done.")
        await asyncio.sleep(60)

# ==========================================
# 7. MAIN ENTRY POINT
# ==========================================
if __name__ == '__main__':
    keep_alive()
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    
    application.add_handler(CommandHandler("mode", set_mode))
    application.add_handler(CommandHandler("timeframe", set_timeframe))
    application.add_handler(CommandHandler("status", status))

    loop = asyncio.get_event_loop()
    loop.create_task(scan_market(application))
    application.run_polling()
