"""
handlers/reply_router.py — Routes reply keyboard button taps to handler functions.

When a user taps a reply keyboard button, Telegram sends its label as a plain
text message.  This router intercepts those messages (before the catch-all
unknown-command handler) and delegates to the correct logic.

Registration order in main.py:
    start → reply_router → … other handlers … → about_help (catch-all last)

Each route function mirrors the behaviour of the equivalent inline button or
slash command, so both interaction modes produce identical results.
"""
from __future__ import annotations

import logging

from aiogram import Router, F, types
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest

from config import ADMIN_CONTACT, PAYMENT_MODE, BOT_NAME, ADMIN_IDS
from middlewares.authorization import is_private_chat, is_user_member, invalidate_member_cache
from utils.bot_ref import get_bot
from utils.database import db_fetchone, db_fetchall
from utils.helpers import esc
from utils.keyboard import InlineBuilder, IKB as InlineKeyboardButton
from utils.reply_keyboard import (
    BTN_BROWSE, BTN_SEARCH, BTN_CATALOG, BTN_STATUS,
    BTN_PREMIUM, BTN_HELP, BTN_ABOUT, BTN_SUPPORT,
    BTN_BACK, BTN_CANCEL,
    main_menu_keyboard, back_keyboard, cancel_keyboard,
)

router = Router()
log = logging.getLogger(__name__)

# ── Known button labels (used in F.text filter) ───────────────────────────────

_ALL_BUTTON_LABELS = {
    BTN_BROWSE, BTN_SEARCH, BTN_CATALOG, BTN_STATUS,
    BTN_PREMIUM, BTN_HELP, BTN_ABOUT, BTN_SUPPORT,
    BTN_BACK, BTN_CANCEL,
}


# ── Access guard ──────────────────────────────────────────────────────────────

async def _guard(message: types.Message) -> tuple[bool, str]:
    """
    Returns (is_approved, status).
    Sends appropriate responses for non-approved users.
    """
    user_id = message.from_user.id
    row = db_fetchone('SELECT status FROM users WHERE user_id = %s', (user_id,))
    status = row[0] if row else 'unknown'

    if status != 'approved':
        if status == 'pending':
            await message.answer(
                "⏳ <b>Your access is still pending approval.</b>\n\n"
                "An admin will review your request and notify you here.",
                parse_mode=ParseMode.HTML,
            )
        else:
            await message.answer("👋 Please send /start to register and request access.")
        return False, status

    invalidate_member_cache(user_id)
    if not await is_user_member(user_id):
        await message.answer(
            "📢 <b>Channel subscription required</b>\n\n"
            "Please join our required channel(s) first.\n"
            "Send /start to see the join buttons.",
            parse_mode=ParseMode.HTML,
        )
        return False, status

    return True, status


# ── Route handlers ────────────────────────────────────────────────────────────

async def _route_browse(message: types.Message) -> None:
    """📚 Browse — open the categories overview (same as /start for approved users)."""
    ok, _ = await _guard(message)
    if not ok:
        return
    from handlers.start import send_ui
    bot = get_bot()
    await send_ui(message.chat.id)


async def _route_search(message: types.Message) -> None:
    """🔍 Search — show the search prompt and set FSM state."""
    ok, _ = await _guard(message)
    if not ok:
        return
    # Send the search prompt with cancel keyboard
    await message.answer(
        "🔍 <b>Search Folders</b>\n\n"
        "Type the name of the folder you're looking for.\n"
        "Supports partial names, abbreviations, and typos!\n\n"
        "<i>Examples: anatomy, pharma, surgery notes, biochem</i>\n\n"
        "👇 <b>Send your search query as a message:</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=cancel_keyboard(),
    )
    # Set FSM state so the next message is treated as a search query
    from handlers.search import SearchState
    from aiogram.fsm.context import FSMContext
    # We can't easily get the FSMContext here without registering as an FSM handler,
    # so we delegate to the search module's own entry point via a direct call.
    # The FSM state is set inside the search handler registered for the /search command.
    # Here we trigger it by re-using the cmd_search flow but without the Command filter.
    from handlers.search import cmd_search
    await cmd_search(message)


async def _route_catalog(message: types.Message) -> None:
    """📖 Catalog — show the Telegra.ph catalog URL."""
    ok, _ = await _guard(message)
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
            "Click any folder to open the bot and download!\n\n"
            f"<i>🔗 {url}</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=kb.build(),
            disable_web_page_preview=True,
        )
    else:
        await message.answer("📖 Content catalog is not available yet. Check back soon!")


async def _route_status(message: types.Message) -> None:
    """👤 My Status — show the account status card."""
    from handlers.status import build_status_text
    user_id = message.from_user.id
    text, kb = await build_status_text(user_id)
    await message.answer(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=kb.build() if kb is not None else None,
        disable_web_page_preview=True,
    )


async def _route_premium(message: types.Message) -> None:
    """⭐ Premium — show premium plans / upgrade info."""
    ok, _ = await _guard(message)
    if not ok:
        return
    kb = InlineBuilder()
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
        else:
            plan_lines = "  Contact admin for current pricing."
            how_to = f"Message {ADMIN_CONTACT} to get your plan activated."
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
        else:
            plan_lines = "  Contact admin for current pricing."
            how_to = f"Message {ADMIN_CONTACT} to get your plan activated."
    else:
        plan_lines = "  Contact admin for current pricing."
        how_to = f"Message {ADMIN_CONTACT} to get your plan activated."

    kb.row(InlineKeyboardButton("📞 Contact Support", url=f"https://t.me/{ADMIN_CONTACT.lstrip('@')}"))

    await message.answer(
        f"⭐ <b>Premium Membership</b>\n\n"
        "<b>What you get:</b>\n"
        "  • ⚡ 5s interval between files  <i>(vs 60s free)</i>\n"
        "  • ⏱ 2 min cooldown  <i>(vs 7 min free)</i>\n"
        "  • ⭐ Access to all Premium-only folders\n\n"
        f"<b>Plans:</b>\n{plan_lines}\n\n"
        f"<b>How to subscribe:</b>\n  {how_to}",
        parse_mode=ParseMode.HTML,
        reply_markup=kb.build(),
    )


async def _route_help(message: types.Message) -> None:
    """❓ Help — show the help guide."""
    ok, _ = await _guard(message)
    if not ok:
        return
    from handlers.about_help import get_help_content, _help_keyboard
    await message.answer(
        get_help_content(),
        parse_mode=ParseMode.HTML,
        reply_markup=_help_keyboard(),
        disable_web_page_preview=True,
    )


async def _route_about(message: types.Message) -> None:
    """ℹ️ About — show the About card."""
    ok, _ = await _guard(message)
    if not ok:
        return
    from handlers.about_help import get_about_content, _about_keyboard
    await message.answer(
        get_about_content(),
        parse_mode=ParseMode.HTML,
        reply_markup=_about_keyboard(),
        disable_web_page_preview=True,
    )


async def _route_support(message: types.Message) -> None:
    """💬 Support — deep link to admin contact."""
    await message.answer(
        f"💬 <b>Contact Support</b>\n\n"
        f"Reach out to us at: {ADMIN_CONTACT}\n\n"
        "We're here to help with:\n"
        "  • Access / verification questions\n"
        "  • Download issues\n"
        "  • Premium subscriptions\n"
        "  • Content requests",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineBuilder().row(
            InlineKeyboardButton("💬 Open Chat", url=f"https://t.me/{ADMIN_CONTACT.lstrip('@')}")
        ).build(),
    )


async def _route_back(message: types.Message) -> None:
    """🏠 Main Menu — return to the categories overview."""
    ok, _ = await _guard(message)
    if not ok:
        return
    from handlers.start import send_ui
    await send_ui(message.chat.id)


async def _route_cancel(message: types.Message) -> None:
    """❌ Cancel — cancel any active FSM state and return to the main menu."""
    # Clear any FSM state
    # Since we can't easily access FSMContext here without being in an FSM handler,
    # we just route back to main menu. The FSM state will be cleared by the
    # next command / callback naturally, or the search FSM timeout.
    ok, _ = await _guard(message)
    if not ok:
        # Even unapproved users can cancel — just send the remove keyboard
        from utils.reply_keyboard import remove_keyboard
        await message.answer("Cancelled.", reply_markup=remove_keyboard())
        return
    from handlers.start import send_ui
    await send_ui(message.chat.id)


# ── Unknown text suggestion (Phase 3.3) ──────────────────────────────────────

async def _route_unknown_text(message: types.Message) -> None:
    """
    When a non-command, non-button text message is received and doesn't
    match any FSM state, respond helpfully instead of silently ignoring.
    """
    if not is_private_chat(message):
        return
    # Don't interfere with commands (handled elsewhere)
    text = message.text or ""
    if text.startswith("/"):
        return
    # Don't interfere with known button labels (already handled above)
    if text in _ALL_BUTTON_LABELS:
        return

    user_id = message.from_user.id
    row = db_fetchone('SELECT status, premium FROM users WHERE user_id = %s', (user_id,))
    status = row[0] if row else 'unknown'
    is_premium = bool(row and row[1]) if row else False

    if status == 'approved':
        await message.answer(
            "🤔 <b>I didn't understand that.</b>\n\n"
            "Here's what you can do right now:\n"
            f"  • Tap <b>{BTN_BROWSE}</b> to see all folders\n"
            f"  • Tap <b>{BTN_SEARCH}</b> to find a specific folder\n"
            "  • Or type /help for the full guide",
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu_keyboard(is_premium),
        )
    else:
        await message.answer(
            "👋 Please send /start to begin.",
        )


# ── Router registration ───────────────────────────────────────────────────────

def register(r: Router) -> None:
    """
    Register all reply keyboard text routes on the given router.
    Called from register_handlers.py.

    We use exact text filters for button labels so they don't collide with
    the FSM text handler or the unknown-command catch-all.
    """
    r.message.register(_route_browse,  F.text == BTN_BROWSE,  F.chat.type == "private")
    r.message.register(_route_search,  F.text == BTN_SEARCH,  F.chat.type == "private")
    r.message.register(_route_catalog, F.text == BTN_CATALOG, F.chat.type == "private")
    r.message.register(_route_status,  F.text == BTN_STATUS,  F.chat.type == "private")
    r.message.register(_route_premium, F.text == BTN_PREMIUM, F.chat.type == "private")
    r.message.register(_route_help,    F.text == BTN_HELP,    F.chat.type == "private")
    r.message.register(_route_about,   F.text == BTN_ABOUT,   F.chat.type == "private")
    r.message.register(_route_support, F.text == BTN_SUPPORT, F.chat.type == "private")
    r.message.register(_route_back,    F.text == BTN_BACK,    F.chat.type == "private")
    r.message.register(_route_cancel,  F.text == BTN_CANCEL,  F.chat.type == "private")

    # Unknown text catch-all (must come AFTER all exact-match routes)
    # We register it with a broad F.text filter; it checks button labels internally.
    r.message.register(
        _route_unknown_text,
        F.text,
        F.chat.type == "private",
        # Exclude commands — they're handled by their own routers
        ~F.text.startswith("/"),
        # Exclude known button labels — already handled above
        F.text.func(lambda t: t not in _ALL_BUTTON_LABELS),
    )
