from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest
from utils.bot_ref import get_bot
import html as _html
import logging
from datetime import datetime, timedelta

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from utils.database import db_execute, db_fetchone
from config import ADMIN_IDS, NOTIFY_COOLDOWN_HOURS, ADMIN_GROUP_ID

# ── HTML escaping helper ──────────────────────────────────────────────────────

def esc(text) -> str:
    """Escape a value for safe embedding in HTML parse_mode messages."""
    return _html.escape(str(text)) if text is not None else ''


# ── Upload-folder state (persisted in DB per admin) ──────────────────────────

def set_current_upload_folder(user_id: int, folder_id: int) -> None:
    """Persist the admin's active upload folder (by ID) to the DB."""
    from utils.database import db_execute
    db_execute(
        'UPDATE users SET current_upload_folder_id = %s WHERE user_id = %s',
        (folder_id, user_id)
    )


def get_current_upload_folder(user_id: int) -> int | None:
    """Return the admin's active upload folder ID from the DB (None if not set)."""
    from utils.database import db_fetchone
    row = db_fetchone(
        'SELECT current_upload_folder_id FROM users WHERE user_id = %s',
        (user_id,)
    )
    return row[0] if row else None


# ── Admin notification helpers ────────────────────────────────────────────────

async def notify_admins(user_id: int, username: str, first_name: str = None):
    """Send a new-user approval request to the admin group (or first admin DM).

    Respects NOTIFY_COOLDOWN_HOURS — skips notification if admin was already
    notified about this user within the cooldown window.
    """
    # Cooldown check
    if NOTIFY_COOLDOWN_HOURS > 0:
        row = db_fetchone('SELECT last_notified FROM users WHERE user_id = %s', (user_id,))
        if row and row[0]:
            last = row[0]
            if hasattr(last, 'tzinfo') and last.tzinfo is not None:
                last = last.replace(tzinfo=None)
            if (datetime.now() - last) < timedelta(hours=NOTIFY_COOLDOWN_HOURS):
                logging.info(f"Skipping admin notification for user {user_id} — within cooldown.")
                return

    db_execute(
        'UPDATE users SET last_notified = %s WHERE user_id = %s',
        (datetime.now(), user_id)
    )

    name_str  = esc(first_name or 'Unknown')
    uname_str = f"@{esc(username)}" if username else 'no username'

    text = (
        f"🔔 <b>New Access Request</b>\n"
        f"─────────────────────────\n"
        f"👤 <b>Name:</b>     {name_str}\n"
        f"💬 <b>Username:</b> {uname_str}\n"
        f"🆔 <b>User ID:</b>  <code>{user_id}</code>\n"
        f"─────────────────────────\n"
        f"Tap a button to approve or reject."
    )

    # Inline approve / reject buttons on the notification itself
    kb = InlineKeyboardMarkup()
    kb.row(
        InlineKeyboardButton("✅ Approve", callback_data=f"approve:{user_id}"),
        InlineKeyboardButton("❌ Reject",  callback_data=f"reject:{user_id}"),
    )

    # Send to group if configured, otherwise DM the first admin
    target = ADMIN_GROUP_ID if ADMIN_GROUP_ID else ADMIN_IDS[0]

    try:
        await bot.send_message(target, text, parse_mode=ParseMode.HTML, reply_markup=kb)
    except TelegramForbiddenError:
        logging.warning(f"Could not reach admin target {target} — bot blocked.")
    except TelegramBadRequest:
        logging.warning(f"Admin target {target} not found.")
    except Exception as e:
        logging.error(f"Error sending approval request to {target}: {e}")


async def notify_admin_for_approval(user_id: int, folder_id: int, folder_name: str):
    """Ask admin to approve a one-time paid-folder download (first request)."""
    db_execute(
        '''
        INSERT INTO user_folder_approval (user_id, folder_id, approved, download_completed)
        VALUES (%s, %s, FALSE, FALSE)
        ON CONFLICT (user_id, folder_id) DO UPDATE
            SET approved = FALSE, download_completed = FALSE
        ''',
        (user_id, folder_id)
    )

    # Fetch user display info for context
    row = db_fetchone('SELECT first_name, username FROM users WHERE user_id = %s', (user_id,))
    name_str  = esc(row[0] if row and row[0] else f'User {user_id}')
    uname_str = f"@{esc(row[1])}" if row and row[1] else 'no username'

    kb = InlineKeyboardMarkup()
    kb.row(
        InlineKeyboardButton("✅ Approve", callback_data=f"papprove:{user_id}:{folder_id}"),
        InlineKeyboardButton("❌ Reject",  callback_data=f"preject:{user_id}:{folder_id}"),
    )

    target = ADMIN_GROUP_ID if ADMIN_GROUP_ID else ADMIN_IDS[0]
    try:
        await bot.send_message(
            target,
            f"📥 <b>Paid-Folder Download Request</b>\n\n"
            f"Name: {name_str}\n"
            f"Username: {uname_str}\n"
            f"ID: <code>{user_id}</code>\n\n"
            f"Folder: <b>{esc(folder_name)}</b> (ID: {folder_id})\n\n"
            f"Tap a button below to approve or reject.",
            parse_mode=ParseMode.HTML,
            reply_markup=kb
        )
    except Exception as e:
        logging.error(f"Failed to notify admin of download request: {e}")


async def notify_admin_for_approval_again(user_id: int, folder_id: int, folder_name: str):
    """Notify admin of a *repeat* paid-folder download request."""
    db_execute(
        '''
        UPDATE user_folder_approval
        SET download_completed = FALSE
        WHERE user_id = %s AND folder_id = %s
        ''',
        (user_id, folder_id)
    )

    row = db_fetchone('SELECT first_name, username FROM users WHERE user_id = %s', (user_id,))
    name_str  = esc(row[0] if row and row[0] else f'User {user_id}')
    uname_str = f"@{esc(row[1])}" if row and row[1] else 'no username'

    kb = InlineKeyboardMarkup()
    kb.row(
        InlineKeyboardButton("✅ Approve Again", callback_data=f"papprove:{user_id}:{folder_id}"),
        InlineKeyboardButton("❌ Reject",        callback_data=f"preject:{user_id}:{folder_id}"),
    )

    target = ADMIN_GROUP_ID if ADMIN_GROUP_ID else ADMIN_IDS[0]
    try:
        await bot.send_message(
            target,
            f"⚠️ <b>REPEAT Paid-Folder Request</b>\n\n"
            f"Name: {name_str}\n"
            f"Username: {uname_str}\n"
            f"ID: <code>{user_id}</code>\n\n"
            f"Folder: <b>{esc(folder_name)}</b> (ID: {folder_id})\n\n"
            f"This user has already downloaded this folder once.",
            parse_mode=ParseMode.HTML,
            reply_markup=kb
        )
    except Exception as e:
        logging.error(f"Failed to notify admin of repeat request: {e}")
