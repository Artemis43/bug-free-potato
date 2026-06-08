import logging
from flask import Flask, request, jsonify
from threading import Thread
from config import KEEP_ALIVE_PORT

app = Flask(__name__)
_started = False  # Guard against double-start


@app.route('/')
def index():
    return "Alive", 200


@app.route('/health')
def health():
    """Standard health-check endpoint for Render, Railway, and UptimeRobot."""
    return {"status": "ok"}, 200


@app.route('/payment/success')
def payment_success():
    """Post-payment redirect landing page (shown by Razorpay after pay)."""
    return (
        "<html><head><title>Payment Received</title>"
        "<style>body{font-family:sans-serif;text-align:center;padding:60px;}"
        "h1{color:#2e7d32;}p{color:#555;}</style></head>"
        "<body><h1>✅ Payment Successful!</h1>"
        "<p>Your payment has been received.<br>"
        "Return to Telegram — your access will be activated shortly.</p>"
        "</body></html>"
    ), 200


@app.route('/payment/razorpay-webhook', methods=['POST'])
def razorpay_webhook():
    """Receive Razorpay payment events and activate user access."""
    from config import RAZORPAY_WEBHOOK_SECRET
    import hashlib, hmac

    payload_bytes = request.get_data()
    signature     = request.headers.get('X-Razorpay-Signature', '')

    # Verify signature if webhook secret is configured
    if RAZORPAY_WEBHOOK_SECRET:
        expected = hmac.new(
            RAZORPAY_WEBHOOK_SECRET.encode(),
            payload_bytes,
            hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(expected, signature):
            logging.warning("[Razorpay Webhook] Invalid signature — rejected.")
            return jsonify({"error": "invalid signature"}), 400

    try:
        import json
        payload = json.loads(payload_bytes)
    except Exception as e:
        logging.error(f"[Razorpay Webhook] JSON parse error: {e}")
        return jsonify({"error": "bad json"}), 400

    try:
        from handlers.payment import process_webhook_payload
        process_webhook_payload(payload)
    except Exception as e:
        logging.error(f"[Razorpay Webhook] Handler error: {e}")

    # Always return 200 to Razorpay so it doesn't retry
    return jsonify({"status": "ok"}), 200


def _run():
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)
    app.run(host='0.0.0.0', port=KEEP_ALIVE_PORT)


def keep_alive():
    global _started
    if _started:
        return
    _started = True
    t = Thread(target=_run, daemon=True)
    t.start()