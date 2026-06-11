"""
/stats admin command — real-time analytics dashboard for administrators.

Shows:
  - User counts (total, approved, pending, premium, blocked)
  - Folder stats (total folders, total files, most downloaded)
  - Download activity (downloads today, this week, all-time)
  - Payment summary (active premium revenue breakdown)
"""
import logging
from datetime import datetime, timedelta
from aiogram import types
from aiogram import Router
from aiogram.enums import ParseMode
from aiogram import Router
from middlewares.authorization import is_private_chat
from config import ADMIN_IDS
from utils.database import db_fetchone, db_fetchall
from utils.helpers import esc

router = Router()

log = logging.getLogger(__name__)


async def stats(message: types.Message):
    """/stats — admin analytics dashboard."""
    if not is_private_chat(message):
        return
    if str(message.from_user.id) not in ADMIN_IDS:
        await message.reply(
            "🔒 <b>Admin only</b>\n\nThis command is restricted to administrators.",
            parse_mode=ParseMode.HTML
        )
        return

    try:
        # ── User stats ────────────────────────────────────────────────────
        total_users    = db_fetchone("SELECT COUNT(*) FROM users")[0]
        approved_users = db_fetchone("SELECT COUNT(*) FROM users WHERE status = 'approved'")[0]
        pending_users  = db_fetchone("SELECT COUNT(*) FROM users WHERE status = 'pending'")[0]
        rejected_users = db_fetchone("SELECT COUNT(*) FROM users WHERE status = 'rejected'")[0]
        premium_users  = db_fetchone("SELECT COUNT(*) FROM users WHERE premium = TRUE")[0]

        # New users today and this week
        today     = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        week_ago  = today - timedelta(days=7)
        new_today = db_fetchone(
            "SELECT COUNT(*) FROM users WHERE created_at >= %s", (today,)
        )
        new_today = new_today[0] if new_today else "N/A"
        new_week  = db_fetchone(
            "SELECT COUNT(*) FROM users WHERE created_at >= %s", (week_ago,)
        )
        new_week = new_week[0] if new_week else "N/A"

        # ── Folder & file stats ───────────────────────────────────────────
        total_folders      = db_fetchone("SELECT COUNT(*) FROM folders")[0]
        premium_folders    = db_fetchone("SELECT COUNT(*) FROM folders WHERE premium = TRUE")[0]
        paid_folders       = db_fetchone("SELECT COUNT(*) FROM folders WHERE admin_approval = TRUE")[0]
        total_files        = db_fetchone("SELECT COUNT(*) FROM files")[0]
        total_dl_count     = db_fetchone("SELECT COALESCE(SUM(download_count), 0) FROM folders")[0]

        # Top 5 most downloaded folders
        top_folders = db_fetchall(
            "SELECT name, download_count FROM folders ORDER BY download_count DESC LIMIT 5"
        )

        # ── Premium expiry upcoming ───────────────────────────────────────
        expiring_soon = db_fetchall(
            """
            SELECT first_name, username, premium_expiration
            FROM users
            WHERE premium = TRUE
              AND premium_expiration IS NOT NULL
              AND premium_expiration <= NOW() + INTERVAL '7 days'
              AND premium_expiration > NOW()
            ORDER BY premium_expiration ASC
            LIMIT 5
            """
        )

        # ── Compose the dashboard ─────────────────────────────────────────
        lines = [
            "📊 <b>Bot Analytics Dashboard</b>",
            "══════════════════",
            "",
            "👥 <b>Users</b>",
            f"  Total:      <b>{total_users}</b>",
            f"  Approved:   <b>{approved_users}</b>",
            f"  Pending:    <b>{pending_users}</b>",
            f"  Rejected:   {rejected_users}",
            f"  Premium:    <b>{premium_users}</b>",
            f"  New today:  <b>{new_today}</b>",
            f"  This week:  <b>{new_week}</b>",
            "",
            "📂 <b>Content</b>",
            f"  Folders:    <b>{total_folders}</b>",
            f"    ⭐ {premium_folders} premium · 💰 {paid_folders} paid",
            f"  Files:      <b>{total_files}</b>",
            f"  Downloads:  <b>{total_dl_count}</b>  total",
        ]

        if top_folders:
            lines.append("\n🏆 <b>Top Downloads</b>")
            medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
            for i, (name, count) in enumerate(top_folders):
                medal = medals[i] if i < len(medals) else f"{i+1}."
                lines.append(f"  {medal} <code>{esc(name[:30])}</code> — {count} downloads")

        if expiring_soon:
            lines.append("\n⚠️ <b>Premium Expiring Soon (7 days)</b>")
            for fname, uname, exp in expiring_soon:
                name_str  = esc(fname or "Unknown")
                uname_str = f"@{esc(uname)}" if uname else "no username"
                exp_str   = exp.strftime('%d %b') if exp else "?"
                lines.append(f"  • {name_str} ({uname_str}) — expires {exp_str}")

        lines += [
            "",
            "══════════════════",
            f"<i>Generated: {datetime.now().strftime('%d %b %Y at %H:%M')}</i>",
        ]

        # Chunk to avoid 4096 char limit
        response = '\n'.join(lines)
        for chunk in [response[i:i+4096] for i in range(0, len(response), 4096)]:
            await message.answer(chunk, parse_mode=ParseMode.HTML)

    except Exception as e:
        log.error(f"Error in /stats command: {e}")
        await message.answer(
            "⚠️ <b>Error generating stats.</b>\n\nCheck bot logs for details.",
            parse_mode=ParseMode.HTML
        )
