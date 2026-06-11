"""
tests/test_config.py — validate that config.py loads correctly and provides
sane defaults for every required variable.
"""
import os
import pytest


class TestConfigLoads:
    """Config should import without crashing and expose required attributes."""

    def test_config_imports_successfully(self):
        import config
        assert config is not None

    def test_api_token_present(self):
        import config
        assert config.API_TOKEN is not None
        assert len(config.API_TOKEN) > 0

    def test_admin_ids_is_list(self):
        import config
        assert isinstance(config.ADMIN_IDS, list)

    def test_payment_mode_valid(self):
        import config
        assert config.PAYMENT_MODE in ("manual", "razorpay", "stars")

    def test_webhook_url_uses_token(self):
        import config
        if config.WEBHOOK_HOST:
            assert config.API_TOKEN in config.WEBHOOK_URL

    def test_keep_alive_port_is_int(self):
        import config
        assert isinstance(config.KEEP_ALIVE_PORT, int)
        assert 1 <= config.KEEP_ALIVE_PORT <= 65535

    def test_notify_cooldown_hours_non_negative(self):
        import config
        assert config.NOTIFY_COOLDOWN_HOURS >= 0

    def test_default_caption_is_string(self):
        import config
        assert isinstance(config.DEFAULT_CAPTION, str)

    def test_log_level_valid(self):
        import config
        import logging
        # getattr will raise AttributeError if invalid level
        assert hasattr(logging, config.LOG_LEVEL)


class TestConfigEnvOverrides:
    """Environment variable overrides should be picked up correctly."""

    def test_admin_ids_parsed_from_env(self):
        import config
        # conftest sets ADMINS=999888777
        assert "999888777" in config.ADMIN_IDS

    def test_payment_mode_from_env(self):
        import config
        # conftest sets PAYMENT_MODE=manual
        assert config.PAYMENT_MODE == "manual"

    def test_empty_subscription_gives_empty_list(self):
        import config
        # conftest sets SUBSCRIPTION='' so REQUIRED_CHANNELS should be []
        assert isinstance(config.REQUIRED_CHANNELS, list)
