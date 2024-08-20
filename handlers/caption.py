from aiogram import types
from config import REQUIRED_CHANNELS, ADMIN_IDS
from middlewares.authorization import is_private_chat, is_user_member
from utils.database import cursor, conn

async def set_caption(message: types.Message):
    if not is_private_chat(message):
        return
    user_id = message.from_user.id

    cursor.execute('SELECT status FROM users WHERE user_id = ?', (user_id,))
    user = cursor.fetchone()

    if not user or user[0] != 'approved':
        await message.reply("You are not authorized to set captions. Please wait for admin approval.")
        return

    if not await is_user_member(user_id):
        join_message = "Welcome to The Medical Content Bot ✨\n\nI have the ever-growing archive of Medical content 👾\n\nJoin our backup channels to remain connected ✊\n"
        for channel in REQUIRED_CHANNELS:
            join_message += f"{channel}\n"
        await message.reply(join_message)
    else:
        if str(user_id) not in ADMIN_IDS:
            await message.reply("You are not authorized to set captions.")
            return

        args = message.get_args()
        if not args:
            await message.reply("Please specify 'custom <your text>' or 'append <your text>'.")
            return

        args_split = args.split(" ", 1)
        caption_type = args_split[0].lower()
        custom_text = args_split[1] if len(args_split) > 1 else ""

        if caption_type not in ['custom', 'append']:
            await message.reply("Invalid option. Use 'custom <your text>' or 'append <your text>'.")
            return

        # Clear the existing caption configuration
        cursor.execute('DELETE FROM current_caption')
        cursor.execute('INSERT INTO current_caption (caption_type, custom_text) VALUES (?, ?)', (caption_type, custom_text))
        conn.commit()

        await message.reply(f"Caption set to '{caption_type}' with text: {custom_text}")