from config import REQUIRED_CHANNELS
from aiogram import types
from aiogram.types import ParseMode
from middlewares.authorization import is_private_chat, is_user_member
from utils.database import db_fetchone


async def help(message: types.Message):
    if not is_private_chat(message):
        return

    user_id = message.from_user.id
    user = db_fetchone('SELECT status FROM users WHERE user_id = %s', (user_id,))

    if not user or user[0] != 'approved':
        await message.reply("You are not authorized to use the bot. Please wait for admin approval.")
        return

    if not await is_user_member(user_id):
        join_message = "Welcome to The Medical Content Bot ✨\n\nJoin our backup channels to remain connected ✊\n"
        for channel in REQUIRED_CHANNELS:
            join_message += f"{channel}\n"
        await message.reply(join_message)
        return

    help_text = (
        "*The Medical Content Bot* ✨\n\n"
        "📥 `/download <folder\\_name>` — Download all files from a folder\n\n"
        "💫 *How to Use:*\n\n"
        "1️⃣ Use `/start` to see the folder list\n"
        "2️⃣ Copy a folder name from the list\n"
        "3️⃣ Send `/download <folder_name>`\n"
        "4️⃣ Files will be sent to you automatically!\n\n"
        "⏳ *Cooldown:*\n"
        "  Free users — 7 mins between downloads\n"
        "  Premium users — 2 mins between downloads\n\n"
        "ℹ️ *Note:* We do not host any content. "
        "Files are deleted after a short time — forward them to Saved Messages!\n\n"
        "🌟 [Upgrade to Premium](https://t.me/medcontentbotinformation/2)"
    )
    await message.reply(help_text, parse_mode=ParseMode.MARKDOWN)


async def about(message: types.Message):
    if not is_private_chat(message):
        return

    user_id = message.from_user.id
    user = db_fetchone('SELECT status FROM users WHERE user_id = %s', (user_id,))

    if not user or user[0] != 'approved':
        await message.reply("You are not authorized to use the bot. Please wait for admin approval.")
        return

    if not await is_user_member(user_id):
        join_message = "Welcome to The Medical Content Bot ✨\n\nJoin our backup channels to remain connected ✊\n"
        for channel in REQUIRED_CHANNELS:
            join_message += f"{channel}\n"
        await message.reply(join_message)
        return

    about_text = (
        "*The Medical Content Bot ✨*\n\n"
        "I knew Telegram was a gold mine for all the students\n"
        "Thus, I came up with an idea of this bot!\n\n"
        "However, sometimes the things I would create may need some help to be alive.\n"
        "Alone, I do so little. Believe me when I say this — Together, we can do much better!\n\n"
        "🫡 [Upgrade to Premium](https://t.me/medcontentbotinformation/2)\n\n"
        "*About the Bot:*\n"
        "Usage limit — `1 CPU | 2 GB RAM`\n"
        "Hosting Cost — `₹560/Month`\n\n"
        "All the Best! 🙌"
    )
    await message.reply(about_text, parse_mode=ParseMode.MARKDOWN)


async def handle_invalid_command(message: types.Message):
    """Silently ignore non-command text from users (avoids noisy 'Invalid command' replies)."""
    # Only reply if the message looks like a mistyped command
    if message.text and message.text.startswith('/'):
        await message.reply(
            "Unknown command. Type /help for a list of available commands."
        )