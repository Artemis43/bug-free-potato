"""
razorpay_webhook/telegram.py
──────────────────────────────
Thin Telegram Bot API client using httpx (no aiogram dependency).
Sends messages directly via the HTTP API — completely independent of the bot process.
"""

import html
import logging
from datetime import datetime

import httpx

from config import BOT_TOKEN, ADMIN_IDS, ADMIN_GROUP_ID, ADMIN_CONTACT

log = logging.getLogger(__name__)

_TG_BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"


def _esc(text) -> str:
    """HTML-escape a value for Telegram HTML parse mode."""
    return html.escape(str(text)) if text is not None else ""


def _send(chat_id: int | str, text: str, parse_mode: str = "HTML",
          reply_markup: dict | None = None) -> bool:
    """
    POST a sendMessage request synchronously.
    Returns True on success, False on failure (already logged).
    """
    payload: dict = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
    }
    if reply_markup:
        import json
        payload["reply_markup"] = json.dumps(reply_markup)

    try:
        resp = httpx.post(
            f"{_TG_BASE}/sendMessage",
            json=payload,
            timeout=10,
        )
        if not resp.is_success:
            log.error(f"[Telegram] sendMessage failed ({resp.status_code}): {resp.text}")
            return False
        return True
    except Exception as e:
        log.error(f"[Telegram] sendMessage exception: {e}")
        return False


def _admin_target() -> str:
    """Return group ID if configured, else first admin ID."""
    return ADMIN_GROUP_ID if ADMIN_GROUP_ID else (ADMIN_IDS[0] if ADMIN_IDS else "")


# ─────────────────────────────────────────────────────────────────────────────
# Notification helpers
# ─────────────────────────────────────────────────────────────────────────────

def notify_user_premium_activated(user_id: int, plan_name: str,
                                   days: int, expiration_date: datetime) -> None:
    """DM the user that their premium is now active."""
    _send(
        user_id,
        f"🎉 <b>Payment Received! You're now Premium.</b>\n\n"
        f"Plan: <b>{_esc(plan_name)}</b>\n"
        f"Duration: <b>{days} days</b>\n"
        f"Expires: <b>{expiration_date.strftime('%d %b %Y')}</b>\n\n"
        f"✨ You now get:\n"
        f"  • ⚡ 5s interval between files\n"
        f"  • ⏱ 2 min cooldown between downloads\n"
        f"  • ⭐ Access to all Premium-only folders\n\n"
        f"Use /start to explore!",
    )


def notify_user_folder_payment_received(user_id: int, folder_name: str) -> None:
    """DM the user that their folder payment was received and is awaiting admin approval."""
    _send(
        user_id,
        f"✅ <b>Payment received for {_esc(folder_name)}!</b>\n\n"
        f"An admin will approve your download shortly.\n"
        f"You'll be notified here once it's ready.\n\n"
        f"<i>This usually takes a few hours.</i>",
    )


def notify_admin_folder_approval(user_id: int, folder_id: int,
                                  folder_name: str, user_info: tuple | None) -> None:
    """Send an approval request to the admin group/DM with inline approve/reject buttons."""
    first_name = _esc(user_info[0] if user_info and user_info[0] else f"User {user_id}")
    username   = f"@{_esc(user_info[1])}" if user_info and user_info[1] else "no username"

    text = (
        f"💰 <b>Paid-Folder Download Request</b> <i>(via Razorpay)</i>\n\n"
        f"Name: {first_name}\n"
        f"Username: {username}\n"
        f"ID: <code>{user_id}</code>\n\n"
        f"Folder: <b>{_esc(folder_name)}</b> (ID: {folder_id})\n\n"
        f"Tap a button below to approve or reject."
    )

    # Inline keyboard with approve / reject callbacks (handled by the bot)
    reply_markup = {
        "inline_keyboard": [[
            {"text": "✅ Approve", "callback_data": f"papprove:{user_id}:{folder_id}"},
            {"text": "❌ Reject",  "callback_data": f"preject:{user_id}:{folder_id}"},
        ]]
    }

    target = _admin_target()
    if target:
        _send(target, text, reply_markup=reply_markup)
    else:
        log.error("[Telegram] No admin target configured — cannot send approval request.")
