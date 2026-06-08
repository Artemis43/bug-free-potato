from aiogram import types, exceptions
from middlewares.authorization import is_private_chat, is_user_member
from config import ADMIN_IDS, REQUIRED_CHANNELS, CHANNEL_ID
from utils.database import db_fetchone, db_fetchall, db_execute
from utils.helpers import set_current_upload_folder
import logging


async def create_folder(message: types.Message):
    if not is_private_chat(message):
        return
    user_id = message.from_user.id

    user = db_fetchone('SELECT status FROM users WHERE user_id = %s', (user_id,))
    if not user or user[0] != 'approved':
        await message.reply("You are not authorized to create folders. Please wait for admin approval.")
        return

    if not await is_user_member(user_id):
        join_message = "Welcome to The Medical Content Bot ✨\n\nJoin our backup channels to remain connected ✊\n"
        for channel in REQUIRED_CHANNELS:
            join_message += f"{channel}\n"
        await message.reply(join_message)
        return

    if str(user_id) not in ADMIN_IDS:
        await message.reply("You are not authorized to create folders.")
        return

    args = message.get_args().split(' ', 2)
    folder_name = args[0].strip()

    if not folder_name:
        await message.reply("Please specify a folder name.")
        return

    # Check for duplicate folder name
    existing = db_fetchone('SELECT id FROM folders WHERE name = %s', (folder_name,))
    if existing:
        await message.reply(f"A folder named '{folder_name}' already exists.")
        return

    premium        = args[1].strip().upper() == 'PREMIUM' if len(args) > 1 else False
    admin_approval = args[2].strip().upper() == 'PAID'    if len(args) > 2 else False

    db_execute(
        'INSERT INTO folders (name, premium, admin_approval) VALUES (%s, %s, %s)',
        (folder_name, premium, admin_approval)
    )

    set_current_upload_folder(user_id, folder_name)

    flags = []
    if premium:
        flags.append("PREMIUM")
    if admin_approval:
        flags.append("PAID/Admin-Approval")
    flag_str = f" [{', '.join(flags)}]" if flags else ""

    await message.reply(
        f"✅ Folder '{folder_name}'{flag_str} created and set as your active upload folder."
    )


async def rename_folder(message: types.Message):
    if not is_private_chat(message):
        return
    user_id = message.from_user.id

    user = db_fetchone('SELECT status FROM users WHERE user_id = %s', (user_id,))
    if not user or user[0] != 'approved':
        await message.reply("You are not authorized to rename folders. Please wait for admin approval.")
        return

    if not await is_user_member(user_id):
        join_message = "Welcome to The Medical Content Bot ✨\n\nJoin our backup channels to remain connected ✊\n"
        for channel in REQUIRED_CHANNELS:
            join_message += f"{channel}\n"
        await message.reply(join_message)
        return

    if str(user_id) not in ADMIN_IDS:
        await message.reply("You are not authorized to rename folders.")
        return

    args = message.get_args().split(',')
    if len(args) != 2:
        await message.reply(
            "Please specify: `/renamefolder <current_name>,<new_name>`",
            parse_mode='Markdown'
        )
        return

    current_name = args[0].strip()
    new_name     = args[1].strip()

    if not current_name or not new_name:
        await message.reply("Folder names cannot be empty.")
        return

    folder_row = db_fetchone('SELECT id FROM folders WHERE name = %s', (current_name,))
    if not folder_row:
        await message.reply(f"Folder '{current_name}' not found.")
        return

    if db_fetchone('SELECT id FROM folders WHERE name = %s', (new_name,)):
        await message.reply(f"A folder named '{new_name}' already exists.")
        return

    db_execute('UPDATE folders SET name = %s WHERE id = %s', (new_name, folder_row[0]))
    await message.reply(f"✅ Folder '{current_name}' renamed to '{new_name}'.")


async def delete_folder(message: types.Message):
    from main import bot
    if not is_private_chat(message):
        return
    user_id = message.from_user.id

    user = db_fetchone('SELECT status FROM users WHERE user_id = %s', (user_id,))
    if not user or user[0] != 'approved':
        await message.reply("You are not authorized to delete folders. Please wait for admin approval.")
        return

    if not await is_user_member(user_id):
        join_message = "Welcome to The Medical Content Bot ✨\n\nJoin our backup channels to remain connected ✊\n"
        for channel in REQUIRED_CHANNELS:
            join_message += f"{channel}\n"
        await message.reply(join_message)
        return

    if str(user_id) not in ADMIN_IDS:
        await message.reply("You are not authorized to delete folders.")
        return

    folder_name = message.get_args().strip()
    if not folder_name:
        await message.reply("Please specify a folder name.")
        return

    folder_row = db_fetchone('SELECT id FROM folders WHERE name = %s', (folder_name,))
    if not folder_row:
        await message.reply("Folder not found.")
        return

    folder_id = folder_row[0]

    # ── Delete channel messages for all files in this folder ──────────────
    message_ids = db_fetchall('SELECT message_id FROM files WHERE folder_id = %s', (folder_id,))

    deleted_count = 0
    for (msg_id,) in message_ids:
        if msg_id is None:
            continue
        try:
            await bot.delete_message(CHANNEL_ID, msg_id)
            deleted_count += 1
        except exceptions.MessageToDeleteNotFound:
            pass
        except Exception as e:
            logging.error(f"Error deleting channel message {msg_id}: {e}")

    db_execute('DELETE FROM files WHERE folder_id = %s', (folder_id,))
    db_execute('DELETE FROM folders WHERE id = %s', (folder_id,))

    await message.reply(
        f"✅ Folder '{folder_name}' deleted "
        f"({deleted_count}/{len(message_ids)} channel messages removed)."
    )