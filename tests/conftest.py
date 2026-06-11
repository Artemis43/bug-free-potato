"""
tests/conftest.py — shared pytest fixtures and environment bootstrap.

Sets up environment variables before any module under test tries to import
config.py, so tests run without a real .env / database / Telegram token.
"""
import os
import sys
import pytest

# ── Inject env vars BEFORE importing any bot module ────────────────────────
os.environ.setdefault("API_TOKEN",  "1234567890:AAFake-Token-For-Testing-Only")
os.environ.setdefault("ADMINS",     "999888777")
os.environ.setdefault("DB_STRING",  "postgresql://localhost/test_bot")
os.environ.setdefault("HOST_URL",   "")
os.environ.setdefault("CHANNEL",    "")
os.environ.setdefault("SUBSCRIPTION", "")
os.environ.setdefault("PAYMENT_MODE", "manual")
os.environ.setdefault("LOG_LEVEL",  "WARNING")

# ── Make the project root importable ───────────────────────────────────────
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


# ── Shared simple fixtures ──────────────────────────────────────────────────
@pytest.fixture(scope="session")
def admin_ids():
    return ["999888777"]


@pytest.fixture(scope="session")
def sample_user():
    """A dict representing a standard approved user row from the DB."""
    return {
        "user_id":   123456789,
        "username":  "testuser",
        "first_name": "Test",
        "status":    "approved",
        "premium":   False,
        "premium_expiration": None,
        "last_download": None,
    }


@pytest.fixture(scope="session")
def premium_user():
    """A dict representing a premium approved user."""
    from datetime import datetime, timedelta
    return {
        "user_id":   987654321,
        "username":  "premiumuser",
        "first_name": "Premium",
        "status":    "approved",
        "premium":   True,
        "premium_expiration": datetime.now() + timedelta(days=30),
        "last_download": None,
    }
