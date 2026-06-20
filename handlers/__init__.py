"""
handlers/__init__.py

Central registration hub for aiogram v3 Router-based handlers.
Each handler module exposes a `router` object. This file imports all modules
so main.py can include all routers with a single loop.

Handler modules are imported here to trigger their router.register() calls.
"""
from handlers import (
    start,
    broadcast,
    caption,
    document,
    getlist,
    folder,
    download,
    setpremium,
    stop,
    about_help,
    sync,
    status,
    admin_tools,
    commands_ref,
    payment,
    payment_stars,
    stats,
    channels,
    category,
    search,
    reply_router,
)

__all__ = [
    "start", "broadcast", "caption", "document", "getlist", "folder",
    "download", "setpremium", "stop", "about_help", "sync", "status",
    "admin_tools", "commands_ref", "payment", "payment_stars", "stats",
    "channels", "category", "search", "reply_router",
]
