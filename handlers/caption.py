from aiogram import types
from aiogram import Router
from aiogram.enums import ParseMode
from aiogram import Router
from config import ADMIN_IDS
from middlewares.authorization import is_private_chat
from utils.database import db_execute
from utils.helpers import esc

router = Router()


async def set_caption(message: types.Message):
    """Admin command: /caption <custom|append> <text>  — set global file caption."""
    if not is_private_chat(message):
        return

    if str(message.from_user.id) not in ADMIN_IDS:
        await message.reply("You are not authorized to set captions.")
        return

    args = (message.text.split(None, 1)[1] if message.text and len(message.text.split(None, 1)) > 1 else '')
    if not args:
        await message.reply(
            "Usage: <code>/caption &lt;custom|append&gt; &lt;your text&gt;</code>\n\n"
            "<code>custom</code> — replace all captions with this text\n"
            "<code>append</code> — append this text to existing captions\n\n"
            "Examples:\n"
            "<code>/caption custom @Medical_Contentbot</code>\n"
            "<code>/caption append — Do not redistribute</code>",
            parse_mode=ParseMode.HTML
        )
        return

    # Split only at the FIRST space so multi-word caption text is preserved
    args_split   = args.split(' ', 1)
    caption_type = args_split[0].lower()
    custom_text  = args_split[1] if len(args_split) > 1 else ''

    if caption_type not in ('custom', 'append'):
        await message.reply(
            "Invalid option. Use <code>custom &lt;text&gt;</code> or <code>append &lt;text&gt;</code>.",
            parse_mode=ParseMode.HTML
        )
        return

    db_execute('DELETE FROM current_caption')
    db_execute(
        'INSERT INTO current_caption (caption_type, custom_text) VALUES (%s, %s)',
        (caption_type, custom_text)
    )

    await message.reply(
        f"✅ Caption set to <b>{esc(caption_type)}</b>:\n<code>{esc(custom_text)}</code>",
        parse_mode=ParseMode.HTML
    )