import os
import ccxt.async_support as ccxt
import pandas as pd
import asyncio
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

# Timeframes to scan (15m removed)
TIMEFRAME_SETTINGS = {
    '1h':  {'lookback': 48,  'sl_mult': 1.5, 'tp_mult': 2.5, 'ema_period': 200},
    '4h':  {'lookback': 30,  'sl_mult': 1.5, 'tp_mult': 2.5, 'ema_period': 200},
    '1d':  {'lookback': 20,  'sl_mult': 1.5, 'tp_mult': 2.5, 'ema_period': 200}
}

last_signals = {}                     # avoid duplicate signals
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

# Global exchange instance
exchange = ccxt.mexc({
    'enableRateLimit': True,
    'options': {'defaultType': 'spot'}
})

# ==========================================
# 2. RENDER KEEP-ALIVE
# ==========================================
app = Flask('')
@app.route('/')
def home(): return "Dynamic Liquidity Bot Active (Improved)"

def run_http():
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))

def keep_alive():
    Thread(target=run_http, daemon=True).start()

# ==========================================
# 3. EXCHANGE CONNECTION (with auto-reconnect)
# ==========================================
async def get_exchange():
    global exchange
    if exchange is None:
        exchange = ccxt.mexc({
            'enableRateLimit': True,
            'options': {'defaultType': 'spot'}
        })
        await exchange.load_markets()
        print("Exchange initialized")
    return exchange

async def reconnect_exchange():
    global exchange
    if exchange:
        await exchange.close()
    exchange = ccxt.mexc({
        'enableRateLimit': True,
        'options': {'defaultType': 'spot'}
    })
    await exchange.load_markets()
    print("Exchange reconnected")

# ==========================================
# 4. IMPROVED STRATEGY LOGIC
# ==========================================
def calculate_ema(series, period):
    return series.ewm(span=period, adjust=False).mean()

def analyze_dynamic_sweep(df, tf):
    """
    Enhanced logic:
    - Detects liquidity sweep + reclaim
    - Requires trend filter (price > 200 EMA for long, < for short)
    - Volume confirmation (reclaim candle volume >= 1.2 * avg volume last 20)
    - Adaptive ATR stop-loss
    """
    try:
        settings = TIMEFRAME_SETTINGS[tf]
        lookback = settings['lookback']
        ema_period = settings['ema_period']
        sl_mult = settings['sl_mult']
        tp_mult = settings['tp_mult']

        if len(df) < lookback + 10:
            return None

        # Last two closed candles
        curr = df.iloc[-2]   # most recent closed candle
        prev = df.iloc[-3]   # the "sweep" candle

        # Calculate range (liquidity zone)
        window = df.iloc[-(lookback + 3):-3]
        range_low = window['low'].min()
        range_high = window['high'].max()

        # ATR (14-period)
        df['tr0'] = abs(df['high'] - df['low'])
        df['tr1'] = abs(df['high'] - df['close'].shift(1))
        df['tr2'] = abs(df['low'] - df['close'].shift(1))
        df['tr'] = df[['tr0', 'tr1', 'tr2']].max(axis=1)
        atr = df['tr'].rolling(window=14).mean().iloc[-2]

        if atr == 0 or pd.isna(atr):
            return None

        # ---- Volume confirmation ----
        avg_volume = df['volume'].tail(20).mean()
        volume_ok = curr['volume'] >= avg_volume * 1.2

        # ---- Trend filter (EMA) ----
        df['ema'] = calculate_ema(df['close'], ema_period)
        current_ema = df['ema'].iloc[-2]
        if pd.isna(current_ema):
            return None

        # ---- Reclaim conditions ----
        # Bullish: prev closed below support, curr closed green above support, trend bullish (price > EMA)
        bullish_reclaim = (prev['close'] < range_low) and (curr['close'] > range_low) and \
                          (curr['close'] > curr['open']) and (curr['close'] > current_ema) and volume_ok

        # Bearish: prev closed above resistance, curr closed red below resistance, trend bearish (price < EMA)
        bearish_reclaim = (prev['close'] > range_high) and (curr['close'] < range_high) and \
                          (curr['close'] < curr['open']) and (curr['close'] < current_ema) and volume_ok

        if bullish_reclaim:
            entry = curr['close']
            sl = entry - (atr * sl_mult)
            tp = entry + (atr * tp_mult)
            # Avoid tiny moves: require at least 0.3% potential profit
            if (tp - entry) / entry < 0.003:
                return None
            return ("LONG", entry, sl, tp, range_low, curr.name)
            
        if bearish_reclaim:
            entry = curr['close']
            sl = entry + (atr * sl_mult)
            tp = entry - (atr * tp_mult)
            if (entry - tp) / entry < 0.003:
                return None
            return ("SHORT", entry, sl, tp, range_high, curr.name)

    except Exception as e:
        # silently skip individual errors
        pass
    return None

# ==========================================
# 5. SCANNER LOOP (with improved logging)
# ==========================================
async def swing_scanner(application):
    global last_scan_time
    # send startup message
    await application.bot.send_message(
        chat_id=CHAT_ID,
        text="🚀 **Improved Dynamic Liquidity Bot Started!**\n"
             "Scanning 1h, 4h, 1d with trend & volume filters.\n"
             "Use /help for available commands."
    )

    while True:
        try:
            exch = await get_exchange()
            now_utc = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')
            print(f"[{now_utc}] Starting scan cycle...")

            for tf in TIMEFRAME_SETTINGS.keys():
                print(f"  Scanning {tf}...")
                for symbol in SYMBOLS_RAW:
                    try:
                        limit = TIMEFRAME_SETTINGS[tf]['lookback'] + 30
                        bars = await exch.fetch_ohlcv(symbol, timeframe=tf, limit=limit)
                        if len(bars) < limit:
                            continue
                        df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
                        df.set_index('timestamp', inplace=True)

                        signal = analyze_dynamic_sweep(df, tf)
                        if signal:
                            side, entry, sl, tp, level, sig_time = signal
                            sig_id = f"{symbol}_{side}_{tf}_{sig_time}"
                            if sig_id not in last_signals:
                                # update stats
                                signal_stats[side] += 1
                                signal_stats['by_tf'][tf] += 1
                                last_signals[sig_id] = True

                                # format message with UTC time
                                sig_dt = sig_time.strftime('%Y-%m-%d %H:%M:%S') if hasattr(sig_time, 'strftime') else str(sig_time)
                                emoji = "🟢" if side == "LONG" else "🔴"
                                msg = (
                                    f"{emoji} **LIQUIDITY RECLAIM ({tf})** {emoji}\n"
                                    f"**Symbol:** `{symbol}`\n"
                                    f"**Side:** {side}\n"
                                    f"**Time (UTC):** {sig_dt}\n"
                                    f"**Swept Level:** `${level:.6f}`\n\n"
                                    f"**Entry:** `${entry:.6f}`\n"
                                    f"**Stop Loss:** `${sl:.6f}`\n"
                                    f"**Take Profit:** `${tp:.6f}`\n\n"
                                    f"📊 *Lookback: {TIMEFRAME_SETTINGS[tf]['lookback']} | ATR multiple SL:{TIMEFRAME_SETTINGS[tf]['sl_mult']} TP:{TIMEFRAME_SETTINGS[tf]['tp_mult']}*"
                                )
                                await application.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode='Markdown')
                                print(f"Signal sent: {symbol} {side} on {tf}")
                        await asyncio.sleep(0.1)  # gentle rate limit
                    except Exception as e:
                        # per-symbol error: skip silently to keep scanning
                        continue

            last_scan_time = datetime.utcnow()
            print(f"Cycle finished. Sleeping 10 minutes...")
            await asyncio.sleep(600)

        except Exception as e:
            print(f"Global loop error: {e}")
            await reconnect_exchange()
            await asyncio.sleep(60)

# ==========================================
# 6. TELEGRAM COMMANDS
# ==========================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 *Dynamic Liquidity Bot*\n"
        "I scan 1h, 4h, 1d for liquidity sweeps + reclaims.\n"
        "Use /help to see available commands.",
        parse_mode='Markdown'
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "📖 *Available commands:*\n"
        "/status – Show bot status & last scan time\n"
        "/stats – Signal statistics (LONG/SHORT per timeframe)\n"
        "/symbols – List all tracked symbols (count)\n"
        "/start – Welcome message\n"
        "/help – This help"
    )
    await update.message.reply_text(help_text, parse_mode='Markdown')

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global last_scan_time
    if last_scan_time:
        last_scan = last_scan_time.strftime('%Y-%m-%d %H:%M:%S UTC')
    else:
        last_scan = "Not yet scanned"
    status_msg = (
        f"✅ *Bot is running*\n"
        f"📊 Scanning timeframes: 1h, 4h, 1d\n"
        f"🕒 Last full scan: {last_scan}\n"
        f"🔍 Symbols tracked: {len(SYMBOLS_RAW)}\n"
        f"📈 Unique signals sent: {len(last_signals)}"
    )
    await update.message.reply_text(status_msg, parse_mode='Markdown')

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stats_msg = (
        f"📊 *Signal Statistics*\n"
        f"LONG signals: {signal_stats['LONG']}\n"
        f"SHORT signals: {signal_stats['SHORT']}\n"
        f"\n*Per timeframe:*\n"
    )
    for tf in TIMEFRAME_SETTINGS:
        stats_msg += f"{tf}: {signal_stats['by_tf'][tf]}\n"
    await update.message.reply_text(stats_msg, parse_mode='Markdown')

async def symbols(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"🔍 Tracking {len(SYMBOLS_RAW)} symbols:\n" + ", ".join(SYMBOLS_RAW[:20]) + ("..." if len(SYMBOLS_RAW) > 20 else ""))

# ==========================================
# 7. MAIN EXECUTION
# ==========================================
async def main():
    if not BOT_TOKEN or not CHAT_ID:
        print("Missing BOT_TOKEN or CHAT_ID environment variables.")
        return

    keep_alive()  # for Render

    application = ApplicationBuilder().token(BOT_TOKEN).build()

    # Register commands
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("stats", stats))
    application.add_handler(CommandHandler("symbols", symbols))

    # Start background scanner
    asyncio.create_task(swing_scanner(application))

    # Start bot
    await application.initialize()
    await application.start()
    await application.updater.start_polling(drop_pending_updates=True)
    await asyncio.Event().wait()  # run forever

if __name__ == '__main__':
    asyncio.run(main())