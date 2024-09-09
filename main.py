import logging
from aiogram import types
from aiogram import Bot, Dispatcher
from aiogram.contrib.middlewares.logging import LoggingMiddleware
from config import API_TOKEN, ADMIN_IDS
from keep_alive import keep_alive

# To keep the bot running
keep_alive()

# Configure logging
logging.basicConfig(level=logging.INFO)

# Initialize bot and dispatcher
bot = Bot(token=API_TOKEN)
dp = Dispatcher(bot)
dp.middleware.setup(LoggingMiddleware())

# New db upload initiated only during /restore
# awaiting_new_db_upload = False

# Global dictionary to track the current upload folder for each admin
# So that all admins can upload files simultaneously
current_upload_folders = {}

from handlers import start, backup, broadcast, caption, document, getlist, folder, download, setpremium, stop, about_help, sync

# Register handlers
dp.register_message_handler(start.handle_start, commands=['start'])
dp.register_message_handler(about_help.about, commands=['about'])
dp.register_message_handler(about_help.help, commands=['help'])
dp.register_message_handler(backup.send_backup, commands=['backup'])
dp.register_message_handler(backup.new_db, commands=['restore'])
dp.register_message_handler(broadcast.broadcast_message, commands=['broadcast'])
dp.register_message_handler(caption.set_caption, commands=['caption'])
dp.register_message_handler(document.handle_document, content_types=[types.ContentType.DOCUMENT])
dp.register_message_handler(document.handle_video, content_types=[types.ContentType.VIDEO])
dp.register_message_handler(document.handle_photo, content_types=[types.ContentType.PHOTO])
dp.register_message_handler(getlist.list_all, commands=['list'])
dp.register_message_handler(folder.rename_folder, commands=['renamefolder'])
dp.register_message_handler(folder.create_folder, commands=['newfolder'])
dp.register_message_handler(folder.delete_folder, commands=['deletefolder'])
dp.register_message_handler(download.get_all_files, commands=['download'])
dp.register_message_handler(setpremium.set_premium_status, commands=['setfolder'])
dp.register_message_handler(setpremium.set_premium, commands=['setuser'])
dp.register_message_handler(download.handle_approval, commands=['approve'])
dp.register_message_handler(download.handle_rejection, commands=['reject'])
dp.register_message_handler(stop.stop, commands=['stop'])
dp.register_message_handler(sync.sync_database_command, commands=['syncdb'])
dp.register_message_handler(
    start.approve_user,
    lambda message: message.text.startswith('/approve_') and str(message.from_user.id) in ADMIN_IDS
)
dp.register_message_handler(
    start.reject_user,
    lambda message: message.text.startswith('/reject_') and str(message.from_user.id) in ADMIN_IDS
)
dp.register_callback_query_handler(start.process_callback, lambda c: c.data)
dp.register_message_handler(about_help.handle_invalid_command, lambda message: not message.text.startswith('/'))

from utils.webhook import on_startup, on_shutdown

# Start the bot
if __name__ == '__main__':
    from aiogram import executor
    executor.start_polling(dp, on_startup=on_startup, on_shutdown=on_shutdown)