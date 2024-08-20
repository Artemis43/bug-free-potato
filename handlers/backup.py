import logging
from aiogram import types
from config import ADMIN_IDS
from middlewares.authorization import is_private_chat
#from bot import bot
from utils.database import conn

async def send_backup(message: types.Message):
    from main import bot
    if not is_private_chat(message):
        return
    if str(message.from_user.id) not in ADMIN_IDS:
        await message.reply("You are not authorized to get the backup.")
        return

    # Commit any pending transactions to ensure the database is up to date
    conn.commit()

    # Path to the database file
    db_file_path = 'file_management.db'
    
    try:
        await bot.send_document(message.chat.id, types.InputFile(db_file_path))
    except Exception as e:
        logging.error(f"Error sending backup file: {e}")
        await message.reply("Error sending backup file. Please try again later.")