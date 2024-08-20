from config import REQUIRED_CHANNELS
from aiogram import types
from middlewares.authorization import is_private_chat, is_user_member
from utils.database import cursor

async def help(message: types.Message):
    if not is_private_chat(message):
        return
    user_id = message.from_user.id
    cursor.execute('SELECT status FROM users WHERE user_id = ?', (user_id,))
    user = cursor.fetchone()

    if not user or user[0] != 'approved':
        await message.reply("You are not authorized to use the bot. Please wait for admin approval.")
        return
    if not await is_user_member(user_id):
        join_message = "Welcome to The Medical Content Bot ✨\n\nI have the ever-growing archive of Medical content 👾\n\nJoin our backup channels to remain connected ✊\n"
        for channel in REQUIRED_CHANNELS:
            join_message += f"{channel}\n"
        await message.reply(join_message)
    else:
        help_text = (
            "*The Medical Content Bot* ✨\n\n"
            "/start - Start the bot\n"
            "/help - Display this help message\n"
            "/download <folder\\_name> - Send with Folder name to get all files\n\n"
            "💫 *How to Use:*\n\n"
            "|- Put folder name after /download\n\n"
            "|- Send and get all your files👌\n\n"
            "**NOTE:\n\n**"
            "`We donot host any content; All the content is from third-party servers.`"
            #"**Contact Us:** [Here](https://t.me/MedContent_Adminbot)"
        )
        await message.reply(help_text, parse_mode='Markdown')


async def about(message: types.Message):
    if not is_private_chat(message):
        return
    user_id = message.from_user.id

    cursor.execute('SELECT status FROM users WHERE user_id = ?', (user_id,))
    user = cursor.fetchone()

    if not user or user[0] != 'approved':
        await message.reply("You are not authorized to use the bot. Please wait for admin approval.")
        return
    
    if not await is_user_member(user_id):
        join_message = "Welcome to The Medical Content Bot ✨\n\nI have the ever-growing archive of Medical content 👾\n\nJoin our backup channels to remain connected ✊\n"
        for channel in REQUIRED_CHANNELS:
            join_message += f"{channel}\n"
        await message.reply(join_message)
    else:
        about_text = (
            "*The Medical Content Bot ✨*\n\n"
            "I knew Telegram was a gold mine for all the students who are interested to learn\n"
            "But, most of my time was gone in the search of the desired content. Thus, I came up with an idea of this bot!\n\n"
            "However, sometimes the things I would create may need some help to be alive...\nAlone, I do so little. Believe me when I say this - Together, we can do much better!\n\n"
            "*Upgrade to Premium:* [Here](https://t.me/medcontentbotinformation/2)\n\n"
            "About the Bot:\n"
            "Usage limit - `1 CPU|2 GB (RAM)`\n"
            "Hosting Cost ~ `₹560/Month`\n"
            "Framework - `Python-Flask`\n\n"
            "All the Best!"
        )
        await message.reply(about_text, parse_mode='Markdown')

async def handle_invalid_command(message: types.Message):
    await message.reply("Invalid command. Please use a valid command.")