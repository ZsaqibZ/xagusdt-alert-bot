import os
import ccxt.async_support as ccxt  # Use async version for better performance
import pandas as pd
import pandas_ta as ta
import asyncio
from datetime import datetime, timezone
from telegram import Bot
from flask import Flask
from threading import Thread

# ==========================================
# CONFIGURATION
# ==========================================
SYMBOLS = [
    # Crypto
    'SOL/USDT', 'XRP/USDT', 'HYPE/USDT', 'RIVER/USDT', 'LINK/USDT',
    'PIPPIN/USDT', 'DOGE/USDT', 'ZEC/USDT', 'ASTER/USDT', 'PENGUIN/USDT',
    'BNB/USDT', 'FARTCOIN/USDT', 'ENA/USDT', 'XMR/USDT', 'PUMP/USDT',
    'DASH/USDT', 'BCH/USDT', 'HBAR/USDT', 'UNI/USDT', 'ZEN/USDT',
    'CHZ/USDT', 'BEAT/USDT', 'FIL/USDT', 'LIT/USDT', 'XPL/USDT',
    'LDO/USDT', 'ETHFI/USDT', 'FLOKI/USDT', 'STRK/USDT', 'FHE/USDT',
    'POL/USDT', 'GIGGLE/USDT', 'SPX/USDT', 'LIGHT/USDT', 'ETC/USDT',
    'COAI/USDT', 'JASMY/USDT', 'JELLY/USDT', 'FOLKS/USDT', 'H/USDT',
    'TURBO/USDT', 'NIGHT/USDT', 'BLESS/USDT', 'XAN/USDT',

    # Commodities (Gold/Silver)
    'XAUT/USDT', 'XAG/USDT', 'PAXG/USDT',

    # Stocks
    'TSLA/USDT', 'META/USDT', 'AAPL/USDT', 'NVDA/USDT', 'MSFT/USDT',
    'MU/USDT', 'MSTR/USDT', 'GOOGL/USDT', 'HOOD/USDT', 'RDDT/USDT',
    'PLTR/USDT', 'ORCL/USDT', 'AMD/USDT', 'COIN/USDT', 'IBM/USDT',
    'FUTU/USDT', 'INTC/USDT', 'UBER/USDT', 'AVGO/USDT', 'UNH/USDT',
    'ARM/USDT', 'QQQ/USDT', 'JPM/USDT', 'LLY/USDT', 'CRCL/USDT',
    'XOM/USDT', 'AMZN/USDT', 'ASML/USDT', 'FIG/USDT', 'NFLX/USDT',
    'BAC/USDT', 'GS/USDT', 'LRCX/USDT', 'JNJ/USDT', 'PEP/USDT',
    'JD/USDT', 'BABA/USDT', 'MA/USDT', 'NOW/USDT', 'MCD/USDT',
    'ADBE/USDT', 'MRVL/USDT', 'GE/USDT', 'CRM/USDT', 'AMAT/USDT',
    'ACN/USDT', 'WMT/USDT', 'V/USDT', 'CSCO/USDT', 'NKE/USDT',
    'COST/USDT', 'BA/USDT', 'QCOM/USDT'
]

TIMEFRAMES = ['15m', '1h', '4h', '8h', '12h', '1d']
LOOKBACK = 50  # Check highs/lows of last 50 candles

# Initialize Exchange
exchange = ccxt.mexc({
    'enableRateLimit': True,
    'options': {'defaultType': 'spot'}
})

BOT_TOKEN = os.environ.get("BOT_TOKEN") 
CHAT_ID = os.environ.get("CHAT_ID")

last_signals = {}  # Cache to prevent duplicate alerts

# ==========================================
# 1. KEEP ALIVE SERVER
# ==========================================
app = Flask('')

@app.route('/')
def home():
    return "Liquidity Sweep Bot is Running!"

def run_http():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_http)
    t.start()

# ==========================================
# 2. STRATEGY: ADVANCED LIQUIDITY SWEEP
# ==========================================
def analyze_sweep(df, timeframe):
    """
    detects:
    1. Swing High/Low in the lookback period.
    2. Price wicks OUTSIDE the range but CLOSES INSIDE (The Reclaim).
    3. Volume Confirmation (Sweep candle volume > Average Volume).
    """
    try:
        # We need at least lookback + 5 candles
        if len(df) < LOOKBACK + 5:
            return None

        # --- DATA PREP ---
        # The 'current' completed candle (index -2 because -1 is the open/unfinished candle)
        curr = df.iloc[-2]
        
        # The range we are checking against (Lookback period BEFORE the current candle)
        # e.g., if we are at candle 100, we check Highs/Lows from 50 to 99.
        range_data = df.iloc[-LOOKBACK-2 : -2]
        
        swing_high = range_data['high'].max()
        swing_low = range_data['low'].min()
        
        # Indicators
        atr = curr['atr']
        avg_vol = curr['vol_avg']
        
        # --- LOGIC ---

        # 1. BEARISH SWEEP (Short Signal)
        # Condition: High went ABOVE Swing High, but Close stayed BELOW Swing High
        if curr['high'] > swing_high and curr['close'] < swing_high:
            
            # Confluence: Volume Spike (Current Vol > Avg Vol)
            if curr['vol'] > avg_vol:
                
                # Concrete Prices
                # SL: Just above the wick + small buffer (0.5 ATR)
                stop_loss = curr['high'] + (atr * 0.5)
                
                # TP: The opposing liquidity (The Swing Low of the range)
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

        # 2. BULLISH SWEEP (Long Signal)
        # Condition: Low went BELOW Swing Low, but Close stayed ABOVE Swing Low
        if curr['low'] < swing_low and curr['close'] > swing_low:
            
            # Confluence: Volume Spike
            if curr['vol'] > avg_vol:
                
                # Concrete Prices
                # SL: Just below the wick - small buffer (0.5 ATR)
                stop_loss = curr['low'] - (atr * 0.5)
                
                # TP: The opposing liquidity (The Swing High of the range)
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
                
    except Exception as e:
        print(f"Error analyzing: {e}")
        return None

    return None

# ==========================================
# 3. MARKET SCANNER
# ==========================================
async def scan_market():
    print(f"🔥 Scanner Started. Monitoring {len(SYMBOLS)} symbols on {TIMEFRAMES}...")
    bot = Bot(token=BOT_TOKEN)

    while True:
        for symbol in SYMBOLS:
            for tf in TIMEFRAMES:
                try:
                    # Fetch Data
                    # We need enough data for Lookback (50) + ATR (14) + VolSMA (20)
                    limit = LOOKBACK + 30 
                    ohlcv = await exchange.fetch_ohlcv(symbol, timeframe=tf, limit=limit)
                    
                    if not ohlcv:
                        continue

                    df = pd.DataFrame(ohlcv, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
                    
                    # Calculate Indicators
                    df['atr'] = df.ta.atr(length=14)
                    df['vol_avg'] = df.ta.sma(close=df['vol'], length=20) # 20 period volume average
                    
                    # Analyze
                    signal = analyze_sweep(df, tf)

                    if signal:
                        # Unique ID for alert: Symbol + Timeframe + CandleTimestamp
                        sig_id = f"{symbol}_{tf}_{signal['time']}"

                        if last_signals.get(sig_id) is None:
                            
                            # Calculate R:R for display
                            entry = signal['price']
                            risk = abs(entry - signal['sl'])
                            reward = abs(signal['tp'] - entry)
                            rr_ratio = round(reward / risk, 2) if risk > 0 else 0

                            emoji = "🟢" if signal['signal'] == "BUY" else "🔴"
                            
                            msg = (
                                f"{emoji} **LIQUIDITY SWEEP CONFIRMED**\n\n"
                                f"🪙 **{symbol}**\n"
                                f"⏰ TF: {tf}\n"
                                f"📉 Side: **{signal['signal']}**\n"
                                f"📊 Logic: {signal['reason']}\n"
                                f"-------------------------\n"
                                f"🚪 Entry: {entry:.4f}\n"
                                f"🛑 Stop Loss: {signal['sl']:.4f}\n"
                                f"🎯 Take Profit: {signal['tp']:.4f}\n"
                                f"⚖️ R:R Ratio: {rr_ratio}R\n"
                                f"-------------------------\n"
                                f"💡 *Reclaim of {signal['swing_level']:.4f} confirmed with volume.*"
                            )

                            await bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode='Markdown')
                            print(f"Sent Alert: {symbol} [{tf}]")
                            
                            # Save to memory
                            last_signals[sig_id] = True

                except Exception as e:
                    print(f"Error on {symbol} {tf}: {e}")
                    await asyncio.sleep(0.5)

            # Small delay between symbols to avoid hitting rate limits too hard
            await asyncio.sleep(1)

        print("Cycle complete. Waiting 60s...")
        await asyncio.sleep(60)

# ==========================================
# 4. MAIN EXECUTION
# ==========================================
if __name__ == '__main__':
    keep_alive()
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(scan_market())
    except KeyboardInterrupt:
        print("Bot stopped.")
    finally:
        loop.run_until_complete(exchange.close())
