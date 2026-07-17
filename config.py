import os

# Load .env file when running locally (no-op in production)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ── Telegram ──────────────────────────────────────────────────────────────────
API_TOKEN = os.environ.get('API_TOKEN')
# Unique key for THIS bot process. Multi-bot deployments share one Supabase DB
# and each runs as its own process with a distinct BOT_ID; downloads are served
# from the storage channels this bot is paired with. Defaults to 'main' so a
# single-bot setup needs no extra config.
BOT_ID = os.environ.get('BOT_ID', 'main')
ADMIN_IDS = [a.strip() for a in os.environ.get('ADMINS', '').split(',') if a.strip()]
CHANNEL_ID  = os.environ.get('CHANNEL')
# Telegram group/supergroup ID where new-user approval requests are posted.
# If not set, requests are DM'd to the first admin instead.
ADMIN_GROUP_ID = os.environ.get('ADMIN_GROUP_ID')
STICKER_ID = os.environ.get('STICKER')
REQUIRED_CHANNELS = [
    c.strip() for c in os.environ.get('SUBSCRIPTION', '').split(',') if c.strip()
]

# ── Webhook / Hosting ─────────────────────────────────────────────────────
WEBHOOK_HOST = os.environ.get('HOST_URL', '').rstrip('/')
WEBHOOK_PATH = f'/webhook/{API_TOKEN}'
WEBHOOK_URL  = f"{WEBHOOK_HOST}{WEBHOOK_PATH}"

# ── Database ───────────────────────────────────────────────────────────────────
POSTGRES_CONNECTION_STRING = os.environ.get('DB_STRING')

# Admin contact handle shown in rejection / support messages
ADMIN_CONTACT = os.environ.get('ADMIN_CONTACT', '@Art3mis_adminbot')
DEFAULT_CAPTION = os.environ.get('DEFAULT_CAPTION', '@Medical_Contentbot\nEver-growing archive of medical content')

# Human-readable bot name shown to users in messages (e.g. welcome, help, about).
# Each bot instance in a multi-bot setup should set its own name.
BOT_NAME = os.environ.get('BOT_NAME', 'Medical Content Bot')

# ── Payment mode ─────────────────────────────────────────────────────────────
# Controls which payment system is active.
#   manual   — no automated payments; paid folders require admin approval
#   razorpay — Razorpay payment gateway (requires keys below)
#   stars    — Telegram Stars (no external gateway, no KYC)
PAYMENT_MODE = os.environ.get('PAYMENT_MODE', 'manual').lower()

# ── Razorpay (only used when PAYMENT_MODE=razorpay) ──────────────────────────
# Get keys from: https://dashboard.razorpay.com/app/keys
RAZORPAY_KEY_ID     = os.environ.get('RAZORPAY_KEY_ID', '')
RAZORPAY_KEY_SECRET = os.environ.get('RAZORPAY_KEY_SECRET', '')
# Webhook secret from: Razorpay Dashboard → Settings → Webhooks
RAZORPAY_WEBHOOK_SECRET = os.environ.get('RAZORPAY_WEBHOOK_SECRET', '')

# ── Optional / Advanced ───────────────────────────────────────────────────────
KEEP_ALIVE_PORT = int(os.environ.get('KEEP_ALIVE_PORT') or '4343')
LOG_LEVEL       = os.environ.get('LOG_LEVEL', 'INFO').upper()

# Hours before re-notifying admin about the same pending user's /start
# Set to 0 to always notify (not recommended for busy bots).
NOTIFY_COOLDOWN_HOURS = int(os.environ.get('NOTIFY_COOLDOWN_HOURS', '4'))

# ── Access Control ────────────────────────────────────────────────────────────
# When True (default), new users must be approved by an admin before they can
# access the bot. When False, all users are auto-approved on first /start and
# the pending/rejected flow is skipped entirely.
REQUIRE_APPROVAL = os.environ.get('REQUIRE_APPROVAL', 'true').lower() in ('true', '1', 'yes')

# ── Sticker on Start ─────────────────────────────────────────────────────────
# When True (default), the bot sends the STICKER env sticker on /start before
# showing the main menu (with a brief delay). When False, the sticker is
# skipped and the main menu appears instantly.
SEND_STICKER = os.environ.get('SEND_STICKER', 'true').lower() in ('true', '1', 'yes')

# ── Startup validation ─────────────────────────────────────────────────────
# Fail fast with a clear message instead of crashing later inside aiogram /
# psycopg2 when a required variable is missing or misconfigured.

_VALID_PAYMENT_MODES = ('manual', 'razorpay', 'stars')


def _validate_config() -> None:
    """Validate required environment variables. Raises RuntimeError on failure."""
    errors = []

    # Always-required settings
    if not API_TOKEN:
        errors.append("API_TOKEN is required (your Telegram bot token from @BotFather).")
    if not ADMIN_IDS:
        errors.append("ADMINS is required (comma-separated admin user IDs).")
    if not POSTGRES_CONNECTION_STRING:
        errors.append("DB_STRING is required (PostgreSQL connection string).")

    # Payment mode must be one of the supported values
    if PAYMENT_MODE not in _VALID_PAYMENT_MODES:
        errors.append(
            f"PAYMENT_MODE='{PAYMENT_MODE}' is invalid. "
            f"Use one of: {', '.join(_VALID_PAYMENT_MODES)}."
        )

    # Razorpay mode needs API keys to actually create payment links
    if PAYMENT_MODE == 'razorpay':
        if not RAZORPAY_KEY_ID or not RAZORPAY_KEY_SECRET:
            errors.append(
                "PAYMENT_MODE=razorpay requires RAZORPAY_KEY_ID and "
                "RAZORPAY_KEY_SECRET to be set."
            )

    if errors:
        bullet_list = "\n".join(f"  - {e}" for e in errors)
        raise RuntimeError(
            "Invalid configuration. Fix the following environment variables "
            f"(see .env.example):\n{bullet_list}"
        )

    # Non-fatal warnings
    if PAYMENT_MODE == 'razorpay' and not RAZORPAY_WEBHOOK_SECRET:
        import logging
        logging.getLogger(__name__).warning(
            "RAZORPAY_WEBHOOK_SECRET is not set — incoming Razorpay webhooks "
            "will be rejected. Set it to enable automatic premium activation."
        )
    if PAYMENT_MODE == 'razorpay' and not WEBHOOK_HOST:
        import logging
        logging.getLogger(__name__).warning(
            "HOST_URL is not set — Razorpay payment links will have no "
            "post-payment redirect. Set HOST_URL to your public server URL "
            "to enable the /payment/success redirect after checkout."
        )


_validate_config()