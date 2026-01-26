import os
import ccxt
import pandas as pd
import pandas_ta as ta
import asyncio
from datetime import datetime, timezone, timedelta
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
current_timeframe = '15m' # Default to 15m for ORB strategy
active_mode = 'all'       # Options: 'ema', 'sweep', 'orb', 'all'
last_signals = {}         # Memory

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
    try:
        prev_ema9 = df['ema9'].iloc[-3]
        prev_ema21 = df['ema21'].iloc[-3]
        curr_ema9 = df['ema9'].iloc[-2]
        curr_ema21 = df['ema21'].iloc[-2]
        close_price = df['close'].iloc[-2]
        atr = df['atr'].iloc[-2]

        if prev_ema9 < prev_ema21 and curr_ema9 > curr_ema21:
            return "BUY", "EMA Cross", close_price, (close_price - 2*atr), (close_price + 4*atr)
        elif prev_ema9 > prev_ema21 and curr_ema9 < curr_ema21:
            return "SELL", "EMA Cross", close_price, (close_price + 2*atr), (close_price - 4*atr)
    except: pass
    return None, None, None, None, None

# ==========================================
# 3. STRATEGY B: LIQUIDATION SWEEP (SFP)
# ==========================================
def strategy_liquidation_sweep(df, lookback=20):
    try:
        past_data = df.iloc[-lookback-2:-2] 
        range_high = past_data['high'].max()
        range_low = past_data['low'].min()
        curr_high = df['high'].iloc[-2]
        curr_low = df['low'].iloc[-2]
        curr_close = df['close'].iloc[-2]
        atr = df['atr'].iloc[-2]

        if curr_high > range_high and curr_close < range_high:
            return "SELL", "Liquidity Sweep", curr_close, (curr_high + 0.5*atr), (curr_close - 3*atr)
        if curr_low < range_low and curr_close > range_low:
            return "BUY", "Liquidity Sweep", curr_close, (curr_low - 0.5*atr), (curr_close + 3*atr)
    except: pass
    return None, None, None, None, None

# ==========================================
# 4. STRATEGY C: NY OPEN ORB (15m)
# ==========================================
def strategy_ny_orb(df):
    """
    NY Open Breakout: 13:30 - 15:00 UTC
    Only works if Timeframe is 15m.
    """
    try:
        # 1. Check if we are in the NY Session (13:30 - 15:00 UTC)
        now_utc = datetime.now(timezone.utc)
        
        # Define today's session times
        start_time = now_utc.replace(hour=13, minute=30, second=0, microsecond=0)
        end_time = now_utc.replace(hour=15, minute=0, second=0, microsecond=0)
        
        # If outside trading hours, return nothing
        if not (start_time <= now_utc <= end_time):
            return None, None, None, None, None

        # 2. Find the ORB Candle (The 13:30 UTC candle)
        # Convert df time (ms) to datetime objects
        df['dt'] = pd.to_datetime(df['time'], unit='ms', utc=True)
        
        # Look for the specific candle at 13:30 today
        orb_candle = df[df['dt'] == start_time]
        
        if orb_candle.empty:
            return None, None, None, None, None # Candle not found yet

        orb_high = orb_candle['high'].values[0]
        orb_low = orb_candle['low'].values[0]

        # 3. Current Market Data
        curr_close = df['close'].iloc[-1] # Live price or last closed
        curr_ema9 = df['ema9'].iloc[-1]
        curr_ema21 = df['ema21'].iloc[-1]
        atr = df['atr'].iloc[-1]

        # 4. Breakout Logic + EMA Filter
        # BUY: Close > ORB High AND EMA 9 > EMA 21
        if curr_close > orb_high and curr_ema9 > curr_ema21:
            sl = orb_low # SL at bottom of range
            tp = curr_close + (2 * (orb_high - orb_low)) # TP is 2x the range size
            return "BUY", "NY ORB Breakout", curr_close, sl, tp

        # SELL: Close < ORB Low AND EMA 9 < EMA 21
        if curr_close < orb_low and curr_ema9 < curr_ema21:
            sl = orb_high # SL at top of range
            tp = curr_close - (2 * (orb_high - orb_low))
            return "SELL", "NY ORB Breakout", curr_close, sl, tp

    except Exception as e:
        pass
        
    return None, None, None, None, None

# ==========================================
# 5. MASTER ANALYSIS
# ==========================================
async def analyze_market(symbol):
    try:
        # Fetch slightly more data to ensure we find the 13:30 candle
        bars = EXCHANGE.fetch_ohlcv(symbol, timeframe=current_timeframe, limit=200)
        df = pd.DataFrame(bars, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        
        df['ema9'] = df.ta.ema(length=9)
        df['ema21'] = df.ta.ema(length=21)
        df['atr'] = df.ta.atr(length=14)

        results = []

        if active_mode in ['ema', 'all']:
            res = strategy_ema_cross(df)
            if res[0]: results.append(res)

        if active_mode in ['sweep', 'all']:
            res = strategy_liquidation_sweep(df)
            if res[0]: results.append(res)
            
        if active_mode in ['orb', 'all']:
            # Only run ORB if TF is 15m
            if current_timeframe == '15m':
                res = strategy_ny_orb(df)
                if res[0]: results.append(res)
            
        return results

    except Exception as e:
        return []

# ==========================================
# 6. TELEGRAM COMMANDS
# ==========================================
async def set_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global active_mode
    try:
        mode = context.args[0].lower()
        if mode in ['ema', 'sweep', 'orb', 'all']:
            active_mode = mode
            await update.message.reply_text(f"✅ Mode set to: **{mode.upper()}**")
        else:
            await update.message.reply_text("❌ Use: /mode ema, /mode sweep, /mode orb, /mode all")
    except:
        await update.message.reply_text(f"ℹ️ Current Mode: {active_mode}")

async def set_timeframe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global current_timeframe, last_signals
    try:
        tf = context.args[0]
        current_timeframe = tf
        last_signals = {} 
        await update.message.reply_text(f"✅ Timeframe: **{tf}**")
        if tf != '15m':
             await update.message.reply_text("⚠️ Note: 'NY ORB' strategy only works on 15m!")
    except:
        await update.message.reply_text(f"ℹ️ Current TF: {current_timeframe}")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    now_utc = datetime.now(timezone.utc).strftime('%H:%M UTC')
    await update.message.reply_text(f"🟢 Running\nTime: {now_utc}\nTF: {current_timeframe}\nMode: {active_mode}")

# ==========================================
# 7. MAIN LOOP
# ==========================================
async def scan_market(app):
    global last_signals
    print("Scanner Started...")
    
    while True:
        for symbol in SYMBOLS:
            signals = await analyze_market(symbol)
            
            for (direction, strat_name, price, sl, tp) in signals:
                sig_id = f"{symbol}_{strat_name}_{direction}_{current_timeframe}_{datetime.now().hour}"
                
                if last_signals.get(symbol) != sig_id:
                    emoji = "🗽" if strat_name == "NY ORB Breakout" else ("🚀" if direction == "BUY" else "🔻")
                    
                    msg = (
                        f"{emoji} **{strat_name.upper()}**\n"
                        f"Coin: **{symbol}**\n"
                        f"Side: **{direction}**\n"
                        f"Entry: {price:.4f}\n\n"
                        f"🎯 TP: {tp:.4f}\n"
                        f"🛑 SL: {sl:.4f}\n"
                        f"TF: {current_timeframe}"
                    )
                    
                    try:
                        await app.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode='Markdown')
                        print(f"Sent: {symbol} ({strat_name})")
                        last_signals[symbol] = sig_id
                    except: pass
            
            await asyncio.sleep(1)

        await asyncio.sleep(60)

if __name__ == '__main__':
    keep_alive()
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    
    application.add_handler(CommandHandler("mode", set_mode))
    application.add_handler(CommandHandler("timeframe", set_timeframe))
    application.add_handler(CommandHandler("status", status))

    loop = asyncio.get_event_loop()
    loop.create_task(scan_market(application))
    application.run_polling()
