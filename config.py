import os

# Bot Api Token from the BotFather
API_TOKEN = os.environ.get('API_TOKEN')

# Admin IDs for the bot. Requests always sent to 1st ID
ADMIN_IDS = os.environ.get('ADMINS').split(',')

# Telegram Storage Channel
CHANNEL_ID = os.environ.get('CHANNEL')

# Sticker
STICKER_ID = os.environ.get('STICKER')

# Compulsory membership of users
REQUIRED_CHANNELS = os.environ.get('SUBSCRIPTION').split(',')

# Render Web Url
WEBHOOK_HOST = os.environ.get('HOST_URL')
WEBHOOK_PATH = f'/webhook/{API_TOKEN}'
WEBHOOK_URL = f"{WEBHOOK_HOST}{WEBHOOK_PATH}"

# Database file
DB_FILE_PATH = 'file_management.db'