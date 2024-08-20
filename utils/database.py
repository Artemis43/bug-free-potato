import sqlite3
from config import DB_FILE_PATH, ADMIN_IDS

# Connect to the SQLite database
conn = sqlite3.connect(DB_FILE_PATH)
cursor = conn.cursor()

# Create tables for folders and files
cursor.execute('''
CREATE TABLE IF NOT EXISTS folders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    parent_id INTEGER,
    premium INTEGER DEFAULT 0, -- Premium column
    download_count INTEGER DEFAULT 0, -- Download count column
    admin_approval INTEGER DEFAULT 0, -- Admin approval required
    FOREIGN KEY (parent_id) REFERENCES folders (id)
)
''')

cursor.execute('''
CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id TEXT NOT NULL,
    file_name TEXT NOT NULL,
    folder_id INTEGER,
    message_id INTEGER,
    caption TEXT, -- Caption column
    FOREIGN KEY (folder_id) REFERENCES folders (id)
)
''')
conn.commit()

# Create table for users for broadcast
cursor.execute('''
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    premium_expiration DATETIME, -- Premium expiration column
    approved INTEGER DEFAULT 0, -- Approved column
    status TEXT DEFAULT 'pending', -- Status column
    premium INTEGER DEFAULT 0, -- Premium column
    last_download DATETIME, -- Last download column
    welcome_sent INTEGER DEFAULT 0 -- Welcome message sent column (0 = not sent, 1 = sent)
)
''')
conn.commit()

# Create table for storing the current caption
cursor.execute('''
CREATE TABLE IF NOT EXISTS current_caption (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    caption_type TEXT NOT NULL,  -- 'custom' or 'append'
    custom_text TEXT
)
''')
conn.commit()

# Create table to store user-folder approval status
cursor.execute('''
CREATE TABLE IF NOT EXISTS user_folder_approval (
    user_id INTEGER,
    folder_id INTEGER,
    approved INTEGER DEFAULT 0, -- 0 = not approved, 1 = approved
    download_completed INTEGER DEFAULT 0, -- 0 = not downloaded, 1 = downloaded
    PRIMARY KEY (user_id, folder_id)
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
