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
        # TCP keepalive — prevents Supabase Pooler from dropping idle connections
        # after its ~10 s inactivity threshold. Sends heartbeat every 30 s.
        keepalives=1,
        keepalives_idle=30,
        keepalives_interval=10,
        keepalives_count=5,
    )
    logging.info("Connection pool initialised (min=2 max=10 keepalives=30s).")


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

        # ── Nested hierarchy migrations for categories ─────────────────────────
        _safe_alter(cur, 'categories', 'parent_id',
                    'INTEGER REFERENCES categories(id) ON DELETE SET NULL')
        _safe_alter(cur, 'categories', 'description', 'TEXT')

        # ── Folders ───────────────────────────────────────────────────────────
        cur.execute('''
            CREATE TABLE IF NOT EXISTS folders (
                id             SERIAL PRIMARY KEY,
                name           TEXT    NOT NULL UNIQUE,
                parent_id      INTEGER REFERENCES folders(id) ON DELETE SET NULL,
                premium        BOOLEAN DEFAULT FALSE,
                admin_approval BOOLEAN DEFAULT FALSE,
                download_count INTEGER DEFAULT 0
            )
        ''')

        # ── Nested hierarchy migrations for folders ────────────────────────────
        _safe_alter(cur, 'folders', 'description', 'TEXT')
        _safe_alter(cur, 'folders', 'emoji', "TEXT DEFAULT '📁'")
        _safe_alter(cur, 'folders', 'sort_order', 'INTEGER DEFAULT 0')
        _safe_alter(cur, 'folders', 'created_at', 'TIMESTAMPTZ DEFAULT NOW()')
        _safe_alter(cur, 'folders', 'updated_at', 'TIMESTAMPTZ DEFAULT NOW()')

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
        # ── Sprint 3.5: Additional indexes for dashboard query patterns ─────
        cur.execute(
            'CREATE INDEX IF NOT EXISTS idx_users_premium '
            'ON users(premium)'
        )
        cur.execute(
            'CREATE INDEX IF NOT EXISTS idx_users_created_at '
            'ON users(created_at DESC NULLS LAST)'
        )
        cur.execute(
            'CREATE INDEX IF NOT EXISTS idx_users_status_premium '
            'ON users(status, premium)'
        )
        cur.execute(
            'CREATE INDEX IF NOT EXISTS idx_payment_orders_status_method '
            'ON payment_orders(status, payment_method)'
        )
        cur.execute(
            'CREATE INDEX IF NOT EXISTS idx_files_name_trgm '
            'ON files USING gin(file_name gin_trgm_ops)'
        ) if True else None  # wrapped in try below

        # ── Sprint 5.2: Admin activity log table ───────────────────────────
        cur.execute('''
            CREATE TABLE IF NOT EXISTS admin_activity_log (
                id          SERIAL      PRIMARY KEY,
                action      TEXT        NOT NULL,
                target_type TEXT,
                target_id   TEXT,
                detail      TEXT,
                created_at  TIMESTAMPTZ DEFAULT NOW()
            )
        ''')
        cur.execute(
            'CREATE INDEX IF NOT EXISTS idx_activity_log_created '
            'ON admin_activity_log(created_at DESC NULLS LAST)'
        )

        # ── Phase 2: User favorites & download history ──────────────────────
        cur.execute('''
            CREATE TABLE IF NOT EXISTS user_favorites (
                user_id   BIGINT  NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                folder_id INTEGER NOT NULL REFERENCES folders(id)    ON DELETE CASCADE,
                added_at  TIMESTAMPTZ DEFAULT NOW(),
                PRIMARY KEY (user_id, folder_id)
            )
        ''')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS download_history (
                id            SERIAL  PRIMARY KEY,
                user_id       BIGINT  NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                folder_id     INTEGER NOT NULL REFERENCES folders(id)    ON DELETE CASCADE,
                downloaded_at TIMESTAMPTZ DEFAULT NOW()
            )
        ''')
        cur.execute(
            'CREATE INDEX IF NOT EXISTS idx_user_favorites_user '
            'ON user_favorites(user_id)'
        )
        cur.execute(
            'CREATE INDEX IF NOT EXISTS idx_download_history_user '
            'ON download_history(user_id, downloaded_at DESC)'
        )
        cur.execute(
            'CREATE INDEX IF NOT EXISTS idx_folders_parent '
            'ON folders(parent_id)'
        )
        cur.execute(
            'CREATE INDEX IF NOT EXISTS idx_categories_parent '
            'ON categories(parent_id)'
        )
        cur.execute(
            'CREATE INDEX IF NOT EXISTS idx_folders_sort_order '
            'ON folders(sort_order)'
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
    Now searches ALL depth levels (no parent_id IS NULL filter).
    Returns list of (folder_id, name, file_count, premium, admin_approval, category_name, path).
    """
    conn = get_connection()
    # Build path as "Category > Parent > Name" for display in results
    _base = '''
        SELECT f.id, f.name,
               COUNT(fi.id) AS file_count,
               f.premium, f.admin_approval,
               COALESCE(c.name, 'Uncategorized') AS category_name,
               COALESCE(c.name || ' > ', '') || f.name AS path
        FROM folders f
        LEFT JOIN files      fi ON fi.folder_id = f.id
        LEFT JOIN categories c  ON c.id = f.category_id
        WHERE TRUE
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


# ── Hierarchy navigation helpers ────────────────────────────────────────────────

def get_child_folders(parent_id=None, category_id=None):
    """
    Get immediate child folders of a parent_id, OR root folders in a category.
    Returns list of (id, name, emoji, premium, admin_approval, file_count, has_children).
    """
    conn = get_connection()
    try:
        cur = conn.cursor()
        if parent_id is not None:
            # Sub-folders of a folder
            cur.execute("""
                SELECT f.id, f.name, COALESCE(f.emoji, '📁'), f.premium, f.admin_approval,
                       COUNT(fi.id) AS file_count,
                       (SELECT COUNT(*) FROM folders sf WHERE sf.parent_id = f.id) > 0 AS has_children
                FROM folders f
                LEFT JOIN files fi ON fi.folder_id = f.id
                WHERE f.parent_id = %s
                GROUP BY f.id
                ORDER BY COALESCE(f.sort_order, 0), f.name
            """, (parent_id,))
        elif category_id is not None:
            if category_id == 0:
                # Uncategorized — root folders with no category and no parent
                cur.execute("""
                    SELECT f.id, f.name, COALESCE(f.emoji, '📁'), f.premium, f.admin_approval,
                           COUNT(fi.id) AS file_count,
                           (SELECT COUNT(*) FROM folders sf WHERE sf.parent_id = f.id) > 0 AS has_children
                    FROM folders f
                    LEFT JOIN files fi ON fi.folder_id = f.id
                    WHERE f.category_id IS NULL AND f.parent_id IS NULL
                    GROUP BY f.id
                    ORDER BY COALESCE(f.sort_order, 0), f.name
                """)
            else:
                # Root folders in a specific category
                cur.execute("""
                    SELECT f.id, f.name, COALESCE(f.emoji, '📁'), f.premium, f.admin_approval,
                           COUNT(fi.id) AS file_count,
                           (SELECT COUNT(*) FROM folders sf WHERE sf.parent_id = f.id) > 0 AS has_children
                    FROM folders f
                    LEFT JOIN files fi ON fi.folder_id = f.id
                    WHERE f.category_id = %s AND f.parent_id IS NULL
                    GROUP BY f.id
                    ORDER BY COALESCE(f.sort_order, 0), f.name
                """, (category_id,))
        return cur.fetchall()
    finally:
        _release(conn)


def get_child_categories(parent_id=None):
    """
    Get immediate sub-categories of a parent category (or root categories if parent_id=None).
    Returns list of (id, name, emoji, sort_order, folder_count, has_children).
    """
    conn = get_connection()
    try:
        cur = conn.cursor()
        if parent_id is None:
            cur.execute("""
                SELECT c.id, c.name, c.emoji, c.sort_order,
                       COUNT(DISTINCT f.id) AS folder_count,
                       (SELECT COUNT(*) FROM categories sc WHERE sc.parent_id = c.id) > 0 AS has_children
                FROM categories c
                LEFT JOIN folders f ON f.category_id = c.id AND f.parent_id IS NULL
                WHERE c.parent_id IS NULL
                GROUP BY c.id
                ORDER BY c.sort_order, c.name
            """)
        else:
            cur.execute("""
                SELECT c.id, c.name, c.emoji, c.sort_order,
                       COUNT(DISTINCT f.id) AS folder_count,
                       (SELECT COUNT(*) FROM categories sc WHERE sc.parent_id = c.id) > 0 AS has_children
                FROM categories c
                LEFT JOIN folders f ON f.category_id = c.id AND f.parent_id IS NULL
                WHERE c.parent_id = %s
                GROUP BY c.id
                ORDER BY c.sort_order, c.name
            """, (parent_id,))
        return cur.fetchall()
    finally:
        _release(conn)


def get_folder_breadcrumb(folder_id: int) -> list:
    """
    Walk up parent_id chain for a folder.
    Returns [(id, name, emoji), ...] root-first.
    """
    crumbs = []
    seen = set()
    conn = get_connection()
    try:
        cur = conn.cursor()
        fid = folder_id
        while fid and fid not in seen:
            seen.add(fid)
            cur.execute(
                "SELECT id, name, COALESCE(emoji, '📁'), parent_id FROM folders WHERE id = %s",
                (fid,)
            )
            row = cur.fetchone()
            if not row:
                break
            crumbs.append((row[0], row[1], row[2]))
            fid = row[3]  # parent_id
        crumbs.reverse()
        return crumbs
    finally:
        _release(conn)


def get_category_breadcrumb(category_id: int) -> list:
    """
    Walk up parent_id chain for a category.
    Returns [(id, name, emoji), ...] root-first.
    """
    crumbs = []
    seen = set()
    conn = get_connection()
    try:
        cur = conn.cursor()
        cid = category_id
        while cid and cid not in seen:
            seen.add(cid)
            cur.execute(
                "SELECT id, name, COALESCE(emoji, '📁'), parent_id FROM categories WHERE id = %s",
                (cid,)
            )
            row = cur.fetchone()
            if not row:
                break
            crumbs.append((row[0], row[1], row[2]))
            cid = row[3]
        crumbs.reverse()
        return crumbs
    finally:
        _release(conn)


def has_folder_cycle(folder_id: int, proposed_parent_id: int) -> bool:
    """
    Returns True if proposed_parent_id is a descendant of folder_id.
    Prevents circular parent-child references when reparenting.
    """
    seen = set()
    conn = get_connection()
    try:
        cur = conn.cursor()
        current = proposed_parent_id
        while current and current not in seen:
            seen.add(current)
            if current == folder_id:
                return True
            cur.execute("SELECT parent_id FROM folders WHERE id = %s", (current,))
            row = cur.fetchone()
            current = row[0] if row else None
        return False
    finally:
        _release(conn)


def get_subtree_file_count(folder_id: int) -> int:
    """Recursively count all files in this folder and all descendant folders."""
    row = db_fetchone("""
        WITH RECURSIVE subtree AS (
            SELECT id FROM folders WHERE id = %s
            UNION ALL
            SELECT f.id FROM folders f
            JOIN subtree s ON f.parent_id = s.id
        )
        SELECT COUNT(*) FROM files WHERE folder_id IN (SELECT id FROM subtree)
    """, (folder_id,))
    return row[0] if row else 0


def toggle_user_favorite(user_id: int, folder_id: int) -> bool:
    """
    Toggle a folder in/out of user's favorites.
    Returns True if added, False if removed.
    """
    existing = db_fetchone(
        "SELECT 1 FROM user_favorites WHERE user_id = %s AND folder_id = %s",
        (user_id, folder_id)
    )
    if existing:
        db_execute(
            "DELETE FROM user_favorites WHERE user_id = %s AND folder_id = %s",
            (user_id, folder_id)
        )
        return False
    else:
        db_execute(
            "INSERT INTO user_favorites (user_id, folder_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (user_id, folder_id)
        )
        return True


def get_user_favorites(user_id: int):
    """Get user's bookmarked folders. Returns (id, name, emoji, file_count, has_children)."""
    return db_fetchall("""
        SELECT f.id, f.name, COALESCE(f.emoji, '📁'),
               COUNT(fi.id) AS file_count,
               (SELECT COUNT(*) FROM folders sf WHERE sf.parent_id = f.id) > 0 AS has_children
        FROM user_favorites uf
        JOIN folders f ON f.id = uf.folder_id
        LEFT JOIN files fi ON fi.folder_id = f.id
        WHERE uf.user_id = %s
        GROUP BY f.id, uf.added_at
        ORDER BY uf.added_at DESC
    """, (user_id,))


def record_download_history(user_id: int, folder_id: int):
    """Record a folder download in history (keep last 50 per user)."""
    db_execute(
        "INSERT INTO download_history (user_id, folder_id) VALUES (%s, %s)",
        (user_id, folder_id)
    )
    # Prune to 50 most recent per user
    db_execute("""
        DELETE FROM download_history
        WHERE id IN (
            SELECT id FROM download_history
            WHERE user_id = %s
            ORDER BY downloaded_at DESC
            OFFSET 50
        )
    """, (user_id,))


def get_recent_downloads(user_id: int, limit: int = 10):
    """Get recently downloaded folders for a user. Returns (id, name, emoji, downloaded_at)."""
    return db_fetchall("""
        SELECT DISTINCT ON (f.id) f.id, f.name, COALESCE(f.emoji, '📁'), dh.downloaded_at
        FROM download_history dh
        JOIN folders f ON f.id = dh.folder_id
        WHERE dh.user_id = %s
        ORDER BY f.id, dh.downloaded_at DESC
        LIMIT %s
    """, (user_id, limit))


def get_recently_added_folders(limit: int = 5):
    """Get the most recently created folders (for What's New section)."""
    return db_fetchall("""
        SELECT f.id, f.name, COALESCE(f.emoji, '📁'), f.created_at,
               COUNT(fi.id) AS file_count
        FROM folders f
        LEFT JOIN files fi ON fi.folder_id = f.id
        WHERE f.created_at IS NOT NULL
        GROUP BY f.id
        ORDER BY f.created_at DESC
        LIMIT %s
    """, (limit,))


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