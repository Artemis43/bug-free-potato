import logging
from datetime import datetime, timedelta
from aiogram import types
from config import REQUIRED_CHANNELS

# ── is_private_chat ───────────────────────────────────────────────────────────

def is_private_chat(message: types.Message) -> bool:
    return message.chat.type == 'private'


# ── is_user_member with 60-second TTL cache ───────────────────────────────────
# Caching avoids hammering the Telegram API with one get_chat_member call per
# handler per request (the original code did this in 5–6 places per /start).

_CACHE_TTL = timedelta(seconds=60)
_member_cache: dict = {}   # { user_id: (result: bool, cached_at: datetime) }

# Channel title cache (populated lazily)
_channel_titles: dict = {}  # { "@handle": "Display Name" }


async def is_user_member(user_id: int) -> bool:
    """Return True if the user is a member of every REQUIRED_CHANNELS entry."""
    from main import bot

    if not REQUIRED_CHANNELS:
        return True

    now = datetime.now()

    # Cache hit
    cached = _member_cache.get(user_id)
    if cached:
        result, cached_at = cached
        if (now - cached_at) < _CACHE_TTL:
            return result

    # Live check
    result = True
    for channel in REQUIRED_CHANNELS:
        try:
            member = await bot.get_chat_member(channel, user_id)
            if member.status not in ('member', 'administrator', 'creator'):
                result = False
                break
        except Exception as e:
            logging.warning(f"Membership check failed for {channel}: {e}")
            # On error, assume not a member to keep forced-sub enforcement safe
            result = False
            break

    _member_cache[user_id] = (result, now)
    return result


def invalidate_member_cache(user_id: int):
    """Call this after a user joins a channel so the next check is fresh."""
    _member_cache.pop(user_id, None)


async def get_channel_title(channel: str) -> str:
    """Return a friendly display name for a Telegram channel handle/ID.
    
    Falls back to the raw handle if the API call fails.
    Titles are cached for the lifetime of the process.
    """
    if channel in _channel_titles:
        return _channel_titles[channel]

    from main import bot
    try:
        chat = await bot.get_chat(channel)
        title = chat.title or chat.username or channel
    except Exception:
        title = channel

    _channel_titles[channel] = title
    return title