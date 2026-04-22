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

TIMEFRAME = '1d'            # Daily Charts
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
def home(): return "EMA 9/21 Daily Bot Active"
def run_http(): app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))
def keep_alive(): Thread(target=run_http, daemon=True).start()

# ==========================================
# 3. INDICATOR CALCULATIONS
# ==========================================
def calculate_indicators(df):
    # EMAs
    df['ema9'] = df['close'].ewm(span=9, adjust=False).mean()
    df['ema21'] = df['close'].ewm(span=21, adjust=False).mean()
    
    # ATR for Risk Management
    df['tr0'] = abs(df['high'] - df['low'])
    df['tr1'] = abs(df['high'] - df['close'].shift(1))
    df['tr2'] = abs(df['low'] - df['close'].shift(1))
    df['tr'] = df[['tr0', 'tr1', 'tr2']].max(axis=1)
    df['atr'] = df['tr'].rolling(window=14).mean()
    return df

# ==========================================
# 4. STRATEGY LOGIC (EMA CROSSOVER)
# ==========================================
def analyze_ema_cross(df):
    try:
        if len(df) < 30: return None
        df = calculate_indicators(df)
        
        curr = df.iloc[-2] # Last fully closed Daily candle
        prev = df.iloc[-3] # The candle before it
        
        # --- BULLISH CROSS (EMA 9 crosses ABOVE 21) ---
        bullish_cross = (prev['ema9'] <= prev['ema21']) and (curr['ema9'] > curr['ema21'])
        
        # --- BEARISH CROSS (EMA 9 crosses BELOW 21) ---
        bearish_cross = (prev['ema9'] >= prev['ema21']) and (curr['ema9'] < curr['ema21'])
        
        if bullish_cross:
            entry = curr['close']
            sl = entry - (2 * curr['atr']) # Stop Loss: 2x ATR below entry
            tp = entry + (4 * curr['atr']) # Take Profit: 1:2 Reward Ratio
            return ("LONG", entry, sl, tp, curr['time'])
            
        if bearish_cross:
            entry = curr['close']
            sl = entry + (2 * curr['atr']) # Stop Loss: 2x ATR above entry
            tp = entry - (4 * curr['atr']) # Take Profit: 1:2 Reward Ratio
            return ("SHORT", entry, sl, tp, curr['time'])
            
    except: return None
    return None

# ==========================================
# 5. SCANNER LOOP
# ==========================================
async def swing_scanner(application):
    pkt_tz = pytz.timezone('Asia/Karachi')
    await application.bot.send_message(chat_id=CHAT_ID, text="🚀 **Daily EMA 9/21 Crossover Bot Started!**\nScanning 100 Binance pairs for trend changes.")

    while True:
        try:
            now_pkt = datetime.now(pkt_tz).strftime('%I:%M %p PKT')
            print(f"[{now_pkt}] Starting Daily EMA Scan...")
            
            for symbol in SYMBOLS_RAW:
                try:
                    # Fetch daily data
                    bars = await exchange.fetch_ohlcv(symbol, timeframe=TIMEFRAME, limit=100)
                    df = pd.DataFrame(bars, columns=['time', 'open', 'high', 'low', 'close', 'volume'])
                    signal = analyze_ema_cross(df)
                    
                    if signal:
                        side, entry, sl, tp, sig_time = signal
                        sig_id = f"{symbol}_{side}_{sig_time}"
                        
                        if sig_id not in last_signals:
                            emoji = "🟢" if side == "LONG" else "🔴"
                            sig_dt = datetime.fromtimestamp(sig_time / 1000, pkt_tz).strftime('%Y-%m-%d')
                            
                            msg = (f"{emoji} **EMA CROSSOVER: {symbol}** {emoji}\n\n"
                                   f"Side: **{side}**\n"
                                   f"Date: {sig_dt}\n\n"
                                   f"Entry: `${entry:.4f}`\n"
                                   f"Stop Loss: `${sl:.4f}`\n"
                                   f"Take Profit: `${tp:.4f}`\n\n"
                                   f"📊 *9 EMA has crossed {'above' if side == 'LONG' else 'below'} 21 EMA.*")
                            
                            await application.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode='Markdown')
                            last_signals[sig_id] = True
                    await asyncio.sleep(0.1)
                except: continue
            
        except Exception as e:
            print(f"Loop Error: {e}")
            
        # Daily candles close once every 24 hours. Scanning every 1 hour is enough.
        await asyncio.sleep(3600) 

# ==========================================
# 6. EXECUTION
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
