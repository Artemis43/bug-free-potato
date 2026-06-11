"""
keep_alive.py — Flask web server that runs in a background daemon thread.

Endpoints:
  GET  /                              — plain "Alive" ping (UptimeRobot)
  GET  /health                        — {"status":"ok"} JSON
  GET  /health/detail                 — full metrics JSON (uptime, handlers, memory)
  GET  /payment/success               — post-payment Razorpay redirect page
  POST /payment/razorpay-webhook      — verified Razorpay webhook handler
"""
import logging
import json as _json
from flask import Flask, request, jsonify
from threading import Thread
from config import KEEP_ALIVE_PORT

app = Flask(__name__)
_started = False  # Guard against double-start

log = logging.getLogger(__name__)


# ── Health ─────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    """Simple keep-alive ping for UptimeRobot / Render cron."""
    return "Alive", 200


@app.route('/health')
def health():
    """Standard health-check endpoint — returns 200 when the bot process is up."""
    return jsonify({"status": "ok"}), 200


@app.route('/health/detail')
def health_detail():
    """
    Detailed metrics endpoint — returns:
      - uptime, start time
      - active downloads count
      - per-handler call / error counters
      - memory RSS (if psutil available)
      - seconds since last Telegram update
    """
    try:
        from utils.monitoring import get_metrics
        metrics = get_metrics()
        return jsonify(metrics), 200
    except Exception as e:
        log.error(f"[health/detail] metrics error: {e}")
        return jsonify({"status": "ok", "metrics": "unavailable", "error": str(e)}), 200


# ── Payment ────────────────────────────────────────────────────────────────

@app.route('/payment/success')
def payment_success():
    """Post-payment redirect landing page (shown by Razorpay after pay)."""
    return (
        """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Payment Successful — Medical Bot</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
      background: linear-gradient(135deg, #0f2027, #203a43, #2c5364);
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      color: #fff;
    }
    .card {
      background: rgba(255,255,255,0.08);
      backdrop-filter: blur(16px);
      border: 1px solid rgba(255,255,255,0.15);
      border-radius: 24px;
      padding: 48px 40px;
      text-align: center;
      max-width: 480px;
      width: 90%;
      box-shadow: 0 20px 60px rgba(0,0,0,0.4);
    }
    .icon { font-size: 64px; margin-bottom: 16px; }
    h1 { font-size: 28px; font-weight: 700; margin-bottom: 12px; }
    p  { font-size: 16px; color: rgba(255,255,255,0.75); line-height: 1.6; margin-bottom: 8px; }
    .badge {
      display: inline-block;
      background: rgba(74,222,128,0.2);
      border: 1px solid rgba(74,222,128,0.4);
      color: #4ade80;
      border-radius: 999px;
      padding: 6px 18px;
      font-size: 13px;
      font-weight: 600;
      margin-top: 24px;
    }
    .tg-btn {
      display: inline-block;
      margin-top: 28px;
      background: #229ED9;
      color: #fff;
      font-size: 16px;
      font-weight: 600;
      text-decoration: none;
      padding: 14px 32px;
      border-radius: 14px;
      transition: background 0.2s;
    }
    .tg-btn:hover { background: #1a8ab5; }
  </style>
</head>
<body>
  <div class="card">
    <div class="icon">🎉</div>
    <h1>Payment Successful!</h1>
    <p>Your payment has been received and is being processed.</p>
    <p>Return to Telegram — your access will be activated automatically within a few seconds.</p>
    <div class="badge">✅ No action needed</div>
    <br>
    <a class="tg-btn" href="https://t.me/">Open Telegram</a>
  </div>
</body>
</html>""",
        200,
        {"Content-Type": "text/html; charset=utf-8"},
    )


@app.route('/payment/razorpay-webhook', methods=['POST'])
def razorpay_webhook():
    """Receive Razorpay payment events and activate user access."""
    import hashlib
    import hmac
    from config import RAZORPAY_WEBHOOK_SECRET

    payload_bytes = request.get_data()
    signature     = request.headers.get('X-Razorpay-Signature', '')

    # Always verify signature — reject if secret is not configured
    if not RAZORPAY_WEBHOOK_SECRET:
        log.warning("[Razorpay Webhook] No webhook secret configured — rejecting request.")
        return jsonify({"error": "webhook secret not configured"}), 400

    expected = hmac.new(
        RAZORPAY_WEBHOOK_SECRET.encode(),
        payload_bytes,
        hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, signature):
        log.warning("[Razorpay Webhook] Invalid signature — rejected.")
        return jsonify({"error": "invalid signature"}), 400

    try:
        payload = _json.loads(payload_bytes)
    except Exception as e:
        log.error(f"[Razorpay Webhook] JSON parse error: {e}")
        return jsonify({"error": "bad json"}), 400

    try:
        from handlers.payment import process_webhook_payload
        process_webhook_payload(payload)
    except Exception as e:
        log.error(f"[Razorpay Webhook] Handler error: {e}")

    # Always return 200 to Razorpay so it doesn't retry
    return jsonify({"status": "ok"}), 200


# ── Server thread ──────────────────────────────────────────────────────────

def _run():
    # Suppress werkzeug request logs (keep error logs)
    logging.getLogger('werkzeug').setLevel(logging.ERROR)
    app.run(host='0.0.0.0', port=KEEP_ALIVE_PORT)


def keep_alive():
    global _started
    if _started:
        return
    _started = True
    t = Thread(target=_run, daemon=True)
    t.start()
    log.info(f"Keep-alive server started on port {KEEP_ALIVE_PORT}.")