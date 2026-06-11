from utils.bot_ref import get_bot
import logging
from aiogram import types
from aiogram import Router
from config import ADMIN_IDS, REQUIRED_CHANNELS, CHANNEL_ID, DEFAULT_CAPTION
from middlewares.authorization import is_private_chat, is_user_member
from utils.database import db_fetchone, db_execute
from utils.helpers import get_current_upload_folder

router = Router()


async def handle_upload(message: types.Message, file_type: str):
    if not is_private_chat(message):
        return

    user_id = message.from_user.id
    bot = get_bot()

    user = db_fetchone('SELECT status FROM users WHERE user_id = %s', (user_id,))

    if not user or user[0] != 'approved':
        await message.reply("You are not authorized to upload files. Please wait for admin approval.")
        return

    if not await is_user_member(user_id):
        join_message = (
            "Welcome to The Medical Content Bot ✨\n\n"
            "I have the ever-growing archive of Medical content 👾\n\n"
            "Join our backup channels to remain connected 😉\n"
        )
        for channel in REQUIRED_CHANNELS:
            join_message += f"{channel}\n"
        await message.reply(join_message)
        return

    # ── Admin-only upload ─────────────────────────────────────────────────
    if str(user_id) not in ADMIN_IDS:
        await message.reply("You are not authorized to upload files.")
        return

    # ── Derive file metadata ───────────────────────────────────────────────
    if file_type == 'document':
        file_id   = message.document.file_id
        file_name = message.document.file_name
    elif file_type == 'video':
        file_id   = message.video.file_id
        file_name = message.video.file_name or f"video_{message.message_id}"
    else:  # photo
        file_id   = message.photo[-1].file_id
        file_name = f"photo_{user_id}_{message.message_id}"

    # ── Determine target folder ───────────────────────────────────────────
    folder_id = get_current_upload_folder(user_id)  # returns int | None directly
    current_upload_folder = None
    if folder_id:
        row = db_fetchone('SELECT name FROM folders WHERE id = %s', (folder_id,))
        current_upload_folder = row[0] if row else None

    # ── Build caption ─────────────────────────────────────────────────────
    caption_config = db_fetchone(
        'SELECT caption_type, custom_text FROM current_caption ORDER BY id DESC LIMIT 1'
    )

    if caption_config:
        caption_type, custom_text = caption_config
        if caption_type == 'custom':
            specific_caption = custom_text
        else:  # append
            specific_caption = f"{message.caption or ''}\n{custom_text}".strip()
    else:
        # Fallback: table should never be empty after startup seeding,
        # but guard defensively.
        specific_caption = message.caption or DEFAULT_CAPTION

    # ── Send to storage channel & save to DB ──────────────────────────────
    try:
        if file_type == 'document':
            sent_message = await bot.send_document(CHANNEL_ID, file_id, caption=specific_caption)
        elif file_type == 'video':
            sent_message = await bot.send_video(CHANNEL_ID, file_id, caption=specific_caption)
        else:
            sent_message = await bot.send_photo(CHANNEL_ID, file_id, caption=specific_caption)

        db_execute(
            '''
            INSERT INTO files (file_id, file_name, folder_id, message_id, caption, file_type)
            VALUES (%s, %s, %s, %s, %s, %s)
            ''',
            (file_id, file_name, folder_id, sent_message.message_id, specific_caption, file_type)
        )

        folder_label = f"in folder '{current_upload_folder}'" if current_upload_folder else "(no folder)"
        await message.reply(f"✅ '{file_name}' uploaded {folder_label}.")

    except Exception as e:
        logging.error(f"Error during file upload: {e}")
        await message.reply(f"An error occurred while uploading the file: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Dispatch by media type
# ─────────────────────────────────────────────────────────────────────────────

async def handle_document(message: types.Message):
    await handle_upload(message, file_type='document')

async def handle_video(message: types.Message):
    await handle_upload(message, file_type='video')

async def handle_photo(message: types.Message):
    await handle_upload(message, file_type='photo')