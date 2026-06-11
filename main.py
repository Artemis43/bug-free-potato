"""
main.py — bot entry point (aiogram v3).

Key v3 changes vs v2:
  - Dispatcher() no longer takes a Bot instance
  - Handlers are registered via Router decorators (see each handler module)
  - executor.start_polling() → asyncio.run(dp.start_polling(bot))
  - Middlewares use BaseMiddleware.__call__ signature
  - Errors are handled via router.errors() decorator
"""
import asyncio
import logging
import traceback

from aiogram import Bot, Dispatcher, types, F
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.dispatcher.middlewares.base import BaseMiddleware
from aiogram.fsm.storage.memory import MemoryStorage

from config import API_TOKEN, ADMIN_IDS, LOG_LEVEL
from keep_alive import keep_alive
from utils.bot_ref import set_bot

# ── Logging ────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Keep-alive web server ──────────────────────────────────────────────────
keep_alive()

# ── Bot & Dispatcher ───────────────────────────────────────────────────────
bot = Bot(
    token=API_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
dp = Dispatcher(storage=MemoryStorage())

# Register the bot instance in the central reference module so handlers can
# call get_bot() without circular imports.
set_bot(bot)

# ── Middlewares ────────────────────────────────────────────────────────────
from middlewares.rate_limit import RateLimitMiddleware, CallbackRateLimitMiddleware
from utils.monitoring import record_update, record_handler_error

class _UpdateTrackingMiddleware(BaseMiddleware):
    """Thin middleware that marks each processed update in monitoring."""
    async def __call__(self, handler, event, data):
        record_update()
        return await handler(event, data)

dp.message.middleware(RateLimitMiddleware())
dp.callback_query.middleware(CallbackRateLimitMiddleware())
dp.message.middleware(_UpdateTrackingMiddleware())
dp.callback_query.middleware(_UpdateTrackingMiddleware())

# ── Routers ───────────────────────────────────────────────────────────────
# Each handler module exposes a `router` object.
# We import and include them here so dp knows about every handler.
from handlers import (
    start, broadcast, caption, document,
    getlist, folder, download, setpremium,
    stop, about_help, sync, status, admin_tools, commands_ref, payment,
    payment_stars, stats,
)

# Register all command/filter bindings onto their respective routers
from utils.register_handlers import register_all_handlers
register_all_handlers()

for module in (
    start, broadcast, caption, document,
    getlist, folder, download, setpremium,
    stop, about_help, sync, status, admin_tools, commands_ref, payment,
    payment_stars, stats,
):
    if hasattr(module, 'router'):
        dp.include_router(module.router)
    else:
        log.warning(f"Handler module {module.__name__} has no 'router' attribute — skipped.")

# ── Global error handler ───────────────────────────────────────────────────
@dp.errors()
async def global_error_handler(event: types.ErrorEvent) -> bool:
    """
    Catch-all for any unhandled exception in a handler.
    1. Logs the full traceback.
    2. Sends a friendly message to the user (if the update has a chat).
    3. Alerts the first admin with the error details.
    4. Returns True to suppress the exception (keeps the bot running).
    """
    exception = event.exception
    update    = event.update

    # Track error in monitoring
    update_type = "callback_query" if update.callback_query else "message"
    record_handler_error(update_type)

    log.error(
        "Unhandled exception in update %s:\n%s",
        update,
        traceback.format_exc(),
    )

    chat_id   = None
    user_id   = None
    user_name = None
    cmd_text  = None

    if update.message:
        chat_id   = update.message.chat.id
        user_id   = update.message.from_user.id
        user_name = update.message.from_user.username
        cmd_text  = update.message.text
    elif update.callback_query:
        chat_id   = update.callback_query.message.chat.id
        user_id   = update.callback_query.from_user.id
        user_name = update.callback_query.from_user.username
        cmd_text  = update.callback_query.data

    if chat_id:
        try:
            await bot.send_message(
                chat_id,
                "⚠️ <b>Something went wrong on our end.</b>\n\n"
                "This is a temporary issue — please try again in a moment.\n"
                "If the problem continues, contact the admin.",
            )
        except Exception:
            pass

    if ADMIN_IDS:
        try:
            exc_summary = str(exception)[:300]
            admin_msg = (
                "🚨 <b>Bot Error Alert</b>\n\n"
                f"<b>Exception:</b> <code>{type(exception).__name__}</code>\n"
                f"<code>{exc_summary}</code>\n\n"
                f"<b>User:</b> <code>{user_id}</code> (@{user_name or 'unknown'})\n"
                f"<b>Input:</b> <code>{str(cmd_text or '')[:200]}</code>"
            )
            await bot.send_message(int(ADMIN_IDS[0]), admin_msg)
        except Exception:
            pass

    return True


# ── Startup / shutdown ─────────────────────────────────────────────────────
from utils.webhook import on_startup, on_shutdown

# ── Entry point ────────────────────────────────────────────────────────────
async def main() -> None:
    await on_startup(bot)
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await on_shutdown(bot)


if __name__ == '__main__':
    asyncio.run(main())