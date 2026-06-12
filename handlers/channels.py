"""
handlers/channels.py — admin commands to manage the storage-channel set.

Every upload is mirrored to all *active* storage channels (see
handlers/document.py). These commands let an admin grow/shrink that set at
runtime — all persisted in the same Supabase DB (utils/storage.py):

    /channels                       list channels + status + replica counts
    /addchannel <chat_id> [title]   verify the bot is admin there, then add
    /togglechannel_<id>             enable/disable a channel (tap from the list)
    /removechannel_<id>             remove a channel (drops its replicas)
"""
import logging
from aiogram import types
from aiogram import Router
from aiogram.enums import ParseMode
from middlewares.authorization import is_private_chat
from config import ADMIN_IDS
from utils.bot_ref import get_bot
from utils.bots import get_current_bot_pk, get_bot_channel_ids, pair_bot_channel
from utils.helpers import esc
from utils.storage import (
    list_storage_channels,
    add_storage_channel,
    remove_storage_channel,
    set_channel_active,
    get_channel,
    channel_replica_count,
)

router = Router()
log = logging.getLogger(__name__)


def _is_admin(message: types.Message) -> bool:
    return str(message.from_user.id) in ADMIN_IDS


def _parse_trailing_id(text: str):
    """'/removechannel_12' → 12, or None if it can't be parsed."""
    try:
        return int((text or "").rsplit('_', 1)[1])
    except (IndexError, ValueError):
        return None


async def list_channels(message: types.Message):
    """/channels — list configured storage channels with status & replica counts."""
    if not is_private_chat(message):
        return
    if not _is_admin(message):
        await message.reply("You are not authorized.")
        return

    channels = list_storage_channels()
    if not channels:
        await message.reply(
            "🗄 <b>Storage Channels</b>\n\nNone configured yet.\n"
            "Add one with <code>/addchannel &lt;chat_id&gt; [title]</code>.",
            parse_mode=ParseMode.HTML,
        )
        return

    # Which of these channels can THIS bot serve downloads from?
    bot_pk = get_current_bot_pk()
    served = set(get_bot_channel_ids(bot_pk)) if bot_pk else set()

    active_count = sum(1 for *_rest, active in channels if active)
    lines = [f"🗄 <b>Storage Channels</b> — {active_count}/{len(channels)} active\n"]
    for ch_id, chat_id, title, active in channels:
        status     = "🟢 active" if active else "🔴 disabled"
        serve_mark = " · 📥 this bot serves" if ch_id in served else ""
        count      = channel_replica_count(ch_id)
        name       = esc(title or "—")
        lines.append(
            f"<b>#{ch_id}</b> {status}{serve_mark} — {count} file(s)\n"
            f"  {name}  <code>{esc(str(chat_id))}</code>\n"
            f"  /togglechannel_{ch_id}   /removechannel_{ch_id}"
        )
    lines.append(
        "\n📥 = this bot can deliver files from that channel.\n"
        "Add: <code>/addchannel &lt;chat_id&gt; [title]</code>"
    )
    await message.reply("\n\n".join(lines), parse_mode=ParseMode.HTML)


async def add_channel(message: types.Message):
    """/addchannel <chat_id> [title] — verify the bot is an admin, then save."""
    if not is_private_chat(message):
        return
    if not _is_admin(message):
        await message.reply("You are not authorized.")
        return

    parts = (message.text or "").split(None, 2)
    if len(parts) < 2:
        await message.reply(
            "Usage: <code>/addchannel &lt;chat_id&gt; [title]</code>\n\n"
            "Tip: add the bot to the channel as an <b>admin</b> first, then pass "
            "the channel id (e.g. <code>-1001234567890</code>) or @username.",
            parse_mode=ParseMode.HTML,
        )
        return

    raw_chat   = parts[1]
    title_arg  = parts[2].strip() if len(parts) > 2 else None
    bot        = get_bot()

    # 1) Resolve the channel (confirms the bot can see it).
    try:
        chat = await bot.get_chat(raw_chat)
    except Exception as e:
        await message.reply(
            f"❌ Couldn't access <code>{esc(raw_chat)}</code>: {esc(str(e))}\n\n"
            "Add the bot to that channel first.",
            parse_mode=ParseMode.HTML,
        )
        return

    # 2) Confirm the bot is an admin (needed to post copies and delete them).
    try:
        member = await bot.get_chat_member(chat.id, bot.id)
        if member.status not in ("administrator", "creator"):
            await message.reply(
                "⚠️ The bot is in the channel but is <b>not an admin</b>.\n"
                "Make it an admin (with permission to post messages), then retry.",
                parse_mode=ParseMode.HTML,
            )
            return
    except Exception as e:
        await message.reply(f"❌ Couldn't verify admin status: {esc(str(e))}", parse_mode=ParseMode.HTML)
        return

    title  = title_arg or chat.title or None
    new_id = add_storage_channel(chat.id, title)

    # This bot just confirmed it's an admin here, so pair it immediately — it
    # can serve downloads from this channel without waiting for a restart.
    # Other bot processes pick it up on their next startup channel sync.
    bot_pk = get_current_bot_pk()
    if bot_pk and new_id:
        pair_bot_channel(bot_pk, new_id)

    await message.reply(
        f"✅ <b>Storage channel added</b> (#{new_id})\n"
        f"📡 {esc(title or '—')}  <code>{esc(str(chat.id))}</code>\n\n"
        "New uploads will be mirrored here, and this bot can already serve "
        "downloads from it. Other bots start serving it after their next "
        "restart. Use /channels to review.",
        parse_mode=ParseMode.HTML,
    )


async def toggle_channel(message: types.Message):
    """/togglechannel_<id> — enable/disable a channel without deleting it."""
    if not is_private_chat(message):
        return
    if not _is_admin(message):
        await message.reply("You are not authorized.")
        return

    channel_id = _parse_trailing_id(message.text)
    row = get_channel(channel_id) if channel_id is not None else None
    if not row:
        await message.reply("Channel not found. Use /channels to see the list.")
        return

    _id, chat_id, title, active = row
    set_channel_active(channel_id, not active)
    new_state = "disabled 🔴" if active else "active 🟢"
    note = (
        "\nUploads will skip it; existing files there are untouched."
        if active else
        "\nNew uploads will be mirrored here again."
    )
    await message.reply(
        f"✅ Channel <b>#{channel_id}</b> ({esc(title or chat_id)}) is now {new_state}.{note}",
        parse_mode=ParseMode.HTML,
    )


async def remove_channel(message: types.Message):
    """/removechannel_<id> — remove a channel (drops its replica records)."""
    if not is_private_chat(message):
        return
    if not _is_admin(message):
        await message.reply("You are not authorized.")
        return

    channel_id = _parse_trailing_id(message.text)
    row = get_channel(channel_id) if channel_id is not None else None
    if not row:
        await message.reply("Channel not found. Use /channels to see the list.")
        return

    _id, chat_id, title, _active = row
    count = channel_replica_count(channel_id)
    remove_storage_channel(channel_id)   # CASCADE removes this channel's file_locations
    await message.reply(
        f"🗑 Removed channel <b>#{channel_id}</b> ({esc(title or chat_id)}).\n"
        f"{count} replica record(s) dropped. Files themselves and other channels "
        "are unaffected; messages already in that channel are not deleted.",
        parse_mode=ParseMode.HTML,
    )
