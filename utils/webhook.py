import logging
#from main import bot
from config import WEBHOOK_URL
from utils.database import conn

async def on_startup(dispatcher):
    from main import bot
    await bot.set_webhook(WEBHOOK_URL)

async def on_shutdown(dispatcher):
    from main import bot
    logging.warning('Shutting down..')
    await bot.delete_webhook()
    conn.close()
    logging.warning('Bye!')