import sys
import json
import base64
import urllib.request
import urllib.parse
from datetime import datetime, date, timezone, timedelta
import utils.database as db

def send_telegram_msg(user_id, text):
    try:
        from config import API_TOKEN
        if not API_TOKEN:
            return
        url = f"https://api.telegram.org/bot{API_TOKEN}/sendMessage"
        data = urllib.parse.urlencode({
            'chat_id': user_id,
            'text': text,
            'parse_mode': 'HTML'
        }).encode('utf-8')
        req = urllib.request.Request(url, data=data)
        urllib.request.urlopen(req, timeout=5)
    except Exception as e:
        sys.stderr.write(f"Telegram notification error: {str(e)}\n")

def get_bot_username():
    try:
        from config import API_TOKEN
        if not API_TOKEN:
            return "bot"
        url = f"https://api.telegram.org/bot{API_TOKEN}/getMe"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("ok"):
                return data["result"]["username"]
    except Exception as e:
        sys.stderr.write(f"Error getting bot username: {str(e)}\n")
    return "bot"

def get_forced_subscriptions(api_token=None):
    from config import REQUIRED_CHANNELS, API_TOKEN as DEFAULT_TOKEN
    token = api_token or DEFAULT_TOKEN
    if not token or not REQUIRED_CHANNELS:
        return []
        
    channels_info = []
    for chan in REQUIRED_CHANNELS:
        try:
            # First fetch chat details
            chat_url = f"https://api.telegram.org/bot{token}/getChat"
            chat_data = urllib.parse.urlencode({'chat_id': chan}).encode('utf-8')
            chat_req = urllib.request.Request(chat_url, data=chat_data)
            
            with urllib.request.urlopen(chat_req, timeout=1.5) as chat_resp:
                chat_res = json.loads(chat_resp.read().decode('utf-8'))
                
            if not chat_res.get("ok"):
                continue
                
            chat = chat_res["result"]
            title = chat.get("title") or chat.get("first_name") or chan
            username = chat.get("username")
            chat_type = chat.get("type", "channel")
            
            # Fetch member count
            count_url = f"https://api.telegram.org/bot{token}/getChatMemberCount"
            count_data = urllib.parse.urlencode({'chat_id': chan}).encode('utf-8')
            count_req = urllib.request.Request(count_url, data=count_data)
            
            member_count = 0
            with urllib.request.urlopen(count_req, timeout=1.5) as count_resp:
                count_res = json.loads(count_resp.read().decode('utf-8'))
                if count_res.get("ok"):
                    member_count = count_res["result"]
                    
            channels_info.append({
                "chat_id": chan,
                "title": title,
                "username": username,
                "type": chat_type,
                "member_count": member_count
            })
        except Exception as e:
            # Return fallback details if API call fails
            channels_info.append({
                "chat_id": chan,
                "title": chan,
                "username": chan.replace("@", "") if chan.startswith("@") else None,
                "type": "channel",
                "member_count": 0,
                "error": str(e)
            })
            
    return channels_info

class DateTimeEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, (datetime, date)):
            return obj.isoformat()
        return super().default(obj)

def get_stats():
    # 1. User stats
    total_users = db.db_fetchone("SELECT COUNT(*) FROM users")[0]
    premium_users = db.db_fetchone("SELECT COUNT(*) FROM users WHERE premium = TRUE")[0]
    
    # Signups
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    users_today = db.db_fetchone("SELECT COUNT(*) FROM users WHERE created_at >= %s", (today_start,))[0]
    
    # 2. Category and Folder stats
    total_categories = db.db_fetchone("SELECT COUNT(*) FROM categories")[0]
    total_folders = db.db_fetchone("SELECT COUNT(*) FROM folders")[0]
    
    free_folders = db.db_fetchone("SELECT COUNT(*) FROM folders WHERE premium = FALSE AND admin_approval = FALSE")[0]
    premium_folders = db.db_fetchone("SELECT COUNT(*) FROM folders WHERE premium = TRUE AND admin_approval = FALSE")[0]
    paid_folders = db.db_fetchone("SELECT COUNT(*) FROM folders WHERE admin_approval = TRUE")[0]
    
    # 3. Files and Downloads
    total_files = db.db_fetchone("SELECT COUNT(*) FROM files")[0]
    total_downloads = db.db_fetchone("SELECT SUM(download_count) FROM folders")[0] or 0
    
    # 4. Revenue (INR Razorpay + Stars)
    # Razorpay paid orders (INR)
    total_revenue_paise = db.db_fetchone("SELECT SUM(amount_paise) FROM payment_orders WHERE status = 'paid' AND payment_method != 'stars'")[0] or 0
    total_revenue_inr = total_revenue_paise / 100.0
    
    # Stars paid orders (Stars)
    # Note: payment_orders table has payment_method which can be 'stars'. If stars, amount_paise is actually stars or stored as stars.
    # Let's check how payment_stars.py records orders. Let's do a select if possible, or support it.
    stars_revenue = db.db_fetchone("SELECT SUM(amount_paise) FROM payment_orders WHERE status = 'paid' AND payment_method = 'stars'")[0] or 0
    
    return {
        "users": {
            "total": total_users,
            "premium": premium_users,
            "today": users_today
        },
        "structure": {
            "categories": total_categories,
            "folders": total_folders,
            "folders_free": free_folders,
            "folders_premium": premium_folders,
            "folders_paid": paid_folders,
            "files": total_files
        },
        "downloads": total_downloads,
        "revenue": {
            "inr": total_revenue_inr,
            "stars": stars_revenue
        }
    }

def get_stats_fast():
    """Consolidated stats in a single CTE query — replaces 10 sequential SELECTs."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    row = db.db_fetchone("""
        WITH user_stats AS (
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE premium = TRUE)                          AS premium,
                COUNT(*) FILTER (WHERE created_at >= %s)                        AS today,
                COUNT(*) FILTER (WHERE status = 'pending')                      AS pending
            FROM users
        ),
        folder_stats AS (
            SELECT
                COUNT(*)                                                         AS total,
                COUNT(*) FILTER (WHERE NOT premium AND NOT admin_approval)      AS free,
                COUNT(*) FILTER (WHERE premium     AND NOT admin_approval)      AS prem,
                COUNT(*) FILTER (WHERE admin_approval)                          AS paid,
                COALESCE(SUM(download_count), 0)                               AS downloads
            FROM folders
        ),
        cat_stats   AS (SELECT COUNT(*) AS total FROM categories),
        file_stats  AS (SELECT COUNT(*) AS total FROM files),
        rev_stats   AS (
            SELECT
                COALESCE(SUM(amount_paise) FILTER (WHERE payment_method != 'stars'), 0) AS inr_paise,
                COALESCE(SUM(amount_paise) FILTER (WHERE payment_method = 'stars'),  0) AS stars
            FROM payment_orders WHERE status = 'paid'
        )
        SELECT
            u.total, u.premium, u.today, u.pending,
            f.total, f.free, f.prem, f.paid, f.downloads,
            c.total,
            fi.total,
            r.inr_paise, r.stars
        FROM user_stats u, folder_stats f, cat_stats c, file_stats fi, rev_stats r
    """, (today_start,))

    if not row:
        return {}

    return {
        "users": {
            "total":   row[0],
            "premium": row[1],
            "today":   row[2],
            "pending": row[3],
        },
        "structure": {
            "categories":    row[9],
            "folders":        row[4],
            "folders_free":   row[5],
            "folders_premium":row[6],
            "folders_paid":   row[7],
            "files":          row[10],
        },
        "downloads": row[8],
        "revenue": {
            "inr":   (row[11] or 0) / 100.0,
            "stars":  row[12] or 0,
        },
    }


def get_stats():
    """Legacy wrapper — now backed by the fast single-query version."""
    return get_stats_fast()


def _build_folders(conn):
    """Fetch folders with file_count using a single GROUP BY query (no correlated subqueries)."""
    cur = conn.cursor()
    cur.execute("""
        SELECT
            f.id, f.name, f.parent_id, f.category_id,
            f.premium, f.admin_approval, f.download_count,
            COUNT(fi.id) AS file_count,
            c.name        AS category_name,
            COALESCE(pfp.amount_paise, 0) AS price_inr_paise,
            COALESCE(sfp.amount_stars, 0) AS price_stars
        FROM folders f
        LEFT JOIN files           fi  ON fi.folder_id = f.id
        LEFT JOIN categories      c   ON c.id = f.category_id
        LEFT JOIN payment_folder_prices pfp ON pfp.folder_id = f.id
        LEFT JOIN stars_folder_prices   sfp ON sfp.folder_id = f.id
        GROUP BY f.id, c.name, pfp.amount_paise, sfp.amount_stars
        ORDER BY f.name
    """)
    rows = cur.fetchall()
    folders = []
    for r in rows:
        ftype = 'free'
        if r[4]:   ftype = 'premium'
        elif r[5]: ftype = 'paid'
        folders.append({
            "id":             r[0],
            "name":           r[1],
            "parent_id":      r[2],
            "category_id":    r[3],
            "category_name":  r[8] or "Uncategorized",
            "premium":        r[4],
            "admin_approval": r[5],
            "type":           ftype,
            "download_count": r[6],
            "file_count":     r[7],
        })
    return folders


def get_core_data():
    """
    Lean initial payload — loads only what the Overview, Categories, and Folders
    tabs need.  Users, Files, Approvals are fetched lazily per-tab.

    Queries batched into as few round-trips as possible via a single connection.
    """
    conn = db.get_connection()
    try:
        cur = conn.cursor()

        # ── 1. Categories ──────────────────────────────────────────────────────
        cur.execute("SELECT id, name, emoji, sort_order, created_at FROM categories ORDER BY sort_order, name")
        categories = [
            {"id": r[0], "name": r[1], "emoji": r[2], "sort_order": r[3], "created_at": r[4]}
            for r in cur.fetchall()
        ]

        # ── 2. Folders (GROUP BY replaces correlated subquery) ─────────────────
        folders = _build_folders(conn)

        # ── 3. Caption ─────────────────────────────────────────────────────────
        cur.execute("SELECT caption_type, custom_text FROM current_caption ORDER BY id DESC LIMIT 1")
        caption_row = cur.fetchone()
        caption = {
            "caption_type": caption_row[0] if caption_row else "custom",
            "custom_text":  caption_row[1] if caption_row else "",
        }

        # ── 4. Pricing config (batch both keys in one query) ───────────────────
        cur.execute(
            "SELECT key, value_int FROM payment_config WHERE key IN (%s, %s)",
            ('default_folder_price_paise', 'default_folder_price_stars')
        )
        price_config = {r[0]: r[1] for r in cur.fetchall()}
        default_price_inr   = (price_config.get('default_folder_price_paise', 9900) or 9900) / 100.0
        default_price_stars = price_config.get('default_folder_price_stars', 50) or 50

        # ── 5. Per-folder price overrides (batch) ──────────────────────────────
        cur.execute("SELECT folder_id, amount_paise FROM payment_folder_prices")
        inr_prices = {r[0]: r[1] / 100.0 for r in cur.fetchall()}

        cur.execute("SELECT folder_id, amount_stars FROM stars_folder_prices")
        stars_prices = {r[0]: r[1] for r in cur.fetchall()}

        # ── 6. Storage channels ────────────────────────────────────────────────
        cur.execute("SELECT id, chat_id, title, active FROM storage_channels ORDER BY id")
        storage_channels = [
            {"id": r[0], "chat_id": str(r[1]), "title": r[2] or f"Channel #{r[0]}", "active": r[3]}
            for r in cur.fetchall()
        ]

    finally:
        db._release(conn)

    # ── 7. Stats (single CTE query, separate connection) ──────────────────────
    stats = get_stats_fast()

    return {
        "categories":    categories,
        "folders":       folders,
        "caption":       caption,
        "folder_prices": {
            "inr":           inr_prices,
            "stars":         stars_prices,
            "default_inr":   default_price_inr,
            "default_stars": default_price_stars,
        },
        "storage_channels": storage_channels,
        "stats":         stats,
        # Stub empty arrays so existing frontend code doesn't break immediately;
        # the dashboard will replace these via lazy per-tab fetches.
        "users":  [],
        "files":  [],
        "folder_approvals": [],
        "payment_plans": [],
        "stars_payment_plans": [],
        "forced_subs": [],
    }


def get_all_data():
    """
    Legacy full-payload action kept for backwards compatibility.
    New dashboards should call get_core + per-tab lazy actions.
    Still faster than before thanks to the consolidated stat query
    and GROUP BY folders query.
    """
    core = get_core_data()

    # ── Users ──────────────────────────────────────────────────────────────────
    users_raw = db.db_fetchall("""
        SELECT user_id, username, first_name, status, premium,
               premium_expiration, welcome_sent, last_download, created_at
        FROM users
        ORDER BY created_at DESC NULLS LAST, user_id DESC
        LIMIT 2000
    """)
    users = [
        {
            "user_id":            str(r[0]),
            "username":           r[1],
            "first_name":         r[2],
            "status":             r[3],
            "premium":            r[4],
            "premium_expiration": r[5],
            "welcome_sent":       r[6],
            "last_download":      r[7],
            "created_at":         r[8],
        }
        for r in users_raw
    ]

    # ── Files (limited) ────────────────────────────────────────────────────────
    files_raw = db.db_fetchall(
        "SELECT id, folder_id, file_id, file_name, file_type, message_id, caption "
        "FROM files ORDER BY id DESC LIMIT 1000"
    )
    files = [
        {
            "id":         r[0], "folder_id":  r[1], "file_id": r[2],
            "file_name":  r[3], "file_type":  r[4], "message_id": r[5],
            "caption":    r[6],
        }
        for r in files_raw
    ]

    # ── Plans ──────────────────────────────────────────────────────────────────
    plans_raw = db.db_fetchall("SELECT id, name, amount_paise, days, active FROM payment_plans ORDER BY amount_paise")
    plans = [
        {"id": r[0], "name": r[1], "amount_paise": r[2], "amount_inr": r[2] / 100.0, "days": r[3], "active": r[4]}
        for r in plans_raw
    ]

    stars_plans_raw = db.db_fetchall("SELECT id, name, amount_stars, days, active FROM stars_payment_plans ORDER BY amount_stars")
    stars_plans = [
        {"id": r[0], "name": r[1], "amount_stars": r[2], "days": r[3], "active": r[4]}
        for r in stars_plans_raw
    ]

    # ── Approvals ──────────────────────────────────────────────────────────────
    approvals_raw = db.db_fetchall("""
        SELECT ufa.user_id, ufa.folder_id, ufa.approved, ufa.download_completed,
               u.first_name, u.username, f.name AS folder_name
        FROM user_folder_approval ufa
        LEFT JOIN users   u ON u.user_id  = ufa.user_id
        LEFT JOIN folders f ON f.id       = ufa.folder_id
        ORDER BY ufa.approved ASC, ufa.user_id DESC
    """)
    approvals = [
        {
            "user_id":           str(r[0]),
            "folder_id":         r[1],
            "approved":          r[2],
            "download_completed":r[3],
            "first_name":        r[4] or f"User {r[0]}",
            "username":          r[5],
            "folder_name":       r[6] or f"Folder #{r[1]}",
        }
        for r in approvals_raw
    ]

    # ── Forced subs (Telegram API — intentionally last) ────────────────────────
    forced_subs = get_forced_subscriptions()

    core.update({
        "users":               users,
        "files":               files,
        "payment_plans":       plans,
        "stars_payment_plans": stars_plans,
        "folder_approvals":    approvals,
        "forced_subs":         forced_subs,
    })
    return core


# ── Per-tab lazy-load actions ──────────────────────────────────────────────────

def get_users_paginated(params):
    """
    Server-side paginated, filtered, sorted user list.
    params: { page, page_size, search, status, premium, sort, dir }
    """
    page      = max(1, int(params.get('page', 1)))
    page_size = min(200, max(10, int(params.get('page_size', 50))))
    search    = (params.get('search') or '').strip()
    status_f  = params.get('status', 'all')
    prem_f    = params.get('premium', 'all')
    sort_col  = params.get('sort', 'created_at')
    sort_dir  = 'DESC' if params.get('dir', 'desc').lower() == 'desc' else 'ASC'

    ALLOWED_COLS = {'user_id', 'username', 'first_name', 'status', 'premium',
                    'premium_expiration', 'created_at', 'last_download'}
    if sort_col not in ALLOWED_COLS:
        sort_col = 'created_at'

    conditions = []
    bind_vals  = []

    if search:
        conditions.append(
            "(CAST(user_id AS TEXT) ILIKE %s OR username ILIKE %s OR first_name ILIKE %s)"
        )
        like = f"%{search}%"
        bind_vals.extend([like, like, like])

    if status_f != 'all':
        conditions.append("status = %s")
        bind_vals.append(status_f)

    if prem_f == 'premium':
        conditions.append("premium = TRUE")
    elif prem_f == 'regular':
        conditions.append("premium = FALSE")

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    count_row = db.db_fetchone(
        f"SELECT COUNT(*) FROM users {where}", tuple(bind_vals)
    )
    total = count_row[0] if count_row else 0

    offset = (page - 1) * page_size
    rows = db.db_fetchall(
        f"""
        SELECT user_id, username, first_name, status, premium,
               premium_expiration, welcome_sent, last_download, created_at
        FROM users
        {where}
        ORDER BY {sort_col} {sort_dir} NULLS LAST
        LIMIT %s OFFSET %s
        """,
        tuple(bind_vals) + (page_size, offset)
    )

    users = [
        {
            "user_id":            str(r[0]),
            "username":           r[1],
            "first_name":         r[2],
            "status":             r[3],
            "premium":            r[4],
            "premium_expiration": r[5],
            "welcome_sent":       r[6],
            "last_download":      r[7],
            "created_at":         r[8],
        }
        for r in rows
    ]

    import math
    return {
        "users": users,
        "total": total,
        "page":  page,
        "pages": math.ceil(total / page_size) if total else 1,
        "page_size": page_size,
    }


def get_files_paginated(params):
    """
    Server-side paginated, filtered file list with folder_name joined in.
    params: { page, page_size, search, file_type, folder_id }
    """
    page      = max(1, int(params.get('page', 1)))
    page_size = min(200, max(10, int(params.get('page_size', 50))))
    search    = (params.get('search') or '').strip()
    type_f    = (params.get('file_type') or 'all').strip()
    folder_f  = params.get('folder_id')  # int or None

    conditions = []
    bind_vals  = []

    if search:
        conditions.append(
            "(fi.file_name ILIKE %s OR fi.caption ILIKE %s)"
        )
        like = f"%{search}%"
        bind_vals.extend([like, like])

    if type_f and type_f != 'all':
        conditions.append("fi.file_type ILIKE %s")
        bind_vals.append(f"%{type_f}%")

    if folder_f:
        try:
            conditions.append("fi.folder_id = %s")
            bind_vals.append(int(folder_f))
        except (TypeError, ValueError):
            pass

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    base_q = f"""
        FROM files fi
        LEFT JOIN folders f ON f.id = fi.folder_id
        {where}
    """

    count_row = db.db_fetchone(f"SELECT COUNT(*) {base_q}", tuple(bind_vals))
    total = count_row[0] if count_row else 0

    offset = (page - 1) * page_size
    rows = db.db_fetchall(
        f"""
        SELECT fi.id, fi.folder_id, fi.file_id, fi.file_name,
               fi.file_type, fi.message_id, fi.caption,
               f.name AS folder_name
        {base_q}
        ORDER BY fi.id DESC
        LIMIT %s OFFSET %s
        """,
        tuple(bind_vals) + (page_size, offset)
    )

    files = [
        {
            "id":          r[0],
            "folder_id":   r[1],
            "file_id":     r[2],
            "file_name":   r[3],
            "file_type":   r[4],
            "message_id":  r[5],
            "caption":     r[6],
            "folder_name": r[7] or "Unknown Folder",
        }
        for r in rows
    ]

    import math
    return {
        "files":  files,
        "total":  total,
        "page":   page,
        "pages":  math.ceil(total / page_size) if total else 1,
        "page_size": page_size,
    }


def get_approvals_data():
    """All folder purchase approvals with user and folder info joined."""
    rows = db.db_fetchall("""
        SELECT ufa.user_id, ufa.folder_id, ufa.approved, ufa.download_completed,
               u.first_name, u.username, f.name AS folder_name
        FROM user_folder_approval ufa
        LEFT JOIN users   u ON u.user_id = ufa.user_id
        LEFT JOIN folders f ON f.id      = ufa.folder_id
        ORDER BY ufa.approved ASC, ufa.user_id DESC
    """)
    approvals = [
        {
            "user_id":            str(r[0]),
            "folder_id":          r[1],
            "approved":           r[2],
            "download_completed": r[3],
            "first_name":         r[4] or f"User {r[0]}",
            "username":           r[5],
            "folder_name":        r[6] or f"Folder #{r[1]}",
        }
        for r in rows
    ]
    pending = sum(1 for a in approvals if not a['approved'])
    return {"folder_approvals": approvals, "pending_count": pending}


def get_plans_data():
    """Both Razorpay and Stars payment plans plus default pricing config."""
    plans_raw = db.db_fetchall(
        "SELECT id, name, amount_paise, days, active FROM payment_plans ORDER BY amount_paise"
    )
    plans = [
        {"id": r[0], "name": r[1], "amount_paise": r[2],
         "amount_inr": r[2] / 100.0, "days": r[3], "active": r[4]}
        for r in plans_raw
    ]

    stars_raw = db.db_fetchall(
        "SELECT id, name, amount_stars, days, active FROM stars_payment_plans ORDER BY amount_stars"
    )
    stars_plans = [
        {"id": r[0], "name": r[1], "amount_stars": r[2], "days": r[3], "active": r[4]}
        for r in stars_raw
    ]

    cfg = db.db_fetchall(
        "SELECT key, value_int FROM payment_config WHERE key IN (%s, %s)",
        ('default_folder_price_paise', 'default_folder_price_stars')
    )
    price_cfg = {r[0]: r[1] for r in cfg}

    return {
        "payment_plans":       plans,
        "stars_payment_plans": stars_plans,
        "default_price_inr":   (price_cfg.get('default_folder_price_paise', 9900) or 9900) / 100.0,
        "default_price_stars": price_cfg.get('default_folder_price_stars', 50) or 50,
    }

def main():
    if len(sys.argv) < 2:
        print(json.dumps({"ok": False, "error": "No action specified"}))
        return

    action = sys.argv[1]
    
    # Parse data payload if provided (base64 encoded JSON)
    params = {}
    if len(sys.argv) >= 3:
        try:
            raw_data = sys.argv[2]
            # Add padding back if missing
            missing_padding = len(raw_data) % 4
            if missing_padding:
                raw_data += '=' * (4 - missing_padding)
            decoded = base64.b64decode(raw_data).decode('utf-8')
            params = json.loads(decoded)
        except Exception as e:
            print(json.dumps({"ok": False, "error": f"Failed to parse parameters: {str(e)}"}))
            return

    try:
        if action == "stats":
            result = get_stats_fast()
            print(json.dumps({"ok": True, "data": result}, cls=DateTimeEncoder))

        elif action in ("get_core", "get_core_data"):
            result = get_core_data()
            print(json.dumps({"ok": True, "data": result}, cls=DateTimeEncoder))

        elif action == "get_all":
            result = get_all_data()
            print(json.dumps({"ok": True, "data": result}, cls=DateTimeEncoder))

        elif action in ("get_users", "get_users_paginated"):
            result = get_users_paginated(params)
            print(json.dumps({"ok": True, "data": result}, cls=DateTimeEncoder))

        elif action in ("get_files", "get_files_paginated"):
            result = get_files_paginated(params)
            print(json.dumps({"ok": True, "data": result}, cls=DateTimeEncoder))

        elif action in ("get_approvals", "get_approvals_data"):
            result = get_approvals_data()
            print(json.dumps({"ok": True, "data": result}, cls=DateTimeEncoder))

        elif action in ("get_plans", "get_plans_data"):
            result = get_plans_data()
            print(json.dumps({"ok": True, "data": result}, cls=DateTimeEncoder))

        elif action == "forced_subs":
            subs = get_forced_subscriptions()
            print(json.dumps({"ok": True, "data": {"forced_subs": subs}}, cls=DateTimeEncoder))

        elif action == "update_caption":
            caption_type = params.get("caption_type", "custom")
            custom_text = params.get("custom_text", "")
            
            db.db_execute("DELETE FROM current_caption")
            db.db_execute(
                "INSERT INTO current_caption (caption_type, custom_text) VALUES (%s, %s)",
                (caption_type, custom_text)
            )
            print(json.dumps({"ok": True}))
            
        elif action == "update_user":
            user_id = int(params["user_id"])
            status = params.get("status")
            premium = params.get("premium")
            
            # Update fields dynamically
            updates = []
            values = []
            if status is not None:
                updates.append("status = %s")
                values.append(status)
            if premium is not None:
                updates.append("premium = %s")
                values.append(premium)
                
                # If setting premium to true, we must make sure premium_expiration is set/extended.
                if premium:
                    days = int(params.get("premium_days", 30))
                    # Calculate new expiration
                    current_exp_row = db.db_fetchone("SELECT premium_expiration FROM users WHERE user_id = %s", (user_id,))
                    current_exp = current_exp_row[0] if current_exp_row and current_exp_row[0] else None
                    if current_exp is not None:
                        if hasattr(current_exp, 'tzinfo') and current_exp.tzinfo is not None:
                            current_exp = current_exp.replace(tzinfo=None)
                    base_date = max(current_exp, datetime.now()) if (current_exp and current_exp > datetime.now()) else datetime.now()
                    expiration_date = base_date + timedelta(days=days) if 'timedelta' in globals() else base_date + datetime.timedelta(days=days)
                    
                    # For safety, let's just do it directly with PostgreSQL intervals
                    db.db_execute(
                        "UPDATE users SET premium_expiration = NOW() + INTERVAL '%s days' WHERE user_id = %s",
                        (days, user_id)
                    )
                else:
                    updates.append("premium_expiration = NULL")
            
            if updates:
                query = f"UPDATE users SET {', '.join(updates)} WHERE user_id = %s"
                values.append(user_id)
                db.db_execute(query, tuple(values))
                
                # Notify on Telegram status change
                if status is not None:
                    try:
                        if status == "approved":
                            text = "🎉 <b>Access Granted!</b>\n\nYou've been approved to use the bot.\n\n👉 Tap /start to get started!"
                            send_telegram_msg(user_id, text)
                        elif status == "banned":
                            from config import ADMIN_CONTACT
                            text = f"Your access request was not approved. 😢\n\nIf you think this is a mistake, contact us: {ADMIN_CONTACT}"
                            send_telegram_msg(user_id, text)
                    except Exception:
                        pass
                
            print(json.dumps({"ok": True}))
            
        elif action == "category_create":
            name = params["name"].strip()
            emoji = params.get("emoji", "📁").strip() or "📁"
            parent_id = params.get("parent_id")
            if parent_id is not None and parent_id != "":
                parent_id = int(parent_id)
            else:
                parent_id = None

            if db.db_fetchone("SELECT id FROM categories WHERE name = %s", (name,)):
                print(json.dumps({"ok": False, "error": "A category with this name already exists."}))
                return

            db.db_execute(
                "INSERT INTO categories (name, emoji, parent_id) VALUES (%s, %s, %s)",
                (name, emoji, parent_id)
            )
            print(json.dumps({"ok": True}))
            
        elif action == "category_update":
            cat_id = int(params["id"])
            name = params["name"].strip()
            emoji = params.get("emoji", "📁").strip() or "📁"
            sort_order = int(params.get("sort_order", 0))
            parent_id = params.get("parent_id")
            if parent_id is not None and parent_id != "":
                parent_id = int(parent_id)
                # Prevent self-reference
                if parent_id == cat_id:
                    print(json.dumps({"ok": False, "error": "A category cannot be its own parent."}))
                    return
            else:
                parent_id = None

            # Check duplicate name elsewhere
            dup = db.db_fetchone("SELECT id FROM categories WHERE name = %s AND id != %s", (name, cat_id))
            if dup:
                print(json.dumps({"ok": False, "error": "Another category with this name already exists."}))
                return

            db.db_execute(
                "UPDATE categories SET name = %s, emoji = %s, sort_order = %s, parent_id = %s WHERE id = %s",
                (name, emoji, sort_order, parent_id, cat_id)
            )
            print(json.dumps({"ok": True}))
            
        elif action == "category_delete":
            cat_id = int(params["id"])
            # Remove category_id from folders (they go to uncategorized)
            db.db_execute("UPDATE folders SET category_id = NULL WHERE category_id = %s", (cat_id,))
            db.db_execute("DELETE FROM categories WHERE id = %s", (cat_id,))
            print(json.dumps({"ok": True}))
            
        elif action == "folder_update":
            folder_id = int(params["id"])
            category_id = params.get("category_id")
            # category_id can be None (uncategorized)
            if category_id is not None:
                category_id = int(category_id) if category_id != "" else None
                
            folder_type = params.get("type") # 'free', 'premium', 'paid'
            
            # Get current folder metadata
            folder_row = db.db_fetchone("SELECT name FROM folders WHERE id = %s", (folder_id,))
            if not folder_row:
                print(json.dumps({"ok": False, "error": "Folder not found"}))
                return
                
            # Update category_id if provided
            if "category_id" in params:
                db.db_execute("UPDATE folders SET category_id = %s WHERE id = %s", (category_id, folder_id))

            # Update parent_id if provided
            if "parent_id" in params:
                new_parent = params["parent_id"]
                if new_parent is not None and new_parent != "":
                    new_parent = int(new_parent)
                    # Cycle check
                    import utils.database as _db_mod
                    if _db_mod.has_folder_cycle(folder_id, new_parent):
                        print(json.dumps({"ok": False, "error": "Circular parent reference detected."}))
                        return
                else:
                    new_parent = None
                db.db_execute("UPDATE folders SET parent_id = %s WHERE id = %s", (new_parent, folder_id))

            # Update folder type flags
            if folder_type:
                flag_map = {
                    "free":    (False, False),
                    "premium": (True,  False),
                    "paid":    (False, True),
                }
                prem, paid = flag_map[folder_type]
                db.db_execute(
                    "UPDATE folders SET premium = %s, admin_approval = %s WHERE id = %s",
                    (prem, paid, folder_id)
                )

            # If price overrides are provided
            if "price_inr" in params:
                price_inr = float(params["price_inr"])
                price_paise = int(price_inr * 100)
                db.db_execute(
                    """
                    INSERT INTO payment_folder_prices (folder_id, amount_paise)
                    VALUES (%s, %s)
                    ON CONFLICT (folder_id) DO UPDATE SET amount_paise = EXCLUDED.amount_paise
                    """,
                    (folder_id, price_paise)
                )

            if "price_stars" in params:
                price_stars = int(params["price_stars"])
                db.db_execute(
                    """
                    INSERT INTO stars_folder_prices (folder_id, amount_stars)
                    VALUES (%s, %s)
                    ON CONFLICT (folder_id) DO UPDATE SET amount_stars = EXCLUDED.amount_stars
                    """,
                    (folder_id, price_stars)
                )

            print(json.dumps({"ok": True}))
            
        elif action == "file_move":
            file_id = int(params["id"])
            folder_id = int(params["folder_id"])
            
            # Verify folder exists
            if not db.db_fetchone("SELECT 1 FROM folders WHERE id = %s", (folder_id,)):
                print(json.dumps({"ok": False, "error": "Target folder does not exist"}))
                return
                
            db.db_execute("UPDATE files SET folder_id = %s WHERE id = %s", (folder_id, file_id))
            print(json.dumps({"ok": True}))
            
        elif action == "file_update":
            file_id = int(params["id"])
            caption = params.get("caption")
            
            db.db_execute("UPDATE files SET caption = %s WHERE id = %s", (caption, file_id))
            print(json.dumps({"ok": True}))
            
        elif action == "folder_approval_approve":
            user_id = int(params["user_id"])
            folder_id = int(params["folder_id"])
            
            db.db_execute(
                """
                INSERT INTO user_folder_approval (user_id, folder_id, approved, download_completed)
                VALUES (%s, %s, TRUE, FALSE)
                ON CONFLICT (user_id, folder_id) DO UPDATE SET approved = TRUE, download_completed = FALSE
                """,
                (user_id, folder_id)
            )
            
            # Fetch folder name
            folder_row = db.db_fetchone("SELECT name FROM folders WHERE id = %s", (folder_id,))
            folder_name = folder_row[0] if folder_row else f"Folder #{folder_id}"
            
            # Notify user
            text = (
                "✅ <b>Download Approved!</b>\n\n"
                f"Your request for <b>{folder_name}</b> has been approved.\n"
                "You get <b>1 download</b> at Premium speed.\n\n"
                "Use /start to see the folder list and tap the folder to download."
            )
            send_telegram_msg(user_id, text)
            print(json.dumps({"ok": True}))
            
        elif action == "folder_approval_reject":
            user_id = int(params["user_id"])
            folder_id = int(params["folder_id"])
            
            db.db_execute(
                "DELETE FROM user_folder_approval WHERE user_id = %s AND folder_id = %s",
                (user_id, folder_id)
            )
            
            # Fetch folder name
            folder_row = db.db_fetchone("SELECT name FROM folders WHERE id = %s", (folder_id,))
            folder_name = folder_row[0] if folder_row else f"Folder #{folder_id}"
            
            # Notify user
            from config import ADMIN_CONTACT
            text = (
                f"❌ Your request for <b>{folder_name}</b> was not approved.\n\n"
                f"Contact us if you think this is a mistake: {ADMIN_CONTACT}"
            )
            send_telegram_msg(user_id, text)
            print(json.dumps({"ok": True}))
            
        elif action == "payment_plan_save":
            plan_type = params["type"] # 'razorpay' | 'stars'
            plan_id = params.get("id") # None for new plan
            name = params["name"].strip()
            days = int(params["days"])
            active = bool(params.get("active", True))
            
            if plan_type == "razorpay":
                amount_inr = float(params["amount_inr"])
                amount_paise = int(amount_inr * 100)
                if plan_id:
                    db.db_execute(
                        "UPDATE payment_plans SET name = %s, amount_paise = %s, days = %s, active = %s WHERE id = %s",
                        (name, amount_paise, days, active, int(plan_id))
                    )
                else:
                    db.db_execute(
                        "INSERT INTO payment_plans (name, amount_paise, days, active) VALUES (%s, %s, %s, %s)",
                        (name, amount_paise, days, active)
                    )
            elif plan_type == "stars":
                amount_stars = int(params["amount_stars"])
                if plan_id:
                    db.db_execute(
                        "UPDATE stars_payment_plans SET name = %s, amount_stars = %s, days = %s, active = %s WHERE id = %s",
                        (name, amount_stars, days, active, int(plan_id))
                    )
                else:
                    db.db_execute(
                        "INSERT INTO stars_payment_plans (name, amount_stars, days, active) VALUES (%s, %s, %s, %s)",
                        (name, amount_stars, days, active)
                    )
            print(json.dumps({"ok": True}))
            
        elif action == "payment_plan_delete":
            plan_type = params["type"] # 'razorpay' | 'stars'
            plan_id = int(params["id"])
            
            if plan_type == "razorpay":
                db.db_execute("DELETE FROM payment_plans WHERE id = %s", (plan_id,))
            elif plan_type == "stars":
                db.db_execute("DELETE FROM stars_payment_plans WHERE id = %s", (plan_id,))
            print(json.dumps({"ok": True}))
            
        elif action == "reorganize_categories":
            # Reorder a list of categories
            # Expects array of {"id": cat_id, "sort_order": order}
            for item in params.get("categories", []):
                db.db_execute(
                    "UPDATE categories SET sort_order = %s WHERE id = %s",
                    (int(item["sort_order"]), int(item["id"]))
                )
            print(json.dumps({"ok": True}))

        elif action == "user_reset_cooldown":
            user_id = int(params["user_id"])
            db.db_execute("UPDATE users SET last_download = NULL WHERE user_id = %s", (user_id,))
            print(json.dumps({"ok": True}))

        elif action == "catalog_status":
            from utils.catalog import get_catalog_url
            url = get_catalog_url()
            last_gen = db.get_catalog_config("catalog_last_generated")
            print(json.dumps({"ok": True, "url": url, "last_generated": last_gen}))

        elif action == "catalog_generate":
            from utils.catalog import generate_catalog
            import asyncio
            bot_username = get_bot_username()
            url = asyncio.run(generate_catalog(bot_username, force=True))
            print(json.dumps({"ok": bool(url), "url": url}))

        elif action == "broadcast_info":
            from config import STICKER_ID
            count = db.db_fetchone("SELECT COUNT(*) FROM users WHERE status = 'approved'")[0]
            print(json.dumps({"ok": True, "recipients_count": count, "default_sticker_id": STICKER_ID or ""}))

        elif action == "broadcast_send":
            from config import API_TOKEN
            if not API_TOKEN:
                print(json.dumps({"ok": False, "error": "API_TOKEN is not configured"}))
                return
            message_type = params.get("message_type", "text")
            text = params.get("text", "")
            parse_mode = params.get("parse_mode")
            sticker_id = params.get("sticker_id", "")
            users = db.db_fetchall("SELECT user_id FROM users WHERE status = 'approved'")
            if not users:
                print(json.dumps({"ok": True, "sent": 0, "blocked": 0, "failed": 0, "total": 0}))
                return
            success = failed = blocked = 0
            total = len(users)
            import time
            for (user_id,) in users:
                try:
                    if message_type == "sticker":
                        url = f"https://api.telegram.org/bot{API_TOKEN}/sendSticker"
                        data_dict = {'chat_id': user_id, 'sticker': sticker_id}
                    else:
                        url = f"https://api.telegram.org/bot{API_TOKEN}/sendMessage"
                        data_dict = {'chat_id': user_id, 'text': text}
                        if parse_mode:
                            data_dict['parse_mode'] = 'Markdown' if parse_mode.upper() == 'MARKDOWN' else parse_mode
                    data = urllib.parse.urlencode(data_dict).encode('utf-8')
                    req = urllib.request.Request(url, data=data)
                    with urllib.request.urlopen(req, timeout=5) as resp:
                        resp_data = json.loads(resp.read().decode('utf-8'))
                        if resp_data.get("ok"):
                            success += 1
                        else:
                            failed += 1
                except urllib.error.HTTPError as e:
                    try:
                        err_data = json.loads(e.read().decode('utf-8'))
                        description = err_data.get("description", "").lower()
                        if "forbidden" in description or "blocked" in description or "chat not found" in description:
                            blocked += 1
                        else:
                            failed += 1
                    except Exception:
                        failed += 1
                except Exception:
                    failed += 1
                time.sleep(0.05)
            print(json.dumps({"ok": True, "sent": success, "blocked": blocked, "failed": failed, "total": total}))

        elif action == "save_default_pricing":
            price_inr = float(params["default_price_inr"])
            price_stars = int(params["default_price_stars"])
            price_paise = int(price_inr * 100)
            db.db_execute(
                """
                INSERT INTO payment_config (key, value_int)
                VALUES ('default_folder_price_paise', %s)
                ON CONFLICT (key) DO UPDATE SET value_int = EXCLUDED.value_int
                """,
                (price_paise,)
            )
            db.db_execute(
                """
                INSERT INTO payment_config (key, value_int)
                VALUES ('default_folder_price_stars', %s)
                ON CONFLICT (key) DO UPDATE SET value_int = EXCLUDED.value_int
                """,
                (price_stars,)
            )
            print(json.dumps({"ok": True}))
            
        elif action == "get_activity_log":
            limit = int(params.get("limit", 100))
            offset = int(params.get("offset", 0))
            rows = db.db_fetchall(
                """
                SELECT id, action, target_type, target_id, detail,
                       created_at AT TIME ZONE 'Asia/Kolkata' as created_at
                FROM admin_activity_log
                ORDER BY created_at DESC
                LIMIT %s OFFSET %s
                """,
                (limit, offset)
            )
            total = db.db_fetchone(
                "SELECT COUNT(*) FROM admin_activity_log"
            )[0]
            log_entries = []
            for r in (rows or []):
                log_entries.append({
                    "id": r[0], "action": r[1],
                    "target_type": r[2], "target_id": r[3],
                    "detail": r[4],
                    "created_at": r[5].isoformat() if r[5] else None
                })
            print(json.dumps({"ok": True, "data": {
                "log": log_entries, "total": total,
                "limit": limit, "offset": offset
            }}))

        elif action == "log_activity":
            db.db_execute(
                """
                INSERT INTO admin_activity_log (action, target_type, target_id, detail)
                VALUES (%s, %s, %s, %s)
                """,
                (
                    params.get("action_name", ""),
                    params.get("target_type", ""),
                    str(params.get("target_id", "")),
                    params.get("detail", "")
                )
            )
            print(json.dumps({"ok": True}))

        elif action == "bulk_user_action":
            bulk_action = params.get("bulk_action", "")
            user_ids = params.get("user_ids", [])
            if not isinstance(user_ids, list) or not user_ids:
                print(json.dumps({"ok": False, "error": "No user IDs provided"}))
            else:
                updated = 0
                if bulk_action == "approve":
                    db.db_execute(
                        "UPDATE users SET status = 'approved' WHERE user_id = ANY(%s::bigint[])",
                        (user_ids,)
                    )
                    updated = len(user_ids)
                elif bulk_action == "ban":
                    db.db_execute(
                        "UPDATE users SET status = 'banned' WHERE user_id = ANY(%s::bigint[])",
                        (user_ids,)
                    )
                    updated = len(user_ids)
                elif bulk_action == "grant_premium":
                    days = int(params.get("days", 30))
                    db.db_execute(
                        """UPDATE users SET premium = TRUE,
                           premium_expiration = NOW() + (%s || ' days')::interval
                           WHERE user_id = ANY(%s::bigint[])""",
                        (str(days), user_ids,)
                    )
                    updated = len(user_ids)
                elif bulk_action == "revoke_premium":
                    db.db_execute(
                        "UPDATE users SET premium = FALSE, premium_expiration = NULL WHERE user_id = ANY(%s::bigint[])",
                        (user_ids,)
                    )
                    updated = len(user_ids)
                else:
                    print(json.dumps({"ok": False, "error": f"Unknown bulk action: {bulk_action}"}))
                    return
                # Log this bulk action
                db.db_execute(
                    "INSERT INTO admin_activity_log (action, target_type, target_id, detail) VALUES (%s, %s, %s, %s)",
                    (f"bulk_{bulk_action}", "users", ",".join(str(u) for u in user_ids[:5]) + ("..." if len(user_ids) > 5 else ""),
                     f"{updated} users affected")
                )
                print(json.dumps({"ok": True, "updated": updated}))

        elif action == "get_tree":
            """Return the full hierarchical content tree for the dashboard."""
            import utils.database as _dbmod
            # Fetch all categories with parent_id
            cats_raw = db.db_fetchall("""
                SELECT id, name, emoji, sort_order, parent_id
                FROM categories
                ORDER BY sort_order, name
            """)
            # Fetch all folders with counts
            folders_raw = db.db_fetchall("""
                SELECT f.id, f.name, COALESCE(f.emoji,'📁'), f.parent_id, f.category_id,
                       f.premium, f.admin_approval, f.download_count,
                       COUNT(fi.id) AS file_count
                FROM folders f
                LEFT JOIN files fi ON fi.folder_id = f.id
                GROUP BY f.id
                ORDER BY COALESCE(f.sort_order,0), f.name
            """)
            cats = [
                {"id": r[0], "name": r[1], "emoji": r[2], "sort_order": r[3], "parent_id": r[4]}
                for r in (cats_raw or [])
            ]
            folders = [
                {
                    "id": r[0], "name": r[1], "emoji": r[2],
                    "parent_id": r[3], "category_id": r[4],
                    "premium": r[5], "admin_approval": r[6],
                    "download_count": r[7], "file_count": r[8],
                    "type": "premium" if r[5] else ("paid" if r[6] else "free"),
                }
                for r in (folders_raw or [])
            ]
            print(json.dumps({"ok": True, "data": {"categories": cats, "folders": folders}}, cls=DateTimeEncoder))

        elif action == "folder_create":
            """Create a new folder node from the dashboard (virtual, for content organisation)."""
            name = params.get("name", "").strip()
            if not name:
                print(json.dumps({"ok": False, "error": "Folder name is required"}))
                return
            if db.db_fetchone("SELECT id FROM folders WHERE name = %s", (name,)):
                print(json.dumps({"ok": False, "error": "A folder with this name already exists"}))
                return
            parent_id = params.get("parent_id")
            if parent_id is not None and parent_id != "":
                parent_id = int(parent_id)
            else:
                parent_id = None
            category_id = params.get("category_id")
            if category_id is not None and category_id != "":
                category_id = int(category_id)
            else:
                category_id = None
            emoji = params.get("emoji", "📁").strip() or "📁"
            description = params.get("description", "").strip() or None
            folder_type = params.get("type", "free")
            is_premium = folder_type == "premium"
            is_paid = folder_type == "paid"
            db.db_execute(
                """
                INSERT INTO folders (name, parent_id, category_id, emoji, description, premium, admin_approval)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (name, parent_id, category_id, emoji, description, is_premium, is_paid)
            )
            new_row = db.db_fetchone("SELECT id FROM folders WHERE name = %s", (name,))
            print(json.dumps({"ok": True, "folder_id": new_row[0] if new_row else None}))

        elif action == "move_node":
            """Reparent a folder or category. Prevents cycles."""
            node_type = params.get("type")   # 'folder' | 'category'
            node_id   = int(params.get("id"))
            new_parent_id = params.get("parent_id")
            if new_parent_id is not None and new_parent_id != "":
                new_parent_id = int(new_parent_id)
            else:
                new_parent_id = None

            if node_type == "folder":
                import utils.database as _dbmod
                if new_parent_id is not None and _dbmod.has_folder_cycle(node_id, new_parent_id):
                    print(json.dumps({"ok": False, "error": "Circular parent reference detected."}))
                    return
                db.db_execute("UPDATE folders SET parent_id = %s WHERE id = %s", (new_parent_id, node_id))
                print(json.dumps({"ok": True}))
            elif node_type == "category":
                if new_parent_id == node_id:
                    print(json.dumps({"ok": False, "error": "A category cannot be its own parent."}))
                    return
                db.db_execute("UPDATE categories SET parent_id = %s WHERE id = %s", (new_parent_id, node_id))
                print(json.dumps({"ok": True}))
            else:
                print(json.dumps({"ok": False, "error": f"Unknown node type: {node_type}"}))

        else:
            print(json.dumps({"ok": False, "error": f"Unknown action: {action}"}))
            
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}))

if __name__ == "__main__":
    main()
