# JU Points Telegram Bot

A Telegram bot that logs into the JU student portal and fetches a student's points upon entering username and password.

## Important
- Obtain university approval and ensure Terms of Service permit automated access.
- If the portal uses CAPTCHA/SSO, you may need an official API or a headless browser approach.

## Setup
1. Create and activate a virtualenv (Windows):
```
py -m venv .venv
.venv\Scripts\activate
```
2. Install dependencies:
```
pip install -r requirements.txt
```
3. Copy `.env.example` to `.env` and set `TELEGRAM_BOT_TOKEN`.
4. Run the bot:
```
py bot.py
```

## Configure the Portal
Edit `portal.py` `PortalConfig` fields:
- `login_path`, `points_path` – exact paths to login and points page.
- `username_field`, `password_field` – input `name` attributes.
- `csrf_field` – set if the portal requires CSRF (e.g., `__RequestVerificationToken`).

Update `fetch_points()` selectors to match the portal HTML.

## Security
- Credentials are not stored; they exist only in memory during a chat session.
- Keep your bot token in `.env` and never commit it.
