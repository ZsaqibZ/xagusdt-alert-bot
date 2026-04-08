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

TIMEFRAME = '1d' # Daily timeframe for Swing Trading
LOOKBACK_PERIOD = 30 # Look for the lowest low of the last 30 days
TOP_PAIRS_COUNT = 150 # Number of top volume pairs to scan

last_signals = {} # To prevent duplicate alerts

# Exchange Setup (SPOT MARKET)
exchange = ccxt.mexc({
    'enableRateLimit': True,
    'options': {'defaultType': 'spot'} # Strictly Spot Market
})

# ==========================================
# 2. RENDER KEEP-ALIVE SERVER
# ==========================================

app = Flask('')

@app.route('/')
def home(): 
    return "Spot Swing Sweep Bot is Running!"

def run_http(): 
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))

def keep_alive(): 
    Thread(target=run_http, daemon=True).start()

# ==========================================
# 3. GET TOP SPOT MARKETS
# ==========================================

async def get_top_spot_pairs():
    """Fetches the top USDT spot pairs by trading volume to ensure liquidity."""
    try:
        print("Fetching latest top spot markets by volume...")
        markets = await exchange.load_markets()
        tickers = await exchange.fetch_tickers()
        
        usdt_pairs = []
        for symbol, ticker in tickers.items():
            # Filter for pure spot USDT pairs (no leveraged tokens like 3L/3S, no futures)
            if symbol.endswith('/USDT') and ':' not in symbol and '3L' not in symbol and '3S' not in symbol:
                if ticker.get('quoteVolume') is not None:
                    usdt_pairs.append({'symbol': symbol, 'vol': ticker['quoteVolume']})
                    
        # Sort by volume and slice the top N pairs
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
        # We need enough history for the lookback period
        if len(df) < LOOKBACK_PERIOD + 5: 
            return None
            
        # curr = Last fully closed daily candle
        curr = df.iloc[-2]
        
        # prev_data = The 30 days BEFORE the trigger candle
        prev_data = df.iloc[-(LOOKBACK_PERIOD + 2):-2]
        
        swing_low = prev_data['low'].min()
        
        curr_low = curr['low']
        curr_close = curr['close']
        curr_open = curr['open']
        sig_time = curr['time']

        # --- BULLISH LIQUIDITY SWEEP LOGIC ---
        # 1. The daily wick must go below the 30-day swing low
        # 2. The daily close must recover and close ABOVE the swing low
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
    print(f"🦅 Spot Swing Sweep Strategy Started. Timeframe: {TIMEFRAME}...")
    
    pkt_tz = pytz.timezone('Asia/Karachi')
    
    while True:
        symbols = await get_top_spot_pairs()
        
        if not symbols:
            await asyncio.sleep(60)
            continue
            
        print(f"Starting daily sweep scan across {len(symbols)} pairs...")
        
        for symbol in symbols:
            try:
                # Fetch daily data
                bars = await exchange.fetch_ohlcv(symbol, timeframe=TIMEFRAME, limit=40)
                if not bars:
                    continue
                    
                df = pd.DataFrame(bars, columns=['time', 'open', 'high', 'low', 'close', 'volume'])
                
                signal = analyze_spot_sweep(df)
                
                if signal:
                    entry, sl, tp, swing_low, sig_time = signal
                    sig_id = f"{symbol}_{sig_time}"
                    
                    # Prevent duplicate alerts for the same candle
                    if sig_id not in last_signals:
                        sig_datetime = datetime.fromtimestamp(sig_time / 1000, pkt_tz).strftime('%Y-%m-%d PKT')
                        
                        msg = (f"🟢 **SPOT LIQUIDITY SWEEP** 🟢\n\n"
                               f"**Asset:** {symbol} (Spot)\n"
                               f"**Date:** {sig_datetime}\n\n"
                               f"📊 **Setup Details:**\n"
                               f"• 30-Day Low Swept: `${swing_low:.4f}`\n"
                               f"• Daily Rejection Confirmed\n\n"
                               f"⚡ **Swing Trade Plan:**\n"
                               f"• **Buy Entry:** `${entry:.4f}`\n"
                               f"• **Stop Loss:** `${sl:.4f}`\n"
                               f"• **Target (1:2):** `${tp:.4f}`\n\n"
                               f"💡 *Note: Spot trade. Buy the asset and set an OCO/Stop-Limit order.*")
                               
                        await application.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode='Markdown')
                        last_signals[sig_id] = True
                        print(f"[ALERT] Bullish Sweep on {symbol}")
                        
                # Respect exchange rate limits when scanning hundreds of pairs
                await asyncio.sleep(0.1)
                
            except Exception as e:
                # Fail silently for individual bad pairs to keep the loop running
                await asyncio.sleep(0.5)
                
        # Since this is a Daily timeframe strategy, the bot will scan all 150 pairs,
        # then sleep for 30 minutes before checking again.
        print("✅ Scan complete. Sleeping for 30 minutes...")
        await asyncio.sleep(1800) 

# ==========================================
# 6. TELEGRAM COMMANDS
# ==========================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🦅 **Spot Swing Bot Online.**\nScanning the top 150 crypto pairs on the Daily chart for liquidity sweeps.",
        parse_mode='Markdown'
    )

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pkt_tz = pytz.timezone('Asia/Karachi')
    now_pkt = datetime.now(pkt_tz).strftime('%I:%M %p PKT')
    
    msg = (
        f"📊 **SYSTEM STATUS**\n"
        f"------------------------\n"
        f"🕒 Local Time: {now_pkt}\n"
        f"🔹 Market: MEXC Spot\n"
        f"🔹 Scope: Top 150 Volume Pairs\n"
        f"🔹 Timeframe: 1d (Daily)\n"
        f"🔹 Strategy: 30-Day Bullish Sweep\n"
        f"🔹 Status: ✅ ACTIVE"
    )
    await update.message.reply_text(msg, parse_mode='Markdown')

# ==========================================
# 7. MAIN EXECUTION
# ==========================================

async def main():
    keep_alive()

    application = ApplicationBuilder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("status", status))

    asyncio.create_task(swing_scanner(application))

    await application.initialize()
    await application.start()
    await application.updater.start_polling(drop_pending_updates=True)
    await asyncio.Event().wait()

if __name__ == '__main__':
    asyncio.run(main())
