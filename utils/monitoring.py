"""
utils/monitoring.py — lightweight, zero-dependency bot monitoring.

Tracks:
  - Uptime since last restart
  - Per-handler invocation counts and error counts
  - Active concurrent downloads
  - Memory usage (RSS) and CPU time

Exposed via /health/detail endpoint in keep_alive.py.
"""
from __future__ import annotations

import os
import time
import logging
from collections import defaultdict
from datetime import datetime, timezone
from threading import Lock
from typing import Dict, Any

log = logging.getLogger(__name__)

# ── State ──────────────────────────────────────────────────────────────────

_start_time: float = time.monotonic()
_start_wall: datetime = datetime.now(tz=timezone.utc)

_handler_counts: Dict[str, int] = defaultdict(int)
_handler_errors: Dict[str, int] = defaultdict(int)
_last_update_ts: float = 0.0          # monotonic timestamp of last Telegram update

_lock = Lock()


# ── Public counters ────────────────────────────────────────────────────────

def record_handler_call(name: str) -> None:
    """Increment the invocation counter for a named handler."""
    with _lock:
        _handler_counts[name] += 1


def record_handler_error(name: str) -> None:
    """Increment the error counter for a named handler."""
    with _lock:
        _handler_errors[name] += 1


def record_update() -> None:
    """Mark the time of the most recently processed Telegram update."""
    global _last_update_ts
    _last_update_ts = time.monotonic()


# ── Metrics snapshot ────────────────────────────────────────────────────────

def uptime_seconds() -> float:
    return time.monotonic() - _start_time


def _fmt_uptime(secs: float) -> str:
    d, rem  = divmod(int(secs), 86400)
    h, rem  = divmod(rem, 3600)
    m, s    = divmod(rem, 60)
    parts = []
    if d:  parts.append(f"{d}d")
    if h:  parts.append(f"{h}h")
    if m:  parts.append(f"{m}m")
    parts.append(f"{s}s")
    return " ".join(parts)


def _memory_mb() -> float | None:
    """Best-effort RSS memory (MB) — works on Linux/Windows."""
    try:
        import psutil
        return psutil.Process(os.getpid()).memory_info().rss / 1_048_576
    except ImportError:
        pass
    # Fallback: /proc/self/status (Linux only)
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024
    except OSError:
        pass
    return None


def get_metrics() -> Dict[str, Any]:
    """Return a dict of all current metrics (safe to JSON-serialise)."""
    from utils.progress import _active_downloads  # avoid circular import

    up = uptime_seconds()
    mem = _memory_mb()

    last_update_ago: float | None = None
    if _last_update_ts:
        last_update_ago = time.monotonic() - _last_update_ts

    with _lock:
        handlers = {
            name: {"calls": _handler_counts[name], "errors": _handler_errors[name]}
            for name in sorted(set(_handler_counts) | set(_handler_errors))
        }

    return {
        "status":          "ok",
        "started_at":      _start_wall.isoformat(),
        "uptime_seconds":  round(up, 1),
        "uptime_human":    _fmt_uptime(up),
        "active_downloads": len(_active_downloads),
        "last_update_ago_s": round(last_update_ago, 1) if last_update_ago else None,
        "memory_mb":       round(mem, 1) if mem else None,
        "handlers":        handlers,
    }
