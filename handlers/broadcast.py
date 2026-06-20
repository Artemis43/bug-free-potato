from aiogram.types import InlineKeyboardMarkup
from utils.keyboard import InlineBuilder, IKB as InlineKeyboardButton
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest, TelegramRetryAfter
from aiogram import Router
from utils.bot_ref import get_bot
import asyncio
import logging
from aiogram import types
from aiogram import Router
from aiogram.enums import ParseMode
from aiogram import Router
from middlewares.authorization import is_private_chat
from config import ADMIN_IDS
from utils.database import db_fetchall, db_execute, db_fetchone

router = Router()

# Telegram's flood-limit ceiling is ~30 msg/s for bots.
# 50ms between messages = 20 msg/s — safe headroom.
_BROADCAST_DELAY = 0.05  # seconds


async def broadcast_message(message: types.Message):
    """/broadcast [html|md] <text>

    Step 1 of 2: parse the message, store it in the DB as a pending broadcast,
    then send the admin a preview with [✅ Send Now] / [❌ Cancel] buttons.
    The actual send happens in execute_broadcast() called from the callback handler.
    """
    if not is_private_chat(message):
        return

    if str(message.from_user.id) not in ADMIN_IDS:
        await message.reply("You are not authorized to send broadcasts.")
        return

    args = (message.text.split(None, 1)[1] if message.text and len(message.text.split(None, 1)) > 1 else '')
    if not args:
        await message.reply(
            "Usage: <code>/broadcast &lt;message&gt;</code>\n\n"
            "<b>Optional format prefix:</b>\n"
            "  <code>/broadcast html &lt;b&gt;Bold&lt;/b&gt;</code> — HTML\n"
            "  <code>/broadcast md *Bold*</code> — Markdown\n"
            "  <code>/broadcast Plain text</code> — no formatting",
            parse_mode=ParseMode.HTML
        )
        return

    # ── Parse optional format prefix ─────────────────────────────────────────
    parse_mode = None
    parts = args.split(' ', 1)
    if parts[0].lower() == 'html' and len(parts) > 1:
        parse_mode = 'HTML'
        text = parts[1]
    elif parts[0].lower() in ('md', 'markdown') and len(parts) > 1:
        parse_mode = 'MARKDOWN'
        text = parts[1]
    else:
        text = args

    # ── Count recipients ──────────────────────────────────────────────────────
    users = db_fetchall("SELECT user_id FROM users WHERE status = 'approved'")
    count = len(users) if users else 0

    # ── Persist in DB (so confirmation survives a restart) ────────────────────
    # Delete any previous pending broadcast from this admin first (only 1 at a time)
    db_execute('DELETE FROM pending_broadcasts WHERE admin_id = %s', (message.from_user.id,))
    db_execute(
        'INSERT INTO pending_broadcasts (admin_id, message_text, parse_mode) VALUES (%s, %s, %s)',
        (message.from_user.id, text, parse_mode)
    )
    row = db_fetchone(
        'SELECT id FROM pending_broadcasts WHERE admin_id = %s ORDER BY id DESC LIMIT 1',
        (message.from_user.id,)
    )
    broadcast_id = row[0] if row else None

    # ── Send preview to admin ─────────────────────────────────────────────────
    kb = InlineBuilder()
    kb.row(
        InlineKeyboardButton(f"✅ Send to {count} users", callback_data=f"bcast_send:{broadcast_id}"),
        InlineKeyboardButton("❌ Cancel",                callback_data=f"bcast_cancel:{broadcast_id}"),
    )

    pm_label = {'HTML': 'HTML', 'MARKDOWN': 'Markdown', None: 'Plain text'}.get(parse_mode, 'Plain')

    await message.reply(
        f"<b>📤 Broadcast Preview</b>  •  {pm_label}\n"
        f"<i>This is exactly how it will appear to users:</i>\n",
        parse_mode=ParseMode.HTML
    )

    # Send the actual preview in the user's chosen format so it renders correctly
    await message.answer(text, parse_mode=parse_mode)

    await message.answer(
        f"👥 Recipients: <b>{count}</b> approved user(s)\n\n"
        "Tap <b>Send</b> to confirm, or <b>Cancel</b> to discard.",
        parse_mode=ParseMode.HTML,
        reply_markup=kb.build()
    )


async def execute_broadcast(callback_query: types.CallbackQuery, broadcast_id: int):
    """Step 2 of 2: called from process_callback when admin taps [✅ Send Now].
    Fetches the pending broadcast from DB and sends it to all approved users.
    """
    bot = get_bot()
    # Load from DB
    row = db_fetchone(
        'SELECT admin_id, message_text, parse_mode FROM pending_broadcasts WHERE id = %s',
        (broadcast_id,)
    )
    if not row:
        await bot.answer_callback_query(
            callback_query.id,
            "Broadcast expired or already sent.",
            show_alert=True
        )
        return

    admin_id, text, parse_mode = row

    # Security: only the original admin can confirm
    if callback_query.from_user.id != admin_id:
        await bot.answer_callback_query(
            callback_query.id, "Not authorized.", show_alert=True
        )
        return

    await bot.answer_callback_query(callback_query.id, "📢 Sending…")

    # Remove buttons from preview message
    try:
        await bot.edit_message_reply_markup(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id,
            reply_markup=None
        )
        await bot.edit_message_text(
            "📢 <b>Broadcast in progress…</b>",
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id,
            parse_mode=ParseMode.HTML
        )
    except Exception:
        pass

    # Delete from DB immediately so duplicate sends are impossible
    db_execute('DELETE FROM pending_broadcasts WHERE id = %s', (broadcast_id,))

    users = db_fetchall("SELECT user_id FROM users WHERE status = 'approved'")
    if not users:
        await bot.send_message(admin_id, "No approved users to broadcast to.")
        return

    success = failed = blocked = 0

    for (user_id,) in users:
        try:
            await bot.send_message(user_id, text, parse_mode=parse_mode)
            success += 1
        except TelegramForbiddenError:
            blocked += 1
        except TelegramBadRequest as e:
            if "chat not found" in str(e).lower():
                blocked += 1
            else:
                logging.error(f"Broadcast failed for user {user_id}: {e}")
                failed += 1
        except TelegramRetryAfter as e:
            logging.warning(f"Broadcast flood limit — waiting {e.retry_after}s")
            await asyncio.sleep(e.retry_after)
            try:
                await bot.send_message(user_id, text, parse_mode=parse_mode)
                success += 1
            except Exception:
                failed += 1
        except Exception as e:
            logging.error(f"Broadcast failed for user {user_id}: {e}")
            failed += 1

        await asyncio.sleep(_BROADCAST_DELAY)

    await bot.send_message(
        admin_id,
        f"📢 <b>Broadcast Complete</b>\n\n"
        f"✅ Sent:    <b>{success}</b>\n"
        f"🚫 Blocked: <b>{blocked}</b>\n"
        f"❌ Failed:  <b>{failed}</b>\n"
        f"📊 Total:   <b>{len(users)}</b>",
        parse_mode=ParseMode.HTML
    )


async def cancel_broadcast(callback_query: types.CallbackQuery, broadcast_id: int):
    """Called from process_callback when admin taps [❌ Cancel]."""
    bot = get_bot()
    row = db_fetchone('SELECT admin_id FROM pending_broadcasts WHERE id = %s', (broadcast_id,))
    if row and row[0] != callback_query.from_user.id:
        await bot.answer_callback_query(callback_query.id, "Not authorized.", show_alert=True)
        return

    db_execute('DELETE FROM pending_broadcasts WHERE id = %s', (broadcast_id,))
    await bot.answer_callback_query(callback_query.id, "Broadcast cancelled.")

    try:
        await bot.edit_message_text(
            "❌ <b>Broadcast cancelled.</b>",
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id,
            parse_mode=ParseMode.HTML
        )
    except Exception:
        pass