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

# All symbols in MEXC Perpetual format (XXX/USDT:USDT)

SYMBOLS_RAW = [
‘BTC/USDT:USDT’, ‘ETH/USDT:USDT’, ‘XRP/USDT:USDT’, ‘BNB/USDT:USDT’,
‘SOL/USDT:USDT’, ‘TRX/USDT:USDT’, ‘DOGE/USDT:USDT’,
‘ADA/USDT:USDT’, ‘AVAX/USDT:USDT’, ‘SHIB/USDT:USDT’, ‘TON/USDT:USDT’,
‘DOT/USDT:USDT’, ‘LINK/USDT:USDT’, ‘BCH/USDT:USDT’, ‘UNI/USDT:USDT’,
‘LTC/USDT:USDT’, ‘NEAR/USDT:USDT’, ‘ICP/USDT:USDT’, ‘APT/USDT:USDT’,
‘STX/USDT:USDT’, ‘FIL/USDT:USDT’, ‘IMX/USDT:USDT’,
‘ETC/USDT:USDT’, ‘HBAR/USDT:USDT’, ‘XLM/USDT:USDT’, ‘VET/USDT:USDT’,
‘ARB/USDT:USDT’, ‘RNDR/USDT:USDT’,
‘ATOM/USDT:USDT’, ‘GRT/USDT:USDT’, ‘KAS/USDT:USDT’, ‘OP/USDT:USDT’,
‘INJ/USDT:USDT’, ‘PEPE/USDT:USDT’, ‘TIA/USDT:USDT’, ‘LDO/USDT:USDT’,
‘XMR/USDT:USDT’, ‘SEI/USDT:USDT’,
‘SUI/USDT:USDT’, ‘ALGO/USDT:USDT’, ‘AAVE/USDT:USDT’, ‘EGLD/USDT:USDT’,
‘QNT/USDT:USDT’, ‘FLOW/USDT:USDT’, ‘SNX/USDT:USDT’,
‘SAND/USDT:USDT’, ‘MANA/USDT:USDT’, ‘EOS/USDT:USDT’, ‘THETA/USDT:USDT’,
‘XTZ/USDT:USDT’, ‘AERO/USDT:USDT’, ‘NEO/USDT:USDT’, ‘IOTA/USDT:USDT’,
‘GALA/USDT:USDT’, ‘KLAY/USDT:USDT’, ‘MINA/USDT:USDT’,
‘CHZ/USDT:USDT’, ‘CRV/USDT:USDT’, ‘COMP/USDT:USDT’,
‘ZIL/USDT:USDT’, ‘1INCH/USDT:USDT’, ‘HOT/USDT:USDT’,
‘ONE/USDT:USDT’, ‘RVN/USDT:USDT’, ‘KAVA/USDT:USDT’,
‘WOO/USDT:USDT’, ‘ROSE/USDT:USDT’, ‘CELO/USDT:USDT’,
‘ENJ/USDT:USDT’, ‘BAT/USDT:USDT’, ‘QTUM/USDT:USDT’, ‘IOST/USDT:USDT’,
‘ZRX/USDT:USDT’, ‘YFI/USDT:USDT’, ‘SUSHI/USDT:USDT’, ‘JUP/USDT:USDT’,
‘PYTH/USDT:USDT’, ‘ORDI/USDT:USDT’, ‘BLUR/USDT:USDT’,
‘MEME/USDT:USDT’, ‘STRK/USDT:USDT’, ‘ZK/USDT:USDT’,
‘ONDO/USDT:USDT’, ‘ENA/USDT:USDT’, ‘W/USDT:USDT’,
‘SAFE/USDT:USDT’, ‘ZRO/USDT:USDT’, ‘IO/USDT:USDT’,
‘NOT/USDT:USDT’, ‘POPCAT/USDT:USDT’,
‘TURBO/USDT:USDT’, ‘JASMY/USDT:USDT’, ‘GNO/USDT:USDT’,
‘RUNE/USDT:USDT’, ‘LUNC/USDT:USDT’,
‘ANKR/USDT:USDT’, ‘TWT/USDT:USDT’,
‘LRC/USDT:USDT’, ‘CVX/USDT:USDT’, ‘BAL/USDT:USDT’,
‘OMG/USDT:USDT’, ‘WAVES/USDT:USDT’, ‘ONT/USDT:USDT’,
‘GLMR/USDT:USDT’, ‘AXS/USDT:USDT’, ‘MKR/USDT:USDT’, ‘FET/USDT:USDT’,
‘FLOKI/USDT:USDT’, ‘BONK/USDT:USDT’, ‘WIF/USDT:USDT’,
‘FLR/USDT:USDT’, ‘CFX/USDT:USDT’, ‘GMX/USDT:USDT’,
‘CKB/USDT:USDT’, ‘ZEC/USDT:USDT’, ‘DASH/USDT:USDT’,
‘OKB/USDT:USDT’, ‘CRO/USDT:USDT’, ‘XEC/USDT:USDT’,
‘OSMO/USDT:USDT’, ‘ICX/USDT:USDT’, ‘AUDIO/USDT:USDT’,
‘GAS/USDT:USDT’, ‘XEM/USDT:USDT’, ‘GLM/USDT:USDT’,
‘KDA/USDT:USDT’, ‘FXS/USDT:USDT’, ‘BRETT/USDT:USDT’,
‘BOME/USDT:USDT’, ‘SATS/USDT:USDT’, ‘BSV/USDT:USDT’,
‘NEXO/USDT:USDT’, ‘BGB/USDT:USDT’, ‘MEW/USDT:USDT’,
‘ETHFI/USDT:USDT’, ‘TNSR/USDT:USDT’, ‘USTC/USDT:USDT’,
‘KCS/USDT:USDT’, ‘MNT/USDT:USDT’, ‘BTT/USDT:USDT’,
]

# Populated at startup after filtering against MEXC’s live market list

SYMBOLS = []

VALID_TIMEFRAMES = [‘15m’, ‘1h’, ‘4h’, ‘8h’, ‘12h’, ‘1d’]
active_timeframes = [‘1h’, ‘4h’]
LOOKBACK = 50
RR_MINIMUM = 1.5

# Gold on Bybit Perpetual (no geo-blocking)

GOLD_SYMBOL = ‘XAU/USDT:USDT’

BOT_TOKEN = os.environ.get(“BOT_TOKEN”)
CHAT_ID = os.environ.get(“CHAT_ID”)

last_signals = {}

# ==========================================

# EXCHANGES

# ==========================================

exchange_mexc = ccxt.mexc({
‘enableRateLimit’: True,
‘options’: {‘defaultType’: ‘swap’}  # Perpetuals
})

exchange_bybit = ccxt.bybit({
‘enableRateLimit’: True,
‘options’: {‘defaultType’: ‘swap’}  # Perpetuals
})

# ==========================================

# 2. RENDER KEEP-ALIVE SERVER

# ==========================================

app = Flask(’’)

@app.route(’/’)
def home(): return “Dual Engine Bot is Running!”

def run_http(): app.run(host=‘0.0.0.0’, port=int(os.environ.get(“PORT”, 8080)))

def keep_alive(): Thread(target=run_http).start()

# ==========================================

# 3. STARTUP: VALIDATE SYMBOLS AGAINST MEXC

# ==========================================

async def load_valid_symbols():
“””
Fetch MEXC’s actual perpetual market list on startup.
Only keep symbols MEXC genuinely supports — eliminates all fetch errors.
“””
global SYMBOLS
print(”[Startup] Fetching MEXC perpetual markets…”)
try:
markets = await exchange_mexc.load_markets()
available = set(markets.keys())
SYMBOLS = [s for s in SYMBOLS_RAW if s in available]
removed = [s for s in SYMBOLS_RAW if s not in available]
print(f”[Startup] ✅ {len(SYMBOLS)} valid symbols confirmed on MEXC perpetuals.”)
if removed:
print(f”[Startup] ⚠️ Removed {len(removed)} symbols not listed on MEXC: {removed}”)
except Exception as e:
print(f”[Startup] ERROR loading MEXC markets: {e}”)
SYMBOLS = SYMBOLS_RAW
print(”[Startup] ⚠️ Falling back to raw symbol list.”)

# ==========================================

# 4. INDICATORS

# ==========================================

def add_sweep_indicators(df):
df[‘ema200’] = df[‘close’].ewm(span=200, adjust=False).mean()
df[‘tr0’] = abs(df[‘high’] - df[‘low’])
df[‘tr1’] = abs(df[‘high’] - df[‘close’].shift(1))
df[‘tr2’] = abs(df[‘low’] - df[‘close’].shift(1))
df[‘tr’] = df[[‘tr0’, ‘tr1’, ‘tr2’]].max(axis=1)
df[‘atr’] = df[‘tr’].rolling(window=14).mean()
return df

def add_gold_indicators(df):
# Bollinger Bands (20, 2)
df[‘sma20’] = df[‘close’].rolling(window=20).mean()
df[‘stddev’] = df[‘close’].rolling(window=20).std()
df[‘upper_band’] = df[‘sma20’] + (2 * df[‘stddev’])
df[‘lower_band’] = df[‘sma20’] - (2 * df[‘stddev’])

```
# RSI 14
delta = df['close'].diff()
gain = delta.where(delta > 0, 0)
loss = -delta.where(delta < 0, 0)
avg_gain = gain.ewm(alpha=1/14, adjust=False).mean()
avg_loss = loss.ewm(alpha=1/14, adjust=False).mean()
rs = avg_gain / avg_loss
df['rsi'] = 100 - (100 / (1 + rs))

# ATR 14 for dynamic SL
df['tr0'] = abs(df['high'] - df['low'])
df['tr1'] = abs(df['high'] - df['close'].shift(1))
df['tr2'] = abs(df['low'] - df['close'].shift(1))
df['tr'] = df[['tr0', 'tr1', 'tr2']].max(axis=1)
df['atr'] = df['tr'].rolling(window=14).mean()
return df
```

# ==========================================

# 5. ENGINE 1: LIQUIDITY SWEEP & RECLAIM

# ==========================================

def analyze_sweep(df, timeframe):
try:
if len(df) < 205: return None
df = add_sweep_indicators(df)

```
    curr = df.iloc[-2]
    range_data = df.iloc[-LOOKBACK-2 : -2]

    swing_high = range_data['high'].max()
    swing_low = range_data['low'].min()
    ema200 = curr['ema200']
    atr = curr['atr']

    # 🔴 BEARISH SWEEP
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

    # 🟢 BULLISH SWEEP
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
    print(f"[Sweep Analysis Error] {e}")
return None
```

# ==========================================

# 6. ENGINE 2: GOLD 5M SCALPER

# ==========================================

def analyze_gold_scalp(df):
try:
if len(df) < 25: return None
df = add_gold_indicators(df)

```
    prev = df.iloc[-3]
    curr = df.iloc[-2]
    candle_time = curr['time']
    atr = curr['atr']
    sl_buffer = atr * 0.5

    # Skip doji / indecision candles
    body_size = abs(curr['close'] - curr['open'])
    if body_size < (atr * 0.1):
        return None

    # 🔴 SHORT: prev wick above upper band + current bearish candle
    if prev['high'] > prev['upper_band']:
        if curr['close'] < curr['open']:
            if curr['rsi'] > 55:
                entry = curr['close']
                tp = curr['sma20']
                sl = max(prev['high'], curr['high']) + sl_buffer
                if abs(entry - tp) > 0.50:
                    return ("SHORT", entry, sl, tp, candle_time)

    # 🟢 LONG: prev wick below lower band + current bullish candle
    if prev['low'] < prev['lower_band']:
        if curr['close'] > curr['open']:
            if curr['rsi'] < 45:
                entry = curr['close']
                tp = curr['sma20']
                sl = min(prev['low'], curr['low']) - sl_buffer
                if abs(tp - entry) > 0.50:
                    return ("LONG", entry, sl, tp, candle_time)

except Exception as e:
    print(f"[Gold Scalp Error] {e}")
return None
```

# ==========================================

# 7. ENGINE 1 SCANNER LOOP

# ==========================================

async def sweep_scanner(application):
print(“🚀 Engine 1 (Sweep - MEXC Perpetuals) Started…”)
while True:
if not SYMBOLS:
print(”[Sweep] Waiting for symbol list…”)
await asyncio.sleep(5)
continue

```
    current_tfs = active_timeframes.copy()
    for symbol in SYMBOLS:
        for tf in current_tfs:
            try:
                bars = await exchange_mexc.fetch_ohlcv(symbol, timeframe=tf, limit=250)
                if not bars:
                    continue
                df = pd.DataFrame(bars, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
                signal = analyze_sweep(df, tf)
                if signal:
                    direction, entry, sl, tp, c_time, rr, logic = signal
                    sig_id = f"SWEEP_{symbol}_{tf}_{c_time}"
                    if last_signals.get(sig_id) is None:
                        display_symbol = symbol.split(':')[0]  # BTC/USDT:USDT -> BTC/USDT
                        emoji = "🔴" if direction == "SHORT" else "🟢"
                        msg = (f"{emoji} **LIQUIDITY SWEEP** {emoji}\n\n"
                               f"🪙 **{display_symbol}** [{tf}]\n"
                               f"⚡ **{direction}** @ {entry:.4f}\n"
                               f"🛑 SL: {sl:.4f}\n"
                               f"🎯 TP: {tp:.4f}\n"
                               f"⚖️ R:R: {rr}R")
                        await application.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode='Markdown')
                        last_signals[sig_id] = True
                        print(f"[Sweep Alert] {display_symbol} {tf} {direction} | RR: {rr}")
            except Exception as e:
                print(f"[Sweep Fetch Error] {symbol} {tf}: {e}")
            await asyncio.sleep(0.5)
        await asyncio.sleep(1)
    await asyncio.sleep(60)
```

# ==========================================

# 8. ENGINE 2 SCANNER LOOP

# ==========================================

async def gold_scanner(application):
print(“🦅 Engine 2 (Gold Scalp - Bybit) Started…”)

```
# Startup connectivity check
try:
    test = await exchange_bybit.fetch_ohlcv(GOLD_SYMBOL, timeframe='5m', limit=5)
    if test:
        print(f"[Gold] ✅ Bybit confirmed. Symbol: {GOLD_SYMBOL}")
    else:
        print(f"[Gold] ⚠️ No data returned for {GOLD_SYMBOL} on Bybit.")
except Exception as e:
    print(f"[Gold] ⚠️ Bybit startup check failed: {e} — will keep retrying...")

cycle = 0
while True:
    cycle += 1
    try:
        bars = await exchange_bybit.fetch_ohlcv(GOLD_SYMBOL, timeframe='5m', limit=50)
        if not bars:
            print(f"[Gold] Cycle {cycle}: No bars returned.")
            await asyncio.sleep(20)
            continue

        df = pd.DataFrame(bars, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        latest_price = df.iloc[-2]['close']

        # Heartbeat every 30 cycles (~10 min)
        if cycle % 30 == 0:
            print(f"[Gold] Heartbeat | Cycle {cycle} | XAU Last Close: ${latest_price:.2f}")

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
                await application.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode='Markdown')
                last_signals[sig_id] = True
                print(f"[Gold Alert] {direction} @ ${entry:.2f} | TP: ${tp:.2f} | SL: ${sl:.2f} | RR: {rr}")

    except Exception as e:
        print(f"[Gold] Cycle {cycle} ERROR: {e}")

    await asyncio.sleep(20)
```

# ==========================================

# 9. TELEGRAM COMMANDS

# ==========================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
await update.message.reply_text(
“🦅 **Dual Engine Bot Online.**\n”
“Use /status to check active systems.”,
parse_mode=‘Markdown’
)

async def set_timeframe(update: Update, context: ContextTypes.DEFAULT_TYPE):
global active_timeframes, last_signals
if not context.args:
await update.message.reply_text(
f”⚠️ Current TFs: {active_timeframes}\nUsage: `/timeframe 15m 1h`”,
parse_mode=‘Markdown’
)
return
new_tfs = [tf.lower() for tf in context.args if tf.lower() in VALID_TIMEFRAMES]
if new_tfs:
active_timeframes = new_tfs
last_signals = {}  # Reset dedup cache on TF change
await update.message.reply_text(
f”✅ Sweep Timeframes Updated: **{active_timeframes}**”,
parse_mode=‘Markdown’
)
else:
await update.message.reply_text(f”❌ Invalid. Allowed: {VALID_TIMEFRAMES}”)

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
now_utc = datetime.now(timezone.utc).strftime(’%H:%M UTC’)
await update.message.reply_text(
f”📊 **SYSTEM STATUS**\n”
f”—————––\n”
f”🕒 Time: {now_utc}\n\n”
f”1️⃣ **Engine: Liquidity Sweep**\n”
f”• Exchange: MEXC Perpetuals\n”
f”• Status: ✅ ACTIVE\n”
f”• Timeframes: {active_timeframes}\n”
f”• Symbols Loaded: {len(SYMBOLS)}\n\n”
f”2️⃣ **Engine: Gold Scalper**\n”
f”• Exchange: Bybit Perpetuals\n”
f”• Status: ✅ ACTIVE\n”
f”• Timeframe: 5m (Fixed)\n”
f”• Symbol: XAU/USDT”,
parse_mode=‘Markdown’
)

# ==========================================

# 10. MAIN

# ==========================================

async def main():
keep_alive()

```
# Validate symbols against MEXC before anything starts
await load_valid_symbols()

application = ApplicationBuilder().token(BOT_TOKEN).build()
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("timeframe", set_timeframe))
application.add_handler(CommandHandler("status", status))

# Launch both engines concurrently
asyncio.create_task(sweep_scanner(application))
asyncio.create_task(gold_scanner(application))

await application.initialize()
await application.start()
await application.updater.start_polling()

# Keep process alive
await asyncio.Event().wait()
```

if **name** == ‘**main**’:
asyncio.run(main())