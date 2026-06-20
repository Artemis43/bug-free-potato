import logging
import psycopg2
from psycopg2 import pool as _pg_pool
from config import POSTGRES_CONNECTION_STRING, DEFAULT_CAPTION, CHANNEL_ID, BOT_ID

_pool: _pg_pool.ThreadedConnectionPool | None = None


def _init_pool() -> None:
    global _pool
    _pool = _pg_pool.ThreadedConnectionPool(
        minconn=2, maxconn=10,
        dsn=POSTGRES_CONNECTION_STRING,
        connect_timeout=10,
    )
    logging.info("Connection pool initialised (min=2 max=10).")


def get_connection():
    """Return a connection from the pool (or a direct connection before pool init)."""
    if _pool is not None:
        return _pool.getconn()
    return psycopg2.connect(POSTGRES_CONNECTION_STRING, connect_timeout=10)


def _release(conn) -> None:
    """Return connection to pool, or close it if pool not yet active."""
    if _pool is not None:
        _pool.putconn(conn)
    else:
        conn.close()


def initialize_database():
    """Create tables and add any missing columns (safe to run on every startup)."""
    conn = None
    try:
        conn = get_connection()
        cur = conn.cursor()

        # ── Users ─────────────────────────────────────────────────────────────
        cur.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id               BIGINT PRIMARY KEY,
                username              TEXT,
                first_name            TEXT,
                status                TEXT        DEFAULT 'pending',
                premium               BOOLEAN     DEFAULT FALSE,
                premium_expiration    TIMESTAMPTZ,
                last_download         TIMESTAMPTZ,
                welcome_sent          BOOLEAN     DEFAULT FALSE,
                last_notified         TIMESTAMPTZ,
                current_upload_folder TEXT,
                created_at            TIMESTAMPTZ DEFAULT NOW()
            )
        ''')

        # ── Categories ────────────────────────────────────────────────────────
        cur.execute('''
            CREATE TABLE IF NOT EXISTS categories (
                id         SERIAL PRIMARY KEY,
                name       TEXT    NOT NULL UNIQUE,
                emoji      TEXT    DEFAULT '📁',
                sort_order INTEGER DEFAULT 0,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        ''')

        # ── Folders ───────────────────────────────────────────────────────────
        cur.execute('''
            CREATE TABLE IF NOT EXISTS folders (
                id             SERIAL PRIMARY KEY,
                name           TEXT    NOT NULL UNIQUE,
                parent_id      INTEGER REFERENCES folders(id),
                premium        BOOLEAN DEFAULT FALSE,
                admin_approval BOOLEAN DEFAULT FALSE,
                download_count INTEGER DEFAULT 0
            )
        ''')

        # ── Files ─────────────────────────────────────────────────────────────
        cur.execute('''
            CREATE TABLE IF NOT EXISTS files (
                id         SERIAL PRIMARY KEY,
                folder_id  INTEGER NOT NULL REFERENCES folders(id) ON DELETE CASCADE,
                file_id    TEXT    NOT NULL,
                file_name  TEXT,
                message_id INTEGER,
                caption    TEXT,
                file_type  TEXT    DEFAULT 'document'
            )
        ''')

        # ── Storage channels (multi-channel backup) ───────────────────────────
        # Admin-configurable set of channels every upload is mirrored to. Stored
        # in the DB (not env) so channels can be added/removed at runtime.
        # chat_id is TEXT to accept either a numeric -100… id or an @username,
        # exactly as the value is passed to aiogram's send_*/copy_message.
        cur.execute('''
            CREATE TABLE IF NOT EXISTS storage_channels (
                id        SERIAL PRIMARY KEY,
                chat_id   TEXT        NOT NULL UNIQUE,
                title     TEXT,
                active    BOOLEAN     DEFAULT TRUE,
                added_at  TIMESTAMPTZ DEFAULT NOW()
            )
        ''')

        # ── File replicas: one row per (file, channel) physical copy ──────────
        # Decoupling the logical `files` record from its physical copies is what
        # isolates a copyright strike: dropping/disabling one channel removes
        # only its file_locations rows, never the file or the other channels.
        cur.execute('''
            CREATE TABLE IF NOT EXISTS file_locations (
                id         SERIAL  PRIMARY KEY,
                file_pk    INTEGER NOT NULL REFERENCES files(id)            ON DELETE CASCADE,
                channel_id INTEGER NOT NULL REFERENCES storage_channels(id) ON DELETE CASCADE,
                message_id INTEGER NOT NULL,
                UNIQUE (file_pk, channel_id)
            )
        ''')

        # ── Bots & bot↔channel pairing (multi-bot retrieval) ──────────────────
        # Each bot process (identified by BOT_ID) registers in `bots`.
        # `bot_channels` records which storage channels a bot can retrieve from
        # (it must be an admin there). Retrieval copies a file from one of the
        # serving bot's paired channels — Telegram file_ids are bot-specific, so
        # cross-bot sharing must go through copy_message from a shared channel.
        cur.execute('''
            CREATE TABLE IF NOT EXISTS bots (
                id      SERIAL  PRIMARY KEY,
                bot_key TEXT    NOT NULL UNIQUE,
                name    TEXT,
                active  BOOLEAN DEFAULT TRUE
            )
        ''')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS bot_channels (
                bot_id     INTEGER NOT NULL REFERENCES bots(id)             ON DELETE CASCADE,
                channel_id INTEGER NOT NULL REFERENCES storage_channels(id) ON DELETE CASCADE,
                PRIMARY KEY (bot_id, channel_id)
            )
        ''')

        # ── Caption config ────────────────────────────────────────────────────
        cur.execute('''
            CREATE TABLE IF NOT EXISTS current_caption (
                id           SERIAL PRIMARY KEY,
                caption_type TEXT DEFAULT 'custom',
                custom_text  TEXT DEFAULT ''
            )
        ''')

        # ── Paid-folder approvals ─────────────────────────────────────────────
        cur.execute('''
            CREATE TABLE IF NOT EXISTS user_folder_approval (
                user_id           BIGINT  NOT NULL,
                folder_id         INTEGER NOT NULL,
                approved          BOOLEAN DEFAULT FALSE,
                download_completed BOOLEAN DEFAULT FALSE,
                PRIMARY KEY (user_id, folder_id)
            )
        ''')

        # ── Pending broadcasts (admin confirmation queue) ──────────────────────────────
        cur.execute('''
            CREATE TABLE IF NOT EXISTS pending_broadcasts (
                id           SERIAL PRIMARY KEY,
                admin_id     BIGINT       NOT NULL,
                message_text TEXT         NOT NULL,
                parse_mode   TEXT         DEFAULT NULL,
                created_at   TIMESTAMPTZ  DEFAULT NOW()
            )
        ''')

        # ── Payment: configurable plans ────────────────────────────────────────────
        cur.execute('''
            CREATE TABLE IF NOT EXISTS payment_plans (
                id           SERIAL PRIMARY KEY,
                name         TEXT    NOT NULL UNIQUE,
                amount_paise INTEGER NOT NULL,
                days         INTEGER NOT NULL,
                active       BOOLEAN DEFAULT TRUE
            )
        ''')

        # ── Payment: order ledger ─────────────────────────────────────────────────────
        cur.execute('''
            CREATE TABLE IF NOT EXISTS payment_orders (
                id                   SERIAL PRIMARY KEY,
                razorpay_link_id     TEXT    NOT NULL UNIQUE,
                razorpay_payment_id  TEXT,
                user_id              BIGINT  NOT NULL,
                order_type           TEXT    NOT NULL,  -- 'premium' | 'folder'
                ref_id               INTEGER,           -- plan_id or folder_id
                amount_paise         INTEGER NOT NULL,
                status               TEXT    DEFAULT 'created',  -- created | paid | failed
                created_at           TIMESTAMPTZ DEFAULT NOW(),
                paid_at              TIMESTAMPTZ
            )
        ''')

        # ── Payment: per-folder price overrides ──────────────────────────────────────
        cur.execute('''
            CREATE TABLE IF NOT EXISTS payment_folder_prices (
                folder_id    INTEGER PRIMARY KEY REFERENCES folders(id) ON DELETE CASCADE,
                amount_paise INTEGER NOT NULL
            )
        ''')

        # ── Payment: key-value config store ──────────────────────────────────────────
        cur.execute('''
            CREATE TABLE IF NOT EXISTS payment_config (
                key        TEXT PRIMARY KEY,
                value_int  INTEGER,
                value_text TEXT
            )
        ''')
        # Seed default paid-folder price if absent
        cur.execute("SELECT 1 FROM payment_config WHERE key = 'default_folder_price_paise'")
        if not cur.fetchone():
            cur.execute(
                "INSERT INTO payment_config (key, value_int) VALUES ('default_folder_price_paise', 9900)"
            )
            logging.info("Seeded default paid-folder price: ₹99.")

        # ── Stars payment: plans ──────────────────────────────────────────────
        cur.execute('''
            CREATE TABLE IF NOT EXISTS stars_payment_plans (
                id            SERIAL PRIMARY KEY,
                name          TEXT    NOT NULL UNIQUE,
                amount_stars  INTEGER NOT NULL,
                days          INTEGER NOT NULL,
                active        BOOLEAN DEFAULT TRUE
            )
        ''')

        # ── Stars payment: per-folder price overrides ─────────────────────────
        cur.execute('''
            CREATE TABLE IF NOT EXISTS stars_folder_prices (
                folder_id     INTEGER PRIMARY KEY REFERENCES folders(id) ON DELETE CASCADE,
                amount_stars  INTEGER NOT NULL
            )
        ''')

        # Seed default Stars folder price if absent
        cur.execute("SELECT 1 FROM payment_config WHERE key = 'default_folder_price_stars'")
        if not cur.fetchone():
            cur.execute(
                "INSERT INTO payment_config (key, value_int) VALUES ('default_folder_price_stars', 50)"
            )
            logging.info("Seeded default Stars folder price: 50 Stars.")

        # Seed a default caption row if the table is empty.
        # This ensures caption logic is always DB-driven — table is never empty.
        cur.execute('SELECT COUNT(*) FROM current_caption')
        if cur.fetchone()[0] == 0:
            cur.execute(
                "INSERT INTO current_caption (caption_type, custom_text) VALUES ('custom', %s)",
                (DEFAULT_CAPTION,)
            )
            logging.info("Seeded default caption from DEFAULT_CAPTION config.")

        # ── Migrations: add columns that didn't exist in old schema ───────────
        _safe_alter(cur, 'users', 'username',               'TEXT')
        _safe_alter(cur, 'users', 'first_name',              'TEXT')
        _safe_alter(cur, 'users', 'last_notified',           'TIMESTAMPTZ')
        _safe_alter(cur, 'users', 'current_upload_folder',   'TEXT')
        _safe_alter(cur, 'files', 'message_id',              'INTEGER')
        _safe_alter(cur, 'files', 'file_type',               "TEXT DEFAULT 'document'")
        _safe_alter(cur, 'payment_orders', 'payment_method', "TEXT DEFAULT 'razorpay'")
        # Add created_at nullable so pre-existing users keep NULL (their true
        # signup time is unknown) rather than all being backfilled to the
        # migration timestamp — which would make /stats report every user as
        # "new today". New rows still default to NOW(). The /stats counts use
        # `created_at >= …`, which correctly excludes the NULL legacy rows.
        _safe_alter(cur, 'users', 'created_at',              "TIMESTAMPTZ")
        cur.execute("ALTER TABLE public.users ALTER COLUMN created_at SET DEFAULT NOW()")

        # ── Migration: replace TEXT upload-folder name with integer FK ────────
        _safe_alter(cur, 'users', 'current_upload_folder_id',
                    'INTEGER REFERENCES folders(id) ON DELETE SET NULL')
        cur.execute("""
            UPDATE users u
            SET current_upload_folder_id = f.id
            FROM folders f
            WHERE f.name = u.current_upload_folder
              AND u.current_upload_folder IS NOT NULL
              AND u.current_upload_folder_id IS NULL
        """)

        # ── Migration: seed multi-channel storage from the legacy CHANNEL ─────
        # Treat the existing single CHANNEL as storage channel #1 and backfill
        # file_locations from files.message_id, so all current content keeps
        # working once retrieval/deletion move to the file_locations model.
        # Idempotent (ON CONFLICT DO NOTHING) — safe to run on every startup.
        if CHANNEL_ID:
            cur.execute('SELECT COUNT(*) FROM storage_channels')
            if cur.fetchone()[0] == 0:
                cur.execute(
                    'INSERT INTO storage_channels (chat_id, title) VALUES (%s, %s) '
                    'ON CONFLICT (chat_id) DO NOTHING',
                    (str(CHANNEL_ID), 'Primary (migrated)')
                )
                logging.info("Seeded primary storage channel from CHANNEL env.")

            cur.execute('SELECT id FROM storage_channels WHERE chat_id = %s', (str(CHANNEL_ID),))
            primary = cur.fetchone()
            if primary:
                cur.execute(
                    '''
                    INSERT INTO file_locations (file_pk, channel_id, message_id)
                    SELECT f.id, %s, f.message_id
                    FROM files f
                    WHERE f.message_id IS NOT NULL
                    ON CONFLICT (file_pk, channel_id) DO NOTHING
                    ''',
                    (primary[0],)
                )

        # ── Pending cross-bot replications ────────────────────────────────────
        # When the uploading bot can't reach some channels (not an admin there),
        # it queues a replication task here. The bot that IS admin in the target
        # channel picks it up on startup or via the periodic 5-minute loop,
        # copy_message-ing the file from a shared channel it CAN access.
        cur.execute('''
            CREATE TABLE IF NOT EXISTS pending_replications (
                id                SERIAL  PRIMARY KEY,
                file_pk           INTEGER NOT NULL REFERENCES files(id)             ON DELETE CASCADE,
                target_channel_id INTEGER NOT NULL REFERENCES storage_channels(id) ON DELETE CASCADE,
                status            TEXT        DEFAULT 'pending',
                created_at        TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE (file_pk, target_channel_id)
            )
        ''')

        # ── Indices for common query patterns ─────────────────────────────────
        cur.execute(
            'CREATE INDEX IF NOT EXISTS idx_files_folder_id   ON files(folder_id)'
        )
        cur.execute(
            'CREATE INDEX IF NOT EXISTS idx_users_status       ON users(status)'
        )
        cur.execute(
            'CREATE INDEX IF NOT EXISTS idx_payment_orders_uid ON payment_orders(user_id)'
        )
        cur.execute(
            'CREATE INDEX IF NOT EXISTS idx_file_locations_file ON file_locations(file_pk)'
        )
        cur.execute(
            'CREATE INDEX IF NOT EXISTS idx_bot_channels_channel ON bot_channels(channel_id)'
        )
        cur.execute(
            'CREATE INDEX IF NOT EXISTS idx_pending_replications_status '
            'ON pending_replications(status) WHERE status = \'pending\''
        )

        # ── Phase 1 migrations: categories & search ───────────────────────────
        # Enable pg_trgm for fuzzy/similarity search (idempotent on Supabase)
        try:
            cur.execute('CREATE EXTENSION IF NOT EXISTS pg_trgm')
            logging.info("pg_trgm extension enabled.")
        except Exception as _ext_err:
            logging.warning(
                f"Could not create pg_trgm extension: {_ext_err}. "
                "Fuzzy search will fall back to prefix matching only. "
                "To enable, run: CREATE EXTENSION pg_trgm; in Supabase SQL editor."
            )
            conn.rollback()
            conn = get_connection()
            cur = conn.cursor()

        # Add category_id FK to folders
        _safe_alter(cur, 'folders', 'category_id',
                    'INTEGER REFERENCES categories(id) ON DELETE SET NULL')

        # Add search_vector column to folders
        _safe_alter(cur, 'folders', 'search_vector', 'tsvector')

        # Backfill search_vector for all existing rows
        cur.execute(
            "UPDATE folders SET search_vector = to_tsvector('english', name) "
            "WHERE search_vector IS NULL"
        )

        # GIN index for full-text search
        cur.execute(
            'CREATE INDEX IF NOT EXISTS idx_folders_search '
            'ON folders USING GIN(search_vector)'
        )
        # Index for category lookups
        cur.execute(
            'CREATE INDEX IF NOT EXISTS idx_folders_category '
            'ON folders(category_id)'
        )
        # Trigram index — created in a try/except since pg_trgm may not be available
        try:
            cur.execute(
                'CREATE INDEX IF NOT EXISTS idx_folders_name_trgm '
                'ON folders USING GIN(name gin_trgm_ops)'
            )
        except Exception as _trgm_err:
            logging.warning(f"Could not create trigram index: {_trgm_err}. Fuzzy search disabled.")
            conn.rollback()
            conn = get_connection()
            cur = conn.cursor()

        # Catalog config: store Telegra.ph token & page path
        cur.execute("SELECT 1 FROM payment_config WHERE key = 'telegraph_access_token'")
        if not cur.fetchone():
            cur.execute(
                "INSERT INTO payment_config (key, value_text) VALUES ('telegraph_access_token', '') "
                "ON CONFLICT (key) DO NOTHING"
            )
        cur.execute("SELECT 1 FROM payment_config WHERE key = 'telegraph_page_path'")
        if not cur.fetchone():
            cur.execute(
                "INSERT INTO payment_config (key, value_text) VALUES ('telegraph_page_path', '') "
                "ON CONFLICT (key) DO NOTHING"
            )
        # Catalog auto-update rate-limit timestamp
        cur.execute("SELECT 1 FROM payment_config WHERE key = 'catalog_last_generated'")
        if not cur.fetchone():
            cur.execute(
                "INSERT INTO payment_config (key, value_text) VALUES ('catalog_last_generated', '') "
                "ON CONFLICT (key) DO NOTHING"
            )

        conn.commit()
        logging.info("Database initialised successfully.")
    except Exception as e:
        logging.critical(f"Database initialisation failed: {e}")
        if conn:
            conn.rollback()
        raise
    finally:
        if conn:
            conn.close()  # direct connection used during init — close normally

    # Init pool after all schema work succeeds
    _init_pool()


def _safe_alter(cur, table: str, column: str, col_type: str):
    """Add a column to a table if it doesn't already exist (idempotent).

    The existence check is scoped to the 'public' schema (where all of this
    bot's tables live). This is essential on Supabase/Postgres: other schemas
    such as `auth` ship their own `users` table with overlapping column names
    (e.g. auth.users.created_at). An unscoped check would match that row and
    wrongly conclude the column already exists, silently skipping the ALTER on
    public.users. The ALTER is likewise schema-qualified so it targets exactly
    the table we checked.
    """
    cur.execute(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = %s AND column_name = %s",
        (table, column)
    )
    if not cur.fetchone():
        cur.execute(f'ALTER TABLE public.{table} ADD COLUMN {column} {col_type}')
        logging.info(f"Migration: added column public.{table}.{column}")


# ── Query helpers ──────────────────────────────────────────────────────────────

def db_execute(query: str, params=None):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(query, params)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        _release(conn)


def db_execute_returning(query: str, params=None):
    """Run a writing statement that returns a row (INSERT/UPDATE … RETURNING)
    and COMMIT it. Use this instead of db_fetchone for writes — db_fetchone
    never commits, so an INSERT through it would be rolled back."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(query, params)
        row = cur.fetchone()
        conn.commit()
        return row
    except Exception:
        conn.rollback()
        raise
    finally:
        _release(conn)


def db_fetchone(query: str, params=None):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(query, params)
        return cur.fetchone()
    finally:
        _release(conn)


def db_fetchall(query: str, params=None):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(query, params)
        return cur.fetchall()
    finally:
        _release(conn)


def add_user_to_db(user_id: int, username: str = None, first_name: str = None):
    """Insert a new user or update their display info if they already exist."""
    db_execute(
        '''
        INSERT INTO users (user_id, username, first_name, status, welcome_sent)
        VALUES (%s, %s, %s, 'pending', FALSE)
        ON CONFLICT (user_id) DO UPDATE
            SET username   = COALESCE(EXCLUDED.username,   users.username),
                first_name = COALESCE(EXCLUDED.first_name, users.first_name)
        ''',
        (user_id, username, first_name)
    )


# ── Search helper ──────────────────────────────────────────────────────────────

def search_folders(query: str, limit: int = 15):
    """
    Layered folder search: exact → prefix → full-text (tsvector) → fuzzy (trigram).
    Returns list of (folder_id, name, file_count, premium, admin_approval, category_name).
    Each layer is tried; first non-empty result wins.
    """
    conn = get_connection()
    _base = '''
        SELECT f.id, f.name,
               COUNT(fi.id) AS file_count,
               f.premium, f.admin_approval,
               c.name AS category_name
        FROM folders f
        LEFT JOIN files fi     ON fi.folder_id = f.id
        LEFT JOIN categories c ON c.id = f.category_id
        WHERE f.parent_id IS NULL
    '''
    try:
        cur = conn.cursor()

        # Layer 1: exact match (case-insensitive)
        cur.execute(
            _base + " AND LOWER(f.name) = LOWER(%s) GROUP BY f.id, c.name ORDER BY f.name LIMIT %s",
            (query, limit)
        )
        rows = cur.fetchall()
        if rows:
            return rows

        # Layer 2: prefix match
        cur.execute(
            _base + " AND LOWER(f.name) LIKE LOWER(%s) GROUP BY f.id, c.name ORDER BY f.name LIMIT %s",
            (query + '%', limit)
        )
        rows = cur.fetchall()
        if rows:
            return rows

        # Layer 3: substring / contains match
        cur.execute(
            _base + " AND LOWER(f.name) LIKE '%' || LOWER(%s) || '%' GROUP BY f.id, c.name ORDER BY f.name LIMIT %s",
            (query, limit)
        )
        rows = cur.fetchall()
        if rows:
            return rows

        # Layer 4: full-text search (tsvector)
        try:
            cur.execute(
                _base + """
                    AND f.search_vector @@ plainto_tsquery('english', %s)
                    GROUP BY f.id, c.name
                    ORDER BY ts_rank(f.search_vector, plainto_tsquery('english', %s)) DESC
                    LIMIT %s
                """,
                (query, query, limit)
            )
            rows = cur.fetchall()
            if rows:
                return rows
        except Exception:
            pass

        # Layer 5: fuzzy trigram similarity (requires pg_trgm)
        try:
            cur.execute(
                _base + """
                    AND f.name %% %s
                    GROUP BY f.id, c.name
                    ORDER BY similarity(f.name, %s) DESC
                    LIMIT %s
                """,
                (query, query, limit)
            )
            rows = cur.fetchall()
            return rows
        except Exception:
            pass

        return []
    finally:
        _release(conn)


# ── Catalog config helpers ─────────────────────────────────────────────────────

def get_catalog_config(key: str) -> str:
    """Read a catalog config value from payment_config table, scoped by BOT_ID."""
    scoped_key = f"{key}_{BOT_ID}"
    row = db_fetchone("SELECT value_text FROM payment_config WHERE key = %s", (scoped_key,))
    return row[0] if row and row[0] else ''


def set_catalog_config(key: str, value: str) -> None:
    """Write a catalog config value to payment_config table, scoped by BOT_ID."""
    scoped_key = f"{key}_{BOT_ID}"
    db_execute(
        "INSERT INTO payment_config (key, value_text) VALUES (%s, %s) "
        "ON CONFLICT (key) DO UPDATE SET value_text = EXCLUDED.value_text",
        (scoped_key, value)
    )