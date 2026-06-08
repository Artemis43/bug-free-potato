# 💳 Razorpay Webhook Service

Standalone FastAPI microservice that receives Razorpay payment events and
activates premium / paid-folder access for users of the Medical Content Telegram Bot.

---

## Architecture

```
Razorpay
  │
  │  POST /webhook  (payment_link.paid)
  ▼
razorpay_webhook/      ← this service
  ├── Verify HMAC-SHA256 signature
  ├── Return 200 OK immediately
  └── BackgroundTask:
        ├── Check idempotency (no duplicate processing)
        ├── Write to shared PostgreSQL DB
        └── Send Telegram notification via Bot API (HTTP)
                             ▲
                    Shared PostgreSQL DB
                             │
                    Telegram Bot (aiogram)  ← reads DB on /start etc.
```

**Key properties:**
- Runs as a separate process/container — zero coupling to the bot
- Shares the same PostgreSQL database — bot sees changes instantly  
- Communicates with Telegram directly via HTTP — no shared event loop
- Idempotent — Razorpay may retry; duplicate events are safely ignored
- Returns `200 OK` in <50ms; processing happens in the background

---

## Files

| File | Purpose |
|------|---------|
| `app.py` | FastAPI application — HTTP routes, signature verification |
| `handlers.py` | Business logic for each Razorpay event |
| `database.py` | DB helpers (mirrors bot's `utils/database.py`) |
| `telegram.py` | Thin Telegram Bot API client (httpx, no aiogram) |
| `config.py` | Config from env vars with startup validation |
| `requirements.txt` | Pinned Python dependencies |
| `.env.example` | All environment variables documented |
| `Dockerfile` | Multi-stage production Docker image |
| `docker-compose.yml` | Single-service compose for standalone deployment |

---

## Quick Start (Local)

```bash
cd razorpay_webhook

# 1. Create virtual environment
python -m venv venv
source venv/bin/activate     # Windows: venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure
cp .env.example .env
# Edit .env — fill in all required values

# 4. Run (development — auto-reload)
uvicorn app:app --reload --port 8000

# 5. Test health
curl http://localhost:8000/health
```

---

## Deployment

### Option A — Docker (recommended)

```bash
cd razorpay_webhook
cp .env.example .env
# fill in .env values

docker compose --env-file .env up -d
docker compose logs -f
```

### Option B — Render / Railway (PaaS)

1. Push this subfolder as a separate service or configure the root path:
   - **Build command:** `pip install -r razorpay_webhook/requirements.txt`
   - **Start command:** `uvicorn razorpay_webhook.app:app --host 0.0.0.0 --port $PORT`
   - Or just point the service root at `razorpay_webhook/`

2. Set all env vars from `.env.example` in the platform's dashboard.

### Option C — Systemd (VPS)

```ini
# /etc/systemd/system/razorpay-webhook.service
[Unit]
Description=Razorpay Webhook Service
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/opt/razorpay_webhook
EnvironmentFile=/opt/razorpay_webhook/.env
ExecStart=/opt/razorpay_webhook/venv/bin/uvicorn app:app \
          --host 0.0.0.0 --port 8000 --workers 2 --proxy-headers
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now razorpay-webhook
sudo journalctl -u razorpay-webhook -f
```

---

## Razorpay Dashboard Configuration

1. Go to **Settings → Webhooks → Create New Webhook**
2. Set the **URL** to:
   ```
   https://<your-domain>/webhook
   ```
3. Under **Events**, enable **only**: `payment_link.paid`
4. Copy the **Secret** and set it as `RAZORPAY_WEBHOOK_SECRET` in `.env`
5. Save

> **⚠️ Important:** Always set `RAZORPAY_WEBHOOK_SECRET` in production.
> Without it, anyone can spoof payment events.

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Liveness ping (for UptimeRobot) |
| `GET` | `/health` | Readiness probe (DB + Razorpay check) |
| `POST` | `/webhook` | Razorpay webhook receiver |
| `GET` | `/payment/success` | Post-payment redirect page |
| `GET` | `/docs` | Swagger UI (dev mode only) |

---

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `RAZORPAY_KEY_ID` | ✅ | Razorpay API key ID |
| `RAZORPAY_KEY_SECRET` | ✅ | Razorpay API key secret |
| `RAZORPAY_WEBHOOK_SECRET` | ⚠️ | Webhook signing secret (required in prod) |
| `BOT_TOKEN` | ✅ | Telegram bot token |
| `ADMINS` | ✅ | Comma-separated admin Telegram IDs |
| `DB_STRING` | ✅ | PostgreSQL connection string (same as bot) |
| `ADMIN_GROUP_ID` | Optional | Group for admin notifications |
| `ADMIN_CONTACT` | Optional | Admin handle shown to users |
| `WEBHOOK_PORT` | Optional | Server port (default: 8000) |
| `APP_ENV` | Optional | `production` disables Swagger UI |

---

## Supported Events

| Event | Action |
|-------|--------|
| `payment_link.paid` (premium) | Activates premium, sets expiry, notifies user |
| `payment_link.paid` (folder) | Creates approval row, notifies admin + user |

All other events are logged and ignored.

---

## Security

- **Signature verification** — every incoming request is validated with HMAC-SHA256 using the Razorpay webhook secret  
- **Idempotency** — payment IDs are stored; duplicate events from Razorpay retries are silently dropped  
- **Immediate 200** — response is sent before processing starts, preventing Razorpay timeouts  
- **Non-root Docker** — container runs as UID 1001  
- **No Swagger in prod** — `/docs` is disabled when `APP_ENV=production`

---

## Testing

```bash
# Test with a mock payload (skip signature verification in dev mode)
curl -X POST http://localhost:8000/webhook \
  -H "Content-Type: application/json" \
  -d '{
    "event": "payment_link.paid",
    "payload": {
      "payment_link": {
        "entity": {
          "id": "plink_test_001",
          "notes": {
            "user_id": "123456789",
            "order_type": "premium",
            "ref_id": "1"
          }
        }
      },
      "payment": {
        "entity": {
          "id": "pay_test_001",
          "amount": 9900
        }
      }
    }
  }'
```
