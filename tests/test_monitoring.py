"""
tests/test_monitoring.py — tests for utils/monitoring.py.

These are pure unit tests: no DB, no Telegram, no network.
"""
import time
import pytest


class TestMonitoringMetrics:
    def setup_method(self):
        """Reset monitoring state before each test."""
        import utils.monitoring as m
        m._handler_counts.clear()
        m._handler_errors.clear()
        m._last_update_ts = 0.0

    def test_uptime_increases_over_time(self):
        from utils.monitoring import uptime_seconds
        t1 = uptime_seconds()
        time.sleep(0.05)
        t2 = uptime_seconds()
        assert t2 > t1

    def test_record_handler_call_increments(self):
        from utils.monitoring import record_handler_call, get_metrics
        record_handler_call("start")
        record_handler_call("start")
        record_handler_call("download")
        metrics = get_metrics()
        assert metrics["handlers"]["start"]["calls"] == 2
        assert metrics["handlers"]["download"]["calls"] == 1

    def test_record_handler_error_increments(self):
        from utils.monitoring import record_handler_error, get_metrics
        record_handler_error("payment")
        metrics = get_metrics()
        assert metrics["handlers"]["payment"]["errors"] == 1

    def test_record_update_sets_timestamp(self):
        from utils.monitoring import record_update, _last_update_ts
        import utils.monitoring as m
        record_update()
        assert m._last_update_ts > 0

    def test_get_metrics_structure(self):
        from utils.monitoring import get_metrics
        m = get_metrics()
        assert m["status"] == "ok"
        assert "uptime_seconds" in m
        assert "uptime_human" in m
        assert "active_downloads" in m
        assert "handlers" in m
        assert "started_at" in m

    def test_uptime_human_format(self):
        from utils.monitoring import _fmt_uptime
        assert _fmt_uptime(0) == "0s"
        assert _fmt_uptime(65) == "1m 5s"
        assert _fmt_uptime(3665) == "1h 1m 5s"
        assert _fmt_uptime(90061) == "1d 1h 1m 1s"

    def test_zero_counts_not_shown(self):
        """With no handler calls, handlers dict should be empty."""
        from utils.monitoring import get_metrics
        m = get_metrics()
        assert m["handlers"] == {}

    def test_last_update_ago_none_before_first_update(self):
        """Before any update, last_update_ago_s should be None."""
        from utils.monitoring import get_metrics
        m = get_metrics()
        assert m["last_update_ago_s"] is None

    def test_last_update_ago_populated_after_record(self):
        from utils.monitoring import record_update, get_metrics
        record_update()
        time.sleep(0.02)
        m = get_metrics()
        assert m["last_update_ago_s"] is not None
        assert m["last_update_ago_s"] >= 0.0

    def test_active_downloads_count(self):
        """Active downloads count matches the progress tracker."""
        import utils.progress as p
        import utils.monitoring as mon
        p._active_downloads.clear()

        from utils.monitoring import get_metrics
        assert get_metrics()["active_downloads"] == 0

        p.start_download(100, "Test", 5)
        p.start_download(101, "Test2", 10)
        assert get_metrics()["active_downloads"] == 2

        p.finish_download(100)
        assert get_metrics()["active_downloads"] == 1

        p.finish_download(101)
        assert get_metrics()["active_downloads"] == 0
