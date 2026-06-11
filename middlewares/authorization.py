"""
middlewares/authorization.py — channel membership check helpers (aiogram v3).

Functions here are UTILITIES called from handlers, not middleware classes.
The actual rate-limit and logging middleware live in rate_limit.py.
"""
from utils.bot_ref import get_bot
import logging
from datetime import datetime, timedelta
from aiogram.types import Message
from config import REQUIRED_CHANNELS

log = logging.getLogger(__name__)

# ── is_private_chat ────────────────────────────────────────────────────────

def is_private_chat(message: Message) -> bool:
    return message.chat.type.value == 'private'


# ── is_user_member with 60-second TTL cache ───────────────────────────────
_CACHE_TTL    = timedelta(seconds=60)
_member_cache: dict = {}   # { user_id: (result: bool, cached_at: datetime) }
_channel_titles: dict = {} # { "@handle": "Display Name" }


async def is_user_member(user_id: int) -> bool:
    """Return True if the user is a member of every REQUIRED_CHANNELS entry."""
    if not REQUIRED_CHANNELS:
        return True

    bot = get_bot()
    now = datetime.now()

    cached = _member_cache.get(user_id)
    if cached:
        result, cached_at = cached
        if (now - cached_at) < _CACHE_TTL:
            return result

    result = True
    for channel in REQUIRED_CHANNELS:
        try:
            member = await bot.get_chat_member(channel, user_id)
            if member.status.value not in ('member', 'administrator', 'creator'):
                result = False
                break
        except Exception as e:
            log.warning(f"Membership check failed for {channel}: {e}")
            result = False
            break

    _member_cache[user_id] = (result, now)
    return result


def invalidate_member_cache(user_id: int) -> None:
    """Call this after a user joins a channel so the next check is fresh."""
    _member_cache.pop(user_id, None)


async def get_channel_title(channel: str) -> str:
    """Return a friendly display name for a channel handle/ID.
    Falls back to the raw handle if the API call fails.
    Titles are cached for the lifetime of the process.
    """
    if channel in _channel_titles:
        return _channel_titles[channel]
    try:
        bot   = get_bot()
        chat  = await bot.get_chat(channel)
        title = chat.title or chat.username or channel
    except Exception:
        title = channel
    _channel_titles[channel] = title
    return title