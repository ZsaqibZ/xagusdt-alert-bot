import os
import ccxt.async_support as ccxt
import asyncio
import uuid
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes
from flask import Flask
from threading import Thread

# ==========================================
# 1. CONFIGURATION & STATE
# ==========================================
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

# Stores alerts. Added 'id' for exact button-click deletion.
price_alerts = []
exchange = None

# ==========================================
# 2. KEEP-ALIVE SERVER
# ==========================================
app = Flask('')
@app.route('/')
def home(): return "Interactive Price Alert Bot Active"

def run_http():
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))

def keep_alive():
    Thread(target=run_http, daemon=True).start()

# ==========================================
# 3. EXCHANGE HANDLER
# ==========================================
async def get_exchange():
    global exchange
    if exchange is None:
        exchange = ccxt.mexc({'enableRateLimit': True})
        await exchange.load_markets()
    return exchange

# ==========================================
# 4. MONITOR LOGIC
# ==========================================
async def price_monitor(application):
    global price_alerts
    while True:
        try:
            if not price_alerts:
                await asyncio.sleep(10)
                continue

            exch = await get_exchange()
            symbols_to_check = list(set(a['symbol'] for a in price_alerts))
            
            for symbol in symbols_to_check:
                try:
                    ticker = await exch.fetch_ticker(symbol)
                    current_price = ticker['last']
                    
                    for alert in price_alerts[:]: 
                        triggered = False
                        if alert['symbol'] == symbol:
                            if alert['direction'] == 'above' and current_price >= alert['target']:
                                triggered = True
                            elif alert['direction'] == 'below' and current_price <= alert['target']:
                                triggered = True

                        if triggered:
                            emoji = "🚀" if alert['direction'] == 'above' else "📉"
                            msg = (
                                f"{emoji} **PRICE ALERT TRIGGERED** {emoji}\n\n"
                                f"**Symbol:** `{symbol}`\n"
                                f"**Target:** `{alert['target']}`\n"
                                f"**Current Price:** `{current_price}`\n"
                                f"**Time:** {datetime.utcnow().strftime('%H:%M:%S UTC')}"
                            )
                            await application.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode='Markdown')
                            price_alerts.remove(alert) 
                except Exception as e:
                    print(f"Error fetching {symbol}: {e}")
            
            await asyncio.sleep(30)
        except Exception as e:
            print(f"Monitor Loop Error: {e}")
            await asyncio.sleep(60)

# ==========================================
# 5. TELEGRAM COMMANDS & BUTTONS
# ==========================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Create a nice starting menu with buttons
    keyboard = [
        [InlineKeyboardButton("📋 List Active Alerts", callback_data="show_list")],
        [InlineKeyboardButton("❓ How to Add Alerts", callback_data="show_help")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "⚡️ **Interactive Price Alert Bot**\n\n"
        "Welcome! Use the buttons below to navigate, or type `/add` to create a new alert.",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def add_alert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        symbol = context.args[0].upper()
        target_price = float(context.args[1])
        
        exch = await get_exchange()
        ticker = await exch.fetch_ticker(symbol)
        current_price = ticker['last']
        
        direction = 'above' if target_price > current_price else 'below'
        
        new_alert = {
            'id': str(uuid.uuid4())[:8], # Unique ID for button targeting
            'symbol': symbol,
            'target': target_price,
            'direction': direction,
            'created_at': datetime.utcnow().strftime('%Y-%m-%d %H:%M')
        }
        
        price_alerts.append(new_alert)
        
        # Add a button to instantly view the list after adding
        keyboard = [[InlineKeyboardButton("📋 View All Alerts", callback_data="show_list")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text(
            f"✅ **Alert Set!**\n`{symbol}` at `{target_price}`\n"
            f"*(Triggers when price goes {direction} target)*\n"
            f"Current market price: `{current_price}`",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    except (IndexError, ValueError):
        await update.message.reply_text("❌ Usage: `/add BTC/USDT 65000`", parse_mode='Markdown')
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")

async def list_alerts_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_alert_list(update.message)

async def send_alert_list(message_obj):
    if not price_alerts:
        await message_obj.reply_text("📭 You have no active alerts.")
        return
    
    text = "📋 **Your Active Alerts:**\nClick a button to delete."
    keyboard = []
    
    # Generate a button for every single alert
    for a in price_alerts:
        btn_text = f"❌ {a['symbol']} @ {a['target']} ({a['direction']})"
        keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"del_{a['id']}")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await message_obj.reply_text(text, reply_markup=reply_markup, parse_mode='Markdown')

# --- BUTTON CLICK HANDLER ---
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer() # Acknowledge the button click so it doesn't hang
    
    data = query.data
    global price_alerts

    if data == "show_list":
        await send_alert_list(query.message)
        
    elif data == "show_help":
        await query.message.reply_text(
            "💡 **How to add an alert:**\n\n"
            "Just type the `/add` command followed by the coin pair and the price.\n\n"
            "**Examples:**\n"
            "`/add BTC/USDT 70000`\n"
            "`/add ETH/USDT 3500`\n"
            "`/add GOLD/USDT 2400`\n\n"
            "The bot will automatically figure out if it needs to wait for the price to go UP or DOWN to hit your target.",
            parse_mode='Markdown'
        )
        
    elif data.startswith("del_"):
        alert_id = data.split("_")[1]
        
        # Find and remove the exact alert matching the ID
        original_count = len(price_alerts)
        price_alerts = [a for a in price_alerts if a['id'] != alert_id]
        
        if len(price_alerts) < original_count:
            # Successfully deleted. Update the list message.
            await query.edit_message_text("✅ Alert deleted successfully!")
            await send_alert_list(query.message) # Resend updated list
        else:
            await query.edit_message_text("⚠️ Alert was already deleted or not found.")

# ==========================================
# 6. MAIN
# ==========================================
async def main():
    if not BOT_TOKEN or not CHAT_ID:
        print("Missing BOT_TOKEN or CHAT_ID environment variables")
        return

    keep_alive()
    
    app_bot = ApplicationBuilder().token(BOT_TOKEN).build()
    
    # Commands
    app_bot.add_handler(CommandHandler("start", start))
    app_bot.add_handler(CommandHandler("add", add_alert))
    app_bot.add_handler(CommandHandler("list", list_alerts_command))
    
    # Button handler
    app_bot.add_handler(CallbackQueryHandler(button_handler))
    
    asyncio.create_task(price_monitor(app_bot))
    
    await app_bot.initialize()
    await app_bot.start()
    print("Bot is polling...")
    await app_bot.updater.start_polling(drop_pending_updates=True)
    await asyncio.Event().wait()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
