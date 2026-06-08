import logging
from aiogram import exceptions
from aiogram.types import ParseMode
from utils.database import db_execute, db_fetchone
from config import ADMIN_IDS

# ---------------------------------------------------------------------------
# Upload-folder state (in-memory, per admin session)
# ---------------------------------------------------------------------------

def set_current_upload_folder(user_id, folder_name):
    from main import current_upload_folders
    current_upload_folders[user_id] = folder_name

def get_current_upload_folder(user_id):
    from main import current_upload_folders
    return current_upload_folders.get(user_id)

# ---------------------------------------------------------------------------
# Bot state  (persisted in PostgreSQL)
# ---------------------------------------------------------------------------

def set_bot_state(key: str, value: bool):
    """Upsert a boolean flag in bot_state using PostgreSQL syntax."""
    db_execute(
        '''
        INSERT INTO bot_state (key, value) VALUES (%s, %s)
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
        ''',
        (key, int(value))
    )

def get_bot_state(key: str) -> bool:
    """Return the boolean value of a bot-state flag (default False)."""
    row = db_fetchone('SELECT value FROM bot_state WHERE key = %s', (key,))
    return bool(row[0]) if row else False

# ---------------------------------------------------------------------------
# Admin notification helpers
# ---------------------------------------------------------------------------

async def notify_admins(user_id: int, username: str):
    """Send a new-user approval request to the primary admin."""
    from main import bot
    username = username or "N/A"
    first_admin_id = ADMIN_IDS[0]

    try:
        await bot.send_message(
            first_admin_id,
            f"👤 User @{username} (ID: `{user_id}`) is requesting access.\n\n"
            f"▶️ /approve\\_{user_id}\n"
            f"❌ /reject\\_{user_id}",
            parse_mode=ParseMode.MARKDOWN
        )
    except exceptions.BotBlocked:
        logging.warning(f"Admin {first_admin_id} has blocked the bot.")
    except exceptions.ChatNotFound:
        logging.warning(f"Admin {first_admin_id} chat not found.")
    except Exception as e:
        logging.error(f"Error sending message to admin {first_admin_id}: {e}")


async def notify_admin_for_approval(user_id: int, folder_id: int, folder_name: str):
    """Ask admin to approve a one-time folder download (first request)."""
    from main import bot

    # Reset download_completed so the user can attempt again after approval
    db_execute(
        '''
        UPDATE user_folder_approval
        SET download_completed = FALSE
        WHERE user_id = %s AND folder_id = %s
        ''',
        (user_id, folder_id)
    )

    first_admin_id = ADMIN_IDS[0]
    try:
        await bot.send_message(
            first_admin_id,
            f"📥 *Download Request*\n\n"
            f"*User ID:* `{user_id}`\n"
            f"*Folder:* `{folder_name}`\n\n"
            f"Approve: `/approve {user_id} {folder_id}`\n"
            f"Reject:  `/reject {user_id} {folder_id}`",
            parse_mode=ParseMode.MARKDOWN
        )
    except Exception as e:
        logging.error(f"Failed to notify admin: {e}")


async def notify_admin_for_approval_again(user_id: int, folder_id: int, folder_name: str):
    """Notify admin of a *repeat* download request for the same folder."""
    from main import bot

    db_execute(
        '''
        UPDATE user_folder_approval
        SET download_completed = FALSE
        WHERE user_id = %s AND folder_id = %s
        ''',
        (user_id, folder_id)
    )

    first_admin_id = ADMIN_IDS[0]
    try:
        await bot.send_message(
            first_admin_id,
            f"⚠️ *REPEAT REQUEST*\n\n"
            f"*User ID:* `{user_id}`\n"
            f"*Folder:* `{folder_name}`\n\n"
            f"Approve: `/approve {user_id} {folder_id}`\n"
            f"Reject:  `/reject {user_id} {folder_id}`",
            parse_mode=ParseMode.MARKDOWN
        )
    except Exception as e:
        logging.error(f"Failed to notify admin: {e}")
