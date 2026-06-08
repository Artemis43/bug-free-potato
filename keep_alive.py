from flask import Flask
from threading import Thread
from config import KEEP_ALIVE_PORT

app = Flask(__name__)

@app.route('/')
def index():
    return "Alive", 200

@app.route('/health')
def health():
    """Standard health-check endpoint used by Render, Railway, and UptimeRobot."""
    return {"status": "ok"}, 200

def _run():
    # Silence Flask's default request logger to avoid log noise
    import logging
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)
    app.run(host='0.0.0.0', port=KEEP_ALIVE_PORT)

def keep_alive():
    t = Thread(target=_run, daemon=True)
    t.start()