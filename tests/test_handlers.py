"""
tests/test_handlers.py — handler logic unit tests using mocked aiogram objects.

Tests verify that each handler function:
  1. Calls the correct Telegram API methods
  2. Handles edge cases (missing data, DB misses) without crashing
  3. Respects access control (admin-only, private-chat-only)

Strategy:
  - Use unittest.mock.AsyncMock for all awaitable bot methods
  - Use unittest.mock.MagicMock for Message / CallbackQuery objects
  - Patch DB functions at the call site to avoid a real database
  - Never make real network calls
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ── Helpers ────────────────────────────────────────────────────────────────

def _make_message(
    user_id: int = 123,
    username: str = "testuser",
    first_name: str = "Test",
    chat_type: str = "private",
    text: str = "/start",
) -> MagicMock:
    """Build a minimal mock Message object."""
    msg = MagicMock()
    msg.from_user.id       = user_id
    msg.from_user.username = username
    msg.from_user.first_name = first_name
    msg.chat.id            = user_id
    msg.chat.type          = chat_type
    msg.text               = text
    msg.reply              = AsyncMock()
    msg.answer             = AsyncMock()
    return msg


def _make_callback(
    user_id: int = 123,
    data: str    = "back_to_main",
    chat_type: str = "private",
) -> MagicMock:
    """Build a minimal mock CallbackQuery object."""
    cq = MagicMock()
    cq.from_user.id        = user_id
    cq.from_user.username  = "testuser"
    cq.data                = data
    cq.message.chat.id     = user_id
    cq.message.chat.type   = chat_type
    cq.message.message_id  = 42
    cq.answer              = AsyncMock()
    cq.message.answer      = AsyncMock()
    cq.message.edit_text   = AsyncMock()
    return cq


# ── middlewares/authorization ──────────────────────────────────────────────

class TestIsPrivateChat:
    def test_private_chat_returns_true(self):
        from middlewares.authorization import is_private_chat
        msg = _make_message(chat_type="private")
        assert is_private_chat(msg) is True

    def test_group_chat_returns_false(self):
        from middlewares.authorization import is_private_chat
        msg = _make_message(chat_type="group")
        assert is_private_chat(msg) is False

    def test_supergroup_returns_false(self):
        from middlewares.authorization import is_private_chat
        msg = _make_message(chat_type="supergroup")
        assert is_private_chat(msg) is False

    def test_channel_returns_false(self):
        from middlewares.authorization import is_private_chat
        msg = _make_message(chat_type="channel")
        assert is_private_chat(msg) is False


class TestMemberCache:
    """invalidate_member_cache removes the cached entry."""

    def setup_method(self):
        from middlewares import authorization as a
        a._member_cache.clear()

    def test_invalidate_removes_entry(self):
        from middlewares import authorization as a
        from datetime import datetime
        a._member_cache[100] = (True, datetime.now())
        a.invalidate_member_cache(100)
        assert 100 not in a._member_cache

    def test_invalidate_nonexistent_is_noop(self):
        from middlewares.authorization import invalidate_member_cache
        invalidate_member_cache(9999)  # should not raise


# ── middlewares/rate_limit ─────────────────────────────────────────────────

class TestRateLimitMiddleware:
    def setup_method(self):
        """Reset rate-limit state between tests."""
        from middlewares import rate_limit as rl
        rl._last_action.clear()
        rl._muted_until.clear()
        rl._violations.clear()

    @pytest.mark.asyncio
    async def test_first_message_passes(self):
        from middlewares.rate_limit import RateLimitMiddleware
        middleware = RateLimitMiddleware()
        handler = AsyncMock(return_value="ok")
        msg = _make_message()
        result = await middleware(handler, msg, {})
        handler.assert_awaited_once()
        assert result == "ok"

    @pytest.mark.asyncio
    async def test_rapid_messages_dropped(self):
        """Second message within 1.5s should be dropped (handler not called).
        
        Note: The rate limiter checks isinstance(event, Message). Since our mock
        doesn't subclass Message, we patch the isinstance check.
        """
        import time
        from middlewares import rate_limit as rl
        from middlewares.rate_limit import RateLimitMiddleware
        from aiogram.types import Message
        middleware = RateLimitMiddleware()
        handler = AsyncMock(return_value="ok")
        msg = _make_message()

        # Simulate last message was 0.1s ago
        rl._last_action[msg.from_user.id] = time.monotonic() - 0.1

        # Patch isinstance so the mock passes the Message type check
        with patch("middlewares.rate_limit.isinstance", side_effect=lambda obj, cls: cls is Message or False):
            result = await middleware(handler, msg, {})
        handler.assert_not_awaited()
        assert result is None  # dropped

    @pytest.mark.asyncio
    async def test_non_message_event_passes_through(self):
        """Non-Message events should pass through the rate limiter unchanged."""
        from middlewares.rate_limit import RateLimitMiddleware
        middleware = RateLimitMiddleware()
        handler = AsyncMock(return_value="passthrough")
        non_msg = MagicMock()  # not an instance of Message
        result = await middleware(handler, non_msg, {})
        handler.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_admin_exempt_from_rate_limit(self):
        """Admins are never throttled — bulk/album uploads must not be dropped."""
        import time
        from middlewares import rate_limit as rl
        from middlewares.rate_limit import RateLimitMiddleware
        from aiogram.types import Message
        middleware = RateLimitMiddleware()
        handler = AsyncMock(return_value="ok")
        msg = _make_message(user_id=999888777)  # admin id from conftest ADMINS

        # A very recent last action would drop a normal user; the admin passes.
        rl._last_action[msg.from_user.id] = time.monotonic() - 0.01
        with patch("middlewares.rate_limit.isinstance",
                   side_effect=lambda obj, cls: cls is Message or False):
            result = await middleware(handler, msg, {})
        handler.assert_awaited_once()
        assert result == "ok"


# ── utils/media_group (album aggregation) ───────────────────────────────────

class TestMediaGroupCollect:
    def setup_method(self):
        import utils.media_group as mg
        mg._buffers.clear()

    @pytest.mark.asyncio
    async def test_single_message_processes_immediately(self):
        """A lone file (no media_group_id) is processed at once as a batch of 1."""
        from utils.media_group import collect_media_group
        msg = MagicMock()
        msg.media_group_id = None
        calls = []

        async def process(batch):
            calls.append(batch)

        await collect_media_group(msg, process)
        assert calls == [[msg]]

    @pytest.mark.asyncio
    async def test_album_batched_into_single_call(self):
        """All items of one album are flushed together as a single batch."""
        import asyncio
        from utils.media_group import collect_media_group
        calls = []

        async def process(batch):
            calls.append(batch)

        msgs = []
        for _ in range(3):
            m = MagicMock()
            m.media_group_id = "ALBUM1"
            msgs.append(m)
        for m in msgs:
            await collect_media_group(m, process, window=0.05)

        assert calls == []          # still buffering within the window
        await asyncio.sleep(0.15)
        assert len(calls) == 1
        assert calls[0] == msgs     # all three, in arrival order

    @pytest.mark.asyncio
    async def test_separate_albums_get_separate_batches(self):
        import asyncio
        from utils.media_group import collect_media_group
        calls = []

        async def process(batch):
            calls.append(batch)

        a = MagicMock(); a.media_group_id = "A"
        b = MagicMock(); b.media_group_id = "B"
        await collect_media_group(a, process, window=0.05)
        await collect_media_group(b, process, window=0.05)
        await asyncio.sleep(0.15)
        assert len(calls) == 2


# ── handlers/stats ─────────────────────────────────────────────────────────

class TestStatsHandler:
    @pytest.mark.asyncio
    async def test_non_admin_rejected(self):
        """Non-admin users get a rejection message."""
        from handlers.stats import stats
        msg = _make_message(user_id=999, text="/stats")
        msg.from_user.id = 999  # not in ADMIN_IDS (which is '999888777')

        await stats(msg)
        msg.reply.assert_awaited()
        # Should contain some 'Admin' or access restriction text
        reply_text = msg.reply.call_args[0][0]
        assert "admin" in reply_text.lower() or "restricted" in reply_text.lower()

    @pytest.mark.asyncio
    async def test_non_private_chat_ignored(self):
        """Stats in group chat should be silently ignored."""
        from handlers.stats import stats
        msg = _make_message(user_id=999888777, chat_type="group")

        await stats(msg)
        msg.reply.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_admin_gets_stats(self):
        """Admin in private chat gets the stats card."""
        from handlers.stats import stats

        mock_db_row = (50, 40, 5, 3, 10)  # total, approved, pending, rejected, premium

        with patch("handlers.stats.db_fetchone", return_value=mock_db_row), \
             patch("handlers.stats.db_fetchall", return_value=[]):
            msg = _make_message(user_id=999888777, text="/stats")
            await stats(msg)
            msg.answer.assert_awaited()


# ── handlers/status ────────────────────────────────────────────────────────

class TestStatusHandler:
    @pytest.mark.asyncio
    async def test_unregistered_user(self):
        """User not in DB gets a friendly not-registered message."""
        from handlers.status import status

        with patch("handlers.status.db_fetchone", return_value=None):
            msg = _make_message()
            await status(msg)
            msg.reply.assert_awaited()
            text = msg.reply.call_args[0][0]
            assert "start" in text.lower() or "haven't started" in text.lower()

    @pytest.mark.asyncio
    async def test_pending_user(self):
        """Pending user sees pending status in the card."""
        from handlers.status import status

        # DB row: (status, premium, premium_expiration, last_download, first_name, username)
        db_row = ("pending", False, None, None, "Alice", "alice")

        with patch("handlers.status.db_fetchone", return_value=db_row):
            msg = _make_message()
            await status(msg)
            msg.reply.assert_awaited()
            text = msg.reply.call_args[0][0]
            assert "pending" in text.lower() or "Pending" in text

    @pytest.mark.asyncio
    async def test_approved_user_shows_cooldown(self):
        """Approved user sees cooldown status."""
        from handlers.status import status
        from datetime import datetime, timedelta

        last_dl = datetime.now() - timedelta(minutes=1)  # 1 min ago
        db_row = ("approved", False, None, last_dl, "Bob", "bob")

        with patch("handlers.status.db_fetchone", return_value=db_row):
            msg = _make_message()
            await status(msg)
            msg.reply.assert_awaited()
            text = msg.reply.call_args[0][0]
            # Should show cooldown remaining
            assert "Cooldown" in text or "cooldown" in text


# ── handlers/about_help ────────────────────────────────────────────────────

class TestHelpContent:
    """Test the text builders in about_help (pure functions, no I/O)."""

    def test_get_help_content_returns_string(self):
        from handlers.about_help import get_help_content
        text = get_help_content()
        assert isinstance(text, str)
        assert len(text) > 100

    def test_get_help_content_has_key_sections(self):
        from handlers.about_help import get_help_content
        text = get_help_content()
        assert "/start" in text
        assert "/download" in text

    def test_get_about_content_returns_string(self):
        from handlers.about_help import get_about_content
        text = get_about_content()
        assert isinstance(text, str)
        assert "Medical" in text or "Bot" in text

    def test_unknown_command_replies(self):
        pass  # Tested via handle_invalid_command integration

    @pytest.mark.asyncio
    async def test_group_chat_silently_ignored(self):
        """Help command in a group is silently dropped."""
        from handlers.about_help import help_command
        msg = _make_message(chat_type="group")

        with patch("handlers.about_help.db_fetchone", return_value=("approved",)):
            await help_command(msg)
            msg.reply.assert_not_awaited()


# ── utils/progress integration ─────────────────────────────────────────────

class TestProgressIntegration:
    """Multi-step download simulation."""

    def setup_method(self):
        import utils.progress as p
        p._active_downloads.clear()

    def test_full_download_lifecycle(self):
        """Simulate a complete download run with progress increments."""
        import utils.progress as p
        chat_id = 10101

        p.start_download(chat_id, "Surgery Notes", 3)
        assert p.is_downloading(chat_id)
        assert not p.is_cancelled(chat_id)

        for _ in range(3):
            p.increment_sent(chat_id)

        state = p.get_state(chat_id)
        assert state["sent"] == 3
        assert state["total"] == 3

        p.finish_download(chat_id)
        assert not p.is_downloading(chat_id)

    def test_cancel_mid_download(self):
        """Cancellation is reflected immediately."""
        import utils.progress as p
        chat_id = 20202

        p.start_download(chat_id, "Radiology", 50)
        p.increment_sent(chat_id)
        p.increment_sent(chat_id)
        p.request_cancel(chat_id)  # correct function name

        assert p.is_cancelled(chat_id)
        state = p.get_state(chat_id)
        assert state["sent"] == 2
        assert state["total"] == 50  # total unchanged

        p.finish_download(chat_id)
        assert not p.is_downloading(chat_id)


# ── Registry Integration ───────────────────────────────────────────────────

class TestHandlerRegistry:
    def test_register_all_handlers_runs_without_error(self):
        """Verify that register_all_handlers imports all modules and registers correctly."""
        from utils.register_handlers import register_all_handlers
        try:
            register_all_handlers()
        except Exception as e:
            pytest.fail(f"register_all_handlers() failed with exception: {e}")

