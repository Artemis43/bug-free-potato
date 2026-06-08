from datetime import datetime, timedelta
import asyncio
import logging
from aiogram import types, exceptions
from config import REQUIRED_CHANNELS
from aiogram.types import ParseMode
from aiogram.utils.exceptions import MessageNotModified
from utils.helpers import notify_admin_for_approval, notify_admin_for_approval_again
from middlewares.authorization import is_private_chat, is_user_member
from utils.database import db_fetchone, db_fetchall, db_execute


async def get_all_files(message: types.Message):
    from main import bot
    if not is_private_chat(message):
        return

    user_id = message.from_user.id

    user_info = db_fetchone(
        'SELECT status, premium, last_download FROM users WHERE user_id = %s',
        (user_id,)
    )

    if not user_info or user_info[0] != 'approved':
        await message.reply("You are not authorized to download content. Please wait for admin approval.")
        return

    user_status, is_premium, last_download = user_info

    # ── Enforce cooldown based on last download time ──────────────────────
    if last_download:
        cooldown = timedelta(minutes=2) if is_premium else timedelta(minutes=7)
        elapsed = datetime.now() - last_download.replace(tzinfo=None)
        if elapsed < cooldown:
            remaining = int((cooldown - elapsed).total_seconds() / 60) + 1
            await message.reply(
                f"⏳ Please wait *{remaining} more minute(s)* before your next download.",
                parse_mode=ParseMode.MARKDOWN
            )
            return

    # ── Force-sub check ───────────────────────────────────────────────────
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

    folder_info = db_fetchone(
        'SELECT id, premium, admin_approval FROM folders WHERE name = %s',
        (folder_name,)
    )

    if not folder_info:
        await message.reply("Folder not found.")
        return

    folder_id, is_premium_folder, requires_admin_approval = folder_info

    # ── Premium-folder gate ───────────────────────────────────────────────
    if is_premium_folder and not is_premium:
        await message.reply("This folder is for premium users only. Please upgrade to access it.")
        return

    # ── Admin-approval gate ───────────────────────────────────────────────
    if requires_admin_approval:
        approval_info = db_fetchone(
            '''
            SELECT approved, download_completed FROM user_folder_approval
            WHERE user_id = %s AND folder_id = %s
            ''',
            (user_id, folder_id)
        )

        if not approval_info or not approval_info[0]:
            await notify_admin_for_approval(user_id, folder_id, folder_name)
            await message.reply("Your download request has been sent to the admin for approval.")
            return

        if approval_info[1]:  # download_completed
            await notify_admin_for_approval_again(user_id, folder_id, folder_name)
            await message.reply(
                "You have already downloaded this folder once. "
                "Please contact the Admin to download it again."
            )
            return

    # ── Temporarily grant premium speed for paid-folder users ─────────────
    temporary_premium = requires_admin_approval and not is_premium
    if temporary_premium:
        is_premium = True

    # ── Progress bar ──────────────────────────────────────────────────────
    progress_message = await message.reply(
        "⚡Connecting to servers…\n[░░░░░░░░░░░░░░░░░░░░░]",
        parse_mode=ParseMode.MARKDOWN
    )

    BAR_LENGTH = 21
    update_interval = 7 / BAR_LENGTH

    for i in range(1, BAR_LENGTH + 1):
        bar = "█" * i + "░" * (BAR_LENGTH - i)
        try:
            await progress_message.edit_text(f"⚡Connecting to servers…\n[{bar}]", parse_mode=ParseMode.MARKDOWN)
        except MessageNotModified:
            pass
        await asyncio.sleep(update_interval)

    await progress_message.edit_text("🚀 Download is starting…", parse_mode=ParseMode.MARKDOWN)
    await asyncio.sleep(3)

    # ── Per-file delay & cooldown period ─────────────────────────────────
    file_interval   = 5   if is_premium else 60
    time_interval   = timedelta(minutes=2) if is_premium else timedelta(minutes=7)
    next_dl_minutes = int(time_interval.total_seconds() / 60)

    if requires_admin_approval:
        folder_type = "Paid"
    elif is_premium_folder:
        folder_type = "Premium"
    else:
        folder_type = "Free"

    if is_premium:
        info_message = (
            f"🎉 *Premium User*\n\n"
            f"User ID: `{user_id}`\n"
            f"Folder: `{folder_name}` ({folder_type})\n"
            f"Delay Between Files: `{file_interval} secs`\n"
            f"Next Download after: `{next_dl_minutes} mins`\n\n"
            "🙌 *Thank You for Downloading!*"
        )
    else:
        info_message = (
            f"🔓 *Free User*\n\n"
            f"User ID: `{user_id}`\n"
            f"Folder: `{folder_name}` ({folder_type})\n"
            f"Delay Between Files: `{file_interval} secs`\n"
            f"Next Download after: `{next_dl_minutes} mins`\n\n"
            "🎉 *Consider Upgrading to Premium for Faster Downloads!*"
        )

    await progress_message.edit_text(info_message, parse_mode=ParseMode.MARKDOWN)

    # ── Increment download counter ────────────────────────────────────────
    db_execute(
        'UPDATE folders SET download_count = download_count + 1 WHERE id = %s',
        (folder_id,)
    )

    # ── Fetch & send files ────────────────────────────────────────────────
    files = db_fetchall(
        'SELECT file_id, file_name, caption, file_type FROM files WHERE folder_id = %s',
        (folder_id,)
    )

    if not files:
        await message.reply("No files found in the specified folder.")
        return

    num_files = len(files)
    if num_files <= 25:
        delete_time = 120
    elif num_files <= 50:
        delete_time = 180
    elif num_files <= 75:
        delete_time = 240
    else:
        delete_time = 300

    messages_to_delete = []

    for index, file in enumerate(files):
        file_id, file_name, caption, file_type = file
        try:
            if file_type == 'document':
                sent = await bot.send_document(message.chat.id, file_id, caption=caption)
            elif file_type == 'video':
                sent = await bot.send_video(message.chat.id, file_id, caption=caption)
            elif file_type == 'photo':
                sent = await bot.send_photo(message.chat.id, file_id, caption=caption)
            else:
                logging.warning(f"Unknown file type '{file_type}' for file_id {file_id}, skipping.")
                continue

            messages_to_delete.append(sent.message_id)
        except Exception as e:
            logging.error(f"Error sending file {file_id}: {e}")
            continue

        if file_interval > 0 and index < len(files) - 1:
            await asyncio.sleep(file_interval)

    # ── Post-send bookkeeping ─────────────────────────────────────────────
    warning_message = await message.reply(
        f"⚠️ To prevent copyright, the files will be deleted in "
        f"{delete_time // 60} min(s). Forward to Saved Messages now!"
    )

    db_execute(
        'UPDATE users SET last_download = %s WHERE user_id = %s',
        (datetime.now(), user_id)
    )

    if requires_admin_approval:
        db_execute(
            '''
            UPDATE user_folder_approval
            SET download_completed = TRUE
            WHERE user_id = %s AND folder_id = %s
            ''',
            (user_id, folder_id)
        )

    # ── Schedule deletion ─────────────────────────────────────────────────
    await asyncio.sleep(delete_time)

    for msg_id in messages_to_delete:
        try:
            await bot.delete_message(message.chat.id, msg_id)
        except exceptions.MessageToDeleteNotFound:
            continue
        except Exception as e:
            logging.error(f"Error deleting message {msg_id}: {e}")

    try:
        await bot.edit_message_text(
            "✅ All downloaded files have been deleted.\nAll the Best!",
            chat_id=message.chat.id,
            message_id=warning_message.message_id
        )
    except MessageNotModified:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Admin: approve / reject paid-folder download requests
# ─────────────────────────────────────────────────────────────────────────────

async def handle_approval(message: types.Message):
    from main import bot
    try:
        parts = message.text.split()
        if len(parts) != 3:
            raise ValueError("Expected /approve <user_id> <folder_id>")
        _, user_id, folder_id = parts
        user_id, folder_id = int(user_id), int(folder_id)
    except ValueError as e:
        await message.reply(f"Usage: /approve <user_id> <folder_id>\n\nError: {e}")
        return

    try:
        db_execute(
            '''
            INSERT INTO user_folder_approval (user_id, folder_id, approved)
            VALUES (%s, %s, TRUE)
            ON CONFLICT (user_id, folder_id) DO UPDATE SET approved = TRUE, download_completed = FALSE
            ''',
            (user_id, folder_id)
        )
        await bot.send_message(
            user_id,
            "✅ Your request to download the folder has been approved by the admin.\n\n"
            "Only *1 download* is allowed.\nYou will get Premium download speed.",
            parse_mode=ParseMode.MARKDOWN
        )
        await message.reply("✅ User has been approved.")
    except Exception as e:
        logging.error(f"Error in handle_approval: {e}")
        await message.reply("Failed to approve the user.")


async def handle_rejection(message: types.Message):
    from main import bot
    try:
        parts = message.text.split()
        if len(parts) != 3:
            raise ValueError("Expected /reject <user_id> <folder_id>")
        _, user_id, folder_id = parts
        user_id, folder_id = int(user_id), int(folder_id)
    except ValueError as e:
        await message.reply(f"Usage: /reject <user_id> <folder_id>\n\nError: {e}")
        return

    try:
        db_execute(
            'DELETE FROM user_folder_approval WHERE user_id = %s AND folder_id = %s',
            (user_id, folder_id)
        )
        await bot.send_message(
            user_id,
            "❌ Your request to download the folder has been rejected by the admin.\n\n"
            "If you think this is a mistake, contact the Admin."
        )
        await message.reply("❌ User's request has been rejected.")
    except Exception as e:
        logging.error(f"Error in handle_rejection: {e}")
        await message.reply("Failed to reject the user.")