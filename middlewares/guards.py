"""
middlewares/guards.py — reusable auth decorators (aiogram v3 compatible).

All guards call the decorated function only if the check passes; otherwise
they silently return or send a rejection message.

Usage:
    @admin_only
    async def my_admin_handler(message: Message) -> None:
        ...
"""
import functools
import logging
from aiogram.types import Message
from config import ADMIN_IDS

log = logging.getLogger(__name__)


def admin_only(fn):
    """Only allow admins (from ADMIN_IDS) to run this handler."""
    @functools.wraps(fn)
    async def wrapper(message: Message, *args, **kwargs):
        if str(message.from_user.id) not in ADMIN_IDS:
            await message.reply("🔒 This command is restricted to administrators.")
            return
        return await fn(message, *args, **kwargs)
    return wrapper


def private_only(fn):
    """Only allow messages from private chats."""
    @functools.wraps(fn)
    async def wrapper(message: Message, *args, **kwargs):
        if message.chat.type.value != 'private':
            return
        return await fn(message, *args, **kwargs)
    return wrapper


def approved_only(fn):
    """Only allow users with status='approved' in the DB."""
    @functools.wraps(fn)
    async def wrapper(message: Message, *args, **kwargs):
        from utils.database import db_fetchone
        row = db_fetchone(
            "SELECT status FROM users WHERE user_id = %s",
            (message.from_user.id,)
        )
        if not row or row[0] != 'approved':
            await message.reply(
                "🔒 You don't have access yet.\n"
                "Use /start to check your approval status."
            )
            return
        return await fn(message, *args, **kwargs)
    return wrapper


def member_check(fn):
    """Reject users who haven't joined required channels."""
    @functools.wraps(fn)
    async def wrapper(message: Message, *args, **kwargs):
        from middlewares.authorization import is_user_member, invalidate_member_cache
        user_id = message.from_user.id
        invalidate_member_cache(user_id)
        if not await is_user_member(user_id):
            await message.reply(
                "📢 Please join our required channels first, then try again."
            )
            return
        return await fn(message, *args, **kwargs)
    return wrapper
