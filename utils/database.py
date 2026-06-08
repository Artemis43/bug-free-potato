import logging
import psycopg2
from psycopg2.extras import RealDictCursor
from config import POSTGRES_CONNECTION_STRING

# ---------------------------------------------------------------------------
# Connection helpers
# ---------------------------------------------------------------------------

def get_connection():
    """Return a fresh psycopg2 connection.

    Using a per-call connection is the simplest safe pattern for an async bot
    running on a single process.  For high-throughput bots, swap this out for
    a connection pool (e.g. psycopg2.pool.ThreadedConnectionPool).
    """
    return psycopg2.connect(POSTGRES_CONNECTION_STRING)


# ---------------------------------------------------------------------------
# Schema initialisation
# ---------------------------------------------------------------------------

def initialize_database():
    """Create all tables if they do not already exist."""
    conn = get_connection()
    cursor = conn.cursor()

    try:
        # Table to manage folders
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS folders (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            parent_id INTEGER,
            premium BOOLEAN DEFAULT FALSE,
            download_count INTEGER DEFAULT 0,
            admin_approval BOOLEAN DEFAULT FALSE,
            FOREIGN KEY (parent_id) REFERENCES folders (id)
        )
        ''')

        # Table to manage files
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS files (
            id SERIAL PRIMARY KEY,
            file_id TEXT NOT NULL,
            file_name TEXT NOT NULL,
            folder_id INTEGER,
            message_id INTEGER,
            caption TEXT,
            file_type TEXT NOT NULL,
            FOREIGN KEY (folder_id) REFERENCES folders (id)
        )
        ''')

        # Table to manage users
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY,
            premium_expiration TIMESTAMPTZ,
            approved BOOLEAN DEFAULT FALSE,
            status TEXT DEFAULT 'pending',
            premium BOOLEAN DEFAULT FALSE,
            last_download TIMESTAMPTZ,
            welcome_sent BOOLEAN DEFAULT FALSE
        )
        ''')

        # Table to store the current caption settings
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS current_caption (
            id SERIAL PRIMARY KEY,
            caption_type TEXT NOT NULL,
            custom_text TEXT
        )
        ''')

        # Table to store user-folder approval status
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_folder_approval (
            user_id BIGINT,
            folder_id INTEGER,
            approved BOOLEAN DEFAULT FALSE,
            download_completed BOOLEAN DEFAULT FALSE,
            PRIMARY KEY (user_id, folder_id),
            FOREIGN KEY (folder_id) REFERENCES folders (id),
            FOREIGN KEY (user_id) REFERENCES users (user_id)
        )
        ''')

        # Table to store global bot states
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS bot_state (
            key TEXT PRIMARY KEY,
            value INTEGER
        )
        ''')

        conn.commit()
        logging.info("Database initialised successfully.")
    except Exception as e:
        conn.rollback()
        logging.error(f"Error initialising database: {e}")
        raise
    finally:
        cursor.close()
        conn.close()


# ---------------------------------------------------------------------------
# User helpers
# ---------------------------------------------------------------------------

def add_user_to_db(user_id: int):
    """Insert a user if they do not already exist (idempotent)."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            '''
            INSERT INTO users (user_id, status, welcome_sent)
            VALUES (%s, 'pending', FALSE)
            ON CONFLICT (user_id) DO NOTHING
            ''',
            (user_id,)
        )
        conn.commit()
    except Exception as e:
        conn.rollback()
        logging.error(f"Error adding user {user_id} to DB: {e}")
        raise
    finally:
        cursor.close()
        conn.close()


# ---------------------------------------------------------------------------
# Generic query helpers  (thin wrappers – keep handlers simple)
# ---------------------------------------------------------------------------

def db_fetchone(query: str, params: tuple = ()):
    """Execute a SELECT and return the first row, or None."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(query, params)
        return cursor.fetchone()
    finally:
        cursor.close()
        conn.close()


def db_fetchall(query: str, params: tuple = ()):
    """Execute a SELECT and return all rows."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(query, params)
        return cursor.fetchall()
    finally:
        cursor.close()
        conn.close()


def db_execute(query: str, params: tuple = ()):
    """Execute a DML statement (INSERT/UPDATE/DELETE) and commit."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(query, params)
        conn.commit()
    except Exception as e:
        conn.rollback()
        logging.error(f"DB execute error: {e}")
        raise
    finally:
        cursor.close()
        conn.close()