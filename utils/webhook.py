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
    start_periodic_upload(api_key='ir9dho0AwDNuDN-TTQIiy0qYJIsE3BP35hqYBVgax4OJUeplyakzzg', db_name= DB_FILE_PATH, db_path= DB_FILE_PATH)

async def on_shutdown(dispatcher):
    from main import bot
    logging.warning('Shutting down..')
    await bot.delete_webhook()
    conn.close()
    logging.warning('Bye!')

def delete_existing_database(api_key, db_name):
    url = 'https://api.dbhub.io/v1/delete'
    response = requests.post(
        url,
        data={'apikey': api_key, 'dbname': db_name}
    )
    
    status_code = response.status_code
    response_content = response.content.decode()
    
    print(f"Delete Status Code: {status_code}")
    print(f"Delete Response Content: {response_content}")
    
    if status_code == 200:
        print("Existing database deleted successfully.")
    elif status_code == 404:
        print("No existing database found to delete.")
    else:
        print(f"Failed to delete existing database: {response_content}")

def upload_database(api_key, db_name, file_path):
    url = 'https://api.dbhub.io/v1/upload'
    with open(file_path, 'rb') as file:
        response = requests.post(
            url,
            data={'apikey': api_key, 'dbname': db_name},
            files={'file': (db_name, file)}
        )
    
    status_code = response.status_code
    response_content = response.content.decode()
    
    print(f"Upload Status Code: {status_code}")
    print(f"Upload Response Content: {response_content}")
    
    if status_code == 201:
        print("Database upload initiated successfully.")
        # Additional handling or verification might be needed
    else:
        print(f"Failed to upload database: {response_content}")

def start_periodic_upload(api_key, db_name, db_path, interval=120):
    def upload_task():
        while True:
            # First, delete any existing database with the same name
            delete_existing_database(api_key, db_name)
            # Then, upload the new database
            upload_database(api_key, db_name, db_path)
            time.sleep(interval)

    thread = threading.Thread(target=upload_task, daemon=True)
    thread.start()