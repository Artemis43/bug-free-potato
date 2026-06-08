import logging
import asyncio
from datetime import datetime, timedelta
from aiogram import types, exceptions
from aiogram.types import ParseMode
from config import REQUIRED_CHANNELS, ADMIN_IDS
from middlewares.authorization import is_private_chat, is_user_member
from utils.database import db_fetchone, db_fetchall, db_execute


async def set_premium_status(message: types.Message):
    """Admin command: /setfolder <folder_id> <0|1>  — toggle folder premium flag."""
    if not is_private_chat(message):
        return

    if not await is_user_member(message.from_user.id):
        join_message = "Welcome to The Medical Content Bot ✨\n\nJoin our backup channels to remain connected ✊\n"
        for channel in REQUIRED_CHANNELS:
            join_message += f"{channel}\n"
        await message.reply(join_message)
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

        db_execute(
            'UPDATE folders SET premium = %s WHERE id = %s',
            (bool(premium_status), folder_id)
        )
        await message.reply(
            f"✅ Folder ID `{folder_id}` premium status set to `{premium_status}`.",
            parse_mode=ParseMode.MARKDOWN
        )
    except (IndexError, ValueError) as e:
        await message.reply(f"Usage: `/setfolder <folder_id> <0 or 1>`\n\nError: {e}", parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        await message.reply(f"Database error: {e}")


async def set_premium(message: types.Message):
    """Admin command: /setuser <user_id> <on|off|days:<N>>  — manage user premium."""
    from main import bot
    if not is_private_chat(message):
        return

    if str(message.from_user.id) not in ADMIN_IDS:
        await message.reply("You are not authorized to perform this action.")
        return

    args = message.get_args().split()

    if len(args) < 2:
        await message.reply(
            "Usage: `/setuser <user_id> <on|off|days:<N>>`\n"
            "Examples:\n"
            "  `/setuser 123456 on` — 10 days\n"
            "  `/setuser 123456 days:30` — custom duration\n"
            "  `/setuser 123456 off` — revoke premium",
            parse_mode=ParseMode.MARKDOWN
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
            await message.reply("Invalid days value. Use `days:<positive integer>`.", parse_mode=ParseMode.MARKDOWN)
            return
        action = 'on'
    elif action == 'off':
        days = 0
    else:
        await message.reply("Invalid action. Use `on`, `off`, or `days:<N>`.", parse_mode=ParseMode.MARKDOWN)
        return

    if action == 'on':
        expiration_date = datetime.now() + timedelta(days=days)
        db_execute(
            'UPDATE users SET premium = TRUE, premium_expiration = %s WHERE user_id = %s',
            (expiration_date, user_id)
        )
        await message.reply(
            f"✅ User `{user_id}` is now Premium for *{days} days* "
            f"(until {expiration_date.strftime('%Y-%m-%d %H:%M')}).",
            parse_mode=ParseMode.MARKDOWN
        )
        try:
            await bot.send_message(
                user_id,
                f"🎉 Congratulations! You have been upgraded to *Premium* for *{days} days*.",
                parse_mode=ParseMode.MARKDOWN
            )
        except exceptions.BotBlocked:
            await message.reply(f"Could not notify user {user_id} — they have blocked the bot.")

        asyncio.create_task(remove_premium_after_expiry(user_id, expiration_date))

    else:  # off
        db_execute(
            'UPDATE users SET premium = FALSE, premium_expiration = NULL WHERE user_id = %s',
            (user_id,)
        )
        await message.reply(f"✅ User `{user_id}` premium status revoked.", parse_mode=ParseMode.MARKDOWN)
        try:
            await bot.send_message(user_id, "Your Premium membership has been removed.")
        except exceptions.BotBlocked:
            pass


async def remove_premium_after_expiry(user_id: int, expiration_date: datetime):
    """Background task: auto-expire premium at the scheduled time."""
    from main import bot
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
    try:
        await bot.send_message(user_id, "⏰ Your Premium membership has expired.")
    except exceptions.BotBlocked:
        logging.warning(f"Could not notify user {user_id} about premium expiration — bot blocked.")
    except Exception as e:
        logging.error(f"Error notifying user {user_id} of premium expiry: {e}")