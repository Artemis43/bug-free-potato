from utils.bot_ref import get_bot
import logging
from aiogram import types
from aiogram import Router
from aiogram.enums import ParseMode
from aiogram import Router
from middlewares.authorization import is_private_chat
from config import ADMIN_IDS
from utils.database import db_fetchone

router = Router()


async def stop(message: types.Message):
    """Admin-only command: /stop — gracefully shut down the bot process."""
    if not is_private_chat(message):
        return

    if str(message.from_user.id) not in ADMIN_IDS:
        await message.reply("You are not authorized to stop the bot.")
        return

    user_id = message.from_user.id

    # Verify the admin exists in the database before stopping
    result = db_fetchone('SELECT 1 FROM users WHERE user_id = %s', (user_id,))

    if not result:
        await message.reply("You need to /start the bot first.")
        return

    await message.reply("🛑 Bot is stopping…")
    logging.warning(f"Bot stopped by admin {user_id}.")

    bot = get_bot()
    # Delete the webhook so Telegram doesn't try to reach us while we're down
    try:
        await bot.delete_webhook()
    except Exception as e:
        logging.error(f"Error deleting webhook during stop: {e}")

    import sys
    sys.exit(0)