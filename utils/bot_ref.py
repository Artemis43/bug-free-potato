"""
utils/bot_ref.py — Centralised bot reference holder.

Problem solved:
    Every handler file previously did `from main import bot`, creating a
    circular dependency: main.py imports handlers, handlers import main.py.

Solution:
    1. main.py calls `set_bot(bot_instance)` immediately after creating the Bot.
    2. All handlers call `get_bot()` to retrieve the same Bot instance.
    3. No circular import chain, no module-level side effects.

Usage in handlers:
    from utils.bot_ref import get_bot

    async def my_handler(message):
        bot = get_bot()
        await bot.send_message(...)
"""
from __future__ import annotations
from typing import Optional

_bot_instance = None
_dispatcher_instance = None


def set_bot(bot) -> None:
    """Called once from main.py after Bot is instantiated."""
    global _bot_instance
    _bot_instance = bot


def get_bot():
    """
    Return the global Bot instance.
    Raises RuntimeError if called before set_bot() (i.e. before startup).
    """
    if _bot_instance is None:
        raise RuntimeError(
            "Bot instance not initialised. "
            "Ensure set_bot() is called in main.py before any handlers run."
        )
    return _bot_instance


def set_dispatcher(dp) -> None:
    """Called once from main.py after the Dispatcher is instantiated."""
    global _dispatcher_instance
    _dispatcher_instance = dp


def get_dispatcher():
    """
    Return the global Dispatcher instance.

    Used by the /stop handler to gracefully stop long-polling. Raises
    RuntimeError if called before set_dispatcher() (i.e. before startup).
    """
    if _dispatcher_instance is None:
        raise RuntimeError(
            "Dispatcher instance not initialised. "
            "Ensure set_dispatcher() is called in main.py before any handlers run."
        )
    return _dispatcher_instance
