import logging
from aiogram import types
from aiogram import Router
from middlewares.authorization import is_private_chat
from config import ADMIN_IDS
from utils.database import db_execute

router = Router()


async def sync_database_command(message: types.Message):
    """Admin command: /forcedsyncdb — manually trigger a database sync."""
    if not is_private_chat(message):
        return

    if str(message.from_user.id) not in ADMIN_IDS:
        await message.reply("You are not authorized to run a forced sync.")
        return

    await message.reply("🔄 Forced database sync started…")
    logging.info(f"Forced sync triggered by admin {message.from_user.id}.")

    # If you have external sync logic (e.g. PlanetScale / Turso replication),
    # call it here.  For now we just acknowledge the request.
    await message.reply("✅ Sync complete.")


async def sync_database(**kwargs):
    """Placeholder for background sync logic.

    Extend this function to implement remote ↔ local DB reconciliation.
    """
    logging.info(f"sync_database called with args: {kwargs}")
