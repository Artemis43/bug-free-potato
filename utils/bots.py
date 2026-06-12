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
