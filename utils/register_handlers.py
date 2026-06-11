"""
utils/register_handlers.py — aiogram v3 handler registration.

In v3, handlers are registered via @router.message(Command("cmd")) decorators
or via router.message.register(fn, Command("cmd")).

This module centralises ALL handler registrations so main.py stays clean.
Each handler function is imported from its module and bound to the shared
router for that module.

HOW THIS WORKS:
  1. Each handler module has a `router = Router()` at module level.
  2. We call router.message.register(handler_fn, Filter(...)) here.
  3. main.py calls register_all_handlers() once, then includes all routers.
"""
from aiogram import Router, F
from aiogram.filters import Command, CommandStart
from aiogram.types import ContentType

# ── Import handler modules ─────────────────────────────────────────────────
import handlers.start        as _start
import handlers.about_help   as _help
import handlers.status       as _status
import handlers.download     as _download
import handlers.getlist      as _getlist
import handlers.folder       as _folder
import handlers.document     as _document
import handlers.setpremium   as _setpremium
import handlers.admin_tools  as _admin_tools
import handlers.commands_ref as _commands_ref
import handlers.broadcast    as _broadcast
import handlers.caption      as _caption
import handlers.payment      as _payment
import handlers.payment_stars as _payment_stars
import handlers.stats        as _stats
import handlers.stop         as _stop
import handlers.sync         as _sync
from config import ADMIN_IDS


def register_all_handlers() -> None:
    """
    Bind every handler function to its router using v3 Command / F filters.
    Called once from main.py before dp.include_router(module.router) loops.
    """

    # ── /start ────────────────────────────────────────────────────────────
    _start.router.message.register(_start.handle_start, CommandStart())

    # Approve/reject user (admin DM commands: /approve_<id>  /reject_<id>)
    _start.router.message.register(
        _start.approve_user,
        F.text.startswith("/approve_") & F.from_user.id.func(lambda uid: str(uid) in ADMIN_IDS)
    )
    _start.router.message.register(
        _start.reject_user,
        F.text.startswith("/reject_") & F.from_user.id.func(lambda uid: str(uid) in ADMIN_IDS)
    )

    # ALL callback queries route through process_callback
    _start.router.callback_query.register(_start.process_callback)

    # ── Help / About ──────────────────────────────────────────────────────
    _help.router.message.register(_help.help_command,    Command("help"))
    _help.router.message.register(_help.about_command,   Command("about"))

    # Unknown commands (MUST be registered last to avoid swallowing known ones)
    # We register it on _help.router but it will be included last in main.py
    _help.router.message.register(
        _help.handle_invalid_command,
        F.text.startswith("/")
    )

    # ── /status ───────────────────────────────────────────────────────────
    _status.router.message.register(_status.status, Command("status"))

    # ── /download ─────────────────────────────────────────────────────────
    _download.router.message.register(_download.get_all_files,     Command("download"))
    _download.router.message.register(_download.handle_approval,   Command("approve"))
    _download.router.message.register(_download.handle_rejection,  Command("reject"))

    # ── Folder management ─────────────────────────────────────────────────
    _folder.router.message.register(_folder.create_folder,  Command("newfolder"))
    _folder.router.message.register(_folder.rename_folder,  Command("renamefolder"))
    _folder.router.message.register(_folder.delete_folder,  Command("deletefolder"))

    # ── List ──────────────────────────────────────────────────────────────
    _getlist.router.message.register(_getlist.list_all, Command("list"))

    # ── Document / media upload ───────────────────────────────────────────
    _document.router.message.register(
        _document.handle_document, F.content_type == ContentType.DOCUMENT
    )
    _document.router.message.register(
        _document.handle_video,    F.content_type == ContentType.VIDEO
    )
    _document.router.message.register(
        _document.handle_photo,    F.content_type == ContentType.PHOTO
    )

    # ── Premium / folder flags ────────────────────────────────────────────
    _setpremium.router.message.register(_setpremium.set_premium_status, Command("setfolder"))
    _setpremium.router.message.register(_setpremium.set_premium,        Command("setuser"))

    # ── Admin tools ───────────────────────────────────────────────────────
    _admin_tools.router.message.register(_admin_tools.pending_users,    Command("pending"))
    _admin_tools.router.message.register(_admin_tools.user_info,        Command("userinfo"))
    _admin_tools.router.message.register(_admin_tools.reset_cooldown,   Command("resetcooldown"))
    _admin_tools.router.message.register(_admin_tools.set_upload_folder, Command("setuploadfolder"))

    # ── Stats ─────────────────────────────────────────────────────────────
    _stats.router.message.register(_stats.stats, Command("stats"))

    # ── Broadcast ─────────────────────────────────────────────────────────
    _broadcast.router.message.register(_broadcast.broadcast_message, Command("broadcast"))

    # ── Caption ───────────────────────────────────────────────────────────
    _caption.router.message.register(_caption.set_caption, Command("caption"))

    # ── Commands reference ────────────────────────────────────────────────
    _commands_ref.router.message.register(_commands_ref.commands_reference, Command("commands"))

    # ── Payment ───────────────────────────────────────────────────────────
    _payment.router.message.register(_payment.cmd_pay,       Command("pay"))
    _payment.router.message.register(_payment.cmd_payfolder, Command("payfolder"))
    _payment.router.message.register(_payment.cmd_payconfig, Command("payconfig"))

    # ── Telegram Stars ────────────────────────────────────────────────────
    _payment_stars.router.pre_checkout_query.register(
        _payment_stars.pre_checkout_handler
    )
    _payment_stars.router.message.register(
        _payment_stars.successful_payment_handler,
        F.content_type == ContentType.SUCCESSFUL_PAYMENT,
    )

    # ── Stop ──────────────────────────────────────────────────────────────
    _stop.router.message.register(_stop.stop, Command("stop"))

    # ── Sync ─────────────────────────────────────────────────────────────
    _sync.router.message.register(_sync.sync_database_command, Command("forcedsyncdb"))
