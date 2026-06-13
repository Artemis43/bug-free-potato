from utils.bot_ref import get_bot
import logging
from aiogram import types
from aiogram import Router
from config import ADMIN_IDS, REQUIRED_CHANNELS, DEFAULT_CAPTION, BOT_NAME, ADMIN_CONTACT
from middlewares.authorization import is_private_chat, is_user_member
from utils.database import db_fetchone, db_execute_returning
from utils.helpers import get_current_upload_folder, esc
from utils.media_group import collect_media_group
from utils.storage import list_storage_channels, record_file_locations, create_pending_replications

router = Router()


# ── Per-message helpers ─────────────────────────────────────────────────────

def _detect_file(message: types.Message):
    """Return (file_type, file_id, file_name) for a supported media message,
    or (None, None, None) if it carries no supported file."""
    if message.document:
        return 'document', message.document.file_id, message.document.file_name
    if message.video:
        return 'video', message.video.file_id, (message.video.file_name or f"video_{message.message_id}")
    if message.photo:
        return 'photo', message.photo[-1].file_id, f"photo_{message.from_user.id}_{message.message_id}"
    return None, None, None


def _build_caption(message: types.Message) -> str:
    """Resolve the caption to attach, honouring the DB caption config.
    Always appends ADMIN_CONTACT on a new line."""
    caption_config = db_fetchone(
        'SELECT caption_type, custom_text FROM current_caption ORDER BY id DESC LIMIT 1'
    )
    if caption_config:
        caption_type, custom_text = caption_config
        if caption_type == 'custom':
            base = custom_text
        else:
            # 'append' mode: keep the file's own caption, then our custom text.
            base = f"{message.caption or ''}\n{custom_text}".strip()
    else:
        # Fallback: table should never be empty after startup seeding, but guard.
        base = message.caption or DEFAULT_CAPTION
    # Always append admin handle
    return f"{base}\n{ADMIN_CONTACT}"


async def _store_file(message: types.Message, folder_id, channels) -> tuple | None:
    """Mirror one file into every active storage channel and record each copy.

    Returns ``(file_name, replicas, total)`` on success, or None if the message
    has no supported file or could not be stored in ANY channel.
    """
    file_type, file_id, file_name = _detect_file(message)
    if file_type is None:
        return None

    bot     = get_bot()
    caption = _build_caption(message)
    total   = len(channels)
    successes: list[tuple[int, int]] = []   # (channel_id, message_id)

    for ch_id, ch_chat, _title, _active in channels:
        try:
            if file_type == 'document':
                sent = await bot.send_document(ch_chat, file_id, caption=caption)
            elif file_type == 'video':
                sent = await bot.send_video(ch_chat, file_id, caption=caption)
            else:
                sent = await bot.send_photo(ch_chat, file_id, caption=caption)
            successes.append((ch_id, sent.message_id))
        except Exception as e:
            # One channel failing must not abort the others — that's the whole
            # point of redundancy. Log and keep going.
            logging.error(f"Upload of '{file_name}' to channel {ch_chat} failed: {e}")

    if not successes:
        return None

    # Logical file record. files.message_id is kept (= first copy) for
    # backward-compat; the authoritative per-channel copies live in file_locations.
    first_msg_id = successes[0][1]
    row = db_execute_returning(
        '''
        INSERT INTO files (file_id, file_name, folder_id, message_id, caption, file_type)
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING id
        ''',
        (file_id, file_name, folder_id, first_msg_id, caption, file_type)
    )
    record_file_locations(row[0], successes)
    # Queue replications for channels this bot couldn't reach (not an admin there).
    # Another bot that IS admin in those channels will copy_message on its next tick.
    stored_ids = {ch_id for ch_id, _ in successes}
    missed_ids  = {ch[0] for ch in channels} - stored_ids
    if missed_ids:
        create_pending_replications(row[0], missed_ids)
    return (file_name, len(successes), total)


# ── Batch processor (album-aware) ───────────────────────────────────────────

async def _process_upload_batch(messages: list[types.Message]) -> None:
    """Store every file in the batch (fanned out to all channels), then send
    ONE summary reply. All messages come from the same admin (auth checked at
    the entry point), so the folder and channel list are resolved once.
    """
    first   = messages[0]
    user_id = first.from_user.id

    channels = list_storage_channels(active_only=True)
    if not channels:
        await first.reply(
            "⚠️ <b>No storage channels configured.</b>\n\n"
            "Add at least one with <code>/addchannel</code> before uploading."
        )
        return

    folder_id   = get_current_upload_folder(user_id)
    folder_name = None
    if folder_id:
        row = db_fetchone('SELECT name FROM folders WHERE id = %s', (folder_id,))
        folder_name = row[0] if row else None
    folder_label = f"in folder '{esc(folder_name)}'" if folder_name else "(no folder)"

    total_channels = len(channels)
    uploaded: list[tuple[str, int]] = []   # (file_name, replicas)
    failed_files = 0
    for msg in messages:
        result = await _store_file(msg, folder_id, channels)
        if result:
            uploaded.append((result[0], result[1]))
        else:
            failed_files += 1

    if not uploaded:
        await first.reply(
            f"❌ Upload failed {folder_label}. Check /channels and that the bot "
            "is an admin in your storage channels."
        )
        return

    under_replicated = sum(1 for _name, reps in uploaded if reps < total_channels)

    # Single fully-stored file → familiar one-liner.
    if len(uploaded) == 1 and failed_files == 0:
        name, reps = uploaded[0]
        await first.reply(
            f"✅ '{esc(name)}' uploaded {folder_label} — stored in {reps}/{total_channels} channel(s)."
        )
        return

    # Batch (album or partial failures) → one consolidated summary.
    lines = [
        f"✅ Uploaded <b>{len(uploaded)}</b> file(s) {folder_label}.",
        f"🗄 Mirrored across {total_channels} active channel(s).",
    ]
    if under_replicated:
        lines.append(f"⚠️ <b>{under_replicated}</b> file(s) reached fewer than {total_channels} channels.")
    if failed_files:
        lines.append(f"⚠️ <b>{failed_files}</b> file(s) failed entirely — check logs.")
    await first.reply("\n".join(lines))


# ── Entry: authorise the uploader, then buffer (album-aware) ─────────────────

async def handle_upload(message: types.Message) -> None:
    if not is_private_chat(message):
        return

    user_id = message.from_user.id

    user = db_fetchone('SELECT status FROM users WHERE user_id = %s', (user_id,))
    if not user or user[0] != 'approved':
        await message.reply("You are not authorized to upload files. Please wait for admin approval.")
        return

    if not await is_user_member(user_id):
        join_message = (
            f"Welcome to {BOT_NAME} ✨\n\n"
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

    # Authorised admin — collect album items (if any) and process as one batch.
    # A lone file is processed immediately; an album is debounced and handled
    # as a single batch so all of its files are stored and one summary is sent.
    await collect_media_group(message, _process_upload_batch)


# ─────────────────────────────────────────────────────────────────────────────
# Dispatch by media type (registered in utils/register_handlers.py)
# ─────────────────────────────────────────────────────────────────────────────

async def handle_document(message: types.Message):
    await handle_upload(message)

async def handle_video(message: types.Message):
    await handle_upload(message)

async def handle_photo(message: types.Message):
    await handle_upload(message)
