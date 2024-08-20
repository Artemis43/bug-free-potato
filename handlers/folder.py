from aiogram import types, exceptions
from middlewares.authorization import is_private_chat, is_user_member
from config import ADMIN_IDS, REQUIRED_CHANNELS, CHANNEL_ID
from utils.database import cursor, conn
from utils.helpers import set_current_upload_folder

async def create_folder(message: types.Message):
    if not is_private_chat(message):
        return
    user_id = message.from_user.id

    cursor.execute('SELECT status FROM users WHERE user_id = ?', (user_id,))
    user = cursor.fetchone()

    if not user or user[0] != 'approved':
        await message.reply("You are not authorized to create folders. Please wait for admin approval.")
        return

    if not await is_user_member(user_id):
        join_message = "Welcome to The Medical Content Bot ✨\n\nJoin our backup channels to remain connected ✊\n"
        for channel in REQUIRED_CHANNELS:
            join_message += f"{channel}\n"
        await message.reply(join_message)
    else:
        if str(user_id) not in ADMIN_IDS:
            await message.reply("You are not authorized to create folders.")
            return

        args = message.get_args().split(' ', 2)
        folder_name = args[0]
        premium = 0  # Default to non-premium
        admin_approval = 0  # Default to no admin approval required

        if len(args) > 1 and args[1].strip().upper() == 'PREMIUM':
            premium = 1
        if len(args) > 2 and args[2].strip().upper() == 'ADMIN_APPROVAL':
            admin_approval = 1

        if not folder_name:
            await message.reply("Please specify a folder name.")
            return

        cursor.execute('INSERT INTO folders (name, premium, admin_approval) VALUES (?, ?, ?)', (folder_name, premium, admin_approval))
        conn.commit()

        set_current_upload_folder(user_id, folder_name)

        await message.reply(f"Folder '{folder_name}' created {'as a PREMIUM folder' if premium else ''}{' with admin approval required' if admin_approval else ''} and set as the current upload folder.")

async def rename_folder(message: types.Message):
    if not is_private_chat(message):
        return
    user_id = message.from_user.id
    
    cursor.execute('SELECT status FROM users WHERE user_id = ?', (user_id,))
    user = cursor.fetchone()

    if not user or user[0] != 'approved':
        await message.reply("You are not authorized to rename folders. Please wait for admin approval.")
        return
    
    if not await is_user_member(user_id):
        join_message = "Welcome to The Medical Content Bot ✨\n\nI have the ever-growing archive of Medical content 👾\n\nJoin our backup channels to remain connected ✊\n"
        for channel in REQUIRED_CHANNELS:
            join_message += f"{channel}\n"
        await message.reply(join_message)
    else:
        if str(message.from_user.id) not in ADMIN_IDS:
            await message.reply("You are not authorized to rename folders.")
            return

        args = message.get_args().split(',')
        if len(args) != 2:
            await message.reply("Please specify the current folder name and the new folder name in the format: /renamefolder <current_name>,<new_name>")
            return

        current_name, new_name = args

        # Check if the folder with the current name exists
        cursor.execute('SELECT id FROM folders WHERE name = ?', (current_name,))
        folder_id = cursor.fetchone()

        if not folder_id:
            await message.reply("Folder not found.")
            return

        # Check if the new folder name already exists
        cursor.execute('SELECT id FROM folders WHERE name = ?', (new_name,))
        existing_folder = cursor.fetchone()

        if existing_folder:
            await message.reply(f"A folder with the name '{new_name}' already exists. Please choose a different name.")
            return

        # Update the folder name in the database
        cursor.execute('UPDATE folders SET name = ? WHERE id = ?', (new_name, folder_id[0]))
        conn.commit()
        
        await message.reply(f"Folder '{current_name}' has been renamed to '{new_name}'.")


async def delete_folder(message: types.Message):
    from main import bot
    if not is_private_chat(message):
        return
    user_id = message.from_user.id

    cursor.execute('SELECT status FROM users WHERE user_id = ?', (user_id,))
    user = cursor.fetchone()

    if not user or user[0] != 'approved':
        await message.reply("You are not authorized to delete folders. Please wait for admin approval.")
        return
    
    if not await is_user_member(user_id):
        join_message = "Welcome to The Medical Content Bot ✨\n\nI have the ever-growing archive of Medical content 👾\n\nJoin our backup channels to remain connected ✊\n"
        for channel in REQUIRED_CHANNELS:
            join_message += f"{channel}\n"
        await message.reply(join_message)
        return

    if str(message.from_user.id) not in ADMIN_IDS:
        await message.reply("You are not authorized to delete folders.")
        return

    folder_name = message.get_args()
    if not folder_name:
        await message.reply("Please specify a folder name.")
        return

    # Get the folder ID to be deleted
    cursor.execute('SELECT id FROM folders WHERE name = ?', (folder_name,))
    folder_id = cursor.fetchone()

    if not folder_id:
        await message.reply("Folder not found.")
        return

    folder_id = folder_id[0]

    # Get the message IDs of the files in the folder
    cursor.execute('SELECT message_id FROM files WHERE folder_id = ?', (folder_id,))
    message_ids = cursor.fetchall()

    # Delete the files from the channel and the database
    for message_id in message_ids:
        try:
            await bot.delete_message(CHANNEL_ID, message_id[0])
        except exceptions.MessageToDeleteNotFound:
            continue  # Skip if the message is not found

    cursor.execute('DELETE FROM files WHERE folder_id = ?', (folder_id,))

    # Delete the folder from the database
    cursor.execute('DELETE FROM folders WHERE id = ?', (folder_id,))
    conn.commit()

    await message.reply(f"Folder '{folder_name}' and its contents deleted.")