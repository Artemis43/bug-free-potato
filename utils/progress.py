"""
Download progress tracker — shared in-memory state for active downloads.

Each running download registers itself here so:
  1. The user can see live per-file progress (edited message)
  2. A "Cancel" button works across callback queries
  3. Duplicate downloads to the same chat are prevented

Structure:
    _active_downloads: {
        chat_id: {
            "cancel":       bool,           # set True to abort the loop
            "total":        int,            # total files in folder
            "sent":         int,            # files successfully sent so far
            "folder_name":  str,
            "msg_id":       int | None,     # progress message Telegram ID
        }
    }
"""
from __future__ import annotations
import asyncio
from typing import Optional

# { chat_id: download_state_dict }
_active_downloads: dict[int, dict] = {}


def start_download(chat_id: int, folder_name: str, total: int) -> None:
    """Register a new download session for this chat."""
    _active_downloads[chat_id] = {
        "cancel":      False,
        "total":       total,
        "sent":        0,
        "folder_name": folder_name,
        "msg_id":      None,
    }


def is_downloading(chat_id: int) -> bool:
    """Return True if there is already an active download for this chat."""
    return chat_id in _active_downloads


def get_state(chat_id: int) -> Optional[dict]:
    return _active_downloads.get(chat_id)


def set_progress_msg_id(chat_id: int, msg_id: int) -> None:
    if chat_id in _active_downloads:
        _active_downloads[chat_id]["msg_id"] = msg_id


def increment_sent(chat_id: int) -> None:
    if chat_id in _active_downloads:
        _active_downloads[chat_id]["sent"] += 1


def request_cancel(chat_id: int) -> None:
    """Signal that the download for this chat should be cancelled."""
    if chat_id in _active_downloads:
        _active_downloads[chat_id]["cancel"] = True


def is_cancelled(chat_id: int) -> bool:
    state = _active_downloads.get(chat_id)
    return bool(state and state.get("cancel"))


def finish_download(chat_id: int) -> None:
    """Clean up after a download completes or is cancelled."""
    _active_downloads.pop(chat_id, None)


def build_progress_text(chat_id: int, file_interval: int) -> str:
    """
    Build a live progress string like:
        📥 Downloading: 12 / 50 files (24%)
        ████████░░░░░░░░░░░░░░░░░░░░  24%
        📁 Anatomy Notes  ·  ⏱ ~38 min remaining
    """
    state = _active_downloads.get(chat_id)
    if not state:
        return "📥 Preparing download…"

    sent  = state["sent"]
    total = state["total"]
    name  = state["folder_name"]

    pct = int(sent / total * 100) if total else 0

    # Unicode block progress bar (20 chars wide)
    filled = int(pct / 100 * 20)
    bar    = "█" * filled + "░" * (20 - filled)

    remaining_files = total - sent
    eta_secs        = remaining_files * file_interval
    if eta_secs >= 3600:
        eta_str = f"{eta_secs // 3600}h {(eta_secs % 3600) // 60}m"
    elif eta_secs >= 60:
        eta_str = f"{eta_secs // 60}m {eta_secs % 60}s"
    else:
        eta_str = f"{eta_secs}s"

    return (
        f"📥 <b>Downloading:</b> {sent} / {total} files ({pct}%)\n"
        f"<code>{bar}</code>  {pct}%\n"
        f"📁 <b>{name}</b>  ·  ⏱ ~{eta_str} remaining"
    )
