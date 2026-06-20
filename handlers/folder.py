from aiogram.types import InlineKeyboardMarkup
from utils.keyboard import InlineBuilder, IKB as InlineKeyboardButton
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest
from aiogram import Router
import logging
from aiogram import types
from aiogram import Router
from aiogram.enums import ParseMode
from aiogram import Router
from middlewares.authorization import is_private_chat
from config import ADMIN_IDS
from utils.database import db_fetchone, db_fetchall, db_execute
from utils.helpers import set_current_upload_folder, esc

router = Router()

log = logging.getLogger(__name__)

# In-memory pending deletions: { user_id: (folder_name, folder_id) }
# NOTE: _pending_deletions is now managed in start.py so callbacks route there.
# This module exposes execute_folder_deletion() called from start.process_callback.


def _trigger_catalog_update() -> None:
    """Fire-and-forget: schedule a background catalog regeneration."""
    import asyncio
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(_do_catalog_update())
    except Exception:
        pass


async def _do_catalog_update():
    try:
        from utils.bot_ref import get_bot
        from utils.catalog import generate_catalog
        bot = get_bot()
        me = await bot.me()
        await generate_catalog(me.username)
    except Exception as e:
        log.debug(f"[Folder] Catalog update skipped: {e}")



async def create_folder(message: types.Message):
    if not is_private_chat(message):
        return
    if str(message.from_user.id) not in ADMIN_IDS:
        await message.reply("You are not authorized to create folders.")
        return

    # Parse flags (PREMIUM, PAID) from the END of the args so that
    # multi-word names like "Human Anatomy PREMIUM" are handled correctly.
    words = (message.text.split(None, 1)[1].split() if message.text and len(message.text.split(None, 1)) > 1 else [])

    premium        = False
    admin_approval = False

    # Strip trailing flag keywords
    while words and words[-1].upper() in ('PREMIUM', 'PAID'):
        flag = words.pop().upper()
        if flag == 'PREMIUM':
            premium = True
        elif flag == 'PAID':
            admin_approval = True

    folder_name = ' '.join(words).strip()

    if not folder_name:
        await message.reply(
            "Usage: <code>/newfolder &lt;name&gt; [PREMIUM] [PAID]</code>\n\n"
            "The flags must come <b>after</b> the full folder name.\n"
            "Examples:\n"
            "<code>/newfolder Human Anatomy</code>\n"
            "<code>/newfolder Surgery Notes PREMIUM</code>\n"
            "<code>/newfolder Exclusive Slides PREMIUM PAID</code>",
            parse_mode=ParseMode.HTML
        )
        return

    if db_fetchone('SELECT id FROM folders WHERE name = %s', (folder_name,)):
        await message.reply(
            f"❌ A folder named <b>{esc(folder_name)}</b> already exists.",
            parse_mode=ParseMode.HTML
        )
        return

    db_execute(
        'INSERT INTO folders (name, premium, admin_approval) VALUES (%s, %s, %s)',
        (folder_name, premium, admin_approval)
    )

    new_folder = db_fetchone('SELECT id FROM folders WHERE name = %s', (folder_name,))
    if new_folder:
        set_current_upload_folder(message.from_user.id, new_folder[0])

    badges = []
    if premium:        badges.append("⭐ Premium")
    if admin_approval: badges.append("💰 Paid")
    type_str = "  •  " + "  •  ".join(badges) if badges else ""

    await message.reply(
        f"✅ <b>Folder Created</b>\n\n"
        f"📁 {esc(folder_name)}{type_str}\n\n"
        f"Upload folder is now set to <b>{esc(folder_name)}</b>. Send files to add them.",
        parse_mode=ParseMode.HTML
    )
    # Update search vector for new folder
    db_execute(
        "UPDATE folders SET search_vector = to_tsvector('english', name) WHERE name = %s",
        (folder_name,)
    )
    # Trigger catalog auto-update
    _trigger_catalog_update()


async def rename_folder(message: types.Message):
    if not is_private_chat(message):
        return
    if str(message.from_user.id) not in ADMIN_IDS:
        await message.reply("You are not authorized to rename folders.")
        return

    args = (message.text.split(None, 1)[1].split(',') if message.text and len(message.text.split(None, 1)) > 1 else [])
    if len(args) != 2:
        await message.reply(
            "Usage: <code>/renamefolder &lt;current_name&gt;,&lt;new_name&gt;</code>",
            parse_mode=ParseMode.HTML
        )
        return

    current_name = args[0].strip()
    new_name     = args[1].strip()

    if not current_name or not new_name:
        await message.reply("Folder names cannot be empty.")
        return

    folder_row = db_fetchone('SELECT id FROM folders WHERE name = %s', (current_name,))
    if not folder_row:
        await message.reply(
            f"Folder <b>{esc(current_name)}</b> not found.", parse_mode=ParseMode.HTML
        )
        return

    if db_fetchone('SELECT id FROM folders WHERE name = %s', (new_name,)):
        await message.reply(
            f"A folder named <b>{esc(new_name)}</b> already exists.", parse_mode=ParseMode.HTML
        )
        return

    db_execute('UPDATE folders SET name = %s WHERE id = %s', (new_name, folder_row[0]))
    # Also update the search vector for the new name
    db_execute(
        "UPDATE folders SET search_vector = to_tsvector('english', name) WHERE id = %s",
        (folder_row[0],)
    )
    await message.reply(
        f"✅ Renamed: <b>{esc(current_name)}</b> → <b>{esc(new_name)}</b>",
        parse_mode=ParseMode.HTML
    )
    _trigger_catalog_update()


async def delete_folder(message: types.Message):
    """Step 1: show folder info and confirm via inline buttons."""
    if not is_private_chat(message):
        return
    user_id = message.from_user.id

    if str(user_id) not in ADMIN_IDS:
        await message.reply("You are not authorized to delete folders.")
        return

    folder_name = (message.text.split(None, 1)[1].strip() if message.text and len(message.text.split(None, 1)) > 1 else '')
    if not folder_name:
        await message.reply(
            "Usage: <code>/deletefolder &lt;folder name&gt;</code>",
            parse_mode=ParseMode.HTML
        )
        return

    folder_row = db_fetchone('SELECT id FROM folders WHERE name = %s', (folder_name,))
    if not folder_row:
        await message.reply(
            f"Folder <b>{esc(folder_name)}</b> not found.", parse_mode=ParseMode.HTML
        )
        return

    folder_id  = folder_row[0]
    file_count = db_fetchone('SELECT COUNT(*) FROM files WHERE folder_id = %s', (folder_id,))[0]

    # Store pending deletion in start.py's shared dict
    from handlers.start import _pending_deletions
    _pending_deletions[user_id] = (folder_name, folder_id)

    kb = InlineBuilder()
    kb.row(
        InlineKeyboardButton("✅ Yes, delete", callback_data=f"dfc:{folder_id}"),
        InlineKeyboardButton("❌ Cancel",       callback_data="dfc_cancel"),
    )
    await message.reply(
        f"⚠️ <b>Confirm Deletion</b>\n\n"
        f"📁 Folder: <b>{esc(folder_name)}</b>\n"
        f"📄 Files: {file_count}\n\n"
        f"This will permanently delete the folder and all {file_count} file(s) "
        f"from the archive channel. This <b>cannot be undone</b>.",
        parse_mode=ParseMode.HTML,
        reply_markup=kb.build()
    )


async def execute_folder_deletion(bot, original_message, folder_id: int, folder_name: str):
    """Step 2: perform the actual deletion (called from start.process_callback)."""
    row = db_fetchone('SELECT id FROM folders WHERE id = %s', (folder_id,))
    if not row:
        await bot.edit_message_text(
            f"Folder <b>{esc(folder_name)}</b> was already deleted.",
            parse_mode=ParseMode.HTML,
            chat_id=original_message.chat.id,
            message_id=original_message.message_id
        )
        return

    # Gather every physical copy of this folder's files across ALL storage
    # channels (active or not — a disabled channel may still hold messages we
    # should clean up). One file can have several copies; we delete each.
    locations = db_fetchall(
        '''
        SELECT sc.chat_id, fl.message_id
        FROM file_locations fl
        JOIN files f             ON f.id = fl.file_pk
        JOIN storage_channels sc ON sc.id = fl.channel_id
        WHERE f.folder_id = %s
        ''',
        (folder_id,)
    )
    total_copies  = len(locations)
    deleted_count = 0

    for chat_id, msg_id in locations:
        if msg_id is None:
            continue
        try:
            await bot.delete_message(chat_id, msg_id)
            deleted_count += 1
        except TelegramBadRequest:
            pass
        except Exception as e:
            logging.error(f"Error deleting message {msg_id} in channel {chat_id}: {e}")

    # Deleting the files cascades to file_locations (ON DELETE CASCADE).
    db_execute('DELETE FROM files   WHERE folder_id = %s', (folder_id,))
    db_execute('DELETE FROM folders WHERE id = %s',        (folder_id,))

    await bot.edit_message_text(
        f"✅ <b>Folder Deleted</b>\n\n"
        f"📁 {esc(folder_name)}\n"
        f"🗑 {deleted_count}/{total_copies} channel copies removed.",
        parse_mode=ParseMode.HTML,
        chat_id=original_message.chat.id,
        message_id=original_message.message_id
    )