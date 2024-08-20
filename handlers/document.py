import shutil
import os
import sys
from aiogram import types
#from main import bot
from config import ADMIN_IDS, REQUIRED_CHANNELS, DB_FILE_PATH, CHANNEL_ID
from middlewares.authorization import is_private_chat, is_user_member
from utils.database import cursor, conn
from utils.helpers import get_current_upload_folder, set_bot_state, get_bot_state

async def handle_document(message: types.Message):
    from main import bot
    if not is_private_chat(message):
        return
    user_id = message.from_user.id

    cursor.execute('SELECT status FROM users WHERE user_id = ?', (user_id,))
    user = cursor.fetchone()

    if not user or user[0] != 'approved':
        await message.reply("You are not authorized to upload documents. Please wait for admin approval.")
        return

    if not await is_user_member(user_id):
        join_message = "Welcome to The Medical Content Bot ✨\n\nI have the ever-growing archive of Medical content 👾\n\nJoin our backup channels to remain connected 😉\n"
        for channel in REQUIRED_CHANNELS:
            join_message += f"{channel}\n"
        await message.reply(join_message)
        return
    
    # Check if awaiting a new DB upload
    if get_bot_state('awaiting_new_db_upload') and message.document.file_name == "file_management.db":
        if str(user_id) not in ADMIN_IDS:
            set_bot_state('awaiting_new_db_upload', False)
            await message.reply("You are not authorized to upload a new database file.")
            return

        # Define the path to the old and new database files
        old_db_path = DB_FILE_PATH
        new_file_path = f"new_{message.document.file_name}"

        # Download the new database file
        await message.document.download(destination_file=new_file_path)

        try:
            # Delete the old database file
            if os.path.exists(old_db_path):
                os.remove(old_db_path)
                await message.reply("Old database file deleted successfully.")
            else:
                await message.reply("Old database file not found. Proceeding with the replacement.")

            # Move the new file to replace the old database
            shutil.move(new_file_path, DB_FILE_PATH)
            set_bot_state('awaiting_new_db_upload', False)
            await message.reply("Database file replaced successfully. Restarting the bot to apply changes.")

            # Restart the bot to apply the new database changes
            os.execl(sys.executable, sys.executable, *sys.argv)
        except Exception as e:
            set_bot_state('awaiting_new_db_upload', False)
            await message.reply(f"An error occurred while replacing the database file: {e}")
            return

    if str(user_id) not in ADMIN_IDS:
        await message.reply("You are not authorized to upload files.")
        return

    file_id = message.document.file_id
    file_name = message.document.file_name

    current_upload_folder = get_current_upload_folder(user_id)
    if current_upload_folder:
        cursor.execute('SELECT id FROM folders WHERE name = ?', (current_upload_folder,))
        folder_id = cursor.fetchone()
        if folder_id:
            folder_id = folder_id[0]
        else:
            folder_id = None
    else:
        folder_id = None

    # Get the current caption configuration
    cursor.execute('SELECT caption_type, custom_text FROM current_caption ORDER BY id DESC LIMIT 1')
    caption_config = cursor.fetchone()

    if caption_config:
        caption_type, custom_text = caption_config
        if caption_type == 'custom':
            specific_caption = custom_text
        elif caption_type == 'append':
            # Append the custom text to the document's original caption
            specific_caption = f"{message.caption or ''}\n{custom_text}"
    else:
        # Default caption if no custom caption is set
        specific_caption = message.caption or "@Medical_Contentbot\nEver-growing archive of medical content"

    # Proceed with the file upload
    sent_message = await bot.send_document(CHANNEL_ID, file_id, caption=specific_caption)
    message_id = sent_message.message_id

    cursor.execute('INSERT INTO files (file_id, file_name, folder_id, message_id, caption) VALUES (?, ?, ?, ?, ?)', 
                    (file_id, file_name, folder_id, message_id, specific_caption))
    conn.commit()

    await message.reply(f"File '{file_name}' uploaded successfully with the caption: {specific_caption}")