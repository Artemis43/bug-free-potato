import asyncio
import logging
from utils.database import initialize_database, db_fetchall
from config import WEBHOOK_URL


async def on_startup(dispatcher):
    """Set the Telegram webhook, initialise the DB, and reschedule premium expiry tasks."""
    from main import bot

    try:
        await bot.set_webhook(WEBHOOK_URL)
        logging.info(f"Webhook set to {WEBHOOK_URL}")
    except Exception as e:
        logging.error(f"Failed to set webhook: {e}")

    try:
        initialize_database()
    except Exception as e:
        logging.critical(f"Database initialisation failed: {e}")
        raise  # Cannot run without DB

    # Reschedule premium-expiry background tasks for all active premium users.
    # These tasks are lost on restart if they were created with asyncio.create_task()
    # in a previous session — so we reconstruct them here on every startup.
    await _reschedule_premium_expiry()


async def _reschedule_premium_expiry():
    """Recreate auto-expiry tasks for any users whose premium hasn't expired yet."""
    from handlers.setpremium import remove_premium_after_expiry

    rows = db_fetchall(
        '''
        SELECT user_id, premium_expiration
        FROM users
        WHERE premium = TRUE AND premium_expiration IS NOT NULL
        '''
    )

    if not rows:
        return

    logging.info(f"Rescheduling premium expiry for {len(rows)} user(s).")
    for user_id, exp in rows:
        # exp may be timezone-aware (TIMESTAMPTZ from PostgreSQL)
        if hasattr(exp, 'tzinfo') and exp.tzinfo is not None:
            exp = exp.replace(tzinfo=None)
        asyncio.create_task(remove_premium_after_expiry(user_id, exp))


async def on_shutdown(dispatcher):
    """Delete the Telegram webhook on graceful shutdown."""
    from main import bot
    logging.warning("Shutting down …")
    try:
        await bot.delete_webhook()
    except Exception as e:
        logging.error(f"Error deleting webhook: {e}")
    logging.warning("Bye!")