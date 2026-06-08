from datetime import datetime, timedelta
import asyncio
import logging
from aiogram import types, exceptions
from aiogram.types import ParseMode
from aiogram.utils.exceptions import MessageNotModified
from config import REQUIRED_CHANNELS, PREMIUM_INFO_URL, ADMIN_CONTACT
from utils.helpers import notify_admin_for_approval, notify_admin_for_approval_again, esc
from middlewares.authorization import is_private_chat, is_user_member
from utils.database import db_fetchone, db_fetchall, db_execute


async def _run_download(bot, chat_id: int, user_id: int,
                        folder_id: int, folder_name: str,
                        is_premium: bool, is_premium_folder: bool,
                        requires_admin_approval: bool):
    """Background coroutine: send all files in a folder then schedule deletion."""

    # ── Progress bar ──────────────────────────────────────────────────────────
    try:
        progress_message = await bot.send_message(
            chat_id, "⚡ Connecting to servers…\n[░░░░░░░░░░░░░░░░░░░░░]"
        )
    except Exception as e:
        logging.error(f"Could not send progress message to {chat_id}: {e}")
        return

    BAR_LENGTH = 21
    for i in range(1, BAR_LENGTH + 1):
        bar = "█" * i + "░" * (BAR_LENGTH - i)
        try:
            await progress_message.edit_text(f"⚡ Connecting to servers…\n[{bar}]")
        except Exception:
            pass
        await asyncio.sleep(7 / BAR_LENGTH)

    await progress_message.edit_text("🚀 Download starting…")
    await asyncio.sleep(2)

    # ── Info card ─────────────────────────────────────────────────────────────
    file_interval   = 5  if is_premium else 60
    time_interval   = timedelta(minutes=2) if is_premium else timedelta(minutes=7)
    next_dl_minutes = int(time_interval.total_seconds() / 60)

    if requires_admin_approval: folder_type = "Paid"
    elif is_premium_folder:     folder_type = "Premium"
    else:                       folder_type = "Free"

    name_row     = db_fetchone('SELECT first_name FROM users WHERE user_id = %s', (user_id,))
    display_name = esc(name_row[0] if name_row and name_row[0] else f"User {user_id}")

    tier     = "Premium" if is_premium else "Free"
    tier_ico = "🎉" if is_premium else "🔓"
    upsell   = (
        f'\n\n💡 <a href="{PREMIUM_INFO_URL}">Upgrade to Premium</a> for 5s intervals'
        if not is_premium else ""
    )

    info_text = (
        f"{tier_ico} <b>{tier} Download</b>\n\n"
        f"👤 {display_name}\n"
        f"📁 Folder: <code>{esc(folder_name)}</code> ({folder_type})\n"
        f"⏱ Interval: <code>{file_interval}s</code> between files\n"
        f"⏳ Next download available in: <code>{next_dl_minutes} min(s)</code>"
        f"{upsell}"
    )
    await progress_message.edit_text(info_text, parse_mode=ParseMode.HTML)

    # ── Update download counter ───────────────────────────────────────────────
    db_execute(
        'UPDATE folders SET download_count = download_count + 1 WHERE id = %s',
        (folder_id,)
    )

    # ── Fetch files ───────────────────────────────────────────────────────────
    files = db_fetchall(
        'SELECT file_id, file_name, caption, file_type FROM files WHERE folder_id = %s',
        (folder_id,)
    )
    if not files:
        await bot.send_message(chat_id, "⚠️ No files found in this folder yet.")
        return

    n = len(files)
    if   n <= 25:  delete_time = 120
    elif n <= 50:  delete_time = 180
    elif n <= 75:  delete_time = 240
    else:          delete_time = 300

    # Record cooldown before sending (prevents re-download on crash)
    db_execute('UPDATE users SET last_download = %s WHERE user_id = %s', (datetime.now(), user_id))

    messages_to_delete = []
    for index, (file_id, file_name, caption, file_type) in enumerate(files):
        try:
            if file_type == 'document':
                sent = await bot.send_document(chat_id, file_id, caption=caption)
            elif file_type == 'video':
                sent = await bot.send_video(chat_id, file_id, caption=caption)
            elif file_type == 'photo':
                sent = await bot.send_photo(chat_id, file_id, caption=caption)
            else:
                logging.warning(f"Unknown file_type '{file_type}' for file_id {file_id}, skipping.")
                continue
            messages_to_delete.append(sent.message_id)
        except Exception as e:
            logging.error(f"Error sending file {file_id}: {e}")
            continue

        if file_interval > 0 and index < len(files) - 1:
            await asyncio.sleep(file_interval)

    if requires_admin_approval:
        db_execute(
            'UPDATE user_folder_approval SET download_completed = TRUE WHERE user_id = %s AND folder_id = %s',
            (user_id, folder_id)
        )

    # ── Warning with exact deletion time ─────────────────────────────────────
    deletion_at  = datetime.now() + timedelta(seconds=delete_time)
    deletion_str = deletion_at.strftime('%I:%M %p')
    sent_count   = len(messages_to_delete)

    warning_message = await bot.send_message(
        chat_id,
        f"✅ <b>{sent_count}/{n} files sent!</b>\n\n"
        f"⚠️ Files will be <b>auto-deleted at {deletion_str}</b> "
        f"({delete_time // 60} min from now).\n"
        f"📌 Forward them to <b>Saved Messages</b> now!",
        parse_mode=ParseMode.HTML
    )

    await asyncio.sleep(delete_time)

    for msg_id in messages_to_delete:
        try:
            await bot.delete_message(chat_id, msg_id)
        except exceptions.MessageToDeleteNotFound:
            continue
        except Exception as e:
            logging.error(f"Error deleting message {msg_id}: {e}")

    try:
        await bot.edit_message_text(
            "🗑 Downloaded files have been deleted.\nAll the Best! 🙌",
            chat_id=chat_id,
            message_id=warning_message.message_id
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
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ParseMode as PM

    async def overlay(text: str, *, parse_mode=None, extra_buttons=None):
        """Show gate message: overlay on the UI message, or fall back to reply_fn."""
        if ui_message_id:
            kb = InlineKeyboardMarkup()
            if extra_buttons:
                for btn in extra_buttons:
                    kb.row(btn)
            kb.add(InlineKeyboardButton("\u25c0 Back", callback_data="back_to_main"))
            try:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=ui_message_id,
                    text=text,
                    parse_mode=parse_mode,
                    reply_markup=kb,
                )
                if callback_query_id:
                    await bot.answer_callback_query(callback_query_id)
            except Exception:
                await reply_fn(text, parse_mode=parse_mode)
        else:
            if extra_buttons:
                kb = InlineKeyboardMarkup()
                for btn in extra_buttons:
                    kb.row(btn)
                await reply_fn(text, parse_mode=parse_mode, reply_markup=kb)
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
                f"<i>Tap \u25c0 Back to return to the folder list.</i>",
                parse_mode=PM.HTML
            )
            return False

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
        from handlers.payment import _get_plans, _fmt_inr
        plans = _get_plans()
        if plans:
            cheapest = min(plans, key=lambda p: p[2])  # (id, name, amount_paise, days)
            extra = [InlineKeyboardButton(
                f"\u2b50 Get Premium \u2014 {_fmt_inr(cheapest[2])} / {cheapest[3]} days",
                callback_data=f"pay_plan:{cheapest[0]}"
            )]
        else:
            extra = [InlineKeyboardButton(
                "\ud83d\udcac Contact Admin",
                url=f"https://t.me/{ADMIN_CONTACT.lstrip('@')}"
            )]

        await overlay(
            "\u2b50 <b>Premium Folder</b>\n"
            + "\u2501" * 22 + "\n\n"
            "This folder is for <b>Premium members only</b>.\n\n"
            "<b>What Premium gives you:</b>\n"
            "  \u2022 \u26a1 5s interval between files <i>(vs 60s free)</i>\n"
            "  \u2022 \u23f1 2 min cooldown <i>(vs 7 min free)</i>\n"
            "  \u2022 \u2b50 Access to all Premium-only folders\n\n"
            "<i>Tap the button below to subscribe, or \u25c0 Back to return.</i>",
            parse_mode=PM.HTML,
            extra_buttons=extra,
        )
        return False

    if requires_admin_approval:
        approval = db_fetchone(
            'SELECT approved, download_completed FROM user_folder_approval WHERE user_id = %s AND folder_id = %s',
            (user_id, folder_id)
        )
        if not approval or not approval[0]:
            from config import RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET, WEBHOOK_HOST
            if RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET:
                # \u2500\u2500 Automated payment flow \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
                from handlers.payment import (
                    _get_folder_price, _default_folder_price,
                    _rzp_client, _create_payment_link, _fmt_inr,
                )
                price = _get_folder_price(folder_id)
                if price is None:
                    price = _default_folder_price()
                try:
                    client   = _rzp_client()
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
                        f"\U0001f4b0 <b>Paid Folder: {esc(folder_name)}</b>\n"
                        + "\u2501" * 22 + "\n\n"
                        f"One-time purchase \u2192 <b>1 download</b> at Premium speed.\n"
                        f"Price: <b>{_fmt_inr(price)}</b>\n\n"
                        "\u2705 Access is <b>granted instantly</b> after payment.\n\n"
                        "<i>Tap \u25c0 Back to return to the folder list.</i>",
                        parse_mode=PM.HTML,
                        extra_buttons=[
                            InlineKeyboardButton(
                                f"\U0001f4b3 Pay {_fmt_inr(price)}", url=payment_url
                            )
                        ],
                    )
                except Exception as e:
                    logging.error(
                        f"Payment link failed for user {user_id} folder {folder_id}: {e}"
                    )
                    await overlay(
                        f"\U0001f4b0 <b>Paid Folder</b>\n\n"
                        f"Could not create a payment link right now.\n"
                        f"Please contact {ADMIN_CONTACT}.\n\n"
                        "<i>Tap \u25c0 Back to return.</i>",
                        parse_mode=PM.HTML,
                    )
            else:
                # \u2500\u2500 No Razorpay \u2014 manual admin approval fallback \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
                await notify_admin_for_approval(user_id, folder_id, folder_name)
                await overlay(
                    "\U0001f4ec <b>Download Request Sent!</b>\n\n"
                    "An admin will review it and notify you here.\n"
                    "This usually takes a few hours.\n\n"
                    "<i>Tap \u25c0 Back to return to the folder list.</i>",
                    parse_mode=PM.HTML,
                )
            return False

        if approval[1]:  # download_completed
            await notify_admin_for_approval_again(user_id, folder_id, folder_name)
            await overlay(
                "\u26a0\ufe0f <b>Already Downloaded</b>\n\n"
                f"You've already downloaded this folder once.\n"
                f"Contact {ADMIN_CONTACT} to request another download.\n\n"
                "<i>Tap \u25c0 Back to return.</i>",
                parse_mode=PM.HTML
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
    from main import bot
    if not is_private_chat(message):
        return

    user_id     = message.from_user.id
    folder_name = message.get_args().strip()

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
    from main import bot

    async def reply_fn(text, **kwargs):
        await bot.send_message(chat_id, text, **kwargs)

    await _check_and_start_download(
        bot, chat_id, user_id, folder_id, reply_fn,
        callback_query_id=callback_query_id,
        ui_message_id=ui_message_id,
    )


async def handle_approval(message: types.Message):
    from main import bot
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
        await bot.send_message(
            user_id,
            "✅ <b>Download Approved!</b>\n\n"
            "Your request for the paid folder has been approved.\n"
            "You get <b>1 download</b> at Premium speed.\n\n"
            "Use /start to see the folder list and tap the folder to download.",
            parse_mode=ParseMode.HTML
        )
        await message.reply("✅ User approved for download.")
    except Exception as e:
        logging.error(f"Error in handle_approval: {e}")
        await message.reply("Failed to approve the user.")


async def handle_rejection(message: types.Message):
    from main import bot
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
        await bot.send_message(
            user_id,
            f"❌ Your download request was not approved.\n\n"
            f"Contact us if you think this is a mistake: {ADMIN_CONTACT}"
        )
        await message.reply("❌ User's request rejected.")
    except Exception as e:
        logging.error(f"Error in handle_rejection: {e}")
        await message.reply("Failed to reject the user.")