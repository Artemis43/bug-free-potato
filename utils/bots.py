"""
utils/bots.py — bot registry & bot↔channel pairing for multi-bot retrieval.

Each bot runs as its own process (its own token) but they all share one
Supabase DB. A file uploaded through any bot is mirrored to every storage
channel (see handlers/document.py), so any bot can serve it — *provided that
bot is an admin in a channel holding the file*. Telegram file_ids are
bot-specific, so a bot cannot resend another bot's file_id; it must
``copy_message`` from a channel it administers.

This module tracks:
  - ``bots``         — one row per bot process (keyed by config.BOT_ID)
  - ``bot_channels`` — which storage channels each bot can retrieve from

``register_bot`` + ``sync_channel_membership`` are called on startup so the
pairing reflects reality; ``get_servable_locations`` is the per-file lookup
used by the download path.
"""
from __future__ import annotations

import logging

from config import BOT_ID
from utils.database import db_fetchall, db_execute, db_execute_returning

log = logging.getLogger(__name__)

# Cached primary-key of THIS process's bot row (set by register_bot at startup).
_current_bot_pk: int | None = None


def register_bot(name: str | None = None) -> int | None:
    """Upsert this process's bot row (by BOT_ID) and cache its pk."""
    global _current_bot_pk
    row = db_execute_returning(
        '''
        INSERT INTO bots (bot_key, name)
        VALUES (%s, %s)
        ON CONFLICT (bot_key) DO UPDATE
            SET name   = COALESCE(EXCLUDED.name, bots.name),
                active = TRUE
        RETURNING id
        ''',
        (BOT_ID, name)
    )
    _current_bot_pk = row[0] if row else None
    return _current_bot_pk


def get_current_bot_pk() -> int | None:
    """The cached pk for this process's bot (None if register_bot hasn't run)."""
    return _current_bot_pk


def pair_bot_channel(bot_pk: int, channel_id: int) -> None:
    db_execute(
        'INSERT INTO bot_channels (bot_id, channel_id) VALUES (%s, %s) '
        'ON CONFLICT DO NOTHING',
        (bot_pk, channel_id)
    )


def unpair_bot_channel(bot_pk: int, channel_id: int) -> None:
    db_execute(
        'DELETE FROM bot_channels WHERE bot_id = %s AND channel_id = %s',
        (bot_pk, channel_id)
    )


def get_bot_channel_ids(bot_pk: int) -> list[int]:
    rows = db_fetchall('SELECT channel_id FROM bot_channels WHERE bot_id = %s', (bot_pk,))
    return [r[0] for r in rows]


async def process_pending_replications(bot) -> int:
    """Copy files queued for this bot's channels from any accessible source.

    For each pending replication targeting a channel this bot is admin of,
    find a source channel this bot can already access (e.g. the shared common
    channel), then copy_message into the target. Records the new file_location
    and marks the task done.

    Returns the number of files successfully replicated this run.
    """
    from utils.storage import (
        get_pending_replications_for_bot, record_file_locations,
        mark_replication_done, mark_replication_failed,
    )

    bot_pk = get_current_bot_pk()
    if bot_pk is None:
        return 0

    pending = get_pending_replications_for_bot(bot_pk)
    if not pending:
        return 0

    done = 0
    for file_pk, target_channel_id, target_chat_id in pending:
        # Find a source: any channel this bot is already admin of that has the file.
        sources = get_servable_locations(file_pk, bot_pk)
        # Exclude the target (can't be a source — file isn't there yet — but guard anyway)
        sources = [(chat, msg) for chat, msg in sources if chat != target_chat_id]
        if not sources:
            # File not yet available in any channel this bot can access; skip for now.
            log.debug("No source available for file %d → channel %d; will retry.", file_pk, target_channel_id)
            continue

        src_chat, src_msg = sources[0]
        try:
            sent = await bot.copy_message(
                chat_id=target_chat_id,
                from_chat_id=src_chat,
                message_id=src_msg,
            )
            record_file_locations(file_pk, [(target_channel_id, sent.message_id)])
            mark_replication_done(file_pk, target_channel_id)
            done += 1
            log.info("Replicated file %d to channel %d (msg %d).", file_pk, target_channel_id, sent.message_id)
        except Exception as e:
            log.warning("Replication of file %d to channel %d failed: %s", file_pk, target_channel_id, e)
            mark_replication_failed(file_pk, target_channel_id)

    return done


def get_servable_locations(file_pk: int, bot_pk: int) -> list[tuple]:
    """Return ``[(chat_id, message_id), …]`` for every ACTIVE storage channel
    that (a) holds this file and (b) this bot is paired with — i.e. every place
    this bot could copy the file from, in channel-id order (fallback order)."""
    return db_fetchall(
        '''
        SELECT sc.chat_id, fl.message_id
        FROM file_locations fl
        JOIN storage_channels sc ON sc.id = fl.channel_id
        JOIN bot_channels bc     ON bc.channel_id = sc.id AND bc.bot_id = %s
        WHERE fl.file_pk = %s AND sc.active = TRUE
        ORDER BY sc.id
        ''',
        (bot_pk, file_pk)
    )
