import os

# Load .env file when running locally (no-op in production where real env vars are set).
# Install: pip install python-dotenv  (already in requirements.txt)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv is optional; production hosts inject env vars natively

# ── Telegram ──────────────────────────────────────────────────────────────────

API_TOKEN = os.environ.get('API_TOKEN')

# Comma-separated admin IDs; first ID receives approval notifications
ADMIN_IDS = [a.strip() for a in os.environ.get('ADMINS', '').split(',') if a.strip()]

# Private Telegram channel used as the file-archive storage backend
CHANNEL_ID = os.environ.get('CHANNEL')

# Sticker file_id sent to users on /start
STICKER_ID = os.environ.get('STICKER')

# Channels that users must join before using the bot (ForcedSub)
REQUIRED_CHANNELS = [
    c.strip() for c in os.environ.get('SUBSCRIPTION', '').split(',') if c.strip()
]

# ── Webhook / Hosting ─────────────────────────────────────────────────────────

WEBHOOK_HOST = os.environ.get('HOST_URL', '').rstrip('/')
WEBHOOK_PATH = f'/webhook/{API_TOKEN}'
WEBHOOK_URL  = f"{WEBHOOK_HOST}{WEBHOOK_PATH}"

# ── Database ───────────────────────────────────────────────────────────────────

POSTGRES_CONNECTION_STRING = os.environ.get('DB_STRING')

# ── Optional / Advanced ───────────────────────────────────────────────────────

# Port for the Flask keep-alive server
KEEP_ALIVE_PORT = int(os.environ.get('KEEP_ALIVE_PORT', '4343'))

# Python logging level
LOG_LEVEL = os.environ.get('LOG_LEVEL', 'INFO').upper()

# ── Startup validation ────────────────────────────────────────────────────────
# Fail immediately with a clear message rather than crashing deep inside a handler.

_REQUIRED = {
    'API_TOKEN': API_TOKEN,
    'ADMINS':    os.environ.get('ADMINS'),
    'CHANNEL':   CHANNEL_ID,
    'DB_STRING': POSTGRES_CONNECTION_STRING,
    'HOST_URL':  os.environ.get('HOST_URL'),
}

_missing = [k for k, v in _REQUIRED.items() if not v]
if _missing:
    raise EnvironmentError(
        f"\n\n[config] Missing required environment variables: {', '.join(_missing)}\n"
        f"Copy .env.example → .env and fill in the values.\n"
    )