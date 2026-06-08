# Medical Content Telegram Bot 🤖

A self-hosted Telegram bot that manages a folder/file hierarchy for medical content.
Files are stored in a private Telegram archive channel and distributed to verified users.

---

## Features

- ✅ Verified-user access (admin approval flow)
- 📁 Folder management — create, rename, delete
- ⬇️ File download with per-user cooldowns
- ⭐ Premium users with faster downloads
- 💰 Paid (admin-approval) folders for one-time access
- 📢 Admin broadcast to all approved users
- 🔒 ForcedSub — users must join required channels
- 🐘 PostgreSQL backend (Supabase-compatible)
- 🚀 Webhook-based deployment (Render / Railway / VPS)

---

## Quick Start

### 1 — Clone the repo

```bash
git clone https://github.com/your-org/bug-free-potato.git
cd bug-free-potato
```

### 2 — Configure environment variables

```bash
cp .env.example .env
```

Open `.env` and fill in every value (see [Environment Variables](#environment-variables) below).

### 3 — Install dependencies

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 4 — Run locally

```bash
python main.py
```

> The bot starts in **polling** mode when `HOST_URL` is set but the webhook is
> not reachable from the internet.  For full webhook mode, deploy to a
> public HTTPS host (see Deployment).

---

## Environment Variables

Copy `.env.example` → `.env` and fill in the values.

| Variable | Required | Description |
|---|---|---|
| `API_TOKEN` | ✅ | Bot token from [@BotFather](https://t.me/BotFather) |
| `ADMINS` | ✅ | Comma-separated admin Telegram user IDs (e.g. `111111,222222`). First ID receives notifications. |
| `CHANNEL` | ✅ | Numeric ID or `@username` of the private storage channel |
| `DB_STRING` | ✅ | Full PostgreSQL connection string (psycopg2 URI format) |
| `HOST_URL` | ✅ | Public HTTPS base URL of your deployment (no trailing slash) |
| `SUBSCRIPTION` | — | Comma-separated channels users must join before using the bot |
| `STICKER` | — | `file_id` of a sticker sent on `/start` |
| `KEEP_ALIVE_PORT` | — | Port for the Flask health server (default: `4343`) |
| `LOG_LEVEL` | — | Python log level: `DEBUG`, `INFO`, `WARNING`, `ERROR` (default: `INFO`) |

---

## Bot Commands

### User commands

| Command | Description |
|---|---|
| `/start` | Register and open the folder list |
| `/help` | Usage instructions |
| `/about` | About the bot |
| `/download <folder>` | Download all files in a folder |

### Admin commands

| Command | Description |
|---|---|
| `/newfolder <name> [PREMIUM] [PAID]` | Create a folder |
| `/renamefolder <old>,<new>` | Rename a folder |
| `/deletefolder <name>` | Delete a folder and its channel messages |
| `/setfolder <id> <0\|1>` | Toggle a folder's premium flag |
| `/setuser <id> <on\|off\|days:N>` | Grant / revoke user premium |
| `/caption <custom\|append> <text>` | Set global file caption |
| `/broadcast <text>` | Send a message to all approved users |
| `/list` | View all folders, users, and premium stats |
| `/approve_<id>` | Approve a new user (from notification) |
| `/reject_<id>` | Reject a new user (from notification) |
| `/approve <user_id> <folder_id>` | Approve a paid-folder download request |
| `/reject <user_id> <folder_id>` | Reject a paid-folder download request |
| `/forcedsyncdb` | Trigger a manual database sync |
| `/stop` | Gracefully shut down the bot |

---

## Deployment

### Render

1. Create a new **Web Service**, connect this repository.
2. Set **Build Command**: `pip install -r requirements.txt`
3. Set **Start Command**: `python main.py`
4. Add all environment variables in the Render dashboard (**Environment** tab).
5. The `HOST_URL` should be `https://<your-app>.onrender.com`.

### Railway

1. Create a project, connect this repository.
2. Add a **PostgreSQL** plugin (or use Supabase).
3. Set all environment variables in the **Variables** tab.
4. Set `HOST_URL` to the generated Railway domain.

### Docker (self-hosted VPS)

```bash
# Build
docker build -t medbot .

# Run (pass your .env file)
docker run -d --env-file .env --name medbot medbot
```

Or with `docker-compose`:

```bash
docker compose up -d
```

---

## Project Structure

```
.
├── config.py                  # Loads and validates all env vars
├── keep_alive.py              # Flask server for uptime pings
├── main.py                    # Bot entry-point, handler registration
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── .env.example               # ← copy this to .env
├── .gitignore
├── .dockerignore
├── handlers/
│   ├── about_help.py          # /about, /help
│   ├── broadcast.py           # /broadcast
│   ├── caption.py             # /caption
│   ├── document.py            # File upload handler
│   ├── download.py            # /download, /approve, /reject
│   ├── folder.py              # /newfolder, /renamefolder, /deletefolder
│   ├── getlist.py             # /list
│   ├── setpremium.py          # /setfolder, /setuser
│   ├── start.py               # /start, approve_user, reject_user
│   ├── stop.py                # /stop
│   └── sync.py                # /forcedsyncdb
├── middlewares/
│   └── authorization.py       # is_private_chat, is_user_member
└── utils/
    ├── database.py            # PostgreSQL helpers
    ├── helpers.py             # Shared utilities & admin notifications
    └── webhook.py             # on_startup / on_shutdown hooks
```

---

## License

MIT
