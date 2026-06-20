"""
utils/catalog.py — Telegra.ph Content Catalog Generator.

Builds and publishes a browsable Telegra.ph page containing all categories
and folders, each with a Telegram deep link back to the bot.

Auto-update is rate-limited to once per 5 minutes to avoid API spam.
The Telegra.ph access token and page path are persisted in payment_config.
"""
import json
import logging
import time
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime

from config import BOT_NAME

log = logging.getLogger(__name__)

_TELEGRAPH_API = "https://api.telegra.ph"
_MIN_REGEN_INTERVAL = 300  # 5 minutes between auto-regenerations


def _telegraph_post(endpoint: str, payload: dict) -> dict:
    """POST to Telegra.ph API. Returns parsed JSON response."""
    data = json.dumps(payload).encode("utf-8")
    url = f"{_TELEGRAPH_API}/{endpoint}"
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as e:
        log.error(f"[Catalog] Telegra.ph API error at {endpoint}: {e}")
        raise


def _get_or_create_account() -> str:
    """Return existing Telegra.ph access token, or create a new account."""
    from utils.database import get_catalog_config, set_catalog_config

    token = get_catalog_config("telegraph_access_token")
    if token:
        return token

    # Create a new Telegra.ph account
    try:
        resp = _telegraph_post("createAccount", {
            "short_name": BOT_NAME[:32],
            "author_name": BOT_NAME[:128],
        })
        if resp.get("ok"):
            token = resp["result"]["access_token"]
            set_catalog_config("telegraph_access_token", token)
            log.info("[Catalog] Created new Telegra.ph account.")
            return token
        else:
            raise RuntimeError(f"Telegra.ph createAccount failed: {resp}")
    except Exception as e:
        log.error(f"[Catalog] Could not create Telegra.ph account: {e}")
        raise


def _build_nodes(bot_username: str) -> list:
    """
    Build Telegra.ph Node[] from all categories and their folders.
    Returns a list of Telegra.ph content nodes.
    """
    from utils.database import db_fetchall

    # Fetch categories ordered by sort_order then name
    categories = db_fetchall(
        "SELECT id, name, emoji FROM categories ORDER BY sort_order, name"
    )

    # Fetch uncategorized folders
    uncat_folders = db_fetchall("""
        SELECT f.id, f.name, f.premium, f.admin_approval, COUNT(fi.id) AS file_count
        FROM folders f
        LEFT JOIN files fi ON fi.folder_id = f.id
        WHERE f.category_id IS NULL AND f.parent_id IS NULL
        GROUP BY f.id
        ORDER BY f.name
    """)

    nodes = []
    total_folders = 0
    total_files = 0

    # Clean introduction block
    nodes.append({
        "tag": "blockquote",
        "children": [
            {"tag": "strong", "children": [f"⚡ Welcome to the {BOT_NAME} Catalog!"]},
            {"tag": "br"},
            "Browse our organized library of files and resources. Click any folder title below to open it directly in the Telegram bot and download its content instantly."
        ]
    })
    nodes.append({"tag": "hr"})

    def folder_node(fid, fname, premium, paid, fcount, botu):
        """Build a list item node for a single folder."""
        nonlocal total_folders, total_files
        total_folders += 1
        total_files += fcount or 0
        
        deep_link = f"https://t.me/{botu}?start=dl_{fid}"
        
        children = [
            {"tag": "strong", "children": [
                {"tag": "a", "attrs": {"href": deep_link}, "children": [fname]}
            ]}
        ]
        
        # Add modern badges inside code tags
        if premium:
            children.extend([" ", {"tag": "code", "children": ["⭐ Premium"]}])
        elif paid:  # paid maps to f.admin_approval in this legacy code
            children.extend([" ", {"tag": "code", "children": ["🔑 Requires Approval"]}])
            
        # Add file count divider and stats
        children.extend([
            " · ",
            {"tag": "em", "children": [f"{fcount} file{'s' if fcount != 1 else ''}"]}
        ])
        
        return {
            "tag": "li",
            "children": children
        }

    first = True

    for cat_id, cat_name, emoji in categories:
        folders = db_fetchall("""
            SELECT f.id, f.name, f.premium, f.admin_approval, COUNT(fi.id) AS file_count
            FROM folders f
            LEFT JOIN files fi ON fi.folder_id = f.id
            WHERE f.category_id = %s AND f.parent_id IS NULL
            GROUP BY f.id
            ORDER BY f.name
        """, (cat_id,))

        if not folders:
            continue

        if not first:
            nodes.append({"tag": "hr"})
        first = False

        # Category header
        nodes.append({"tag": "h3", "children": [f"{emoji} {cat_name}"]})
        
        li_nodes = []
        for fid, fname, premium, paid, fcount in folders:
            li_nodes.append(folder_node(fid, fname, premium, paid, fcount or 0, bot_username))
            
        nodes.append({"tag": "ul", "children": li_nodes})

    # Uncategorized section
    if uncat_folders:
        if not first:
            nodes.append({"tag": "hr"})
        first = False
        
        nodes.append({"tag": "h3", "children": ["📦 Uncategorized"]})
        
        li_nodes = []
        for fid, fname, premium, paid, fcount in uncat_folders:
            li_nodes.append(folder_node(fid, fname, premium, paid, fcount or 0, bot_username))
            
        nodes.append({"tag": "ul", "children": li_nodes})

    # Summary footer
    nodes.append({"tag": "hr"})
    now_str = datetime.now().strftime("%d %b %Y at %H:%M")
    nodes.append({
        "tag": "aside",
        "children": [
            f"📊 Total: {total_folders} folder{'s' if total_folders != 1 else ''} · {total_files} file{'s' if total_files != 1 else ''} | 🕒 Last updated: {now_str} (UTC)"
        ]
    })

    return nodes


async def generate_catalog(bot_username: str, force: bool = False) -> str:
    """
    Build and publish (or update) the Telegra.ph catalog page.

    Parameters:
        bot_username: The bot's @username (without @) for deep links.
        force: If True, skip the rate-limit check and regenerate immediately.

    Returns:
        The full Telegra.ph URL of the published catalog page.
    """
    from utils.database import get_catalog_config, set_catalog_config

    # Rate-limit check (unless forced by admin /catalog command)
    if not force:
        last_gen_str = get_catalog_config("catalog_last_generated")
        if last_gen_str:
            try:
                last_ts = float(last_gen_str)
                if time.time() - last_ts < _MIN_REGEN_INTERVAL:
                    # Still within cooldown — return cached URL
                    page_path = get_catalog_config("telegraph_page_path")
                    if page_path:
                        return f"https://telegra.ph/{page_path}"
            except (ValueError, TypeError):
                pass

    try:
        token = _get_or_create_account()
        nodes = _build_nodes(bot_username)

        if not nodes:
            return ""

        page_path = get_catalog_config("telegraph_page_path")
        title = f"{BOT_NAME} — Full Content Catalog"

        if page_path:
            # Update the existing page
            resp = _telegraph_post("editPage", {
                "access_token": token,
                "path": page_path,
                "title": title,
                "content": nodes,
                "author_name": BOT_NAME,
            })
        else:
            # Create a new page
            resp = _telegraph_post("createPage", {
                "access_token": token,
                "title": title,
                "content": nodes,
                "author_name": BOT_NAME,
                "return_content": False,
            })

        if not resp.get("ok"):
            log.error(f"[Catalog] Telegra.ph page operation failed: {resp}")
            return ""

        result = resp["result"]
        new_path = result["path"]
        url = result["url"]

        set_catalog_config("telegraph_page_path", new_path)
        set_catalog_config("catalog_last_generated", str(time.time()))

        log.info(f"[Catalog] Published catalog: {url}")
        return url

    except Exception as e:
        log.error(f"[Catalog] Failed to generate catalog: {e}")
        return ""


def get_catalog_url() -> str:
    """Return the current catalog URL without regenerating."""
    from utils.database import get_catalog_config
    page_path = get_catalog_config("telegraph_page_path")
    if page_path:
        return f"https://telegra.ph/{page_path}"
    return ""


def schedule_catalog_update(bot_username: str) -> None:
    """
    Fire-and-forget: schedule an async catalog regeneration.
    Called from folder/category mutation handlers.
    Respects the 5-minute rate limit.
    """
    import asyncio
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(_async_update(bot_username))
    except Exception as e:
        log.debug(f"[Catalog] Could not schedule update: {e}")


async def _async_update(bot_username: str) -> None:
    """Async wrapper for catalog generation (runs in background task)."""
    try:
        url = await generate_catalog(bot_username)
        if url:
            log.info(f"[Catalog] Auto-updated: {url}")
    except Exception as e:
        log.warning(f"[Catalog] Auto-update failed: {e}")
