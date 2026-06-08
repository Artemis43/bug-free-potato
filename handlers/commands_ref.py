from aiogram import types
from aiogram.types import ParseMode
from middlewares.authorization import is_private_chat
from config import ADMIN_IDS


# ─────────────────────────────────────────────────────────────────────────────
# /commands — detailed reference for all bot commands
# ─────────────────────────────────────────────────────────────────────────────

_USER_COMMANDS = """
<b>📖 Command Reference — Medical Content Bot</b>
━━━━━━━━━━━━━━━━━━━━━━━━

<b>🚀 Getting Started</b>

▸ <code>/start</code>
  Opens the main folder menu with clickable download buttons.
  <i>Run this any time to refresh the folder list.</i>

▸ <code>/help</code>
  Shows how to use the bot — download methods, cooldowns, tips.

▸ <code>/about</code>
  What this bot is, who it's for, and how to contact support.

━━━━━━━━━━━━━━━━━━━━━━━━

<b>⬇️ Downloading</b>

▸ <code>/start</code>  → tap a folder button
  The easiest way — no typing needed.

▸ <code>/download &lt;folder name&gt;</code>
  Downloads all files in a named folder.
  Example:
  <code>/download Anatomy</code>
  <code>/download First Year Surgery Notes</code>
  ⚠️ Folder name must match exactly (case-sensitive).

━━━━━━━━━━━━━━━━━━━━━━━━

<b>📊 Your Account</b>

▸ <code>/status</code>
  Shows your account details in one place:
  • Approval status (Approved / Pending / Rejected)
  • Premium tier + expiry date + days remaining
  • Last download timestamp
  • Live cooldown countdown (e.g. "3m 12s remaining")

━━━━━━━━━━━━━━━━━━━━━━━━

<b>ℹ️ Cooldown Rules</b>

  🔓 Free user:
    • 60 seconds between each file
    • 7 minutes between downloads

  ⭐ Premium user:
    • 5 seconds between each file
    • 2 minutes between downloads

  💰 Paid folder (one-time):
    • Billed at Premium speed
    • 1 download per approval
"""

_ADMIN_COMMANDS = """
━━━━━━━━━━━━━━━━━━━━━━━━
<b>🛠️ Admin Commands</b>
━━━━━━━━━━━━━━━━━━━━━━━━

<b>👥 User Management</b>

▸ <code>/pending</code>
  Lists all users waiting for approval with name, username,
  user ID, and when they requested access.

▸ <code>/approve_&lt;user_id&gt;</code>
  Approve a user via DM command (from /pending list).
  Example:  <code>/approve_7093051689</code>

▸ <code>/reject_&lt;user_id&gt;</code>
  Reject a user via DM command.
  Example:  <code>/reject_7093051689</code>

  💡 <i>If ADMIN_GROUP_ID is set, use the ✅/❌ inline buttons
  in the group instead — no typing needed.</i>

▸ <code>/userinfo &lt;user_id&gt;</code>
  Full account details for any user:
  name, username, access status, premium expiry, last download,
  number of paid folders downloaded.
  Example:  <code>/userinfo 7093051689</code>

▸ <code>/resetcooldown &lt;user_id&gt;</code>
  Clears a user's download cooldown so they can download immediately.
  Useful when a user had a network error mid-download.
  Example:  <code>/resetcooldown 7093051689</code>

━━━━━━━━━━━━━━━━━━━━━━━━

<b>⭐ Premium Management</b>

▸ <code>/setuser &lt;user_id&gt; on</code>
  Grant premium for the default 10 days.
  Example:  <code>/setuser 7093051689 on</code>

▸ <code>/setuser &lt;user_id&gt; days:&lt;N&gt;</code>
  Grant premium for a custom number of days.
  Examples:
  <code>/setuser 7093051689 days:30</code>   ← 1 month
  <code>/setuser 7093051689 days:7</code>    ← 1 week
  <code>/setuser 7093051689 days:365</code>  ← 1 year

▸ <code>/setuser &lt;user_id&gt; off</code>
  Immediately revoke a user's premium.
  Example:  <code>/setuser 7093051689 off</code>

━━━━━━━━━━━━━━━━━━━━━━━━

<b>📁 Folder Management</b>

▸ <code>/newfolder &lt;name&gt; [PREMIUM] [PAID]</code>
  Create a folder and set it as your active upload target.
  Multi-word names are supported — flags must come LAST.
  Flags (optional):
    <code>PREMIUM</code> — only premium users can download
    <code>PAID</code>    — requires admin approval per user (one-time)
  Examples:
  <code>/newfolder Anatomy</code>                             ← single word, free
  <code>/newfolder Human Anatomy</code>                      ← multi-word, free
  <code>/newfolder Surgery Notes PREMIUM</code>              ← multi-word, premium
  <code>/newfolder First Year Notes PREMIUM PAID</code>      ← multi-word, paid+premium
  <code>/newfolder Exclusive Slides PAID</code>              ← paid-only

▸ <code>/setuploadfolder &lt;name&gt;</code>
  Switch your active upload target to an existing folder
  without creating a new one. Send files after this to add them.
  Example:  <code>/setuploadfolder Anatomy</code>

▸ <code>/renamefolder &lt;current&gt;,&lt;new&gt;</code>
  Rename a folder (comma-separated, no spaces around comma).
  Examples:
  <code>/renamefolder Anatomy,Human Anatomy</code>
  <code>/renamefolder Old Notes,2025 Notes</code>

▸ <code>/deletefolder &lt;name&gt;</code>
  Delete a folder and all its files from the archive channel.
  Shows a confirmation dialog (✅ Yes / ❌ Cancel) before proceeding.
  Example:  <code>/deletefolder Old Anatomy Notes</code>

▸ <code>/setfolder &lt;folder_id&gt; &lt;0 or 1&gt;</code>
  Toggle a folder's premium flag (get the ID from /list).
  <code>/setfolder 5 1</code>  ← make folder #5 premium-only
  <code>/setfolder 5 0</code>  ← make folder #5 free

▸ <code>/list</code>
  Full inventory: all folders (with IDs and download counts),
  premium folders, paid folders, total users, pending users,
  and all premium users with expiry dates.

━━━━━━━━━━━━━━━━━━━━━━━━

<b>📢 Broadcasting</b>

▸ <code>/broadcast &lt;text&gt;</code>
  Send a plain-text message to all approved users.
  Example:  <code>/broadcast New content added — check /start!</code>

▸ <code>/broadcast html &lt;html text&gt;</code>
  Send an HTML-formatted broadcast.
  Example:
  <code>/broadcast html &lt;b&gt;New folder!&lt;/b&gt; Check /start 🎉</code>

▸ <code>/broadcast md &lt;markdown text&gt;</code>
  Send a Markdown-formatted broadcast.
  Example:  <code>/broadcast md *Important* update!</code>

━━━━━━━━━━━━━━━━━━━━━━━━

<b>💰 Paid Folder Approvals</b>

▸ <code>/approve &lt;user_id&gt; &lt;folder_id&gt;</code>
  Grant a user one download of a paid folder.
  Example:  <code>/approve 7093051689 3</code>

▸ <code>/reject &lt;user_id&gt; &lt;folder_id&gt;</code>
  Decline a user's paid-folder request.
  Example:  <code>/reject 7093051689 3</code>

━━━━━━━━━━━━━━━━━━━━━━━━

<b>🔧 Maintenance</b>

▸ <code>/caption &lt;type&gt; &lt;text&gt;</code>
  Set the caption appended to uploaded files.
  Types: <code>custom</code> | <code>append</code>
  Examples:
  <code>/caption custom @Medical_Contentbot</code>
  <code>/caption append — Do not redistribute</code>

▸ <code>/forcedsyncdb</code>
  Manually trigger a DB sync (re-indexes files from the archive channel).

▸ <code>/stop</code>
  Gracefully shut down the bot.

▸ <code>/commands</code>
  Shows this reference.
"""


async def commands_reference(message: types.Message):
    """/commands — show a detailed reference of all available commands."""
    if not is_private_chat(message):
        return

    is_admin = str(message.from_user.id) in ADMIN_IDS

    # Send user section first
    await message.reply(_USER_COMMANDS.strip(), parse_mode=ParseMode.HTML)

    # Send admin section only to admins
    if is_admin:
        await message.answer(_ADMIN_COMMANDS.strip(), parse_mode=ParseMode.HTML)
