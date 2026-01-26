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
current_timeframe = '15m' 
active_mode = 'all'       
last_signals = {}         

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
            return "BUY", "EMA Cross", close_price, (close_price - 2*atr), "Dynamic / 2R"
        elif prev_ema9 > prev_ema21 and curr_ema9 < curr_ema21:
            return "SELL", "EMA Cross", close_price, (close_price + 2*atr), "Dynamic / 2R"
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
            return "SELL", "Liquidity Sweep", curr_close, (curr_high + 0.5*atr), "Dynamic / 3R"
        if curr_low < range_low and curr_close > range_low:
            return "BUY", "Liquidity Sweep", curr_close, (curr_low - 0.5*atr), "Dynamic / 3R"
    except: pass
    return None, None, None, None, None

# ==========================================
# 4. STRATEGY C: FORTIFIED NY ORB
# ==========================================
def strategy_ny_orb(df):
    """
    UPGRADES APPLIED:
    1. Volume Filter (> Avg Vol)
    2. 2-Candle Confirmation (Close 1 & Close 2 > Level)
    3. Trailing Stop Advice
    """
    try:
        # --- Time Check (NY Session) ---
        now_utc = datetime.now(timezone.utc)
        start_time = now_utc.replace(hour=13, minute=30, second=0, microsecond=0)
        end_time = now_utc.replace(hour=16, minute=0, second=0, microsecond=0) # Extended to 16:00 for management
        
        if not (start_time <= now_utc <= end_time):
            return None, None, None, None, None

        # --- Find ORB Candle (13:30 UTC) ---
        df['dt'] = pd.to_datetime(df['time'], unit='ms', utc=True)
        orb_candle = df[df['dt'] == start_time]
        
        if orb_candle.empty:
            return None, None, None, None, None 

        orb_high = orb_candle['high'].values[0]
        orb_low = orb_candle['low'].values[0]

        # --- Data Prep ---
        # We need the last TWO closed candles for confirmation
        last_close = df['close'].iloc[-2]      # The candle that just finished
        prev_close = df['close'].iloc[-3]      # The one before that
        
        curr_ema9 = df['ema9'].iloc[-2]
        curr_ema21 = df['ema21'].iloc[-2]
        
        # Volume Check
        curr_vol = df['vol'].iloc[-2]          # Volume of the breakout candle
        avg_vol = df['vol_avg'].iloc[-2]       # Average volume

        # --- BUY LOGIC ---
        # 1. Confirmation: Last 2 candles CLOSED above High
        # 2. Filter: EMA 9 > EMA 21 (Trend is Up)
        # 3. Volume: Breakout volume must be higher than average
        if (last_close > orb_high and prev_close > orb_high) and (curr_ema9 > curr_ema21):
             if curr_vol > avg_vol:
                sl = orb_low 
                tp_advice = "TRAIL EMA 9" # Upgrade #3
                return "BUY", "NY ORB (Strong)", last_close, sl, tp_advice

        # --- SELL LOGIC ---
        # 1. Confirmation: Last 2 candles CLOSED below Low
        # 2. Filter: EMA 9 < EMA 21 (Trend is Down)
        # 3. Volume: Breakout volume > Average
        if (last_close < orb_low and prev_close < orb_low) and (curr_ema9 < curr_ema21):
            if curr_vol > avg_vol:
                sl = orb_high
                tp_advice = "TRAIL EMA 9" # Upgrade #3
                return "SELL", "NY ORB (Strong)", last_close, sl, tp_advice

    except Exception as e:
        pass
        
    return None, None, None, None, None

# ==========================================
# 5. MASTER ANALYSIS
# ==========================================
async def analyze_market(symbol):
    try:
        bars = EXCHANGE.fetch_ohlcv(symbol, timeframe=current_timeframe, limit=200)
        df = pd.DataFrame(bars, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        
        df['ema9'] = df.ta.ema(length=9)
        df['ema21'] = df.ta.ema(length=21)
        df['atr'] = df.ta.atr(length=14)
        df['vol_avg'] = df.ta.sma(close=df['vol'], length=20) # Added Volume Moving Average

        results = []

        if active_mode in ['ema', 'all']:
            res = strategy_ema_cross(df)
            if res[0]: results.append(res)

        if active_mode in ['sweep', 'all']:
            res = strategy_liquidation_sweep(df)
            if res[0]: results.append(res)
            
        if active_mode in ['orb', 'all']:
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
             await update.message.reply_text("⚠️ 'NY ORB' strategy paused (Requires 15m).")
    except:
        await update.message.reply_text(f"ℹ️ Current TF: {current_timeframe}")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    now_utc = datetime.now(timezone.utc).strftime('%H:%M UTC')
    await update.message.reply_text(f"🟢 Running\nTime: {now_utc}\nTF: {current_timeframe}\nMode: {active_mode}")

# ==========================================
# 7. SCANNER LOOP
# ==========================================
async def scan_market(app):
    global last_signals
    print("Scanner Started...")
    
    while True:
        for symbol in SYMBOLS:
            signals = await analyze_market(symbol)
            
            for (direction, strat_name, price, sl, tp) in signals:
                # Unique ID: Symbol + Strategy + Direction + Timeframe + Hour (prevents spam but allows new hourly signals)
                sig_id = f"{symbol}_{strat_name}_{direction}_{current_timeframe}_{datetime.now().hour}"
                
                if last_signals.get(symbol) != sig_id:
                    
                    emoji = "🗽" if "ORB" in strat_name else ("🚀" if direction == "BUY" else "🔻")
                    
                    msg = (
                        f"{emoji} **{strat_name.upper()} ALERT**\n"
                        f"Coin: **{symbol}**\n"
                        f"Side: **{direction}**\n"
                        f"Entry: {price:.4f}\n\n"
                        f"🎯 TP: {tp}\n"
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
