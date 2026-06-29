"""
db_server.py — Persistent HTTP micro-server for Potato Bot dashboard API.

Sprint 6.1: Replaces the per-request Python spawn() model with a long-lived
server on localhost:5056. Connection pool initializes once on startup
and stays warm between requests.

Usage:
    python db_server.py [--port 5056] [--host 127.0.0.1]

Security:
    Requests must include header: X-Api-Key: <DB_SERVER_SECRET>
    Set DB_SERVER_SECRET env var. Defaults to 'local-dev-secret' for local-only use.
"""

import os
import sys
import json
import logging
import argparse
import traceback
from http.server import BaseHTTPRequestHandler, HTTPServer
import urllib.parse

# -- Logging ------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger(__name__)

# -- Load .env (must happen before reading any env vars) ----------------------
try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv(override=False)  # don't override vars already set in the OS env
except ImportError:
    pass  # python-dotenv not installed; rely on OS-level env vars

# -- Shared secret (V1 fix: unauthenticated API) ------------------------------
_API_SECRET = os.environ.get('DB_SERVER_SECRET', 'local-dev-secret')
if _API_SECRET == 'local-dev-secret':
    log.warning("DB_SERVER_SECRET not set -- using default 'local-dev-secret'. "
                "Set this env var to a strong random value in production.")

# -- Whitelisted env var names (V5 fix: env var injection) --------------------
_ALLOWED_ENV_OVERRIDES = {'API_TOKEN', 'BOT_ID', 'BOT_TOKEN', 'DATABASE_URL'}

# -- Allowed CORS origins (V2 fix: no wildcard '*') ---------------------------
_ALLOWED_ORIGINS = {
    'http://localhost:3791',
    'http://127.0.0.1:3791',
    'http://localhost:5056',
    'http://127.0.0.1:5056',
}

# -- Load project config & initialise DB pool ---------------------------------
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

try:
    import utils.database as db
    db._init_pool()
    log.info("DB connection pool initialised.")
    db.initialize_database()
    log.info("DB migrations and schema checks complete.")
except Exception as e:
    log.critical(f"Failed to initialise DB pool or run migrations: {e}")
    sys.exit(1)

# -- Import db_api once at startup (V4 fix: no importlib.reload per request) --
try:
    import db_api
    log.info("db_api module loaded.")
except Exception as e:
    log.critical(f"Failed to import db_api: {e}")
    sys.exit(1)


# -- Action dispatcher --------------------------------------------------------
def dispatch(action: str, params: dict) -> dict:
    """
    Calls db_api.main() in-process. Security fixes:
    - V4: importlib.reload() removed -- module loaded once at startup.
    - V5: Only whitelisted env var names accepted from request body.
    """
    import io
    import base64

    clean_params = {}
    env_overrides = {}
    for k, v in params.items():
        if k.startswith('_'):
            env_key = k[1:]
            if env_key in _ALLOWED_ENV_OVERRIDES:
                env_overrides[env_key] = str(v)
            else:
                log.warning(f"Rejected non-whitelisted env override: {env_key!r}")
        else:
            clean_params[k] = v

    encoded = base64.b64encode(json.dumps(clean_params).encode('utf-8')).decode('ascii')

    buf = io.StringIO()
    old_argv   = sys.argv[:]
    old_stdout = sys.stdout
    old_env    = {}
    try:
        for k, v in env_overrides.items():
            old_env[k] = os.environ.get(k)
            os.environ[k] = v

        import importlib
        import config
        importlib.reload(config)

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
        for k, old_val in old_env.items():
            if old_val is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = old_val
        import importlib
        import config
        importlib.reload(config)

    output = buf.getvalue().strip()
    if not output:
        return {"ok": False, "error": "No output from action"}
    try:
        return json.loads(output)
    except json.JSONDecodeError as e:
        log.error(f"JSON parse error from dispatch({action}): {e}\nOutput: {output[:500]}")
        return {"ok": False, "error": f"JSON parse error: {e}", "raw": output[:500]}


# -- HTTP Request Handler -----------------------------------------------------
class ApiHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        log.info(f"{self.address_string()} - {fmt % args}")

    def _cors_origin(self):
        origin = self.headers.get('Origin', '')
        return origin if origin in _ALLOWED_ORIGINS else 'http://localhost:3791'

    def _send_json(self, data: dict, status: int = 200):
        body = json.dumps(data).encode('utf-8')
        origin = self._cors_origin()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Access-Control-Allow-Origin', origin)
        self.send_header('Vary', 'Origin')
        self.end_headers()
        self.wfile.write(body)

    def _check_auth(self) -> bool:
        provided = self.headers.get('X-Api-Key', '')
        if provided != _API_SECRET:
            log.warning(f"Unauthorized request from {self.address_string()}")
            self._send_json({"ok": False, "error": "Unauthorized"}, 401)
            return False
        return True

    def do_OPTIONS(self):
        origin = self._cors_origin()
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', origin)
        self.send_header('Access-Control-Allow-Methods', 'POST, GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, X-Api-Key')
        self.send_header('Vary', 'Origin')
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

        if not self._check_auth():
            return

        length = int(self.headers.get('Content-Length', 0))
        if length == 0:
            self._send_json({"ok": False, "error": "Empty body"}, 400)
            return

        if length > 1_048_576:
            self._send_json({"ok": False, "error": "Payload too large"}, 413)
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

        for k, v in params.items():
            if isinstance(v, int) and abs(v) > 2_147_483_647:
                self._send_json({"ok": False, "error": f"Parameter '{k}' out of range"}, 400)
                return

        log.info(f"Action: {action}  Params: {json.dumps(params)[:120]}")
        result = dispatch(action, params)
        self._send_json(result)


# -- Server startup -----------------------------------------------------------
def run(host: str = '127.0.0.1', port: int = 5056):
    server = HTTPServer((host, port), ApiHandler)
    log.info(f"db_server running at http://{host}:{port}")
    log.info(f"Auth: X-Api-Key required "
             f"({'DEFAULT secret -- set DB_SERVER_SECRET!' if _API_SECRET == 'local-dev-secret' else 'custom secret OK'})")
    log.info("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Shutting down db_server.")
        server.shutdown()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Potato Bot DB API Server')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=5056)
    args = parser.parse_args()
    run(args.host, args.port)
