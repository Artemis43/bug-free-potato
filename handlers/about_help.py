from config import REQUIRED_CHANNELS, PREMIUM_INFO_URL, ADMIN_CONTACT, VERIFY_URL, PAYMENT_MODE
from aiogram import types
from aiogram.types import ParseMode
from middlewares.authorization import is_private_chat, is_user_member, invalidate_member_cache
from utils.database import db_fetchone


def get_help_content() -> str:
    """Return the formatted HTML text for the help message."""
    if PAYMENT_MODE in ('stars', 'razorpay'):
        premium_upgrade = "🌟 <b>Upgrade to Premium:</b> Use /pay to view active plans."
    else:
        premium_upgrade = f"🌟 <b>Upgrade to Premium:</b> Contact {ADMIN_CONTACT} for details."

    return (
        "<b>How to Use the Medical Content Bot</b> ✨\n\n"
        "📂 <b>Method 1 — Tap a folder button (easiest!)</b>\n"
        "Send /start → tap any folder button → files arrive automatically.\n\n"
        "⌨️ <b>Method 2 — Type a command</b>\n"
        "<code>/download &lt;folder name&gt;</code> — downloads all files from that folder.\n\n"
        "⏳ <b>Cooldown between downloads:</b>\n"
        "  🔓 Free — 7 minutes\n"
        "  ⭐ Premium — 2 minutes\n\n"
        "⏱ <b>Time between files:</b>\n"
        "  🔓 Free — 60 seconds\n"
        "  ⭐ Premium — 5 seconds\n\n"
        "⚠️ <b>Important:</b> Files are deleted after a few minutes to prevent copyright issues.\n"
        "📌 <b>Forward them to Saved Messages immediately!</b>\n\n"
        f"{premium_upgrade}\n\n"
        "📊 Check your account: /status"
    )


def get_about_content() -> str:
    """Return the formatted HTML text for the about message."""
    if PAYMENT_MODE in ('stars', 'razorpay'):
        premium_upgrade = "🌟 <b>Want faster downloads &amp; exclusive content?</b>\nUse /pay to Upgrade to Premium! 🚀"
    else:
        premium_upgrade = f"🌟 <b>Want faster downloads &amp; exclusive content?</b>\nContact {ADMIN_CONTACT} to Upgrade to Premium!"

    return (
        "<b>The Medical Content Bot</b> ✨\n\n"
        "Your personal assistant for medical study materials — organising and distributing "
        "content to verified students across Telegram.\n\n"
        "Access is limited to verified medical students to protect content creators and "
        "avoid copyright issues. Together, we keep this resource alive! 🙌\n\n"
        f"{premium_upgrade}\n\n"
        "🤝 <b>Support or queries?</b>\n"
        f"Contact us: {ADMIN_CONTACT}\n\n"
        "All the Best! 💙"
    )


async def help(message: types.Message):
    if not is_private_chat(message):
        return

    user_id = message.from_user.id
    user = db_fetchone('SELECT status FROM users WHERE user_id = %s', (user_id,))

    if not user or user[0] != 'approved':
        await message.reply("You haven't been approved yet. You'll be notified once an admin reviews your request.")
        return

    invalidate_member_cache(user_id)
    if not await is_user_member(user_id):
        await message.reply("Please join our required channels first. Send /start for details.")
        return

    await message.reply(get_help_content(), parse_mode=ParseMode.HTML)


async def about(message: types.Message):
    if not is_private_chat(message):
        return

    user_id = message.from_user.id
    user = db_fetchone('SELECT status FROM users WHERE user_id = %s', (user_id,))

    if not user or user[0] != 'approved':
        await message.reply("You haven't been approved yet.")
        return

    invalidate_member_cache(user_id)
    if not await is_user_member(user_id):
        await message.reply("Please join our required channels first. Send /start for details.")
        return

    await message.reply(get_about_content(), parse_mode=ParseMode.HTML)


async def handle_invalid_command(message: types.Message):
    if message.text and message.text.startswith('/'):
        await message.reply("Unknown command. 🤔\n\nUse /help to see what I can do.")