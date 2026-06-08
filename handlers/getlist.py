import logging
from aiogram import types
from aiogram.types import ParseMode
from middlewares.authorization import is_private_chat, is_user_member
from config import ADMIN_IDS, REQUIRED_CHANNELS
from utils.database import db_fetchall


async def list_all(message: types.Message):
    """Admin command: /list — show all folders, users, premium stats."""
    if not is_private_chat(message):
        return

    user_id = message.from_user.id

    # Re-use the same pattern: channel membership + admin check
    if not await is_user_member(user_id):
        join_message = "Welcome to The Medical Content Bot ✨\n\nJoin our backup channels to remain connected ✊\n"
        for channel in REQUIRED_CHANNELS:
            join_message += f"{channel}\n"
        await message.reply(join_message)
        return

    if str(user_id) not in ADMIN_IDS:
        await message.reply("You are not authorized to access the database.")
        return

    try:
        folders               = db_fetchall('SELECT id, name, download_count FROM folders ORDER BY name')
        premium_folders       = db_fetchall('SELECT id, name, download_count FROM folders WHERE premium = TRUE ORDER BY name')
        admin_approval_folders= db_fetchall('SELECT id, name, download_count FROM folders WHERE admin_approval = TRUE ORDER BY name')

        users         = db_fetchall("SELECT user_id, status FROM users ORDER BY user_id")
        premium_users = db_fetchall("SELECT user_id, premium_expiration FROM users WHERE premium = TRUE")
        pending_users = db_fetchall("SELECT user_id FROM users WHERE status = 'pending'")

        lines = []

        lines.append("<b>📁 All Folders:</b>")
        lines += [f"  • {f[1]} (ID: {f[0]}, ⬇️ {f[2]})" for f in folders] or ["  None"]

        lines.append("\n<b>⭐ Premium Folders:</b>")
        lines += [f"  • {f[1]} (ID: {f[0]}, ⬇️ {f[2]})" for f in premium_folders] or ["  None"]

        lines.append("\n<b>💰 Paid (Admin-Approval) Folders:</b>")
        lines += [f"  • {f[1]} (ID: {f[0]}, ⬇️ {f[2]})" for f in admin_approval_folders] or ["  None"]

        lines.append(f"\n<b>👥 Total Users: {len(users)}</b>")
        lines.append(f"  Pending approval: {len(pending_users)}")

        lines.append("\n<b>🌟 Premium Users:</b>")
        if premium_users:
            for uid, exp in premium_users:
                exp_str = exp.strftime('%Y-%m-%d') if exp else "N/A"
                lines.append(f"  • {uid} (expires: {exp_str})")
        else:
            lines.append("  None")

        response = "\n".join(lines)
        max_len  = 4096
        chunks   = [response[i:i + max_len] for i in range(0, len(response), max_len)]

        for chunk in chunks:
            await message.answer(chunk, parse_mode=ParseMode.HTML)

    except Exception as e:
        logging.error(f"Error in /list command: {e}")
        await message.answer("An error occurred while fetching the list.")