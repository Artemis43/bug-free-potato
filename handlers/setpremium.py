from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest
from aiogram import Router
from utils.bot_ref import get_bot
import logging
import asyncio
from datetime import datetime, timedelta
from aiogram import types
from aiogram import Router
from aiogram.enums import ParseMode
from aiogram import Router
from config import ADMIN_IDS, PREMIUM_INFO_URL, PAYMENT_MODE
from middlewares.authorization import is_private_chat
from utils.database import db_fetchone, db_execute
from utils.helpers import esc

router = Router()


async def set_premium_status(message: types.Message):
    """Admin: /setfolder <folder_id> <0|1> — toggle folder premium flag."""
    if not is_private_chat(message):
        return
    if str(message.from_user.id) not in ADMIN_IDS:
        await message.reply("You are not authorized.")
        return

    try:
        parts = message.text.split()
        if len(parts) != 3:
            raise ValueError("Wrong number of arguments.")
        folder_id      = int(parts[1])
        premium_status = int(parts[2])
        if premium_status not in (0, 1):
            raise ValueError("Premium status must be 0 or 1.")

        db_execute('UPDATE folders SET premium = %s WHERE id = %s', (bool(premium_status), folder_id))
        label = "⭐ Premium" if premium_status else "🔓 Free"
        await message.reply(
            f"✅ Folder ID <code>{folder_id}</code> is now <b>{label}</b>.",
            parse_mode=ParseMode.HTML
        )
    except (IndexError, ValueError) as e:
        await message.reply(
            f"Usage: <code>/setfolder &lt;folder_id&gt; &lt;0 or 1&gt;</code>\n\nError: {e}",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        await message.reply(f"Database error: {e}")


async def set_premium(message: types.Message):
    """Admin: /setuser <user_id> <on|off|days:N> — manage user premium."""
    if not is_private_chat(message):
        return
    if str(message.from_user.id) not in ADMIN_IDS:
        await message.reply("You are not authorized.")
        return

    args = (message.text.split(None, 1)[1].split() if message.text and len(message.text.split(None, 1)) > 1 else [])
    if len(args) < 2:
        await message.reply(
            "Usage: <code>/setuser &lt;user_id&gt; &lt;on|off|days:N&gt;</code>\n\n"
            "Examples:\n"
            "  <code>/setuser 123456 on</code> — 10 days (default)\n"
            "  <code>/setuser 123456 days:30</code> — custom duration\n"
            "  <code>/setuser 123456 off</code> — revoke premium",
            parse_mode=ParseMode.HTML
        )
        return

    try:
        user_id = int(args[0])
    except ValueError:
        await message.reply("Invalid user ID.")
        return

    action = args[1].lower()

    if action == 'on':
        days = 10
    elif action.startswith('days:'):
        try:
            days = int(action.split(':')[1])
            if days <= 0:
                raise ValueError()
        except ValueError:
            await message.reply(
                "Invalid days value. Use <code>days:&lt;positive integer&gt;</code>.",
                parse_mode=ParseMode.HTML
            )
            return
        action = 'on'
    elif action == 'off':
        days = 0
    else:
        await message.reply(
            "Invalid action. Use <code>on</code>, <code>off</code>, or <code>days:&lt;N&gt;</code>.",
            parse_mode=ParseMode.HTML
        )
        return

    bot = get_bot()
    if action == 'on':
        expiration_date = datetime.now() + timedelta(days=days)
        db_execute(
            'UPDATE users SET premium = TRUE, premium_expiration = %s WHERE user_id = %s',
            (expiration_date, user_id)
        )
        await message.reply(
            f"✅ User <code>{user_id}</code> is now <b>Premium</b> for <b>{days} days</b>\n"
            f"(until {expiration_date.strftime('%d %b %Y %H:%M')}).",
            parse_mode=ParseMode.HTML
        )
        try:
            await bot.send_message(
                user_id,
                f"🎉 <b>You're now a Premium member!</b>\n\n"
                f"Your premium lasts <b>{days} days</b> (until {expiration_date.strftime('%d %b %Y')}).\n\n"
                f"✨ You now get:\n"
                f"  • 5-second interval between files\n"
                f"  • 2-minute cooldown between downloads\n"
                f"  • Access to Premium-only folders\n\n"
                f"Use /start to explore!",
                parse_mode=ParseMode.HTML
            )
        except TelegramForbiddenError:
            await message.reply(f"Could not notify user {user_id} — they've blocked the bot.")

        asyncio.create_task(remove_premium_after_expiry(user_id, expiration_date))

    else:
        db_execute(
            'UPDATE users SET premium = FALSE, premium_expiration = NULL WHERE user_id = %s',
            (user_id,)
        )
        await message.reply(
            f"✅ User <code>{user_id}</code> premium revoked.", parse_mode=ParseMode.HTML
        )
        upgrade_text = 'Use /pay to upgrade again.' if PAYMENT_MODE in ('stars', 'razorpay') else f'<a href="{PREMIUM_INFO_URL}">Upgrade again →</a>'
        try:
            await bot.send_message(
                user_id,
                f"Your Premium membership has ended.\n\n"
                f"You can still use the bot as a free user.\n"
                f"{upgrade_text}",
                parse_mode=ParseMode.HTML
            )
        except TelegramForbiddenError:
            pass


async def remove_premium_after_expiry(user_id: int, expiration_date: datetime):
    """Background task: auto-expire premium at the scheduled time."""
    bot = get_bot()
    sleep_time = max((expiration_date - datetime.now()).total_seconds(), 0)
    await asyncio.sleep(sleep_time)

    db_execute(
        '''
        UPDATE users
        SET premium = FALSE, premium_expiration = NULL
        WHERE user_id = %s AND premium_expiration <= %s
        ''',
        (user_id, datetime.now())
    )
    renew_text = 'Use /pay to renew Premium.' if PAYMENT_MODE in ('stars', 'razorpay') else f'<a href="{PREMIUM_INFO_URL}">Renew Premium →</a>'
    try:
        await bot.send_message(
            user_id,
            f"⏰ <b>Your Premium has expired.</b>\n\n"
            f"You can still use the bot as a free user.\n"
            f"{renew_text}",
            parse_mode=ParseMode.HTML
        )
    except TelegramForbiddenError:
        logging.warning(f"Could not notify user {user_id} about premium expiry — bot blocked.")
    except Exception as e:
        logging.error(f"Error notifying user {user_id} of premium expiry: {e}")