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

# Dynamic Lookbacks translated directly from your Pine Script
TIMEFRAME_SETTINGS = {
    '15m': {'lookback': 96, 'sl_mult': 1.2, 'tp_mult': 2.4},
    '1h':  {'lookback': 100, 'sl_mult': 1.5, 'tp_mult': 3.0}, # Fallback 100
    '4h':  {'lookback': 60,  'sl_mult': 1.8, 'tp_mult': 3.6},
    '1d':  {'lookback': 20,  'sl_mult': 2.0, 'tp_mult': 4.0}
}

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
def home(): return "Dynamic Liquidity Bot Active"
def run_http(): app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))
def keep_alive(): Thread(target=run_http, daemon=True).start()

# ==========================================
# 3. STRATEGY LOGIC (DYNAMIC RECLAIM SWEEP)
# ==========================================
def analyze_dynamic_sweep(df, tf):
    try:
        lookback = TIMEFRAME_SETTINGS[tf]['lookback']
        
        # Need enough data for the lookback + current candles
        if len(df) < lookback + 5: return None
        
        curr = df.iloc[-2] # Last closed candle
        prev = df.iloc[-3] # The "trap" candle before it
        
        # Slicing the dataframe to get the exact rolling window BEFORE the 'prev' candle
        # This accurately mirrors `ta.lowest(low, lookback)[1]` in Pine Script
        window_data = df.iloc[-(lookback + 3):-3]
        
        range_low = window_data['low'].min()
        range_high = window_data['high'].max()
        
        # ATR for Risk Management
        df['tr0'] = abs(df['high'] - df['low'])
        df['tr1'] = abs(df['high'] - df['close'].shift(1))
        df['tr2'] = abs(df['low'] - df['close'].shift(1))
        df['tr'] = df[['tr0', 'tr1', 'tr2']].max(axis=1)
        curr_atr = df['tr'].rolling(window=14).mean().iloc[-2]

        sl_mult = TIMEFRAME_SETTINGS[tf]['sl_mult']
        tp_mult = TIMEFRAME_SETTINGS[tf]['tp_mult']

        # --- Reclaim Logic ---
        # Bullish: Prev candle closed below Support, Curr candle closed green above Support
        bullish_reclaim = (prev['close'] < range_low) and (curr['close'] > range_low) and (curr['close'] > curr['open'])
        
        # Bearish: Prev candle closed above Resistance, Curr candle closed red below Resistance
        bearish_reclaim = (prev['close'] > range_high) and (curr['close'] < range_high) and (curr['close'] < curr['open'])
        
        if bullish_reclaim:
            entry = curr['close']
            sl = entry - (curr_atr * sl_mult)
            tp = entry + (curr_atr * tp_mult)
            return ("LONG", entry, sl, tp, range_low, curr['time'])
            
        if bearish_reclaim:
            entry = curr['close']
            sl = entry + (curr_atr * sl_mult)
            tp = entry - (curr_atr * tp_mult)
            return ("SHORT", entry, sl, tp, range_high, curr['time'])
            
    except: return None
    return None

# ==========================================
# 4. SCANNER LOOP
# ==========================================
async def swing_scanner(application):
    pkt_tz = pytz.timezone('Asia/Karachi')
    await application.bot.send_message(
        chat_id=CHAT_ID, 
        text="🚀 **Dynamic Liquidity Bot Started!**\nScanning 15m, 1h, 4h, and 1d with Auto-Lookbacks."
    )

    while True:
        try:
            for tf in TIMEFRAME_SETTINGS.keys():
                now_pkt = datetime.now(pkt_tz).strftime('%I:%M %p PKT')
                print(f"[{now_pkt}] Scanning {tf} (Lookback: {TIMEFRAME_SETTINGS[tf]['lookback']})...")
                
                for symbol in SYMBOLS_RAW:
                    try:
                        # Fetch enough bars to cover the max lookback safely
                        limit = TIMEFRAME_SETTINGS[tf]['lookback'] + 20
                        bars = await exchange.fetch_ohlcv(symbol, timeframe=tf, limit=limit)
                        df = pd.DataFrame(bars, columns=['time', 'open', 'high', 'low', 'close', 'volume'])
                        signal = analyze_dynamic_sweep(df, tf)
                        
                        if signal:
                            side, entry, sl, tp, level, sig_time = signal
                            sig_id = f"{symbol}_{side}_{tf}_{sig_time}"
                            
                            if sig_id not in last_signals:
                                emoji = "🟢" if side == "LONG" else "🔴"
                                sig_dt = datetime.fromtimestamp(sig_time / 1000, pkt_tz).strftime('%Y-%m-%d %I:%M %p')
                                
                                msg = (f"{emoji} **LIQUIDITY RECLAIM ({tf}): {symbol}** {emoji}\n\n"
                                       f"Side: **{side}**\n"
                                       f"Time: {sig_dt} PKT\n"
                                       f"Level Swept: `${level:.4f}`\n\n"
                                       f"Entry: `${entry:.4f}`\n"
                                       f"Stop Loss: `${sl:.4f}`\n"
                                       f"Take Profit: `${tp:.4f}`\n\n"
                                       f"📊 *Auto-Lookback Used: {TIMEFRAME_SETTINGS[tf]['lookback']} candles*")
                                
                                await application.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode='Markdown')
                                last_signals[sig_id] = True
                                
                        await asyncio.sleep(0.1) # Protect Binance Rate Limits
                    except: continue
                
            print("Full Multi-TF scan finished. Sleeping for 10 minutes...")
            await asyncio.sleep(600)

        except Exception as e:
            print(f"Global Loop Error: {e}")
            await asyncio.sleep(60)

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
