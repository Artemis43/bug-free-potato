from config import REQUIRED_CHANNELS, PREMIUM_INFO_URL, ADMIN_CONTACT, VERIFY_URL
from aiogram import types
from aiogram.types import ParseMode
from middlewares.authorization import is_private_chat, is_user_member
from utils.database import db_fetchone


async def help(message: types.Message):
    if not is_private_chat(message):
        return

    user_id = message.from_user.id
    user = db_fetchone('SELECT status FROM users WHERE user_id = %s', (user_id,))

    if not user or user[0] != 'approved':
        await message.reply("You haven't been approved yet. You'll be notified once an admin reviews your request.")
        return

    if not await is_user_member(user_id):
        await message.reply("Please join our required channels first. Send /start for details.")
        return

    help_text = (
        "<b>How to Use the Medical Content Bot</b> ✨\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "📂 <b>Method 1 — Tap a folder button (easiest!)</b>\n"
        "Send /start → tap any folder button → files arrive automatically.\n\n"
        "⌨️ <b>Method 2 — Type a command</b>\n"
        "<code>/download &lt;folder name&gt;</code> — downloads all files from that folder.\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "⏳ <b>Cooldown between downloads:</b>\n"
        "  🔓 Free — 7 minutes\n"
        "  ⭐ Premium — 2 minutes\n\n"
        "⏱ <b>Time between files:</b>\n"
        "  🔓 Free — 60 seconds\n"
        "  ⭐ Premium — 5 seconds\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "⚠️ <b>Important:</b> Files are deleted after a few minutes to prevent copyright issues.\n"
        "📌 <b>Forward them to Saved Messages immediately!</b>\n\n"
        f'🌟 <a href="{PREMIUM_INFO_URL}">Upgrade to Premium</a>\n'
        "📊 Check your account: /status"
    )
    await message.reply(help_text, parse_mode=ParseMode.HTML)


async def about(message: types.Message):
    if not is_private_chat(message):
        return

    user_id = message.from_user.id
    user = db_fetchone('SELECT status FROM users WHERE user_id = %s', (user_id,))

    if not user or user[0] != 'approved':
        await message.reply("You haven't been approved yet.")
        return

    if not await is_user_member(user_id):
        await message.reply("Please join our required channels first. Send /start for details.")
        return

    about_text = (
        "<b>The Medical Content Bot</b> ✨\n\n"
        "Your personal assistant for medical study materials — organising and distributing "
        "content to verified students across Telegram.\n\n"
        "Access is limited to verified medical students to protect content creators and "
        "avoid copyright issues. Together, we keep this resource alive! 🙌\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "🌟 <b>Want faster downloads &amp; exclusive content?</b>\n"
        f'<a href="{PREMIUM_INFO_URL}">Upgrade to Premium →</a>\n\n'
        "🤝 <b>Support or queries?</b>\n"
        f"Contact us: {ADMIN_CONTACT}\n\n"
        "All the Best! 💙"
    )
    await message.reply(about_text, parse_mode=ParseMode.HTML)


async def handle_invalid_command(message: types.Message):
    if message.text and message.text.startswith('/'):
        await message.reply("Unknown command. 🤔\n\nUse /help to see what I can do.")