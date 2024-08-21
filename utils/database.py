import sqlite3
from config import DB_FILE_PATH, ADMIN_IDS

# Connect to the SQLite database
conn = sqlite3.connect(DB_FILE_PATH)
cursor = conn.cursor()

# Table to manage folders
cursor.execute('''
CREATE TABLE IF NOT EXISTS folders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    parent_id INTEGER,
    premium INTEGER DEFAULT 0,  -- Indicates if the folder is premium
    download_count INTEGER DEFAULT 0,  -- Tracks the number of downloads
    admin_approval INTEGER DEFAULT 0,  -- Indicates if admin approval is required
    FOREIGN KEY (parent_id) REFERENCES folders (id)
)
''')

## Table to manage files
cursor.execute('''
CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id TEXT NOT NULL,
    file_name TEXT NOT NULL,
    folder_id INTEGER,
    message_id INTEGER,
    caption TEXT,  -- Custom caption for the file
    file_type TEXT NOT NULL,  -- Type of the file ('document', 'video', 'photo')
    FOREIGN KEY (folder_id) REFERENCES folders (id)
)
''')
conn.commit()

# Table to manage users
cursor.execute('''
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    premium_expiration DATETIME,  -- Expiration date of premium status
    approved INTEGER DEFAULT 0,  -- Indicates if the user is approved
    status TEXT DEFAULT 'pending',  -- User status ('pending', 'approved', etc.)
    premium INTEGER DEFAULT 0,  -- Indicates if the user is a premium member
    last_download DATETIME,  -- Timestamp of the last download
    welcome_sent INTEGER DEFAULT 0  -- Tracks if the welcome message was sent (0 = not sent, 1 = sent)
)
''')
conn.commit()

# Table to store the current caption settings
cursor.execute('''
CREATE TABLE IF NOT EXISTS current_caption (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    caption_type TEXT NOT NULL,  -- 'custom' or 'append'
    custom_text TEXT  -- The custom text to be used in the caption
)
''')
conn.commit()

# Table to store user-folder approval status
cursor.execute('''
CREATE TABLE IF NOT EXISTS user_folder_approval (
    user_id INTEGER,
    folder_id INTEGER,
    approved INTEGER DEFAULT 0,  -- 0 = not approved, 1 = approved
    download_completed INTEGER DEFAULT 0,  -- 0 = not downloaded, 1 = downloaded
    PRIMARY KEY (user_id, folder_id)
)
''')
conn.commit()

# Table to store global bot states
cursor.execute('''
CREATE TABLE IF NOT EXISTS bot_state (
    key TEXT PRIMARY KEY,  -- The name of the state (e.g., 'awaiting_new_db_upload')
    value INTEGER  -- The value of the state (e.g., 0 or 1)
)
''')
conn.commit()

# Adds new users to the database
def add_user_to_db(user_id):
    cursor.execute('SELECT user_id FROM users WHERE user_id = ?', (user_id,))
    user = cursor.fetchone()
    
    if not user:
        status = 'approved' if str(user_id) in ADMIN_IDS else 'pending'
        cursor.execute('INSERT INTO users (user_id, status) VALUES (?, ?)', (user_id, status))
        conn.commit()
