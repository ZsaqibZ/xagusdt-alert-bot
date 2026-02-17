import os
import ccxt.async_support as ccxt
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
# Default Settings
VALID_TIMEFRAMES = ['15m', '1h', '4h', '1d']
active_timeframes = ['1h', '4h'] # default to higher TFs for stability
LOOKBACK = 50 

BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

last_signals = {}
exchange = ccxt.mexc({'enableRateLimit': True, 'options': {'defaultType': 'spot'}})

# ==========================================
# 1. SERVER
# ==========================================
app = Flask('')
@app.route('/')
def home(): return "Trend Sweep Bot Running"
def run_http(): app.run(host='0.0.0.0', port=8080)
def keep_alive(): Thread(target=run_http).start()

# ==========================================
# 2. STRATEGY: TREND + SWEEP
# ==========================================
def analyze_trend_sweep(df, timeframe):
    try:
        # Need enough data for EMA 200
        if len(df) < 205: return None

        curr = df.iloc[-2] # Completed candle
        range_data = df.iloc[-LOOKBACK-2 : -2] # Past 50 candles
        
        swing_high = range_data['high'].max()
        swing_low = range_data['low'].min()
        
        # Indicators
        ema200 = df['ema200'].iloc[-2]
        rsi = df['rsi'].iloc[-2]
        atr = curr['atr']
        
        # --- SELL SETUP (Bearish Sweep in Downtrend) ---
        # 1. Trend is DOWN (Price < EMA 200)
        # 2. RSI is not oversold (> 40) (Room to drop)
        # 3. Sweep High + Reclaim
        if curr['close'] < ema200 and rsi > 40:
            if curr['high'] > swing_high and curr['close'] < swing_high:
                
                # WIDER STOP LOSS: 1.5 ATR
                stop_loss = curr['high'] + (atr * 1.5)
                take_profit = swing_low # Target the recent low
                
                return {
                    "signal": "SELL",
                    "reason": "Trend Continuation Sweep (Bearish)",
                    "price": curr['close'],
                    "sl": stop_loss,
                    "tp": take_profit,
                    "time": curr['time']
                }

        # --- BUY SETUP (Bullish Sweep in Uptrend) ---
        # 1. Trend is UP (Price > EMA 200)
        # 2. RSI is not overbought (< 60) (Room to pump)
        # 3. Sweep Low + Reclaim
        if curr['close'] > ema200 and rsi < 60:
            if curr['low'] < swing_low and curr['close'] > swing_low:
                
                # WIDER STOP LOSS: 1.5 ATR
                stop_loss = curr['low'] - (atr * 1.5)
                take_profit = swing_high # Target the recent high
                
                return {
                    "signal": "BUY",
                    "reason": "Trend Continuation Sweep (Bullish)",
                    "price": curr['close'],
                    "sl": stop_loss,
                    "tp": take_profit,
                    "time": curr['time']
                }
                
    except Exception as e: pass
    return None

# ==========================================
# 3. TELEGRAM COMMANDS
# ==========================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🛡️ **Trend-Sweep Bot Online**\nUse /timeframe to set TFs.")

async def set_timeframe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global active_timeframes, last_signals
    if not context.args: return
    new_tfs = [t for t in context.args if t in VALID_TIMEFRAMES]
    if new_tfs:
        active_timeframes = new_tfs
        last_signals.clear()
        await update.message.reply_text(f"✅ Scanning: {active_timeframes}")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Running on: {active_timeframes}")

# ==========================================
# 4. SCANNER
# ==========================================
async def scan_market(app):
    print("Scanner Started...")
    while True:
        current_tfs = active_timeframes.copy()
        for symbol in SYMBOLS:
            for tf in current_tfs:
                try:
                    # Fetch 300 candles to calculate EMA 200 correctly
                    ohlcv = await exchange.fetch_ohlcv(symbol, timeframe=tf, limit=300)
                    if not ohlcv: continue

                    df = pd.DataFrame(ohlcv, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
                    
                    # Calculate Indicators
                    df['ema200'] = df.ta.ema(length=200) # The Trend Filter
                    df['rsi'] = df.ta.rsi(length=14)     # Momentum
                    df['atr'] = df.ta.atr(length=14)     # Volatility for SL
                    
                    signal = analyze_trend_sweep(df, tf)

                    if signal:
                        sig_id = f"{symbol}_{tf}_{signal['time']}"
                        if last_signals.get(sig_id) is None:
                            
                            entry = signal['price']
                            sl = signal['sl']
                            tp = signal['tp']
                            
                            # Calculate R:R
                            risk = abs(entry - sl)
                            reward = abs(tp - entry)
                            rr = round(reward / risk, 2) if risk > 0 else 0

                            # Only send high quality setup (R:R > 1.5)
                            if rr >= 1.5:
                                emoji = "🟢" if signal['signal'] == "BUY" else "🔴"
                                msg = (
                                    f"{emoji} **TREND SWEEP ALERT**\n"
                                    f"🪙 **{symbol}** [{tf}]\n"
                                    f"Side: **{signal['signal']}**\n"
                                    f"----------------------\n"
                                    f"Entry: {entry}\n"
                                    f"🛑 SL: {sl:.4f} (Safe)\n"
                                    f"🎯 TP: {tp:.4f}\n"
                                    f"⚖️ R:R: {rr}R\n"
                                    f"----------------------\n"
                                    f"Reason: {signal['reason']}"
                                )
                                await app.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode='Markdown')
                                last_signals[sig_id] = True
                                print(f"Sent: {symbol}")

                except Exception: await asyncio.sleep(0.1)
            await asyncio.sleep(0.5)
        await asyncio.sleep(60)

if __name__ == '__main__':
    keep_alive()
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("timeframe", set_timeframe))
    application.add_handler(CommandHandler("status", status))
    loop = asyncio.get_event_loop()
    loop.create_task(scan_market(application))
    application.run_polling()
