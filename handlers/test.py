import requests

def download_database(api_key, db_owner, db_name, db_path):
    url = 'https://api.dbhub.io/v1/download'
    response = requests.post(
        url,
        data={'apikey': api_key, 'dbowner': db_owner, 'dbname': db_name}
    )
    
    status_code = response.status_code
    if status_code == 200:
        with open(db_path, 'wb') as file:
            file.write(response.content)
        print("Database downloaded and saved successfully.")
    else:
        print(f"Failed to download database: Status Code: {status_code}, Response: {response.content.decode()}")

import os

def delete_local_database(db_path):
    if os.path.exists(db_path):
        os.remove(db_path)
        print("Local database deleted successfully.")
    else:
        print("Local database not found.")

def sync_database(api_key, db_owner, db_name, db_path):
    # Delete the local database
    delete_local_database(db_path)
    
    # Download the new database from dbhub.io
    download_database(api_key, db_owner, db_name, db_path)

from aiogram.types import ParseMode
from aiogram import types

async def sync_database_command(message: types.Message):
    sync_database(api_key='ir9dho0AwDNuDN-TTQIiy0qYJIsE3BP35hqYBVgax4OJUeplyakzzg', db_owner='norse', db_name='file_management.db', db_path='file_management.db')
    await message.reply("Database has been synced with dbhub.io.", parse_mode=ParseMode.MARKDOWN)