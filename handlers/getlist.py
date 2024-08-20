import logging
from aiogram import types
from aiogram.types import ParseMode
from config import ADMIN_IDS, REQUIRED_CHANNELS
from middlewares.authorization import is_private_chat, is_user_member
from utils.database import cursor

async def list_all(message: types.Message):
    if not is_private_chat(message):
        return
    user_id = message.from_user.id
    
    cursor.execute('SELECT status FROM users WHERE user_id = ?', (user_id,))
    user = cursor.fetchone()

    if not user or user[0] != 'approved':
        await message.reply("You are not authorized to use the bot. Please wait for admin approval.")
        return
    
    if not await is_user_member(user_id):
        join_message = "Welcome to The Medical Content Bot ✨\n\nI have the ever-growing archive of Medical content 👾\n\nJoin our backup channels to remain connected ✊\n"
        for channel in REQUIRED_CHANNELS:
            join_message += f"{channel}\n"
        await message.reply(join_message)
    else:
        if str(message.from_user.id) not in ADMIN_IDS:
            await message.reply("You are not authorized to access the database.")
            return
        
    try:
        # Fetch all folders with download counts
        cursor.execute('SELECT id, name, download_count FROM folders')
        folders = cursor.fetchall()

        # Fetch all premium folders with download counts
        cursor.execute('SELECT id, name, download_count FROM folders WHERE premium = 1')
        premium_folders = cursor.fetchall()

        # Fetch all users
        cursor.execute('SELECT user_id FROM users')
        users = cursor.fetchall()

        # Fetch all premium users
        cursor.execute('SELECT user_id FROM users WHERE premium = 1')
        premium_users = cursor.fetchall()

        # Prepare the response message
        response = "<b>Folders:</b>\n"
        if folders:
            response += "\n".join([f"- {folder[1]} (ID: {folder[0]}, Downloads: {folder[2]})" for folder in folders])
        else:
            response += "No folders found."

        response += "\n\n<b>Premium Folders:</b>\n"
        if premium_folders:
            response += "\n".join([f"- {folder[1]} (ID: {folder[0]}, Downloads: {folder[2]})" for folder in premium_folders])
        else:
            response += "No premium folders found."

        response += "\n\n<b>Users:</b>\n"
        if users:
            response += "\n".join([f"- User ID: {user[0]}" for user in users])
        else:
            response += "No users found."

        response += "\n\n<b>Premium Users:</b>\n"
        if premium_users:
            response += "\n".join([f"- User ID: {user[0]}" for user in premium_users])
        else:
            response += "No premium users found."

        # Send the response message
        await message.answer(response, parse_mode=ParseMode.HTML)
    
    except Exception as e:
        logging.error(f"Error in /list command: {e}")
        await message.answer("An error occurred while fetching the list.")