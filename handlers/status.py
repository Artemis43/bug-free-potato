from datetime import datetime, timedelta
from aiogram import types
from aiogram.types import ParseMode
from middlewares.authorization import is_private_chat, is_user_member
from config import PREMIUM_INFO_URL, ADMIN_CONTACT, PAYMENT_MODE
from utils.database import db_fetchone
from utils.helpers import esc


async def status(message: types.Message):
    """/status — show the user's own account status, premium info, and cooldown."""
    if not is_private_chat(message):
        return

    user_id = message.from_user.id

    row = db_fetchone(
        'SELECT status, premium, premium_expiration, last_download, first_name, username '
        'FROM users WHERE user_id = %s',
        (user_id,)
    )

    if not row:
        await message.reply("You haven't started the bot yet. Send /start first.")
        return

    user_status, is_premium, premium_expiration, last_download, first_name, username = row

    display_name = esc(first_name or message.from_user.first_name or f"User {user_id}")
    uname_str    = f"(@{esc(username)})" if username else ""

    lines = [
        "<b>📊 Account Status</b>\n",
        f"👤 {display_name} {uname_str}".strip(),
        f"🆔 <code>{user_id}</code>\n",
    ]

    status_map = {
        'approved': '✅ Approved',
        'pending':  '⏳ Pending approval',
        'rejected': '❌ Rejected',
    }
    lines.append(f"Access: {status_map.get(user_status, user_status)}")

    if user_status != 'approved':
        if user_status == 'pending':
            lines.append("\nYou'll be notified here once the admin reviews your request.")
        elif user_status == 'rejected':
            lines.append(f"\nContact us: {ADMIN_CONTACT}")
        await message.reply('\n'.join(lines), parse_mode=ParseMode.HTML)
        return

    # Premium status
    if is_premium and premium_expiration:
        exp = premium_expiration
        if hasattr(exp, 'tzinfo') and exp.tzinfo is not None:
            exp = exp.replace(tzinfo=None)
        days_left = (exp - datetime.now()).days
        exp_str   = exp.strftime('%d %b %Y')
        if days_left > 0:
            premium_line = f"⭐ Premium — expires {exp_str} ({days_left} day(s) left)"
        elif days_left == 0:
            premium_line = f"⭐ Premium — expires <b>today!</b>"
        else:
            premium_line = f"🔓 Free — your premium expired on {exp_str}"
    elif is_premium:
        premium_line = "⭐ Premium (no expiry set)"
    else:
        if PAYMENT_MODE in ('stars', 'razorpay'):
            premium_line = '🔓 Free User  •  /pay'
        else:
            premium_line = f'🔓 Free User  •  <a href="{PREMIUM_INFO_URL}">🌟 Upgrade</a>'

    lines.append(premium_line)
    lines.append("")

    # Cooldown
    if last_download:
        ld = last_download
        if hasattr(ld, 'tzinfo') and ld.tzinfo is not None:
            ld = ld.replace(tzinfo=None)
        cooldown  = timedelta(minutes=2) if is_premium else timedelta(minutes=7)
        remaining = cooldown - (datetime.now() - ld)
        if remaining.total_seconds() > 0:
            total_secs = int(remaining.total_seconds())
            mins, secs = divmod(total_secs, 60)
            cooldown_line = f"⏳ Cooldown: {mins}m {secs}s remaining"
        else:
            last_str      = ld.strftime('%b %d at %I:%M %p')
            cooldown_line = f"⬇️ Last download: {last_str}\n✅ Ready for next download"
    else:
        cooldown_line = "✅ No downloads yet — ready to go!"

    lines.append(cooldown_line)
    lines.append("\nUse /help to see how to download folders.")

    # Show active payment mode so users know how to purchase
    _mode_labels = {
        'stars':    '⭐ Telegram Stars',
        'razorpay': '💳 Razorpay (INR)',
        'manual':   '📬 Manual (contact admin)',
    }
    lines.append(f"\nPayment mode: {_mode_labels.get(PAYMENT_MODE, PAYMENT_MODE)}")

    await message.reply('\n'.join(lines), parse_mode=ParseMode.HTML)
