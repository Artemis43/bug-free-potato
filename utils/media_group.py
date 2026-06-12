"""
utils/media_group.py — Telegram album (media-group) aggregation.

Telegram delivers an "album" (several files sent together) as SEPARATE
update messages that all share the same ``media_group_id``. aiogram has no
built-in batching, so each item arrives as its own handler call.

This helper buffers the items of a media group for a short, sliding window and
then invokes a single ``process`` callback with the whole list — so an album of
N files produces ONE batch (and one summary reply) instead of N independent
uploads. Messages with no ``media_group_id`` (a single file) are passed
straight through as a batch of one.

Usage (from a handler):

    from utils.media_group import collect_media_group

    async def _process_batch(messages: list[Message]):
        ...  # store every message, send one summary

    await collect_media_group(message, _process_batch)
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Awaitable, Callable

from aiogram.types import Message

log = logging.getLogger(__name__)

# Quiet period (seconds) to wait after the FIRST item of a group before
# flushing. Album items arrive within ~200ms of each other, so a window of a
# second comfortably captures the whole album while staying responsive.
_WINDOW_SECONDS: float = 1.0

_buffers: dict[str, list[Message]] = defaultdict(list)

ProcessFn = Callable[[list[Message]], Awaitable[None]]


async def collect_media_group(
    message: Message,
    process: ProcessFn,
    *,
    window: float = _WINDOW_SECONDS,
) -> None:
    """Buffer album items and flush them to ``process`` as one batch.

    - No ``media_group_id`` (a lone file): ``process`` is awaited immediately
      with ``[message]``.
    - Part of an album: the message is buffered; the first item of the group
      schedules a background flush after ``window`` seconds that calls
      ``process`` once with every item collected in that window.
    """
    mgid = message.media_group_id
    if not mgid:
        await process([message])
        return

    first_of_group = mgid not in _buffers
    _buffers[mgid].append(message)

    if first_of_group:
        asyncio.create_task(_flush_after(mgid, window, process))


async def _flush_after(mgid: str, window: float, process: ProcessFn) -> None:
    """Wait ``window`` seconds, then process and clear the buffered group."""
    await asyncio.sleep(window)
    messages = _buffers.pop(mgid, [])
    if not messages:
        return
    try:
        await process(messages)
    except Exception:
        # This runs in a detached task; log instead of leaking an
        # "Task exception was never retrieved" warning.
        log.exception("media-group batch processing failed (group=%s)", mgid)
