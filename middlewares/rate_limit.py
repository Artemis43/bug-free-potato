"""
Rate limiting middleware — aiogram v3 compatible.

Silently drops commands that arrive faster than the configured threshold
per user. Prevents spam & abuse.
"""
import time
import logging
from collections import defaultdict
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery, TelegramObject
from aiogram.enums import ParseMode

from config import ADMIN_IDS

log = logging.getLogger(__name__)

# Seconds required between any two messages from the same user.
_RATE_LIMIT_SECONDS: float = 1.5
# How long (seconds) to silence a user who keeps spamming after being warned.
_SPAM_MUTE_SECONDS: float = 10.0

_last_action: dict = defaultdict(float)
_muted_until: dict = defaultdict(float)
_violations:  dict = defaultdict(int)


class RateLimitMiddleware(BaseMiddleware):
    """Drop messages that exceed per-user rate limits (v3 BaseMiddleware)."""

    async def __call__(
        self,
        handler: Callable[[Message, Dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: Dict[str, Any],
    ) -> Any:
        if not isinstance(event, Message):
            return await handler(event, data)

        user = event.from_user
        if not user:
            return await handler(event, data)

        user_id = user.id

        # ── Admin exemption ───────────────────────────────────────────────
        # Admins are trusted and are the only users who upload files. Never
        # throttle them — otherwise a bulk/album upload (items arrive in
        # <200ms bursts) would have most of its files silently dropped.
        if str(user_id) in ADMIN_IDS:
            return await handler(event, data)

        now     = time.monotonic()

        # ── Mute check ────────────────────────────────────────────────────
        if now < _muted_until.get(user_id, 0):
            log.debug(f"[RateLimit] User {user_id} is muted.")
            return  # silently drop

        # ── Rate check ────────────────────────────────────────────────────
        last = _last_action.get(user_id, 0)
        if (now - last) < _RATE_LIMIT_SECONDS:
            _violations[user_id] += 1
            if _violations[user_id] >= 5:
                _muted_until[user_id] = now + _SPAM_MUTE_SECONDS
                _violations[user_id]  = 0
                log.warning(f"[RateLimit] Muted user {user_id} for {_SPAM_MUTE_SECONDS}s")
                try:
                    await event.reply(
                        "⚠️ <b>Slow down!</b>\n\n"
                        "You've sent too many messages in a row.\n"
                        f"Please wait <b>{int(_SPAM_MUTE_SECONDS)} seconds</b> before trying again.",
                        parse_mode=ParseMode.HTML,
                    )
                except Exception:
                    pass
            return  # drop
        else:
            _violations[user_id] = 0

        _last_action[user_id] = now
        return await handler(event, data)


class CallbackRateLimitMiddleware(BaseMiddleware):
    """Light rate limiter for callback queries (button taps feel snappy)."""

    async def __call__(
        self,
        handler: Callable[[CallbackQuery, Dict[str, Any]], Awaitable[Any]],
        event: CallbackQuery,
        data: Dict[str, Any],
    ) -> Any:
        if not isinstance(event, CallbackQuery):
            return await handler(event, data)

        user_id = event.from_user.id

        # Admins are exempt (mirrors RateLimitMiddleware above).
        if str(user_id) in ADMIN_IDS:
            return await handler(event, data)

        now     = time.monotonic()

        if now < _muted_until.get(user_id, 0):
            try:
                await event.answer("⚠️ Too many requests. Please wait a moment.", show_alert=False)
            except Exception:
                pass
            return  # drop

        return await handler(event, data)
