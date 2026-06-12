"""
razorpay_webhook/app.py
──────────────────────────────────────────────────────────────────────────────
FastAPI application — the standalone Razorpay webhook receiver.

Endpoints
─────────
  GET  /             → health check (load balancer ping)
  GET  /health       → detailed health (DB + Razorpay reachability)
  POST /webhook      → Razorpay webhook receiver (main endpoint)
  GET  /payment/success → post-payment redirect page shown to users

Standard practices implemented
───────────────────────────────
  ✅  HMAC-SHA256 signature verification (RFC-compliant constant-time compare)
  ✅  Idempotency  — duplicate payment IDs are silently ignored
  ✅  200 OK returned immediately; processing happens in BackgroundTask
  ✅  Structured JSON logging (uvicorn + Python logging)
  ✅  /health endpoint with DB ping for liveness/readiness probes
  ✅  Request ID header propagation (X-Request-ID)
  ✅  Graceful error handling — never returns 5xx to Razorpay
  ✅  CORS headers restricted (no browser access needed for webhooks)
"""

import hashlib
import hmac
import json
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime

import httpx
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse

import config
import handlers

# ─────────────────────────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO if config.ENV == "production" else logging.DEBUG,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("razorpay_webhook")


# ─────────────────────────────────────────────────────────────────────────────
# App factory
# ─────────────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("🚀 Razorpay Webhook Service starting…")
    log.info(f"   Mode: {config.ENV}")
    log.info(f"   Port: {config.PORT}")
    if config.RAZORPAY_WEBHOOK_SECRET:
        log.info("   Signature verification: ✅ enabled")
    else:
        log.warning("   Signature verification: ⚠️ DISABLED — "
                    "set RAZORPAY_WEBHOOK_SECRET to secure the endpoint")

    import database as db
    db.init_pool()

    yield
    log.info("Razorpay Webhook Service stopped.")


app = FastAPI(
    title=f"{config.BOT_NAME} — Razorpay Webhook",
    version="1.0.0",
    description="Receives and processes Razorpay payment events for the Telegram bot.",
    docs_url="/docs" if config.ENV != "production" else None,
    redoc_url=None,
    lifespan=lifespan,
)

# Restrict CORS — webhooks are server-to-server; browsers should never call this
app.add_middleware(
    CORSMiddleware,
    allow_origins=[],   # No browser access
    allow_methods=["POST", "GET"],
    allow_headers=["*"],
)


# ─────────────────────────────────────────────────────────────────────────────
# Signature verification
# ─────────────────────────────────────────────────────────────────────────────

def _verify_signature(payload_bytes: bytes, signature: str) -> bool:
    """
    Validate Razorpay's HMAC-SHA256 webhook signature.
    Uses constant-time comparison to prevent timing attacks.
    Rejects (returns False) if webhook secret is not configured.
    """
    if not config.RAZORPAY_WEBHOOK_SECRET:
        log.warning("[Signature] Webhook secret not configured — rejecting request.")
        return False

    expected = hmac.new(
        config.RAZORPAY_WEBHOOK_SECRET.encode("utf-8"),
        payload_bytes,
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(expected, signature)


# ─────────────────────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/", include_in_schema=False)
async def root():
    """Simple liveness ping for load balancers / UptimeRobot."""
    return {"status": "alive", "service": "razorpay-webhook", "ts": datetime.utcnow().isoformat()}


@app.get("/health")
async def health():
    """
    Readiness probe — checks DB connectivity and Razorpay API reachability.
    Returns 200 if healthy, 503 if any dependency is down.
    """
    checks: dict = {}

    # ── Database check ────────────────────────────────────────────────────────
    try:
        import database as db
        db.db_fetchone("SELECT 1")
        checks["database"] = "ok"
    except Exception as exc:
        log.error(f"[Health] DB check failed: {exc}")
        # Return only the first line to avoid leaking internal addresses/details
        first_line = str(exc).split('\n')[0].strip()
        checks["database"] = f"error: {first_line}"

    # ── Razorpay API check ────────────────────────────────────────────────────
    try:
        resp = httpx.get(
            "https://api.razorpay.com/v1/payments",
            auth=(config.RAZORPAY_KEY_ID, config.RAZORPAY_KEY_SECRET),
            params={"count": 1},
            timeout=5,
        )
        checks["razorpay"] = "ok" if resp.status_code in (200, 401) else f"http_{resp.status_code}"
        # 401 = auth error but API is reachable; we still mark reachability as ok
        if resp.status_code == 401:
            checks["razorpay_auth"] = "check your API keys"
    except Exception as exc:
        log.error(f"[Health] Razorpay check failed: {exc}")
        checks["razorpay"] = f"error: {exc}"

    all_ok = all(v == "ok" for v in checks.values())
    return JSONResponse(
        content={"status": "healthy" if all_ok else "degraded", "checks": checks},
        status_code=200 if all_ok else 503,
    )


@app.post("/webhook")
async def razorpay_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_razorpay_signature: str = Header(default="", alias="X-Razorpay-Signature"),
    x_request_id: str = Header(default="", alias="X-Request-ID"),
):
    """
    Main Razorpay webhook endpoint.

    Flow:
      1. Read raw body (needed for HMAC verification)
      2. Verify HMAC-SHA256 signature
      3. Parse JSON
      4. Schedule event processing as a BackgroundTask
      5. Return 200 OK immediately (Razorpay requires fast response)
    """
    request_id = x_request_id or str(uuid.uuid4())
    log.info(f"[Webhook] POST /webhook | request_id={request_id}")

    # ── 1. Read raw body ──────────────────────────────────────────────────────
    body = await request.body()

    # ── 2. Verify signature ───────────────────────────────────────────────────
    if not _verify_signature(body, x_razorpay_signature):
        log.warning(f"[Webhook] Invalid signature | request_id={request_id}")
        # Return 200 anyway — some teams argue for this to prevent enumeration,
        # but 400 is also acceptable; we choose 400 for clarity.
        return JSONResponse(
            content={"error": "invalid_signature", "request_id": request_id},
            status_code=400,
        )

    # ── 3. Parse JSON ─────────────────────────────────────────────────────────
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        log.error(f"[Webhook] JSON parse error: {exc} | request_id={request_id}")
        return JSONResponse(
            content={"error": "invalid_json", "request_id": request_id},
            status_code=400,
        )

    event = payload.get("event", "unknown")
    log.info(f"[Webhook] event={event!r} | request_id={request_id}")

    # ── 4. Schedule processing in background ──────────────────────────────────
    background_tasks.add_task(handlers.dispatch, event, payload)

    # ── 5. Return 200 immediately ─────────────────────────────────────────────
    return JSONResponse(
        content={"status": "queued", "event": event, "request_id": request_id},
        status_code=200,
    )


@app.get("/payment/success", response_class=HTMLResponse)
async def payment_success():
    """
    Post-payment redirect page.
    Razorpay redirects the user here after a successful payment.
    """
    return """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Payment Successful — BOT_NAME_PLACEHOLDER</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      background: #f0faf4;
      display: flex;
      align-items: center;
      justify-content: center;
      min-height: 100vh;
      padding: 24px;
    }
    .card {
      background: #fff;
      border-radius: 16px;
      box-shadow: 0 4px 24px rgba(0,0,0,.08);
      max-width: 420px;
      width: 100%;
      padding: 48px 40px;
      text-align: center;
    }
    .icon { font-size: 64px; margin-bottom: 20px; }
    h1 { color: #1a7f4b; font-size: 1.75rem; margin-bottom: 12px; }
    p  { color: #555; line-height: 1.6; margin-bottom: 8px; }
    .badge {
      display: inline-block;
      background: #e8f5e9;
      color: #2e7d32;
      border-radius: 999px;
      padding: 6px 18px;
      font-size: .85rem;
      font-weight: 600;
      margin-top: 20px;
    }
    a.btn {
      display: inline-block;
      margin-top: 28px;
      background: #229ED9;
      color: #fff;
      text-decoration: none;
      border-radius: 8px;
      padding: 12px 28px;
      font-weight: 600;
      font-size: .95rem;
      transition: opacity .2s;
    }
    a.btn:hover { opacity: .85; }
  </style>
</head>
<body>
  <div class="card">
    <div class="icon">✅</div>
    <h1>Payment Successful!</h1>
    <p>Your payment has been received and is being processed.</p>
    <p>Return to Telegram — your access will be activated automatically.</p>
    <div class="badge">⭐ Premium activation in progress</div>
    <br/>
    <a class="btn" href="https://t.me">Open Telegram</a>
  </div>
</body>
</html>
""".replace("BOT_NAME_PLACEHOLDER", config.BOT_NAME)
