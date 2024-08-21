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
        # Fetch all folders
        cursor.execute('SELECT id, name, download_count FROM folders')
        folders = cursor.fetchall()

        # Fetch all premium folders
        cursor.execute('SELECT id, name, download_count FROM folders WHERE premium = 1')
        premium_folders = cursor.fetchall()

        # Fetch all admin-approval folders
        cursor.execute('SELECT id, name, download_count FROM folders WHERE admin_approval = 1')
        admin_approval_folders = cursor.fetchall()

        # Fetch all users
        cursor.execute('SELECT user_id FROM users')
        users = cursor.fetchall()

        # Fetch all premium users
        cursor.execute('SELECT user_id FROM users WHERE premium = 1')
        premium_users = cursor.fetchall()

        # Prepare the response message parts
        response_parts = []

        # Add folder details
        response_parts.append("<b>Folders:</b>")
        if folders:
            response_parts.append("\n".join([f"- {folder[1]} (ID: {folder[0]}, Downloads: {folder[2]})" for folder in folders]))
        else:
            response_parts.append("No folders found.")

        # Add premium folder details
        response_parts.append("\n\n<b>Premium Folders:</b>")
        if premium_folders:
            response_parts.append("\n".join([f"- {folder[1]} (ID: {folder[0]}, Downloads: {folder[2]})" for folder in premium_folders]))
        else:
            response_parts.append("No premium folders found.")

        # Add admin-approval folder details
        response_parts.append("\n\n<b>Admin-Approval Folders:</b>")
        if admin_approval_folders:
            response_parts.append("\n".join([f"- {folder[1]} (ID: {folder[0]}, Downloads: {folder[2]})" for folder in admin_approval_folders]))
        else:
            response_parts.append("No admin-approval folders found.")

        # Add user details
        response_parts.append("\n\n<b>Users:</b>")
        if users:
            response_parts.append("\n".join([f"- User ID: {user[0]}" for user in users]))
        else:
            response_parts.append("No users found.")

        # Add premium user details
        response_parts.append("\n\n<b>Premium Users:</b>")
        if premium_users:
            response_parts.append("\n".join([f"- User ID: {user[0]}" for user in premium_users]))
        else:
            response_parts.append("No premium users found.")

        # Split response into multiple messages if too long
        max_message_length = 4096  # Telegram message limit
        response = "\n".join(response_parts)
        messages = [response[i:i+max_message_length] for i in range(0, len(response), max_message_length)]
        
        # Send the response message(s)
        for msg in messages:
            await message.answer(msg, parse_mode=ParseMode.HTML)
    
    except Exception as e:
        logging.error(f"Error in /list command: {e}")
        await message.answer("An error occurred while fetching the list.")