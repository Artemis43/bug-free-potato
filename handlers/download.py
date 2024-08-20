from datetime import datetime, timedelta
import asyncio
import logging
#from main import bot
from aiogram import types, exceptions
from config import REQUIRED_CHANNELS
from aiogram.types import ParseMode
from aiogram.utils.exceptions import MessageNotModified
from utils.helpers import notify_admin_for_approval
from middlewares.authorization import is_private_chat, is_user_member
from utils.database import cursor, conn

async def get_all_files(message: types.Message):
    from main import bot
    if not is_private_chat(message):
        return
    user_id = message.from_user.id

    cursor.execute('SELECT status, premium, last_download FROM users WHERE user_id = ?', (user_id,))
    user_info = cursor.fetchone()

    if not user_info or user_info[0] != 'approved':
        await message.reply("You are not authorized to download content. Please wait for admin approval.")
        return

    user_status, is_premium, last_download = user_info

    # Check if the user is a member of the required channels
    if not await is_user_member(user_id):
        join_message = "Welcome to The Medical Content Bot ✨\n\nJoin our backup channels to remain connected ✊\n"
        for channel in REQUIRED_CHANNELS:
            join_message += f"{channel}\n"
        await message.reply(join_message)
        return

    folder_name = message.get_args()
    if not folder_name:
        await message.reply("Please specify a folder name.")
        return

    # Get the folder ID, premium status, and admin approval requirement
    cursor.execute('SELECT id, premium, admin_approval FROM folders WHERE name = ?', (folder_name,))
    folder_info = cursor.fetchone()

    if not folder_info:
        await message.reply("Folder not found.")
        return

    folder_id, is_premium_folder, requires_admin_approval = folder_info

    # Check if the folder is premium and if the user is allowed to access it
    if is_premium_folder and not is_premium:
        await message.reply("This folder is for premium users only. Please upgrade to access it.")
        return

    # If the folder requires admin approval, apply the approval logic
    if requires_admin_approval:
        cursor.execute('''
        SELECT approved, download_completed FROM user_folder_approval 
        WHERE user_id = ? AND folder_id = ?
        ''', (user_id, folder_id))
        approval_info = cursor.fetchone()

        if not approval_info or approval_info[0] != 1:
            await notify_admin_for_approval(user_id, folder_id, folder_name)
            await message.reply("Your download request has been sent to the admin for approval.")
            return

        if approval_info[1] == 1:
            await message.reply("You have already downloaded this folder. Please request approval again if needed.")
            return

    # Simulate connecting to servers with a progress bar
    progress_message = await message.reply("⚡Connecting to servers...\n[░░░░░░░░░░░░░░░░░░░░░]", parse_mode=ParseMode.MARKDOWN)

    progress_bar_length = 21  # Length of the progress bar
    update_interval = 7 / progress_bar_length  # Time interval for each progress update (7 seconds in total)

    for i in range(1, progress_bar_length + 1):
        progress_bar = "█" * i + "░" * (progress_bar_length - i)
        await progress_message.edit_text(f"⚡Connecting to servers...\n[{progress_bar}]", parse_mode=ParseMode.MARKDOWN)
        await asyncio.sleep(update_interval)

    # Display "Download starting..." message for 3 seconds
    await progress_message.edit_text("🚀Download is starting...", parse_mode=ParseMode.MARKDOWN)
    await asyncio.sleep(3)

    # Update the message to show the information
    user_type = "Premium" if is_premium else "Free"
    folder_type = "Premium" if is_premium_folder else "Free"

    # Determine if a delay should be applied between files
    file_interval = 5 if is_premium else 60  # 5 seconds for premium users, 1 minute (60 seconds) for free users

    # The time interval to delay the next download, based on user type
    time_interval = timedelta(minutes=2) if is_premium else timedelta(minutes=7)

    next_download_time = (time_interval.total_seconds() / 60)

    # Customize the info_message based on user type
    if is_premium:
        info_message = (
            f"🎉 *Premium User*\n\n"
            f"User ID: `{user_id}`\n"
            f"Folder Name: `{folder_name}`\n"
            f"Folder Type: `{folder_type} folder`\n"
            f"Delay Between Files: `{file_interval} secs`\n"
            f"Next Download only after `{int(next_download_time)} mins`\n\n"
            "🙌 *Thank You for Downloading!*"
        )
    else:
        info_message = (
            f"🔓 *Free User*\n\n"
            f"User ID: `{user_id}`\n"
            f"Folder Name: `{folder_name}`\n"
            f"Folder Type: `{folder_type} folder`\n"
            f"Delay Between Files: `{file_interval} secs`\n"
            f"Next Download only after `{int(next_download_time)} mins`\n\n"
            "🎉 *Consider Upgrading to Premium for Faster Downloads!*"
        )

    await progress_message.edit_text(info_message, parse_mode=ParseMode.MARKDOWN)

    # Increment the download count
    cursor.execute('''
    UPDATE folders
    SET download_count = download_count + 1
    WHERE id = ?
    ''', (folder_id,))
    conn.commit()

    # Get the file IDs, names, and captions in the folder
    cursor.execute('SELECT file_id, file_name, caption FROM files WHERE folder_id = ?', (folder_id,))
    files = cursor.fetchall()

    if files:
        # Determine the time to delete messages based on the number of files
        num_files = len(files)
        if num_files <= 25:
            delete_time = 120  # 2 minutes in seconds
        elif num_files <= 50:
            delete_time = 180  # 3 minutes in seconds
        elif num_files <= 75:
            delete_time = 240  # 4 minutes in seconds
        else:
            delete_time = 300  # Default to 5 minutes if more than 75 files

        messages_to_delete = []

        for file in files:
            sent_message = await bot.send_document(message.chat.id, file[0], caption=file[2])
            messages_to_delete.append(sent_message.message_id)

            # Wait for the appropriate interval before sending the next file
            if file_interval > 0:
                await asyncio.sleep(file_interval)

        # Notify the user that files will be deleted and start the countdown immediately
        warning_message = await message.reply(f"To prevent copyright, the files will be deleted in {delete_time // 60} mins. Forward files to Saved Messages!")

        # Update the last download time for the user after all files are sent
        current_time = datetime.now()  # Update current time after sending the files
        cursor.execute('''
        UPDATE users
        SET last_download = ?
        WHERE user_id = ?
        ''', (current_time.strftime("%Y-%m-%d %H:%M:%S"), user_id))
        conn.commit()

        # If admin approval was required, mark the download as completed
        if requires_admin_approval:
            cursor.execute('''
            UPDATE user_folder_approval
            SET download_completed = 1
            WHERE user_id = ? AND folder_id = ?
            ''', (user_id, folder_id))
            conn.commit()

        # Schedule deletion of messages after the calculated time
        await asyncio.sleep(delete_time)

        for message_id in messages_to_delete:
            try:
                await bot.delete_message(message.chat.id, message_id)
            except exceptions.MessageToDeleteNotFound:
                continue

        # Edit the warning message to indicate files have been deleted
        try:
            await bot.edit_message_text("All Downloaded files deleted.\nAll the Best!", chat_id=message.chat.id, message_id=warning_message.message_id)
        except MessageNotModified:
            pass
    else:
        await message.reply("No files found in the specified folder.")

async def handle_approval(message: types.Message):
    from main import bot

    try:
        _, user_id, folder_id = message.text.split()
        user_id, folder_id = int(user_id), int(folder_id)
        
        # Update the approval status in the database
        cursor.execute('''
        INSERT INTO user_folder_approval (user_id, folder_id, approved) 
        VALUES (?, ?, 1)
        ON CONFLICT(user_id, folder_id) DO UPDATE SET approved = 1, download_completed = 0
        ''', (user_id, folder_id))
        conn.commit()

        # Notify the user that they are approved
        await bot.send_message(user_id, "Your request to download the folder has been approved by the admin. You can now download it.")
        await message.reply("User has been approved.")
        
    except Exception as e:
        logging.error(f"Error in handle_approval: {e}")
        await message.reply("Failed to approve the user.")

async def handle_rejection(message: types.Message):
    from main import bot

    try:
        _, user_id, folder_id = message.text.split()
        user_id, folder_id = int(user_id), int(folder_id)

        # Update the approval status in the database to rejected
        cursor.execute('''
        DELETE FROM user_folder_approval WHERE user_id = ? AND folder_id = ?
        ''', (user_id, folder_id))
        conn.commit()

        # Notify the user that they are rejected
        await bot.send_message(user_id, "Your request to download the folder has been rejected by the admin.")
        await message.reply("User's request has been rejected.")
        
    except Exception as e:
        logging.error(f"Error in handle_rejection: {e}")
        await message.reply("Failed to reject the user.")