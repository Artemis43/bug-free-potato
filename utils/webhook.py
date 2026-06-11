from utils.bot_ref import get_bot
import asyncio
import logging
from utils.database import initialize_database, db_fetchall
from config import WEBHOOK_URL, ADMIN_IDS

log = logging.getLogger(__name__)


# ── BotFather command menus ────────────────────────────────────────────────

async def _register_commands(bot):
    """
    Register command descriptions with Telegram so they appear in the
    '/' menu within the chat input field.

    Two scopes are registered:
      • All private chats  → user-visible commands
      • Each admin's chat  → user commands + admin commands
    """
    from aiogram.types import (
        BotCommand,
        BotCommandScopeAllPrivateChats,
        BotCommandScopeChat,
    )

    user_commands = [
        BotCommand(command="start",    description="📂 Open the folder menu"),
        BotCommand(command="help",     description="❓ How to download content"),
        BotCommand(command="about",    description="ℹ️  About this bot"),
        BotCommand(command="status",   description="👤 Your account & cooldown info"),
        BotCommand(command="pay",      description="💎 View Premium plans"),
        BotCommand(command="download", description="⬇️  Download a folder by name"),
        BotCommand(command="commands", description="📋 Full command reference"),
    ]

    admin_commands = user_commands + [
        BotCommand(command="pending",         description="🕐 List pending approval requests"),
        BotCommand(command="userinfo",        description="🔍 Look up a user's account"),
        BotCommand(command="resetcooldown",   description="🔄 Clear a user's download cooldown"),
        BotCommand(command="setuser",         description="⭐ Grant or revoke Premium for a user"),
        BotCommand(command="setfolder",       description="📁 Toggle a folder's premium flag"),
        BotCommand(command="newfolder",       description="➕ Create a new folder"),
        BotCommand(command="setuploadfolder", description="📤 Switch active upload folder"),
        BotCommand(command="renamefolder",    description="✏️  Rename a folder"),
        BotCommand(command="deletefolder",    description="🗑️  Delete a folder and its files"),
        BotCommand(command="list",            description="📋 Full folder & user inventory"),
        BotCommand(command="broadcast",       description="📣 Send a message to all users"),
        BotCommand(command="payconfig",       description="💳 Configure payment plans & prices"),
        BotCommand(command="caption",         description="🏷️  Set the file caption"),
        BotCommand(command="stats",           description="📊 Analytics dashboard"),
        BotCommand(command="forcedsyncdb",    description="🔄 Force a database sync"),
        BotCommand(command="stop",            description="🛑 Gracefully shut down the bot"),
    ]

    # Register for all private chats (standard users)
    try:
        await bot.set_my_commands(user_commands, scope=BotCommandScopeAllPrivateChats())
        log.info("Bot commands registered for all private chats.")
    except Exception as e:
        log.warning(f"Could not register user commands: {e}")

    # Register extended admin commands for each admin's own chat
    for admin_id in ADMIN_IDS:
        try:
            scope = BotCommandScopeChat(chat_id=int(admin_id))
            await bot.set_my_commands(admin_commands, scope=scope)
            log.info(f"Admin commands registered for admin {admin_id}.")
        except Exception as e:
            log.warning(f"Could not register admin commands for {admin_id}: {e}")


# ── Startup ────────────────────────────────────────────────────────────────

async def on_startup(bot):
    """
    Called from main.py before the bot starts polling (aiogram v3).

    1. Initialise / migrate the database schema.
    2. Register BotFather command menus.
    3. Reschedule premium-expiry background tasks for active premium users.

    NOTE: This bot runs in long-polling mode (see main.py). We must NOT register
    a Telegram webhook here — doing so conflicts with getUpdates polling (HTTP
    409). The Razorpay/Flask webhook service is a separate process and uses
    HOST_URL independently.
    """
    log.info("Starting in long-polling mode (no Telegram webhook registered).")

    # 1. Database
    try:
        initialize_database()
        log.info("Database initialised successfully.")
    except Exception as e:
        log.critical(f"Database initialisation failed: {e}")
        raise  # Cannot run without DB

    # 2. BotFather command menus
    await _register_commands(bot)

    # 3. Reschedule premium expiry tasks
    await _reschedule_premium_expiry()

    log.info("✅ Bot is ready and listening.")


# ── Premium expiry rescheduler ─────────────────────────────────────────────

async def _reschedule_premium_expiry():
    """
    Recreate auto-expiry tasks for users whose premium hasn't expired yet.
    These tasks are lost on restart (asyncio.create_task is in-process only).
    """
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

    log.info(f"Rescheduling premium expiry for {len(rows)} user(s).")
    for user_id, exp in rows:
        # exp may be timezone-aware (TIMESTAMPTZ from PostgreSQL)
        if hasattr(exp, 'tzinfo') and exp.tzinfo is not None:
            exp = exp.replace(tzinfo=None)
        asyncio.create_task(remove_premium_after_expiry(user_id, exp))


# ── Shutdown ───────────────────────────────────────────────────────────────

async def on_shutdown(bot):
    """Called from main.py on graceful shutdown (aiogram v3)."""
    log.warning("⚠️ Shutting down…")
    try:
        await bot.delete_webhook()
    except Exception as e:
        log.error(f"Error deleting webhook: {e}")
    log.warning("👋 Bot offline. Goodbye!")