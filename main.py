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

# --- INDEPENDENT SETTINGS ---
# Strategy 1 & 2 (EMA + Sweep) Settings
main_timeframe = '4h'       # Default 4H as requested
active_mode = 'both'        # 'ema', 'sweep', 'both' (Does NOT affect ORB)
sweep_lookback = 50         # Updated to 50 candles as requested

# Strategy 3 (ORB) Settings
orb_active = True           # Independent Switch
orb_timeframe = '15m'       # FIXED at 15m forever

last_signals = {}           # Memory

# ==========================================
# 1. KEEP ALIVE SERVER
# ==========================================
app = Flask('')

@app.route('/')
def home():
    return f"Alive! Main TF: {main_timeframe} | ORB: {'ON' if orb_active else 'OFF'}"

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
        # Get the timestamp of the candle we are checking
        candle_time = df['time'].iloc[-2] 
        
        prev_ema9 = df['ema9'].iloc[-3]
        prev_ema21 = df['ema21'].iloc[-3]
        curr_ema9 = df['ema9'].iloc[-2]
        curr_ema21 = df['ema21'].iloc[-2]
        close_price = df['close'].iloc[-2]
        atr = df['atr'].iloc[-2]

        if prev_ema9 < prev_ema21 and curr_ema9 > curr_ema21:
            return "BUY", "EMA Cross", close_price, (close_price - 2*atr), "Dynamic / 2R", candle_time
        elif prev_ema9 > prev_ema21 and curr_ema9 < curr_ema21:
            return "SELL", "EMA Cross", close_price, (close_price + 2*atr), "Dynamic / 2R", candle_time
    except: pass
    return None, None, None, None, None, None

# ==========================================
# 3. STRATEGY B: LIQUIDATION SWEEP (SFP)
# ==========================================
def strategy_liquidation_sweep(df, lookback):
    try:
        candle_time = df['time'].iloc[-2]
        
        # Lookback Logic using the variable 'lookback' (50)
        past_data = df.iloc[-lookback-2:-2] 
        range_high = past_data['high'].max()
        range_low = past_data['low'].min()
        
        curr_high = df['high'].iloc[-2]
        curr_low = df['low'].iloc[-2]
        curr_close = df['close'].iloc[-2]
        atr = df['atr'].iloc[-2]

        # SELL SWEEP
        if curr_high > range_high and curr_close < range_high:
            return "SELL", "Liquidity Sweep", curr_close, (curr_high + 0.5*atr), "Dynamic / 3R", candle_time
        
        # BUY SWEEP
        if curr_low < range_low and curr_close > range_low:
            return "BUY", "Liquidity Sweep", curr_close, (curr_low - 0.5*atr), "Dynamic / 3R", candle_time
            
    except: pass
    return None, None, None, None, None, None

# ==========================================
# 4. STRATEGY C: FORTIFIED NY ORB (15m Fixed)
# ==========================================
def strategy_ny_orb(df):
    try:
        candle_time = df['time'].iloc[-2] # Timestamp of the breakdown candle
        
        # Time Check (NY Session)
        now_utc = datetime.now(timezone.utc)
        start_time = now_utc.replace(hour=13, minute=30, second=0, microsecond=0)
        end_time = now_utc.replace(hour=16, minute=0, second=0, microsecond=0)
        
        if not (start_time <= now_utc <= end_time):
            return None, None, None, None, None, None

        # Find 13:30 Candle
        df['dt'] = pd.to_datetime(df['time'], unit='ms', utc=True)
        orb_candle = df[df['dt'] == start_time]
        
        if orb_candle.empty:
            return None, None, None, None, None, None

        orb_high = orb_candle['high'].values[0]
        orb_low = orb_candle['low'].values[0]

        # Logic
        last_close = df['close'].iloc[-2]
        prev_close = df['close'].iloc[-3]
        curr_ema9 = df['ema9'].iloc[-2]
        curr_ema21 = df['ema21'].iloc[-2]
        curr_vol = df['vol'].iloc[-2]
        avg_vol = df['vol_avg'].iloc[-2]

        if (last_close > orb_high and prev_close > orb_high) and (curr_ema9 > curr_ema21):
             if curr_vol > avg_vol:
                return "BUY", "NY ORB (Strong)", last_close, orb_low, "TRAIL EMA 9", candle_time

        if (last_close < orb_low and prev_close < orb_low) and (curr_ema9 < curr_ema21):
            if curr_vol > avg_vol:
                return "SELL", "NY ORB (Strong)", last_close, orb_high, "TRAIL EMA 9", candle_time

    except Exception as e: pass 
    return None, None, None, None, None, None

# ==========================================
# 5. MASTER ANALYSIS (MULTI-TIMEFRAME)
# ==========================================
async def analyze_market(symbol):
    results = []

    # --- BLOCK 1: MAIN TIMEFRAME (EMA & SWEEP) ---
    try:
        # Fetch Data for Main Timeframe (e.g., 4h)
        bars_main = EXCHANGE.fetch_ohlcv(symbol, timeframe=main_timeframe, limit=100)
        df_main = pd.DataFrame(bars_main, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        
        # Indicators
        df_main['ema9'] = df_main.ta.ema(length=9)
        df_main['ema21'] = df_main.ta.ema(length=21)
        df_main['atr'] = df_main.ta.atr(length=14)

        if active_mode in ['ema', 'both']:
            res = strategy_ema_cross(df_main)
            if res[0]: results.append(res) # Appends (Dir, Name, Price, SL, TP, TIME)

        if active_mode in ['sweep', 'both']:
            # Pass the custom lookback (50) here
            res = strategy_liquidation_sweep(df_main, lookback=sweep_lookback)
            if res[0]: results.append(res)

    except Exception as e:
        print(f"Main TF Error {symbol}: {e}")


    # --- BLOCK 2: ORB TIMEFRAME (15m FIXED) ---
    if orb_active:
        try:
            # Always fetch 15m for ORB, regardless of what main_timeframe is
            bars_orb = EXCHANGE.fetch_ohlcv(symbol, timeframe=orb_timeframe, limit=100)
            df_orb = pd.DataFrame(bars_orb, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
            
            # ORB Specific Indicators
            df_orb['ema9'] = df_orb.ta.ema(length=9)
            df_orb['ema21'] = df_orb.ta.ema(length=21)
            df_orb['vol_avg'] = df_orb.ta.sma(close=df_orb['vol'], length=20)

            res_orb = strategy_ny_orb(df_orb)
            if res_orb[0]: results.append(res_orb)

        except Exception as e:
            # Quiet fail if 15m data not available for this specific coin
            pass
            
    return results

# ==========================================
# 6. TELEGRAM COMMANDS
# ==========================================
async def set_main_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global active_mode
    try:
        mode = context.args[0].lower()
        if mode in ['ema', 'sweep', 'both']:
            active_mode = mode
            await update.message.reply_text(f"✅ Main Strategy Mode: **{mode.upper()}**")
        else:
            await update.message.reply_text("❌ Use: /mode ema, /mode sweep, /mode both")
    except:
        await update.message.reply_text(f"ℹ️ Current Main Mode: {active_mode}")

async def set_main_timeframe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global main_timeframe, last_signals
    try:
        tf = context.args[0]
        # Allow standard timeframes
        if tf in ['15m', '1h', '4h', '1d']:
            main_timeframe = tf
            last_signals = {} # Clear cache to allow new signals on new TF
            await update.message.reply_text(f"✅ Main Timeframe: **{tf}** (Affects EMA & Sweep)")
        else:
            await update.message.reply_text("❌ Invalid. Try 15m, 1h, 4h, 1d")
    except:
        await update.message.reply_text(f"ℹ️ Current Main TF: {main_timeframe}")

async def orb_control(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global orb_active
    try:
        action = context.args[0].lower()
        if action == "pause":
            orb_active = False
            await update.message.reply_text("⏸️ NY ORB Strategy **PAUSED**.")
        elif action == "resume":
            orb_active = True
            await update.message.reply_text("▶️ NY ORB Strategy **RESUMED** (Active 13:30-16:00 UTC).")
        else:
            await update.message.reply_text("❌ Use: `/orb pause` or `/orb resume`")
    except:
        status_text = "RUNNING" if orb_active else "PAUSED"
        await update.message.reply_text(f"ℹ️ ORB Status: **{status_text}**")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    now_utc = datetime.now(timezone.utc).strftime('%H:%M UTC')
    await update.message.reply_text(
        f"🟢 **SYSTEM STATUS**\n"
        f"------------------\n"
        f"🕒 Time: {now_utc}\n\n"
        f"1️⃣ **Main Engine (EMA/Sweep)**\n"
        f"• TF: {main_timeframe}\n"
        f"• Mode: {active_mode}\n"
        f"• Lookback: {sweep_lookback}\n\n"
        f"2️⃣ **ORB Engine (NY Session)**\n"
        f"• TF: 15m (Fixed)\n"
        f"• Status: {'✅ ACTIVE' if orb_active else '⏸️ PAUSED'}"
    )

# ==========================================
# 7. SCANNER LOOP
# ==========================================
async def scan_market(app):
    global last_signals
    print("Scanner Started...")
    
    while True:
        for symbol in SYMBOLS:
            signals = await analyze_market(symbol)
            
            for (direction, strat_name, price, sl, tp, candle_time) in signals:
                
                # --- FIX FOR DUPLICATE ALERTS ---
                # We use the 'candle_time' as the unique ID. 
                # The bot will ONLY alert if it sees a NEW candle timestamp for this strategy.
                # Format: "BTC/USDT_Liquidity Sweep_BUY_167890000"
                sig_id = f"{symbol}_{strat_name}_{direction}_{candle_time}"
                
                if last_signals.get(symbol + strat_name) != sig_id:
                    
                    emoji = "🗽" if "ORB" in strat_name else ("🚀" if direction == "BUY" else "🔻")
                    tf_display = "15m" if "ORB" in strat_name else main_timeframe
                    
                    msg = (
                        f"{emoji} **{strat_name.upper()}**\n"
                        f"Coin: **{symbol}**\n"
                        f"Side: **{direction}**\n"
                        f"Entry: {price:.4f}\n\n"
                        f"🎯 TP: {tp}\n"
                        f"🛑 SL: {sl:.4f}\n"
                        f"TF: {tf_display}"
                    )
                    
                    try:
                        await app.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode='Markdown')
                        print(f"Sent: {symbol} {strat_name}")
                        # Update memory with the specific candle time
                        last_signals[symbol + strat_name] = sig_id
                    except: pass
            
            await asyncio.sleep(1) # Rate limit

        await asyncio.sleep(60)

if __name__ == '__main__':
    keep_alive()
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    
    # New Command Structure
    application.add_handler(CommandHandler("mode", set_main_mode))      # Controls EMA/Sweep only
    application.add_handler(CommandHandler("timeframe", set_main_timeframe)) # Controls EMA/Sweep only
    application.add_handler(CommandHandler("orb", orb_control))         # Controls ORB pause/resume
    application.add_handler(CommandHandler("status", status))

    loop = asyncio.get_event_loop()
    loop.create_task(scan_market(application))
    application.run_polling()
