"""
db_server.py — Persistent HTTP micro-server for Potato Bot dashboard API.

Sprint 6.1: Replaces the per-request Python spawn() model with a long-lived
Flask server on localhost:5055. Connection pool initializes once on startup
and stays warm between requests.

Usage:
    python db_server.py [--port 5055] [--host 127.0.0.1]

Node.js integration:
    Instead of spawn('python', ['db_api.py', action, params]),
    server.js does: fetch('http://127.0.0.1:5055/api', { method:'POST', body: JSON.stringify({action, params}) })
"""

import os
import sys
import json
import logging
import argparse
import traceback
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
import urllib.parse

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger(__name__)

# ── Load project config & initialise DB pool ──────────────────────────────────
# We need to be in the bot directory so relative imports work.
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

try:
    import utils.database as db
    db._init_pool()          # warm up the Supabase connection pool once
    log.info("DB connection pool initialised.")
    db.initialize_database() # run migrations and ensure tables (e.g. admin_activity_log) exist
    log.info("DB migrations and schema checks complete.")
except Exception as e:
    log.critical(f"Failed to initialise DB pool or run migrations: {e}")
    sys.exit(1)

# ── Import the db_api action dispatcher ───────────────────────────────────────
try:
    import db_api
    log.info("db_api module loaded.")
except Exception as e:
    log.critical(f"Failed to import db_api: {e}")
    sys.exit(1)


# ── Action dispatcher ──────────────────────────────────────────────────────────
def dispatch(action: str, params: dict) -> dict:
    """
    Calls db_api.main() in-process by mimicking the CLI spawn protocol:
    - sys.argv[1] = action
    - sys.argv[2] = base64(json(params))   ← same as server.js spawn
    - env vars for API_TOKEN / BOT_ID       ← same as server.js spawnEnv
    Returns the parsed JSON dict instead of printing it.
    """
    import io
    import base64

    # Extract and inject any _-prefixed env vars (e.g. _API_TOKEN from HTTP body)
    clean_params = {}
    env_overrides = {}
    for k, v in params.items():
        if k.startswith('_'):
            env_overrides[k[1:]] = str(v)   # strip leading '_', set as env var
        else:
            clean_params[k] = v

    # Encode params the same way server.js does for spawn mode
    encoded = base64.b64encode(json.dumps(clean_params).encode('utf-8')).decode('ascii')

    # Capture stdout that db_api.main() prints
    buf = io.StringIO()
    old_argv   = sys.argv[:]
    old_stdout = sys.stdout
    old_env    = {}
    try:
        # Inject env vars (API_TOKEN, BOT_ID, etc.)
        for k, v in env_overrides.items():
            old_env[k] = os.environ.get(k)
            os.environ[k] = v

        sys.argv   = ['db_api.py', action, encoded]
        sys.stdout = buf
        db_api.main()
    except SystemExit:
        pass
    except Exception as exc:
        sys.stdout = old_stdout
        log.error(f"dispatch({action}) error: {exc}\n{traceback.format_exc()}")
        return {"ok": False, "error": str(exc)}
    finally:
        sys.stdout = old_stdout
        sys.argv   = old_argv
        # Restore env vars
        for k, old_val in old_env.items():
            if old_val is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = old_val

    output = buf.getvalue().strip()
    if not output:
        return {"ok": False, "error": "No output from action"}
    try:
        return json.loads(output)
    except json.JSONDecodeError as e:
        log.error(f"JSON parse error from dispatch({action}): {e}\nOutput: {output[:500]}")
        return {"ok": False, "error": f"JSON parse error: {e}", "raw": output[:500]}


# ── HTTP Request Handler ───────────────────────────────────────────────────────
class ApiHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        # Override to use our logger
        log.info(f"{self.address_string()} - {fmt % args}")

    def _send_json(self, data: dict, status: int = 200):
        body = json.dumps(data).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == '/health':
            self._send_json({"ok": True, "status": "healthy", "server": "db_server"})
        else:
            self._send_json({"ok": False, "error": "Use POST /api"}, 404)

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if path != '/api':
            self._send_json({"ok": False, "error": "Unknown endpoint"}, 404)
            return

        length = int(self.headers.get('Content-Length', 0))
        if length == 0:
            self._send_json({"ok": False, "error": "Empty body"}, 400)
            return

        try:
            raw = self.rfile.read(length)
            body = json.loads(raw.decode('utf-8'))
        except Exception as e:
            self._send_json({"ok": False, "error": f"Invalid JSON: {e}"}, 400)
            return

        action = body.get('action', '')
        params = body.get('params', {})

        if not action:
            self._send_json({"ok": False, "error": "Missing 'action'"}, 400)
            return

        log.info(f"Action: {action}  Params: {json.dumps(params)[:120]}")
        result = dispatch(action, params)
        self._send_json(result)


# ── Server startup ─────────────────────────────────────────────────────────────
def run(host: str = '127.0.0.1', port: int = 5055):
    server = HTTPServer((host, port), ApiHandler)
    log.info(f"db_server running at http://{host}:{port}")
    log.info("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Shutting down db_server.")
        server.shutdown()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Potato Bot DB API Server')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=5055)
    args = parser.parse_args()
    run(args.host, args.port)
