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

# --- SETTINGS ---
main_timeframe = '4h'       # Supported: 15m, 1h, 4h, 8h, 12h, 1d
active_mode = 'all'         # 'trend', 'vwap', 'funding', 'sweep', 'all'
sweep_lookback = 50         # Lookback for liquidity zones

# ORB Settings (Engine 2)
orb_active = True           
orb_timeframe = '15m'       

last_signals = {}           

# ==========================================
# 1. KEEP ALIVE SERVER
# ==========================================
app = Flask('')

@app.route('/')
def home():
    return f"Alive! TF: {main_timeframe} | Strategies: 4 | ORB: {'ON' if orb_active else 'OFF'}"

def run_http():
    port = int(os.environ.get("PORT", 5000)) 
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_http)
    t.start()

# ==========================================
# 2. STRATEGY 1: TREND-PULLBACK TRAP
# ==========================================
def strategy_trend_pullback(df, lookback):
    try:
        candle_time = df['time'].iloc[-2]
        
        curr_close = df['close'].iloc[-2]
        curr_open = df['open'].iloc[-2]
        curr_low = df['low'].iloc[-2]
        curr_high = df['high'].iloc[-2]
        
        ema50 = df['ema50'].iloc[-2]
        ema200 = df['ema200'].iloc[-2]
        atr = df['atr'].iloc[-2]

        # Liquidity Zones
        past_lows = df['low'].iloc[-lookback-2:-2]
        recent_low = past_lows.min()
        
        past_highs = df['high'].iloc[-lookback-2:-2]
        recent_high = past_highs.max()

        # LONG
        if (curr_close > ema200) and (curr_low < recent_low) and (curr_close > curr_open):
            sl = curr_low - (0.5 * atr)
            tp = curr_close + (3 * atr) 
            return "BUY", "Trend Trap 🪤", curr_close, sl, tp, candle_time

        # SHORT
        if (curr_close < ema200) and (curr_high > recent_high) and (curr_close < curr_open):
            sl = curr_high + (0.5 * atr)
            tp = curr_close - (3 * atr)
            return "SELL", "Trend Trap 🪤", curr_close, sl, tp, candle_time

    except: pass
    return None, None, None, None, None, None

# ==========================================
# 3. STRATEGY 2: VWAP + RSI MEAN REVERSION
# ==========================================
def strategy_vwap_rsi(df):
    try:
        candle_time = df['time'].iloc[-2]
        curr_close = df['close'].iloc[-2]
        curr_open = df['open'].iloc[-2]
        
        vwap = df['vwap'].iloc[-2]
        rsi = df['rsi'].iloc[-2]
        atr = df['atr'].iloc[-2]

        # SHORT (Overextended Up)
        if (curr_close > vwap) and (rsi >= 70) and (curr_close < curr_open):
            sl = df['high'].iloc[-2] + atr
            tp = vwap 
            return "SELL", "VWAP Sniper 🎯", curr_close, sl, tp, candle_time

        # LONG (Overextended Down)
        if (curr_close < vwap) and (rsi <= 30) and (curr_close > curr_open):
            sl = df['low'].iloc[-2] - atr
            tp = vwap
            return "BUY", "VWAP Sniper 🎯", curr_close, sl, tp, candle_time

    except: pass
    return None, None, None, None, None, None

# ==========================================
# 4. STRATEGY 3: BREAKOUT RETEST + FUNDING
# ==========================================
async def strategy_funding_retest(df, symbol):
    try:
        candle_time = df['time'].iloc[-2]
        curr_close = df['close'].iloc[-2]
        curr_open = df['open'].iloc[-2]
        curr_high = df['high'].iloc[-2]
        curr_low = df['low'].iloc[-2]
        
        ema50 = df['ema50'].iloc[-2]
        atr = df['atr'].iloc[-2]
        
        is_short_setup = (curr_close < ema50) and (curr_high >= ema50) and (curr_close < curr_open)
        is_long_setup = (curr_close > ema50) and (curr_low <= ema50) and (curr_close > curr_open)

        if not (is_short_setup or is_long_setup):
            return None, None, None, None, None, None

        # Fetch Funding
        funding_info = EXCHANGE.fetch_funding_rate(symbol)
        funding_rate = funding_info['fundingRate']

        # Short (Crowded Longs)
        if is_short_setup and funding_rate > 0.0001:
            sl = curr_high + atr
            tp = curr_close - (3 * atr)
            return "SELL", "Funding Bias 🏦", curr_close, sl, tp, candle_time

        # Long (Crowded Shorts)
        if is_long_setup and funding_rate < -0.0001:
            sl = curr_low - atr
            tp = curr_close + (3 * atr)
            return "BUY", "Funding Bias 🏦", curr_close, sl, tp, candle_time

    except: pass
    return None, None, None, None, None, None

# ==========================================
# 5. STRATEGY 4: RAW LIQUIDITY SWEEP
# ==========================================
def strategy_raw_sweep(df, lookback):
    try:
        candle_time = df['time'].iloc[-2]
        past_data = df.iloc[-lookback-2:-2] 
        range_high = past_data['high'].max()
        range_low = past_data['low'].min()
        curr_high = df['high'].iloc[-2]
        curr_low = df['low'].iloc[-2]
        curr_close = df['close'].iloc[-2]
        atr = df['atr'].iloc[-2]

        if curr_high > range_high and curr_close < range_high:
            return "SELL", "Raw Sweep 🧹", curr_close, (curr_high + 0.5*atr), "Dynamic", candle_time
        
        if curr_low < range_low and curr_close > range_low:
            return "BUY", "Raw Sweep 🧹", curr_close, (curr_low - 0.5*atr), "Dynamic", candle_time
    except: pass
    return None, None, None, None, None, None

# ==========================================
# 6. ORB STRATEGY (Engine 2)
# ==========================================
def strategy_ny_orb(df):
    try:
        candle_time = df['time'].iloc[-2]
        now_utc = datetime.now(timezone.utc)
        start_time = now_utc.replace(hour=13, minute=30, second=0, microsecond=0)
        end_time = now_utc.replace(hour=16, minute=0, second=0, microsecond=0)
        
        if not (start_time <= now_utc <= end_time): return None, None, None, None, None, None

        df['dt'] = pd.to_datetime(df['time'], unit='ms', utc=True)
        orb_candle = df[df['dt'] == start_time]
        
        if orb_candle.empty: return None, None, None, None, None, None

        orb_high = orb_candle['high'].values[0]
        orb_low = orb_candle['low'].values[0]
        last_close = df['close'].iloc[-2]
        prev_close = df['close'].iloc[-3]
        curr_ema9 = df['ema9'].iloc[-2]
        curr_ema21 = df['ema21'].iloc[-2]
        
        # FIXED: Use 'volume' instead of 'vol'
        curr_vol = df['volume'].iloc[-2]
        avg_vol = df['vol_avg'].iloc[-2]

        if (last_close > orb_high and prev_close > orb_high) and (curr_ema9 > curr_ema21) and (curr_vol > avg_vol):
            return "BUY", "NY ORB (Strong)", last_close, orb_low, "Trail EMA9", candle_time

        if (last_close < orb_low and prev_close < orb_low) and (curr_ema9 < curr_ema21) and (curr_vol > avg_vol):
            return "SELL", "NY ORB (Strong)", last_close, orb_high, "Trail EMA9", candle_time

    except: pass 
    return None, None, None, None, None, None

# ==========================================
# 7. MASTER ANALYSIS
# ==========================================
async def analyze_market(symbol):
    results = []

    # --- ENGINE 1: MULTI-STRATEGY CORE ---
    try:
        # FIXED: Column name changed to 'volume' for pandas_ta compatibility
        bars_main = EXCHANGE.fetch_ohlcv(symbol, timeframe=main_timeframe, limit=200)
        df = pd.DataFrame(bars_main, columns=['time', 'open', 'high', 'low', 'close', 'volume'])
        
        # Calc Indicators
        df['ema50'] = df.ta.ema(length=50)
        df['ema200'] = df.ta.ema(length=200)
        df['vwap'] = df.ta.vwap() # This was causing the crash, now fixed!
        df['rsi'] = df.ta.rsi(length=14)
        df['atr'] = df.ta.atr(length=14)
        
        if active_mode in ['trend', 'all']:
            res = strategy_trend_pullback(df, sweep_lookback)
            if res[0]: results.append(res)

        if active_mode in ['vwap', 'all']:
            res = strategy_vwap_rsi(df)
            if res[0]: results.append(res)
            
        if active_mode in ['funding', 'all']:
            res = await strategy_funding_retest(df, symbol)
            if res[0]: results.append(res)

        if active_mode in ['sweep', 'all']:
            res = strategy_raw_sweep(df, sweep_lookback)
            if res[0]: results.append(res)

    except Exception as e: pass

    # --- ENGINE 2: NY ORB ---
    if orb_active:
        try:
            # FIXED: Column name changed to 'volume'
            bars_orb = EXCHANGE.fetch_ohlcv(symbol, timeframe=orb_timeframe, limit=100)
            df_orb = pd.DataFrame(bars_orb, columns=['time', 'open', 'high', 'low', 'close', 'volume'])
            df_orb['ema9'] = df_orb.ta.ema(length=9)
            df_orb['ema21'] = df_orb.ta.ema(length=21)
            # FIXED: Explicitly referencing 'volume'
            df_orb['vol_avg'] = df_orb.ta.sma(close=df_orb['volume'], length=20)

            res_orb = strategy_ny_orb(df_orb)
            if res_orb[0]: results.append(res_orb)
        except: pass
            
    return results

# ==========================================
# 8. TELEGRAM COMMANDS
# ==========================================
async def set_main_timeframe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global main_timeframe, last_signals
    try:
        tf = context.args[0]
        if tf in ['15m', '1h', '4h', '8h', '12h', '1d']: 
            main_timeframe = tf
            last_signals = {} 
            await update.message.reply_text(f"✅ Main Engine TF: **{tf}**")
        else:
            await update.message.reply_text("❌ Invalid. Use: 15m, 1h, 4h, 8h, 12h, 1d")
    except: pass

async def orb_control(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global orb_active
    try:
        action = context.args[0].lower()
        if action == "pause": orb_active = False; await update.message.reply_text("⏸️ ORB Paused.")
        elif action == "resume": orb_active = True; await update.message.reply_text("▶️ ORB Resumed.")
    except: pass

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"🟢 **SYSTEM STATUS**\n"
        f"Engine 1 (Main): {main_timeframe}\n"
        f"Strategies: Trend Trap, VWAP Sniper, Funding Bias, Raw Sweep\n"
        f"Engine 2 (ORB): {'ON' if orb_active else 'OFF'}"
    )

# ==========================================
# 9. MAIN SCANNER
# ==========================================
async def scan_market(app):
    global last_signals
    print("Scanner Started...")
    
    while True:
        for symbol in SYMBOLS:
            signals = await analyze_market(symbol)
            
            for (direction, strat_name, price, sl, tp, candle_time) in signals:
                sig_id = f"{symbol}_{strat_name}_{direction}_{candle_time}"
                
                if last_signals.get(symbol + strat_name) != sig_id:
                    emoji = "🗽" if "ORB" in strat_name else ("🚀" if direction == "BUY" else "🔻")
                    
                    msg = (
                        f"{emoji} **{strat_name.upper()} ALERT**\n"
                        f"Coin: **{symbol}**\n"
                        f"Side: **{direction}**\n"
                        f"Entry: {price:.4f}\n\n"
                        f"🎯 TP: {tp}\n"
                        f"🛑 SL: {sl:.4f}\n"
                        f"TF: {main_timeframe if 'ORB' not in strat_name else '15m'}"
                    )
                    try:
                        await app.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode='Markdown')
                        last_signals[symbol + strat_name] = sig_id
                    except: pass
            
            await asyncio.sleep(1)
        await asyncio.sleep(60)

if __name__ == '__main__':
    keep_alive()
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("timeframe", set_main_timeframe))
    application.add_handler(CommandHandler("orb", orb_control))
    application.add_handler(CommandHandler("status", status))
    loop = asyncio.get_event_loop()
    loop.create_task(scan_market(application))
    application.run_polling()
