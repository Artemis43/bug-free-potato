import logging
from datetime import datetime
from aiogram import types
from aiogram.types import ParseMode
from middlewares.authorization import is_private_chat
from config import ADMIN_IDS
from utils.database import db_fetchall, db_fetchone, db_execute
from utils.helpers import set_current_upload_folder, esc


async def pending_users(message: types.Message):
    """/pending — list all users awaiting approval."""
    if not is_private_chat(message):
        return
    if str(message.from_user.id) not in ADMIN_IDS:
        await message.reply("You are not authorized.")
        return

    rows = db_fetchall(
        'SELECT user_id, username, first_name, last_notified FROM users '
        "WHERE status = 'pending' ORDER BY last_notified ASC NULLS FIRST"
    )

    if not rows:
        await message.reply("✅ No pending approval requests.")
        return

    lines = [f"<b>📋 Pending Approvals ({len(rows)})</b>\n"]

    for user_id, username, first_name, last_notified in rows:
        name_str  = esc(first_name or "Unknown")
        uname_str = f"@{esc(username)}" if username else "no username"

        if last_notified:
            ln = last_notified
            if hasattr(ln, 'tzinfo') and ln.tzinfo:
                ln = ln.replace(tzinfo=None)
            delta = datetime.now() - ln
            hours = int(delta.total_seconds() // 3600)
            time_str = f"{hours}h ago" if hours < 24 else f"{hours // 24}d ago"
        else:
            time_str = "unknown"

        lines.append(
            f"• {name_str} ({uname_str})\n"
            f"  ID: <code>{user_id}</code> — requested {time_str}\n"
            f"  /approve_{user_id}  /reject_{user_id}"
        )

    await message.reply('\n\n'.join(lines), parse_mode=ParseMode.HTML)


async def set_upload_folder(message: types.Message):
    """/setuploadfolder <name> — switch active upload folder without creating one."""
    if not is_private_chat(message):
        return
    if str(message.from_user.id) not in ADMIN_IDS:
        await message.reply("You are not authorized.")
        return

    folder_name = message.get_args().strip()
    if not folder_name:
        await message.reply(
            "Usage: <code>/setuploadfolder &lt;folder name&gt;</code>",
            parse_mode=ParseMode.HTML
        )
        return

    row = db_fetchone('SELECT id, name FROM folders WHERE name = %s', (folder_name,))
    if not row:
        await message.reply(
            f"❌ No folder named <b>{esc(folder_name)}</b> found.\n\n"
            "Use /list to see all folders, or /newfolder to create one.",
            parse_mode=ParseMode.HTML
        )
        return

    set_current_upload_folder(message.from_user.id, folder_name)
    await message.reply(
        f"✅ Upload folder set to <b>{esc(folder_name)}</b>.\n\nSend files now to add them.",
        parse_mode=ParseMode.HTML
    )


async def user_info(message: types.Message):
    """/userinfo <user_id> — look up a specific user's account details."""
    if not is_private_chat(message):
        return
    if str(message.from_user.id) not in ADMIN_IDS:
        await message.reply("You are not authorized.")
        return

    args = message.get_args().strip()
    if not args:
        await message.reply(
            "Usage: <code>/userinfo &lt;user_id&gt;</code>", parse_mode=ParseMode.HTML
        )
        return

    try:
        target_id = int(args)
    except ValueError:
        await message.reply("Invalid user ID — must be a number.")
        return

    row = db_fetchone(
        'SELECT user_id, username, first_name, status, premium, premium_expiration, last_download '
        'FROM users WHERE user_id = %s',
        (target_id,)
    )
    if not row:
        await message.reply(
            f"No user with ID <code>{target_id}</code> found.", parse_mode=ParseMode.HTML
        )
        return

    uid, username, first_name, status, is_premium, premium_exp, last_dl = row

    name_str  = esc(first_name or "Unknown")
    uname_str = f"@{esc(username)}" if username else "no username"

    status_map = {
        'approved': '✅ Approved',
        'pending':  '⏳ Pending',
        'rejected': '❌ Rejected',
    }

    if is_premium and premium_exp:
        exp = premium_exp
        if hasattr(exp, 'tzinfo') and exp.tzinfo:
            exp = exp.replace(tzinfo=None)
        premium_str = f"⭐ Premium (expires {exp.strftime('%d %b %Y')})"
    elif is_premium:
        premium_str = "⭐ Premium (no expiry)"
    else:
        premium_str = "🔓 Free"

    last_dl_str = last_dl.strftime('%d %b %Y %I:%M %p') if last_dl else "Never"
    dl_count    = db_fetchone(
        'SELECT COUNT(*) FROM user_folder_approval WHERE user_id = %s AND download_completed = TRUE',
        (target_id,)
    )
    completed_downloads = dl_count[0] if dl_count else 0

    lines = [
        "<b>👤 User Info</b>\n",
        f"Name: {name_str}",
        f"Username: {uname_str}",
        f"ID: <code>{uid}</code>\n",
        f"Access: {status_map.get(status, status)}",
        f"Premium: {premium_str}\n",
        f"Last Download: {last_dl_str}",
        f"Paid Folders Downloaded: {completed_downloads}",
    ]
    await message.reply('\n'.join(lines), parse_mode=ParseMode.HTML)


async def reset_cooldown(message: types.Message):
    """/resetcooldown <user_id> — clear a user's download cooldown."""
    if not is_private_chat(message):
        return
    if str(message.from_user.id) not in ADMIN_IDS:
        await message.reply("You are not authorized.")
        return

    args = message.get_args().strip()
    if not args:
        await message.reply(
            "Usage: <code>/resetcooldown &lt;user_id&gt;</code>", parse_mode=ParseMode.HTML
        )
        return

    try:
        target_id = int(args)
    except ValueError:
        await message.reply("Invalid user ID.")
        return

    if not db_fetchone('SELECT 1 FROM users WHERE user_id = %s', (target_id,)):
        await message.reply(
            f"User <code>{target_id}</code> not found.", parse_mode=ParseMode.HTML
        )
        return

    db_execute('UPDATE users SET last_download = NULL WHERE user_id = %s', (target_id,))
    await message.reply(
        f"✅ Cooldown cleared for user <code>{target_id}</code>. They can download immediately.",
        parse_mode=ParseMode.HTML
    )
