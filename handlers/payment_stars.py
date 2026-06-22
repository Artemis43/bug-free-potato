from utils.keyboard import InlineBuilder, InlineKeyboardButton
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest
from aiogram import Router
from aiogram.enums import ParseMode
from utils.bot_ref import get_bot
"""
handlers/payment_stars.py
─────────────────────────────────────────────────────────────────────────────
Telegram Stars payment integration.

Flow
────
1. Premium subscription  (/pay [plan])
   User picks a Stars plan → bot sends invoice → user pays in Telegram
   → pre_checkout_query → successful_payment → premium activated

2. Paid-folder one-time purchase  (/payfolder <folder_id>)
   Bot sends folder invoice → user pays → successful_payment → access granted

3. Admin configuration  (/payconfig ...)
   Handled via cmd_payconfig_stars() called from payment.py when
   PAYMENT_MODE=stars. Subcommands: list, addplan, removeplan,
   setfolderprice, setdefault, status, orders.

Stars notes
───────────
  • provider_token=""  (empty string) → Telegram Stars
  • currency="XTR"
  • amount is in whole Stars (no sub-units)
  • invoice_payload carries "premium:{plan_id}" or "folder:{folder_id}"
  • telegram_payment_charge_id is stored as the order reference
"""

import logging
from datetime import datetime, timedelta

from aiogram import types
from aiogram import Router
from aiogram.enums import ParseMode
from aiogram.types import InlineKeyboardMarkup, LabeledPrice
from aiogram import Router
from aiogram.enums import ParseMode

from config import ADMIN_CONTACT, ADMIN_GROUP_ID, ADMIN_IDS
from utils.database import db_execute, db_fetchall, db_fetchone
from utils.helpers import esc

router = Router()

log = logging.getLogger(__name__)

_CURRENCY = "XTR"


# ─────────────────────────────────────────────────────────────────────────────
# DB helpers
# ─────────────────────────────────────────────────────────────────────────────

def get_stars_plans() -> list[tuple]:
    """Return active Stars plans sorted by price. [(id, name, amount_stars, days), ...]"""
    return db_fetchall(
        "SELECT id, name, amount_stars, days FROM stars_payment_plans "
        "WHERE active = TRUE ORDER BY amount_stars"
    ) or []


def get_stars_folder_price(folder_id: int) -> int | None:
    """Return per-folder Stars price override, or None."""
    row = db_fetchone(
        "SELECT amount_stars FROM stars_folder_prices WHERE folder_id = %s",
        (folder_id,),
    )
    return row[0] if row else None


def default_stars_folder_price() -> int:
    """Return the global default Stars folder price."""
    row = db_fetchone(
        "SELECT value_int FROM payment_config WHERE key = 'default_folder_price_stars'"
    )
    return row[0] if row else 50


# ─────────────────────────────────────────────────────────────────────────────
# Invoice senders
# ─────────────────────────────────────────────────────────────────────────────

async def send_premium_invoice(bot, user_id: int, plan: tuple) -> None:
    """Send a Telegram Stars invoice for a premium plan."""
    plan_id, name, amount_stars, days = plan
    try:
        await bot.send_invoice(
            chat_id=user_id,
            title=f"⭐ Premium — {name}",
            description=f"{days}-day Premium access. Faster downloads and premium folders.",
            payload=f"premium:{plan_id}",
            provider_token="",          # empty = Telegram Stars
            currency=_CURRENCY,
            prices=[LabeledPrice(label=name, amount=amount_stars)],
        )
    except Exception as e:
        log.error(f"[Stars] Failed to send premium invoice to {user_id}: {e}")
        try:
            await bot.send_message(user_id, "❌ Could not create Stars payment. Please try again.")
        except Exception:
            pass


async def send_folder_invoice(bot, user_id: int, folder_id: int) -> None:
    """Send a Telegram Stars invoice for a paid folder."""
    folder_row = db_fetchone(
        "SELECT name, admin_approval FROM folders WHERE id = %s", (folder_id,)
    )
    if not folder_row:
        await bot.send_message(user_id, "❌ Folder not found.")
        return
    folder_name, requires_payment = folder_row
    if not requires_payment:
        await bot.send_message(user_id, "This folder does not require payment.")
        return

    price = get_stars_folder_price(folder_id)
    if price is None:
        price = default_stars_folder_price()

    try:
        await bot.send_invoice(
            chat_id=user_id,
            title=f"💰 {folder_name}",
            description=f"One-time access to '{folder_name}'. Download once after payment.",
            payload=f"folder:{folder_id}",
            provider_token="",
            currency=_CURRENCY,
            prices=[LabeledPrice(label=folder_name, amount=price)],
        )
    except Exception as e:
        log.error(f"[Stars] Failed to send folder invoice to {user_id}: {e}")
        try:
            await bot.send_message(user_id, "❌ Could not create Stars payment. Please try again.")
        except Exception:
            pass


async def create_folder_invoice_link(bot, folder_id: int) -> str | None:
    """Create a Telegram Stars invoice link for a paid folder."""
    folder_row = db_fetchone(
        "SELECT name, admin_approval FROM folders WHERE id = %s", (folder_id,)
    )
    if not folder_row:
        return None
    folder_name, requires_payment = folder_row

    price = get_stars_folder_price(folder_id)
    if price is None:
        price = default_stars_folder_price()

    try:
        link = await bot.create_invoice_link(
            title=f"💰 {folder_name}",
            description=f"One-time access to '{folder_name}'. Download once after payment.",
            payload=f"folder:{folder_id}",
            provider_token="",
            currency=_CURRENCY,
            prices=[LabeledPrice(label=folder_name, amount=price)],
        )
        return link
    except Exception as e:
        log.error(f"[Stars] Failed to create folder invoice link: {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# /pay — Stars plan picker (shared with payment.py routing)
# ─────────────────────────────────────────────────────────────────────────────

async def cmd_pay_stars(message: types.Message) -> None:
    """/pay in Stars mode — show plan picker or go straight to invoice."""
    from middlewares.authorization import is_private_chat
    if not is_private_chat(message):
        return

    user_id = message.from_user.id
    bot = get_bot()
    plans = get_stars_plans()

    if not plans:
        await message.reply(
            "⭐ No payment plans have been set up yet.\n"
            f"Contact {ADMIN_CONTACT} for details."
        )
        return

    arg = (message.text.split(None, 1)[1].strip() if message.text and len(message.text.split(None, 1)) > 1 else '').lower()

    # Direct plan by name
    if arg:
        matched = next((p for p in plans if p[1].lower() == arg), None)
        if not matched:
            await message.reply(
                f"❌ Unknown plan <code>{esc(arg)}</code>.\n\nUse /pay to see all plans.",
                parse_mode=ParseMode.HTML,
            )
            return
        await send_premium_invoice(bot, user_id, matched)
        return

    # Single plan → send invoice directly, no need for picker
    if len(plans) == 1:
        await send_premium_invoice(bot, user_id, plans[0])
        return

    # Multiple plans → show inline picker
    kb = InlineBuilder()
    for plan_id, name, amount_stars, days in plans:
        kb.add(InlineKeyboardButton(
            f"⭐ {name} — {amount_stars} Stars ({days} days)",
            callback_data=f"stars_plan:{plan_id}",
        ))
    kb.add(InlineKeyboardButton("✕ Cancel", callback_data="stars_cancel"))
    await message.reply(
        "⭐ <b>Choose a Premium Plan</b>\n\nSelect the plan you want to purchase:",
        parse_mode=ParseMode.HTML,
        reply_markup=kb.build(),
    )


async def cmd_payfolder_stars(message: types.Message) -> None:
    """/payfolder in Stars mode."""
    from middlewares.authorization import is_private_chat
    if not is_private_chat(message):
        return

    args = (message.text.split(None, 1)[1].strip() if message.text and len(message.text.split(None, 1)) > 1 else '')
    if not args:
        await message.reply(
            "Usage: <code>/payfolder &lt;folder_id&gt;</code>\n\nUse /start to see folder IDs.",
            parse_mode=ParseMode.HTML,
        )
        return
    try:
        folder_id = int(args)
    except ValueError:
        await message.reply("Invalid folder ID.")
        return

    await send_folder_invoice(get_bot(), message.from_user.id, folder_id)


# ─────────────────────────────────────────────────────────────────────────────
# Callback: Stars plan picker
# ─────────────────────────────────────────────────────────────────────────────

async def handle_stars_plan_callback(callback_query: types.CallbackQuery) -> None:
    """Dispatched from start._CB_HANDLERS for stars_plan: and stars_cancel."""
    bot = get_bot()
    data = callback_query.data or ""
    user_id = callback_query.from_user.id

    if data == "stars_cancel":
        await bot.answer_callback_query(callback_query.id)
        try:
            await bot.delete_message(
                callback_query.message.chat.id, callback_query.message.message_id
            )
        except Exception:
            pass
        return

    if data.startswith("stars_plan:"):
        try:
            plan_id = int(data.split(":", 1)[1])
        except (ValueError, IndexError):
            await bot.answer_callback_query(callback_query.id, "Invalid plan.")
            return

        plan = db_fetchone(
            "SELECT id, name, amount_stars, days FROM stars_payment_plans "
            "WHERE id = %s AND active = TRUE",
            (plan_id,),
        )
        if not plan:
            await bot.answer_callback_query(callback_query.id, "Plan no longer available.", show_alert=True)
            return

        await bot.answer_callback_query(callback_query.id)
        try:
            await bot.delete_message(
                callback_query.message.chat.id, callback_query.message.message_id
            )
        except Exception:
            pass

        await send_premium_invoice(bot, user_id, plan)


# ─────────────────────────────────────────────────────────────────────────────
# Telegram payment protocol handlers
# ─────────────────────────────────────────────────────────────────────────────

async def pre_checkout_handler(pre_checkout_query: types.PreCheckoutQuery) -> None:
    """Validate the Stars payment before Telegram confirms it."""
    bot = get_bot()
    payload = pre_checkout_query.invoice_payload or ""
    ok, error_msg = False, "Invalid payment."

    if payload.startswith("premium:"):
        try:
            plan_id = int(payload.split(":", 1)[1])
            row = db_fetchone(
                "SELECT id FROM stars_payment_plans WHERE id = %s AND active = TRUE",
                (plan_id,),
            )
            ok = row is not None
            if not ok:
                error_msg = "This plan is no longer available."
        except (ValueError, IndexError):
            error_msg = "Invalid plan."

    elif payload.startswith("folder:"):
        try:
            folder_id = int(payload.split(":", 1)[1])
            row = db_fetchone(
                "SELECT id FROM folders WHERE id = %s AND admin_approval = TRUE",
                (folder_id,),
            )
            ok = row is not None
            if not ok:
                error_msg = "This folder is no longer available for purchase."
        except (ValueError, IndexError):
            error_msg = "Invalid folder."

    await bot.answer_pre_checkout_query(
        pre_checkout_query.id, ok=ok, error_message=None if ok else error_msg
    )


async def successful_payment_handler(message: types.Message) -> None:
    """Process a confirmed Telegram Stars payment."""
    from handlers.setpremium import remove_premium_after_expiry
    import asyncio

    bot = get_bot()
    payment = message.successful_payment
    payload = payment.invoice_payload or ""
    user_id = message.from_user.id
    stars_paid = payment.total_amount
    charge_id = payment.telegram_payment_charge_id  # unique Telegram reference

    log.info(f"[Stars] Payment confirmed: user={user_id} payload={payload} stars={stars_paid} charge={charge_id}")

    # ── Premium plan ──────────────────────────────────────────────────────────
    if payload.startswith("premium:"):
        try:
            plan_id = int(payload.split(":", 1)[1])
        except (ValueError, IndexError):
            log.error(f"[Stars] Bad premium payload: {payload}")
            return

        plan = db_fetchone(
            "SELECT name, amount_stars, days FROM stars_payment_plans WHERE id = %s",
            (plan_id,),
        )
        if not plan:
            log.error(f"[Stars] Plan {plan_id} not found after payment (charge {charge_id})")
            await bot.send_message(
                user_id,
                "✅ Payment received! If premium isn't active in a minute, contact admin.",
            )
            return

        name, amount_stars, days = plan

        # Extend from current expiry if user already has active premium (don't truncate)
        current_exp_row = db_fetchone(
            "SELECT premium_expiration FROM users WHERE user_id = %s", (user_id,)
        )
        current_exp = current_exp_row[0] if current_exp_row and current_exp_row[0] else None
        if current_exp is not None:
            if hasattr(current_exp, 'tzinfo') and current_exp.tzinfo is not None:
                current_exp = current_exp.replace(tzinfo=None)
        base_date = max(current_exp, datetime.now()) if (current_exp and current_exp > datetime.now()) else datetime.now()
        expiration_date = base_date + timedelta(days=days)

        db_execute(
            "UPDATE users SET premium = TRUE, premium_expiration = %s WHERE user_id = %s",
            (expiration_date, user_id),
        )
        db_execute(
            """
            INSERT INTO payment_orders
                (razorpay_link_id, user_id, order_type, ref_id,
                 amount_paise, status, payment_method, created_at, paid_at)
            VALUES (%s, %s, 'premium', %s, %s, 'paid', 'stars', NOW(), NOW())
            ON CONFLICT (razorpay_link_id) DO NOTHING
            """,
            (charge_id, user_id, plan_id, stars_paid),
        )

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
                parse_mode=ParseMode.HTML,
            )
        except TelegramForbiddenError:
            pass

        # Notify admin
        _notify_admin_premium(user_id, name, days, stars_paid, expiration_date)

    # ── Paid folder ───────────────────────────────────────────────────────────
    elif payload.startswith("folder:"):
        try:
            folder_id = int(payload.split(":", 1)[1])
        except (ValueError, IndexError):
            log.error(f"[Stars] Bad folder payload: {payload}")
            return

        folder_row = db_fetchone("SELECT name FROM folders WHERE id = %s", (folder_id,))
        folder_name = folder_row[0] if folder_row else f"Folder #{folder_id}"

        db_execute(
            """
            INSERT INTO user_folder_approval (user_id, folder_id, approved, download_completed)
            VALUES (%s, %s, TRUE, FALSE)
            ON CONFLICT (user_id, folder_id) DO UPDATE
                SET approved = TRUE, download_completed = FALSE
            """,
            (user_id, folder_id),
        )
        db_execute(
            """
            INSERT INTO payment_orders
                (razorpay_link_id, user_id, order_type, ref_id,
                 amount_paise, status, payment_method, created_at, paid_at)
            VALUES (%s, %s, 'folder', %s, %s, 'paid', 'stars', NOW(), NOW())
            ON CONFLICT (razorpay_link_id) DO NOTHING
            """,
            (charge_id, user_id, folder_id, stars_paid),
        )

        try:
            await bot.send_message(
                user_id,
                f"✅ <b>Access Granted: {esc(folder_name)}!</b>\n\n"
                f"Payment received — access activated automatically.\n\n"
                f"Use /start and tap the folder to begin your download.",
                parse_mode=ParseMode.HTML,
            )
        except TelegramForbiddenError:
            pass

    else:
        log.warning(f"[Stars] Unknown payload: {payload!r}")


def _notify_admin_premium(user_id: int, plan_name: str, days: int,
                           stars_paid: int, expiration_date: datetime) -> None:
    """Fire-and-forget admin notification after Stars premium purchase."""
    import asyncio

    async def _send():
        bot = get_bot()
        admin_target = ADMIN_GROUP_ID if ADMIN_GROUP_ID else (ADMIN_IDS[0] if ADMIN_IDS else None)
        if not admin_target:
            return
        user_row = db_fetchone("SELECT first_name, username FROM users WHERE user_id = %s", (user_id,))
        first_name = esc(user_row[0] if user_row and user_row[0] else f"User {user_id}")
        username = f"@{esc(user_row[1])}" if user_row and user_row[1] else "no username"
        try:
            await bot.send_message(
                admin_target,
                f"⭐ <b>Premium via Stars</b>\n\n"
                f"Name: {first_name}\nUsername: {username}\n"
                f"ID: <code>{user_id}</code>\n\n"
                f"Plan: <b>{esc(plan_name)}</b> ({days} days)\n"
                f"Stars paid: <b>{stars_paid}</b>\n"
                f"Expires: <b>{expiration_date.strftime('%d %b %Y')}</b>",
                parse_mode=ParseMode.HTML,
            )
        except Exception as e:
            log.warning(f"[Stars] Could not notify admin: {e}")

    asyncio.create_task(_send())


# ─────────────────────────────────────────────────────────────────────────────
# /payconfig — Stars mode subcommands (called from payment.cmd_payconfig)
# ─────────────────────────────────────────────────────────────────────────────

async def cmd_payconfig_stars(message: types.Message, args: list[str]) -> 'bool | None':
    """Handle /payconfig subcommands when PAYMENT_MODE=stars."""
    sub = args[0].lower() if args else "list"

    if sub == "list":
        plans = get_stars_plans()
        default_price = default_stars_folder_price()
        folder_prices = db_fetchall(
            """
            SELECT sfp.folder_id, f.name, sfp.amount_stars
            FROM stars_folder_prices sfp
            JOIN folders f ON f.id = sfp.folder_id
            ORDER BY f.name
            """
        ) or []

        text = "⭐ <b>Payment Configuration (Telegram Stars)</b>\n\n"

        if plans:
            text += "<b>Premium Plans:</b>\n"
            for pid, name, stars, days in plans:
                text += f"  • <code>{esc(name)}</code> — {stars} Stars / {days} days (id:{pid})\n"
        else:
            text += "<b>Premium Plans:</b> <i>None configured</i>\n"

        text += f"\n<b>Default Paid-Folder Price:</b> {default_price} Stars\n"

        if folder_prices:
            text += "\n<b>Custom Folder Prices:</b>\n"
            for fid, fname, fstars in folder_prices:
                text += f"  • <code>{esc(fname)}</code> (id:{fid}) — {fstars} Stars\n"

        text += (
            "\n<b>Commands:</b>\n"
            "  <code>/payconfig addplan &lt;name&gt; &lt;stars&gt; &lt;days&gt;</code>\n"
            "  <code>/payconfig removeplan &lt;name&gt;</code>\n"
            "  <code>/payconfig setfolderprice &lt;folder_id&gt; &lt;stars&gt;</code>\n"
            "  <code>/payconfig setdefault &lt;stars&gt;</code>\n"
            "  <code>/payconfig setfolder &lt;id&gt; free|premium|paid</code>\n"
            "  <code>/payconfig orders [N]</code>\n"
            "\n<i>Amounts in whole Stars (e.g. 50, 120)</i>"
        )
        await message.reply(text, parse_mode=ParseMode.HTML)

    elif sub == "addplan":
        if len(args) < 4:
            await message.reply(
                "Usage: <code>/payconfig addplan &lt;name&gt; &lt;stars&gt; &lt;days&gt;</code>\n"
                "Example: <code>/payconfig addplan Basic 50 10</code>",
                parse_mode=ParseMode.HTML,
            )
            return
        name = args[1]
        try:
            stars = int(args[2])
            days = int(args[3])
            if stars <= 0 or days <= 0:
                raise ValueError
        except ValueError:
            await message.reply("Invalid stars or days. Use positive integers.")
            return

        db_execute(
            """
            INSERT INTO stars_payment_plans (name, amount_stars, days, active)
            VALUES (%s, %s, %s, TRUE)
            ON CONFLICT (name) DO UPDATE
                SET amount_stars = EXCLUDED.amount_stars,
                    days = EXCLUDED.days,
                    active = TRUE
            """,
            (name, stars, days),
        )
        await message.reply(
            f"✅ Plan <code>{esc(name)}</code> set to <b>{stars} Stars</b> / <b>{days} days</b>.",
            parse_mode=ParseMode.HTML,
        )

    elif sub == "removeplan":
        if len(args) < 2:
            await message.reply(
                "Usage: <code>/payconfig removeplan &lt;name&gt;</code>", parse_mode=ParseMode.HTML
            )
            return
        name = args[1]
        db_execute("UPDATE stars_payment_plans SET active = FALSE WHERE name = %s", (name,))
        await message.reply(f"✅ Plan <code>{esc(name)}</code> deactivated.", parse_mode=ParseMode.HTML)

    elif sub == "setfolderprice":
        if len(args) < 3:
            await message.reply(
                "Usage: <code>/payconfig setfolderprice &lt;folder_id&gt; &lt;stars&gt;</code>",
                parse_mode=ParseMode.HTML,
            )
            return
        try:
            folder_id = int(args[1])
            stars = int(args[2])
            if stars <= 0:
                raise ValueError
        except ValueError:
            await message.reply("Invalid folder_id or stars amount.")
            return

        folder_row = db_fetchone("SELECT name FROM folders WHERE id = %s", (folder_id,))
        if not folder_row:
            await message.reply(f"Folder ID {folder_id} not found.")
            return

        db_execute(
            """
            INSERT INTO stars_folder_prices (folder_id, amount_stars)
            VALUES (%s, %s)
            ON CONFLICT (folder_id) DO UPDATE SET amount_stars = EXCLUDED.amount_stars
            """,
            (folder_id, stars),
        )
        await message.reply(
            f"✅ Folder <b>{esc(folder_row[0])}</b> price set to <b>{stars} Stars</b>.",
            parse_mode=ParseMode.HTML,
        )

    elif sub == "setdefault":
        if len(args) < 2:
            await message.reply(
                "Usage: <code>/payconfig setdefault &lt;stars&gt;</code>", parse_mode=ParseMode.HTML
            )
            return
        try:
            stars = int(args[1])
            if stars <= 0:
                raise ValueError
        except ValueError:
            await message.reply("Invalid stars amount. Use a positive integer.")
            return

        db_execute(
            """
            INSERT INTO payment_config (key, value_int)
            VALUES ('default_folder_price_stars', %s)
            ON CONFLICT (key) DO UPDATE SET value_int = EXCLUDED.value_int
            """,
            (stars,),
        )
        await message.reply(
            f"✅ Default paid-folder price set to <b>{stars} Stars</b>.",
            parse_mode=ParseMode.HTML,
        )

    elif sub == "status":
        plans = get_stars_plans()
        default_price = default_stars_folder_price()
        plan_count = len(plans)
        plan_status = (
            f"✅ {plan_count} plan(s) configured" if plans
            else f"⚠️ No plans configured yet — use /payconfig addplan"
        )
        await message.reply(
            f"⭐ <b>Payment Mode: Telegram Stars</b>\n\n"
            "No external gateway — Stars payments are handled natively by Telegram.\n"
            "No API keys or webhooks required.\n\n"
            f"<b>Premium Plans:</b> {plan_status}\n"
            f"<b>Default Folder Price:</b> {default_price} Stars\n\n"
            "Use <code>/payconfig list</code> to see full configuration.",
            parse_mode=ParseMode.HTML,
        )

    else:
        # Unknown subcommand — fall through to shared handlers in payment.py
        return False

    return True
