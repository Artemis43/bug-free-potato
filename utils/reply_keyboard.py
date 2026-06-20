"""
utils/reply_keyboard.py — Persistent Reply Keyboard system.

Telegram's ReplyKeyboardMarkup places large, always-visible buttons above the
phone keyboard — making all actions discoverable without knowing any commands.

This module defines:
  - Button label constants (single source of truth)
  - Keyboard builders for each bot state
  - Dynamic keyboard builders for category/folder navigation

Design notes:
  - Reply keyboards can only be attached to send_message(), not edit_message_text().
    They persist until explicitly replaced with another keyboard or removed.
  - Button labels use emoji prefixes to avoid collision with folder names / user text.
  - Category and folder buttons are prefixed so they never clash with system buttons.
"""
from __future__ import annotations

from aiogram.types import (
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

# ── System button label constants ─────────────────────────────────────────────
# These exact strings are sent as text messages when the user taps the button.
# The reply_router.py uses these same constants to match incoming messages.

BTN_BROWSE        = "📚 Browse"
BTN_SEARCH        = "🔍 Search"
BTN_CATALOG       = "📖 Catalog"
BTN_STATUS        = "👤 My Status"
BTN_PREMIUM       = "⭐ Premium"
BTN_HELP          = "❓ Help"
BTN_ABOUT         = "ℹ️ About"
BTN_SUPPORT       = "💬 Support"
BTN_BACK_MAIN     = "🏠 Main Menu"
BTN_BACK_CATS     = "🔙 Categories"
BTN_CANCEL        = "❌ Cancel"
BTN_CANCEL_DL     = "❌ Cancel Download"

# Prefix used for category and folder name buttons so they don't clash
CAT_PREFIX    = "📂 "   # category choice buttons
FOLDER_PREFIX = "📁 "   # folder choice buttons

# ── Internal helper ───────────────────────────────────────────────────────────

def _kb(*rows: list[str], resize: bool = True, one_time: bool = False,
        placeholder: str = "Choose an option…") -> ReplyKeyboardMarkup:
    """Build a ReplyKeyboardMarkup from a list of rows (each row is a list of labels)."""
    keyboard = [[KeyboardButton(text=label) for label in row] for row in rows]
    return ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=resize,
        one_time_keyboard=one_time,
        input_field_placeholder=placeholder,
    )


# ── Static keyboard builders ──────────────────────────────────────────────────

def main_menu_keyboard(is_premium: bool = False) -> ReplyKeyboardMarkup:
    """
    Main navigation keyboard shown to approved users.

    Row layout:
      [📚 Browse]  [🔍 Search]
      [📖 Catalog] [👤 My Status]
      [⭐ Premium] [❓ Help]     ← free users
      [ℹ️ About]   [💬 Support]
    """
    rows: list[list[str]] = [
        [BTN_BROWSE, BTN_SEARCH],
        [BTN_CATALOG, BTN_STATUS],
    ]
    if is_premium:
        rows.append([BTN_HELP, BTN_ABOUT])
        rows.append([BTN_SUPPORT])
    else:
        rows.append([BTN_PREMIUM, BTN_HELP])
        rows.append([BTN_ABOUT, BTN_SUPPORT])
    return _kb(*rows, placeholder="Browse, Search, or pick an option…")


def back_keyboard() -> ReplyKeyboardMarkup:
    """Minimal keyboard while browsing — always lets user escape to main menu."""
    return _kb(
        [BTN_BACK_MAIN],
        [BTN_SEARCH],
        placeholder="Tap a folder above, or navigate…",
    )


def cancel_keyboard() -> ReplyKeyboardMarkup:
    """Shown during FSM states needing cancellation (search input etc.)."""
    return _kb(
        [BTN_CANCEL],
        one_time=True,
        placeholder="Type your query, or cancel…",
    )


def download_active_keyboard() -> ReplyKeyboardMarkup:
    """Shown while a download is in progress — prominent cancel button."""
    return _kb(
        [BTN_CANCEL_DL],
        placeholder="Download in progress…",
    )


def remove_keyboard() -> ReplyKeyboardRemove:
    """Remove the reply keyboard entirely (e.g. after /stop or for pending users)."""
    return ReplyKeyboardRemove()


# ── Dynamic keyboard builders ─────────────────────────────────────────────────

def categories_keyboard(categories: list[tuple], uncat_count: int = 0) -> ReplyKeyboardMarkup:
    """
    Build a reply keyboard showing all categories as buttons.
    Each category row: [CAT_PREFIX + emoji + name]
    Bottom rows: [🔍 Search]  [🏠 Main Menu]

    categories: list of (id, name, emoji, folder_count)
    """
    rows: list[list[str]] = []
    for _, name, emoji, folder_count in categories:
        label = f"{CAT_PREFIX}{emoji} {name} ({folder_count})"
        rows.append([label])

    if uncat_count > 0:
        rows.append([f"{CAT_PREFIX}📦 Uncategorized ({uncat_count})"])

    rows.append([BTN_SEARCH, BTN_BACK_MAIN])
    return _kb(*rows, placeholder="Tap a category to browse its folders…")


def folders_keyboard(folders: list[tuple], page: int = 0,
                     page_size: int = 15) -> ReplyKeyboardMarkup:
    """
    Build a reply keyboard showing paginated folders as buttons.
    Each folder: [FOLDER_PREFIX + name]
    Navigation: [◀️ Prev] [Next ▶️]   [🔙 Categories]  [🏠 Main Menu]

    folders: list of (id, name, premium, admin_approval, file_count)
    """
    total_pages = max(1, (len(folders) + page_size - 1) // page_size)
    page = max(0, min(page, total_pages - 1))
    page_folders = folders[page * page_size:(page + 1) * page_size]

    rows: list[list[str]] = []
    for _, name, premium, admin_approval, file_count in page_folders:
        icon = "⭐" if premium else ("💰" if admin_approval else "")
        label = f"{FOLDER_PREFIX}{name}"
        if icon:
            label += f" {icon}"
        # Truncate if too long for a button (Telegram limit ~50 chars visible)
        if len(label) > 48:
            label = label[:45] + "…"
        rows.append([label])

    # Pagination row
    nav = []
    if page > 0:
        nav.append(f"◀️ Page {page}")
    if page < total_pages - 1:
        nav.append(f"Page {page + 2} ▶️")
    if nav:
        rows.append(nav)

    rows.append([BTN_BACK_CATS, BTN_BACK_MAIN])
    return _kb(*rows, placeholder="Tap a folder to download, or go back…")
