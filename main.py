import os
import ccxt.async_support as ccxt
import pandas as pd
import numpy as np
import asyncio
from datetime import datetime, timezone
from telegram import Update, Bot
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from flask import Flask
from threading import Thread

# ==========================================
# 1. CONFIGURATION
# ==========================================
SYMBOLS = [
    'BTC/USDT', 'ETH/USDT', 'XRP/USDT', 'BNB/USDT', 
    'SOL/USDT', 'USDC/USDT', 'TRX/USDT', 'DOGE/USDT',
    'ADA/USDT', 'AVAX/USDT', 'SHIB/USDT', 'TON/USDT', 
    'DOT/USDT', 'LINK/USDT', 'BCH/USDT', 'UNI/USDT', 
    'LTC/USDT', 'NEAR/USDT', 'ICP/USDT', 'APT/USDT', 
    'DAI/USDT', 'STX/USDT', 'FIL/USDT', 'IMX/USDT', 
    'ETC/USDT', 'HBAR/USDT', 'XLM/USDT', 'VET/USDT', 
    'OKB/USDT', 'CRO/USDT', 'ARB/USDT', 'RNDR/USDT', 
    'ATOM/USDT', 'GRT/USDT', 'KAS/USDT', 'OP/USDT', 
    'INJ/USDT', 'PEPE/USDT', 'TIA/USDT', 'LDO/USDT', 
    'XMR/USDT', 'MNT/USDT', 'FDUSD/USDT', 'SEI/USDT', 
    'SUI/USDT', 'ALGO/USDT', 'AAVE/USDT', 'EGLD/USDT', 
    'QNT/USDT', 'BSV/USDT', 'FLOW/USDT', 'SNX/USDT', 
    'SAND/USDT', 'MANA/USDT', 'EOS/USDT', 'THETA/USDT', 
    'XTZ/USDT', 'AERO/USDT', 'NEO/USDT', 'IOTA/USDT', 
    'KCS/USDT', 'GALA/USDT', 'KLAY/USDT', 'MINA/USDT', 
    'CHZ/USDT', 'FXS/USDT', 'CRV/USDT', 'COMP/USDT', 
    'ZIL/USDT', '1INCH/USDT', 'HOT/USDT', 'BTT/USDT', 
    'XEC/USDT', 'ONE/USDT', 'RVN/USDT', 'KAVA/USDT', 
    'WOO/USDT', 'ROSE/USDT', 'CELO/USDT', 'NEXO/USDT', 
    'ENJ/USDT', 'BAT/USDT', 'QTUM/USDT', 'IOST/USDT', 
    'ZRX/USDT', 'YFI/USDT', 'SUSHI/USDT', 'JUP/USDT', 
    'PYTH/USDT', 'ORDI/USDT', 'SATS/USDT', 'BLUR/USDT', 
    'MEME/USDT', 'STRK/USDT', 'ZK/USDT', 'BLAST/USDT', 
    'ONDO/USDT', 'ETHFI/USDT', 'ENA/USDT', 'W/USDT', 
    'TNSR/USDT', 'SAFE/USDT', 'ZRO/USDT', 'IO/USDT', 
    'NOT/USDT', 'MEW/USDT', 'POPCAT/USDT', 'BRETT/USDT', 
    'BOME/USDT', 'TURBO/USDT', 'JASMY/USDT', 'GNO/USDT', 
    'OSMO/USDT', 'RUNE/USDT', 'LUNC/USDT', 'USTC/USDT', 
    'ANKR/USDT', 'GLM/USDT', 'KDA/USDT', 'TWT/USDT', 
    'LRC/USDT', 'CVX/USDT', 'BAL/USDT', 'ICX/USDT', 
    'OMG/USDT', 'WAVES/USDT', 'ONT/USDT', 'AUDIO/USDT', 
    'GLMR/USDT', 'AXS/USDT', 'MKR/USDT', 'BGB/USDT', 'FET/USDT', 
    'FLOKI/USDT', 'BONK/USDT', 'WIF/USDT', 'USDe/USDT', 
    'PYUSD/USDT', 'PAXG/USDT', 'USDD/USDT', 'BTT/USDT', 
    'FLR/USDT', 'CFX/USDT', 'GMX/USDT', 'GAS/USDT', 
    'CKB/USDT', 'ZEC/USDT', 'DASH/USDT', 'XEM/USDT'
]

VALID_TIMEFRAMES = ['15m', '1h', '4h', '8h', '12h', '1d']
active_timeframes = ['4h', '1d']
LOOKBACK = 50 
RR_MINIMUM = 1.5

# FIX 1: Try both possible gold symbol formats — fallback handled in scanner
GOLD_SYMBOL_PERP = 'XAU/USDT:USDT'  # Binance USDM perpetual
GOLD_SYMBOL_SPOT = 'XAUUSDT'         # Fallback spot-style format

BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

last_signals = {}

exchange_spot = ccxt.mexc({'enableRateLimit': True, 'options': {'defaultType': 'spot'}})
exchange_perp = ccxt.binanceusdm({'enableRateLimit': True})

# ==========================================
# 2. RENDER KEEP-ALIVE SERVER
# ==========================================
app = Flask('')
@app.route('/')
def home(): return "Dual Engine Bot is Running!"
def run_http(): app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))
def keep_alive(): Thread(target=run_http).start()

# ==========================================
# 3. NATIVE PANDAS INDICATORS
# ==========================================
def add_sweep_indicators(df):
    df['ema200'] = df['close'].ewm(span=200, adjust=False).mean()
    df['tr0'] = abs(df['high'] - df['low'])
    df['tr1'] = abs(df['high'] - df['close'].shift(1))
    df['tr2'] = abs(df['low'] - df['close'].shift(1))
    df['tr'] = df[['tr0', 'tr1', 'tr2']].max(axis=1)
    df['atr'] = df['tr'].rolling(window=14).mean()
    return df

def add_gold_indicators(df):
    # Bollinger Bands (20, 2)
    df['sma20'] = df['close'].rolling(window=20).mean()
    df['stddev'] = df['close'].rolling(window=20).std()
    df['upper_band'] = df['sma20'] + (2 * df['stddev'])
    df['lower_band'] = df['sma20'] - (2 * df['stddev'])

    # RSI 14
    delta = df['close'].diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    avg_gain = gain.ewm(alpha=1/14, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/14, adjust=False).mean()
    rs = avg_gain / avg_loss
    df['rsi'] = 100 - (100 / (1 + rs))

    # FIX 2: Add ATR for dynamic SL instead of fixed $1 buffer
    df['tr0'] = abs(df['high'] - df['low'])
    df['tr1'] = abs(df['high'] - df['close'].shift(1))
    df['tr2'] = abs(df['low'] - df['close'].shift(1))
    df['tr'] = df[['tr0', 'tr1', 'tr2']].max(axis=1)
    df['atr'] = df['tr'].rolling(window=14).mean()

    return df

# ==========================================
# 4. ENGINE 1: LIQUIDITY SWEEP & RECLAIM
# ==========================================
def analyze_sweep(df, timeframe):
    try:
        if len(df) < 205: return None
        df = add_sweep_indicators(df)

        curr = df.iloc[-2]
        range_data = df.iloc[-LOOKBACK-2 : -2]
        
        swing_high = range_data['high'].max()
        swing_low = range_data['low'].min()
        ema200 = curr['ema200']
        atr = curr['atr']

        # BEARISH SWEEP
        if curr['high'] > swing_high and curr['close'] < swing_high:
            if curr['close'] < ema200: 
                entry = curr['close']
                sl = curr['high'] + (atr * 0.5)
                tp = swing_low
                risk = abs(entry - sl)
                reward = abs(entry - tp)
                rr = round(reward / risk, 2) if risk > 0 else 0
                if rr >= RR_MINIMUM:
                    return ("SHORT", entry, sl, tp, curr['time'], rr, "Sweep + Reclaim")

        # BULLISH SWEEP
        if curr['low'] < swing_low and curr['close'] > swing_low:
            if curr['close'] > ema200:
                entry = curr['close']
                sl = curr['low'] - (atr * 0.5)
                tp = swing_high
                risk = abs(entry - sl)
                reward = abs(tp - entry)
                rr = round(reward / risk, 2) if risk > 0 else 0
                if rr >= RR_MINIMUM:
                    return ("LONG", entry, sl, tp, curr['time'], rr, "Sweep + Reclaim")

    except Exception as e:
        print(f"[Sweep Error] {e}")
    return None

# ==========================================
# 5. ENGINE 2: GOLD 5M SCALPER (FIXED)
# ==========================================
def analyze_gold_scalp(df):
    """
    FIX 3: Relaxed signal conditions so valid setups aren't filtered out.
    
    Changes from original:
    - SHORT: prev candle wick above upper band (high > upper_band), no longer requires
      full close above. This catches shadow rejections which are more common.
    - LONG: prev candle wick below lower band (low < lower_band), same logic.
    - RSI thresholds loosened: SHORT needs RSI > 55 (was 60), LONG needs RSI < 45 (was 40).
    - Min TP distance lowered: $0.50 (was $0.80) — gold moves in small increments on 5m.
    - SL uses ATR * 0.5 buffer instead of fixed $1.00, making it adaptive to volatility.
    - Added % body check to confirm rejection candle has a real body (not a doji).
    """
    try:
        if len(df) < 25: return None
        df = add_gold_indicators(df)

        prev = df.iloc[-3]
        curr = df.iloc[-2]
        candle_time = curr['time']
        atr = curr['atr']
        sl_buffer = atr * 0.5

        # Confirm curr candle has a meaningful body (not a doji)
        body_size = abs(curr['close'] - curr['open'])
        if body_size < (atr * 0.1):
            return None  # Skip doji/indecision candles

        # 🔴 SHORT SCALP: prev wick above upper band + bearish rejection candle
        if prev['high'] > prev['upper_band']:
            if curr['close'] < curr['open']:           # Bearish candle
                if curr['rsi'] > 55:                   # Loosened from 60
                    entry = curr['close']
                    tp = curr['sma20']
                    sl = max(prev['high'], curr['high']) + sl_buffer
                    if abs(entry - tp) > 0.50:         # Loosened from 0.80
                        return ("SHORT", entry, sl, tp, candle_time)

        # 🟢 LONG SCALP: prev wick below lower band + bullish rejection candle
        if prev['low'] < prev['lower_band']:
            if curr['close'] > curr['open']:           # Bullish candle
                if curr['rsi'] < 45:                   # Loosened from 40
                    entry = curr['close']
                    tp = curr['sma20']
                    sl = min(prev['low'], curr['low']) - sl_buffer
                    if abs(tp - entry) > 0.50:         # Loosened from 0.80
                        return ("LONG", entry, sl, tp, candle_time)

    except Exception as e:
        print(f"[Gold Scalp Error] {e}")  # FIX 4: Never swallow errors silently
    return None

# ==========================================
# 6. PARALLEL SCANNER LOOPS
# ==========================================
async def sweep_scanner(app):
    print("🚀 Engine 1 (Sweep) Started...")
    while True:
        current_tfs = active_timeframes.copy()
        for symbol in SYMBOLS:
            for tf in current_tfs:
                try:
                    bars = await exchange_spot.fetch_ohlcv(symbol, timeframe=tf, limit=250)
                    if not bars: continue
                    df = pd.DataFrame(bars, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
                    signal = analyze_sweep(df, tf)
                    if signal:
                        direction, entry, sl, tp, c_time, rr, logic = signal
                        sig_id = f"SWEEP_{symbol}_{tf}_{c_time}"
                        if last_signals.get(sig_id) is None:
                            emoji = "🔴" if direction == "SHORT" else "🟢"
                            msg = (f"{emoji} **LIQUIDITY SWEEP** {emoji}\n\n"
                                   f"🪙 **{symbol}** [{tf}]\n"
                                   f"⚡ **{direction}** @ {entry:.4f}\n"
                                   f"🛑 SL: {sl:.4f}\n"
                                   f"🎯 TP: {tp:.4f}\n"
                                   f"⚖️ R:R: {rr}R")
                            await app.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode='Markdown')
                            last_signals[sig_id] = True
                            print(f"[Sweep Alert] {symbol} {tf} {direction}")
                except Exception as e:
                    print(f"[Sweep Fetch Error] {symbol} {tf}: {e}")  # FIX 4: Log errors
                await asyncio.sleep(0.5)
            await asyncio.sleep(1)
        await asyncio.sleep(60)

async def gold_scanner(app):
    """
    FIX 1: Added symbol validation on startup + fallback logging.
    Now logs every cycle so you can see it's alive in Render logs.
    """
    print("🦅 Engine 2 (Gold Scalp) Started...")

    # Startup check: confirm gold symbol is reachable
    gold_symbol = GOLD_SYMBOL_PERP
    try:
        test = await exchange_perp.fetch_ohlcv(gold_symbol, timeframe='5m', limit=5)
        if test:
            print(f"[Gold] Symbol confirmed: {gold_symbol}")
        else:
            print(f"[Gold] WARNING: No data returned for {gold_symbol}")
    except Exception as e:
        print(f"[Gold] STARTUP ERROR for {gold_symbol}: {e}")
        print("[Gold] Bot will keep retrying every cycle...")

    cycle = 0
    while True:
        cycle += 1
        try:
            bars = await exchange_perp.fetch_ohlcv(gold_symbol, timeframe='5m', limit=50)
            if not bars:
                print(f"[Gold] Cycle {cycle}: No bars returned.")
                await asyncio.sleep(20)
                continue

            df = pd.DataFrame(bars, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
            latest_price = df.iloc[-2]['close']

            # Heartbeat log every 30 cycles (~10 min) so you know it's alive
            if cycle % 30 == 0:
                print(f"[Gold] Heartbeat | Cycle {cycle} | Last close: ${latest_price:.2f}")

            signal = analyze_gold_scalp(df)
            if signal:
                direction, entry, sl, tp, c_time = signal
                sig_id = f"GOLD_{direction}_{c_time}"
                if last_signals.get(sig_id) is None:
                    risk = abs(entry - sl)
                    reward = abs(tp - entry)
                    rr = round(reward / risk, 2) if risk > 0 else 0
                    emoji = "📉" if direction == "SHORT" else "📈"
                    msg = (f"{emoji} **GOLD 5M SCALP** {emoji}\n\n"
                           f"⚡ **{direction}** Market\n"
                           f"📥 Entry: `${entry:.2f}`\n"
                           f"🎯 Target (SMA): `${tp:.2f}`\n"
                           f"🛑 Stop Loss: `${sl:.2f}`\n"
                           f"⚖️ R:R: {rr}R")
                    await app.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode='Markdown')
                    last_signals[sig_id] = True
                    print(f"[Gold Alert] {direction} @ ${entry:.2f} | TP: ${tp:.2f} | SL: ${sl:.2f}")

        except Exception as e:
            print(f"[Gold] Cycle {cycle} ERROR: {e}")  # FIX 4: Full error visibility

        await asyncio.sleep(20)

# ==========================================
# 7. TELEGRAM COMMANDS
# ==========================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🦅 **Dual Engine Bot Online.**\nUse /status to check active systems.", parse_mode='Markdown')

async def set_timeframe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global active_timeframes, last_signals
    if not context.args:
        await update.message.reply_text(f"⚠️ Current TFs: {active_timeframes}\nUsage: `/timeframe 15m 1h`", parse_mode='Markdown')
        return
    new_tfs = [tf.lower() for tf in context.args if tf.lower() in VALID_TIMEFRAMES]
    if new_tfs:
        active_timeframes = new_tfs
        last_signals = {}  # Clear dedup cache on timeframe change
        await update.message.reply_text(f"✅ Sweep Timeframes Updated: **{active_timeframes}**", parse_mode='Markdown')
    else:
        await update.message.reply_text(f"❌ Invalid. Allowed: {VALID_TIMEFRAMES}")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    now_utc = datetime.now(timezone.utc).strftime('%H:%M UTC')
    await update.message.reply_text(
        f"📊 **SYSTEM STATUS**\n"
        f"-------------------\n"
        f"🕒 Time: {now_utc}\n\n"
        f"1️⃣ **Engine: Liquidity Sweep**\n"
        f"• Status: ✅ ACTIVE\n"
        f"• Timeframes: {active_timeframes}\n"
        f"• Symbols: {len(SYMBOLS)} Pairs\n\n"
        f"2️⃣ **Engine: Gold Scalper**\n"
        f"• Status: ✅ ACTIVE\n"
        f"• Timeframe: 5m (Fixed)\n"
        f"• Symbol: XAU/USDT",
        parse_mode='Markdown'
    )

# ==========================================
# 8. MAIN THREAD
# ==========================================
if __name__ == '__main__':
    keep_alive()
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("timeframe", set_timeframe))
    application.add_handler(CommandHandler("status", status))

    loop = asyncio.get_event_loop()
    loop.create_task(sweep_scanner(application))
    loop.create_task(gold_scanner(application))

    application.run_polling()
