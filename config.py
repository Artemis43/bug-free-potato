import os

# Bot Api Token from the BotFather
API_TOKEN = os.environ.get('ApiToken')

# Admin IDs for the bot. Requests always sent to 1st ID
ADMIN_IDS = os.environ.get('AdminIds').split(',')

# Telegram Storage Channel
CHANNEL_ID = os.environ.get('MyChannel')

# Sticker
STICKER_ID = os.environ.get('MedSticker')

# Compulsory membership of users
REQUIRED_CHANNELS = os.environ.get('ForcedSubs').split(',')

# Render Web Url
WEBHOOK_HOST = os.environ.get('RenderUrl')
WEBHOOK_PATH = f'/webhook/{API_TOKEN}'
WEBHOOK_URL = f"{WEBHOOK_HOST}{WEBHOOK_PATH}"

# Database file
DB_FILE_PATH = 'file_management.db'