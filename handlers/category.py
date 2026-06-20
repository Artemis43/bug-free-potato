"""
handlers/category.py — Admin commands for managing content categories.

Commands:
    /newcategory  <emoji> <name>         Create a new category
    /renamecategory <old>,<new>          Rename a category
    /deletecategory <name>               Delete category (folders → Uncategorized)
    /setcategoryemoji <name>,<emoji>     Change the category's emoji
    /movefolder <folder>,<category>      Assign a folder to a category
    /removefoldercategory <folder>       Remove a folder from its category
    /categories                          List all categories with folder counts
    /reordercategory <name>,<position>   Change a category's display order
"""
import logging
import unicodedata

from aiogram import Router, types
from aiogram.enums import ParseMode

from config import ADMIN_IDS
from middlewares.authorization import is_private_chat
from utils.database import db_execute, db_fetchall, db_fetchone
from utils.helpers import esc

router = Router()
log = logging.getLogger(__name__)


def _is_emoji_char(ch: str) -> bool:
    """Return True if ch is an emoji or special symbol character."""
    try:
        cat = unicodedata.category(ch)
        # So is So = Symbol, other (covers most emoji)
        if cat in ("So", "Sm", "Sk"):
            return True
        # Also check unicode blocks for regional indicators / dingbats
        cp = ord(ch)
        return (
            0x1F300 <= cp <= 0x1FAFF  # Misc symbols, emoticons, etc.
            or 0x2600 <= cp <= 0x27BF   # Misc symbols, dingbats
            or 0xFE00 <= cp <= 0xFE0F   # Variation selectors
            or 0x1F1E0 <= cp <= 0x1F1FF  # Regional indicators
        )
    except Exception:
        return False


def _extract_emoji_and_name(text: str):
    """
    Try to extract a leading emoji from the text.
    Returns (emoji, name). If no emoji found, returns ('📁', text).
    """
    text = text.strip()
    if not text:
        return '📁', ''

    # Walk through codepoints: if the first grapheme cluster is emoji-like, use it
    i = 0
    emoji_chars = []
    while i < len(text):
        ch = text[i]
        # Consume the character and any following variation selectors / ZWJ
        emoji_chars.append(ch)
        i += 1
        while i < len(text) and (
            unicodedata.category(text[i]) in ('Mn', 'Me', 'Cf')
            or ord(text[i]) in range(0xFE00, 0xFE10)  # variation selectors
        ):
            emoji_chars.append(text[i])
            i += 1
        break  # only take first grapheme

    first = ''.join(emoji_chars)
    if _is_emoji_char(first[0]):
        name = text[len(first):].strip()
        return first, name

    return '📁', text


# ─────────────────────────────────────────────────────────────────────────────
# /newcategory <emoji?> <name>
# ─────────────────────────────────────────────────────────────────────────────

async def new_category(message: types.Message):
    """/newcategory — create a content category."""
    if not is_private_chat(message):
        return
    if str(message.from_user.id) not in ADMIN_IDS:
        await message.reply("You are not authorized.")
        return

    args = (message.text.split(None, 1)[1] if message.text and len(message.text.split(None, 1)) > 1 else '').strip()
    if not args:
        await message.reply(
            "Usage: <code>/newcategory [emoji] &lt;name&gt;</code>\n\n"
            "Examples:\n"
            "<code>/newcategory 🧬 Anatomy</code>\n"
            "<code>/newcategory Pharmacology</code>",
            parse_mode=ParseMode.HTML
        )
        return

    emoji, name = _extract_emoji_and_name(args)
    if not name:
        await message.reply("Please provide a category name.", parse_mode=ParseMode.HTML)
        return

    if db_fetchone("SELECT id FROM categories WHERE name = %s", (name,)):
        await message.reply(
            f"❌ A category named <b>{esc(name)}</b> already exists.",
            parse_mode=ParseMode.HTML
        )
        return

    db_execute(
        "INSERT INTO categories (name, emoji) VALUES (%s, %s)",
        (name, emoji)
    )

    await message.reply(
        f"✅ <b>Category Created</b>\n\n{emoji} {esc(name)}\n\n"
        f"Use <code>/movefolder &lt;folder&gt;,{esc(name)}</code> to assign folders.",
        parse_mode=ParseMode.HTML
    )
    _trigger_catalog_update()


# ─────────────────────────────────────────────────────────────────────────────
# /renamecategory <old>,<new>
# ─────────────────────────────────────────────────────────────────────────────

async def rename_category(message: types.Message):
    """/renamecategory old,new — rename a category."""
    if not is_private_chat(message):
        return
    if str(message.from_user.id) not in ADMIN_IDS:
        await message.reply("You are not authorized.")
        return

    args = message.text.split(None, 1)[1] if message.text and len(message.text.split(None, 1)) > 1 else ''
    parts = [p.strip() for p in args.split(',', 1)]
    if len(parts) != 2 or not parts[0] or not parts[1]:
        await message.reply(
            "Usage: <code>/renamecategory &lt;old name&gt;,&lt;new name&gt;</code>",
            parse_mode=ParseMode.HTML
        )
        return

    old_name, new_name = parts

    row = db_fetchone("SELECT id FROM categories WHERE name = %s", (old_name,))
    if not row:
        await message.reply(f"❌ Category <b>{esc(old_name)}</b> not found.", parse_mode=ParseMode.HTML)
        return

    if db_fetchone("SELECT id FROM categories WHERE name = %s", (new_name,)):
        await message.reply(f"❌ A category named <b>{esc(new_name)}</b> already exists.", parse_mode=ParseMode.HTML)
        return

    db_execute("UPDATE categories SET name = %s WHERE id = %s", (new_name, row[0]))
    await message.reply(
        f"✅ Renamed: <b>{esc(old_name)}</b> → <b>{esc(new_name)}</b>",
        parse_mode=ParseMode.HTML
    )
    _trigger_catalog_update()


# ─────────────────────────────────────────────────────────────────────────────
# /deletecategory <name>
# ─────────────────────────────────────────────────────────────────────────────

async def delete_category(message: types.Message):
    """/deletecategory — delete a category; its folders become Uncategorized."""
    if not is_private_chat(message):
        return
    if str(message.from_user.id) not in ADMIN_IDS:
        await message.reply("You are not authorized.")
        return

    name = (message.text.split(None, 1)[1].strip() if message.text and len(message.text.split(None, 1)) > 1 else '')
    if not name:
        await message.reply(
            "Usage: <code>/deletecategory &lt;name&gt;</code>",
            parse_mode=ParseMode.HTML
        )
        return

    row = db_fetchone("SELECT id FROM categories WHERE name = %s", (name,))
    if not row:
        await message.reply(f"❌ Category <b>{esc(name)}</b> not found.", parse_mode=ParseMode.HTML)
        return

    cat_id = row[0]
    folder_count = db_fetchone("SELECT COUNT(*) FROM folders WHERE category_id = %s", (cat_id,))[0]

    # ON DELETE SET NULL is on the FK, but let's be explicit
    db_execute("UPDATE folders SET category_id = NULL WHERE category_id = %s", (cat_id,))
    db_execute("DELETE FROM categories WHERE id = %s", (cat_id,))

    await message.reply(
        f"✅ Category <b>{esc(name)}</b> deleted.\n"
        f"📦 {folder_count} folder(s) moved to Uncategorized.",
        parse_mode=ParseMode.HTML
    )
    _trigger_catalog_update()


# ─────────────────────────────────────────────────────────────────────────────
# /setcategoryemoji <name>,<emoji>
# ─────────────────────────────────────────────────────────────────────────────

async def set_category_emoji(message: types.Message):
    """/setcategoryemoji name,🎯 — change the emoji for a category."""
    if not is_private_chat(message):
        return
    if str(message.from_user.id) not in ADMIN_IDS:
        await message.reply("You are not authorized.")
        return

    args = message.text.split(None, 1)[1] if message.text and len(message.text.split(None, 1)) > 1 else ''
    parts = [p.strip() for p in args.split(',', 1)]
    if len(parts) != 2 or not parts[0] or not parts[1]:
        await message.reply(
            "Usage: <code>/setcategoryemoji &lt;name&gt;,&lt;emoji&gt;</code>\n\n"
            "Example: <code>/setcategoryemoji Anatomy,🧬</code>",
            parse_mode=ParseMode.HTML
        )
        return

    name, emoji = parts[0], parts[1][:8]  # cap emoji length
    row = db_fetchone("SELECT id FROM categories WHERE name = %s", (name,))
    if not row:
        await message.reply(f"❌ Category <b>{esc(name)}</b> not found.", parse_mode=ParseMode.HTML)
        return

    db_execute("UPDATE categories SET emoji = %s WHERE id = %s", (emoji, row[0]))
    await message.reply(
        f"✅ Category emoji updated: {emoji} <b>{esc(name)}</b>",
        parse_mode=ParseMode.HTML
    )
    _trigger_catalog_update()


# ─────────────────────────────────────────────────────────────────────────────
# /movefolder <folder>,<category>
# ─────────────────────────────────────────────────────────────────────────────

async def move_folder(message: types.Message):
    """/movefolder folder,category — assign a folder to a category."""
    if not is_private_chat(message):
        return
    if str(message.from_user.id) not in ADMIN_IDS:
        await message.reply("You are not authorized.")
        return

    args = message.text.split(None, 1)[1] if message.text and len(message.text.split(None, 1)) > 1 else ''
    parts = [p.strip() for p in args.split(',', 1)]
    if len(parts) != 2 or not parts[0] or not parts[1]:
        await message.reply(
            "Usage: <code>/movefolder &lt;folder name&gt;,&lt;category name&gt;</code>\n\n"
            "Example: <code>/movefolder Human Anatomy,Anatomy</code>\n\n"
            "To remove from a category: <code>/removefoldercategory &lt;folder name&gt;</code>",
            parse_mode=ParseMode.HTML
        )
        return

    folder_name, cat_name = parts

    folder_row = db_fetchone("SELECT id FROM folders WHERE name = %s", (folder_name,))
    if not folder_row:
        await message.reply(f"❌ Folder <b>{esc(folder_name)}</b> not found.", parse_mode=ParseMode.HTML)
        return

    cat_row = db_fetchone("SELECT id, emoji FROM categories WHERE name = %s", (cat_name,))
    if not cat_row:
        await message.reply(
            f"❌ Category <b>{esc(cat_name)}</b> not found.\n"
            "Use <code>/categories</code> to see all categories, or create one with "
            "<code>/newcategory</code>.",
            parse_mode=ParseMode.HTML
        )
        return

    db_execute("UPDATE folders SET category_id = %s WHERE id = %s", (cat_row[0], folder_row[0]))
    await message.reply(
        f"✅ Moved <b>{esc(folder_name)}</b> → {cat_row[1]} <b>{esc(cat_name)}</b>",
        parse_mode=ParseMode.HTML
    )
    _trigger_catalog_update()


# ─────────────────────────────────────────────────────────────────────────────
# /removefoldercategory <folder>
# ─────────────────────────────────────────────────────────────────────────────

async def remove_folder_category(message: types.Message):
    """/removefoldercategory folder — move a folder to Uncategorized."""
    if not is_private_chat(message):
        return
    if str(message.from_user.id) not in ADMIN_IDS:
        await message.reply("You are not authorized.")
        return

    folder_name = (message.text.split(None, 1)[1].strip() if message.text and len(message.text.split(None, 1)) > 1 else '')
    if not folder_name:
        await message.reply(
            "Usage: <code>/removefoldercategory &lt;folder name&gt;</code>",
            parse_mode=ParseMode.HTML
        )
        return

    row = db_fetchone("SELECT id FROM folders WHERE name = %s", (folder_name,))
    if not row:
        await message.reply(f"❌ Folder <b>{esc(folder_name)}</b> not found.", parse_mode=ParseMode.HTML)
        return

    db_execute("UPDATE folders SET category_id = NULL WHERE id = %s", (row[0],))
    await message.reply(
        f"✅ <b>{esc(folder_name)}</b> moved to 📦 Uncategorized.",
        parse_mode=ParseMode.HTML
    )
    _trigger_catalog_update()


# ─────────────────────────────────────────────────────────────────────────────
# /categories
# ─────────────────────────────────────────────────────────────────────────────

async def list_categories(message: types.Message):
    """/categories — list all categories with folder counts."""
    if not is_private_chat(message):
        return
    if str(message.from_user.id) not in ADMIN_IDS:
        await message.reply("You are not authorized.")
        return

    rows = db_fetchall("""
        SELECT c.id, c.name, c.emoji, c.sort_order, COUNT(f.id) AS folder_count
        FROM categories c
        LEFT JOIN folders f ON f.category_id = c.id AND f.parent_id IS NULL
        GROUP BY c.id
        ORDER BY c.sort_order, c.name
    """)

    uncat_count = db_fetchone(
        "SELECT COUNT(*) FROM folders WHERE category_id IS NULL AND parent_id IS NULL"
    )[0]

    if not rows and uncat_count == 0:
        await message.reply(
            "📭 No categories yet.\n\nCreate one with <code>/newcategory</code>.",
            parse_mode=ParseMode.HTML
        )
        return

    lines = ["<b>🗂 Content Categories</b>\n"]
    for cat_id, name, emoji, sort_ord, folder_count in rows:
        lines.append(f"  {emoji} <b>{esc(name)}</b> — {folder_count} folder(s) | ID: {cat_id} | Order: {sort_ord}")

    if uncat_count > 0:
        lines.append(f"  📦 <b>Uncategorized</b> — {uncat_count} folder(s)")

    lines.append(
        "\n<i>Commands:</i>\n"
        "<code>/newcategory [emoji] name</code>\n"
        "<code>/renamecategory old,new</code>\n"
        "<code>/deletecategory name</code>\n"
        "<code>/setcategoryemoji name,emoji</code>\n"
        "<code>/movefolder folder,category</code>\n"
        "<code>/reordercategory name,position</code>"
    )

    await message.reply('\n'.join(lines), parse_mode=ParseMode.HTML)


# ─────────────────────────────────────────────────────────────────────────────
# /reordercategory <name>,<position>
# ─────────────────────────────────────────────────────────────────────────────

async def reorder_category(message: types.Message):
    """/reordercategory name,<number> — set sort order (lower = first)."""
    if not is_private_chat(message):
        return
    if str(message.from_user.id) not in ADMIN_IDS:
        await message.reply("You are not authorized.")
        return

    args = message.text.split(None, 1)[1] if message.text and len(message.text.split(None, 1)) > 1 else ''
    parts = [p.strip() for p in args.split(',', 1)]
    if len(parts) != 2 or not parts[0] or not parts[1]:
        await message.reply(
            "Usage: <code>/reordercategory &lt;name&gt;,&lt;position&gt;</code>\n\n"
            "Example: <code>/reordercategory Anatomy,1</code>\n"
            "(Lower numbers appear first)",
            parse_mode=ParseMode.HTML
        )
        return

    name = parts[0]
    try:
        position = int(parts[1])
    except ValueError:
        await message.reply("Position must be an integer.", parse_mode=ParseMode.HTML)
        return

    row = db_fetchone("SELECT id FROM categories WHERE name = %s", (name,))
    if not row:
        await message.reply(f"❌ Category <b>{esc(name)}</b> not found.", parse_mode=ParseMode.HTML)
        return

    db_execute("UPDATE categories SET sort_order = %s WHERE id = %s", (position, row[0]))
    await message.reply(
        f"✅ <b>{esc(name)}</b> is now at position <b>{position}</b>.",
        parse_mode=ParseMode.HTML
    )
    _trigger_catalog_update()


# ─────────────────────────────────────────────────────────────────────────────
# Catalog auto-update hook
# ─────────────────────────────────────────────────────────────────────────────

def _trigger_catalog_update() -> None:
    """Schedule a background catalog regeneration after any category change."""
    import asyncio
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(_do_update())
    except Exception:
        pass


async def _do_update():
    """Async task: fetch bot username then update catalog."""
    try:
        from utils.bot_ref import get_bot
        from utils.catalog import generate_catalog
        bot = get_bot()
        me = await bot.me()
        await generate_catalog(me.username)
    except Exception as e:
        log.debug(f"[Category] Catalog update skipped: {e}")
