"""
/about, /help, and unknown-command handler.

Provides rich, formatted, inline-keyboard-enhanced messages rather than plain text.
"""
import logging
from aiogram import types
from aiogram import Router
from aiogram.enums import ParseMode
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from middlewares.authorization import is_private_chat, is_user_member, invalidate_member_cache
from utils.database import db_fetchone
from utils.keyboard import InlineBuilder
from config import (
    REQUIRED_CHANNELS, PREMIUM_INFO_URL, ADMIN_CONTACT,
    VERIFY_URL, PAYMENT_MODE,
)

router = Router()
log = logging.getLogger(__name__)


# ── Text builders ─────────────────────────────────────────────────────────


def _premium_upgrade_line() -> str:
    if PAYMENT_MODE in ('stars', 'razorpay'):
        return (
            "💎 <b>Want faster downloads & exclusive content?</b>\n"
            "Use /pay to view Premium plans!"
        )
    return (
        f"💎 <b>Want faster downloads & exclusive content?</b>\n"
        f"Contact {ADMIN_CONTACT} to upgrade!"
    )


def get_help_content() -> str:
    """Formatted HTML help message."""
    upgrade_line = _premium_upgrade_line()

    return (
        "🏥 <b>How to Use the Medical Content Bot</b>\n\n"

        "╔══════════════════════════════╗\n"
        "║  📂  <b>Downloading Files</b>        ║\n"
        "╚══════════════════════════════╝\n\n"

        "📌 <b>Method 1 — Tap a folder button (easiest!)</b>\n"
        "Send /start → tap any 📁 folder → files arrive automatically.\n\n"

        "⌨️ <b>Method 2 — Type a command</b>\n"
        "<code>/download &lt;folder name&gt;</code>  — download a specific folder.\n\n"

        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

        "⏱️ <b>Cooldown & Speed</b>\n\n"
        "  👤 <b>Free</b>        — 60s between files · 7 min between downloads\n"
        "  ⭐ <b>Premium</b>  — 5s between files · 2 min between downloads\n\n"

        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

        "⚠️ <b>Important</b>\n"
        "Files are deleted from chat after a few minutes to respect copyright.\n"
        "💾 <b>Forward them to your Saved Messages immediately!</b>\n\n"

        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{upgrade_line}\n\n"
        "👤 Check your account status: /status"
    )


def get_about_content() -> str:
    """Formatted HTML about message."""
    upgrade_line = _premium_upgrade_line()

    return (
        "🏥 <b>Medical Content Bot</b>\n\n"
        "Your personal study companion on Telegram — organising and delivering "
        "medical content directly to verified students.\n\n"

        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

        "🔐 <b>Why verification?</b>\n"
        "Access is limited to verified medical students to protect content creators "
        "and avoid copyright issues. By verifying, you help keep this resource alive "
        "for everyone. 🙏\n\n"

        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{upgrade_line}\n\n"

        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🛟 <b>Support or questions?</b>\n"
        f"Reach us at: {ADMIN_CONTACT}\n\n"
        "Good luck with your studies! 📚✨"
    )


# ── Inline keyboard builders ──────────────────────────────────────────────


def _help_keyboard() -> InlineKeyboardMarkup:
    kb = InlineBuilder()
    if PAYMENT_MODE in ('stars', 'razorpay'):
        kb.add(InlineKeyboardButton("\U0001f48e Upgrade to Premium", callback_data="info_premium"))
    kb.add(
        InlineKeyboardButton("\U0001f4c2 Browse Folders", callback_data="back_to_main"),
        InlineKeyboardButton("\U0001f464 My Status",      callback_data="info_status"),
    )
    return kb.build()


def _about_keyboard() -> InlineKeyboardMarkup:
    kb = InlineBuilder()
    kb.add(
        InlineKeyboardButton("\U0001f4c2 Browse Folders",  callback_data="back_to_main"),
        InlineKeyboardButton("\u2753 How to Use",       callback_data="info_help"),
    )
    if VERIFY_URL:
        kb.add(InlineKeyboardButton("\u2705 Verification Info", url=VERIFY_URL))
    kb.add(InlineKeyboardButton(
        "\U0001f4ac Contact Admin",
        url=f"https://t.me/{ADMIN_CONTACT.lstrip('@')}"
    ))
    return kb.build()


# ── Guard helper ─────────────────────────────────────────────────────────


async def _check_access(message: types.Message) -> bool:
    """
    Returns True if user is approved and a member of required channels.
    Sends appropriate error message and returns False otherwise.
    """
    user_id = message.from_user.id
    row = db_fetchone('SELECT status FROM users WHERE user_id = %s', (user_id,))
    status = row[0] if row else None

    if not row or status != 'approved':
        if status == 'pending':
            await message.reply(
                "⏳ <b>Access pending</b>\n\n"
                "Your request is being reviewed. You'll be notified once approved.",
                parse_mode=ParseMode.HTML
            )
        else:
            await message.reply(
                "👋 Please send /start to register and request access.",
                parse_mode=ParseMode.HTML
            )
        return False

    invalidate_member_cache(user_id)
    if not await is_user_member(user_id):
        await message.reply(
            "📢 <b>Channel subscription required</b>\n\n"
            "You must join our required channel(s) to use this bot.\n"
            "Send /start to see the join buttons.",
            parse_mode=ParseMode.HTML
        )
        return False

    return True


# ── Handlers ─────────────────────────────────────────────────────────────


async def help_command(message: types.Message):
    """/help — formatted guide with inline navigation buttons."""
    if not is_private_chat(message):
        return
    if not await _check_access(message):
        return

    await message.reply(
        get_help_content(),
        parse_mode=ParseMode.HTML,
        reply_markup=_help_keyboard(),
        disable_web_page_preview=True,
    )


async def about_command(message: types.Message):
    """/about — bot info card with inline navigation buttons."""
    if not is_private_chat(message):
        return
    if not await _check_access(message):
        return

    await message.reply(
        get_about_content(),
        parse_mode=ParseMode.HTML,
        reply_markup=_about_keyboard(),
        disable_web_page_preview=True,
    )


async def handle_invalid_command(message: types.Message):
    """
    Catches any unrecognised /command and gives a helpful suggestion
    instead of silently ignoring the user.
    """
    if not (message.text and message.text.startswith('/')):
        return

    # Extract just the command name (strip args and the slash)
    cmd = message.text.split()[0].lstrip('/').split('@')[0]

    await message.reply(
        f"🤔 <b>Unknown command:</b> <code>/{cmd}</code>\n\n"
        "Here's what I can do:\n"
        "• /start — open the folder menu\n"
        "• /help — how to download files\n"
        "• /status — your account info\n"
        "• /commands — full command list\n\n"
        "<i>Can't find what you need? Contact the admin.</i>",
        parse_mode=ParseMode.HTML,
    )
    log.debug(f"Unknown command /{cmd} from user {message.from_user.id}")