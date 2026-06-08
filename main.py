import logging
from aiogram import types, Bot, Dispatcher
from aiogram.contrib.middlewares.logging import LoggingMiddleware

from config import API_TOKEN, ADMIN_IDS, LOG_LEVEL
from keep_alive import keep_alive

# ── Keep-alive web server ──────────────────────────────────────────────────
keep_alive()

# ── Logging ────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# ── Bot & dispatcher ───────────────────────────────────────────────────────
bot = Bot(token=API_TOKEN)
dp  = Dispatcher(bot)
dp.middleware.setup(LoggingMiddleware())


# ── Import handlers AFTER bot/dp are created ──────────────────────────────
from handlers import (
    start, broadcast, caption, document,
    getlist, folder, download, setpremium,
    stop, about_help, sync, status, admin_tools, commands_ref, payment,
    payment_stars,
)

# ── Command handlers ───────────────────────────────────────────────────────
dp.register_message_handler(start.handle_start,                 commands=['start'])
dp.register_message_handler(about_help.about,                   commands=['about'])
dp.register_message_handler(about_help.help,                    commands=['help'])
dp.register_message_handler(status.status,                      commands=['status'])
dp.register_message_handler(commands_ref.commands_reference,    commands=['commands'])
dp.register_message_handler(broadcast.broadcast_message,        commands=['broadcast'])
dp.register_message_handler(caption.set_caption,                commands=['caption'])
dp.register_message_handler(getlist.list_all,                   commands=['list'])
dp.register_message_handler(admin_tools.pending_users,          commands=['pending'])
dp.register_message_handler(admin_tools.user_info,              commands=['userinfo'])
dp.register_message_handler(admin_tools.reset_cooldown,         commands=['resetcooldown'])
dp.register_message_handler(admin_tools.set_upload_folder,      commands=['setuploadfolder'])
dp.register_message_handler(folder.rename_folder,               commands=['renamefolder'])
dp.register_message_handler(folder.create_folder,               commands=['newfolder'])
dp.register_message_handler(folder.delete_folder,               commands=['deletefolder'])
dp.register_message_handler(download.get_all_files,             commands=['download'])
dp.register_message_handler(setpremium.set_premium_status,      commands=['setfolder'])
dp.register_message_handler(setpremium.set_premium,             commands=['setuser'])
dp.register_message_handler(download.handle_approval,           commands=['approve'])
dp.register_message_handler(download.handle_rejection,          commands=['reject'])
dp.register_message_handler(payment.cmd_pay,                    commands=['pay'])
dp.register_message_handler(payment.cmd_payfolder,              commands=['payfolder'])
dp.register_message_handler(payment.cmd_payconfig,              commands=['payconfig'])
dp.register_message_handler(stop.stop,                          commands=['stop'])
dp.register_message_handler(sync.sync_database_command,         commands=['forcedsyncdb'])

# Media handlers (admin upload)
dp.register_message_handler(document.handle_document, content_types=[types.ContentType.DOCUMENT])
dp.register_message_handler(document.handle_video,    content_types=[types.ContentType.VIDEO])
dp.register_message_handler(document.handle_photo,    content_types=[types.ContentType.PHOTO])

# Dynamic /approve_<id> and /reject_<id> — work from any chat (private or group)
dp.register_message_handler(
    start.approve_user,
    lambda msg: bool(msg.text) and msg.text.startswith('/approve_') and str(msg.from_user.id) in ADMIN_IDS
)
dp.register_message_handler(
    start.reject_user,
    lambda msg: bool(msg.text) and msg.text.startswith('/reject_') and str(msg.from_user.id) in ADMIN_IDS
)

# Telegram Stars payment handlers
dp.register_pre_checkout_query_handler(payment_stars.pre_checkout_handler)
dp.register_message_handler(
    payment_stars.successful_payment_handler,
    content_types=[types.ContentType.SUCCESSFUL_PAYMENT],
)

# ── Callback query handler ─────────────────────────────────────────────────
# ONE unified handler with NO filter — aiogram v2 lambdas as filter
# are unreliable; a single no-filter handler always matches all callbacks.
# start.process_callback dispatches internally by data prefix.
dp.register_callback_query_handler(start.process_callback)

# Unknown slash-commands (must be LAST to avoid swallowing known commands)
dp.register_message_handler(
    about_help.handle_invalid_command,
    lambda msg: bool(msg.text) and msg.text.startswith('/')
)

# ── Startup / shutdown hooks ───────────────────────────────────────────────
from utils.webhook import on_startup, on_shutdown

# ── Entry-point ──────────────────�