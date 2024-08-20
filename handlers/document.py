import shutil
import os
import sys
from aiogram import types
#from main import bot
from config import ADMIN_IDS, REQUIRED_CHANNELS, DB_FILE_PATH, CHANNEL_ID
from middlewares.authorization import is_private_chat, is_user_member
from utils.database import cursor, conn
from utils.helpers import get_current_upload_folder

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
    else:
        global awaiting_new_db_upload

        from backup import awaiting_new_db_upload

        if awaiting_new_db_upload and message.document.file_name == "file_management.db":
            if str(user_id) not in ADMIN_IDS:
                awaiting_new_db_upload = False
                await message.reply("You are not authorized to upload a new database file.")
                return

            file_path = f"new_{message.document.file_name}"
            await message.document.download(destination_file=file_path)

            shutil.move(file_path, DB_FILE_PATH)
            awaiting_new_db_upload = False
            await message.reply("Database file replaced successfully. Restarting the bot to apply changes.")

            os.execl(sys.executable, sys.executable, *sys.argv)
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

        # Send the file to the channel with the specific caption and get the message ID
        sent_message = await bot.send_document(CHANNEL_ID, file_id, caption=specific_caption)
        message_id = sent_message.message_id

        cursor.execute('INSERT INTO files (file_id, file_name, folder_id, message_id, caption) VALUES (?, ?, ?, ?, ?)', 
                    (file_id, file_name, folder_id, message_id, specific_caption))
        conn.commit()

        await message.reply(f"File '{file_name}' uploaded successfully with the caption: {specific_caption}")