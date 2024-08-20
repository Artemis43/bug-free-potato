import logging
from aiogram import exceptions, types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
#from main import current_upload_folders, bot
from utils.database import cursor
from config import ADMIN_IDS

# Function to set the current upload folder for a user
def set_current_upload_folder(user_id, folder_name):
    from main import current_upload_folders
    current_upload_folders[user_id] = folder_name

# Function to get the current upload folder for a user
def get_current_upload_folder(user_id):
    from main import current_upload_folders
    return current_upload_folders.get(user_id)

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
    
    # Get the first admin's ID
    first_admin_id = ADMIN_IDS[0]
    
    # Create inline buttons for approval and rejection
    keyboard = InlineKeyboardMarkup()
    approve_button = InlineKeyboardButton(text="Approve ✅", callback_data=f"approve_{user_id}_{folder_id}")
    reject_button = InlineKeyboardButton(text="Reject ❌", callback_data=f"reject_{user_id}_{folder_id}")
    keyboard.add(approve_button, reject_button)
    
    # Send the notification to the first admin
    try:
        await bot.send_message(
            first_admin_id, 
            f"User ID: {user_id} has requested to download the folder '{folder_name}'.\nDo you approve?",
            reply_markup=keyboard
        )
    except Exception as e:
        logging.error(f"Failed to notify admin: {e}")