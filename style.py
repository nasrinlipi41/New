# --- Your existing bot code starts here ---
# (Keep all your current code for the Telegram bot)
# For example:
from flask import Flask
import threading
import os
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters
import logging

# Enable logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    level=logging.INFO)
logger = logging.getLogger(__name__)

# Define command handlers (use your existing ones)
def start(update, context):
    update.message.reply_text('Hi! I am your style bot.')

def help_command(update, context):
    update.message.reply_text('Help!')

def echo(update, context):
    update.message.reply_text(update.message.text)

def main():
    # 1. Initialize your bot with the token
    updater = Updater("YOUR_BOT_TOKEN_HERE", use_context=True)
    dp = updater.dispatcher

    # 2. Add your handlers
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("help", help_command))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, echo))

    # 3. Start the bot in polling mode IN THE BACKGROUND
    updater.start_polling()
    # --- Your existing bot code ends here ---

# ============================================
# NEW CODE: Minimal Flask server for Render
# ============================================
app = Flask(__name__)

@app.route('/')
def home():
    return "✅ Telegram Bot is running!"

@app.route('/health')
def health_check():
    return "OK", 200

if __name__ == "__main__":
    # Start your Telegram bot in a separate thread
    print("🤖 Starting Telegram Bot in background thread...")
    bot_thread = threading.Thread(target=main)
    bot_thread.daemon = True  # Thread will close if main program exits
    bot_thread.start()
    
    # Start the Flask web server
    print("🌐 Starting Flask web server for Render...")
    port = int(os.environ.get("PORT", 10000))  # Render sets the PORT env variable
    app.run(host='0.0.0.0', port=port)