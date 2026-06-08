import logging
from aiogram import types
from aiogram.types import ParseMode
from middlewares.authorization import is_private_chat
from config import ADMIN_IDS
from utils.database import db_fetchall


async def broadcast_message(message: types.Message):
    """Admin command: /broadcast <text> — send a message to all users."""
    from main import bot
    if not is_private_chat(message):
        return

    if str(message.from_user.id) not in ADMIN_IDS:
        await message.reply("You are not authorized to send broadcasts.")
        return

    text = message.get_args()
    if not text:
        await message.reply(
            "Usage: `/broadcast <your message>`",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    try:
        # Only send to approved users to avoid spamming pending/rejected accounts
        users = db_fetchall("SELECT user_id FROM users WHERE status = 'approved'")
    except Exception as e:
        logging.error(f"Error fetching users for broadcast: {e}")
        await message.reply("Error fetching users. Please try again later.")
        return

    success = 0
    failed  = 0

    for (user_id,) in users:
        try:
            await bot.send_message(user_id, text)
            success += 1
        except Exception as e:
            logging.error(f"Broadcast failed for user {user_id}: {e}")
            failed += 1

    await message.reply(
        f"📢 Broadcast complete.\n✅ Sent: {success}\n❌ Failed: {failed}",
        parse_mode=ParseMode.MARKDOWN
    )