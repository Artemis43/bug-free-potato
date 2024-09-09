import logging
import requests
import threading
import time
from config import WEBHOOK_URL, DB_FILE_PATH
from utils.database import conn

async def on_startup(dispatcher):
    from main import bot
    await bot.set_webhook(WEBHOOK_URL)

     # Start the periodic upload in a background thread
    start_periodic_upload(api_key='ir9dho0AwDNuDN-TTQIiy0qYJIsE3BP35hqYBVgax4OJUeplyakzzg', db_name='file_management', db_path= DB_FILE_PATH)

async def on_shutdown(dispatcher):
    from main import bot
    logging.warning('Shutting down..')
    await bot.delete_webhook()
    conn.close()
    logging.warning('Bye!')

def upload_database(api_key, db_name, file_path):
    url = 'https://api.dbhub.io/v1/database/upload'
    with open(file_path, 'rb') as file:
        response = requests.post(
            url,
            headers={'Authorization': f'Bearer {api_key}'},
            files={'file': (db_name, file)}
        )
    if response.status_code == 200:
        print("Database uploaded successfully.")
    else:
        print(f"Failed to upload database: {response.text}")

def start_periodic_upload(api_key, db_name, db_path, interval=300):
    def upload_task():
        while True:
            upload_database(api_key, db_name, db_path)
            time.sleep(interval)

    # Start the thread
    thread = threading.Thread(target=upload_task, daemon=True)
    thread.start()