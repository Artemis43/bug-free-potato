import os

# Load .env file when running locally (no-op in production)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ── Telegram ──────────────────────────────────────────────────────────────────
API_TOKEN = os.environ.get('API_TOKEN')
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

# ── Content URLs (change via env vars without touching code) ───────────────────────
# Verification post shown to new users on /start
VERIFY_URL = os.environ.get('VERIFY_URL', 'https://t.me/medcontentbotinformation/4')
# Premium upgrade info page
PREMIUM_INFO_URL = os.environ.get('PREMIUM_INFO_URL', 'https://t.me/medcontentbotinformation/2')
# Admin contact handle shown in rejection / support messages
ADMIN_CONTACT = os.environ.get('ADMIN_CONTACT', '@Art3mis_adminbot')
DEFAULT_CAPTION = os.environ.get('DEFAULT_CAPTION', '@Medical_Contentbot\nEver-growing archive of medical content')

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
KEEP_ALIVE_PORT = int(os.environ.get('KEEP_ALIVE_PORT', '4343'))
LOG_LEVEL       = os.environ.get('LOG_LEVEL', 'INFO').upper()

# Hours before re-notifying admin about the same pending user's /start
# Set to 0 to always notify (not recommended for busy bots).
NOTIFY_COOLDOWN_HOURS = int(os.environ.get('NOTIFY_COOLDOWN_HOURS', '4'))

# ── Startup validation ─────────────�