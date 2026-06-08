import asyncio
import logging
from aiogram import types, exceptions
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, ParseMode
from middlewares.authorization import is_private_chat, is_user_member
from utils.database import add_user_to_db, db_fetchone, db_execute, db_fetchall
from utils.helpers import notify_admins
from config import REQUIRED_CHANNELS, STICKER_ID, ADMIN_IDS
from datetime import datetime, timedelta

# Global variables to throttle auto-sync
last_sync_time = None
sync_lock = asyncio.Lock()


# ─────────────────────────────────────────────────────────────────────────────
# UI builder
# ─────────────────────────────────────────────────────────────────────────────

async def send_ui(chat_id, message_id=None, current_folder=None):
    from main import bot

    global last_sync_time

    folder_count = db_fetchone('SELECT COUNT(*) FROM folders')[0]
    file_count   = db_fetchone('SELECT COUNT(*) FROM files')[0]

    keyboard = InlineKeyboardMarkup()
    keyboard.add(InlineKeyboardButton("🙃 Refresh", callback_data='root'))

    chat = await bot.get_chat(chat_id)
    chat_name = chat.full_name or chat.username or str(chat_id)

    user_data = db_fetchone(
        'SELECT premium, premium_expiration FROM users WHERE user_id = %s',
        (chat_id,)
    )
    is_premium_user    = bool(user_data and user_data[0])
    premium_expiration = user_data[1] if is_premium_user else None

    text  = f"Hello {chat_name}👋,\n\n"
    text += f"*I'm The Medical Content Bot* ✨\n"
    text += f"About Us: /about\n"
    text += f"How to Use: /help\n\n"

    if is_premium_user:
        text += f"🥳 *You are a Premium User!*\n\n"
    else:
        text += f"🌟 [Upgrade to Premium](https://t.me/medcontentbotinformation/2)\n\n"

    text += f"**List of Folders 🔽**\n\\_\\_\\_\\_\\_\\_\\_\\_\\_\\_\\_\\_\\_\\_\\_\n\n"

    folders = db_fetchall(
        'SELECT name, premium, admin_approval FROM folders WHERE parent_id IS NULL ORDER BY name'
    )

    if not folders:
        text += "😴😴\nHey there! Sorry I was asleep😅\nTry again in 60 secs."

        try:
            if message_id:
                await bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text,
                                            reply_markup=keyboard, parse_mode='Markdown')
            else:
                await bot.send_message(chat_id, text, reply_markup=keyboard, parse_mode='Markdown')
        except exceptions.MessageNotModified:
            pass

        # Trigger auto-sync at most once every 20 minutes
        now = datetime.now()
        if last_sync_time is None or (now - last_sync_time) >= timedelta(minutes=20):
            async with sync_lock:
                if last_sync_time is None or (datetime.now() - last_sync_time) >= timedelta(minutes=20):
                    last_sync_time = datetime.now()
                    from handlers import sync as sync_module
                    from config import WEBHOOK_HOST
                    # sync_module.sync_database can be called here if needed
                    logging.info("Auto-sync triggered.")
                else:
                    logging.info("Sync already in progress.")
        else:
            logging.info("Sync was recently performed.")
    else:
        for folder_name, premium, admin_approval in folders:
            label = ""
            if not is_premium_user and premium:
                label = " (Premium)"
            elif admin_approval:
                label = " (Paid)"
            text += f"|-📒 `{folder_name}`{label}\n"

        text += "\n\n\\_\\_\\_\\_\\_\\_\\_\\_\\_\\_\\_\\_\\_\\_\\_\n\n"
        if is_premium_user:
            text += "`To download Paid-folders,`\n👉 [Contact Admin](https://t.me/Art3mis_adminbot)"
        else:
            text += "`For Paid-folders OR Premium,`\n👉 [Contact Admin](https://t.me/Art3mis_adminbot)"

        try:
            if message_id:
                await bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text,
                                            reply_markup=keyboard, parse_mode='Markdown')
            else:
                await bot.send_message(chat_id, text, reply_markup=keyboard, parse_mode='Markdown')
        except exceptions.MessageNotModified:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# Callback handler
# ─────────────────────────────────────────────────────────────────────────────

async def process_callback(callback_query: types.CallbackQuery):
    from main import bot
    user_id = callback_query.from_user.id

    if not callback_query.data.startswith('approval_'):
        if not await is_user_member(user_id):
            join_message = (
                "Welcome to The Medical Content Bot ✨\n\n"
                "I have the ever-growing archive of Medical content 👾\n\n"
                "Join our backup channels to remain connected ✊\n"
            )
            for channel in REQUIRED_CHANNELS:
                join_message += f"{channel}\n"
            await bot.answer_callback_query(callback_query.id)
            await bot.send_message(user_id, join_message)
            return

        code = callback_query.data
        if code == 'root':
            await send_ui(user_id, callback_query.message.message_id)
        else:
            await send_ui(user_id, callback_query.message.message_id, current_folder=code)

    await bot.answer_callback_query(callback_query.id)


# ─────────────────────────────────────────────────────────────────────────────
# Sticker helper — optional, skipped gracefully if STICKER_ID is not set
# ─────────────────────────────────────────────────────────────────────────────

async def send_sticker_safe(bot, chat_id: int, delay: float = 2.0):
    """Send the welcome sticker and delete it after `delay` seconds.
    
    If STICKER_ID is not configured, or the sticker file_id is invalid,
    this function logs a warning and returns None instead of raising.
    """
    if not STICKER_ID:
        logging.debug("STICKER_ID is not set — skipping sticker.")
        return None
    try:
        msg = await bot.send_sticker(chat_id, STICKER_ID)
        await asyncio.sleep(delay)
        await bot.delete_message(chat_id, msg.message_id)
        return msg
    except exceptions.BadRequest as e:
        logging.warning(f"Sticker send failed (bad file_id?): {e}")
    except Exception as e:
        logging.warning(f"Sticker send failed: {e}")
    return None


# ─────────────────────────────────────────────────────────────────────────────
# /start
# ─────────────────────────────────────────────────────────────────────────────

async def handle_start(message: types.Message):
    from main import bot
    if not is_private_chat(message):
        return

    user_id  = message.from_user.id
    username = message.from_user.username

    add_user_to_db(user_id)

    user = db_fetchone(
        'SELECT status, welcome_sent FROM users WHERE user_id = %s',
        (user_id,)
    )

    if not user:
        await message.answer("Something went wrong. Please try again.")
        return

    status, welcome_sent = user

    if status == 'pending':
        await message.answer(
            "Hello,\nI'm The Medical Content Bot ✨\n\n"
            "To prevent scammers and copyright strikes, we allow only Medical students to use this bot 🙃\n\n"
            "👉 Verify Now:\nhttps://t.me/medcontentbotinformation/4\n\n"
            "You will be granted access only after verification!"
        )
        await notify_admins(user_id, username)

    elif status == 'approved':
        if not welcome_sent:
            await message.answer("Welcome! You have been given access to the bot 🙌")
            db_execute(
                'UPDATE users SET welcome_sent = TRUE WHERE user_id = %s',
                (user_id,)
            )

        if not await is_user_member(user_id):
            await send_sticker_safe(bot, message.chat.id, delay=3)

            join_message = (
                "Welcome to The Medical Content Bot ✨\n\n"
                "I have the ever-growing archive of Medical content 👾\n\n"
                "Join our backup channels to remain connected ✊\n\nAfter joining 👉 /start\n"
            )
            keyboard = InlineKeyboardMarkup(row_width=1)
            for channel in REQUIRED_CHANNELS:
                keyboard.add(InlineKeyboardButton(text=channel, url=f"https://t.me/{channel.lstrip('@')}"))
            await message.reply(join_message, reply_markup=keyboard)
        else:
            await send_sticker_safe(bot, message.chat.id, delay=2)
            await send_ui(message.chat.id)

    elif status == 'rejected':
        await message.answer(
            "Your access request has been rejected. You cannot use this bot 😢\n\n"
            "If you think this is a mistake, Contact Us: @Art3mis_adminbot"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Admin approve / reject helpers
# ─────────────────────────────────────────────────────────────────────────────

async def approve_user(message: types.Message):
    from main import bot
    try:
        user_id = int(message.text.split('_')[1])
    except (IndexError, ValueError):
        await message.answer("Invalid command format.")
        return

    db_execute(
        "UPDATE users SET status = 'approved' WHERE user_id = %s",
        (user_id,)
    )
    await message.answer(f"✅ User {user_id} has been approved.")

    try:
        await bot.send_message(user_id, "You have been approved to use the bot\n\nClick here 👉 /start")
    except exceptions.BotBlocked:
        logging.warning(f"User {user_id} has blocked the bot.")
    except exceptions.ChatNotFound:
        logging.warning(f"User {user_id} chat not found.")
    except Exception as e:
        logging.error(f"Error sending approval message to user {user_id}: {e}")


async def reject_user(message: types.Message):
    from main import bot
    try:
        user_id = int(message.text.split('_')[1])
    except (IndexError, ValueError):
        await message.answer("Invalid command format.")
        return

    db_execute(
        "UPDATE users SET status = 'rejected' WHERE user_id = %s",
        (user_id,)
    )
    await message.answer(f"❌ User {user_id} has been rejected.")

    try:
        await bot.send_message(
            user_id,
            "You have been rejected from using the bot 🫤\n\n"
            "If you think this is a mistake, **Contact Us:** @Art3mis_adminbot"
        )
    except exceptions.BotBlocked:
        logging.warning(f"User {user_id} has blocked the bot.")
    except exceptions.ChatNotFound:
        logging.warning(f"User {user_id} chat not found.")
    except Exception as e:
        logging.error(f"Error sending rejection message to user {user_id}: {e}")