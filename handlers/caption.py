from aiogram import types
from aiogram.types import ParseMode
from config import REQUIRED_CHANNELS, ADMIN_IDS
from middlewares.authorization import is_private_chat, is_user_member
from utils.database import db_fetchone, db_execute


async def set_caption(message: types.Message):
    """Admin command: /caption <custom|append> <text>  — set global file caption."""
    if not is_private_chat(message):
        return

    user_id = message.from_user.id

    user = db_fetchone('SELECT status FROM users WHERE user_id = %s', (user_id,))
    if not user or user[0] != 'approved':
        await message.reply("You are not authorized to set captions. Please wait for admin approval.")
        return

    if not await is_user_member(user_id):
        join_message = "Welcome to The Medical Content Bot ✨\n\nJoin our backup channels to remain connected ✊\n"
        for channel in REQUIRED_CHANNELS:
            join_message += f"{channel}\n"
        await message.reply(join_message)
        return

    if str(user_id) not in ADMIN_IDS:
        await message.reply("You are not authorized to set captions.")
        return

    args = message.get_args()
    if not args:
        await message.reply(
            "Usage: `/caption <custom|append> <your text>`\n\n"
            "`custom` — replace all captions with this text\n"
            "`append` — append this text to existing captions",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    args_split   = args.split(" ", 1)
    caption_type = args_split[0].lower()
    custom_text  = args_split[1] if len(args_split) > 1 else ""

    if caption_type not in ('custom', 'append'):
        await message.reply(
            "Invalid option. Use `custom <text>` or `append <text>`.",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    # Clear old config and insert the new one
    db_execute('DELETE FROM current_caption')
    db_execute(
        'INSERT INTO current_caption (caption_type, custom_text) VALUES (%s, %s)',
        (caption_type, custom_text)
    )

    await message.reply(
        f"✅ Caption set to *{caption_type}*:\n`{custom_text}`",
        parse_mode=ParseMode.MARKDOWN
    )