from utils.keyboard import InlineBuilder
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest
from aiogram import Router
from utils.bot_ref import get_bot
import asyncio
import logging
from aiogram import types
from aiogram import Router
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram import Router
from middlewares.authorization import is_private_chat, is_user_member, get_channel_title, invalidate_member_cache
from utils.database import add_user_to_db, db_fetchone, db_execute, db_fetchall
from utils.helpers import notify_admins, esc
from config import REQUIRED_CHANNELS, STICKER_ID, ADMIN_IDS, PREMIUM_INFO_URL, ADMIN_CONTACT, VERIFY_URL, PAYMENT_MODE
from datetime import datetime, timedelta

router = Router()

log = logging.getLogger(__name__)

# Global throttle for auto-sync
last_sync_time = None
sync_lock = asyncio.Lock()

# Folders per page in the UI (Telegram limit: 100 buttons/msg, ~30–40 safe)
_PAGE_SIZE = 20

# In-memory pending folder deletions: { user_id: (folder_name, folder_id) }
_pending_deletions: dict = {}


# ─────────────────────────────────────────────────────────────────────────────
# Sticker helper
# ─────────────────────────────────────────────────────────────────────────────

async def send_sticker_safe(bot, chat_id: int, delay: float = 2.0):
    if not STICKER_ID:
        return None
    try:
        msg = await bot.send_sticker(chat_id, STICKER_ID)
        await asyncio.sleep(delay)
        await bot.delete_message(chat_id, msg.message_id)
        return msg
    except exceptions.BadRequest as e:
        logging.warning(f"Sticker send failed (bad file_id?): {e}")
    except Exception as e:
        logging.warning(f"Sticker send failed: {e}")
    return None


# ─────────────────────────────────────────────────────────────────────────────
# UI builder (HTML, paginated)
# ─────────────────────────────────────────────────────────────────────────────

async def send_ui(chat_id: int, message_id: int = None,
                  is_returning: bool = False, page: int = 0):
    global last_sync_time

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
        f"👋 Welcome, {greeting}!\n\n"
        "🏥 <b>Medical Content Bot</b> ✨\n\n"
    )

    if is_premium_user and premium_expiration:
        exp = premium_expiration
        if hasattr(exp, 'tzinfo') and exp.tzinfo:
            exp = exp.replace(tzinfo=None)
        days_left = (exp - datetime.now()).days
        text += f"⭐ <b>Premium Access Active</b>\n🕒 Expires in: <code>{days_left} day(s)</code>\n\n"
    elif is_premium_user:
        text += "⭐ <b>Premium Access Active</b>\n🕒 Lifetime access\n\n"
    else:
        text += "🔓 <b>Free Tier Active</b>\n💡 Upgrade to Premium for max download speed & no cooldowns.\n\n"

    # ── Fetch all folders ─────────────────────────────────────────────────────
    all_folders = db_fetchall(
        '''
        SELECT f.id, f.name, f.premium, f.admin_approval,
               COUNT(fi.id) AS file_count
        FROM folders f
        LEFT JOIN files fi ON fi.folder_id = f.id
        WHERE f.parent_id IS NULL
        GROUP BY f.id, f.name, f.premium, f.admin_approval
        ORDER BY f.name
        '''
    )

    keyboard = InlineBuilder()

    if not all_folders:
        text += (
            "📂 <b>No folders yet</b>\n\n"
            "Content is being added — check back soon!\n"
            "Tap the button below to refresh."
        )
        keyboard.add(InlineKeyboardButton("🔄 Refresh", callback_data='pg:0'))

        now = datetime.now()
        if last_sync_time is None or (now - last_sync_time) >= timedelta(minutes=20):
            async with sync_lock:
                if last_sync_time is None or (datetime.now() - last_sync_time) >= timedelta(minutes=20):
                    last_sync_time = datetime.now()
                    logging.info("Auto-sync triggered.")
    else:
        total_pages = max(1, (len(all_folders) + _PAGE_SIZE - 1) // _PAGE_SIZE)
        page        = max(0, min(page, total_pages - 1))
        page_folders = all_folders[page * _PAGE_SIZE:(page + 1) * _PAGE_SIZE]

        text += f"📂 <b>Available Folders</b> (page {page + 1}/{total_pages}):\n\n"

        folder_buttons = []
        for folder_id, folder_name, premium, admin_approval, file_count in page_folders:
            safe_name = esc(folder_name)

            if not is_premium_user and premium:
                tag      = " [⭐ Premium]"
                btn_icon = "⭐"
            elif admin_approval:
                tag      = " [💰 Paid]"
                btn_icon = "💰"
            else:
                tag      = ""
                btn_icon = "📁"

            text += f"• <code>{safe_name}</code>{tag} — <i>{file_count} file{'s' if file_count != 1 else ''}</i>\n"

            label = f"{btn_icon} {folder_name} ({file_count})"
            if len(label) > 32:
                label = label[:29] + "…"
            folder_buttons.append(
                InlineKeyboardButton(label, callback_data=f"dl:{folder_id}")
            )

        text += "\n"

        # Folder buttons (1 per row to span the full message width)
        for btn in folder_buttons:
            keyboard.row(btn)

        # Pagination controls
        nav_buttons = []
        if page > 0:
            nav_buttons.append(InlineKeyboardButton(f"◀️ Page {page}", callback_data=f"pg:{page - 1}"))
        nav_buttons.append(InlineKeyboardButton("🔄 Refresh", callback_data=f"pg:{page}"))
        if page < total_pages - 1:
            nav_buttons.append(InlineKeyboardButton(f"Page {page + 2} ▶️", callback_data=f"pg:{page + 1}"))
        keyboard.row(*nav_buttons)

        # Info / contact buttons
        if not is_premium_user:
            keyboard.row(InlineKeyboardButton("⭐ Get Premium", callback_data="info_premium"))

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


# ─────────────────────────────────────────────────────────────────────────────
# Individual callback handlers (module-level for dict-dispatch)
# ─────────────────────────────────────────────────────────────────────────────

async def _cb_page(cq: types.CallbackQuery, bot, user_id: int) -> None:
    try:
        page = int(cq.data.split(':', 1)[1])
    except (ValueError, IndexError):
        page = 0

    user_row = db_fetchone('SELECT status FROM users WHERE user_id = %s', (user_id,))
    if (user_row[0] if user_row else 'pending') != 'approved':
        await bot.answer_callback_query(
            cq.id, "You are not yet approved. Please wait for admin approval.", show_alert=True
        )
        return

    invalidate_member_cache(user_id)
    if not await is_user_member(user_id):
        await cq.answer("Please join the required channels first.")
        return

    await cq.answer()
    await send_ui(user_id, cq.message.message_id, is_returning=True, page=page)


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
                f"⭐ <b>Premium Membership</b>\n\n"
                "<b>What you get:</b>\n"
                f"  • ⚡ 5s interval between files  <i>(vs 60s free)</i>\n"
                f"  • ⏱ 2 min cooldown  <i>(vs 7 min free)</i>\n"
                f"  • ⭐ Access to all Premium-only folders\n\n"
                f"<b>Plans:</b>\n{plan_lines}\n\n"
                f"<b>How to subscribe:</b>\n  {how_to}\n\n"
                f"<i>Tap 🔙 Back to Menu to return to the folder list.</i>"
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
                "🎓 <b>Student Verification</b>\n\n"
                "Access is limited to verified medical students\n"
                "to protect our content from redistribution.\n\n"
                "<b>How to verify:</b>\n"
                "  1️⃣ Take a photo of your student ID or enrollment letter\n"
                f"  2️⃣ Send it to: {ADMIN_CONTACT}\n"
                "  3️⃣ Admin reviews and approves within a few hours\n\n"
                "<b>Accepted documents:</b>\n"
                "  • College / University student ID card\n"
                "  • Enrollment certificate\n"
                "  • Fee receipt with your name + course\n\n"
                "<i>Once approved you'll get a notification here.\n"
                "Tap 🔙 Back to Menu to return.</i>"
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
                    f"Hello {esc(first_name or 'there')}! 👋\n\n"
                    "<b>I'm The Medical Content Bot</b> ✨\n\n"
                    "Access is limited to verified medical students to protect the content. 🙃\n\n"
                    "Your request has been sent to an admin.\n"
                    "Tap <b>How to Verify</b> below to see what to send them.\n\n"
                    "You'll be notified here as soon as your request is reviewed! ✅"
                ),
                parse_mode=ParseMode.HTML, reply_markup=kb.build(),
            )
        except Exception:
            pass
    elif user_status == 'rejected':
        try:
            await bot.edit_message_text(
                chat_id=cq.message.chat.id, message_id=cq.message.message_id,
                text=(
                    f"Your access request was not approved. 😢\n\n"
                    f"If you think this is a mistake, contact us: {ADMIN_CONTACT}"
                ),
                reply_markup=None,
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
        else:
            await send_ui(user_id, message_id=cq.message.message_id, is_returning=True)


# ── Dispatch table — split on ':' gives the key for both prefix and exact data
_CB_HANDLERS = {
    "pg":           _cb_page,
    "dl":           _cb_download,
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
}


# ─────────────────────────────────────────────────────────────────────────────
# Unified callback handler (ALL callbacks route through here)
# ─────────────────────────────────────────────────────────────────────────────

async def process_callback(callback_query: types.CallbackQuery):
    """Route every inline keyboard callback via _CB_HANDLERS dict-dispatch."""
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

    # Show typing indicator while we process
    await bot.send_chat_action(message.chat.id, ChatAction.TYPING)

    add_user_to_db(user_id, username=username, first_name=first_name)

    user = db_fetchone(
        'SELECT status, welcome_sent FROM users WHERE user_id = %s',
        (user_id,)
    )
    if not user:
        await message.answer(
            "⚠️ <b>Something went wrong.</b>\n\n"
            "Please try again in a moment.",
            parse_mode=ParseMode.HTML
        )
        return

    status, welcome_sent = user

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
                "🏥 <b>Welcome to the Medical Content Bot</b>\n\n"
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
                "📂 <b>Browse Folders</b> — tap any folder button to download all its files\n"
                "⬇️ <b>/download \u003cfolder name\u003e</b> — download by typing the name\n"
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
            kb.add(InlineKeyboardButton("✅ I've Joined — Refresh", callback_data="pg:0"))
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


# ─────────────────────────────────────────────────────────────────────────────
# Admin /approve_<id> and /reject_<id> text commands (legacy / DM style)
# ─────────────────────────────────────────────────────────────────────────────

async def approve_user(message: types.Message):
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
