import os
from typing import Dict
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)
from telegram.request import HTTPXRequest
from portal import PortalClient, PortalConfig

ASK_USERNAME, ASK_PASSWORD = range(2)
user_sessions: Dict[int, Dict[str, str]] = {}

load_dotenv()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

portal_config = PortalConfig(
    base_url="https://portal.ju.edu.et",
    login_path="/login",           # confirmed by form action
    points_path="/student/academic/grade", # provided
    username_field="username",     # TODO: verify
    password_field="password",     # TODO: verify
    csrf_field="_token",
)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Welcome to the JU Points Bot.\n\nUse /login to securely check your points.\nYour credentials are not stored; they are used only for this session."
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Available commands:\n"
        "- /start — Welcome message\n"
        "- /login — Check your grades and GPA\n"
        "- /cancel — Cancel current action"
    )

async def login_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Please enter your username:")
    return ASK_USERNAME

async def ask_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_sessions[chat_id] = {"username": update.message.text}
    await update.message.reply_text("Please enter your password:")
    return ASK_PASSWORD

async def ask_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id not in user_sessions or "username" not in user_sessions[chat_id]:
        await update.message.reply_text("Session expired. Please /login again.")
        return ConversationHandler.END

    username = user_sessions[chat_id]["username"]
    password = update.message.text

    await update.message.reply_text("Logging in to JU portal...")

    client = PortalClient(config=portal_config)
    try:
        ok = client.login(username, password)
    except Exception as e:
        await update.message.reply_text(f"Login failed due to a connection or portal error.\nDetails: {e}")
        user_sessions.pop(chat_id, None)
        return ConversationHandler.END

    if not ok:
        await update.message.reply_text("Invalid credentials or portal rejected the login. Please try /login again.")
        user_sessions.pop(chat_id, None)
        return ConversationHandler.END

    await update.message.reply_text("Login successful. Fetching your points...")
    try:
        points = client.fetch_points()
    except Exception as e:
        await update.message.reply_text(
            "Could not retrieve your points. The portal HTML likely changed or requires updated selectors.\n"
            f"Details: {e}"
        )
        user_sessions.pop(chat_id, None)
        return ConversationHandler.END

    lines = [f"{k}: {v}" for k, v in points.items()]
    text = "Your Points:\n" + "\n".join(lines) if lines else "No points found."
    await update.message.reply_text(text)

    user_sessions.pop(chat_id, None)
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_sessions.pop(update.effective_chat.id, None)
    await update.message.reply_text("Cancelled. Use /login to try again.")
    return ConversationHandler.END

async def unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "I didn't understand that command. Use /help to see available commands."
    )

async def fallback_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Please use /login to check your grades or /help to see commands."
    )

def main():
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set in environment or .env")

    # --- FIX: remove system proxy variables to prevent 'proxy_port' error ---
    for proxy_var in ["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"]:
        if proxy_var in os.environ:
            del os.environ[proxy_var]
    # ----------------------------------------------------------------------

    # Increase HTTP timeouts to reduce TimedOut/ConnectTimeout on slow or filtered networks
    request = HTTPXRequest(
        connect_timeout=30.0,
        read_timeout=30.0,
        write_timeout=30.0,
        pool_timeout=30.0,
    )

    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).request(request).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("login", login_cmd)],
        states={
            ASK_USERNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_username)],
            ASK_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_password)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(conv)
    # Generic handlers after conversation so they don't interfere with flows
    app.add_handler(MessageHandler(filters.COMMAND, unknown_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, fallback_text))

    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
