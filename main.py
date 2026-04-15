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

# Add this hardcoded list here:
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

# Exchange Setup (BINANCE SPOT MARKET)
exchange = ccxt.binance({
    'enableRateLimit': True,
    'options': {'defaultType': 'spot'} # Strictly Spot Market
})

# ==========================================
# 2. RENDER KEEP-ALIVE SERVER (Optional)
# ==========================================

app = Flask('')

@app.route('/')
def home(): 
    return "Binance 4H Spot Sweep Bot is Running!"

def run_http(): 
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))

def keep_alive(): 
    Thread(target=run_http, daemon=True).start()

# ==========================================
# 3. GET TOP SPOT MARKETS (BINANCE)
# ==========================================

async def get_top_spot_pairs():
    """Fetches the top USDT spot pairs by trading volume on Binance."""
    try:
        print("Fetching latest top spot markets by volume on Binance...")
        markets = await exchange.load_markets()
        tickers = await exchange.fetch_tickers()
        
        usdt_pairs = []
        for symbol, ticker in tickers.items():
            # Filter for pure spot USDT pairs
            # Exclude Binance leveraged tokens (UP/DOWN)
            if symbol.endswith('/USDT') and not any(sub in symbol for sub in ['UP/', 'DOWN/', 'BULL/', 'BEAR/']):
                if ticker.get('quoteVolume') is not None:
                    usdt_pairs.append({'symbol': symbol, 'vol': ticker['quoteVolume']})
                    
        # Sort by volume and slice the top 100 pairs
        usdt_pairs.sort(key=lambda x: x['vol'], reverse=True)
        top_symbols = [pair['symbol'] for pair in usdt_pairs[:TOP_PAIRS_COUNT]]
        
        print(f"✅ Successfully loaded {len(top_symbols)} highly liquid spot pairs.")
        return top_symbols
    except Exception as e:
        print(f"[Market Fetch Error] {e}")
        return []

# ==========================================
# 4. STRATEGY LOGIC (BULLISH SWEEP)
# ==========================================

def analyze_spot_sweep(df):
    try:
        # We need enough history for the 180 lookback period
        if len(df) < LOOKBACK_PERIOD + 5: 
            return None
            
        # curr = Last fully closed 4h candle
        curr = df.iloc[-2]
        
        # prev_data = The 180 candles BEFORE the trigger candle
        prev_data = df.iloc[-(LOOKBACK_PERIOD + 2):-2]
        
        swing_low = prev_data['low'].min()
        
        curr_low = curr['low']
        curr_close = curr['close']
        curr_open = curr['open']
        sig_time = curr['time']

        # --- BULLISH LIQUIDITY SWEEP LOGIC ---
        # 1. The 4h wick must go below the 180-candle swing low
        # 2. The 4h close must recover and close ABOVE the swing low
        # 3. The candle should ideally close green (Close > Open) for added momentum
        if (curr_low < swing_low) and (curr_close > swing_low) and (curr_close > curr_open):
            
            entry = curr_close
            # Stop Loss is placed slightly below the actual sweep wick
            sl = curr_low * 0.98 
            
            # Take Profit set to a 1:2 Risk/Reward ratio for stable portfolio growth
            risk = entry - sl
            tp = entry + (risk * 2) 
            
            return (entry, sl, tp, swing_low, sig_time)

    except Exception as e:
        pass
        
    return None

# ==========================================
# 5. BOT SCANNER LOOP
# ==========================================

async def swing_scanner(application):
    print(f"🦅 Binance Spot Sweep Strategy Started. Timeframe: {TIMEFRAME}...")
    
    # Strictly setting timezone to Pakistan Standard Time (UTC+5)
    pkt_tz = pytz.timezone('Asia/Karachi')
    
    while True:
        symbols = SYMBOLS_RAW
        
        if not symbols:
            await asyncio.sleep(60)
            continue
            
        now_pkt = datetime.now(pkt_tz).strftime('%I:%M %p PKT')
        print(f"[{now_pkt}] Starting 4h sweep scan across top {len(symbols)} pairs...")
        
        for symbol in symbols:
            try:
                # Fetch enough 4h data to cover the 180 lookback period + padding
                bars = await exchange.fetch_ohlcv(symbol, timeframe=TIMEFRAME, limit=200)
                if not bars:
                    continue
                    
                df = pd.DataFrame(bars, columns=['time', 'open', 'high', 'low', 'close', 'volume'])
                
                signal = analyze_spot_sweep(df)
                
                if signal:
                    entry, sl, tp, swing_low, sig_time = signal
                    sig_id = f"{symbol}_{sig_time}"
                    
                    # Prevent duplicate alerts for the same candle
                    if sig_id not in last_signals:
                        sig_datetime = datetime.fromtimestamp(sig_time / 1000, pkt_tz).strftime('%Y-%m-%d %I:%M %p PKT')
                        
                        msg = (f"🟢 **BINANCE SPOT SWEEP** 🟢\n\n"
                               f"**Asset:** {symbol}\n"
                               f"**Time:** {sig_datetime}\n\n"
                               f"📊 **Setup Details:**\n"
                               f"• 180-Candle Low Swept: `${swing_low:.4f}`\n"
                               f"• 4H Rejection Confirmed\n\n"
                               f"⚡ **Swing Trade Plan:**\n"
                               f"• **Buy Entry:** `${entry:.4f}`\n"
                               f"• **Stop Loss:** `${sl:.4f}`\n"
                               f"• **Target (1:2):** `${tp:.4f}`\n\n"
                               f"💡 *Note: Spot trade. Buy the asset and set an OCO/Stop-Limit order.*")
                               
                        await application.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode='Markdown')
                        last_signals[sig_id] = True
                        print(f"[ALERT] Bullish Sweep on {symbol} at {sig_datetime}")
                        
                # Respect Binance rate limits when scanning 100 pairs
                await asyncio.sleep(0.1)
                
            except Exception as e:
                await asyncio.sleep(0.5)
                
        # Since 4h candles close every 4 hours, checking every 15 minutes 
        # ensures we catch the close promptly without spamming the API.
        print("✅ Scan complete. Sleeping for 15 minutes...")
        await asyncio.sleep(900) 

# ==========================================
# 6. TELEGRAM COMMANDS
# ==========================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🦅 **Binance Spot Swing Bot Online.**\nScanning the top 100 crypto pairs on the 4H chart for liquidity sweeps.",
        parse_mode='Markdown'
    )

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pkt_tz = pytz.timezone('Asia/Karachi')
    now_pkt = datetime.now(pkt_tz).strftime('%I:%M %p PKT')
    
    msg = (
        f"📊 **SYSTEM STATUS**\n"
        f"------------------------\n"
        f"🕒 Local Time: {now_pkt}\n"
        f"🔹 Market: Binance Spot\n"
        f"🔹 Scope: Top 100 Volume Pairs\n"
        f"🔹 Timeframe: 4h\n"
        f"🔹 Strategy: 180-Candle Bullish Sweep\n"
        f"🔹 Status: ✅ ACTIVE"
    )
    await update.message.reply_text(msg, parse_mode='Markdown')

# ==========================================
# 7. MAIN EXECUTION
# ==========================================

async def main():
    if not BOT_TOKEN or not CHAT_ID:
        print("❌ ERROR: BOT_TOKEN or CHAT_ID environment variables are missing!")
        return

    # Optional keep-alive for Render/cloud deployment
    keep_alive()

    application = ApplicationBuilder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("status", status))

    asyncio.create_task(swing_scanner(application))

    await application.initialize()
    await application.start()
    
    print("✅ Bot is successfully connected to Telegram and running.")
    await application.updater.start_polling(drop_pending_updates=True)
    await asyncio.Event().wait()

if __name__ == '__main__':
    asyncio.run(main())
