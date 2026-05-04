import os
import ccxt.async_support as ccxt
import pandas as pd
import asyncio
import numpy as np
from datetime import datetime, timedelta
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from flask import Flask
from threading import Thread
from collections import defaultdict

# ==========================================
# 1. CONFIGURATION
# ==========================================
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

# Only 1h, 4h, 1d (15m removed)
TIMEFRAME_SETTINGS = {
    '1h':  {'sl_mult': 1.2, 'tp_mult': 2.2, 'ema_period': 200, 'min_atr_pct': 0.0015},
    '4h':  {'sl_mult': 1.3, 'tp_mult': 2.4, 'ema_period': 200, 'min_atr_pct': 0.0020},
    '1d':  {'sl_mult': 1.5, 'tp_mult': 2.6, 'ema_period': 200, 'min_atr_pct': 0.0025}
}

# Dynamic lookback: will be calculated per symbol based on ATR
BASE_LOOKBACK = {'1h': 48, '4h': 30, '1d': 20}

last_signals = {}
signal_stats = {'LONG': 0, 'SHORT': 0, 'by_tf': defaultdict(int)}
last_scan_time = None

SYMBOLS_RAW = [
    'BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'BNB/USDT', 'XRP/USDT',
    'DOGE/USDT', 'TRX/USDT', 'ADA/USDT', 'AVAX/USDT', 'LINK/USDT',
    'SUI/USDT', 'PEPE/USDT', 'SHIB/USDT', 'NEAR/USDT', 'TON/USDT',
    'DOT/USDT', 'BCH/USDT', 'LTC/USDT', 'OP/USDT', 'ARB/USDT',
    'APT/USDT', 'TIA/USDT', 'FET/USDT', 'ICP/USDT', 'RNDR/USDT',
    'INJ/USDT', 'STX/USDT', 'ETC/USDT', 'ATOM/USDT', 'IMX/USDT',
    'HBAR/USDT', 'GRT/USDT', 'SEI/USDT', 'WIF/USDT', 'JUP/USDT',
    'AAVE/USDT', 'LDO/USDT', 'ORDI/USDT', 'PYTH/USDT', 'BOME/USDT',
    'EGLD/USDT', 'ONDO/USDT', 'MKR/USDT', 'FLOKI/USDT', 'ENA/USDT',
    'STRK/USDT', 'THETA/USDT', 'JASMY/USDT', 'AXS/USDT', 'GALA/USDT',
    'MANA/USDT', 'SAND/USDT', 'CRV/USDT', 'SNX/USDT', 'ALGO/USDT',
    'MINA/USDT', 'CHZ/USDT', 'DYDX/USDT', 'ROSE/USDT', 'KAVA/USDT',
    'ZEC/USDT', 'DASH/USDT', 'XMR/USDT', 'IOTA/USDT', 'EOS/USDT',
    'XTZ/USDT', 'ZIL/USDT', 'ENJ/USDT', 'ANKR/USDT', '1INCH/USDT',
    'COMP/USDT', 'LRC/USDT', 'YFI/USDT', 'SUSHI/USDT', 'ZRX/USDT',
    'RVN/USDT', 'BAT/USDT', 'ONT/USDT', 'QTUM/USDT', 'HOT/USDT',
    'IOST/USDT', 'CELO/USDT', 'ONE/USDT', 'KDA/USDT', 'GLM/USDT',
    'XEM/USDT', 'MEME/USDT', 'FLOW/USDT', 'FIL/USDT', 'QNT/USDT',
    'NEO/USDT', 'VET/USDT', 'KAS/USDT', 'BEAMX/USDT', 'WOO/USDT',
    'NOT/USDT', 'TURBO/USDT', 'TAO/USDT', 'W/USDT', 'TNSR/USDT'
]

exchange = None

# ==========================================
# 2. KEEP-ALIVE SERVER
# ==========================================
app = Flask('')
@app.route('/')
def home(): return "Liquidity Footprint Bot Active"

def run_http():
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))

def keep_alive():
    Thread(target=run_http, daemon=True).start()

# ==========================================
# 3. EXCHANGE HANDLER
# ==========================================
async def get_exchange():
    global exchange
    if exchange is None:
        exchange = ccxt.mexc({'enableRateLimit': True, 'options': {'defaultType': 'spot'}})
        await exchange.load_markets()
    return exchange

async def reconnect_exchange():
    global exchange
    if exchange:
        await exchange.close()
    exchange = ccxt.mexc({'enableRateLimit': True, 'options': {'defaultType': 'spot'}})
    await exchange.load_markets()

# ==========================================
# 4. VOLUME FOOTPRINT & WICK DETECTION
# ==========================================
def calculate_delta(df):
    """Approximate buying/selling pressure: (close - open) * volume, normalised"""
    df['delta'] = (df['close'] - df['open']) * df['volume']
    return df

def wick_size(df, level, side='support'):
    """Calculate wick relative to level on previous candle"""
    if side == 'support':
        wick_below = level - df['low']
        return wick_below / (df['high'] - df['low'] + 1e-8)
    else:
        wick_above = df['high'] - level
        return wick_above / (df['high'] - df['low'] + 1e-8)

# ==========================================
# 5. IMPROVED STRATEGY WITH STRENGTH SCORE
# ==========================================
def calculate_dynamic_lookback(df, tf):
    """Adjust lookback based on market volatility (ATR)"""
    atr = df['tr'].rolling(14).mean().iloc[-1]
    price = df['close'].iloc[-1]
    atr_pct = atr / price
    base = BASE_LOOKBACK[tf]
    if atr_pct > 0.03:  # high volatility – shorter lookback
        return max(10, base // 2)
    elif atr_pct < 0.01: # low volatility – longer lookback
        return base * 2
    return base

def analyze_footprint_sweep(df, tf):
    """
    Returns (signal_type, entry, sl, tp, level, strength_score) or None
    Strength score 0-100, only send if >= 70
    """
    try:
        settings = TIMEFRAME_SETTINGS[tf]
        min_atr_pct = settings['min_atr_pct']
        sl_mult = settings['sl_mult']
        tp_mult = settings['tp_mult']
        ema_period = settings['ema_period']

        if len(df) < 60:
            return None

        # Calculate indicators
        df['tr0'] = abs(df['high'] - df['low'])
        df['tr1'] = abs(df['high'] - df['close'].shift(1))
        df['tr2'] = abs(df['low'] - df['close'].shift(1))
        df['tr'] = df[['tr0', 'tr1', 'tr2']].max(axis=1)
        atr = df['tr'].rolling(14).mean()
        current_atr = atr.iloc[-2]
        price = df['close'].iloc[-2]
        if current_atr / price < min_atr_pct:
            return None  # too choppy

        # Dynamic lookback
        lookback = calculate_dynamic_lookback(df, tf)
        
        # Last two closed candles
        curr = df.iloc[-2]
        prev = df.iloc[-3]

        # Range for liquidity
        window = df.iloc[-(lookback+3):-3]
        range_low = window['low'].min()
        range_high = window['high'].max()

        # Volume footprint: delta and volume spike
        df = calculate_delta(df)
        avg_volume = df['volume'].tail(20).mean()
        avg_delta = df['delta'].tail(20).mean()
        
        volume_spike = curr['volume'] > avg_volume * 1.5
        delta_strength = abs(curr['delta']) > abs(avg_delta) * 2.0
        
        # Wick confirmation
        wick_long = False
        # Trend filter
        df['ema'] = df['close'].ewm(span=ema_period, adjust=False).mean()
        current_ema = df['ema'].iloc[-2]
        above_ema = curr['close'] > current_ema
        below_ema = curr['close'] < current_ema

        # ---------- BULLISH RECLAIM ----------
        if (prev['close'] < range_low) and (curr['close'] > range_low) and (curr['close'] > curr['open']) and above_ema:
            # Reclaim margin: close must be > range_low + 0.1% 
            margin = range_low * 0.001
            if curr['close'] < range_low + margin:
                return None
            # Wick on prev candle?
            wick_below = (range_low - prev['low']) / (prev['high'] - prev['low'] + 1e-8)
            if wick_below > 0.3:  # at least 30% wick below support
                wick_long = True
            # Strength score
            score = 0
            if volume_spike: score += 30
            if delta_strength: score += 30
            if wick_long: score += 20
            if above_ema: score += 10
            if curr['close'] > range_low * 1.002: score += 10  # reclaimed well
            if score < 70:
                return None
            entry = curr['close']
            # Place SL below the sweep wick or ATR-based, whichever is tighter
            sl_candidate = min(entry - current_atr * sl_mult, range_low - current_atr * 0.5)
            sl = max(sl_candidate, entry * 0.98)  # max 2% SL
            tp = entry + current_atr * tp_mult
            return ("LONG", entry, sl, tp, range_low, curr.name, score)

        # ---------- BEARISH RECLAIM ----------
        if (prev['close'] > range_high) and (curr['close'] < range_high) and (curr['close'] < curr['open']) and below_ema:
            margin = range_high * 0.001
            if curr['close'] > range_high - margin:
                return None
            wick_above = (prev['high'] - range_high) / (prev['high'] - prev['low'] + 1e-8)
            if wick_above > 0.3:
                wick_long = True
            score = 0
            if volume_spike: score += 30
            if delta_strength: score += 30
            if wick_long: score += 20
            if below_ema: score += 10
            if curr['close'] < range_high * 0.998: score += 10
            if score < 70:
                return None
            entry = curr['close']
            sl_candidate = max(entry + current_atr * sl_mult, range_high + current_atr * 0.5)
            sl = min(sl_candidate, entry * 1.02)
            tp = entry - current_atr * tp_mult
            return ("SHORT", entry, sl, tp, range_high, curr.name, score)

    except Exception as e:
        pass
    return None

# ==========================================
# 6. SCANNER LOOP
# ==========================================
async def swing_scanner(application):
    global last_scan_time
    await application.bot.send_message(
        chat_id=CHAT_ID,
        text="🔍 **Liquidity Footprint Bot Active**\nScanning 1h, 4h, 1d with volume footprint & strength scoring.\nMinimum signal strength: 70/100"
    )
    while True:
        try:
            exch = await get_exchange()
            now_utc = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')
            print(f"[{now_utc}] Scan cycle start")
            for tf in TIMEFRAME_SETTINGS:
                print(f"  Scanning {tf}...")
                for symbol in SYMBOLS_RAW:
                    try:
                        limit = BASE_LOOKBACK[tf] + 50
                        bars = await exch.fetch_ohlcv(symbol, timeframe=tf, limit=limit)
                        if len(bars) < limit:
                            continue
                        df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
                        df.set_index('timestamp', inplace=True)
                        res = analyze_footprint_sweep(df, tf)
                        if res:
                            side, entry, sl, tp, level, sig_time, score = res
                            sig_id = f"{symbol}_{side}_{tf}_{sig_time}"
                            if sig_id not in last_signals:
                                last_signals[sig_id] = True
                                signal_stats[side] += 1
                                signal_stats['by_tf'][tf] += 1
                                sig_dt = sig_time.strftime('%Y-%m-%d %H:%M:%S')
                                emoji = "🟢" if side == "LONG" else "🔴"
                                msg = (
                                    f"{emoji} **STRONG LIQUIDITY RECLAIM ({tf})** {emoji}\n"
                                    f"**Symbol:** `{symbol}`\n"
                                    f"**Side:** {side}\n"
                                    f"**Time (UTC):** {sig_dt}\n"
                                    f"**Swept Level:** `${level:.6f}`\n"
                                    f"**Strength Score:** {score}/100\n\n"
                                    f"**Entry:** `${entry:.6f}`\n"
                                    f"**Stop Loss:** `${sl:.6f}`\n"
                                    f"**Take Profit:** `${tp:.6f}`\n\n"
                                    f"📌 *Volatility adjusted lookback | Volume footprint confirmed*"
                                )
                                await application.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode='Markdown')
                                print(f"Signal: {symbol} {side} on {tf} (score {score})")
                        await asyncio.sleep(0.1)
                    except Exception:
                        continue
            last_scan_time = datetime.utcnow()
            print("Cycle done, sleeping 10 minutes")
            await asyncio.sleep(600)
        except Exception as e:
            print(f"Loop error: {e}")
            await reconnect_exchange()
            await asyncio.sleep(60)

# ==========================================
# 7. TELEGRAM COMMANDS
# ==========================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Footprint Liquidity Bot – sends high‑strength signals only.")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = "/status – bot health\n/stats – signal counts\n/symbols – tracked symbols\n/strength – explanation of scoring"
    await update.message.reply_text(text)

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Active. Last scan: {last_scan_time or 'never'}")

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = f"LONG: {signal_stats['LONG']}\nSHORT: {signal_stats['SHORT']}\nBy TF: {dict(signal_stats['by_tf'])}"
    await update.message.reply_text(msg)

async def symbols(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Tracking {len(SYMBOLS_RAW)} symbols")

async def strength(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = ("Signal strength (0-100) requires:\n"
            "• Volume spike (30 pts)\n"
            "• Delta surge (30 pts)\n"
            "• Long wick on sweep (20 pts)\n"
            "• Trend alignment (10 pts)\n"
            "• Clean reclaim (10 pts)\n"
            "Minimum 70 to send.")
    await update.message.reply_text(text)

# ==========================================
# 8. MAIN
# ==========================================
async def main():
    if not BOT_TOKEN or not CHAT_ID:
        print("Missing env variables")
        return
    keep_alive()
    app_bot = ApplicationBuilder().token(BOT_TOKEN).build()
    app_bot.add_handler(CommandHandler("start", start))
    app_bot.add_handler(CommandHandler("help", help_command))
    app_bot.add_handler(CommandHandler("status", status))
    app_bot.add_handler(CommandHandler("stats", stats))
    app_bot.add_handler(CommandHandler("symbols", symbols))
    app_bot.add_handler(CommandHandler("strength", strength))
    asyncio.create_task(swing_scanner(app_bot))
    await app_bot.initialize()
    await app_bot.start()
    await app_bot.updater.start_polling(drop_pending_updates=True)
    await asyncio.Event().wait()

if __name__ == '__main__':
    asyncio.run(main())