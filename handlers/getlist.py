import logging
from aiogram import types
from aiogram import Router
from aiogram.enums import ParseMode
from aiogram import Router
from middlewares.authorization import is_private_chat
from config import ADMIN_IDS
from utils.database import db_fetchall
from utils.helpers import esc

router = Router()


async def list_all(message: types.Message):
    """Admin command: /list — show all folders, users, and premium stats."""
    if not is_private_chat(message):
        return

    if str(message.from_user.id) not in ADMIN_IDS:
        await message.reply("You are not authorized.")
        return

    try:
        folders         = db_fetchall('SELECT id, name, download_count FROM folders ORDER BY name')
        premium_folders = db_fetchall('SELECT id, name FROM folders WHERE premium = TRUE ORDER BY name')
        paid_folders    = db_fetchall('SELECT id, name FROM folders WHERE admin_approval = TRUE ORDER BY name')

        all_users     = db_fetchall("SELECT user_id, username, first_name, status FROM users ORDER BY user_id")
        premium_users = db_fetchall(
            "SELECT user_id, username, first_name, premium_expiration FROM users WHERE premium = TRUE"
        )
        pending_users = db_fetchall(
            "SELECT user_id, username, first_name FROM users WHERE status = 'pending'"
        )

        lines = []

        # ── Folders ───────────────────────────────────────────────────────────
        lines.append("<b>📁 All Folders:</b>")
        if folders:
            for fid, fname, dl_count in folders:
                lines.append(f"  • {esc(fname)} (ID: {fid}, ⬇️ {dl_count})")
        else:
            lines.append("  None")

        lines.append("\n<b>⭐ Premium Folders:</b>")
        lines += [f"  • {esc(f[1])} (ID: {f[0]})" for f in premium_folders] or ["  None"]

        lines.append("\n<b>💰 Paid (Admin-Approval) Folders:</b>")
        lines += [f"  • {esc(f[1])} (ID: {f[0]})" for f in paid_folders] or ["  None"]

        # ── Users ─────────────────────────────────────────────────────────────
        lines.append(f"\n<b>👥 Total Users: {len(all_users)}</b>")
        lines.append(f"  ⏳ Pending: {len(pending_users)}")

        if pending_users:
            lines.append("\n<b>⏳ Pending Approval:</b>")
            for uid, uname, fname in pending_users:
                name_str  = esc(fname or "Unknown")
                uname_str = f"@{esc(uname)}" if uname else "no username"
                lines.append(f"  • {name_str} ({uname_str}) — ID: {uid}")

        lines.append("\n<b>🌟 Premium Users:</b>")
        if premium_users:
            for uid, uname, fname, exp in premium_users:
                name_str  = esc(fname or "Unknown")
                uname_str = f"@{esc(uname)}" if uname else "no username"
                exp_str   = exp.strftime('%d %b %Y') if exp else "N/A"
                lines.append(f"  • {name_str} ({uname_str}) — expires {exp_str}")
        else:
            lines.append("  None")

        # ── Chunk and send ────────────────────────────────────────────────────
        response = "\n".join(lines)
        max_len  = 4096
        chunks   = [response[i:i + max_len] for i in range(0, len(response), max_len)]

        for chunk in chunks:
            await message.answer(chunk, parse_mode=ParseMode.HTML)

    except Exception as e:
        logging.error(f"Error in /list command: {e}")
        await message.answer("An error occurred while fetching the list.")