"""
handlers/reply_router.py — Routes reply keyboard button taps to handler functions.

When a user taps a reply keyboard button, Telegram sends its label as a plain
text message. This router intercepts those messages and delegates to the correct
logic, including a full FSM-based category → folder browse flow.

FSM States
----------
BrowseState.selecting_category : User is looking at the category keyboard.
BrowseState.selecting_folder   : User has picked a category; folder keyboard shown.
                                  FSM data holds: category_id, category_name,
                                  category_emoji, all_folders list, current_page.

Registration order in main.py:
    start → reply_router → … other handlers … → about_help (catch-all last)
"""
from __future__ import annotations

import asyncio
import logging

from aiogram import Router, F, types
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from config import ADMIN_CONTACT, PAYMENT_MODE, BOT_NAME
from middlewares.authorization import is_private_chat, is_user_member, invalidate_member_cache
from utils.bot_ref import get_bot
from utils.database import db_fetchone, db_fetchall
from utils.helpers import esc
from utils.keyboard import InlineBuilder, IKB as InlineKeyboardButton
from utils.reply_keyboard import (
    BTN_BROWSE, BTN_SEARCH, BTN_CATALOG, BTN_STATUS,
    BTN_PREMIUM, BTN_HELP, BTN_ABOUT, BTN_SUPPORT,
    BTN_BACK_MAIN, BTN_BACK_CATS, BTN_CANCEL, BTN_CANCEL_DL,
    CAT_PREFIX, FOLDER_PREFIX,
    main_menu_keyboard, cancel_keyboard, download_active_keyboard,
    categories_keyboard, folders_keyboard,
)
import utils.progress as progress

router = Router()
log = logging.getLogger(__name__)

_PAGE_SIZE = 15  # folders per page in the reply keyboard


# ── FSM States ────────────────────────────────────────────────────────────────

class BrowseState(StatesGroup):
    selecting_category = State()
    selecting_folder   = State()


# ── System button label set (used to filter unknown-text catch-all) ────────────

_SYSTEM_LABELS = {
    BTN_BROWSE, BTN_SEARCH, BTN_CATALOG, BTN_STATUS,
    BTN_PREMIUM, BTN_HELP, BTN_ABOUT, BTN_SUPPORT,
    BTN_BACK_MAIN, BTN_BACK_CATS, BTN_CANCEL, BTN_CANCEL_DL,
}


# ── Access guard ──────────────────────────────────────────────────────────────

async def _guard(message: types.Message) -> tuple[bool, str, bool]:
    """Returns (is_approved, status, is_premium)."""
    user_id = message.from_user.id
    row = db_fetchone('SELECT status, premium FROM users WHERE user_id = %s', (user_id,))
    status = row[0] if row else 'unknown'
    is_premium = bool(row and row[1]) if row else False

    if status != 'approved':
        if status == 'pending':
            await message.answer(
                "⏳ <b>Your access is still pending approval.</b>\n\n"
                "An admin will review your request and notify you here.",
                parse_mode=ParseMode.HTML,
            )
        else:
            await message.answer("👋 Please send /start to register and request access.")
        return False, status, False

    invalidate_member_cache(user_id)
    if not await is_user_member(user_id):
        await message.answer(
            "📢 <b>Channel subscription required</b>\n\n"
            "Please join our required channel(s) first.\n"
            "Send /start to see the join buttons.",
            parse_mode=ParseMode.HTML,
        )
        return False, status, False

    return True, status, is_premium


# ── Browse flow: show categories ──────────────────────────────────────────────

async def _route_browse(message: types.Message, state: FSMContext) -> None:
    """📚 Browse — show all categories as reply keyboard buttons."""
    ok, _, is_premium = await _guard(message)
    if not ok:
        return

    categories = db_fetchall(
        "SELECT id, name, emoji, COUNT(f.id) AS folder_count "
        "FROM categories c "
        "LEFT JOIN folders f ON f.category_id = c.id AND f.parent_id IS NULL "
        "GROUP BY c.id ORDER BY c.sort_order, c.name"
    )
    uncat = db_fetchone(
        "SELECT COUNT(*) FROM folders WHERE category_id IS NULL AND parent_id IS NULL"
    )
    uncat_count = uncat[0] if uncat else 0

    if not categories and uncat_count == 0:
        await message.answer(
            "📭 <b>No categories yet.</b>\n\nContent is being added — check back soon!",
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu_keyboard(is_premium),
        )
        return

    await state.set_state(BrowseState.selecting_category)
    await state.update_data(categories=categories, uncat_count=uncat_count)

    # Build text summary
    lines = ["📚 <b>Browse by Category</b>\n"]
    for _, name, emoji, count in categories:
        lines.append(f"  {emoji} <b>{esc(name)}</b> — {count} folder{'s' if count != 1 else ''}")
    if uncat_count:
        lines.append(f"  📦 <b>Uncategorized</b> — {uncat_count} folder{'s' if uncat_count != 1 else ''}")
    lines.append("\n👇 <i>Tap a category below to see its folders:</i>")

    await message.answer(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=categories_keyboard(categories, uncat_count),
    )


# ── Browse flow: category selected → show folders ─────────────────────────────

async def _handle_category_selection(message: types.Message, state: FSMContext) -> None:
    """User tapped a category button — show its folders."""
    text = message.text or ""
    data = await state.get_data()
    categories: list = data.get("categories", [])
    uncat_count: int = data.get("uncat_count", 0)

    # Strip the category prefix to get the label we stored
    label = text[len(CAT_PREFIX):]  # e.g. "🩺 Anatomy (5)" or "📦 Uncategorized (3)"

    # Match against categories
    matched_cat_id = None
    matched_name   = None
    matched_emoji  = None

    for cat_id, cat_name, emoji, count in categories:
        expected = f"{emoji} {cat_name} ({count})"
        if label.strip() == expected.strip():
            matched_cat_id = cat_id
            matched_name   = cat_name
            matched_emoji  = emoji
            break

    # Check for Uncategorized
    is_uncat = False
    if matched_cat_id is None and label.startswith("📦 Uncategorized"):
        is_uncat = True
        matched_cat_id = 0
        matched_name   = "Uncategorized"
        matched_emoji  = "📦"

    if matched_cat_id is None:
        # Tap didn't match — refresh categories view
        await _route_browse(message, state)
        return

    # Fetch folders for this category
    if is_uncat:
        all_folders = db_fetchall("""
            SELECT f.id, f.name, f.premium, f.admin_approval, COUNT(fi.id) AS file_count
            FROM folders f
            LEFT JOIN files fi ON fi.folder_id = f.id
            WHERE f.category_id IS NULL AND f.parent_id IS NULL
            GROUP BY f.id ORDER BY f.name
        """)
    else:
        all_folders = db_fetchall("""
            SELECT f.id, f.name, f.premium, f.admin_approval, COUNT(fi.id) AS file_count
            FROM folders f
            LEFT JOIN files fi ON fi.folder_id = f.id
            WHERE f.category_id = %s AND f.parent_id IS NULL
            GROUP BY f.id ORDER BY f.name
        """, (matched_cat_id,))

    if not all_folders:
        await message.answer(
            f"{matched_emoji} <b>{esc(matched_name)}</b>\n\n"
            "📭 No folders in this category yet.",
            parse_mode=ParseMode.HTML,
            reply_markup=categories_keyboard(categories, uncat_count),
        )
        return

    await state.set_state(BrowseState.selecting_folder)
    await state.update_data(
        category_id=matched_cat_id,
        category_name=matched_name,
        category_emoji=matched_emoji,
        all_folders=all_folders,
        current_page=0,
        categories=categories,
        uncat_count=uncat_count,
    )

    await _send_folder_keyboard(message, state, matched_emoji, matched_name, all_folders, 0)


async def _send_folder_keyboard(
    message: types.Message, state: FSMContext,
    cat_emoji: str, cat_name: str,
    all_folders: list, page: int,
) -> None:
    """Send the folder selection keyboard for a category page."""
    total = len(all_folders)
    total_pages = max(1, (total + _PAGE_SIZE - 1) // _PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    page_folders = all_folders[page * _PAGE_SIZE:(page + 1) * _PAGE_SIZE]

    lines = [
        f"{cat_emoji} <b>{esc(cat_name)}</b>"
        f" — {total} folder{'s' if total != 1 else ''}"
        f" (page {page + 1}/{total_pages})\n"
    ]
    for fid, fname, premium, paid, fcount in page_folders:
        fcount = fcount or 0
        tag = " ⭐" if premium else (" 💰" if paid else "")
        lines.append(f"  📁 <code>{esc(fname)}</code>{tag} — {fcount} file{'s' if fcount != 1 else ''}")

    lines.append("\n👇 <i>Tap a folder to download all its files:</i>")

    await message.answer(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=folders_keyboard(all_folders, page, _PAGE_SIZE),
    )


# ── Browse flow: folder selected → start download ─────────────────────────────

async def _handle_folder_selection(message: types.Message, state: FSMContext) -> None:
    """User tapped a folder button — trigger download."""
    text = message.text or ""
    data = await state.get_data()
    all_folders: list = data.get("all_folders", [])
    current_page: int = data.get("current_page", 0)
    categories = data.get("categories", [])
    uncat_count = data.get("uncat_count", 0)

    # Handle pagination buttons
    if text.startswith("◀️ Page "):
        try:
            page = int(text.split("◀️ Page ")[1]) - 1
        except (ValueError, IndexError):
            page = 0
        await state.update_data(current_page=page)
        cat_emoji = data.get("category_emoji", "📂")
        cat_name  = data.get("category_name", "")
        await _send_folder_keyboard(message, state, cat_emoji, cat_name, all_folders, page)
        return

    if text.startswith("Page ") and text.endswith(" ▶️"):
        try:
            page = int(text.split("Page ")[1].split(" ▶️")[0]) - 1
        except (ValueError, IndexError):
            page = 0
        await state.update_data(current_page=page)
        cat_emoji = data.get("category_emoji", "📂")
        cat_name  = data.get("category_name", "")
        await _send_folder_keyboard(message, state, cat_emoji, cat_name, all_folders, page)
        return

    # Strip folder prefix to get name
    label = text[len(FOLDER_PREFIX):]  # e.g. "Gray's Anatomy ⭐"

    # Match by prefix — strip badge suffix if present
    for badge in [" ⭐", " 💰", ""]:
        clean = label.rstrip()
        if clean.endswith(badge) and badge:
            clean = clean[: -len(badge)].rstrip()
        # Also handle truncated names (ending with …)
        matched_folder = None
        for fid, fname, premium, paid, fcount in all_folders:
            btn_name = fname
            if premium:
                btn_name_full = f"{FOLDER_PREFIX}{fname} ⭐"
            elif paid:
                btn_name_full = f"{FOLDER_PREFIX}{fname} 💰"
            else:
                btn_name_full = f"{FOLDER_PREFIX}{fname}"
            if len(btn_name_full) > 48:
                btn_name_full = btn_name_full[:45] + "…"
            if text.strip() == btn_name_full.strip():
                matched_folder = (fid, fname, premium, paid, fcount)
                break
        if matched_folder:
            break

    if not matched_folder:
        await message.answer(
            "🤔 Couldn't identify that folder. Please tap a button from the list.",
            reply_markup=folders_keyboard(all_folders, current_page, _PAGE_SIZE),
        )
        return

    fid, fname, premium, paid, fcount = matched_folder

    # Clear FSM state before starting download
    await state.clear()

    user_id = message.from_user.id
    bot = get_bot()

    async def reply_fn(text_msg, **kwargs):
        await bot.send_message(message.chat.id, text_msg, **kwargs)

    from handlers.download import _check_and_start_download
    await _check_and_start_download(
        bot, message.chat.id, user_id, fid, reply_fn
    )


# ── Route handlers (non-browse buttons) ──────────────────────────────────────

async def _route_search(message: types.Message, state: FSMContext) -> None:
    """🔍 Search — set FSM and show search prompt."""
    ok, _, _ = await _guard(message)
    if not ok:
        return
    from handlers.search import cmd_search
    await cmd_search(message, state)


async def _route_catalog(message: types.Message, state: FSMContext) -> None:
    """📖 Catalog — show the Telegra.ph catalog URL."""
    ok, _, _ = await _guard(message)
    if not ok:
        return
    from utils.catalog import get_catalog_url, generate_catalog
    bot = get_bot()
    url = get_catalog_url()
    if not url:
        me = await bot.me()
        url = await generate_catalog(me.username)
    if url:
        kb = InlineBuilder()
        kb.row(InlineKeyboardButton("📖 Open Catalog", url=url))
        await message.answer(
            "📖 <b>Full Content Catalog</b>\n\n"
            "Browse all available folders organised by category.\n"
            f"<i>🔗 {url}</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=kb.build(),
            disable_web_page_preview=True,
        )
    else:
        await message.answer("📖 Content catalog is not available yet. Check back soon!")


async def _route_status(message: types.Message, state: FSMContext) -> None:
    """👤 My Status — show account status card."""
    from handlers.status import build_status_text
    text, kb = await build_status_text(message.from_user.id)
    await message.answer(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=kb.build() if kb is not None else None,
        disable_web_page_preview=True,
    )


async def _route_premium(message: types.Message, state: FSMContext) -> None:
    """⭐ Premium — show upgrade plans."""
    ok, _, _ = await _guard(message)
    if not ok:
        return
    kb = InlineBuilder()
    plan_lines = "  Contact admin for current pricing."
    how_to = f"Message {ADMIN_CONTACT} to get your plan activated."

    if PAYMENT_MODE == 'stars':
        from handlers.payment_stars import get_stars_plans
        plans = get_stars_plans()
        if plans:
            for plan_id, name, amount_stars, days in plans:
                kb.add(InlineKeyboardButton(
                    f"⭐ {name} — {amount_stars} Stars ({days} days)",
                    callback_data=f"stars_plan:{plan_id}",
                ))
            plan_lines = "\n".join(
                f"  • <b>{name}</b> — {amount_stars} ⭐ / {days} days"
                for _, name, amount_stars, days in plans
            )
            how_to = "Tap a plan below to pay with Telegram Stars."
    elif PAYMENT_MODE == 'razorpay':
        from handlers.payment import _get_plans, _fmt_inr
        plans = _get_plans()
        if plans:
            for plan_id, name, amount_paise, days in plans:
                kb.add(InlineKeyboardButton(
                    f"💳 {name} — {_fmt_inr(amount_paise)} ({days} days)",
                    callback_data=f"pay_plan:{plan_id}",
                ))
            plan_lines = "\n".join(
                f"  • <b>{name}</b> — {_fmt_inr(amount_paise)} / {days} days"
                for _, name, amount_paise, days in plans
            )
            how_to = "Tap a plan below to pay via UPI / Card / Net Banking."

    kb.row(InlineKeyboardButton("📞 Contact Support",
                                url=f"https://t.me/{ADMIN_CONTACT.lstrip('@')}"))
    await message.answer(
        "⭐ <b>Premium Membership</b>\n\n"
        "<b>What you get:</b>\n"
        "  • ⚡ 5s interval between files  <i>(vs 60s free)</i>\n"
        "  • ⏱ 2 min cooldown  <i>(vs 7 min free)</i>\n"
        "  • ⭐ Access to all Premium-only folders\n\n"
        f"<b>Plans:</b>\n{plan_lines}\n\n"
        f"<b>How to subscribe:</b>\n  {how_to}",
        parse_mode=ParseMode.HTML,
        reply_markup=kb.build(),
    )


async def _route_help(message: types.Message, state: FSMContext) -> None:
    """❓ Help — show the help guide."""
    ok, _, _ = await _guard(message)
    if not ok:
        return
    from handlers.about_help import get_help_content, _help_keyboard
    await message.answer(
        get_help_content(),
        parse_mode=ParseMode.HTML,
        reply_markup=_help_keyboard(),
        disable_web_page_preview=True,
    )


async def _route_about(message: types.Message, state: FSMContext) -> None:
    """ℹ️ About — show the About card."""
    ok, _, _ = await _guard(message)
    if not ok:
        return
    from handlers.about_help import get_about_content, _about_keyboard
    await message.answer(
        get_about_content(),
        parse_mode=ParseMode.HTML,
        reply_markup=_about_keyboard(),
        disable_web_page_preview=True,
    )


async def _route_support(message: types.Message, state: FSMContext) -> None:
    """💬 Support — link to admin contact."""
    await message.answer(
        f"💬 <b>Contact Support</b>\n\n"
        f"Reach out to us at: {ADMIN_CONTACT}\n\n"
        "We help with:\n"
        "  • Access / verification questions\n"
        "  • Download issues\n"
        "  • Premium subscriptions\n"
        "  • Content requests",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineBuilder().row(
            InlineKeyboardButton("💬 Open Chat",
                                 url=f"https://t.me/{ADMIN_CONTACT.lstrip('@')}")
        ).build(),
    )


async def _route_back_main(message: types.Message, state: FSMContext) -> None:
    """🏠 Main Menu — return to the categories overview and clear FSM."""
    await state.clear()
    ok, _, is_premium = await _guard(message)
    if not ok:
        return
    from handlers.start import send_ui
    await send_ui(message.chat.id)


async def _route_back_cats(message: types.Message, state: FSMContext) -> None:
    """🔙 Categories — go back to category selection from folder view."""
    await state.clear()
    ok, _, _ = await _guard(message)
    if not ok:
        return
    await _route_browse(message, state)


async def _route_cancel(message: types.Message, state: FSMContext) -> None:
    """❌ Cancel — cancel any FSM state and return to main menu."""
    await state.clear()
    ok, _, is_premium = await _guard(message)
    if not ok:
        return
    from handlers.start import send_ui
    await send_ui(message.chat.id)


async def _route_cancel_download(message: types.Message, state: FSMContext) -> None:
    """
    ❌ Cancel Download — signals the active download to stop.
    This is the REPLY KEYBOARD counterpart to the inline ❌ Cancel Download button.
    It sets the cancel flag in the progress tracker so the download loop stops
    after the current file.
    """
    chat_id = message.chat.id
    if progress.is_downloading(chat_id):
        progress.request_cancel(chat_id)
        await message.answer(
            "⏹ <b>Cancellation requested.</b>\n\n"
            "The download will stop after the current file.\n"
            "Files already sent will be auto-deleted on schedule.",
            parse_mode=ParseMode.HTML,
        )
    else:
        # No active download — just return to main menu
        ok, _, is_premium = await _guard(message)
        if not ok:
            return
        from handlers.start import send_ui
        await send_ui(chat_id)


# ── Unknown text suggestion ───────────────────────────────────────────────────

async def _route_unknown_text(message: types.Message, state: FSMContext) -> None:
    """
    When a non-command, non-button text message is received outside of any FSM
    state, respond helpfully instead of silently ignoring.
    """
    if not is_private_chat(message):
        return
    text = message.text or ""
    if text.startswith("/") or text in _SYSTEM_LABELS:
        return

    user_id = message.from_user.id
    row = db_fetchone('SELECT status, premium FROM users WHERE user_id = %s', (user_id,))
    status     = row[0] if row else 'unknown'
    is_premium = bool(row and row[1]) if row else False

    if status == 'approved':
        await message.answer(
            "🤔 <b>I didn't understand that.</b>\n\n"
            f"  • Tap <b>{BTN_BROWSE}</b> to browse all content categories\n"
            f"  • Tap <b>{BTN_SEARCH}</b> to find a specific folder\n"
            "  • Type /help for the full guide",
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu_keyboard(is_premium),
        )
    else:
        await message.answer("👋 Please send /start to begin.")


# ── Router registration ───────────────────────────────────────────────────────

def register(r: Router) -> None:
    """
    Register all reply keyboard text routes.
    Called from register_handlers.py.

    Order matters:
    1. Exact system button matches (highest priority)
    2. FSM-state handlers for category/folder navigation (registered with state filter)
    3. Unknown-text catch-all (last)
    """
    # ── System buttons (exact match, any FSM state) ───────────────────────────
    # We pass state=FSMContext so handlers can clear/read FSM state
    r.message.register(_route_cancel_download, F.text == BTN_CANCEL_DL, F.chat.type == "private")
    r.message.register(_route_cancel,   F.text == BTN_CANCEL,    F.chat.type == "private")
    r.message.register(_route_back_main,F.text == BTN_BACK_MAIN, F.chat.type == "private")
    r.message.register(_route_back_cats,F.text == BTN_BACK_CATS, F.chat.type == "private")
    r.message.register(_route_browse,   F.text == BTN_BROWSE,    F.chat.type == "private")
    r.message.register(_route_search,   F.text == BTN_SEARCH,    F.chat.type == "private")
    r.message.register(_route_catalog,  F.text == BTN_CATALOG,   F.chat.type == "private")
    r.message.register(_route_status,   F.text == BTN_STATUS,    F.chat.type == "private")
    r.message.register(_route_premium,  F.text == BTN_PREMIUM,   F.chat.type == "private")
    r.message.register(_route_help,     F.text == BTN_HELP,      F.chat.type == "private")
    r.message.register(_route_about,    F.text == BTN_ABOUT,     F.chat.type == "private")
    r.message.register(_route_support,  F.text == BTN_SUPPORT,   F.chat.type == "private")

    # ── FSM state handlers ────────────────────────────────────────────────────
    # Intercept category button taps while in selecting_category state
    r.message.register(
        _handle_category_selection,
        BrowseState.selecting_category,
        F.text.startswith(CAT_PREFIX),
        F.chat.type == "private",
    )
    # Intercept folder button taps (and pagination) while in selecting_folder state
    r.message.register(
        _handle_folder_selection,
        BrowseState.selecting_folder,
        F.chat.type == "private",
        F.text.func(lambda t: (
            t.startswith(FOLDER_PREFIX) or
            t.startswith("◀️ Page ") or
            (t.startswith("Page ") and t.endswith(" ▶️"))
        )),
    )

    # ── Unknown text catch-all ────────────────────────────────────────────────
    r.message.register(
        _route_unknown_text,
        F.text,
        F.chat.type == "private",
        ~F.text.startswith("/"),
        F.text.func(lambda t: t not in _SYSTEM_LABELS),
    )
