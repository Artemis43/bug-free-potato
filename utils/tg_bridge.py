"""
utils/tg_bridge.py — Telegram Client Bridge (MTProto) for marrow-dashboard.
Runs Telethon in a dedicated background event loop thread.
"""

import os
import asyncio
import threading
import logging
from telethon import TelegramClient, functions, types
from telethon.errors import SessionPasswordNeededError
import utils.database as db

log = logging.getLogger(__name__)

# Global state
_loop = None
_thread = None
_client = None
_phone_code_hash = None
_phone = None

def start_loop():
    global _loop, _thread
    if _loop is None:
        _loop = asyncio.new_event_loop()
        _thread = threading.Thread(target=_loop_run_forever, args=(_loop,), daemon=True)
        _thread.start()
        log.info("Telegram bridge background event loop started.")

def _loop_run_forever(loop):
    asyncio.set_event_loop(loop)
    loop.run_forever()

def run_async(coro):
    """Run a coroutine safely on our background event loop thread and block for result."""
    start_loop()
    future = asyncio.run_coroutine_threadsafe(coro, _loop)
    return future.result()

async def _init_client_async(api_id: int, api_hash: str):
    global _client
    if _client is not None:
        try:
            if _client.is_connected():
                return _client
        except Exception:
            pass
    
    # Store session file in the bot folder
    session_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "marrow_bridge")
    _client = TelegramClient(session_path, api_id, api_hash)
    await _client.connect()
    return _client

def init_client_from_db():
    """Tries to initialize client from stored credentials in database."""
    try:
        api_id_row = db.db_fetchone("SELECT value_text FROM payment_config WHERE key = 'tg_bridge_api_id'")
        api_hash_row = db.db_fetchone("SELECT value_text FROM payment_config WHERE key = 'tg_bridge_api_hash'")
        if api_id_row and api_hash_row and api_id_row[0] and api_hash_row[0]:
            api_id = int(api_id_row[0].strip())
            api_hash = api_hash_row[0].strip()
            run_async(_init_client_async(api_id, api_hash))
            return True
    except Exception as e:
        log.error(f"Failed to auto-init tg_bridge client: {e}")
    return False

def get_connection_status():
    """Returns status dictionary for the bridge."""
    if _client is None:
        # Try auto-init once
        if init_client_from_db():
            return get_connection_status()
        return {"connected": False, "authorized": False, "message": "No client configured."}
    
    try:
        connected = _client.is_connected()
        authorized = run_async(_client.is_user_authorized()) if connected else False
        me = None
        if authorized:
            me_user = run_async(_client.get_me())
            # Fetch active upload folder ID for the admin user from DB
            row = db.db_fetchone("SELECT current_upload_folder_id FROM users WHERE user_id = %s", (me_user.id,))
            current_folder_id = row[0] if row else None
            me = {
                "id": me_user.id,
                "first_name": me_user.first_name,
                "username": me_user.username,
                "phone": me_user.phone,
                "current_upload_folder_id": current_folder_id
            }
        return {
            "connected": connected,
            "authorized": authorized,
            "me": me,
            "message": "Authorized & Online" if authorized else ("Connected, needs login" if connected else "Disconnected")
        }
    except Exception as e:
        return {"connected": False, "authorized": False, "message": str(e)}

async def _send_code_async(api_id: int, api_hash: str, phone: str):
    global _client, _phone_code_hash, _phone
    await _init_client_async(api_id, api_hash)
    _phone = phone.strip()
    result = await _client.send_code_request(_phone)
    _phone_code_hash = result.phone_code_hash
    # Save config to database
    db.db_execute(
        "INSERT INTO payment_config (key, value_text) VALUES ('tg_bridge_api_id', %s) "
        "ON CONFLICT (key) DO UPDATE SET value_text = EXCLUDED.value_text",
        (str(api_id),)
    )
    db.db_execute(
        "INSERT INTO payment_config (key, value_text) VALUES ('tg_bridge_api_hash', %s) "
        "ON CONFLICT (key) DO UPDATE SET value_text = EXCLUDED.value_text",
        (api_hash,)
    )
    db.db_execute(
        "INSERT INTO payment_config (key, value_text) VALUES ('tg_bridge_phone', %s) "
        "ON CONFLICT (key) DO UPDATE SET value_text = EXCLUDED.value_text",
        (_phone,)
    )
    return {"ok": True, "message": "Code sent successfully."}

def send_code(api_id: int, api_hash: str, phone: str):
    try:
        return run_async(_send_code_async(api_id, api_hash, phone))
    except Exception as e:
        return {"ok": False, "error": str(e)}

async def _login_async(code: str, password: str = None):
    global _client, _phone, _phone_code_hash
    if not _client:
        return {"ok": False, "error": "Client not initialized. Request code first."}
    
    try:
        await _client.sign_in(_phone, code, phone_code_hash=_phone_code_hash)
        return {"ok": True, "message": "Logged in successfully!"}
    except SessionPasswordNeededError:
        if password:
            await _client.sign_in(password=password)
            return {"ok": True, "message": "Logged in successfully with 2FA password!"}
        else:
            return {"ok": True, "need_password": True, "message": "2FA password required."}

def login(code: str, password: str = None):
    try:
        return run_async(_login_async(code, password))
    except Exception as e:
        return {"ok": False, "error": str(e)}

def logout():
    global _client
    if _client:
        try:
            run_async(_client.log_out())
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}
    return {"ok": True}

async def _get_chats_async(query: str = None):
    global _client
    if not _client or not await _client.is_user_authorized():
        return []
    
    dialogs = await _client.get_dialogs()
    results = []
    for d in dialogs:
        # Filter groups, channels, supergroups, private chats
        is_channel = d.is_channel
        is_group = d.is_group
        
        type_str = "user"
        if is_channel:
            type_str = "channel" if getattr(d.entity, 'broadcast', False) else "supergroup"
        elif is_group:
            type_str = "group"
            
        results.append({
            "id": d.id,
            "name": d.name or "Unnamed",
            "type": type_str,
            "username": getattr(d.entity, 'username', None),
            "unread_count": d.unread_count
        })
        
    if query:
        q = query.lower()
        results = [r for r in results if q in r["name"].lower() or (r["username"] and q in r["username"].lower())]
        
    return results

def get_chats(query: str = None):
    try:
        return {"ok": True, "chats": run_async(_get_chats_async(query))}
    except Exception as e:
        return {"ok": False, "error": str(e)}

async def _get_messages_async(chat_id: int, limit: int = 50):
    global _client
    if not _client or not await _client.is_user_authorized():
        return []
    
    messages = await _client.get_messages(chat_id, limit=limit)
    results = []
    for m in messages:
        # Check media details
        media_type = None
        file_name = None
        file_size = 0
        
        if m.media:
            if m.document:
                media_type = "document"
                # Find filename in attributes
                for attr in m.document.attributes:
                    if isinstance(attr, types.DocumentAttributeFilename):
                        file_name = attr.file_name
                        break
                if not file_name:
                    file_name = f"doc_{m.id}"
                file_size = m.document.size
            elif m.video:
                media_type = "video"
                file_name = f"video_{m.id}.mp4"
                file_size = m.video.size
            elif m.photo:
                media_type = "photo"
                file_name = f"photo_{m.id}.jpg"
                # photos don't have direct size attribute, get size of largest size
                file_size = 0
                if hasattr(m.photo, 'sizes') and m.photo.sizes:
                    largest = m.photo.sizes[-1]
                    if hasattr(largest, 'size'):
                        file_size = largest.size
                    elif hasattr(largest, 'sizes') and largest.sizes:
                        file_size = largest.sizes[-1]
        
        results.append({
            "id": m.id,
            "date": m.date.isoformat() if m.date else None,
            "text": m.text or "",
            "media_type": media_type,
            "file_name": file_name,
            "file_size": file_size,
            "from_id": m.sender_id
        })
    return results

def get_messages(chat_id: int, limit: int = 50):
    try:
        return {"ok": True, "messages": run_async(_get_messages_async(chat_id, limit))}
    except Exception as e:
        return {"ok": False, "error": str(e)}

async def _forward_messages_async(chat_id: int, message_ids: list[int], bot_username: str):
    global _client
    if not _client or not await _client.is_user_authorized():
        return {"ok": False, "error": "Not authorized"}
        
    # Forward the messages directly to the bot chat
    await _client.forward_messages(bot_username, message_ids, chat_id)
    return {"ok": True, "forwarded_count": len(message_ids)}

def forward_messages(chat_id: int, message_ids: list[int], bot_username: str):
    try:
        return run_async(_forward_messages_async(chat_id, message_ids, bot_username))
    except Exception as e:
        return {"ok": False, "error": str(e)}
