"""
/status handler — rich account overview with cooldown countdown and premium info.
Exports build_status_text() so the inline info_status callback can reuse it.
"""
from datetime import datetime, timedelta
from typing import Optional, Tuple
from aiogram import types, Router
from aiogram.enums import ParseMode
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from utils.keyboard import InlineBuilder
from middlewares.authorization import is_private_chat
from config import ADMIN_CONTACT, PAYMENT_MODE
from utils.database import db_fetchone
from utils.helpers import esc

router = Router()


async def build_status_text(user_id: int, bot=None) -> Tuple[str, Optional[InlineKeyboardMarkup]]:
    """
    Build the status card text and keyboard for a given user_id.
    Returns (text, keyboard) so it can be reused from both the /status command
    and the info_status inline callback without code duplication.
    """
    row = db_fetchone(
        'SELECT status, premium, premium_expiration, last_download, first_name, username '
        'FROM users WHERE user_id = %s',
        (user_id,)
    )

    if not row:
        return (
            "👋 You haven't started the bot yet.\nSend /start to register.",
            None
        )

    user_status, is_premium, premium_expiration, last_download, first_name, username = row

    display_name = esc(first_name or f"User {user_id}")
    uname_str    = f"@{esc(username)}" if username else "no username"

    lines = [
        "👤 <b>Account Status</b>",
        "<b>──────────────────────────────────</b>",
        "",
        f"<b>Name:</b>     {display_name}",
        f"<b>Username:</b> {uname_str}",
        f"<b>User ID:</b>  <code>{user_id}</code>",
        "",
    ]

    status_map = {
        'approved': '✅ Approved',
        'pending':  '⏳ Pending approval',
        'rejected': '❌ Rejected',
        'banned':   '🚫 Banned',
    }
    lines.append(f"<b>Access:</b>   {status_map.get(user_status, user_status)}")

    if user_status != 'approved':
        if user_status == 'pending':
            lines.append("\n📋 You'll be notified once the admin reviews your request.")
        elif user_status == 'rejected':
            lines.append(f"\n💬 To appeal, contact: {ADMIN_CONTACT}")
        elif user_status == 'banned':
            lines.append(f"\n🚫 You have violated the rules and hence are now banned. 🚫\n💬 To appeal, contact: {ADMIN_CONTACT}")
        return ('\n'.join(lines), None)

    # ── Premium status ────────────────────────────────────────────────────
    if is_premium and premium_expiration:
        exp = premium_expiration
        if hasattr(exp, 'tzinfo') and exp.tzinfo is not None:
            exp = exp.replace(tzinfo=None)
        days_left = (exp - datetime.now()).days
        exp_str   = exp.strftime('%d %b %Y')
        if days_left > 1:
            premium_line = f"⭐ <b>Premium</b> — expires {exp_str} (<b>{days_left} days left</b>)"
        elif days_left == 1:
            premium_line = f"⭐ <b>Premium</b> — expires <b>tomorrow</b> ({exp_str})"
        elif days_left == 0:
            premium_line = f"⭐ <b>Premium</b> — expires <b>today!</b> 🚨"
        else:
            premium_line = f"👤 <b>Free</b> — premium expired on {exp_str}"
    elif is_premium:
        premium_line = "⭐ <b>Premium</b> (lifetime access)"
    else:
        if PAYMENT_MODE in ('stars', 'razorpay'):
            premium_line = "👤 <b>Free Tier</b>  ·  use /pay to upgrade 💎"
        else:
            premium_line = f"👤 <b>Free Tier</b>  ·  contact {ADMIN_CONTACT} to upgrade 💎"

    lines.append(f"<b>Plan:</b>     {premium_line}")
    lines.append("")
    lines.append("<b>──────────────────────────────────</b>")

    # ── Cooldown ──────────────────────────────────────────────────────────
    cooldown_mins = 2 if is_premium else 7
    file_delay    = 5 if is_premium else 60

    if last_download:
        ld = last_download
        if hasattr(ld, 'tzinfo') and ld.tzinfo is not None:
            ld = ld.replace(tzinfo=None)
        cooldown  = timedelta(minutes=cooldown_mins)
        remaining = cooldown - (datetime.now() - ld)
        if remaining.total_seconds() > 0:
            total_secs = int(remaining.total_seconds())
            mins, secs = divmod(total_secs, 60)
            lines.append(f"⏳ <b>Cooldown:</b> {mins}m {secs}s until next download")
            if not is_premium:
                lines.append("  <i>💎 Premium users only wait 2 minutes!</i>")
        else:
            last_str = ld.strftime('%b %d at %I:%M %p')
            lines.append(f"✅ <b>Cooldown:</b> Ready! (last download: {last_str})")
    else:
        lines.append("✅ <b>Cooldown:</b> Ready — no downloads yet!")

    # ── Speed summary ─────────────────────────────────────────────────────
    lines.append("")
    tier = "⭐ Premium" if is_premium else "👤 Free"
    lines.append(
        f"⚡ <b>Your speed ({tier}):</b>\n"
        f"  • {file_delay}s between files\n"
        f"  • {cooldown_mins} min between downloads"
    )

    # ── Keyboard ──────────────────────────────────────────────────────────
    kb = InlineBuilder()
    kb.add(
        InlineKeyboardButton(text="\U0001f4c2 Browse Folders", callback_data="back_to_main"),
        InlineKeyboardButton(text="\u2753 Help Guide",         callback_data="info_help"),
    )
    if not is_premium and PAYMENT_MODE in ('stars', 'razorpay'):
        kb.add(InlineKeyboardButton(text="\U0001f48e Upgrade to Premium", callback_data="info_premium"))

    return ('\n'.join(lines), kb)


async def status(message: types.Message):
    """/status — rich card showing account status, premium tier, and cooldown."""
    if not is_private_chat(message):
        return

    user_id = message.from_user.id
    text, kb = await build_status_text(user_id)

    await message.reply(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=kb.build() if kb is not None else None,
        disable_web_page_preview=True,
    )
