import os
import shutil
import requests
import sys
import subprocess

# Path to the flag file
FLAG_FILE_PATH = 'restart_flag.tmp'

# Function to delete the local database
def delete_local_database(db_path):
    if os.path.exists(db_path):
        os.remove(db_path)
        print("Local database deleted successfully.")
    else:
        print("Local database not found.")

# Function to download the database from dbhub.io
def download_database(api_key, db_owner, db_name, temp_db_path):
    url = 'https://api.dbhub.io/v1/download'
    try:
        response = requests.post(
            url,
            data={'apikey': api_key, 'dbowner': db_owner, 'dbname': db_name}
        )
        
        status_code = response.status_code
        if status_code == 200:
            with open(temp_db_path, 'wb') as file:
                file.write(response.content)
            print("Database downloaded and saved successfully.")
        else:
            print(f"Failed to download database: Status Code: {status_code}, Response: {response.content.decode()}")
    except Exception as e:
        print(f"An error occurred while downloading the database: {e}")

# Function to replace the local database with the downloaded file
def replace_local_database(db_path, temp_db_path):
    if os.path.exists(temp_db_path):
        try:
            if os.path.exists(db_path):
                os.remove(db_path)
                print("Existing local database removed.")
            shutil.move(temp_db_path, db_path)
            print("New database file set successfully.")
        except Exception as e:
            print(f"An error occurred while replacing the database: {e}")
    else:
        print(f"Temporary database file not found: {temp_db_path}")

# Function to restart the script using subprocess
def restart_script():
    if not os.path.exists(FLAG_FILE_PATH):
        with open(FLAG_FILE_PATH, 'w') as flag_file:
            flag_file.write('restart')
        
        print("Restarting script...")
        try:
            subprocess.run([sys.executable] + sys.argv, check=True)
        except Exception as e:
            print(f"An error occurred while restarting the script: {e}")
        finally:
            # Remove the flag file after the restart
            os.remove(FLAG_FILE_PATH)
    else:
        print("Script already restarted. Skipping restart.")

# Main sync function
def sync_database(api_key, db_owner, db_name, db_path):
    temp_db_path = db_path + '.tmp'
    
    # Download the new database
    download_database(api_key, db_owner, db_name, temp_db_path)
    
    # Replace the old database with the new one
    replace_local_database(db_path, temp_db_path)
    
    # Restart the script to ensure the bot uses the new database
    restart_script()

from aiogram.types import ParseMode
from aiogram import types

async def sync_database_command(message: types.Message):
    sync_database(api_key='ir9dho0AwDNuDN-TTQIiy0qYJIsE3BP35hqYBVgax4OJUeplyakzzg', db_owner='norse', db_name='file_management.db', db_path='file_management.db')
    await message.reply("Database has been synced with dbhub.io.", parse_mode=ParseMode.MARKDOWN)