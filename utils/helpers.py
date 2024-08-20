import logging
from aiogram import exceptions, types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
#from main import current_upload_folders, bot
from utils.database import cursor, conn
from config import ADMIN_IDS

# Function to set the current upload folder for a user
def set_current_upload_folder(user_id, folder_name):
    from main import current_upload_folders
    current_upload_folders[user_id] = folder_name

# Function to get the current upload folder for a user
def get_current_upload_folder(user_id):
    from main import current_upload_folders
    return current_upload_folders.get(user_id)

# Function to set the DB upload await state
def set_bot_state(key: str, value: bool):
    cursor.execute('''
        INSERT INTO bot_state (key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
    ''', (key, int(value)))
    conn.commit()

# Function to get the DB upload await state
def get_bot_state(key: str) -> bool:
    cursor.execute('SELECT value FROM bot_state WHERE key = ?', (key,))
    result = cursor.fetchone()
    if result:
        return bool(result[0])
    return False

# Notifies Admins for approve/reject permission of the bot for new users
async def notify_admins(user_id, username):
    from main import bot
    username = username or "N/A"  # Use "N/A" if the username is None
    first_admin_id = ADMIN_IDS[0]  # Get the first admin ID

    try:
        await bot.send_message(
            first_admin_id,
            f"User @{username} (ID: {user_id}) is requesting access to the bot. Approve?\n\n/approve_{user_id}\n\n/reject_{user_id}"
        )
    except exceptions.BotBlocked:
        logging.warning(f"Admin {first_admin_id} has blocked the bot.")
    except exceptions.ChatNotFound:
        logging.warning(f"Admin {first_admin_id} chat not found.")
    except Exception as e:
        logging.error(f"Error sending message to admin {first_admin_id}: {e}")

# Notifies Admin for one-time folder download approval/rejection
async def notify_admin_for_approval(user_id: int, folder_id: int, folder_name: str):
    from main import bot
    
    # Reset the download_completed field to allow a new download attempt
    cursor.execute('''
    UPDATE user_folder_approval
    SET download_completed = 0
    WHERE user_id = ? AND folder_id = ?
    ''', (user_id, folder_id))
    conn.commit()

    first_admin_id = ADMIN_IDS[0]
    
    # Notify the admin to approve or reject by replying with specific commands
    try:
        await bot.send_message(
            first_admin_id, 
            f"User ID: {user_id} has requested to download the folder '{folder_name}'.\n"
            f"Reply with `/approve {user_id} {folder_id}` to approve.\n"
            f"Reply with `/reject {user_id} {folder_id}` to reject."
        )
    except Exception as e:
        logging.error(f"Failed to notify admin: {e}")