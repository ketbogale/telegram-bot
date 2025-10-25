import os
from typing import Dict
from dotenv import load_dotenv
from telegram import Update
from telegram.constants import ChatAction
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
CA_BUNDLE_PATH = os.getenv("CA_BUNDLE_PATH")

# Externalized portal configuration (no hardcoded defaults)
PORTAL_BASE_URL = (os.getenv("PORTAL_BASE_URL") or "").rstrip("/")
PORTAL_LOGIN_PATH = os.getenv("PORTAL_LOGIN_PATH") or ""
PORTAL_POINTS_PATH = os.getenv("PORTAL_POINTS_PATH") or ""
PORTAL_USERNAME_FIELD = os.getenv("PORTAL_USERNAME_FIELD") or ""
PORTAL_PASSWORD_FIELD = os.getenv("PORTAL_PASSWORD_FIELD") or ""
PORTAL_CSRF_FIELD = os.getenv("PORTAL_CSRF_FIELD") or ""
VERIFY_SSL_ENV = os.getenv("VERIFY_SSL", "false").strip().lower()
VERIFY_SSL = VERIFY_SSL_ENV in ("1", "true", "yes", "on")

if not (PORTAL_BASE_URL and PORTAL_LOGIN_PATH and PORTAL_POINTS_PATH and PORTAL_USERNAME_FIELD and PORTAL_PASSWORD_FIELD and PORTAL_CSRF_FIELD):
    raise RuntimeError(
        "Missing portal configuration. Set PORTAL_BASE_URL, PORTAL_LOGIN_PATH, PORTAL_POINTS_PATH, "
        "PORTAL_USERNAME_FIELD, PORTAL_PASSWORD_FIELD, PORTAL_CSRF_FIELD in .env"
    )

portal_config = PortalConfig(
    base_url=PORTAL_BASE_URL,
    login_path=PORTAL_LOGIN_PATH,
    points_path=PORTAL_POINTS_PATH,
    username_field=PORTAL_USERNAME_FIELD,
    password_field=PORTAL_PASSWORD_FIELD,
    csrf_field=PORTAL_CSRF_FIELD,
    verify_ssl=VERIFY_SSL,
    ca_bundle_path=CA_BUNDLE_PATH,
)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Welcome to the JU Points Bot."
    )
    await help_cmd(update, context)

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Available commands:\n"
        "- /start — Welcome message\n"
        "- /login — Check your grades and GPA\n"
        "- /cancel — Cancel current action"
    )

async def login_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Clear any previous session and start fresh
    user_sessions.pop(update.effective_chat.id, None)
    await update.message.reply_text("Please enter your username:")
    return ASK_USERNAME

async def ask_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    username = (update.message.text or "").strip()
    if not username:
        await update.message.reply_text("Username cannot be empty. Please enter your username:")
        return ASK_USERNAME
    user_sessions[chat_id] = {"username": username}
    await update.message.reply_text("Please enter your password:")
    return ASK_PASSWORD

async def ask_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id not in user_sessions or "username" not in user_sessions[chat_id]:
        await update.message.reply_text("Session expired. Please /login again.")
        return ConversationHandler.END

    username = user_sessions[chat_id]["username"]
    password = (update.message.text or "").strip()
    if not password:
        await update.message.reply_text("Password cannot be empty. Please enter your password:")
        return ASK_PASSWORD

    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    await update.message.reply_text("Logging in to the portal...")

    client = PortalClient(config=portal_config)
    try:
        ok = client.login(username, password)
    except Exception as e:
        await update.message.reply_text(f"Login failed due to a connection or portal error.\nDetails: {e}")
        user_sessions.pop(chat_id, None)
        return ConversationHandler.END

    if not ok:
        await update.message.reply_text("Invalid credentials or portal rejected the login. Use /login to try again.")
        user_sessions.pop(chat_id, None)
        return ConversationHandler.END

    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
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

async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    try:
        if hasattr(context, "error") and update and isinstance(update, Update) and update.effective_message:
            await update.effective_message.reply_text("An error occurred. Please try again later.")
    except Exception:
        pass

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
    app.add_error_handler(on_error)

    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()
