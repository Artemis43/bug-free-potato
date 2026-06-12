"""
utils/storage.py — storage-channel registry & per-file replica tracking.

The bot mirrors every uploaded file into a configurable set of "storage
channels" (DB table ``storage_channels``) for redundancy. Each physical copy is
recorded in ``file_locations`` (one row per file × channel). This module is the
single place that reads/writes those two tables.

Design notes:
  - ``chat_id`` is stored as TEXT (a numeric -100… id or an @username), exactly
    as it is passed to aiogram's ``send_*`` / ``copy_message`` / ``delete_message``.
  - Removing or disabling a channel never touches ``files`` — only its
    ``file_locations`` rows (via ON DELETE CASCADE / the ``active`` flag), which
    is what isolates a copyright takedown to a single channel.
"""
from __future__ import annotations

from utils.database import db_fetchall, db_execute, db_execute_returning


# ── Channel registry ────────────────────────────────────────────────────────

def list_storage_channels(active_only: bool = False) -> list[tuple]:
    """Return ``[(id, chat_id, title, active), …]`` ordered by id."""
    query = 'SELECT id, chat_id, title, active FROM storage_channels'
    if active_only:
        query += ' WHERE active = TRUE'
    query += ' ORDER BY id'
    return db_fetchall(query)


def add_storage_channel(chat_id, title: str | None = None) -> int | None:
    """Insert a storage channel (idempotent on chat_id). Returns its row id."""
    row = db_execute_returning(
        '''
        INSERT INTO storage_channels (chat_id, title)
        VALUES (%s, %s)
        ON CONFLICT (chat_id) DO UPDATE
            SET title  = COALESCE(EXCLUDED.title, storage_channels.title),
                active = TRUE
        RETURNING id
        ''',
        (str(chat_id), title)
    )
    return row[0] if row else None


def remove_storage_channel(channel_id: int) -> None:
    """Delete a channel and (via CASCADE) all its file_locations rows."""
    db_execute('DELETE FROM storage_channels WHERE id = %s', (channel_id,))


def set_channel_active(channel_id: int, active: bool) -> None:
    db_execute('UPDATE storage_channels SET active = %s WHERE id = %s', (active, channel_id))


def get_channel(channel_id: int):
    """Return ``(id, chat_id, title, active)`` for one channel, or None."""
    rows = db_fetchall(
        'SELECT id, chat_id, title, active FROM storage_channels WHERE id = %s',
        (channel_id,)
    )
    return rows[0] if rows else None


def channel_replica_count(channel_id: int) -> int:
    """How many file copies currently live in this channel."""
    rows = db_fetchall(
        'SELECT COUNT(*) FROM file_locations WHERE channel_id = %s', (channel_id,)
    )
    return rows[0][0] if rows else 0


# ── Per-file replicas ───────────────────────────────────────────────────────

def record_file_locations(file_pk: int, locations) -> None:
    """Persist replicas for a file. ``locations`` is an iterable of
    ``(channel_id, message_id)`` tuples."""
    for channel_id, message_id in locations:
        db_execute(
            '''
            INSERT INTO file_locations (file_pk, channel_id, message_id)
            VALUES (%s, %s, %s)
            ON CONFLICT (file_pk, channel_id) DO UPDATE
                SET message_id = EXCLUDED.message_id
            ''',
            (file_pk, channel_id, message_id)
        )


def get_file_locations(file_pk: int, active_only: bool = True) -> list[tuple]:
    """Return ``[(channel_id, chat_id, message_id), …]`` for a file, joined to
    storage_channels. ``active_only`` keeps only enabled channels (for
    retrieval); pass False to reach disabled channels too (for cleanup)."""
    query = '''
        SELECT fl.channel_id, sc.chat_id, fl.message_id
        FROM file_locations fl
        JOIN storage_channels sc ON sc.id = fl.channel_id
        WHERE fl.file_pk = %s
    '''
    if active_only:
        query += ' AND sc.active = TRUE'
    query += ' ORDER BY sc.id'
    return db_fetchall(query, (file_pk,))
