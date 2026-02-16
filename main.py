import os
import ccxt.async_support as ccxt
import pandas as pd
import pandas_ta as ta
import asyncio
from datetime import datetime
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from flask import Flask
from threading import Thread

# ==========================================
# CONFIGURATION
# ==========================================
SYMBOLS = [
    'BTC/USDT', 'ETH/USDT', 'XRP/USDT', 'BNB/USDT', 'SOL/USDT', 'USDC/USDT', 'TRX/USDT', 'DOGE/USDT', 'ADA/USDT', 'AVAX/USDT', 'SHIB/USDT', 'TON/USDT', 'DOT/USDT', 'LINK/USDT', 'BCH/USDT', 'UNI/USDT', 'LTC/USDT', 'NEAR/USDT', 'ICP/USDT', 'APT/USDT', 'DAI/USDT', 'STX/USDT', 'FIL/USDT', 'IMX/USDT', 'ETC/USDT', 'HBAR/USDT', 'XLM/USDT', 'VET/USDT', 'OKB/USDT', 'CRO/USDT', 'ARB/USDT', 'RNDR/USDT', 'ATOM/USDT', 'GRT/USDT', 'KAS/USDT', 'OP/USDT', 'INJ/USDT', 'PEPE/USDT', 'TIA/USDT', 'LDO/USDT', 'XMR/USDT', 'MNT/USDT', 'FDUSD/USDT', 'SEI/USDT', 'SUI/USDT', 'ALGO/USDT', 'AAVE/USDT', 'EGLD/USDT', 'QNT/USDT', 'BSV/USDT', 'FLOW/USDT', 'SNX/USDT', 'SAND/USDT', 'MANA/USDT', 'EOS/USDT', 'THETA/USDT', 'XTZ/USDT', 'AERO/USDT', 'NEO/USDT', 'IOTA/USDT', 'KCS/USDT', 'GALA/USDT', 'KLAY/USDT', 'MINA/USDT', 'CHZ/USDT', 'FXS/USDT', 'CRV/USDT', 'COMP/USDT', 'ZIL/USDT', '1INCH/USDT', 'HOT/USDT', 'BTT/USDT', 'XEC/USDT', 'ONE/USDT', 'RVN/USDT', 'KAVA/USDT', 'WOO/USDT', 'ROSE/USDT', 'CELO/USDT', 'NEXO/USDT', 'ENJ/USDT', 'BAT/USDT', 'QTUM/USDT', 'IOST/USDT', 'ZRX/USDT', 'YFI/USDT', 'SUSHI/USDT', 'JUP/USDT', 'PYTH/USDT', 'ORDI/USDT', 'SATS/USDT', 'BLUR/USDT', 'MEME/USDT', 'STRK/USDT', 'ZK/USDT', 'BLAST/USDT', 'ONDO/USDT', 'ETHFI/USDT', 'ENA/USDT', 'W/USDT', 'TNSR/USDT', 'SAFE/USDT', 'ZRO/USDT', 'IO/USDT', 'NOT/USDT', 'MEW/USDT', 'POPCAT/USDT', 'BRETT/USDT', 'BOME/USDT', 'TURBO/USDT', 'JASMY/USDT', 'GNO/USDT', 'OSMO/USDT', 'RUNE/USDT', 'LUNC/USDT', 'USTC/USDT', 'ANKR/USDT', 'GLM/USDT', 'KDA/USDT', 'TWT/USDT', 'LRC/USDT', 'CVX/USDT', 'BAL/USDT', 'ICX/USDT', 'OMG/USDT', 'WAVES/USDT', 'ONT/USDT', 'AUDIO/USDT', 'GLMR/USDT', 'AXS/USDT', 'MKR/USDT', 'BGB/USDT', 'FET/USDT', 'FLOKI/USDT', 'BONK/USDT', 'WIF/USDT', 'USDe/USDT', 'PYUSD/USDT', 'PAXG/USDT', 'USDD/USDT', 'BTT/USDT', 'FLR/USDT', 'CFX/USDT', 'GMX/USDT', 'GAS/USDT', 'CKB/USDT', 'ZEC/USDT', 'DASH/USDT', 'XEM/USDT'

]

# Default Settings
VALID_TIMEFRAMES = ['15m', '1h', '4h', '8h', '12h', '1d']
active_timeframes = ['15m', '1h', '4h'] # Default start
LOOKBACK = 50 

BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

last_signals = {}
exchange = ccxt.mexc({'enableRateLimit': True, 'options': {'defaultType': 'spot'}})

# ==========================================
# 1. KEEP ALIVE SERVER
# ==========================================
app = Flask('')

@app.route('/')
def home():
    return f"Bot Running. Scanning: {active_timeframes}"

def run_http():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_http)
    t.start()

# ==========================================
# 2. STRATEGY: SOLID LIQUIDITY SWEEP
# ==========================================
def analyze_sweep(df, timeframe):
    """
    Strict Liquidity Sweep Strategy:
    1. Swing High/Low of last 50 candles.
    2. Sweep: Price wicks beyond Swing High/Low.
    3. Reclaim: Price CLOSES back inside the range.
    4. Volume: Sweep candle volume > Average Volume.
    """
    try:
        if len(df) < LOOKBACK + 5: return None

        curr = df.iloc[-2] # Completed candle
        range_data = df.iloc[-LOOKBACK-2 : -2] # Past 50 candles
        
        swing_high = range_data['high'].max()
        swing_low = range_data['low'].min()
        
        atr = curr['atr']
        avg_vol = curr['vol_avg']
        
        # --- SELL SETUP (Bearish Sweep) ---
        # Wick went ABOVE High, but Close stayed BELOW High
        if curr['high'] > swing_high and curr['close'] < swing_high:
            if curr['vol'] > avg_vol:
                stop_loss = curr['high'] + (atr * 0.5)
                take_profit = swing_low
                return {
                    "signal": "SELL",
                    "reason": "Bearish Sweep + Reclaim",
                    "price": curr['close'],
                    "sl": stop_loss,
                    "tp": take_profit,
                    "time": curr['time'],
                    "swing_level": swing_high
                }

        # --- BUY SETUP (Bullish Sweep) ---
        # Wick went BELOW Low, but Close stayed ABOVE Low
        if curr['low'] < swing_low and curr['close'] > swing_low:
            if curr['vol'] > avg_vol:
                stop_loss = curr['low'] - (atr * 0.5)
                take_profit = swing_high
                return {
                    "signal": "BUY",
                    "reason": "Bullish Sweep + Reclaim",
                    "price": curr['close'],
                    "sl": stop_loss,
                    "tp": take_profit,
                    "time": curr['time'],
                    "swing_level": swing_low
                }
                
    except Exception as e: pass
    return None

# ==========================================
# 3. TELEGRAM COMMANDS
# ==========================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🦅 **Liquidity Sweep Bot Online**\n\n"
        "I strictly hunt for Stop Hunts & Reclaims.\n"
        "Commands:\n"
        "• `/timeframe 15m 1h` - Set active timeframes\n"
        "• `/status` - Check running settings"
    )

async def set_timeframe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global active_timeframes, last_signals
    
    if not context.args:
        await update.message.reply_text(f"⚠️ Current: {active_timeframes}\nUsage: `/timeframe 15m 4h`")
        return

    new_tfs = [tf.lower() for tf in context.args if tf.lower() in VALID_TIMEFRAMES]
    
    if new_tfs:
        active_timeframes = new_tfs
        last_signals.clear() # Clear cache to allow fresh signals on new settings
        await update.message.reply_text(f"✅ Timeframes Updated: **{active_timeframes}**")
    else:
        await update.message.reply_text(f"❌ Invalid. Allowed: {VALID_TIMEFRAMES}")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"📊 **SYSTEM STATUS**\n"
        f"-------------------\n"
        f"🕒 Timeframes: {active_timeframes}\n"
        f"👀 Lookback: {LOOKBACK} candles\n"
        f"🪙 Coins: {len(SYMBOLS)}\n"
        f"✅ System: ONLINE"
    )

# ==========================================
# 4. MARKET SCANNER LOOP
# ==========================================
async def scan_market(app):
    print("Scanner loop started...")
    
    while True:
        # Loop through the DYNAMIC 'active_timeframes' list
        current_tfs = active_timeframes.copy() 
        
        for symbol in SYMBOLS:
            for tf in current_tfs:
                try:
                    # Fetch Data
                    ohlcv = await exchange.fetch_ohlcv(symbol, timeframe=tf, limit=LOOKBACK + 30)
                    if not ohlcv: continue

                    df = pd.DataFrame(ohlcv, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
                    df['atr'] = df.ta.atr(length=14)
                    df['vol_avg'] = df.ta.sma(close=df['vol'], length=20)
                    
                    # Analyze
                    signal = analyze_sweep(df, tf)

                    if signal:
                        sig_id = f"{symbol}_{tf}_{signal['time']}"

                        if last_signals.get(sig_id) is None:
                            entry = signal['price']
                            risk = abs(entry - signal['sl'])
                            reward = abs(signal['tp'] - entry)
                            rr_ratio = round(reward / risk, 2) if risk > 0 else 0

                            emoji = "🟢" if signal['signal'] == "BUY" else "🔴"
                            
                            msg = (
                                f"{emoji} **LIQUIDITY SWEEP**\n"
                                f"🪙 **{symbol}** [{tf}]\n"
                                f"Side: **{signal['signal']}**\n"
                                f"Reason: {signal['reason']}\n\n"
                                f"🚪 Entry: {entry:.4f}\n"
                                f"🛑 Stop: {signal['sl']:.4f}\n"
                                f"🎯 Target: {signal['tp']:.4f}\n"
                                f"⚖️ R:R: {rr_ratio}R"
                            )

                            await app.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode='Markdown')
                            last_signals[sig_id] = True
                            print(f"Alert: {symbol} {tf}")

                except Exception as e:
                    print(f"Error {symbol}: {e}")
                    await asyncio.sleep(0.5)

            await asyncio.sleep(1) # Pacing between symbols

        await asyncio.sleep(60) # Wait before next full scan

# ==========================================
# 5. MAIN EXECUTION
# ==========================================
if __name__ == '__main__':
    keep_alive()
    
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("timeframe", set_timeframe))
    application.add_handler(CommandHandler("status", status))

    loop = asyncio.get_event_loop()
    loop.create_task(scan_market(application))
    
    application.run_polling()
