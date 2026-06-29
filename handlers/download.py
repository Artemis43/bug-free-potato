import asyncio
import logging
from datetime import datetime, timedelta

from aiogram import Router, types
from aiogram.enums import ChatAction, ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.exceptions import TelegramBadRequest as MessageNotModified
from aiogram.types import InlineKeyboardMarkup
from utils.keyboard import IKB as InlineKeyboardButton

from config import ADMIN_CONTACT, PAYMENT_MODE, REQUIRED_CHANNELS
from middlewares.authorization import (
    invalidate_member_cache,
    is_private_chat,
    is_user_member,
)
from utils.bot_ref import get_bot
from utils.bots import get_current_bot_pk, get_servable_locations
from utils.database import db_execute, db_fetchall, db_fetchone
from utils.helpers import esc, notify_admin_for_approval, notify_admin_for_approval_again
from utils.keyboard import InlineBuilder
import utils.progress as progress

log = logging.getLogger(__name__)
router = Router()


async def _deliver_file(bot, chat_id: int, locations):
    """Copy a file to the user from the first working storage channel.

    ``locations`` is an ordered list of ``(src_chat_id, src_message_id)`` the
    serving bot may copy from. Returns ``(sent, user_blocked)``:
      - ``sent`` — the copy_message result, or None if no channel could serve it
      - ``user_blocked`` — True if the user has blocked the bot (caller aborts)

    Trying each channel in turn is the delivery-time redundancy: if one channel
    was taken down or the bot lost access, the next copy still delivers.
    """
    for src_chat_id, src_msg_id in locations:
        try:
            sent = await bot.copy_message(chat_id, src_chat_id, src_msg_id)
            return sent, False
        except TelegramForbiddenError as e:
            # "bot was blocked by the user" is about the destination — abort.
            # Anything else means we lost access to this source channel; fall
            # back to the next one.
            if 'block' in str(e).lower():
                return None, True
            log.warning(f"No access to channel {src_chat_id}: {e}; trying next channel")
            continue
        except TelegramBadRequest as e:
            log.warning(f"Copy of msg {src_msg_id} from {src_chat_id} failed: {e}; trying next channel")
            continue
        except Exception as e:
            log.error(f"Unexpected error copying from {src_chat_id}: {e}")
            continue
    return None, False


async def _run_download(
    bot, chat_id: int, user_id: int,
    folder_id: int, folder_name: str,
    is_premium: bool, is_premium_folder: bool,
    requires_admin_approval: bool,
):
    """
    Background coroutine: send all files in a folder, showing live per-file
    progress with a cancel button, then schedule deletion.
    """

    # ── Guard: prevent duplicate downloads ────────────────────────────────
    if progress.is_downloading(chat_id):
        try:
            await bot.send_message(
                chat_id,
                "⚠️ <b>Download already in progress!</b>\n"
                "Please wait for the current download to finish, "
                "or tap Cancel on the progress message.",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass
        return

    # Show upload indicator while preparing
    try:
        await bot.send_chat_action(chat_id, ChatAction.UPLOAD_DOCUMENT)
    except Exception:
        pass

    # ── Fetch files first so we know the total ────────────────────────────
    # We fetch the logical file id and resolve a storage channel to copy from
    # per file at send time (see the loop). file_id is NOT used for delivery —
    # Telegram file_ids are bot-specific, so files are served via copy_message
    # from a storage channel this bot administers.
    files = db_fetchall(
        'SELECT id, file_name FROM files WHERE folder_id = %s ORDER BY id',
        (folder_id,)
    )
    if not files:
        await bot.send_message(
            chat_id,
            "⚠️ <b>No files found</b> in this folder.\n"
            "<b>──────────────────────────────────</b>\n\n"
            "Content is being added soon.\n"
            "Use /start to return to the menu.",
            parse_mode=ParseMode.HTML,
        )
        return

    n             = len(files)
    file_interval = 5 if is_premium else 60

    if   n <= 25: delete_time = 120
    elif n <= 50: delete_time = 180
    elif n <= 75: delete_time = 240
    else:         delete_time = 300

    # ── Register download session ─────────────────────────────────────────
    progress.start_download(chat_id, folder_name, n)

    # ── Info card + Cancel button ─────────────────────────────────────────
    time_interval   = timedelta(minutes=2) if is_premium else timedelta(minutes=7)
    next_dl_minutes = int(time_interval.total_seconds() / 60)

    if requires_admin_approval: folder_type = "Paid"
    elif is_premium_folder:     folder_type = "Premium"
    else:                       folder_type = "Free"

    name_row     = db_fetchone('SELECT first_name FROM users WHERE user_id = %s', (user_id,))
    display_name = esc(name_row[0] if name_row and name_row[0] else f"User {user_id}")

    tier     = "Premium" if is_premium else "Free"
    tier_ico = "⭐" if is_premium else "👤"

    if not is_premium:
        upsell = (
            "\n\n💳 Use /pay for 5s intervals\n"
            "   & no cooldowns between downloads"
            if PAYMENT_MODE in ('stars', 'razorpay')
            else '\n\n💳 Contact ' + ADMIN_CONTACT + '\n   to upgrade for 5s intervals'
        )
    else:
        upsell = ""

    cancel_kb = InlineBuilder()
    cancel_kb.add(InlineKeyboardButton("❌ Cancel Download", callback_data=f"cancel_dl:{chat_id}"))

    progress_text = progress.build_progress_text(chat_id, file_interval)
    info_text = (
        f"{tier_ico} <b>{tier} Download</b>\n"
        f"<b>──────────────────────────────────</b>\n"
        f"👤 {display_name}\n"
        f"📂 <code>{esc(folder_name)}</code> ({folder_type})\n"
        f"⏱ Interval: <code>{file_interval}s</code>/file\n"
        f"⏳ Next download in: <code>{next_dl_minutes} min</code>"
        f"{upsell}\n"
        f"<b>──────────────────────────────────</b>\n"
        f"{progress_text}"
    )

    try:
        prog_msg = await bot.send_message(
            chat_id, info_text,
            parse_mode=ParseMode.HTML,
            reply_markup=cancel_kb.build(),
        )
        progress.set_progress_msg_id(chat_id, prog_msg.message_id)
    except Exception as e:
        log.error(f"Could not send progress message to {chat_id}: {e}")
        progress.finish_download(chat_id)
        return

    # ── Update download counter ───────────────────────────────────────────
    db_execute(
        'UPDATE folders SET download_count = download_count + 1 WHERE id = %s',
        (folder_id,)
    )

    # Record cooldown before sending (prevents re-download on crash)
    db_execute('UPDATE users SET last_download = %s WHERE user_id = %s', (datetime.now(), user_id))

    # ── Send files with live progress ─────────────────────────────────────
    messages_to_delete: list[int] = []
    unavailable = 0
    bot_pk = get_current_bot_pk()
    if bot_pk is None:
        log.error("This bot is not registered (bot_pk is None); cannot serve files.")

    for index, (file_pk, file_name) in enumerate(files):
        # Check for cancel signal
        if progress.is_cancelled(chat_id):
            log.info(f"Download cancelled by user {user_id} at file {index + 1}/{n}")
            break

        # Serve by copying the file from a storage channel THIS bot is paired
        # with, trying each in turn — that fallback IS the redundancy (see
        # _deliver_file). copy_message preserves the stored caption.
        locations = get_servable_locations(file_pk, bot_pk) if bot_pk else []
        sent, user_blocked = await _deliver_file(bot, chat_id, locations)

        if user_blocked:
            log.warning(f"User {user_id} blocked bot mid-download.")
            break

        if sent is None:
            unavailable += 1
            log.warning(f"File {file_pk} ('{file_name}') has no servable copy for bot pk={bot_pk}.")
            continue

        messages_to_delete.append(sent.message_id)
        progress.increment_sent(chat_id)

        # Update progress message every file (throttle to avoid flood)
        if prog_msg and index % 1 == 0:
            try:
                new_text = (
                    f"{tier_ico} <b>{tier} Download</b>\n\n"
                    f"👤 {display_name}\n"
                    f"📂 Folder: <code>{esc(folder_name)}</code> ({folder_type})\n"
                    f"⏱ Interval: <code>{file_interval}s</code>\n"
                    f"⏳ Next download in: <code>{next_dl_minutes} min</code>"
                    f"{upsell}\n\n"
                    f"{progress.build_progress_text(chat_id, file_interval)}"
                )
                await bot.edit_message_text(
                    new_text,
                    chat_id=chat_id,
                    message_id=prog_msg.message_id,
                    parse_mode=ParseMode.HTML,
                    reply_markup=cancel_kb.build(),
                )
            except (MessageNotModified, Exception):
                pass

        if file_interval > 0 and index < n - 1:
            await asyncio.sleep(file_interval)

    # ── Completion / cancellation ─────────────────────────────────────────
    sent_count    = progress.get_state(chat_id)["sent"] if progress.get_state(chat_id) else 0
    was_cancelled = progress.is_cancelled(chat_id)
    progress.finish_download(chat_id)

    if requires_admin_approval and not was_cancelled:
        db_execute(
            'UPDATE user_folder_approval SET download_completed = TRUE '
            'WHERE user_id = %s AND folder_id = %s',
            (user_id, folder_id)
        )

    # ── Update the progress card: remove Cancel button, show final status ──
    try:
        status_prefix = "\u274c Cancelled" if was_cancelled else "\u2705 Complete"
        if was_cancelled:
            progress_body = (
                "\u26a0\ufe0f Download cancelled by you.\n"
                + (f"\u23f3 The {sent_count} file(s) already sent will be "
                   f"<b>auto-deleted in {delete_time // 60} min</b>.\n"
                   "\U0001f4be Forward them to <b>Saved Messages</b> now!"
                   if messages_to_delete else
                   "No files were sent.")
            )
        else:
            progress_body = (
                f"\u23f3 Files will be <b>auto-deleted in {delete_time // 60} min</b>.\n"
                "\U0001f4be Forward them to <b>Saved Messages</b> now!"
            )
            if unavailable:
                progress_body += f"\n\n\u26a0\ufe0f {unavailable} file(s) couldn't be served right now."

        final_progress_text = (
            f"{status_prefix}: "
            f"<b>{sent_count}/{n} files</b> from <code>{esc(folder_name)}</code>\n\n"
            + progress_body
        )
        await bot.edit_message_text(
            final_progress_text,
            chat_id=chat_id,
            message_id=prog_msg.message_id,
            parse_mode=ParseMode.HTML,
            reply_markup=None,   # removes cancel button
        )
    except Exception:
        pass

    # If nothing was sent, nothing to delete — exit early
    if not messages_to_delete:
        return

    # ── Schedule deletion of every sent file (even on cancellation) ────────
    await asyncio.sleep(delete_time)

    deleted = 0
    for msg_id in messages_to_delete:
        try:
            await bot.delete_message(chat_id, msg_id)
            deleted += 1
        except TelegramBadRequest:
            continue
        except Exception as e:
            log.error(f"Error deleting message {msg_id}: {e}")

    # ── Send a NEW message to notify deletion (not an edit of the progress card)
    try:
        await bot.send_message(
            chat_id,
            "\U0001f5d1 <b>Auto-deleted!</b>\n\n"
            f"The {deleted} file(s) from <code>{esc(folder_name)}</code> "
            "have been removed from this chat.\n"
            "\U0001f4da Saved them? All the best with your studies! \U0001f31f",
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        pass


async def _check_and_start_download(bot, chat_id: int, user_id: int,
                                    folder_id: int, reply_fn,
                                    callback_query_id: str = None,
                                    ui_message_id: int = None):
    """Run all gate checks; if passed, start _run_download as a background task.

    If callback_query_id and ui_message_id are provided (i.e. the request came
    from the main UI inline button), gate failures are shown as in-place overlays
    (editing the main message) rather than new messages, so the user never leaves
    their current context.  The callback toast is only fired on success.
    """

    async def overlay(text: str, *, parse_mode=None, extra_buttons=None):
        """Show gate message: overlay on the UI message, or fall back to reply_fn."""
        if ui_message_id:
            kb = InlineBuilder()
            if extra_buttons:
                for btn in extra_buttons:
                    kb.row(btn)
            kb.add(InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_main"))
            try:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=ui_message_id,
                    text=text,
                    parse_mode=parse_mode,
                    reply_markup=kb.build(),
                )
                if callback_query_id:
                    await bot.answer_callback_query(callback_query_id)
            except Exception:
                await reply_fn(text, parse_mode=parse_mode)
        else:
            if extra_buttons:
                kb = InlineBuilder()
                for btn in extra_buttons:
                    kb.row(btn)
                await reply_fn(text, parse_mode=parse_mode, reply_markup=kb.build())
            else:
                await reply_fn(text, parse_mode=parse_mode)

    user_info = db_fetchone(
        'SELECT status, premium, last_download FROM users WHERE user_id = %s',
        (user_id,)
    )

    if not user_info or user_info[0] != 'approved':
        await overlay("You're not authorized. Please wait for admin approval.")
        return False

    _, is_premium, last_download = user_info

    if last_download:
        ld = last_download
        if hasattr(ld, 'tzinfo') and ld.tzinfo:
            ld = ld.replace(tzinfo=None)
        cooldown  = timedelta(minutes=2) if is_premium else timedelta(minutes=7)
        remaining = cooldown - (datetime.now() - ld)
        if remaining.total_seconds() > 0:
            total_secs = int(remaining.total_seconds())
            mins, secs = divmod(total_secs, 60)
            await overlay(
                f"\u23f3 <b>Cooldown active</b>\n\n"
                f"Please wait <b>{mins}m {secs}s</b> before your next download.\n\n"
                f"<i>Tap 🔙 Back to Menu to return to the folder list.</i>",
                parse_mode=ParseMode.HTML
            )
            return False

    invalidate_member_cache(user_id)
    if not await is_user_member(user_id):
        await overlay("Please join our required channels first, then try again.")
        return False

    folder_info = db_fetchone(
        'SELECT id, name, premium, admin_approval FROM folders WHERE id = %s',
        (folder_id,)
    )
    if not folder_info:
        await overlay("Folder not found. Use /start to see available folders.")
        return False

    _, folder_name, is_premium_folder, requires_admin_approval = folder_info

    if is_premium_folder and not is_premium:
        from config import PAYMENT_MODE as _PAYMENT_MODE
        if _PAYMENT_MODE == 'stars':
            from handlers.payment_stars import get_stars_plans
            plans = get_stars_plans()
            if plans:
                cheapest = min(plans, key=lambda p: p[2])  # (id, name, amount_stars, days)
                extra = [InlineKeyboardButton(
                    f"⭐ Get Premium \u2014 {cheapest[2]} Stars / {cheapest[3]} days",
                    callback_data=f"stars_plan:{cheapest[0]}"
                )]
            else:
                extra = [InlineKeyboardButton(
                    f"💮 Contact Admin",
                    url=f"https://t.me/{ADMIN_CONTACT.lstrip('@')}"
                )]
        elif _PAYMENT_MODE == 'razorpay':
            from handlers.payment import _get_plans, _fmt_inr
            plans = _get_plans()
            if plans:
                cheapest = min(plans, key=lambda p: p[2])  # (id, name, amount_paise, days)
                extra = [InlineKeyboardButton(
                    f"⭐ Get Premium \u2014 {_fmt_inr(cheapest[2])} / {cheapest[3]} days",
                    callback_data=f"pay_plan:{cheapest[0]}"
                )]
            else:
                extra = [InlineKeyboardButton(
                    f"💬 Contact Admin",
                    url=f"https://t.me/{ADMIN_CONTACT.lstrip('@')}"
                )]
        else:
            extra = [InlineKeyboardButton(
                f"💬 Contact Admin",
                url=f"https://t.me/{ADMIN_CONTACT.lstrip('@')}"
            )]

        await overlay(
            f"⭐ <b>Premium Folder: {esc(folder_name)}</b>\n\n"
            "This folder contains premium medical content.\n"
            "Upgrade to premium to access this and all other premium folders.\n\n"
            "<i>Tap 🔙 Back to Menu to return to the folder list.</i>",
            parse_mode=ParseMode.HTML,
            extra_buttons=extra
        )
        return False

    if requires_admin_approval:
        approval = db_fetchone(
            'SELECT approved, download_completed FROM user_folder_approval WHERE user_id = %s AND folder_id = %s',
            (user_id, folder_id)
        )
        if not approval or not approval[0]:
            from config import PAYMENT_MODE as _PM
            if _PM == 'stars':
                # -- Telegram Stars automated payment ---------------------------------
                from handlers.payment_stars import (
                    get_stars_folder_price, default_stars_folder_price, create_folder_invoice_link
                )
                price = get_stars_folder_price(folder_id)
                if price is None:
                    price = default_stars_folder_price()
                
                invoice_url = await create_folder_invoice_link(bot, folder_id)
                if invoice_url:
                    await overlay(
                        f"💰 <b>Paid Folder: {esc(folder_name)}</b>\n\n"
                        f"One-time purchase \u2192 <b>1 download</b> at Premium speed.\n"
                        f"Price: <b>{price} ⭐ Stars</b>\n\n"
                        f"✅ Access is <b>granted instantly</b> after Stars payment.\n\n"
                        f"<i>Tap 🔙 Back to Menu to return to the folder list.</i>",
                        parse_mode=ParseMode.HTML,
                        extra_buttons=[
                            InlineKeyboardButton(
                                f"⭐ Pay {price} Stars", url=invoice_url
                            )
                        ]
                    )
                else:
                    await overlay(
                        f"💰 <b>Paid Folder</b>\n\n"
                        f"Could not create a Stars payment link right now.\n"
                        f"Please contact {ADMIN_CONTACT}.\n\n"
                        f"<i>Tap 🔙 Back to Menu to return.</i>",
                        parse_mode=ParseMode.HTML,
                    )

            elif _PM == 'razorpay':
                # -- Razorpay automated payment ----------------------------------------
                from config import WEBHOOK_HOST
                from handlers.payment import (
                    _get_folder_price, _default_folder_price,
                    _rzp_client, _create_payment_link, _fmt_inr,
                )
                price = _get_folder_price(folder_id)
                if price is None:
                    price = _default_folder_price()
                try:
                    client    = _rzp_client()
                    link_data = _create_payment_link(
                        client, price,
                        f"Folder: {folder_name}",
                        user_id, "folder", ref_id=folder_id,
                        host_url=WEBHOOK_HOST,
                    )
                    payment_url     = link_data["short_url"]
                    payment_link_id = link_data["id"]
                    db_execute(
                        '''
                        INSERT INTO payment_orders
                            (razorpay_link_id, user_id, order_type, ref_id,
                             amount_paise, status, created_at)
                        VALUES (%s, %s, 'folder', %s, %s, 'created', NOW())
                        ON CONFLICT (razorpay_link_id) DO NOTHING
                        ''',
                        (payment_link_id, user_id, folder_id, price),
                    )
                    await overlay(
                        f"💰 <b>Paid Folder: {esc(folder_name)}</b>\n\n"
                        f"One-time purchase \u2192 <b>1 download</b> at Premium speed.\n"
                        f"Price: <b>{_fmt_inr(price)}</b>\n\n"
                        f"✅ Access is <b>granted instantly</b> after payment.\n\n"
                        f"<i>Tap 🔙 Back to Menu to return to the folder list.</i>",
                        parse_mode=ParseMode.HTML,
                        extra_buttons=[
                            InlineKeyboardButton(
                                f"💳 Pay {_fmt_inr(price)}", url=payment_url
                            )
                        ],
                    )
                except Exception as e:
                    logging.error(
                        f"Payment link failed for user {user_id} folder {folder_id}: {e}"
                    )
                    await overlay(
                        f"💰 <b>Paid Folder</b>\n\n"
                        f"Could not create a payment link right now.\n"
                        f"Please contact {ADMIN_CONTACT}.\n\n"
                        f"<i>Tap 🔙 Back to Menu to return.</i>",
                        parse_mode=ParseMode.HTML,
                    )

            else:
                # -- Manual mode: admin approval fallback ----------------------------
                await notify_admin_for_approval(user_id, folder_id, folder_name)
                await overlay(
                    f"📬 <b>Download Request Sent!</b>\n\n"
                    "An admin will review it and notify you here.\n"
                    "This usually takes a few hours.\n\n"
                    f"<i>Tap 🔙 Back to Menu to return to the folder list.</i>",
                    parse_mode=ParseMode.HTML,
                )
            return False
        if approval[1]:  # download_completed
            await notify_admin_for_approval_again(user_id, folder_id, folder_name)
            await overlay(
                "\u26a0\ufe0f <b>Already Downloaded</b>\n\n"
                f"You've already downloaded this folder once.\n"
                f"Contact {ADMIN_CONTACT} to request another download.\n\n"
                "<i>Tap 🔙 Back to Menu to return.</i>",
                parse_mode=ParseMode.HTML
            )
            return False

    # All checks passed — fire the toast and start the download
    if callback_query_id:
        try:
            await bot.answer_callback_query(callback_query_id, "\u2b07\ufe0f Starting download\u2026")
        except Exception:
            pass

    effective_premium = is_premium or (requires_admin_approval and not is_premium)

    asyncio.create_task(
        _run_download(bot, chat_id, user_id, folder_id, folder_name,
                      effective_premium, is_premium_folder, requires_admin_approval)
    )
    return True



async def get_all_files(message: types.Message):
    """/download <folder_name> command handler."""
    if not is_private_chat(message):
        return

    user_id     = message.from_user.id
    folder_name = (message.text.split(None, 1)[1].strip() if message.text and len(message.text.split(None, 1)) > 1 else '')

    if not folder_name:
        await message.reply(
            "Please specify a folder name.\n\n"
            "Usage: <code>/download &lt;folder name&gt;</code>\n\n"
            "Or use /start and tap a folder button.",
            parse_mode=ParseMode.HTML
        )
        return

    row = db_fetchone('SELECT id FROM folders WHERE name = %s', (folder_name,))
    if not row:
        await message.reply(
            f"❌ No folder named <b>{esc(folder_name)}</b> found.\n\n"
            "Use /start to see available folders.",
            parse_mode=ParseMode.HTML
        )
        return

    folder_id = row[0]

    bot = get_bot()

    async def reply_fn(text, **kwargs):
        await message.reply(text, **kwargs)

    await _check_and_start_download(bot, message.chat.id, user_id, folder_id, reply_fn)


async def trigger_folder_download(user_id: int, folder_id: int, chat_id: int,
                                  callback_query_id: str = None,
                                  ui_message_id: int = None):
    """Called from the folder-button callback in start.py.

    callback_query_id and ui_message_id are passed when triggered from the
    inline keyboard so gate messages can be shown as in-place overlays.
    """
    bot = get_bot()

    async def reply_fn(text, **kwargs):
        await bot.send_message(chat_id, text, **kwargs)

    await _check_and_start_download(
        bot, chat_id, user_id, folder_id, reply_fn,
        callback_query_id=callback_query_id,
        ui_message_id=ui_message_id,
    )


async def handle_folder_download_by_id(user_id: int, folder_id: int, chat_id: int):
    """Called from deep links (start=dl_<folder_id>) — from catalog or inline results.

    Shows a folder info card with a ⬇️ Download button so the user sees the
    folder details before committing.  The actual download starts only when
    they press the button (which routes through the normal dl: callback).
    """
    bot = get_bot()

    folder_row = db_fetchone(
        'SELECT id, name, premium, admin_approval FROM folders WHERE id = %s',
        (folder_id,)
    )
    if not folder_row:
        await bot.send_message(
            chat_id,
            "❌ <b>Folder not found.</b>\n\nIt may have been removed. Use /start to browse all folders.",
            parse_mode=ParseMode.HTML,
        )
        return

    fid, fname, is_premium, is_paid = folder_row
    file_count = db_fetchone(
        'SELECT COUNT(*) FROM files WHERE folder_id = %s', (fid,)
    )
    file_count = file_count[0] if file_count else 0

    # Category info
    cat_row = db_fetchone(
        '''SELECT c.emoji, c.name FROM categories c
           JOIN folders f ON f.category_id = c.id
           WHERE f.id = %s''',
        (fid,)
    )
    cat_text = f"  {cat_row[0]} {esc(cat_row[1])}\n" if cat_row else ""

    if is_premium:
        badge = "⭐ Premium"
    elif is_paid:
        badge = "💰 Paid"
    else:
        badge = "🆓 Free"

    kb = InlineBuilder()
    kb.row(InlineKeyboardButton(f"⬇️ Download {esc(fname)}", callback_data=f"dl:{fid}"))
    kb.row(InlineKeyboardButton("🔙 Back to Menu", callback_data="cat_main"))

    await bot.send_message(
        chat_id,
        f"📁 <b>{esc(fname)}</b>\n"
        f"{cat_text}"
        f"📄 {file_count} file{'s' if file_count != 1 else ''}\n"
        f"🏷 {badge}\n\n"
        f"Tap the button below to start your download.",
        parse_mode=ParseMode.HTML,
        reply_markup=kb.build(),
    )


async def handle_approval(message: types.Message):
    try:
        parts = message.text.split()
        if len(parts) != 3:
            raise ValueError
        _, user_id, folder_id = parts
        user_id, folder_id = int(user_id), int(folder_id)
    except (ValueError, IndexError):
        await message.reply(
            "Usage: <code>/approve &lt;user_id&gt; &lt;folder_id&gt;</code>",
            parse_mode=ParseMode.HTML
        )
        return

    try:
        db_execute(
            '''
            INSERT INTO user_folder_approval (user_id, folder_id, approved)
            VALUES (%s, %s, TRUE)
            ON CONFLICT (user_id, folder_id) DO UPDATE SET approved = TRUE, download_completed = FALSE
            ''',
            (user_id, folder_id)
        )
        await get_bot().send_message(
            user_id,
            "\u2705 <b>Download Approved!</b>\n\n"
            "Your request for the paid folder has been approved.\n"
            "You get <b>1 download</b> at Premium speed.\n\n"
            "Use /start to see the folder list and tap the folder to download.",
            parse_mode=ParseMode.HTML
        )
        await message.reply("\u2705 User approved for download.")
    except Exception as e:
        logging.error(f"Error in handle_approval: {e}")
        await message.reply("Failed to approve the user.")


async def handle_rejection(message: types.Message):
    try:
        parts = message.text.split()
        if len(parts) != 3:
            raise ValueError
        _, user_id, folder_id = parts
        user_id, folder_id = int(user_id), int(folder_id)
    except (ValueError, IndexError):
        await message.reply(
            "Usage: <code>/reject &lt;user_id&gt; &lt;folder_id&gt;</code>",
            parse_mode=ParseMode.HTML
        )
        return

    try:
        db_execute(
            'DELETE FROM user_folder_approval WHERE user_id = %s AND folder_id = %s',
            (user_id, folder_id)
        )
        await get_bot().send_message(
            user_id,
            f"\u274c Your download request was not approved.\n\n"
            f"Contact us if you think this is a mistake: {ADMIN_CONTACT}"
        )
        await message.reply("\u274c User's request rejected.")
    except Exception as e:
        logging.error(f"Error in handle_rejection: {e}")
        await message.reply("Failed to reject the user.")