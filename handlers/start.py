import asyncio
import logging
from aiogram import types, exceptions
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from middlewares.authorization import is_private_chat, is_user_member
from utils.database import add_user_to_db, cursor, conn
from utils.helpers import notify_admins
from config import REQUIRED_CHANNELS, STICKER_ID, ADMIN_IDS, API_KEY, DB_FILE_PATH, DBNAME, DBOWNER

async def send_ui(chat_id, message_id=None, current_folder=None, selected_letter=None):
    from main import bot
    # Fetch the number of files and folders
    cursor.execute('SELECT COUNT(*) FROM folders')
    folder_count = cursor.fetchone()[0]
    cursor.execute('SELECT COUNT(*) FROM files')
    file_count = cursor.fetchone()[0]

    # Visual representation of the current location
    current_path = "Root"
    if current_folder:
        current_path = f"Root / {current_folder}"

    # Create inline keyboard for navigation
    keyboard = InlineKeyboardMarkup()
    keyboard.add(InlineKeyboardButton("🙃 Refresh", callback_data='root'))

    # Get chat info to retrieve the name
    chat = await bot.get_chat(chat_id)
    chat_name = chat.full_name if chat.full_name else chat.username

    # Check if the user is a premium user and fetch the premium expiration date
    cursor.execute('SELECT premium, premium_expiration FROM users WHERE user_id = ?', (chat_id,))
    user_data = cursor.fetchone()

    is_premium_user = user_data and user_data[0] == 1
    premium_expiration = user_data[1] if is_premium_user else None

    # Compose the UI message text
    text = f"Hello {chat_name}👋,\n\n"
    text += f"*I'm The Medical Content Bot* ✨\n"
    text += f"About Us: /about\n"
    text += f"How to Use: /help\n\n"

    if is_premium_user:
        text += f"🥳 *You are a Premium User!*\n\n"
    else:
        text += f"🌟 [Upgrade to Premium](https://t.me/medcontentbotinformation/2)\n\n"

    text += f"**List of Folders 🔽**\n\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\n\n"

    # Fetch and list all folders, including premium and admin approval status
    cursor.execute('SELECT name, premium, admin_approval FROM folders WHERE parent_id IS NULL ORDER BY name')
    folders = cursor.fetchall()

    # Check if there are no folders
    if not folders:
        from handlers import sync
        text += "No folders available. Please sync with the database.\n"
        await sync.sync_database(api_key=API_KEY , db_owner=DBOWNER, db_name=DBNAME, db_path=DB_FILE_PATH)  # Call your sync command here

    else:
        # Add folders to the text with appropriate labeling
        for folder_name, premium, admin_approval in folders:
            label = ""
            if not is_premium_user and premium:
                label = " (Premium)"
            elif admin_approval:
                label = " (Paid)"

            text += f"|-📒 `{folder_name}`{label}\n"

    text += "\n\n\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\n\n"

    if is_premium_user:
        text += f"`To download Paid-folders,`\n👉 [Contact Admin](https://t.me/Art3mis_adminbot)"
    else:
        text += f"`For Paid-folders OR Premium,`\n👉 [Contact Admin](https://t.me/Art3mis_adminbot)"

    try:
        if message_id:
            await bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text, reply_markup=keyboard, parse_mode='Markdown')
        else:
            await bot.send_message(chat_id, text, reply_markup=keyboard, parse_mode='Markdown')
    except exceptions.MessageNotModified:
        pass  # Handle the exception gracefully by ignoring it

async def process_callback(callback_query: types.CallbackQuery):
    from main import bot
    global current_upload_folder
    user_id = callback_query.from_user.id

    # Check if the callback data is not related to approval or rejection
    if not callback_query.data.startswith('approval_'):
        if not await is_user_member(user_id):
            join_message = "Welcome to The Medical Content Bot ✨\n\nI have the ever-growing archive of Medical content 👾\n\nJoin our backup channels to remain connected ✊\n"
            for channel in REQUIRED_CHANNELS:
                join_message += f"{channel}\n"
            await bot.answer_callback_query(callback_query.id)
            await bot.send_message(callback_query.from_user.id, join_message)
            return

        code = callback_query.data

        if code == 'root':
            await send_ui(callback_query.from_user.id, callback_query.message.message_id)
        else:
            current_upload_folder = code
            await send_ui(callback_query.from_user.id, callback_query.message.message_id, current_folder=current_upload_folder)

        await bot.answer_callback_query(callback_query.id)

"""# The UI of the bot
async def send_ui(chat_id, message_id=None, current_folder=None, selected_letter=None):
    # Fetch the number of files and folders
    cursor.execute('SELECT COUNT(*) FROM folders')
    folder_count = cursor.fetchone()[0]
    cursor.execute('SELECT COUNT(*) FROM files')
    file_count = cursor.fetchone()[0]

    # Visual representation of the current location
    current_path = "Root"
    if current_folder:
        current_path = f"Root / {current_folder}"

    # Create inline keyboard for navigation
    keyboard = InlineKeyboardMarkup()
    keyboard.add(InlineKeyboardButton("🙃 Refresh", callback_data='root'))

    # Get chat info to retrieve the name
    chat = await bot.get_chat(chat_id)
    chat_name = chat.full_name if chat.full_name else chat.username

    # Compose the UI message text
    text = (
        f"**Hello `{chat_name}`👋,**\n\n"
        f"**I'm The Medical Content Bot ✨**\n"
        f"**About Me:** /about\n"
        f"**How to Use:** /help\n\n"
        f"**List of Folders 🔽**\n\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\n\n"
    )

    # Check if the user is premium
    cursor.execute('SELECT premium FROM users WHERE user_id = ?', (chat_id,))
    is_premium_user = cursor.fetchone()
    is_premium_user = is_premium_user and is_premium_user[0]

    # Fetch and list folders in alphabetical order, filter based on premium status
    if is_premium_user:
        cursor.execute('SELECT name FROM folders WHERE parent_id IS NULL ORDER BY name')
    else:
        cursor.execute('SELECT name FROM folders WHERE parent_id IS NULL AND premium = 0 ORDER BY name')

    folders = cursor.fetchall()

    # Add folders to the text
    for folder in folders:
        text += f"|-📒 `{folder[0]}`\n"

    text += "\n\n\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\n\n`Please share any files that you may think are useful to others :D` - [Share](https://t.me/Art3mis_adminbot)"

    try:
        if message_id:
            await bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text, reply_markup=keyboard, parse_mode='Markdown')
        else:
            await bot.send_message(chat_id, text, reply_markup=keyboard, parse_mode='Markdown')
    except exceptions.MessageNotModified:
        pass  # Handle the exception gracefully by ignoring it"""

async def handle_start(message: types.Message):
    from main import bot
    if not is_private_chat(message):
        return
    user_id = message.from_user.id
    username = message.from_user.username
    add_user_to_db(user_id)
    cursor.execute('SELECT status, welcome_sent FROM users WHERE user_id = ?', (user_id,))
    user = cursor.fetchone()

    if user[0] == 'pending':
        await message.answer("Hello,\nI'm The Medical Content Bot ✨\n\nTo prevent scammers and copyright strikes, we allow only Medical students to use this bot 🙃\n\n👉 Verify Now:\nhttps://t.me/medcontentbotinformation/4\n\nYou will be granted access only after verification!")
        await notify_admins(user_id, username)  # Ensure this is after the initial message to the user
    elif user[0] == 'approved':
        if user[1] == 0:  # Check if welcome message has not been sent
            await message.answer("Welcome! You have been given access to the bot 🙌")
            cursor.execute('UPDATE users SET welcome_sent = 1 WHERE user_id = ?', (user_id,))
            conn.commit()

        if not await is_user_member(user_id):
            sticker_msg = await bot.send_sticker(message.chat.id, STICKER_ID)
            await asyncio.sleep(3)
            await bot.delete_message(message.chat.id, sticker_msg.message_id)
            join_message = "Welcome to The Medical Content Bot ✨\n\nI have the ever-growing archive of Medical content 👾\n\nJoin our backup channels to remain connected ✊\n\nAfter joining 👉 /start\n"
            keyboard = InlineKeyboardMarkup(row_width=1)
            for channel in REQUIRED_CHANNELS:
                button = InlineKeyboardButton(text=channel, url=f"https://t.me/{channel.lstrip('@')}")
                keyboard.add(button)
            await message.reply(join_message, reply_markup=keyboard)
        else:
            sticker_msg = await bot.send_sticker(message.chat.id, STICKER_ID)
            await asyncio.sleep(2)
            await bot.delete_message(message.chat.id, sticker_msg.message_id)
            await send_ui(message.chat.id)
    elif user[0] == 'rejected':
        await message.answer("Your access request has been rejected. You cannot use this bot 😢\n\nIf you think this is a mistake, Contact Us: @Art3mis_adminbot")

"""async def handle_start(message: types.Message):
    from main import bot
    if not is_private_chat(message):
        return
    user_id = message.from_user.id
    username = message.from_user.username
    add_user_to_db(user_id)
    cursor.execute('SELECT status FROM users WHERE user_id = ?', (user_id,))
    user = cursor.fetchone()

    if user[0] == 'pending':
        await message.answer("Hello,\nI'm The Medical Content Bot ✨\n\nTo prevent scammers and copyright strikes, we allow only Medical students to use this bot 🙃\n\n👉 Verify Now:\nhttps://t.me/medcontentbotinformation/4\n\nYou will be granted access only after verification!")
        await notify_admins(user_id, username)  # Ensure this is after the initial message to the user
    elif user[0] == 'approved':
        await message.answer("Welcome! You have been given access to the bot 🙌")
        if not await is_user_member(user_id):
            sticker_msg = await bot.send_sticker(message.chat.id, STICKER_ID)
            await asyncio.sleep(3)
            await bot.delete_message(message.chat.id, sticker_msg.message_id)
            join_message = "Welcome to The Medical Content Bot ✨\n\nI have the ever-growing archive of Medical content 👾\n\nJoin our backup channels to remain connected ✊\n\nAfter joining 👉 /start\n"
            keyboard = InlineKeyboardMarkup(row_width=1)
            for channel in REQUIRED_CHANNELS:
                button = InlineKeyboardButton(text=channel, url=f"https://t.me/{channel.lstrip('@')}")
                keyboard.add(button)
            await message.reply(join_message, reply_markup=keyboard)
        else:
            sticker_msg = await bot.send_sticker(message.chat.id, STICKER_ID)
            await asyncio.sleep(2)
            await bot.delete_message(message.chat.id, sticker_msg.message_id)
            await send_ui(message.chat.id)
    elif user[0] == 'rejected':
        await message.answer("Your access request has been rejected. You cannot use this bot 😢\n\nIf you think this is a mistake, Contact Us: @Art3mis_adminbot")"""


async def approve_user(message: types.Message):
    from main import bot
    user_id = int(message.text.split('_')[1])
    cursor.execute('UPDATE users SET status = ? WHERE user_id = ?', ('approved', user_id))
    conn.commit()
    await message.answer(f"User {user_id} has been approved.")
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
    user_id = int(message.text.split('_')[1])
    cursor.execute('UPDATE users SET status = ? WHERE user_id = ?', ('rejected', user_id))
    conn.commit()
    await message.answer(f"User {user_id} has been rejected.")
    try:
        await bot.send_message(user_id, "You have been rejected from using the bot 🫤\n\nIf you think this is a mistake, **Contact Us:** @Art3mis_adminbot")
    except exceptions.BotBlocked:
        logging.warning(f"User {user_id} has blocked the bot.")
    except exceptions.ChatNotFound:
        logging.warning(f"User {user_id} chat not found.")
    except Exception as e:
        logging.error(f"Error sending rejection message to user {user_id}: {e}")