import os
import shutil
import requests
import sys
import subprocess
from config import API_KEY, DB_FILE_PATH, DBNAME, DBOWNER

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
    # Only restart if the script is not already being restarted
    if os.path.exists(FLAG_FILE_PATH):
        print("Script already in the process of restarting. Skipping restart.")
        return
    
    # Create the flag file to indicate a restart is in progress
    with open(FLAG_FILE_PATH, 'w') as flag_file:
        flag_file.write('restart')
    
    print("Restarting script...")
    try:
        subprocess.run([sys.executable] + sys.argv, check=True)
    except Exception as e:
        print(f"An error occurred while restarting the script: {e}")
    finally:
        # Remove the flag file after the restart attempt to avoid looping restarts
        if os.path.exists(FLAG_FILE_PATH):
            os.remove(FLAG_FILE_PATH)

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
    sync_database(api_key=API_KEY , db_owner=DBOWNER, db_name=DBNAME, db_path=DB_FILE_PATH)
    await message.reply("Database has now been synced. You can use the bot now", parse_mode=ParseMode.MARKDOWN)