"""
razorpay_webhook/config.py
──────────────────────────
Centralised configuration loaded from environment variables.
All settings are documented; mandatory ones raise at import time if missing.
"""

import os
import sys

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ── Razorpay ──────────────────────────────────────────────────────────────────
RAZORPAY_KEY_ID     : str = os.environ.get("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET : str = os.environ.get("RAZORPAY_KEY_SECRET", "")
RAZORPAY_WEBHOOK_SECRET: str = os.environ.get("RAZORPAY_WEBHOOK_SECRET", "")

# ── Telegram ──────────────────────────────────────────────────────────────────
# Bot token — used to send messages via Telegram Bot API (HTTP, no aiogram)
BOT_TOKEN: str = os.environ.get("BOT_TOKEN", "")

# Admin chat IDs (comma-separated).  Used for approval notifications.
ADMIN_IDS: list[str] = [
    a.strip() for a in os.environ.get("ADMINS", "").split(",") if a.strip()
]

# Group / supergroup where paid-folder approval requests are posted.
# Falls back to the first admin DM if not set.
ADMIN_GROUP_ID: str = os.environ.get("ADMIN_GROUP_ID", "")

ADMIN_CONTACT: str = os.environ.get("ADMIN_CONTACT", "@admin")

# ── Database ──────────────────────────────────────────────────────────────────
DB_STRING: str = os.environ.get("DB_STRING", "")

# ── Server ────────────────────────────────────────────────────────────────────
# Port this service listens on (default 8000 — change if the bot uses 8443)
PORT: int = int(os.environ.get("WEBHOOK_PORT", "8000"))

# Set to "production" to suppress debug output
ENV: str = os.environ.get("APP_ENV", "development")

# ── Startup validation ────────────────────────────────────────────────────────
_REQUIRED = {
    "RAZORPAY_KEY_ID":      RAZORPAY_KEY_ID,
    "RAZORPAY_KEY_SECRET":  RAZORPAY_KEY_SECRET,
    "BOT_TOKEN":            BOT_TOKEN,
    "DB_STRING":            DB_STRING,
    "ADMINS":               os.environ.get("ADMINS", ""),
}

_missing = [k for k, v in _REQUIRED.items() if not v]
if _missing:
    print(
        f"\n[config] ❌ Missing required environment variables: {', '.join(_missing)}\n"
        "Copy .env.example → .env and fill in the values.\n",
        file=sys.stderr,
    )
    sys.exit(1)

if not RAZORPAY_WEBHOOK_SECRET:
    print(
        "[config] ⚠️  RAZORPAY_WEBHOOK_SECRET is not set — "
        "webhook signature verification is DISABLED. "
        "Set it in production to prevent spoofed requests.\n",
        file=sys.stderr,
    )
