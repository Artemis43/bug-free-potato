import logging
import sqlite3
import asyncio
#from main import bot
from datetime import datetime, timedelta
from aiogram import types, exceptions
from config import REQUIRED_CHANNELS, ADMIN_IDS
from middlewares.authorization import is_private_chat, is_user_member
from utils.database import cursor, conn

async def set_premium_status(message: types.Message):
    if not is_private_chat(message):
        return
    
    if not await is_user_member(message.from_user.id):
        join_message = "Welcome to The Medical Content Bot ✨\n\nI have the ever-growing archive of Medical content 👾\n\nJoin our backup channels to remain connected ✊\n"
        for channel in REQUIRED_CHANNELS:
            join_message += f"{channel}\n"
        await message.reply(join_message)
    else:
        if str(message.from_user.id) not in ADMIN_IDS:
            await message.reply("You are not authorized.")
            return
    # Split the message to get the folder ID and the new premium status
    try:
        parts = message.text.split()
        folder_id = int(parts[1])
        premium_status = int(parts[2])
        
        if premium_status not in (0, 1):
            raise ValueError("Invalid premium status. Use 0 for non-premium and 1 for premium.")
        
        # Update the premium status in the database
        cursor.execute('''
        UPDATE folders
        SET premium = ?
        WHERE id = ?
        ''', (premium_status, folder_id))
        conn.commit()

        await message.reply(f"Folder ID {folder_id} premium status set to {premium_status}.")
    except (IndexError, ValueError) as e:
        await message.reply("Usage: /setfolder <folder_id> <0 or 1>\nExample: /setfolder 123 1")
    except sqlite3.Error as e:
        await message.reply(f"An error occurred while updating the folder: {e}")


async def set_premium(message: types.Message):
    from main import bot
    if not is_private_chat(message):
        return
    if str(message.from_user.id) in ADMIN_IDS:
        args = message.get_args().split()

        if len(args) != 2:
            await message.reply("Usage: /setuser <user_id> <on|off>")
            return

        user_id, action = args
        user_id = int(user_id)
        action = action.lower()

        if action == 'on':
            expiration_date = datetime.now() + timedelta(days=15)
            cursor.execute('''
                UPDATE users 
                SET premium = 1, premium_expiration = ? 
                WHERE user_id = ?
            ''', (expiration_date, user_id))
            conn.commit()
            
            await message.reply(f"User {user_id} has been marked as premium until {expiration_date}.")
            
            try:
                await bot.send_message(user_id, "Congratulations! You have been upgraded to Premium for 15 days.")
            except exceptions.BotBlocked:
                await message.reply(f"Could not notify user {user_id}, as they have blocked the bot.")

            # Schedule task to remove premium after 15 days
            asyncio.create_task(remove_premium_after_expiry(user_id, expiration_date))

        elif action == 'off':
            cursor.execute('''
                UPDATE users 
                SET premium = 0, premium_expiration = NULL 
                WHERE user_id = ?
            ''', (user_id,))
            conn.commit()
            
            await message.reply(f"User {user_id} has been removed from premium status.")
        else:
            await message.reply("Invalid action. Use 'on' to set premium or 'off' to remove premium.")
    else:
        await message.reply("You are not authorized to perform this action.")

async def remove_premium_after_expiry(user_id: int, expiration_date: datetime):
    from main import bot
    now = datetime.now()
    sleep_time = (expiration_date - now).total_seconds()
    await asyncio.sleep(sleep_time)
    
    cursor.execute('''
        UPDATE users 
        SET premium = 0, premium_expiration = NULL 
        WHERE user_id = ? AND premium_expiration <= ?
    ''', (user_id, datetime.now()))
    conn.commit()
    
    try:
        await bot.send_message(user_id, "Your Premium has expired.")
    except exceptions.BotBlocked:
        logging.warning(f"Could not notify user {user_id} about premium expiration, as they have blocked the bot.")