import logging
import psycopg2
from config import POSTGRES_CONNECTION_STRING, DEFAULT_CAPTION


def get_connection():
    """Return a fresh psycopg2 connection. Per-call; callers must close."""
    return psycopg2.connect(POSTGRES_CONNECTION_STRING)


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
                current_upload_folder TEXT
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

        conn.commit()
        logging.info("Database initialised successfully.")
    except Exception as e:
        logging.critical(f"Database initialisation failed: {e}")
        if conn:
            conn.rollback()
        raise
    finally:
        if conn:
            conn.close()


def _safe_alter(cur, table: str, column: str, col_type: str):
    """Add a column to a table if it doesn't already exist (idempotent)."""
    cur.execute(
        "SELECT 1 FROM information_schema.columns WHERE table_name=%s AND column_name=%s",
        (table, column)
    )
    if not cur.fetchone():
        cur.execute(f'ALTER TABLE {table} ADD COLUMN {column} {col_type}')
        logging.info(f"Migration: added column {table}.{column}")


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
        conn.close()


def db_fetchone(query: str, params=None):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(query, params)
        return cur.fetchone()
    finally:
        conn.close()


def db_fetchall(query: str, params=None):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(query, params)
        return cur.fetchall()
    finally:
        conn.close()


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