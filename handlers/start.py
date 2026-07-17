from utils.keyboard import InlineBuilder
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest
from aiogram import Router
from aiogram.enums import ParseMode, ChatAction
from utils.bot_ref import get_bot
import asyncio
import logging
from aiogram import types
from aiogram.types import InlineKeyboardMarkup
from utils.keyboard import IKB as InlineKeyboardButton
from middlewares.authorization import is_private_chat, is_user_member, get_channel_title, invalidate_member_cache
from utils.database import (
    add_user_to_db, db_fetchone, db_execute, db_fetchall,
    get_child_folders, get_child_categories,
    get_folder_breadcrumb, get_category_breadcrumb,
    get_subtree_file_count, toggle_user_favorite,
    get_user_favorites, get_recent_downloads, get_recently_added_folders,
    record_download_history, get_folder_direct_file_count,
)
from utils.helpers import notify_admins, esc
from config import REQUIRED_CHANNELS, STICKER_ID, ADMIN_IDS, ADMIN_CONTACT, PAYMENT_MODE, BOT_NAME, REQUIRE_APPROVAL, SEND_STICKER
from datetime import datetime, timedelta

router = Router()

log = logging.getLogger(__name__)

# Global throttle for auto-sync
last_sync_time = None
sync_lock = asyncio.Lock()

# Folders per page inside a category view
_PAGE_SIZE = 20

# In-memory pending folder deletions: { user_id: (folder_name, folder_id) }
_pending_deletions: dict = {}


# ─────────────────────────────────────────────────────────────────────────────
# Sticker helper
# ─────────────────────────────────────────────────────────────────────────────

async def send_sticker_safe(bot, chat_id: int, delay: float = 2.0):
    if not SEND_STICKER or not STICKER_ID:
        return None
    try:
        msg = await bot.send_sticker(chat_id, STICKER_ID)
        await asyncio.sleep(delay)
        await bot.delete_message(chat_id, msg.message_id)
        return msg
    except TelegramBadRequest as e:
        logging.warning(f"Sticker send failed (bad file_id?): {e}")
    except Exception as e:
        logging.warning(f"Sticker send failed: {e}")
    return None


# ─────────────────────────────────────────────────────────────────────────────
# UI builder (HTML, paginated)
# ─────────────────────────────────────────────────────────────────────────────

async def send_ui(chat_id: int, message_id: int = None,
                  is_returning: bool = False, page: int = 0):
    """
    Main menu: show categories overview with folder counts.
    This replaces the old flat folder list.
    """
    global last_sync_time

    bot = get_bot()
    chat = await bot.get_chat(chat_id)
    chat_name = esc(chat.full_name or chat.username or str(chat_id))

    user_data = db_fetchone(
        'SELECT premium, premium_expiration FROM users WHERE user_id = %s',
        (chat_id,)
    )
    is_premium_user    = bool(user_data and user_data[0])
    premium_expiration = user_data[1] if is_premium_user else None

    # ── Header ────────────────────────────────────────────────────────────────
    greeting = f"<b>{chat_name}</b>"
    text = (
        f"👋 Welcome, {greeting}!\n"
        f"🏥 <b>{BOT_NAME}</b> ✨\n"
        f"<b>──────────────────────────────────</b>\n"
    )

    if is_premium_user and premium_expiration:
        exp = premium_expiration
        if hasattr(exp, 'tzinfo') and exp.tzinfo:
            exp = exp.replace(tzinfo=None)
        days_left = (exp - datetime.now()).days
        text += f"⭐ <b>Premium Access Active</b>\n🕒 Expires in: <code>{days_left} day(s)</code>\n"
    elif is_premium_user:
        text += "⭐ <b>Premium Access Active</b>\n🕒 Lifetime access\n"
    else:
        text += (
            "🔓 <b>Free Tier Active</b>\n"
            "💡 Upgrade to Premium for max download speed\n"
            "   & no cooldowns.\n"
        )
    text += f"<b>──────────────────────────────────</b>\n\n"

    # ── Fetch categories ──────────────────────────────────────────────────────
    categories = db_fetchall("""
        SELECT c.id, c.name, c.emoji,
               (SELECT COUNT(*) FROM folders f WHERE f.category_id = c.id AND f.parent_id IS NULL) AS folder_count,
               (SELECT COUNT(*) FROM categories sc WHERE sc.parent_id = c.id) AS subcat_count
        FROM categories c
        WHERE c.parent_id IS NULL
        GROUP BY c.id
        ORDER BY c.sort_order, c.name
    """)

    uncat_count = db_fetchone(
        "SELECT COUNT(*) FROM folders WHERE category_id IS NULL AND parent_id IS NULL"
    )[0]

    # 🔥 Trending: top 3 folders by download_count
    trending = db_fetchall("""
        SELECT f.id, f.name, f.download_count
        FROM folders f
        WHERE f.parent_id IS NULL AND f.download_count > 0
        ORDER BY f.download_count DESC
        LIMIT 3
    """)

    keyboard = InlineBuilder()
    has_content = bool(categories) or uncat_count > 0

    if not has_content:
        text += (
            "📂 <b>No folders yet</b>\n\n"
            "Content is being added — check back soon!\n"
            "Tap the button below to refresh."
        )
        keyboard.add(InlineKeyboardButton("🔄 Refresh", callback_data='cat_main'))

        now = datetime.now()
        if last_sync_time is None or (now - last_sync_time) >= timedelta(minutes=20):
            async with sync_lock:
                if last_sync_time is None or (datetime.now() - last_sync_time) >= timedelta(minutes=20):
                    last_sync_time = datetime.now()
                    logging.info("Auto-sync triggered.")
    else:
        text += "📚 <b>Browse by Category:</b>\n\n"

        for cat_id, cat_name, emoji, folder_count, subcat_count in categories:
            parts = []
            if subcat_count > 0:
                parts.append(f"{subcat_count} sub")
            if folder_count > 0 or not parts:
                parts.append(f"{folder_count} fld")
            desc = "+".join(parts)
            text += f"  {emoji} <b>{esc(cat_name)}</b> ({desc})\n"

        if uncat_count > 0:
            text += f"  📦 <b>Uncategorized</b> ({uncat_count} fld)\n"

        if trending:
            text += "\n🔥 <b>Trending:</b>\n"
            for fid, fname, dl_count in trending:
                text += f"  📥 <code>{esc(fname[:30])}</code> — {dl_count} downloads\n"

        text += "\n"

        # Category buttons (one per row)
        for cat_id, cat_name, emoji, folder_count, subcat_count in categories:
            parts = []
            if subcat_count > 0:
                parts.append(f"{subcat_count} sub")
            if folder_count > 0 or not parts:
                parts.append(f"{folder_count} fld")
            desc = "+".join(parts)
            label = f"{emoji} {cat_name} ({desc})"
            if len(label) > 36:
                label = label[:33] + "…"
            keyboard.row(InlineKeyboardButton(label, callback_data=f"nav:c:{cat_id}:0"))

        if uncat_count > 0:
            keyboard.row(InlineKeyboardButton(
                f"📦 Uncategorized ({uncat_count})",
                callback_data="nav:c:0:0"  # category_id=0 means uncategorized
            ))

        # Utility buttons
        keyboard.row(InlineKeyboardButton("🔍 Search Folders", callback_data="search"))
        
        # Get or generate catalog URL to link directly to it
        from utils.catalog import get_catalog_url, generate_catalog
        catalog_url = get_catalog_url()
        if not catalog_url:
            try:
                me = await bot.me()
                catalog_url = await generate_catalog(me.username)
            except Exception as e:
                logging.warning(f"Failed to auto-generate catalog in send_ui: {e}")

        if catalog_url:
            keyboard.row(InlineKeyboardButton("📖 Full Content Catalog", url=catalog_url))
        else:
            keyboard.row(InlineKeyboardButton("📖 Full Content Catalog", callback_data="catalog"))

        keyboard.row(InlineKeyboardButton("🔄 Refresh", callback_data="cat_main"))

        if not is_premium_user:
            keyboard.row(InlineKeyboardButton("⭐ Get Premium", callback_data="info_premium"))

        # New UX features row
        keyboard.row(
            InlineKeyboardButton("⭐ Favorites",    callback_data="fav_list"),
            InlineKeyboardButton("📥 Recent",      callback_data="hist_list"),
            InlineKeyboardButton("🆕 New",          callback_data="new_list"),
        )

        keyboard.row(
            InlineKeyboardButton("📖 About Us", callback_data="info_about"),
            InlineKeyboardButton("❓ Help Guide", callback_data="info_help")
        )
        keyboard.row(InlineKeyboardButton("💬 Contact Support", url=f"https://t.me/{ADMIN_CONTACT.lstrip('@')}"))

    try:
        if message_id:
            await bot.edit_message_text(
                chat_id=chat_id, message_id=message_id,
                text=text, reply_markup=keyboard.build(), parse_mode=ParseMode.HTML
            )
        else:
            await bot.send_message(chat_id, text, reply_markup=keyboard.build(), parse_mode=ParseMode.HTML)
    except TelegramBadRequest:
        pass


async def send_category_ui(chat_id: int, category_id: int, message_id: int = None, page: int = 0):
    """
    Thin compatibility shim — all category navigation now goes through
    send_hierarchy_ui so sub-categories and sub-folders are shown correctly.
    """
    await send_hierarchy_ui(chat_id, 'c', category_id, message_id, page)


# ─────────────────────────────────────────────────────────────────────────────
# Individual callback handlers (module-level for dict-dispatch)
# ─────────────────────────────────────────────────────────────────────────────

async def _cb_page(cq: types.CallbackQuery, bot, user_id: int) -> None:
    """pg: callback — now used only as a fallback refresh (redirects to cat_main)."""
    try:
        page = int(cq.data.split(':', 1)[1])
    except (ValueError, IndexError):
        page = 0

    user_row = db_fetchone('SELECT status FROM users WHERE user_id = %s', (user_id,))
    if REQUIRE_APPROVAL and (user_row[0] if user_row else 'pending') != 'approved':
        await bot.answer_callback_query(
            cq.id, "You are not yet approved. Please wait for admin approval.", show_alert=True
        )
        return

    invalidate_member_cache(user_id)
    if not await is_user_member(user_id):
        await cq.answer("Please join the required channels first.")
        return

    await cq.answer()
    # Redirect to new category overview
    await send_ui(user_id, cq.message.message_id, is_returning=True)


async def _cb_category(cq: types.CallbackQuery, bot, user_id: int) -> None:
    """cat:<category_id>:<page> — show folders/sub-categories in a category (hierarchical)."""
    try:
        _, cat_id_str, page_str = cq.data.split(':', 2)
        category_id = int(cat_id_str)
        page        = int(page_str)
    except (ValueError, IndexError):
        await cq.answer("Invalid category.")
        return

    user_row = db_fetchone('SELECT status FROM users WHERE user_id = %s', (user_id,))
    if REQUIRE_APPROVAL and (user_row[0] if user_row else 'pending') != 'approved':
        await cq.answer("Not yet approved.", show_alert=True)
        return

    invalidate_member_cache(user_id)
    if not await is_user_member(user_id):
        await cq.answer("Please join the required channels first.")
        return

    await cq.answer()
    # Route all category navigation through the unified hierarchy UI
    await send_hierarchy_ui(user_id, 'c', category_id, cq.message.message_id, page)


async def _cb_category_main(cq: types.CallbackQuery, bot, user_id: int) -> None:
    """cat_main — go back to the categories overview (main menu)."""
    user_row = db_fetchone('SELECT status FROM users WHERE user_id = %s', (user_id,))
    if REQUIRE_APPROVAL and (user_row[0] if user_row else 'pending') != 'approved':
        await cq.answer("Not yet approved.", show_alert=True)
        return

    invalidate_member_cache(user_id)
    if not await is_user_member(user_id):
        await cq.answer("Please join the required channels first.")
        return

    await cq.answer()
    await send_ui(user_id, cq.message.message_id, is_returning=True)
async def _cb_search(cq: types.CallbackQuery, bot, user_id: int) -> None:
    """search — show the search prompt. The search router handles the FSM state."""
    await cq.answer()
    try:
        from utils.keyboard import InlineBuilder, IKB as IBtn
        kb = InlineBuilder()
        kb.row(IBtn("🔙 Back to Menu", callback_data="cat_main"))
        await bot.edit_message_text(
            chat_id=cq.message.chat.id,
            message_id=cq.message.message_id,
            text=(
                "🔍 <b>Search Folders</b>\n\n"
                "Type the name of the folder you're looking for.\n"
                "Supports partial names, abbreviations, and typos!\n\n"
                "<i>Examples: anatomy, pharma, surgery notes, biochem</i>\n\n"
                "👇 <b>Send your search query as a message:</b>"
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=kb.build(),
        )
    except TelegramBadRequest:
        pass


async def _cb_catalog(cq: types.CallbackQuery, bot, user_id: int) -> None:
    """catalog — show/regenerate the Telegra.ph content catalog."""
    await cq.answer("Fetching catalog…")
    try:
        from utils.catalog import get_catalog_url, generate_catalog
        url = get_catalog_url()
        if not url:
            me = await bot.me()
            url = await generate_catalog(me.username)

        if url:
            from utils.keyboard import InlineBuilder, IKB as IBtn
            from aiogram.enums import ParseMode
            kb = InlineBuilder()
            kb.row(IBtn("📖 Open Catalog", url=url))
            kb.row(IBtn("🔙 Back to Menu", callback_data="cat_main"))
            try:
                await bot.edit_message_text(
                    chat_id=cq.message.chat.id,
                    message_id=cq.message.message_id,
                    text=(
                        "📖 <b>Full Content Catalog</b>\n\n"
                        "Browse all available folders organized by category.\n"
                        "Click any folder link to open the bot and start downloading!\n\n"
                        f"<i>🔗 {url}</i>"
                    ),
                    parse_mode=ParseMode.HTML,
                    reply_markup=kb.build(),
                )
            except TelegramBadRequest:
                pass
    except Exception as e:
        logging.error(f"Catalog callback error: {e}", exc_info=True)
        await cq.answer("Error displaying catalog.")


async def _cb_download(cq: types.CallbackQuery, bot, user_id: int) -> None:
    try:
        folder_id = int(cq.data.split(':', 1)[1])
    except (ValueError, IndexError):
        await cq.answer("Invalid folder.")
        return

    from handlers.download import trigger_folder_download
    asyncio.create_task(
        trigger_folder_download(
            user_id, folder_id,
            cq.message.chat.id,
            cq.id,
            cq.message.message_id,
        )
    )


async def _cb_approve(cq: types.CallbackQuery, bot, user_id: int) -> None:
    if str(user_id) not in ADMIN_IDS:
        await cq.answer("Not authorized.", show_alert=True)
        return
    try:
        target_id = int(cq.data.split(':', 1)[1])
    except (ValueError, IndexError):
        await cq.answer("Invalid user ID.")
        return

    db_execute("UPDATE users SET status = 'approved' WHERE user_id = %s", (target_id,))
    admin_name = esc(cq.from_user.first_name or str(user_id))
    await bot.answer_callback_query(cq.id, f"✅ Approved user {target_id}")

    try:
        await bot.edit_message_reply_markup(
            chat_id=cq.message.chat.id, message_id=cq.message.message_id, reply_markup=None
        )
        await bot.edit_message_text(
            chat_id=cq.message.chat.id, message_id=cq.message.message_id,
            text=cq.message.text + f"\n\n✅ <b>Approved</b> by {admin_name}",
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        pass

    try:
        await bot.send_message(
            target_id,
            "🎉 <b>Access Granted!</b>\n\nYou've been approved to use the bot.\n\n👉 Tap /start to get started!",
            parse_mode=ParseMode.HTML,
        )
    except TelegramForbiddenError:
        logging.warning(f"User {target_id} has blocked the bot.")
    except Exception as e:
        logging.error(f"Error notifying user {target_id} of approval: {e}")


async def _cb_reject(cq: types.CallbackQuery, bot, user_id: int) -> None:
    if str(user_id) not in ADMIN_IDS:
        await cq.answer("Not authorized.", show_alert=True)
        return
    try:
        target_id = int(cq.data.split(':', 1)[1])
    except (ValueError, IndexError):
        await cq.answer("Invalid user ID.")
        return

    db_execute("UPDATE users SET status = 'rejected' WHERE user_id = %s", (target_id,))
    admin_name = esc(cq.from_user.first_name or str(user_id))
    await bot.answer_callback_query(cq.id, f"❌ Rejected user {target_id}")

    try:
        await bot.edit_message_text(
            chat_id=cq.message.chat.id, message_id=cq.message.message_id,
            text=cq.message.text + f"\n\n❌ <b>Rejected</b> by {admin_name}",
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        pass

    try:
        await bot.send_message(
            target_id,
            f"Your access request was not approved. 😢\n\nIf you think this is a mistake, contact us: {ADMIN_CONTACT}",
        )
    except TelegramForbiddenError:
        logging.warning(f"User {target_id} has blocked the bot.")
    except Exception as e:
        logging.error(f"Error notifying user {target_id} of rejection: {e}")


async def _cb_folder_approve(cq: types.CallbackQuery, bot, user_id: int) -> None:
    if str(user_id) not in ADMIN_IDS:
        await cq.answer("Not authorized.", show_alert=True)
        return
    try:
        _, target_id, folder_id = cq.data.split(':', 2)
        target_id, folder_id = int(target_id), int(folder_id)
    except (ValueError, IndexError):
        await cq.answer("Invalid data.")
        return

    db_execute(
        '''
        INSERT INTO user_folder_approval (user_id, folder_id, approved, download_completed)
        VALUES (%s, %s, TRUE, FALSE)
        ON CONFLICT (user_id, folder_id) DO UPDATE
            SET approved = TRUE, download_completed = FALSE
        ''',
        (target_id, folder_id)
    )

    folder_row = db_fetchone('SELECT name FROM folders WHERE id = %s', (folder_id,))
    folder_name = folder_row[0] if folder_row else f"Folder #{folder_id}"
    admin_name = esc(cq.from_user.first_name or str(user_id))
    await bot.answer_callback_query(cq.id, f"✅ Approved download for user {target_id}")

    try:
        await bot.edit_message_text(
            chat_id=cq.message.chat.id, message_id=cq.message.message_id,
            text=cq.message.text + f"\n\n✅ <b>Approved</b> by {admin_name}",
            parse_mode=ParseMode.HTML, reply_markup=None,
        )
    except Exception:
        pass

    try:
        await bot.send_message(
            target_id,
            f"✅ <b>Download Approved!</b>\n\n"
            f"Your request for <b>{esc(folder_name)}</b> has been approved.\n"
            f"You get <b>1 download</b> at Premium speed.\n\nUse /start and tap the folder to begin.",
            parse_mode=ParseMode.HTML,
        )
    except TelegramForbiddenError:
        logging.warning(f"User {target_id} has blocked the bot.")
    except Exception as e:
        logging.error(f"Error notifying user {target_id} of paid-folder approval: {e}")


async def _cb_folder_reject(cq: types.CallbackQuery, bot, user_id: int) -> None:
    if str(user_id) not in ADMIN_IDS:
        await cq.answer("Not authorized.", show_alert=True)
        return
    try:
        _, target_id, folder_id = cq.data.split(':', 2)
        target_id, folder_id = int(target_id), int(folder_id)
    except (ValueError, IndexError):
        await cq.answer("Invalid data.")
        return

    db_execute(
        'DELETE FROM user_folder_approval WHERE user_id = %s AND folder_id = %s',
        (target_id, folder_id)
    )

    folder_row = db_fetchone('SELECT name FROM folders WHERE id = %s', (folder_id,))
    folder_name = folder_row[0] if folder_row else f"Folder #{folder_id}"
    admin_name = esc(cq.from_user.first_name or str(user_id))
    await bot.answer_callback_query(cq.id, f"❌ Rejected request for user {target_id}")

    try:
        await bot.edit_message_text(
            chat_id=cq.message.chat.id, message_id=cq.message.message_id,
            text=cq.message.text + f"\n\n❌ <b>Rejected</b> by {admin_name}",
            parse_mode=ParseMode.HTML, reply_markup=None,
        )
    except Exception:
        pass

    try:
        await bot.send_message(
            target_id,
            f"❌ Your request for <b>{esc(folder_name)}</b> was not approved.\n\n"
            f"If you think this is a mistake, contact us: {ADMIN_CONTACT}",
            parse_mode=ParseMode.HTML,
        )
    except TelegramForbiddenError:
        logging.warning(f"User {target_id} has blocked the bot.")
    except Exception as e:
        logging.error(f"Error notifying user {target_id} of paid-folder rejection: {e}")


async def _cb_delete_confirm(cq: types.CallbackQuery, bot, user_id: int) -> None:
    if str(user_id) not in ADMIN_IDS:
        await cq.answer("Not authorized.", show_alert=True)
        return

    pending = _pending_deletions.pop(user_id, None)
    if not pending:
        await cq.answer()
        await bot.edit_message_text(
            "Session expired. Please use /deletefolder again.",
            chat_id=cq.message.chat.id, message_id=cq.message.message_id,
        )
        return

    folder_name, folder_id = pending
    from handlers.folder import execute_folder_deletion
    await cq.answer("🗑 Deleting…")
    await execute_folder_deletion(bot, cq.message, folder_id, folder_name)


async def _cb_delete_cancel(cq: types.CallbackQuery, bot, user_id: int) -> None:
    _pending_deletions.pop(user_id, None)
    await cq.answer("Cancelled.")
    try:
        await bot.edit_message_text(
            "❌ Folder deletion cancelled.",
            chat_id=cq.message.chat.id, message_id=cq.message.message_id,
        )
    except Exception:
        pass


async def _cb_broadcast_send(cq: types.CallbackQuery, bot, user_id: int) -> None:
    if str(user_id) not in ADMIN_IDS:
        await cq.answer("Not authorized.", show_alert=True)
        return
    try:
        broadcast_id = int(cq.data.split(':', 1)[1])
    except (ValueError, IndexError):
        await cq.answer("Invalid broadcast ID.")
        return
    from handlers.broadcast import execute_broadcast
    asyncio.create_task(execute_broadcast(cq, broadcast_id))


async def _cb_broadcast_cancel(cq: types.CallbackQuery, bot, user_id: int) -> None:
    if str(user_id) not in ADMIN_IDS:
        await cq.answer("Not authorized.", show_alert=True)
        return
    try:
        broadcast_id = int(cq.data.split(':', 1)[1])
    except (ValueError, IndexError):
        await cq.answer("Invalid broadcast ID.")
        return
    from handlers.broadcast import cancel_broadcast
    await cancel_broadcast(cq, broadcast_id)


async def _cb_pay_plan(cq: types.CallbackQuery, bot, user_id: int) -> None:
    from handlers.payment import handle_pay_callback
    await handle_pay_callback(cq)


async def _cb_pay_cancel(cq: types.CallbackQuery, bot, user_id: int) -> None:
    from handlers.payment import handle_pay_callback
    await handle_pay_callback(cq)


async def _cb_stars_plan(cq: types.CallbackQuery, bot, user_id: int) -> None:
    from handlers.payment_stars import handle_stars_plan_callback
    await handle_stars_plan_callback(cq)


async def _cb_stars_cancel(cq: types.CallbackQuery, bot, user_id: int) -> None:
    from handlers.payment_stars import handle_stars_plan_callback
    await handle_stars_plan_callback(cq)


async def _cb_cancel_download(cq: types.CallbackQuery, bot, user_id: int) -> None:
    """Cancel Download button — signals the active download to stop."""
    import utils.progress as progress
    try:
        # callback_data = "cancel_dl:<chat_id>"
        target_chat_id = int(cq.data.split(':', 1)[1])
    except (ValueError, IndexError):
        target_chat_id = cq.message.chat.id

    if progress.is_downloading(target_chat_id):
        progress.request_cancel(target_chat_id)
        await bot.answer_callback_query(
            cq.id, "⏹ Download cancelled — stopping after current file.", show_alert=False
        )
    else:
        await cq.answer("No active download to cancel.", show_alert=False)


async def _cb_info_status(cq: types.CallbackQuery, bot, user_id: int) -> None:
    """Show status card inline (edit the current message)."""
    await cq.answer()
    from handlers.status import build_status_text
    try:
        text, kb = await build_status_text(user_id, bot)
        await bot.edit_message_text(
            chat_id=cq.message.chat.id,
            message_id=cq.message.message_id,
            text=text,
            parse_mode=ParseMode.HTML,
            reply_markup=kb.build(),
            disable_web_page_preview=True,
        )
    except Exception:
        pass


async def _cb_info_premium(cq: types.CallbackQuery, bot, user_id: int) -> None:
    """Show the premium purchase screen, routed by PAYMENT_MODE."""
    await cq.answer()

    kb = InlineBuilder()

    if PAYMENT_MODE == 'stars':
        # -- Telegram Stars mode -----------------------------------------------
        from handlers.payment_stars import get_stars_plans
        plans = get_stars_plans()

        if plans:
            for plan_id, name, amount_stars, days in plans:
                kb.add(InlineKeyboardButton(
                    f"⭐ {name} \u2014 {amount_stars} Stars ({days} days)",
                    callback_data=f"stars_plan:{plan_id}",
                ))
            plan_lines = "\n".join(
                f"  • <b>{name}</b> \u2014 {amount_stars} ⭐ / {days} days"
                for _, name, amount_stars, days in plans
            )
            how_to = "Tap a plan below to pay with Telegram Stars."
        else:
            plan_lines = "  Contact admin for current pricing."
            how_to = f"Message {ADMIN_CONTACT} to get your plan activated."

        kb.row(InlineKeyboardButton("📞 Contact Support", url=f"https://t.me/{ADMIN_CONTACT.lstrip('@')}"))
        kb.row(InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_main"))

    elif PAYMENT_MODE == 'razorpay':
        # -- Razorpay mode -----------------------------------------------------
        from handlers.payment import _get_plans, _fmt_inr
        plans = _get_plans()

        if plans:
            for plan_id, name, amount_paise, days in plans:
                kb.add(InlineKeyboardButton(
                    f"💳 {name} \u2014 {_fmt_inr(amount_paise)} ({days} days)",
                    callback_data=f"pay_plan:{plan_id}",
                ))
            plan_lines = "\n".join(
                f"  • <b>{name}</b> \u2014 {_fmt_inr(amount_paise)} / {days} days"
                for _, name, amount_paise, days in plans
            )
            how_to = "Tap a plan below to pay via UPI / Card / Net Banking."
        else:
            plan_lines = "  Contact admin for current pricing."
            how_to = f"Message {ADMIN_CONTACT} to get your plan activated."

        kb.row(InlineKeyboardButton("📞 Contact Support", url=f"https://t.me/{ADMIN_CONTACT.lstrip('@')}"))
        kb.row(InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_main"))

    else:
        # -- Manual mode: no plans, just contact admin -------------------------
        plan_lines = "  Contact admin for current pricing."
        how_to = f"Message {ADMIN_CONTACT} to get your plan activated."
        kb.row(InlineKeyboardButton("📞 Contact Support", url=f"https://t.me/{ADMIN_CONTACT.lstrip('@')}"))
        kb.row(InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_main"))

    try:
        await bot.edit_message_text(
            chat_id=cq.message.chat.id, message_id=cq.message.message_id,
            text=(
                f"⭐ <b>Premium Membership</b>\n"
                "<b>──────────────────────────────────</b>\n\n"
                "<b>What you get:</b>\n"
                "  • ⚡ 5s/file  <i>(vs 60s on free)</i>\n"
                "  • ⏱ 2 min cooldown  <i>(vs 7 min)</i>\n"
                "  • ⭐ Premium-only folder access\n\n"
                f"<b>Plans:</b>\n{plan_lines}\n\n"
                f"<b>How to subscribe:</b>\n"
                f"  {how_to}\n\n"
                "<i>Tap 🔙 Back to return to menu.</i>"
            ),
            parse_mode=ParseMode.HTML, reply_markup=kb.build(),
        )
    except Exception:
        pass


async def _cb_info_verify(cq: types.CallbackQuery, bot, user_id: int) -> None:
    await cq.answer()
    kb = InlineBuilder()
    kb.row(InlineKeyboardButton("📨 Message Admin", url=f"https://t.me/{ADMIN_CONTACT.lstrip('@')}"))
    kb.row(InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_main"))
    try:
        await bot.edit_message_text(
            chat_id=cq.message.chat.id, message_id=cq.message.message_id,
            text=(
                "🎓 <b>Student Verification</b>\n"
                "<b>──────────────────────────────────</b>\n\n"
                "Access is limited to verified medical\n"
                "students to protect our content.\n\n"
                "<b>How to verify:</b>\n"
                "  1️⃣ Photo of your student ID\n"
                "      or enrollment letter\n"
                f"  2️⃣ Send it to: {ADMIN_CONTACT}\n"
                "  3️⃣ Approved within a few hours\n\n"
                "<b>Accepted documents:</b>\n"
                "  • Student ID card\n"
                "  • Enrollment certificate\n"
                "  • Fee receipt (name + course)\n\n"
                "<i>You'll get a notification once approved.</i>"
            ),
            parse_mode=ParseMode.HTML, reply_markup=kb.build(),
        )
    except Exception:
        pass


async def _cb_info_about(cq: types.CallbackQuery, bot, user_id: int) -> None:
    await cq.answer()
    from handlers.about_help import get_about_content
    kb = InlineBuilder()
    kb.row(InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_main"))
    try:
        await bot.edit_message_text(
            chat_id=cq.message.chat.id, message_id=cq.message.message_id,
            text=get_about_content(), parse_mode=ParseMode.HTML, reply_markup=kb.build()
        )
    except Exception:
        pass


async def _cb_info_help(cq: types.CallbackQuery, bot, user_id: int) -> None:
    await cq.answer()
    from handlers.about_help import get_help_content
    kb = InlineBuilder()
    kb.row(InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_main"))
    try:
        await bot.edit_message_text(
            chat_id=cq.message.chat.id, message_id=cq.message.message_id,
            text=get_help_content(), parse_mode=ParseMode.HTML, reply_markup=kb.build()
        )
    except Exception:
        pass


async def _cb_back_to_main(cq: types.CallbackQuery, bot, user_id: int) -> None:
    await cq.answer()

    user_row = db_fetchone('SELECT status, first_name FROM users WHERE user_id = %s', (user_id,))
    user_status = user_row[0] if user_row else 'pending'
    first_name  = user_row[1] if user_row else 'there'

    if user_status == 'pending':
        kb = InlineBuilder()
        kb.row(
            InlineKeyboardButton("🎓 How to Verify", callback_data="info_verify"),
            InlineKeyboardButton("💬 Contact Admin", url=f"https://t.me/{ADMIN_CONTACT.lstrip('@')}"),
        )
        try:
            await bot.edit_message_text(
                chat_id=cq.message.chat.id, message_id=cq.message.message_id,
                text=(
                    f"👋 Hello {esc(first_name or 'there')}!\n"
                    f"<b>I'm {BOT_NAME}</b> ✨\n"
                    "<b>──────────────────────────────────</b>\n\n"
                    "🔒 Access is limited to verified\n"
                    "medical students to protect content.\n\n"
                    "Your request has been sent to admin.\n"
                    "Tap <b>How to Verify</b> to see\n"
                    "what documents to send them.\n\n"
                    "✅ You'll be notified once reviewed!"
                ),
                parse_mode=ParseMode.HTML, reply_markup=kb.build(),
            )
        except Exception:
            pass
    elif user_status == 'rejected':
        kb = InlineBuilder()
        kb.row(InlineKeyboardButton("💬 Contact Admin", url=f"https://t.me/{ADMIN_CONTACT.lstrip('@')}"))
        try:
            await bot.edit_message_text(
                chat_id=cq.message.chat.id, message_id=cq.message.message_id,
                text=(
                    "❌ <b>Access Not Approved</b>\n"
                    "<b>──────────────────────────────────</b>\n\n"
                    "Unfortunately your access request\n"
                    "was not approved. 😢\n\n"
                    f"If you think this is a mistake,\n"
                    f"contact us at: {ADMIN_CONTACT}"
                ),
                parse_mode=ParseMode.HTML,
                reply_markup=kb.build(),
            )
        except Exception:
            pass
    elif user_status == 'banned':
        try:
            kb = InlineBuilder()
            kb.row(InlineKeyboardButton(
                "💬 Appeal to Admin",
                url=f"https://t.me/{ADMIN_CONTACT.lstrip('@')}"
            ))
            await bot.edit_message_text(
                chat_id=cq.message.chat.id, message_id=cq.message.message_id,
                text=(
                    f"🚫 <b>Account Banned</b>\n"
                    "<b>──────────────────────────────────</b>\n\n"
                    f"Hi {esc(first_name or 'there')},\n"
                    "you have been banned for violating\n"
                    "our community rules.\n\n"
                    "If you believe this is a mistake,\n"
                    "please contact the admin to appeal."
                ),
                parse_mode=ParseMode.HTML,
                reply_markup=kb.build(),
            )
        except Exception:
            pass
    else:
        invalidate_member_cache(user_id)
        if not await is_user_member(user_id):
            kb = InlineBuilder()
            for channel in REQUIRED_CHANNELS:
                title = await get_channel_title(channel)
                kb.add(InlineKeyboardButton(
                    f"📢 {title}", url=f"https://t.me/{channel.lstrip('@')}"
                ))
            kb.add(InlineKeyboardButton("🔄 Check Subscription / Refresh", callback_data="back_to_main"))
            try:
                await bot.edit_message_text(
                    chat_id=cq.message.chat.id, message_id=cq.message.message_id,
                    text=(
                        "Please join our required channels to continue using the bot 👇\n\n"
                        "After joining all channels, tap the button below to refresh."
                    ),
                    reply_markup=kb.build()
                )
            except Exception:
                pass
            return

        await send_ui(user_id, message_id=cq.message.message_id, is_returning=True)
async def send_hierarchy_ui(chat_id: int, node_type: str, node_id: int, message_id: int = None, page: int = 0):
    bot = get_bot()
    user_data = db_fetchone('SELECT premium FROM users WHERE user_id = %s', (chat_id,))
    is_premium_user = bool(user_data and user_data[0])

    keyboard = InlineBuilder()
    
    if node_type == 'c':
        # Category navigation
        if node_id == 0:
            cat_name = "Uncategorized"
            cat_emoji = "📦"
            crumbs_text = "📦 Uncategorized"
            parent_id = None
        else:
            cat_row = db_fetchone("SELECT name, emoji, parent_id FROM categories WHERE id = %s", (node_id,))
            if not cat_row:
                await send_ui(chat_id, message_id)
                return
            cat_name, cat_emoji, parent_id = cat_row
            crumbs = get_category_breadcrumb(node_id)
            crumbs_text = " ➔ ".join(f"{emoji} {name}" for cid, name, emoji in crumbs)

        # Fetch children: sub-categories first, then folders
        sub_cats = get_child_categories(node_id) if node_id > 0 else []
        folders = get_child_folders(category_id=node_id)
        
        # Combine lists for pagination
        items = []
        for cid, name, emoji, sort_order, folder_count, has_children in sub_cats:
            items.append(('c', cid, name, emoji, folder_count, has_children, False, False))
        for fid, name, emoji, premium, admin_approval, file_count, has_children in folders:
            items.append(('f', fid, name, emoji, file_count, has_children, premium, admin_approval))
            
        total_pages = max(1, (len(items) + _PAGE_SIZE - 1) // _PAGE_SIZE)
        page = max(0, min(page, total_pages - 1))
        page_items = items[page * _PAGE_SIZE : (page + 1) * _PAGE_SIZE]
        
        text = (
            f"<b>📂 Navigation Path:</b>\n"
            f"📍 <code>{esc(crumbs_text)}</code>\n"
            f"<b>──────────────────────────────────</b>\n"
            f"Select a category or folder below to browse\n"
            f"(page {page + 1}/{total_pages}):\n"
            f"<b>──────────────────────────────────</b>\n\n"
        )
        
        for item_type, iid, name, emoji, count, has_children, premium, admin_approval in page_items:
            if item_type == 'c':
                text += f"• 📂 <code>{esc(name)}</code> ({count} fld)\n"
                keyboard.row(InlineKeyboardButton(f"📂 {name} ({count})", callback_data=f"nav:c:{iid}:0"))
            else:
                safe_name = esc(name)
                if not is_premium_user and premium:
                    tag, btn_icon = " [⭐ Premium]", "⭐"
                elif admin_approval:
                    tag, btn_icon = " [💰 Paid]", "💰"
                else:
                    tag, btn_icon = "", "📁"
                text += f"• {btn_icon} <code>{safe_name}</code>{tag} ({count} files)\n"
                
                if has_children:
                    callback_data = f"nav:f:{iid}:0"
                else:
                    callback_data = f"fi:{iid}:0"
                keyboard.row(InlineKeyboardButton(f"{btn_icon} {name} ({count})", callback_data=callback_data))
                
        # Pagination row
        nav_buttons = []
        if page > 0:
            nav_buttons.append(InlineKeyboardButton(f"◀️ Page {page}", callback_data=f"nav:c:{node_id}:{page - 1}"))
        if page < total_pages - 1:
            nav_buttons.append(InlineKeyboardButton(f"Page {page + 2} ▶️", callback_data=f"nav:c:{node_id}:{page + 1}"))
        if nav_buttons:
            keyboard.row(*nav_buttons)
            
        # Back button
        if node_id > 0:
            if parent_id is not None:
                keyboard.row(InlineKeyboardButton("🔙 Back / Up One Level", callback_data=f"nav:c:{parent_id}:0"))
            else:
                keyboard.row(InlineKeyboardButton("🔙 Back to Main Menu", callback_data="cat_main"))
        else:
            keyboard.row(InlineKeyboardButton("🔙 Back to Main Menu", callback_data="cat_main"))

    elif node_type == 'f':
        # Folder navigation (sub-folders)
        folder_row = db_fetchone("SELECT name, emoji, category_id, parent_id FROM folders WHERE id = %s", (node_id,))
        if not folder_row:
            await send_ui(chat_id, message_id)
            return
        folder_name, folder_emoji, category_id, parent_id = folder_row
        crumbs = get_folder_breadcrumb(node_id)
        crumbs_text = " ➔ ".join(f"{emoji} {name}" for fid, name, emoji in crumbs)
        
        sub_folders = get_child_folders(parent_id=node_id)
        
        total_pages = max(1, (len(sub_folders) + _PAGE_SIZE - 1) // _PAGE_SIZE)
        page = max(0, min(page, total_pages - 1))
        page_folders = sub_folders[page * _PAGE_SIZE : (page + 1) * _PAGE_SIZE]
        
        text = (
            f"<b>📁 Folder Contents:</b>\n"
            f"📍 <code>{esc(crumbs_text)}</code>\n"
            f"<b>──────────────────────────────────</b>\n"
            f"Browse sub-folders\n"
            f"(page {page + 1}/{total_pages}):\n"
            f"<b>──────────────────────────────────</b>\n\n"
        )
        
        for fid, name, emoji, premium, admin_approval, file_count, has_children in page_folders:
            safe_name = esc(name)
            if not is_premium_user and premium:
                tag, btn_icon = " [⭐ Premium]", "⭐"
            elif admin_approval:
                tag, btn_icon = " [💰 Paid]", "💰"
            else:
                tag, btn_icon = "", "📁"
            text += f"• {btn_icon} <code>{safe_name}</code>{tag} ({file_count} files)\n"
            
            if has_children:
                callback_data = f"nav:f:{fid}:0"
            else:
                callback_data = f"fi:{fid}:0"
            keyboard.row(InlineKeyboardButton(f"{btn_icon} {name} ({file_count})", callback_data=callback_data))
            
        # Pagination row
        nav_buttons = []
        if page > 0:
            nav_buttons.append(InlineKeyboardButton(f"◀️ Page {page}", callback_data=f"nav:f:{node_id}:{page - 1}"))
        if page < total_pages - 1:
            nav_buttons.append(InlineKeyboardButton(f"Page {page + 2} ▶️", callback_data=f"nav:f:{node_id}:{page + 1}"))
        if nav_buttons:
            keyboard.row(*nav_buttons)
            
        # Action buttons
        is_fav = db_fetchone("SELECT 1 FROM user_favorites WHERE user_id = %s AND folder_id = %s", (chat_id, node_id))
        fav_label = "★ Unfavorite" if is_fav else "☆ Favorite"

        # C2: If the folder has BOTH sub-folders AND direct files,
        # show a dedicated button to view/download only the direct files.
        direct_file_count = get_folder_direct_file_count(node_id)
        if direct_file_count > 0:
            keyboard.row(
                InlineKeyboardButton("📥 Download All (incl. sub-folders)", callback_data=f"dl:{node_id}"),
            )
            keyboard.row(
                InlineKeyboardButton(f"📄 View Direct Files ({direct_file_count})", callback_data=f"fi:{node_id}:0"),
                InlineKeyboardButton(fav_label, callback_data=f"fav:{node_id}")
            )
        else:
            keyboard.row(
                InlineKeyboardButton("📥 Download All", callback_data=f"dl:{node_id}"),
                InlineKeyboardButton(fav_label, callback_data=f"fav:{node_id}")
            )
        
        if parent_id is not None:
            keyboard.row(InlineKeyboardButton("🔙 Up One Level", callback_data=f"nav:f:{parent_id}:0"))
        elif category_id is not None:
            keyboard.row(InlineKeyboardButton("🔙 Up One Level", callback_data=f"nav:c:{category_id}:0"))
        else:
            keyboard.row(InlineKeyboardButton("🔙 Back to Main Menu", callback_data="cat_main"))

    try:
        if message_id:
            await bot.edit_message_text(
                chat_id=chat_id, message_id=message_id,
                text=text, reply_markup=keyboard.build(), parse_mode=ParseMode.HTML
            )
        else:
            await bot.send_message(
                chat_id, text, reply_markup=keyboard.build(), parse_mode=ParseMode.HTML
            )
    except TelegramBadRequest:
        pass


async def _cb_navigate(cq: types.CallbackQuery, bot, user_id: int) -> None:
    try:
        _, nav_type, node_id_str, page_str = cq.data.split(':', 3)
        node_id = int(node_id_str)
        page = int(page_str)
    except (ValueError, IndexError):
        await cq.answer("Invalid navigation path.")
        return
    await cq.answer()
    await send_hierarchy_ui(user_id, nav_type, node_id, cq.message.message_id, page)


async def _cb_file_preview(cq: types.CallbackQuery, bot, user_id: int) -> None:
    try:
        _, folder_id_str, page_str = cq.data.split(':', 2)
        folder_id = int(folder_id_str)
        page = int(page_str)
    except (ValueError, IndexError):
        await cq.answer("Invalid folder selection.")
        return
        
    await cq.answer()
    
    folder_row = db_fetchone("SELECT name, emoji, category_id, parent_id FROM folders WHERE id = %s", (folder_id,))
    if not folder_row:
        await send_ui(user_id, cq.message.message_id)
        return
    folder_name, folder_emoji, category_id, parent_id = folder_row
    
    files = db_fetchall("SELECT id, file_name, file_type FROM files WHERE folder_id = %s ORDER BY id", (folder_id,))
    
    PAGE_SIZE_FILES = 8
    total_pages = max(1, (len(files) + PAGE_SIZE_FILES - 1) // PAGE_SIZE_FILES)
    page = max(0, min(page, total_pages - 1))
    page_files = files[page * PAGE_SIZE_FILES : (page + 1) * PAGE_SIZE_FILES]
    
    text = (
        f"<b>📁 File Preview:</b>\n"
        f"📂 <code>{esc(folder_name)}</code>\n"
        f"<b>──────────────────────────────────</b>\n"
        f"Total: {len(files)} file{'s' if len(files) != 1 else ''}"
        f" — page {page + 1}/{total_pages}\n"
        f"<b>──────────────────────────────────</b>\n\n"
    )
    
    keyboard = InlineBuilder()
    for fid, fname, ftype in page_files:
        icon = {"video": "🎥", "photo": "🖼️", "audio": "🎵"}.get(ftype, "📄")
        short = fname if len(fname) <= 32 else fname[:29] + "…"
        text += f"• {icon} <code>{esc(short)}</code>\n"
        keyboard.row(InlineKeyboardButton(f"{icon} {short}", callback_data=f"dl_file:{fid}:{folder_id}:{page}"))
        
    # Pagination
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton(f"◀️ Prev", callback_data=f"fi:{folder_id}:{page - 1}"))
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton(f"Next ▶️", callback_data=f"fi:{folder_id}:{page + 1}"))
    if nav_buttons:
        keyboard.row(*nav_buttons)
        
    # C1: Back button navigates UP (to parent folder, parent category, or main menu)
    # NOT back to nav:f:<folder_id> which would loop into this same folder's sub-folder view.
    if parent_id is not None:
        back_cb = f"nav:f:{parent_id}:0"
        back_label = "🔙 Up One Level"
    elif category_id is not None:
        back_cb = f"nav:c:{category_id}:0"
        back_label = "🔙 Back to Category"
    else:
        back_cb = "cat_main"
        back_label = "🔙 Back to Main Menu"

    keyboard.row(InlineKeyboardButton("📥 Download All", callback_data=f"dl:{folder_id}"))
    keyboard.row(InlineKeyboardButton(back_label, callback_data=back_cb))
    
    try:
        await bot.edit_message_text(
            chat_id=user_id, message_id=cq.message.message_id,
            text=text, reply_markup=keyboard.build(), parse_mode=ParseMode.HTML
        )
    except TelegramBadRequest:
        pass


async def _cb_download_file(cq: types.CallbackQuery, bot, user_id: int) -> None:
    try:
        _, file_id_str, folder_id_str, page_str = cq.data.split(':', 3)
        file_id = int(file_id_str)
        folder_id = int(folder_id_str)
        page = int(page_str)
    except (ValueError, IndexError):
        await cq.answer("Invalid file selection.")
        return

    await cq.answer("Delivering file...")
    
    try:
        from utils.bots import get_current_bot_pk, get_servable_locations
        from handlers.download import _deliver_file
        from utils.database import get_file_caption, get_active_caption
        me = await bot.me()
        bot_pk = get_current_bot_pk(me.username)
        locations = get_servable_locations(file_id, bot_pk) if bot_pk else []

        # Resolve caption: per-file caption → global caption → None (keep original)
        caption_text = get_file_caption(file_id)
        if not caption_text:
            _, global_text = get_active_caption()
            if global_text and global_text.strip():
                caption_text = global_text.strip()

        sent, user_blocked = await _deliver_file(bot, user_id, locations, caption_override=caption_text)
        if user_blocked:
            return
        if sent is None:
            await cq.answer("File is currently unavailable.", show_alert=True)
        else:
            await cq.answer("File delivered successfully!", show_alert=False)
    except Exception as e:
        logging.error(f"Single file delivery error: {e}", exc_info=True)
        await cq.answer("Error delivering file.")


async def _cb_toggle_favorite(cq: types.CallbackQuery, bot, user_id: int) -> None:
    try:
        _, folder_id_str = cq.data.split(':', 1)
        folder_id = int(folder_id_str)
    except (ValueError, IndexError):
        await cq.answer("Invalid folder ID.")
        return
        
    added = toggle_user_favorite(user_id, folder_id)
    status_msg = "Added to Favorites! ⭐" if added else "Removed from Favorites. 📥"
    await cq.answer(status_msg)
    
    await send_hierarchy_ui(user_id, 'f', folder_id, cq.message.message_id, 0)


async def _cb_fav_list(cq: types.CallbackQuery, bot, user_id: int) -> None:
    await cq.answer()
    
    favs = get_user_favorites(user_id)
    
    text = (
        "⭐ <b>Your Favorites:</b>\n"
        "<b>──────────────────────────────────</b>\n\n"
    )
    keyboard = InlineBuilder()
    
    if not favs:
        text += "You haven't bookmarked any folders yet.\n"
        text += "Tap the <b>⭐ Add Favorite</b> button on any folder page to save it here!"
    else:
        for fid, name, emoji, file_count, has_children in favs:
            text += f"• {emoji} <code>{esc(name)}</code> ({file_count} files)\n"
            callback_data = f"nav:f:{fid}:0" if has_children else f"fi:{fid}:0"
            keyboard.row(InlineKeyboardButton(f"{emoji} {name} ({file_count})", callback_data=callback_data))
            
    keyboard.row(InlineKeyboardButton("🔙 Back to Main Menu", callback_data="cat_main"))
    
    try:
        await bot.edit_message_text(
            chat_id=user_id,
            message_id=cq.message.message_id,
            text=text,
            reply_markup=keyboard.build(),
            parse_mode=ParseMode.HTML
        )
    except TelegramBadRequest:
        pass


async def _cb_hist_list(cq: types.CallbackQuery, bot, user_id: int) -> None:
    await cq.answer()
    
    history = get_recent_downloads(user_id)
    
    text = (
        "📥 <b>Recent Downloads:</b>\n"
        "<b>──────────────────────────────────</b>\n\n"
    )
    keyboard = InlineBuilder()
    
    if not history:
        text += "No recent downloads recorded yet."
    else:
        for fid, name, emoji, downloaded_at, has_children in history:
            ts = downloaded_at.strftime("%d/%m/%Y") if downloaded_at else ""
            text += f"• {emoji} <code>{esc(name)}</code> (Downloaded {ts})\n"
            callback_data = f"nav:f:{fid}:0" if has_children else f"fi:{fid}:0"
            keyboard.row(InlineKeyboardButton(f"{emoji} {name}", callback_data=callback_data))
            
    keyboard.row(InlineKeyboardButton("🔙 Back to Main Menu", callback_data="cat_main"))
    
    try:
        await bot.edit_message_text(
            chat_id=user_id,
            message_id=cq.message.message_id,
            text=text,
            reply_markup=keyboard.build(),
            parse_mode=ParseMode.HTML
        )
    except TelegramBadRequest:
        pass


async def _cb_new_list(cq: types.CallbackQuery, bot, user_id: int) -> None:
    await cq.answer()
    
    new_folders = get_recently_added_folders(5)
    
    text = (
        "🆕 <b>What's New (Recently Added):</b>\n"
        "<b>──────────────────────────────────</b>\n\n"
    )
    keyboard = InlineBuilder()
    
    if not new_folders:
        text += "No folders added recently."
    else:
        for fid, name, emoji, created_at, file_count, has_children in new_folders:
            ts = created_at.strftime("%d/%m/%Y") if created_at else ""
            text += f"• {emoji} <code>{esc(name)}</code> ({file_count} files, Added {ts})\n"
            callback_data = f"nav:f:{fid}:0" if has_children else f"fi:{fid}:0"
            keyboard.row(InlineKeyboardButton(f"{emoji} {name} ({file_count})", callback_data=callback_data))
            
    keyboard.row(InlineKeyboardButton("🔙 Back to Main Menu", callback_data="cat_main"))
    
    try:
        await bot.edit_message_text(
            chat_id=user_id,
            message_id=cq.message.message_id,
            text=text,
            reply_markup=keyboard.build(),
            parse_mode=ParseMode.HTML
        )
    except TelegramBadRequest:
        pass


# ── Dispatch table — split on ':' gives the key for both prefix and exact data
_CB_HANDLERS = {
    "pg":           _cb_page,
    "cat":          _cb_category,
    "cat_main":     _cb_category_main,
    "search":       _cb_search,
    "catalog":      _cb_catalog,
    "dl":           _cb_download,
    "dl_file":      _cb_download_file,
    "approve":      _cb_approve,
    "reject":       _cb_reject,
    "papprove":     _cb_folder_approve,
    "preject":      _cb_folder_reject,
    "dfc":          _cb_delete_confirm,
    "dfc_cancel":   _cb_delete_cancel,
    "bcast_send":   _cb_broadcast_send,
    "bcast_cancel": _cb_broadcast_cancel,
    "pay_plan":     _cb_pay_plan,
    "pay_cancel":   _cb_pay_cancel,
    "stars_plan":   _cb_stars_plan,
    "stars_cancel": _cb_stars_cancel,
    "cancel_dl":    _cb_cancel_download,
    "info_premium": _cb_info_premium,
    "info_verify":  _cb_info_verify,
    "info_about":   _cb_info_about,
    "info_help":    _cb_info_help,
    "info_status":  _cb_info_status,
    "close_info":   _cb_back_to_main,
    "back_to_main": _cb_back_to_main,
    # ── Hierarchical nav ──────────────────────────
    "nav":          _cb_navigate,        # nav:c:<id>:<page> or nav:f:<id>:<page>
    "fi":           _cb_file_preview,    # fi:<folder_id>:<page>
    "fav":          _cb_toggle_favorite, # fav:<folder_id>  (toggle bookmark)
    "fav_list":     _cb_fav_list,        # fav_list  (show favorites)
    "hist_list":    _cb_hist_list,       # hist_list (recent downloads)
    "new_list":     _cb_new_list,        # new_list  (recently added)
}


# ─────────────────────────────────────────────────────────────────────────────
# Unified callback handler (ALL callbacks route through here)
# ─────────────────────────────────────────────────────────────────────────────

async def process_callback(callback_query: types.CallbackQuery):
    """Route every inline keyboard callback via _CB_HANDLERS dict-dispatch."""
    bot = get_bot()
    user_id = callback_query.from_user.id
    prefix  = (callback_query.data or '').split(':')[0]
    handler = _CB_HANDLERS.get(prefix)
    if handler:
        await handler(callback_query, bot, user_id)
    else:
        await bot.answer_callback_query(callback_query.id)



# ─────────────────────────────────────────────────────────────────────────────
# /start
# ─────────────────────────────────────────────────────────────────────────────

async def handle_start(message: types.Message):
    if not is_private_chat(message):
        return

    user_id    = message.from_user.id
    username   = message.from_user.username
    first_name = message.from_user.first_name
    name       = esc(first_name or 'there')

    bot = get_bot()
    # Show typing indicator while we process
    await bot.send_chat_action(message.chat.id, ChatAction.TYPING)

    # ── Deep link payload parsing ──────────────────────────────────────────────
    # Payload comes from t.me/botname?start=dl_<folder_id>  or  cat_<category_id>
    payload = ''
    parts = (message.text or '').split(maxsplit=1)
    if len(parts) > 1:
        payload = parts[1].strip()

    add_user_to_db(user_id, username=username, first_name=first_name)

    user = db_fetchone(
        'SELECT status, welcome_sent FROM users WHERE user_id = %s',
        (user_id,)
    )

    user = db_fetchone(
        'SELECT status, welcome_sent FROM users WHERE user_id = %s',
        (user_id,)
    )

    if user and user[0] == 'approved' and payload:
        # Handle deep link directly for approved users
        invalidate_member_cache(user_id)
        if await is_user_member(user_id):
            if payload.startswith('dl_'):
                try:
                    folder_id = int(payload[3:])
                    from handlers.download import handle_folder_download_by_id
                    await handle_folder_download_by_id(user_id, folder_id, message.chat.id)
                    return
                except Exception as e:
                    log.warning(f"Deep link dl_ failed: {e}")
                    # Fall through to normal start
            elif payload.startswith('cat_'):
                try:
                    category_id = int(payload[4:])
                    await send_sticker_safe(bot, message.chat.id, delay=2)
                    await send_category_ui(message.chat.id, category_id)
                    return
                except Exception as e:
                    log.warning(f"Deep link cat_ failed: {e}")
                    # Fall through to normal start

    if not user:
        await message.answer(
            "⚠️ <b>Something went wrong.</b>\n\n"
            "Please try again in a moment.",
            parse_mode=ParseMode.HTML
        )
        return

    status, welcome_sent = user

    # ── AUTO-APPROVE (when REQUIRE_APPROVAL is disabled) ──────────────────
    if not REQUIRE_APPROVAL and status in ('pending', 'rejected'):
        # Silently approve and treat as a normal approved user
        db_execute(
            "UPDATE users SET status = 'approved' WHERE user_id = %s",
            (user_id,)
        )
        status = 'approved'

    # ── PENDING ────────────────────────────────────────────────────────────
    if status == 'pending':
        # Check how long ago they registered
        notified_row = db_fetchone(
            'SELECT last_notified FROM users WHERE user_id = %s', (user_id,)
        )
        last_notified = notified_row[0] if notified_row and notified_row[0] else None

        if last_notified:
            # Returning pending user — give a status update, not a repeat pitch
            if hasattr(last_notified, 'tzinfo') and last_notified.tzinfo:
                last_notified = last_notified.replace(tzinfo=None)
            hours_waiting = int((datetime.now() - last_notified).total_seconds() // 3600)
            wait_str = f"{hours_waiting}h" if hours_waiting < 24 else f"{hours_waiting // 24}d"

            kb = InlineBuilder()
            kb.row(
                InlineKeyboardButton("✅ How to Verify", callback_data="info_verify"),
                InlineKeyboardButton("💬 Contact Admin", url=f"https://t.me/{ADMIN_CONTACT.lstrip('@')}"),
            )
            await message.answer(
                f"⏳ <b>Still waiting, {name}!</b>\n\n"
                f"Your access request has been pending for <b>~{wait_str}</b>.\n\n"
                "📋 An admin will review it and notify you here.\n"
                "If it's been a long time, tap <b>Contact Admin</b> to follow up.",
                parse_mode=ParseMode.HTML,
                reply_markup=kb.build()
            )
        else:
            # Brand-new pending user — full onboarding message
            kb = InlineBuilder()
            kb.row(
                InlineKeyboardButton("✅ How to Verify", callback_data="info_verify"),
                InlineKeyboardButton("💬 Contact Admin", url=f"https://t.me/{ADMIN_CONTACT.lstrip('@')}"),
            )
            await message.answer(
                f"👋 <b>Hello, {name}!</b>\n\n"
                f"🏥 <b>Welcome to {BOT_NAME}</b>\n\n"
                "This bot gives verified medical students access to an organised "
                "archive of study materials — directly in Telegram.\n\n"
                "🔐 <b>Access is verified-students only</b> to protect content creators.\n\n"
                "📋 <b>What happens next:</b>\n"
                "  1️⃣ Your request has been sent to an admin\n"
                "  2️⃣ Tap <b>How to Verify</b> below to send your proof\n"
                "  3️⃣ You'll be notified here once approved\n\n"
                "⏱️ <i>Reviews typically take a few hours.</i>",
                parse_mode=ParseMode.HTML,
                reply_markup=kb.build()
            )
        await notify_admins(user_id, username, first_name)

    # ── APPROVED ───────────────────────────────────────────────────────────
    elif status == 'approved':
        if not welcome_sent:
            # First-ever login after approval — full feature tour
            await message.answer(
                f"🎉 <b>Welcome, {name}! You're approved!</b>\n\n"
                "Here's what you can do:\n\n"
                "📚 <b>Browse Categories</b> — tap a category button to see its folders\n"
                "📁 <b>Tap any folder</b> — all files download automatically\n"
                "🔍 <b>Search</b> — find any folder by name or keyword\n"
                "📖 <b>Full Catalog</b> — browse everything online\n"
                "⬇️ <b>/download</b> <code>&lt;folder name&gt;</code> — download by typing\n"
                "👤 <b>/status</b> — check your account & cooldown\n"
                "❓ <b>/help</b> — full usage guide\n\n"
                "💾 <b>Tip:</b> Forward received files to your <b>Saved Messages</b> — "
                "they're deleted from the chat after a few minutes!",
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
            db_execute(
                'UPDATE users SET welcome_sent = TRUE WHERE user_id = %s',
                (user_id,)
            )

        invalidate_member_cache(user_id)
        if not await is_user_member(user_id):
            await send_sticker_safe(bot, message.chat.id, delay=2)
            kb = InlineBuilder()
            for channel in REQUIRED_CHANNELS:
                title = await get_channel_title(channel)
                kb.add(InlineKeyboardButton(
                    f"📢 Join: {title}", url=f"https://t.me/{channel.lstrip('@')}"
                ))
            kb.add(InlineKeyboardButton("✅ I've Joined — Refresh", callback_data="cat_main"))
            await message.reply(
                "📢 <b>Channel Subscription Required</b>\n\n"
                "You need to join our channel(s) to access content.\n"
                "Tap the button below, then tap <b>I've Joined</b>.",
                parse_mode=ParseMode.HTML,
                reply_markup=kb.build()
            )
        else:
            await send_sticker_safe(bot, message.chat.id, delay=2)
            await send_ui(message.chat.id, is_returning=bool(welcome_sent))

    # ── REJECTED ───────────────────────────────────────────────────────────
    elif status == 'rejected':
        kb = InlineBuilder()
        kb.add(InlineKeyboardButton(
            "💬 Appeal to Admin",
            url=f"https://t.me/{ADMIN_CONTACT.lstrip('@')}"
        ))
        await message.answer(
            f"❌ <b>Access Not Approved</b>\n\n"
            f"Hi {name}, your access request was not approved at this time.\n\n"
            "If you believe this is a mistake or would like to appeal, "
            "please contact the admin directly.",
            parse_mode=ParseMode.HTML,
            reply_markup=kb.build()
        )

    # ── BANNED ─────────────────────────────────────────────────────────────
    elif status == 'banned':
        kb = InlineBuilder()
        kb.add(InlineKeyboardButton(
            "💬 Appeal to Admin",
            url=f"https://t.me/{ADMIN_CONTACT.lstrip('@')}"
        ))
        await message.answer(
            f"❌ <b>Access Banned</b>\n\n"
            f"Hi {name}, you have violated the rules and hence are now banned. 🚫\n\n"
            "If you believe this is a mistake or would like to appeal, "
            "please contact the admin directly.",
            parse_mode=ParseMode.HTML,
            reply_markup=kb.build()
        )


# ─────────────────────────────────────────────────────────────────────────────
# Admin /approve_<id> and /reject_<id> text commands (legacy / DM style)
# ─────────────────────────────────────────────────────────────────────────────

async def approve_user(message: types.Message):
    bot = get_bot()
    try:
        target_id = int(message.text.split('_')[1])
    except (IndexError, ValueError):
        await message.answer("Invalid format. Use /approve_&lt;user_id&gt;", parse_mode=ParseMode.HTML)
        return

    db_execute("UPDATE users SET status = 'approved' WHERE user_id = %s", (target_id,))
    await message.answer(f"✅ User <code>{target_id}</code> approved.", parse_mode=ParseMode.HTML)

    try:
        await bot.send_message(
            target_id,
            "🎉 <b>Access Granted!</b>\n\n"
            "You've been approved to use the bot.\n\n"
            "👉 Tap /start to get started!",
            parse_mode=ParseMode.HTML
        )
    except TelegramForbiddenError:
        logging.warning(f"User {target_id} has blocked the bot.")
    except TelegramBadRequest:
        logging.warning(f"User {target_id} chat not found.")
    except Exception as e:
        logging.error(f"Error notifying user {target_id} of approval: {e}")


async def reject_user(message: types.Message):
    bot = get_bot()
    try:
        target_id = int(message.text.split('_')[1])
    except (IndexError, ValueError):
        await message.answer("Invalid format. Use /reject_&lt;user_id&gt;", parse_mode=ParseMode.HTML)
        return

    db_execute("UPDATE users SET status = 'rejected' WHERE user_id = %s", (target_id,))
    await message.answer(f"❌ User <code>{target_id}</code> rejected.", parse_mode=ParseMode.HTML)

    try:
        await bot.send_message(
            target_id,
            f"Your access request was not approved. 😢\n\n"
            f"If you think this is a mistake, contact us: {ADMIN_CONTACT}"
        )
    except TelegramForbiddenError:
        logging.warning(f"User {target_id} has blocked the bot.")
    except TelegramBadRequest:
        logging.warning(f"User {target_id} chat not found.")
    except Exception as e:
        logging.error(f"Error notifying user {target_id} of rejection: {e}")
