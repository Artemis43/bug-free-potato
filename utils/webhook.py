import logging
from utils.database import initialize_database
from config import WEBHOOK_URL

async def on_startup(dispatcher):
    """Set the Telegram webhook and initialise the database schema."""
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
        raise  # Let the error propagate — bot cannot run without a DB

async def on_shutdown(dispatcher):
    """Delete the Telegram webhook on graceful shutdown."""
    from main import bot
    logging.warning("Shutting down …")
    try:
        await bot.delete_webhook()
    except Exception as e:
        logging.error(f"Error deleting webhook: {e}")
    logging.warning("Bye!")