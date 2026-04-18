import os
import ccxt.async_support as ccxt
import pandas as pd
import asyncio
import pytz
from datetime import datetime
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from flask import Flask
from threading import Thread

# ==========================================
# 1. CONFIGURATION
# ==========================================
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

TIMEFRAME = '4h'            
LOOKBACK_PERIOD = 180       
last_signals = {}           

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

exchange = ccxt.binance({
    'enableRateLimit': True,
    'options': {'defaultType': 'spot'}
})

# ==========================================
# 2. RENDER KEEP-ALIVE
# ==========================================
app = Flask('')
@app.route('/')
def home(): return "Bot Active"
def run_http(): app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))
def keep_alive(): Thread(target=run_http, daemon=True).start()

# ==========================================
# 3. STRATEGY LOGIC (BI-DIRECTIONAL SWEEP)
# ==========================================
def analyze_liquidity_sweep(df):
    try:
        if len(df) < LOOKBACK_PERIOD + 5: return None
        curr = df.iloc[-2] # Last closed 4h candle
        prev_data = df.iloc[-(LOOKBACK_PERIOD + 2):-2]
        
        # Ranges
        range_low = prev_data['low'].min()
        range_high = prev_data['high'].max()
        
        # --- BULLISH SWEEP (LONG) ---
        if (curr['low'] < range_low) and (curr['close'] > range_low) and (curr['close'] > curr['open']):
            entry = curr['close']
            sl = curr['low'] * 0.995 # Tightened SL for 4H
            tp = entry + ((entry - sl) * 2) 
            return ("LONG", entry, sl, tp, range_low, curr['time'])

        # --- BEARISH SWEEP (SHORT) ---
        if (curr['high'] > range_high) and (curr['close'] < range_high) and (curr['close'] < curr['open']):
            entry = curr['close']
            sl = curr['high'] * 1.005 # Tightened SL for 4H
            tp = entry - ((sl - entry) * 2)
            return ("SHORT", entry, sl, tp, range_high, curr['time'])

    except: return None
    return None

# ==========================================
# 4. SCANNER LOOP
# ==========================================
async def swing_scanner(application):
    pkt_tz = pytz.timezone('Asia/Karachi')
    await application.bot.send_message(chat_id=CHAT_ID, text="🚀 **Bi-Directional 4H Sweep Bot Active!**\nMonitoring Long & Short opportunities on 100 pairs.")

    while True:
        try:
            now_pkt = datetime.now(pkt_tz).strftime('%I:%M %p PKT')
            print(f"[{now_pkt}] Scanning for Long/Short sweeps...")
            
            for symbol in SYMBOLS_RAW:
                try:
                    bars = await exchange.fetch_ohlcv(symbol, timeframe=TIMEFRAME, limit=200)
                    df = pd.DataFrame(bars, columns=['time', 'open', 'high', 'low', 'close', 'volume'])
                    signal = analyze_liquidity_sweep(df)
                    
                    if signal:
                        side, entry, sl, tp, level, sig_time = signal
                        sig_id = f"{symbol}_{side}_{sig_time}"
                        
                        if sig_id not in last_signals:
                            emoji = "🟢" if side == "LONG" else "🔴"
                            sig_dt = datetime.fromtimestamp(sig_time / 1000, pkt_tz).strftime('%Y-%m-%d %I:%M %p')
                            
                            msg = (f"{emoji} **{side} LIQUIDITY SWEEP: {symbol}** {emoji}\n\n"
                                   f"Time: {sig_dt} PKT\n"
                                   f"{'Support' if side == 'LONG' else 'Resistance'} Swept: `${level:.4f}`\n\n"
                                   f"Entry: `${entry:.4f}`\n"
                                   f"Stop Loss: `${sl:.4f}`\n"
                                   f"Take Profit: `${tp:.4f}`\n\n"
                                   f"💡 *Strategy: 1x Leverage/Spot-Style Swing*")
                            
                            await application.bot.send_message(chat_id=CHAT_ID, text=msg)
                            last_signals[sig_id] = True
                    await asyncio.sleep(0.1) # Protect API rate limit
                except: continue
            
        except Exception as e:
            print(f"Loop Error: {e}")
            
        await asyncio.sleep(900) # Scan every 15 minutes

# ==========================================
# 5. EXECUTION
# ==========================================
async def main():
    if not BOT_TOKEN or not CHAT_ID: return
    keep_alive()
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    
    asyncio.create_task(swing_scanner(application))
    
    await application.initialize()
    await application.start()
    await application.updater.start_polling(drop_pending_updates=True)
    await asyncio.Event().wait()

if __name__ == '__main__':
    asyncio.run(main())
