"""
razorpay_webhook/handlers.py
──────────────────────────────────────────────────────────────────────────────
Business logic for each Razorpay webhook event.

Design principles
─────────────────
• Idempotent  — safe to call twice (duplicate Razorpay retries are ignored).
• Synchronous — no async; runs inside a FastAPI BackgroundTask so the HTTP
                response to Razorpay is sent immediately (200 OK), and the
                actual DB writes + Telegram notifications happen in the background.
• Decoupled   — talks to Telegram via plain HTTP (telegram.py), not via the
                bot's aiogram instance.
"""

import logging
from datetime import datetime, timedelta

import database as db
import telegram as tg

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Dispatcher
# ─────────────────────────────────────────────────────────────────────────────

def dispatch(event: str, payload: dict) -> None:
    """
    Route an incoming Razorpay webhook event to the appropriate handler.
    All handlers are synchronous and safe to call in a background task.
    """
    log.info(f"[Webhook] Dispatching event: {event}")

    if event == "payment_link.paid":
        _handle_payment_link_paid(payload)
    else:
        log.debug(f"[Webhook] Ignoring unhandled event: {event}")


# ─────────────────────────────────────────────────────────────────────────────
# payment_link.paid
# ─────────────────────────────────────────────────────────────────────────────

def _handle_payment_link_paid(payload: dict) -> None:
    """
    Triggered when a Razorpay Payment Link is fully paid.

    Expected payload structure:
      payload.payment_link.entity.notes.user_id      → our Telegram user ID
      payload.payment_link.entity.notes.order_type   → 'premium' | 'folder'
      payload.payment_link.entity.notes.ref_id       → plan_id or folder_id
      payload.payment_link.entity.id                 → razorpay_link_id
      payload.payment.entity.id                      → razorpay_payment_id
    """
    try:
        pl_entity      = payload["payload"]["payment_link"]["entity"]
        notes          = pl_entity.get("notes", {})
        razorpay_link_id = pl_entity["id"]

        user_id    = int(notes["user_id"])
        order_type = str(notes["order_type"])          # 'premium' | 'folder'
        ref_id     = int(notes.get("ref_id", 0))

        payment_entity   = payload["payload"]["payment"]["entity"]
        razorpay_payment_id = str(payment_entity["id"])
        amount_paise        = int(payment_entity.get("amount", 0))

    except (KeyError, ValueError, TypeError) as exc:
        log.error(f"[Webhook] Malformed payment_link.paid payload: {exc}")
        return

    log.info(
        f"[Webhook] payment_link.paid | user={user_id} type={order_type} "
        f"ref={ref_id} payment={razorpay_payment_id} amount=₹{amount_paise/100:.2f}"
    )

    # ── Idempotency guard ────────────────────────────────────────────────────
    if db.is_duplicate_payment(razorpay_payment_id):
        log.warning(f"[Webhook] Duplicate payment {razorpay_payment_id} — skipping.")
        return

    # ── Route by order type ──────────────────────────────────────────────────
    if order_type == "premium":
        _process_premium_payment(user_id, ref_id, razorpay_link_id, razorpay_payment_id)
    elif order_type == "folder":
        _process_folder_payment(user_id, ref_id, razorpay_link_id, razorpay_payment_id)
    else:
        log.warning(f"[Webhook] Unknown order_type: {order_type}")


def _process_premium_payment(user_id: int, plan_id: int,
                              razorpay_link_id: str, razorpay_payment_id: str) -> None:
    """Activate premium for the user who paid for a subscription plan."""

    plan = db.get_plan(plan_id)
    if not plan:
        log.error(f"[Webhook] Plan {plan_id} not found for user {user_id}")
        return

    plan_name, amount_paise, days = plan
    expiration_date = datetime.utcnow() + timedelta(days=days)

    # 1. Mark order paid (idempotent UPDATE)
    db.mark_order_paid(razorpay_link_id, razorpay_payment_id, user_id, "premium", plan_id)

    # 2. Activate premium in users table
    db.activate_user_premium(user_id, expiration_date)

    log.info(
        f"[Webhook] Premium activated | user={user_id} plan={plan_name!r} "
        f"days={days} expires={expiration_date.date()}"
    )

    # 3. Notify user via Telegram
    tg.notify_user_premium_activated(user_id, plan_name, days, expiration_date)


def _process_folder_payment(user_id: int, folder_id: int,
                             razorpay_link_id: str, razorpay_payment_id: str) -> None:
    """Record a paid-folder purchase and grant access automatically."""

    folder_name = db.get_folder_name(folder_id)
    user_info   = db.get_user_info(user_id)

    # 1. Mark order paid
    db.mark_order_paid(razorpay_link_id, razorpay_payment_id, user_id, "folder", folder_id)

    # 2. Auto-approve access — payment is the authorization
    db.approve_folder_access(user_id, folder_id)

    log.info(
        f"[Webhook] Folder access granted | user={user_id} folder={folder_id} ({folder_name!r})"
    )

    # 3. Notify admin (informational only — no action required)
    tg.notify_admin_folder_purchased(user_id, folder_id, folder_name, user_info)

    # 4. Notify user that access is immediately available
    tg.notify_user_folder_access_granted(user_id, folder_name)
