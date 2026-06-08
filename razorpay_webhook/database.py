"""
razorpay_webhook/database.py
─────────────────────────────
Synchronous psycopg2 helpers — mirrors the bot's utils/database.py.
This service writes to the same PostgreSQL database as the bot.
"""

import logging
import psycopg2
from psycopg2.extras import RealDictCursor

from config import DB_STRING

log = logging.getLogger(__name__)


def get_connection():
    """Return a fresh psycopg2 connection. Callers must close it.

    If your DB_STRING points to a Supabase direct-connection host
    (db.<project>.supabase.co port 5432) and the service is hosted on
    Render.com, you may get "Network is unreachable" because Render's
    network cannot reach Supabase's IPv6 address.  Switch DB_STRING to
    the Supabase Session/Transaction pooler URL instead:
        postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:6543/postgres
    """
    return psycopg2.connect(DB_STRING, connect_timeout=5)


def db_execute(query: str, params=None) -> None:
    """Execute a write query (INSERT / UPDATE / DELETE)."""
    conn = None
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(query, params)
        conn.commit()
    except Exception:
        if conn:
            conn.rollback()
        raise
    finally:
        if conn:
            conn.close()


def db_fetchone(query: str, params=None) -> tuple | None:
    """Fetch a single row. Returns None if not found."""
    conn = None
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(query, params)
        return cur.fetchone()
    finally:
        if conn:
            conn.close()


def db_fetchall(query: str, params=None) -> list[tuple]:
    """Fetch all matching rows."""
    conn = None
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(query, params)
        return cur.fetchall() or []
    finally:
        if conn:
            conn.close()


def is_duplicate_payment(razorpay_payment_id: str) -> bool:
    """Return True if this payment ID has already been processed (idempotency guard)."""
    row = db_fetchone(
        "SELECT 1 FROM payment_orders WHERE razorpay_payment_id = %s AND status = 'paid'",
        (razorpay_payment_id,)
    )
    return row is not None


def mark_order_paid(razorpay_link_id: str, razorpay_payment_id: str,
                    user_id: int, order_type: str, ref_id: int) -> None:
    """Atomically mark the matching order as paid."""
    db_execute(
        """
        UPDATE payment_orders
           SET status               = 'paid',
               paid_at              = NOW(),
               razorpay_payment_id  = %s
         WHERE razorpay_link_id = %s
            OR (user_id = %s AND order_type = %s AND ref_id = %s AND status = 'created')
        """,
        (razorpay_payment_id, razorpay_link_id, user_id, order_type, ref_id),
    )


def activate_user_premium(user_id: int, expiration_date) -> None:
    """Set premium = TRUE and record expiry on the user row."""
    db_execute(
        "UPDATE users SET premium = TRUE, premium_expiration = %s WHERE user_id = %s",
        (expiration_date, user_id),
    )


def insert_folder_approval(user_id: int, folder_id: int) -> None:
    """Create / reset a paid-folder approval row (not yet approved — admin must confirm)."""
    db_execute(
        """
        INSERT INTO user_folder_approval (user_id, folder_id, approved, download_completed)
        VALUES (%s, %s, FALSE, FALSE)
        ON CONFLICT (user_id, folder_id) DO UPDATE
            SET approved           = FALSE,
                download_completed = FALSE
        """,
        (user_id, folder_id),
    )


def get_plan(plan_id: int) -> tuple | None:
    """Return (name, amount_paise, days) for a plan, or None."""
    return db_fetchone(
        "SELECT name, amount_paise, days FROM payment_plans WHERE id = %s",
        (plan_id,),
    )


def get_folder_name(folder_id: int) -> str:
    """Return folder name, or a fallback string."""
    row = db_fetchone("SELECT name FROM folders WHERE id = %s", (folder_id,))
    return row[0] if row else f"Folder #{folder_id}"


def get_user_info(user_id: int) -> tuple | None:
    """Return (first_name, username) for a user."""
    return db_fetchone(
        "SELECT first_name, username FROM users WHERE user_id = %s",
        (user_id,),
    )
