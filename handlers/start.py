import asyncio
import logging
from aiogram import types, exceptions
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, ParseMode
from middlewares.authorization import is_private_chat, is_user_member, get_channel_title
from utils.database import add_user_to_db, db_fetchone, db_execute, db_fetchall
from utils.helpers import notify_admins, esc
from config import REQUIRED_CHANNELS, STICKER_ID, ADMIN_IDS, PREMIUM_INFO_URL, ADMIN_CONTACT, VERIFY_URL
from datetime import datetime, timedelta

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
    from main import bot
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
    greeting = f"Welcome back, {chat_name}! 👋" if is_returning else f"Hey {chat_name}! 👋"
    text  = f"{greeting}\n\n"
    text += "<b>Medical Content Bot</b> ✨  •  /about  •  /help\n\n"

    if is_premium_user and premium_expiration:
        exp = premium_expiration
        if hasattr(exp, 'tzinfo') and exp.tzinfo:
            exp = exp.replace(tzinfo=None)
        days_left = (exp - datetime.now()).days
        text += f"⭐ <b>Premium User</b> — {days_left} day(s) remaining\n\n"
    elif is_premium_user:
        text += "⭐ <b>Premium User</b>\n\n"
    else:
        text += "🌟 Not premium yet — tap below for info\n\n"

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

    keyboard = InlineKeyboardMarkup(row_width=2)

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

        text += f"📂 <b>Folders</b> (page {page + 1}/{total_pages}) — tap to download:\n"
        text += "───────────────\n\n"

        folder_buttons = []
        for folder_id, folder_name, premium, admin_approval, file_count in page_folders:
            safe_name = esc(folder_name)

            if not is_premium_user and premium:
                tag      = " ⭐"
                btn_icon = "⭐"
            elif admin_approval:
                tag      = " 💰"
                btn_icon = "💰"
            else:
                tag      = ""
                btn_icon = "📒"

            text += f"  <code>{safe_name}</code>{tag} — <i>{file_count} files</i>\n"

            label = f"{btn_icon} {folder_name}"
            if len(label) > 32:
                label = label[:29] + "…"
            folder_buttons.append(
                InlineKeyboardButton(label, callback_data=f"dl:{folder_id}")
            )

        text += "\n───────────────\n"

        # Folder buttons (2 per row)
        for i in range(0, len(folder_buttons), 2):
            keyboard.row(*folder_buttons[i:i + 2])

        # Pagination controls
        nav_buttons = []
        if page > 0:
            nav_buttons.append(InlineKeyboardButton("◀ Prev", callback_data=f"pg:{page - 1}"))
        nav_buttons.append(InlineKeyboardButton("🔄 Refresh", callback_data=f"pg:{page}"))
        if page < total_pages - 1:
            nav_buttons.append(InlineKeyboardButton("Next ▶", callback_data=f"pg:{page + 1}"))
        keyboard.row(*nav_buttons)

        # Info / contact buttons (always shown at the bottom)
        if not is_premium_user:
            keyboard.row(
                InlineKeyboardButton("💳 Get Premium",   callback_data="info_premium"),
                InlineKeyboardButton("💬 Contact Admin", url=f"https://t.me/{ADMIN_CONTACT.lstrip('@')}"),
            )
        else:
            keyboard.row(
                InlineKeyboardButton("💬 Contact Admin", url=f"https://t.me/{ADMIN_CONTACT.lstrip('@')}"),
            )


    try:
        if message_id:
            await bot.edit_message_text(
                chat_id=chat_id, message_id=message_id,
                text=text, reply_markup=keyboard, parse_mode=ParseMode.HTML
            )
        else:
            await bot.send_message(chat_id, text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
    except exceptions.MessageNotModified:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Unified callback handler (ALL callbacks route through here)
# ─────────────────────────────────────────────────────────────────────────────

async def process_callback(callback_query: types.CallbackQuery):
    """Single entry point for every inline keyboard callback in the bot.

    Dispatches on callback_data prefix:
      pg:<n>          — folder list page navigation / refresh
      dl:<folder_id>  — trigger folder download
      approve:<uid>   — admin approves a pending user  (from admin group)
      reject:<uid>    — admin rejects a pending user   (from admin group)
      dfc:<folder_id> — confirm folder deletion
      dfc_cancel      — cancel folder deletion
    """
    from main import bot
    user_id = callback_query.from_user.id
    data    = callback_query.data or ''

    # ── Page navigation / refresh ─────────────────────────────────────────────
    if data.startswith('pg:'):
        try:
            page = int(data.split(':', 1)[1])
        except (ValueError, IndexError):
            page = 0

        user_row = db_fetchone('SELECT status FROM users WHERE user_id = %s', (user_id,))
        user_status = user_row[0] if user_row else 'pending'
        if user_status != 'approved':
            await bot.answer_callback_query(
                callback_query.id,
                "You are not yet approved. Please wait for admin approval.",
                show_alert=True
            )
            return

        if not await is_user_member(user_id):
            await bot.answer_callback_query(callback_query.id, "Please join the required channels first.")
            return

        await bot.answer_callback_query(callback_query.id)
        await send_ui(user_id, callback_query.message.message_id,
                      is_returning=True, page=page)
        return

    # ── Folder download button ────────────────────────────────────────────────
    if data.startswith('dl:'):
        try:
            folder_id = int(data.split(':', 1)[1])
        except (ValueError, IndexError):
            await bot.answer_callback_query(callback_query.id, "Invalid folder.")
            return

        from handlers.download import trigger_folder_download
        asyncio.create_task(
            trigger_folder_download(
                user_id, folder_id,
                callback_query.message.chat.id,
                callback_query.id,
                callback_query.message.message_id,
            )
        )
        return

    # ── Admin: approve pending user ───────────────────────────────────────────
    if data.startswith('approve:'):
        if str(user_id) not in ADMIN_IDS:
            await bot.answer_callback_query(callback_query.id, "Not authorized.", show_alert=True)
            return
        try:
            target_id = int(data.split(':', 1)[1])
        except (ValueError, IndexError):
            await bot.answer_callback_query(callback_query.id, "Invalid user ID.")
            return

        db_execute("UPDATE users SET status = 'approved' WHERE user_id = %s", (target_id,))
        admin_name = esc(callback_query.from_user.first_name or str(user_id))
        await bot.answer_callback_query(callback_query.id, f"✅ Approved user {target_id}")

        # Update the group message
        try:
            await bot.edit_message_reply_markup(
                chat_id=callback_query.message.chat.id,
                message_id=callback_query.message.message_id,
                reply_markup=None
            )
            await bot.edit_message_text(
                chat_id=callback_query.message.chat.id,
                message_id=callback_query.message.message_id,
                text=callback_query.message.text + f"\n\n✅ <b>Approved</b> by {admin_name}",
                parse_mode=ParseMode.HTML
            )
        except Exception:
            pass

        # Notify the user
        try:
            await bot.send_message(
                target_id,
                "🎉 <b>Access Granted!</b>\n\n"
                "You've been approved to use the bot.\n\n"
                "👉 Tap /start to get started!",
                parse_mode=ParseMode.HTML
            )
        except exceptions.BotBlocked:
            logging.warning(f"User {target_id} has blocked the bot.")
        except Exception as e:
            logging.error(f"Error notifying user {target_id} of approval: {e}")
        return

    # ── Admin: reject pending user ────────────────────────────────────────────
    if data.startswith('reject:'):
        if str(user_id) not in ADMIN_IDS:
            await bot.answer_callback_query(callback_query.id, "Not authorized.", show_alert=True)
            return
        try:
            target_id = int(data.split(':', 1)[1])
        except (ValueError, IndexError):
            await bot.answer_callback_query(callback_query.id, "Invalid user ID.")
            return

        db_execute("UPDATE users SET status = 'rejected' WHERE user_id = %s", (target_id,))
        admin_name = esc(callback_query.from_user.first_name or str(user_id))
        await bot.answer_callback_query(callback_query.id, f"❌ Rejected user {target_id}")

        try:
            await bot.edit_message_text(
                chat_id=callback_query.message.chat.id,
                message_id=callback_query.message.message_id,
                text=callback_query.message.text + f"\n\n❌ <b>Rejected</b> by {admin_name}",
                parse_mode=ParseMode.HTML
            )
        except Exception:
            pass

        try:
            await bot.send_message(
                target_id,
                f"Your access request was not approved. 😢\n\n"
                f"If you think this is a mistake, contact us: {ADMIN_CONTACT}"
            )
        except exceptions.BotBlocked:
            logging.warning(f"User {target_id} has blocked the bot.")
        except Exception as e:
            logging.error(f"Error notifying user {target_id} of rejection: {e}")
        return

    # ── Admin: approve paid-folder request ───────────────────────────────────
    if data.startswith('papprove:'):
        if str(user_id) not in ADMIN_IDS:
            await bot.answer_callback_query(callback_query.id, "Not authorized.", show_alert=True)
            return
        try:
            _, target_id, folder_id = data.split(':', 2)
            target_id, folder_id = int(target_id), int(folder_id)
        except (ValueError, IndexError):
            await bot.answer_callback_query(callback_query.id, "Invalid data.")
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

        # Fetch folder name for the user notification
        folder_row = db_fetchone('SELECT name FROM folders WHERE id = %s', (folder_id,))
        folder_name = folder_row[0] if folder_row else f"Folder #{folder_id}"

        admin_name = esc(callback_query.from_user.first_name or str(user_id))
        await bot.answer_callback_query(callback_query.id, f"✅ Approved download for user {target_id}")

        # Update the group message
        try:
            await bot.edit_message_text(
                chat_id=callback_query.message.chat.id,
                message_id=callback_query.message.message_id,
                text=callback_query.message.text + f"\n\n✅ <b>Approved</b> by {admin_name}",
                parse_mode=ParseMode.HTML,
                reply_markup=None
            )
        except Exception:
            pass

        # Notify the user
        try:
            await bot.send_message(
                target_id,
                f"✅ <b>Download Approved!</b>\n\n"
                f"Your request for <b>{esc(folder_name)}</b> has been approved.\n"
                f"You get <b>1 download</b> at Premium speed.\n\n"
                f"Use /start and tap the folder to begin.",
                parse_mode=ParseMode.HTML
            )
        except exceptions.BotBlocked:
            logging.warning(f"User {target_id} has blocked the bot.")
        except Exception as e:
            logging.error(f"Error notifying user {target_id} of paid-folder approval: {e}")
        return

    # ── Admin: reject paid-folder request ────────────────────────────────────
    if data.startswith('preject:'):
        if str(user_id) not in ADMIN_IDS:
            await bot.answer_callback_query(callback_query.id, "Not authorized.", show_alert=True)
            return
        try:
            _, target_id, folder_id = data.split(':', 2)
            target_id, folder_id = int(target_id), int(folder_id)
        except (ValueError, IndexError):
            await bot.answer_callback_query(callback_query.id, "Invalid data.")
            return

        db_execute(
            'DELETE FROM user_folder_approval WHERE user_id = %s AND folder_id = %s',
            (target_id, folder_id)
        )

        folder_row = db_fetchone('SELECT name FROM folders WHERE id = %s', (folder_id,))
        folder_name = folder_row[0] if folder_row else f"Folder #{folder_id}"

        admin_name = esc(callback_query.from_user.first_name or str(user_id))
        await bot.answer_callback_query(callback_query.id, f"❌ Rejected request for user {target_id}")

        try:
            await bot.edit_message_text(
                chat_id=callback_query.message.chat.id,
                message_id=callback_query.message.message_id,
                text=callback_query.message.text + f"\n\n❌ <b>Rejected</b> by {admin_name}",
                parse_mode=ParseMode.HTML,
                reply_markup=None
            )
        except Exception:
            pass

        try:
            await bot.send_message(
                target_id,
                f"❌ Your request for <b>{esc(folder_name)}</b> was not approved.\n\n"
                f"If you think this is a mistake, contact us: {ADMIN_CONTACT}",
                parse_mode=ParseMode.HTML
            )
        except exceptions.BotBlocked:
            logging.warning(f"User {target_id} has blocked the bot.")
        except Exception as e:
            logging.error(f"Error notifying user {target_id} of paid-folder rejection: {e}")
        return

    # ── Folder deletion: confirm ──────────────────────────────────────────────
    if data.startswith('dfc:'):
        if str(user_id) not in ADMIN_IDS:
            await bot.answer_callback_query(callback_query.id, "Not authorized.", show_alert=True)
            return

        pending = _pending_deletions.pop(user_id, None)
        if not pending:
            await bot.answer_callback_query(callback_query.id)
            await bot.edit_message_text(
                "Session expired. Please use /deletefolder again.",
                chat_id=callback_query.message.chat.id,
                message_id=callback_query.message.message_id
            )
            return

        folder_name, folder_id = pending
        from handlers.folder import execute_folder_deletion
        await bot.answer_callback_query(callback_query.id, "🗑 Deleting…")
        await execute_folder_deletion(bot, callback_query.message, folder_id, folder_name)
        return

    # ── Folder deletion: cancel ───────────────────────────────────────────────
    if data == 'dfc_cancel':
        _pending_deletions.pop(user_id, None)
        await bot.answer_callback_query(callback_query.id, "Cancelled.")
        try:
            await bot.edit_message_text(
                "❌ Folder deletion cancelled.",
                chat_id=callback_query.message.chat.id,
                message_id=callback_query.message.message_id
            )
        except Exception:
            pass
        return

    # ── Broadcast: send confirmed ─────────────────────────────────────────────
    if data.startswith('bcast_send:'):
        if str(user_id) not in ADMIN_IDS:
            await bot.answer_callback_query(callback_query.id, "Not authorized.", show_alert=True)
            return
        try:
            broadcast_id = int(data.split(':', 1)[1])
        except (ValueError, IndexError):
            await bot.answer_callback_query(callback_query.id, "Invalid broadcast ID.")
            return
        from handlers.broadcast import execute_broadcast
        asyncio.create_task(execute_broadcast(callback_query, broadcast_id))
        return

    # ── Broadcast: cancel ─────────────────────────────────────────────────────
    if data.startswith('bcast_cancel:'):
        if str(user_id) not in ADMIN_IDS:
            await bot.answer_callback_query(callback_query.id, "Not authorized.", show_alert=True)
            return
        try:
            broadcast_id = int(data.split(':', 1)[1])
        except (ValueError, IndexError):
            await bot.answer_callback_query(callback_query.id, "Invalid broadcast ID.")
            return
        from handlers.broadcast import cancel_broadcast
        await cancel_broadcast(callback_query, broadcast_id)
        return

    # ── Payment: plan picker & cancel ─────────────────────────────────────────
    if data.startswith('pay_plan:') or data == 'pay_cancel':
        from handlers.payment import handle_pay_callback
        await handle_pay_callback(callback_query)
        return

    # ── Info dialogs: Premium info (in-place overlay / modal) ────────────────
    if data == 'info_premium':
        await bot.answer_callback_query(callback_query.id)
        # Pull live plans from DB for the overlay
        from handlers.payment import _get_plans, _fmt_inr
        plans = _get_plans()

        kb = InlineKeyboardMarkup(row_width=1)
        if plans:
            for plan_id, name, amount_paise, days in plans:
                kb.add(InlineKeyboardButton(
                    f"💳 {name} — {_fmt_inr(amount_paise)} ({days} days)",
                    callback_data=f"pay_plan:{plan_id}"
                ))
        kb.row(
            InlineKeyboardButton(
                "💬 Contact Admin",
                url=f"https://t.me/{ADMIN_CONTACT.lstrip('@')}"
            )
        )
        kb.row(InlineKeyboardButton("◀ Back", callback_data="back_to_main"))

        # Build plan text
        if plans:
            plan_lines = "\n".join(
                f"  • <b>{name}</b> — {_fmt_inr(amount_paise)} / {days} days"
                for _, name, amount_paise, days in plans
            )
            how_to = "Tap a plan below to pay via UPI / Card / Net Banking."
        else:
            plan_lines = "  Contact admin for current pricing."
            how_to = f"Message {ADMIN_CONTACT} to get your plan activated."

        try:
            await bot.edit_message_text(
                chat_id=callback_query.message.chat.id,
                message_id=callback_query.message.message_id,
                text=(
                    "⭐ <b>Premium Membership</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    "<b>What you get:</b>\n"
                    "  • ⚡ 5s interval between files  <i>(vs 60s free)</i>\n"
                    "  • ⏱ 2 min cooldown  <i>(vs 7 min free)</i>\n"
                    "  • ⭐ Access to all Premium-only folders\n\n"
                    f"<b>Plans:</b>\n{plan_lines}\n\n"
                    f"<b>How to subscribe:</b>\n  {how_to}\n\n"
                    "<i>Tap ◀ Back to return to the folder list.</i>"
                ),
                parse_mode=ParseMode.HTML,
                reply_markup=kb
            )
        except Exception:
            pass
        return


    # ── Info dialogs: Verification info (in-place overlay / modal) ───────────
    if data == 'info_verify':
        await bot.answer_callback_query(callback_query.id)
        kb = InlineKeyboardMarkup()
        kb.row(
            InlineKeyboardButton(
                "📨 Message Admin",
                url=f"https://t.me/{ADMIN_CONTACT.lstrip('@')}"
            )
        )
        kb.row(InlineKeyboardButton("◀ Back", callback_data="back_to_main"))
        try:
            await bot.edit_message_text(
                chat_id=callback_query.message.chat.id,
                message_id=callback_query.message.message_id,
                text=(
                    "🎓 <b>Student Verification</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━━━\n\n"
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
                    "Tap ◀ Back to return.</i>"
                ),
                parse_mode=ParseMode.HTML,
                reply_markup=kb
            )
        except Exception:
            pass
        return

    # ── Info dialogs: back to main UI (from any in-place overlay) ────────────
    if data in ('close_info', 'back_to_main'):
        await bot.answer_callback_query(callback_query.id)

        user_row = db_fetchone('SELECT status, first_name FROM users WHERE user_id = %s', (user_id,))
        user_status = user_row[0] if user_row else 'pending'
        first_name  = user_row[1] if user_row else 'there'

        if user_status == 'pending':
            kb = InlineKeyboardMarkup()
            kb.row(
                InlineKeyboardButton("🎓 How to Verify", callback_data="info_verify"),
                InlineKeyboardButton("💬 Contact Admin", url=f"https://t.me/{ADMIN_CONTACT.lstrip('@')}"),
            )
            try:
                await bot.edit_message_text(
                    chat_id=callback_query.message.chat.id,
                    message_id=callback_query.message.message_id,
                    text=(
                        f"Hello {esc(first_name or 'there')}! 👋\n\n"
                        "<b>I'm The Medical Content Bot</b> ✨\n\n"
                        "Access is limited to verified medical students to protect the content. 🙃\n\n"
                        "Your request has been sent to an admin.\n"
                        "Tap <b>How to Verify</b> below to see what to send them.\n\n"
                        "You'll be notified here as soon as your request is reviewed! ✅"
                    ),
                    parse_mode=ParseMode.HTML,
                    reply_markup=kb,
                )
            except Exception:
                pass
        elif user_status == 'rejected':
            try:
                await bot.edit_message_text(
                    chat_id=callback_query.message.chat.id,
                    message_id=callback_query.message.message_id,
                    text=(
                        f"Your access request was not approved. 😢\n\n"
                        f"If you think this is a mistake, contact us: {ADMIN_CONTACT}"
                    ),
                    reply_markup=None,
                )
            except Exception:
                pass
        else:
            # Approved — show the folder list
            await send_ui(
                user_id,
                message_id=callback_query.message.message_id,
                is_returning=True
            )
        return

    # ── Unknown callback — just acknowledge ───────────────────────────────────
    await bot.answer_callback_query(callback_query.id)



# ─────────────────────────────────────────────────────────────────────────────
# /start
# ─────────────────────────────────────────────────────────────────────────────

async def handle_start(message: types.Message):
    from main import bot
    if not is_private_chat(message):
        return

    user_id    = message.from_user.id
    username   = message.from_user.username
    first_name = message.from_user.first_name

    add_user_to_db(user_id, username=username, first_name=first_name)

    user = db_fetchone(
        'SELECT status, welcome_sent FROM users WHERE user_id = %s',
        (user_id,)
    )
    if not user:
        await message.answer("Something went wrong. Please try again.")
        return

    status, welcome_sent = user

    if status == 'pending':
        kb = InlineKeyboardMarkup()
        kb.row(
            InlineKeyboardButton("🎓 How to Verify", callback_data="info_verify"),
            InlineKeyboardButton("💬 Contact Admin", url=f"https://t.me/{ADMIN_CONTACT.lstrip('@')}"),
        )
        await message.answer(
            f"Hello {esc(first_name or 'there')}! 👋\n\n"
            "<b>I'm The Medical Content Bot</b> ✨\n\n"
            "Access is limited to verified medical students to protect the content. 🙃\n\n"
            "Your request has been sent to an admin.\n"
            "Tap <b>How to Verify</b> below to see what to send them.\n\n"
            "You'll be notified here as soon as your request is reviewed! ✅",
            parse_mode=ParseMode.HTML,
            reply_markup=kb
        )
        await notify_admins(user_id, username, first_name)

    elif status == 'approved':
        if not welcome_sent:
            await message.answer(
                "🎉 <b>Welcome!</b> You've been granted access to the bot.\n\n"
                "Use /help to learn how to download content.",
                parse_mode=ParseMode.HTML
            )
            db_execute(
                'UPDATE users SET welcome_sent = TRUE WHERE user_id = %s',
                (user_id,)
            )

        if not await is_user_member(user_id):
            await send_sticker_safe(bot, message.chat.id, delay=3)
            kb = InlineKeyboardMarkup(row_width=1)
            for channel in REQUIRED_CHANNELS:
                title = await get_channel_title(channel)
                kb.add(InlineKeyboardButton(
                    f"📢 {title}", url=f"https://t.me/{channel.lstrip('@')}"
                ))
            await message.reply(
                "Please join our channels to continue using the bot 👇\n\n"
                "After joining, send /start again.",
                reply_markup=kb
            )
        else:
            await send_sticker_safe(bot, message.chat.id, delay=2)
            await send_ui(message.chat.id, is_returning=bool(welcome_sent))

    elif status == 'rejected':
        await message.answer(
            f"Your access request was not approved. 😢\n\n"
            f"If you think this is a mistake, contact us: {ADMIN_CONTACT}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Admin /approve_<id> and /reject_<id> text commands (legacy / DM style)
# ─────────────────────────────────────────────────────────────────────────────

async def approve_user(message: types.Message):
    from main import bot
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
    except exceptions.BotBlocked:
        logging.warning(f"User {target_id} has blocked the bot.")
    except exceptions.ChatNotFound:
        logging.warning(f"User {target_id} chat not found.")
    except Exception as e:
        logging.error(f"Error notifying user {target_id} of approval: {e}")


async def reject_user(message: types.Message):
    from main import bot
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
    except exceptions.BotBlocked:
        logging.warning(f"User {target_id} has blocked the bot.")
    except exceptions.ChatNotFound:
        logging.warning(f"User {target_id} chat not found.")
    except Exception as e:
        logging.error(f"Error notifying user {target_id} of rejection: {e}")