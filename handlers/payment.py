"""
handlers/payment.py
────────────────────────────────────────────────────────────────────────────
Payment routing for the Medical Content Bot.

Active payment system is controlled by PAYMENT_MODE env var:
  manual   — no automated payments; paid folders need admin approval
  razorpay — Razorpay gateway (existing flow)
  stars    — Telegram Stars (see handlers/payment_stars.py)

cmd_pay, cmd_payfolder, and cmd_payconfig all route based on PAYMENT_MODE.
Razorpay-specific logic stays in this file; Stars logic is in payment_stars.py.
"""

import hashlib
import hmac
import json
import logging
from datetime import datetime, timedelta

import razorpay
from aiogram import exceptions, types
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ParseMode,
)

from config import ADMIN_IDS, ADMIN_CONTACT, RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET, ADMIN_GROUP_ID, PAYMENT_MODE
from middlewares.authorization import is_private_chat
from utils.database import db_execute, db_fetchall, db_fetchone
from utils.helpers import esc

log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Razorpay client (lazy — only instantiated if keys are configured)
# ─────────────────────────────────────────────────────────────────────────────

def _rzp_client():
    if not RAZORPAY_KEY_ID or not RAZORPAY_KEY_SECRET:
        raise RuntimeError("RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET not configured.")
    return razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _fmt_inr(paise: int) -> str:
    """Convert paise → '₹X' string."""
    rupees = paise / 100
    return f"₹{rupees:,.0f}" if rupees == int(rupees) else f"₹{rupees:,.2f}"


def _get_plans() -> list[tuple]:
    """Return all active premium plans sorted by amount. [(id, name, amount_paise, days), ...]"""
    return db_fetchall(
        "SELECT id, name, amount_paise, days FROM payment_plans WHERE active = TRUE ORDER BY amount_paise"
    ) or []


def _get_folder_price(folder_id: int) -> int | None:
    """Return the configured price (paise) for a paid folder, or None."""
    row = db_fetchone(
        "SELECT amount_paise FROM payment_folder_prices WHERE folder_id = %s",
        (folder_id,)
    )
    return row[0] if row else None


def _default_folder_price() -> int:
    """Return the global default paid-folder price in paise."""
    row = db_fetchone(
        "SELECT value_int FROM payment_config WHERE key = 'default_folder_price_paise'"
    )
    return row[0] if row else 9900  # ₹99 default


def _create_payment_link(client, amount_paise: int, description: str,
                          user_id: int, order_type: str, ref_id: int | None = None,
                          host_url: str = "") -> dict:
    """Create a Razorpay Payment Link and return the API response dict."""
    notes = {
        "user_id": str(user_id),
        "order_type": order_type,   # 'premium' | 'folder'
    }
    if ref_id is not None:
        notes["ref_id"] = str(ref_id)   # plan_id or folder_id

    payload = {
        "amount": amount_paise,
        "currency": "INR",
        "description": description,
        "notes": notes,
        "notify": {"sms": False, "email": False},
        "reminder_enable": False,
    }
    # callback_url is optional but gives a nicer post-pay redirect
    if host_url:
        payload["callback_url"] = f"{host_url}/payment/success"
        payload["callback_method"] = "get"

    return client.payment_link.create(payload)


# ─────────────────────────────────────────────────────────────────────────────
# /pay — user purchases a premium plan
# ─────────────────────────────────────────────────────────────────────────────

async def cmd_pay(message: types.Message):
    """/pay [plan_name]  — buy a premium subscription."""
    if not is_private_chat(message):
        return

    # ── Route by payment mode ─────────────────────────────────────────────────
    if PAYMENT_MODE == 'stars':
        from handlers.payment_stars import cmd_pay_stars
        await cmd_pay_stars(message)
        return

    if PAYMENT_MODE == 'manual':
        await message.reply(
            "💳 Online payments are not configured.\n"
            f"Contact {ADMIN_CONTACT} to purchase premium manually."
        )
        return

    # ── Razorpay mode ─────────────────────────────────────────────────────────
    user_id = message.from_user.id

    if not RAZORPAY_KEY_ID or not RAZORPAY_KEY_SECRET:
        await message.reply(
            "💳 Online payments are not yet configured.\n"
            f"Please contact {ADMIN_CONTACT} to purchase premium manually."
        )
        return

    plans = _get_plans()
    if not plans:
        await message.reply(
            "💳 No payment plans have been set up yet.\n"
            f"Contact {ADMIN_CONTACT} for pricing."
        )
        return

    arg = message.get_args().strip().lower()

    # If a specific plan is requested by name
    matched = None
    if arg:
        for plan in plans:
            if plan[1].lower() == arg:
                matched = plan
                break
        if not matched:
            await message.reply(
                f"❌ Unknown plan <code>{esc(arg)}</code>.\n\n"
                "Use /pay to see all available plans.",
                parse_mode=ParseMode.HTML
            )
            return

    # Show plan picker if no plan specified
    if not matched:
        kb = InlineKeyboardMarkup(row_width=1)
        for plan_id, name, amount_paise, days in plans:
            kb.add(InlineKeyboardButton(
                f"💳 {name} — {_fmt_inr(amount_paise)} ({days} days)",
                callback_data=f"pay_plan:{plan_id}"
            ))
        kb.add(InlineKeyboardButton("✕ Cancel", callback_data="pay_cancel"))
        await message.reply(
            "⭐ <b>Choose a Premium Plan</b>\n\n"
            "Select the plan you want to purchase:",
            parse_mode=ParseMode.HTML,
            reply_markup=kb
        )
        return

    await _create_premium_link(message.reply, user_id, matched)


async def _create_premium_link(reply_fn, user_id: int, plan: tuple):
    """Generate a Razorpay link for a premium plan and DM it to the user."""
    from config import WEBHOOK_HOST
    plan_id, name, amount_paise, days = plan

    try:
        client = _rzp_client()
        link_data = _create_payment_link(
            client, amount_paise,
            f"Premium — {name} ({days} days)",
            user_id, "premium", ref_id=plan_id,
            host_url=WEBHOOK_HOST,
        )
        payment_url = link_data["short_url"]
        payment_link_id = link_data["id"]
    except Exception as e:
        log.error(f"Razorpay link creation failed for user {user_id}: {e}")
        await reply_fn(
            "❌ Could not create a payment link. Please try again later.",
            parse_mode=ParseMode.HTML
        )
        return

    # Persist order
    db_execute(
        '''
        INSERT INTO payment_orders
            (razorpay_link_id, user_id, order_type, ref_id, amount_paise, status, created_at)
        VALUES (%s, %s, 'premium', %s, %s, 'created', NOW())
        ''',
        (payment_link_id, user_id, plan_id, amount_paise)
    )

    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("💳 Pay Now", url=payment_url))

    await reply_fn(
        f"⭐ <b>Premium — {esc(name)}</b>\n\n"
        f"Duration: <b>{days} days</b>\n"
        f"Amount: <b>{_fmt_inr(amount_paise)}</b>\n\n"
        "Tap <b>Pay Now</b> to complete your payment via UPI / Card / Net Banking.\n\n"
        "✅ Your premium will be <b>activated automatically</b> after payment.\n"
        "<i>Link expires in 15 minutes.</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=kb,
    )


# ─────────────────────────────────────────────────────────────────────────────
# /payfolder — user purchases access to a paid folder
# ─────────────────────────────────────────────────────────────────────────────

async def cmd_payfolder(message: types.Message):
    """/payfolder <folder_id>  — pay for a paid-folder download."""
    if not is_private_chat(message):
        return

    # ── Route by payment mode ─────────────────────────────────────────────────
    if PAYMENT_MODE == 'stars':
        from handlers.payment_stars import cmd_payfolder_stars
        await cmd_payfolder_stars(message)
        return

    if PAYMENT_MODE == 'manual':
        await message.reply(
            "💳 Online payments are not configured.\n"
            f"Contact {ADMIN_CONTACT} to arrange access."
        )
        return

    # ── Razorpay mode ─────────────────────────────────────────────────────────
    if not RAZORPAY_KEY_ID or not RAZORPAY_KEY_SECRET:
        await message.reply(
            "💳 Online payments are not yet configured.\n"
            f"Contact {ADMIN_CONTACT} to arrange payment."
        )
        return

    args = message.get_args().strip()
    if not args:
        await message.reply(
            "Usage: <code>/payfolder &lt;folder_id&gt;</code>\n\n"
            "Use /start to see folder IDs.",
            parse_mode=ParseMode.HTML
        )
        return

    try:
        folder_id = int(args)
    except ValueError:
        await message.reply("Invalid folder ID.")
        return

    await _create_folder_link(message.reply, message.from_user.id, folder_id)


async def _create_folder_link(reply_fn, user_id: int, folder_id: int):
    """Generate a Razorpay link for a paid-folder purchase."""
    from config import WEBHOOK_HOST

    folder_row = db_fetchone(
        "SELECT name, admin_approval FROM folders WHERE id = %s",
        (folder_id,)
    )
    if not folder_row:
        await reply_fn("❌ Folder not found.")
        return

    folder_name, requires_admin_approval = folder_row
    if not requires_admin_approval:
        await reply_fn("This folder does not require payment.")
        return

    price = _get_folder_price(folder_id)
    if price is None:
        price = _default_folder_price()

    try:
        client = _rzp_client()
        link_data = _create_payment_link(
            client, price,
            f"Paid Folder: {folder_name}",
            user_id, "folder", ref_id=folder_id,
            host_url=WEBHOOK_HOST,
        )
        payment_url = link_data["short_url"]
        payment_link_id = link_data["id"]
    except Exception as e:
        log.error(f"Razorpay folder link creation failed for user {user_id}: {e}")
        await reply_fn("❌ Could not create payment link. Try again later.")
        return

    db_execute(
        '''
        INSERT INTO payment_orders
            (razorpay_link_id, user_id, order_type, ref_id, amount_paise, status, created_at)
        VALUES (%s, %s, 'folder', %s, %s, 'created', NOW())
        ON CONFLICT (razorpay_link_id) DO NOTHING
        ''',
        (payment_link_id, user_id, folder_id, price)
    )

    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("💳 Pay Now", url=payment_url))

    await reply_fn(
        f"💰 <b>Paid Folder: {esc(folder_name)}</b>\n\n"
        f"This is a one-time payment for <b>1 download</b> of this folder.\n"
        f"Amount: <b>{_fmt_inr(price)}</b>\n\n"
        "After payment, an admin will approve your download within a few hours.\n\n"
        "Tap <b>Pay Now</b> to complete via UPI / Card / Net Banking.\n"
        "<i>Link expires in 15 minutes.</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=kb,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Callback: plan picker inline buttons
# ─────────────────────────────────────────────────────────────────────────────

async def handle_pay_callback(callback_query: types.CallbackQuery):
    """Dispatched from start.process_callback for pay_plan: and pay_cancel data."""
    from main import bot
    data = callback_query.data or ""
    user_id = callback_query.from_user.id

    if data == "pay_cancel":
        await bot.answer_callback_query(callback_query.id)
        try:
            await bot.delete_message(
                callback_query.message.chat.id,
                callback_query.message.message_id
            )
        except Exception:
            pass
        return

    if data.startswith("pay_plan:"):
        try:
            plan_id = int(data.split(":", 1)[1])
        except (ValueError, IndexError):
            await bot.answer_callback_query(callback_query.id, "Invalid plan.")
            return

        plan = db_fetchone(
            "SELECT id, name, amount_paise, days FROM payment_plans WHERE id = %s AND active = TRUE",
            (plan_id,)
        )
        if not plan:
            await bot.answer_callback_query(callback_query.id, "Plan no longer available.", show_alert=True)
            return

        await bot.answer_callback_query(callback_query.id, "Creating payment link…")
        # Edit the picker message to a loading state
        try:
            await bot.edit_message_text(
                "⏳ Creating your payment link…",
                chat_id=callback_query.message.chat.id,
                message_id=callback_query.message.message_id,
            )
        except Exception:
            pass

        await _create_premium_link(
            lambda text, **kw: bot.send_message(user_id, text, **kw),
            user_id, plan
        )
        # Remove the loading message
        try:
            await bot.delete_message(
                callback_query.message.chat.id,
                callback_query.message.message_id,
            )
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# Webhook handler — called from keep_alive Flask route
# ─────────────────────────────────────────────────────────────────────────────

def verify_razorpay_signature(payload_bytes: bytes, signature: str, secret: str) -> bool:
    """Return True if the webhook signature is valid."""
    expected = hmac.new(
        secret.encode(),
        payload_bytes,
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


async def _activate_premium(user_id: int, plan_id: int, razorpay_payment_id: str):
    """Grant premium to user based on plan. Called after verified payment."""
    from main import bot
    from handlers.setpremium import remove_premium_after_expiry

    plan = db_fetchone(
        "SELECT name, amount_paise, days FROM payment_plans WHERE id = %s",
        (plan_id,)
    )
    if not plan:
        log.error(f"[Payment] Plan {plan_id} not found for user {user_id}")
        return

    name, amount_paise, days = plan
    expiration_date = datetime.now() + timedelta(days=days)

    db_execute(
        "UPDATE users SET premium = TRUE, premium_expiration = %s WHERE user_id = %s",
        (expiration_date, user_id)
    )
    db_execute(
        "UPDATE payment_orders SET status = 'paid', paid_at = NOW(), razorpay_payment_id = %s "
        "WHERE razorpay_payment_id = %s OR (user_id = %s AND order_type = 'premium' AND status = 'created')",
        (razorpay_payment_id, razorpay_payment_id, user_id)
    )

    import asyncio
    asyncio.create_task(remove_premium_after_expiry(user_id, expiration_date))

    try:
        await bot.send_message(
            user_id,
            f"🎉 <b>Payment Received! You're now Premium.</b>\n\n"
            f"Plan: <b>{esc(name)}</b>\n"
            f"Duration: <b>{days} days</b>\n"
            f"Expires: <b>{expiration_date.strftime('%d %b %Y')}</b>\n\n"
            f"✨ You now get:\n"
            f"  • ⚡ 5s interval between files\n"
            f"  • ⏱ 2 min cooldown between downloads\n"
            f"  • ⭐ Access to all Premium-only folders\n\n"
            f"Use /start to explore!",
            parse_mode=ParseMode.HTML
        )
    except exceptions.BotBlocked:
        log.warning(f"[Payment] Could not notify user {user_id} — bot blocked.")
    except Exception as e:
        log.error(f"[Payment] Error notifying user {user_id}: {e}")

    # Notify admin group (informational only)
    user_row = db_fetchone("SELECT first_name, username FROM users WHERE user_id = %s", (user_id,))
    first_name = esc(user_row[0] if user_row and user_row[0] else f"User {user_id}")
    username   = f"@{esc(user_row[1])}" if user_row and user_row[1] else "no username"
    admin_target = ADMIN_GROUP_ID if ADMIN_GROUP_ID else (ADMIN_IDS[0] if ADMIN_IDS else None)
    if admin_target:
        try:
            await bot.send_message(
                admin_target,
                f"💎 <b>Premium Purchased</b> <i>(via Flask webhook)</i>\n\n"
                f"Name: {first_name}\n"
                f"Username: {username}\n"
                f"ID: <code>{user_id}</code>\n\n"
                f"Plan: <b>{esc(name)}</b>\n"
                f"Duration: <b>{days} days</b>\n"
                f"Expires: <b>{expiration_date.strftime('%d %b %Y')}</b>\n\n"
                f"<i>Premium activated automatically. No action needed.</i>",
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            log.warning(f"[Payment] Could not notify admin of premium purchase: {e}")


async def _handle_folder_payment(user_id: int, folder_id: int, razorpay_payment_id: str):
    """After payment for a paid folder: auto-approve access and notify user."""
    from main import bot

    db_execute(
        "UPDATE payment_orders SET status = 'paid', paid_at = NOW(), razorpay_payment_id = %s "
        "WHERE razorpay_payment_id = %s OR (user_id = %s AND order_type = 'folder' AND ref_id = %s AND status = 'created')",
        (razorpay_payment_id, razorpay_payment_id, user_id, folder_id)
    )

    folder_row = db_fetchone("SELECT name FROM folders WHERE id = %s", (folder_id,))
    folder_name = folder_row[0] if folder_row else f"Folder #{folder_id}"

    # Auto-approve immediately — payment is the authorization
    db_execute(
        '''
        INSERT INTO user_folder_approval (user_id, folder_id, approved, download_completed)
        VALUES (%s, %s, TRUE, FALSE)
        ON CONFLICT (user_id, folder_id) DO UPDATE
            SET approved = TRUE, download_completed = FALSE
        ''',
        (user_id, folder_id)
    )

    try:
        await bot.send_message(
            user_id,
            f"✅ <b>Access Granted: {esc(folder_name)}!</b>\n\n"
            f"Your payment was received and access has been <b>activated automatically</b>.\n\n"
            f"Use /start and tap the folder to begin your download.",
            parse_mode=ParseMode.HTML
        )
    except exceptions.BotBlocked:
        log.warning(f"[Payment] Could not notify user {user_id} — bot blocked.")
    except Exception as e:
        log.error(f"[Payment] Error notifying user {user_id}: {e}")


def process_webhook_payload(payload: dict, razorpay_payment_id: str = ""):
    """
    Called synchronously from the Flask webhook route.
    Dispatches the correct async handler via asyncio.
    """
    import asyncio

    event = payload.get("event", "")
    log.info(f"[Razorpay Webhook] event={event}")

    if event != "payment_link.paid":
        return  # Only care about successful payments

    try:
        entity = payload["payload"]["payment_link"]["entity"]
        notes  = entity.get("notes", {})
        user_id    = int(notes["user_id"])
        order_type = notes["order_type"]          # 'premium' | 'folder'
        ref_id     = int(notes.get("ref_id", 0))
        payment_entity = payload["payload"]["payment"]["entity"]
        payment_id = payment_entity.get("id", razorpay_payment_id)
    except (KeyError, ValueError, TypeError) as e:
        log.error(f"[Razorpay Webhook] Bad payload: {e}")
        return

    # Schedule the async work on the running event loop
    try:
        loop = asyncio.get_event_loop()
        if order_type == "premium":
            loop.create_task(_activate_premium(user_id, ref_id, payment_id))
        elif order_type == "folder":
            loop.create_task(_handle_folder_payment(user_id, ref_id, payment_id))
    except RuntimeError:
        # No running loop yet (shouldn't happen with webhook mode)
        log.warning("[Razorpay Webhook] No running event loop.")


# ─────────────────────────────────────────────────────────────────────────────
# /payconfig — admin configuration
# ─────────────────────────────────────────────────────────────────────────────

async def cmd_payconfig(message: types.Message):
    """Admin: /payconfig [subcommand] [args]

    Mode-aware — subcommands vary by PAYMENT_MODE.
    Shared subcommands (all modes): setfolder, orders.
    razorpay mode: list, addplan, removeplan, setfolderprice, setdefault, status.
    stars mode:    list, addplan, removeplan, setfolderprice, setdefault, status.
    manual mode:   setfolder, orders only (no pricing config needed).
    """
    if not is_private_chat(message):
        return
    if str(message.from_user.id) not in ADMIN_IDS:
        await message.reply("Not authorized.")
        return

    args = message.get_args().split()
    sub = args[0].lower() if args else "list"

    # ── Stars mode: delegate to payment_stars ────────────────────────────────
    if PAYMENT_MODE == 'stars':
        from handlers.payment_stars import cmd_payconfig_stars
        # Stars handler returns False for unknown subs (falls through to shared)
        result = await cmd_payconfig_stars(message, args)
        if result is not False:
            return
        # Fall through to shared subcommands (setfolder, orders)

    # ── Manual mode: only allow shared subcommands ────────────────────────────
    if PAYMENT_MODE == 'manual' and sub not in ('setfolder', 'orders', 'status'):
        await message.reply(
            "ℹ️ <b>Payment mode: Manual</b>\n\n"
            "No payment gateway is configured.\n"
            "Paid folders require admin approval via /approve or inline buttons.\n\n"
            "Available commands:\n"
            "  <code>/payconfig setfolder &lt;id&gt; free|premium|paid</code>\n"
            "  <code>/payconfig orders [N]</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    if PAYMENT_MODE == 'manual' and sub == 'status':
        await message.reply(
            "ℹ️ <b>Payment Mode: Manual</b>\n\n"
            "No automated payment system active.\n"
            "Paid folders need admin approval via inline buttons or /approve.",
            parse_mode=ParseMode.HTML,
        )
        return

    if sub == "list":
        plans = _get_plans()
        default_price = _default_folder_price()
        folder_prices = db_fetchall(
            '''
            SELECT pfp.folder_id, f.name, pfp.amount_paise
            FROM payment_folder_prices pfp
            JOIN folders f ON f.id = pfp.folder_id
            ORDER BY f.name
            '''
        ) or []

        text = "💳 <b>Payment Configuration</b>\n\n"

        if plans:
            text += "<b>Premium Plans:</b>\n"
            for pid, name, amount, days in plans:
                text += f"  • <code>{esc(name)}</code> — {_fmt_inr(amount)} / {days} days (id:{pid})\n"
        else:
            text += "<b>Premium Plans:</b> <i>None configured</i>\n"

        text += f"\n<b>Default Paid-Folder Price:</b> {_fmt_inr(default_price)}\n"

        if folder_prices:
            text += "\n<b>Custom Folder Prices:</b>\n"
            for fid, fname, fprice in folder_prices:
                text += f"  • <code>{esc(fname)}</code> (id:{fid}) — {_fmt_inr(fprice)}\n"

        text += (
            "\n<b>Commands:</b>\n"
            "  <code>/payconfig addplan &lt;name&gt; &lt;amount&gt; &lt;days&gt;</code>\n"
            "  <code>/payconfig removeplan &lt;name&gt;</code>\n"
            "  <code>/payconfig setfolder &lt;id&gt; free|premium|paid</code>\n"
            "  <code>/payconfig setfolderprice &lt;folder_id&gt; &lt;amount&gt;</code>\n"
            "  <code>/payconfig setdefault &lt;amount&gt;</code>\n"
            "  <code>/payconfig status</code>\n"
            "\n<i>Amounts in ₹ rupees (e.g. 99, 199.50)</i>"
        )
        await message.reply(text, parse_mode=ParseMode.HTML)

    elif sub == "addplan":
        # /payconfig addplan <name> <amount_rupees> <days>
        if len(args) < 4:
            await message.reply(
                "Usage: <code>/payconfig addplan &lt;name&gt; &lt;amount_₹&gt; &lt;days&gt;</code>\n"
                "Example: <code>/payconfig addplan Basic 99 10</code>",
                parse_mode=ParseMode.HTML
            )
            return
        name = args[1]
        try:
            amount_paise = int(float(args[2]) * 100)
            days = int(args[3])
            if amount_paise <= 0 or days <= 0:
                raise ValueError
        except ValueError:
            await message.reply("Invalid amount or days. Use positive numbers.")
            return

        db_execute(
            '''
            INSERT INTO payment_plans (name, amount_paise, days, active)
            VALUES (%s, %s, %s, TRUE)
            ON CONFLICT (name) DO UPDATE
                SET amount_paise = EXCLUDED.amount_paise,
                    days = EXCLUDED.days,
                    active = TRUE
            ''',
            (name, amount_paise, days)
        )
        await message.reply(
            f"✅ Plan <code>{esc(name)}</code> set to "
            f"<b>{_fmt_inr(amount_paise)}</b> / <b>{days} days</b>.",
            parse_mode=ParseMode.HTML
        )

    elif sub == "removeplan":
        if len(args) < 2:
            await message.reply("Usage: <code>/payconfig removeplan &lt;name&gt;</code>", parse_mode=ParseMode.HTML)
            return
        name = args[1]
        db_execute(
            "UPDATE payment_plans SET active = FALSE WHERE name = %s",
            (name,)
        )
        await message.reply(f"✅ Plan <code>{esc(name)}</code> deactivated.", parse_mode=ParseMode.HTML)

    elif sub == "setfolderprice":
        if len(args) < 3:
            await message.reply(
                "Usage: <code>/payconfig setfolderprice &lt;folder_id&gt; &lt;amount_₹&gt;</code>",
                parse_mode=ParseMode.HTML
            )
            return
        try:
            folder_id = int(args[1])
            amount_paise = int(float(args[2]) * 100)
            if amount_paise <= 0:
                raise ValueError
        except ValueError:
            await message.reply("Invalid folder_id or amount.")
            return

        folder_row = db_fetchone("SELECT name FROM folders WHERE id = %s", (folder_id,))
        if not folder_row:
            await message.reply(f"Folder ID {folder_id} not found.")
            return

        db_execute(
            '''
            INSERT INTO payment_folder_prices (folder_id, amount_paise)
            VALUES (%s, %s)
            ON CONFLICT (folder_id) DO UPDATE SET amount_paise = EXCLUDED.amount_paise
            ''',
            (folder_id, amount_paise)
        )
        await message.reply(
            f"✅ Folder <b>{esc(folder_row[0])}</b> price set to <b>{_fmt_inr(amount_paise)}</b>.",
            parse_mode=ParseMode.HTML
        )

    elif sub == "setdefault":
        if len(args) < 2:
            await message.reply("Usage: <code>/payconfig setdefault &lt;amount_₹&gt;</code>", parse_mode=ParseMode.HTML)
            return
        try:
            amount_paise = int(float(args[1]) * 100)
            if amount_paise <= 0:
                raise ValueError
        except ValueError:
            await message.reply("Invalid amount.")
            return

        db_execute(
            '''
            INSERT INTO payment_config (key, value_int)
            VALUES ('default_folder_price_paise', %s)
            ON CONFLICT (key) DO UPDATE SET value_int = EXCLUDED.value_int
            ''',
            (amount_paise,)
        )
        await message.reply(
            f"✅ Default paid-folder price set to <b>{_fmt_inr(amount_paise)}</b>.",
            parse_mode=ParseMode.HTML
        )

    elif sub == "setfolder":
        # /payconfig setfolder <folder_id> free|premium|paid
        if len(args) < 3:
            await message.reply(
                "Usage: <code>/payconfig setfolder &lt;folder_id&gt; &lt;free|premium|paid&gt;</code>\n\n"
                "  <b>free</b>    — anyone can download\n"
                "  <b>premium</b> — Premium subscribers only\n"
                "  <b>paid</b>    — one-time purchase required",
                parse_mode=ParseMode.HTML
            )
            return
        try:
            folder_id = int(args[1])
        except ValueError:
            await message.reply("Invalid folder ID.")
            return
        folder_type = args[2].lower()
        if folder_type not in ("free", "premium", "paid"):
            await message.reply(
                "Folder type must be <code>free</code>, <code>premium</code>, or <code>paid</code>.",
                parse_mode=ParseMode.HTML
            )
            return

        folder_row = db_fetchone("SELECT name FROM folders WHERE id = %s", (folder_id,))
        if not folder_row:
            await message.reply(f"Folder ID {folder_id} not found.")
            return

        flag_map = {
            "free":    (False, False),
            "premium": (True,  False),
            "paid":    (False, True),
        }
        prem, paid = flag_map[folder_type]
        db_execute(
            "UPDATE folders SET premium = %s, admin_approval = %s WHERE id = %s",
            (prem, paid, folder_id)
        )
        label = {"free": "🔓 Free", "premium": "⭐ Premium", "paid": "💰 Paid"}[folder_type]
        await message.reply(
            f"✅ Folder <b>{esc(folder_row[0])}</b> (ID: {folder_id}) is now <b>{label}</b>.",
            parse_mode=ParseMode.HTML
        )

    elif sub == "status":
        if not RAZORPAY_KEY_ID or not RAZORPAY_KEY_SECRET:
            await message.reply(
                "❌ <b>Razorpay not configured.</b>\n\n"
                "Set <code>RAZORPAY_KEY_ID</code> and <code>RAZORPAY_KEY_SECRET</code> in your .env file.",
                parse_mode=ParseMode.HTML
            )
            return
        try:
            client = _rzp_client()
            # Quick API test — fetch account details
            client.payment.all({"count": 1})
            mode = "🔴 Live" if not RAZORPAY_KEY_ID.startswith("rzp_test_") else "🟡 Test"
            await message.reply(
                f"✅ <b>Razorpay connected</b> ({mode} mode)\n\n"
                f"Key ID: <code>{RAZORPAY_KEY_ID[:12]}…</code>\n"
                f"Webhook Secret: {'✅ Set' if __import__('config').RAZORPAY_WEBHOOK_SECRET else '❌ Not set'}",
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            await message.reply(
                f"❌ <b>Razorpay connection failed:</b>\n<code>{esc(str(e))}</code>",
                parse_mode=ParseMode.HTML
            )

    elif sub == "orders":
        limit = 10
        if len(args) > 1:
            try:
                limit = max(1, min(int(args[1]), 50))
            except ValueError:
                pass

        rows = db_fetchall(
            '''
            SELECT user_id, order_type, ref_id, amount_paise, status,
                   payment_method, created_at, paid_at
            FROM payment_orders
            ORDER BY created_at DESC
            LIMIT %s
            ''',
            (limit,)
        ) or []

        if not rows:
            await message.reply("No payment orders found.")
            return

        lines = [f"<b>💳 Last {len(rows)} Payment Orders</b>\n"]
        for uid, otype, ref_id, amount, status, method, created_at, paid_at in rows:
            status_icon = "✅" if status == "paid" else ("❌" if status == "failed" else "⏳")
            date_str = paid_at.strftime('%d %b %H:%M') if paid_at else (
                created_at.strftime('%d %b %H:%M') if created_at else "—"
            )
            method_tag = f" [{method}]" if method and method != "razorpay" else ""
            lines.append(
                f"{status_icon} <code>{uid}</code> · {otype}"
                f"{'#' + str(ref_id) if ref_id else ''}"
                f" · {_fmt_inr(amount)} · {date_str}{method_tag}"
            )

        await message.reply('\n'.join(lines), parse_mode=ParseMode.HTML)

    else:
        await message.reply(
            f"Unknown subcommand <code>{esc(sub)}</code>. Use <code>/payconfig list</code> to see options.",
            parse_mode=ParseMode.HTML
        )
to see options.",
            parse_mode=ParseMode.HTML
        )
