"""
utils/reply_keyboard.py — Persistent Reply Keyboard system.

Telegram's ReplyKeyboardMarkup places large, always-visible buttons above the
phone keyboard — making all actions discoverable without knowing any commands.

This module defines:
  - Button label constants (single source of truth — used by both the keyboard
    builders and the text-routing table in handlers/reply_router.py)
  - Keyboard builders for each bot state
  - A helper to extract the "back" button for any state

Design notes:
  - Reply keyboards can only be attached to send_message(), not edit_message_text().
    They persist until explicitly replaced with another keyboard or removed.
  - Button labels use emoji prefixes to avoid collision with folder names / user text.
  - All constants are UPPERCASE so they're easy to grep and won't be confused
    with regular strings.
"""
from __future__ import annotations

from aiogram.types import (
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

# ── Button label constants ─────────────────────────────────────────────────────
# These exact strings are sent as text messages when the user taps the button.
# The reply_router.py uses these same constants to match incoming messages.

BTN_BROWSE   = "📚 Browse"
BTN_SEARCH   = "🔍 Search"
BTN_CATALOG  = "📖 Catalog"
BTN_STATUS   = "👤 My Status"
BTN_PREMIUM  = "⭐ Premium"
BTN_HELP     = "❓ Help"
BTN_ABOUT    = "ℹ️ About"
BTN_SUPPORT  = "💬 Support"
BTN_BACK     = "🏠 Main Menu"
BTN_CANCEL   = "❌ Cancel"


# ── Internal helper ───────────────────────────────────────────────────────────

def _kb(*rows: list[str], resize: bool = True, one_time: bool = False) -> ReplyKeyboardMarkup:
    """Build a ReplyKeyboardMarkup from a list of rows (each row is a list of labels)."""
    keyboard = [[KeyboardButton(text=label) for label in row] for row in rows]
    return ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=resize,
        one_time_keyboard=one_time,
        input_field_placeholder="Choose an option or type a command…",
    )


# ── Keyboard builders ─────────────────────────────────────────────────────────

def main_menu_keyboard(is_premium: bool = False) -> ReplyKeyboardMarkup:
    """
    Main navigation keyboard — shown to approved users on /start and after
    most state transitions that return to the menu.

    Row layout:
      [📚 Browse]  [🔍 Search]
      [📖 Catalog] [👤 My Status]
      [⭐ Premium] / [❓ Help]  [ℹ️ About]
      [💬 Support]
    """
    rows: list[list[str]] = [
        [BTN_BROWSE, BTN_SEARCH],
        [BTN_CATALOG, BTN_STATUS],
    ]
    if is_premium:
        rows.append([BTN_HELP, BTN_ABOUT])
    else:
        rows.append([BTN_PREMIUM, BTN_HELP])
        rows.append([BTN_ABOUT, BTN_SUPPORT])
    return _kb(*rows)


def back_keyboard() -> ReplyKeyboardMarkup:
    """
    Minimal keyboard shown while browsing categories / viewing a folder card.
    Keeps a single prominent "🏠 Main Menu" button so the user can always escape.
    """
    return _kb(
        [BTN_BACK],
        [BTN_SEARCH],
    )


def cancel_keyboard() -> ReplyKeyboardMarkup:
    """
    Shown during FSM states that need cancellation (e.g. search input, waiting
    for a text response).  The ❌ Cancel button returns to the main menu.
    """
    return _kb(
        [BTN_CANCEL],
        one_time=True,
    )


def download_active_keyboard() -> ReplyKeyboardMarkup:
    """
    Shown while a download is in progress.  The only available action is cancel
    (the inline cancel button is also shown on the progress card).
    """
    return _kb(
        [BTN_CANCEL],
        one_time=True,
    )


def remove_keyboard() -> ReplyKeyboardRemove:
    """Remove the reply keyboard entirely (e.g. after /stop or for pending users)."""
    return ReplyKeyboardRemove()
