"""
handlers/search.py — Search feature for the Medical Content Bot.

Provides:
  1. FSM-based text search (triggered by 🔍 button or /search command)
  2. Inline query handler: @botname <query> — shows results anywhere in Telegram

Search layers (in priority order):
  1. Exact match (case-insensitive)
  2. Prefix match
  3. Substring / contains match
  4. Full-text search (tsvector, PostgreSQL)
  5. Fuzzy trigram similarity (pg_trgm, if enabled)
"""
import logging

from aiogram import Router, types, F
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    InlineKeyboardMarkup,
    InlineQueryResultArticle,
    InputTextMessageContent,
)

from config import ADMIN_CONTACT
from middlewares.authorization import is_private_chat
from utils.bot_ref import get_bot
from utils.database import db_fetchone, search_folders
from utils.helpers import esc
from utils.keyboard import InlineBuilder, IKB as InlineKeyboardButton

router = Router()
log = logging.getLogger(__name__)

# Minimum query length
_MIN_QUERY_LEN = 2
# How many results to show in the chat UI
_CHAT_RESULT_LIMIT = 15
# How many results to show in inline mode
_INLINE_RESULT_LIMIT = 10


# ─────────────────────────────────────────────────────────────────────────────
# FSM States
# ─────────────────────────────────────────────────────────────────────────────

class SearchState(StatesGroup):
    waiting_for_query = State()


# ─────────────────────────────────────────────────────────────────────────────
# Keyboard helpers
# ─────────────────────────────────────────────────────────────────────────────

def _search_prompt_keyboard() -> InlineKeyboardMarkup:
    kb = InlineBuilder()
    kb.row(InlineKeyboardButton("🔙 Back to Menu", callback_data="cat_main"))
    return kb.build()


def _search_result_keyboard(results) -> InlineKeyboardMarkup:
    kb = InlineBuilder()
    for folder_id, name, file_count, premium, admin_approval, category_name, path, has_children in results:
        file_count = file_count or 0
        icon = "⭐" if premium else ("💰" if admin_approval else "📁")
        label = f"{icon} {name} ({file_count})"
        if len(label) > 36:
            label = label[:33] + "…"
        # Route to sub-folder browser for parent folders, file preview for leaf folders
        callback_data = f"nav:f:{folder_id}:0" if has_children else f"fi:{folder_id}:0"
        kb.row(InlineKeyboardButton(label, callback_data=callback_data))
    kb.row(
        InlineKeyboardButton("🔍 Search Again", callback_data="search"),
        InlineKeyboardButton("🔙 Menu", callback_data="cat_main"),
    )
    return kb.build()


def _no_results_keyboard() -> InlineKeyboardMarkup:
    kb = InlineBuilder()
    kb.row(
        InlineKeyboardButton("🔍 Try Again", callback_data="search"),
        InlineKeyboardButton("🔙 Menu", callback_data="cat_main"),
    )
    return kb.build()


# ─────────────────────────────────────────────────────────────────────────────
# Search entry points
# ─────────────────────────────────────────────────────────────────────────────

async def cmd_search(message: types.Message, state: FSMContext):
    """/search [query] — search folders. If no query given, enters FSM mode."""
    if not is_private_chat(message):
        return

    user_id = message.from_user.id
    user_row = db_fetchone("SELECT status FROM users WHERE user_id = %s", (user_id,))
    if not user_row or user_row[0] != 'approved':
        await message.reply("Please wait for admin approval before searching.")
        return

    # If query was passed inline (e.g. /search anatomy), run immediately
    args = message.text.split(None, 1)[1].strip() if message.text and len(message.text.split(None, 1)) > 1 else ''
    if args:
        await _execute_search(message.chat.id, args, state, reply_fn=message.reply)
        return

    # Otherwise, enter FSM waiting state
    await state.set_state(SearchState.waiting_for_query)
    await message.reply(
        "🔍 <b>Search Folders</b>\n"
        "<b>──────────────────────────────────</b>\n\n"
        "Type the folder name you're looking for.\n"
        "Supports partial names and keywords!\n\n"
        "<i>Examples: anatomy, pharma, surgery</i>\n\n"
        "👇 <b>Type your search query:</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=_search_prompt_keyboard(),
    )


async def cb_search(cq: types.CallbackQuery, state: FSMContext = None):
    """Callback: 🔍 Search button in the main menu."""
    user_id = cq.from_user.id
    user_row = db_fetchone("SELECT status FROM users WHERE user_id = %s", (user_id,))
    if not user_row or user_row[0] != 'approved':
        await cq.answer("Not authorized.", show_alert=True)
        return

    if state is not None:
        await state.set_state(SearchState.waiting_for_query)

    await cq.answer()
    bot = get_bot()
    try:
        from utils.keyboard import InlineBuilder, IKB as IBtn
        kb = InlineBuilder()
        kb.row(IBtn("🔙 Back to Menu", callback_data="cat_main"))
        await bot.edit_message_text(
            chat_id=cq.message.chat.id,
            message_id=cq.message.message_id,
            text=(
                "🔍 <b>Search Folders</b>\n"
                "<b>──────────────────────────────────</b>\n\n"
                "Type the folder name you're looking for.\n"
                "Supports partial names and keywords!\n\n"
                "<i>Examples: anatomy, pharma, surgery</i>\n\n"
                "👇 <b>Send your query as a message:</b>"
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=kb.build(),
        )
    except Exception:
        pass


async def process_search_query(message: types.Message, state: FSMContext):
    """FSM handler: process text sent while in search state."""
    if not is_private_chat(message):
        return

    query = (message.text or '').strip()
    if not query or query.startswith('/'):
        # User sent a command — exit FSM, don't process
        await state.clear()
        return

    await state.clear()
    await _execute_search(message.chat.id, query, state, reply_fn=message.reply)


# ─────────────────────────────────────────────────────────────────────────────
# Core search executor
# ─────────────────────────────────────────────────────────────────────────────

async def _execute_search(chat_id: int, query: str, state: FSMContext, reply_fn=None):
    """Run search and send results."""
    bot = get_bot()

    if len(query) < _MIN_QUERY_LEN:
        text = (
            f"🔍 Query too short.\n\n"
            f"Please enter at least {_MIN_QUERY_LEN} characters."
        )
        if reply_fn:
            await reply_fn(text, reply_markup=_no_results_keyboard())
        else:
            await bot.send_message(chat_id, text, reply_markup=_no_results_keyboard())
        return

    results = search_folders(query, limit=_CHAT_RESULT_LIMIT)

    safe_query = esc(query[:50])

    if not results:
        text = (
            f"🔍 <b>No results for</b> '<code>{safe_query}</code>'\n"
            "<b>──────────────────────────────────</b>\n\n"
            "💡 <b>Suggestions:</b>\n"
            "  • Try a shorter keyword\n"
            "  • Check for typos\n"
            "  • Use subject names\n"
            "  • Browse categories via /start\n\n"
            f"<i>Contact {ADMIN_CONTACT} for help.</i>"
        )
        kb = _no_results_keyboard()
        if reply_fn:
            await reply_fn(text, parse_mode=ParseMode.HTML, reply_markup=kb)
        else:
            await bot.send_message(chat_id, text, parse_mode=ParseMode.HTML, reply_markup=kb)
        return

    # Build result text
    lines = [
        f"🔍 <b>Search results for</b> '<code>{safe_query}</code>'",
        "<b>──────────────────────────────────</b>",
        "",
    ]
    for folder_id, name, file_count, premium, admin_approval, category_name, path, has_children in results:
        file_count = file_count or 0
        icon = "⭐" if premium else ("💰" if admin_approval else "📁")
        cat_tag = f" <i>[{esc(category_name)}]</i>" if category_name else ""
        sub_tag = " 📂" if has_children else ""
        lines.append(f"  {icon} <code>{esc(name)}</code>{cat_tag} ({file_count} files){sub_tag}")

    lines.append(f"\n<i>Showing {len(results)} result(s) — tap to browse or download</i>")
    text = '\n'.join(lines)
    kb = _search_result_keyboard(results)

    if reply_fn:
        await reply_fn(text, parse_mode=ParseMode.HTML, reply_markup=kb)
    else:
        await bot.send_message(chat_id, text, parse_mode=ParseMode.HTML, reply_markup=kb)


# ─────────────────────────────────────────────────────────────────────────────
# Inline query handler: @botname <query>
# ─────────────────────────────────────────────────────────────────────────────

async def inline_search_handler(inline_query: types.InlineQuery):
    """
    Handle @botname <query> inline queries — shows folder results in any chat.
    Each result has a deep link so users can tap and be taken to the bot.
    """
    query = (inline_query.query or '').strip()
    user_id = inline_query.from_user.id

    # Only serve approved users in inline mode
    user_row = db_fetchone("SELECT status FROM users WHERE user_id = %s", (user_id,))
    if not user_row or user_row[0] != 'approved':
        await inline_query.answer(
            results=[],
            cache_time=10,
            is_personal=True,
            switch_pm_text="🔐 Start the bot to get access",
            switch_pm_parameter="start",
        )
        return

    if len(query) < _MIN_QUERY_LEN:
        await inline_query.answer(
            results=[],
            cache_time=5,
            is_personal=True,
            switch_pm_text="🔍 Type at least 2 characters to search",
            switch_pm_parameter="start",
        )
        return

    results_data = search_folders(query, limit=_INLINE_RESULT_LIMIT)

    # Get bot username for deep links
    try:
        bot = get_bot()
        me = await bot.me()
        bot_username = me.username
    except Exception:
        bot_username = ""

    articles = []
    for folder_id, name, file_count, premium, admin_approval, category_name, path, has_children in results_data:
        file_count = file_count or 0
        icon = "⭐" if premium else ("💰" if admin_approval else "📁")
        badge_text = "Premium" if premium else ("Paid" if admin_approval else "Free")
        cat_info = f" · {category_name}" if category_name else ""
        deep_link = f"https://t.me/{bot_username}?start=dl_{folder_id}"

        description = f"{file_count} file{'s' if file_count != 1 else ''} · {badge_text}{cat_info}"

        # The message sent when user taps the result and shares it
        msg_text = (
            f"{icon} <b>{esc(name)}</b>\n"
            f"📄 {file_count} file{'s' if file_count != 1 else ''}\n"
            f"🏷 {badge_text}{cat_info}\n\n"
            f"👉 <a href=\"{deep_link}\">View &amp; Download in Bot</a>"
        )

        articles.append(
            InlineQueryResultArticle(
                id=str(folder_id),
                title=f"{icon} {name}",
                description=description,
                input_message_content=InputTextMessageContent(
                    message_text=msg_text,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                ),
                url=deep_link,
                hide_url=False,
            )
        )

    if not articles:
        await inline_query.answer(
            results=[],
            cache_time=30,
            is_personal=True,
            switch_pm_text=f"🔍 No results for '{query[:30]}'",
            switch_pm_parameter="start",
        )
        return

    await inline_query.answer(
        results=articles,
        cache_time=60,
        is_personal=True,
    )
