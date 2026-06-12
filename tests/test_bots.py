"""
tests/test_bots.py — multi-bot registry/pairing (utils/bots) and the
copy_message delivery fallback (handlers/download._deliver_file).

DB and Telegram are fully mocked.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError


# ── utils/bots registry & pairing ───────────────────────────────────────────

class TestBotRegistry:
    def test_register_bot_caches_pk(self):
        import utils.bots as bots
        with patch("utils.bots.db_execute_returning", return_value=(3,)):
            pk = bots.register_bot(name="TestBot")
        assert pk == 3
        assert bots.get_current_bot_pk() == 3

    def test_register_bot_handles_no_row(self):
        import utils.bots as bots
        with patch("utils.bots.db_execute_returning", return_value=None):
            assert bots.register_bot() is None

    def test_get_servable_locations_filters_by_bot_and_active(self):
        import utils.bots as bots
        with patch("utils.bots.db_fetchall", return_value=[]) as m:
            bots.get_servable_locations(file_pk=10, bot_pk=2)
        sql, params = m.call_args[0]
        assert "bc.bot_id = %s" in sql
        assert "sc.active = TRUE" in sql
        assert "ORDER BY sc.id" in sql
        assert params == (2, 10)

    def test_pair_and_unpair_issue_writes(self):
        import utils.bots as bots
        with patch("utils.bots.db_execute") as m:
            bots.pair_bot_channel(1, 5)
            bots.unpair_bot_channel(1, 5)
        assert m.call_count == 2


# ── handlers/download._deliver_file (copy_message + fallback) ────────────────

def _badrequest(msg="message to copy not found"):
    return TelegramBadRequest(method=None, message=msg)

def _blocked():
    return TelegramForbiddenError(method=None, message="Forbidden: bot was blocked by the user")

def _no_access():
    return TelegramForbiddenError(method=None, message="Forbidden: bot is not a member of the channel chat")


class TestDeliverFile:
    @pytest.mark.asyncio
    async def test_first_channel_succeeds_no_fallback(self):
        from handlers.download import _deliver_file
        bot = MagicMock()
        bot.copy_message = AsyncMock(return_value=MagicMock(message_id=77))
        sent, blocked = await _deliver_file(bot, 5, [("-100A", 11), ("-100B", 22)])
        assert sent.message_id == 77
        assert blocked is False
        assert bot.copy_message.await_count == 1

    @pytest.mark.asyncio
    async def test_falls_back_to_next_channel(self):
        from handlers.download import _deliver_file
        bot = MagicMock()
        seq = {"n": 0}

        def flaky(chat, src, msg):
            seq["n"] += 1
            if seq["n"] == 1:
                raise _badrequest()           # first channel can't serve it
            return MagicMock(message_id=99)

        bot.copy_message = AsyncMock(side_effect=flaky)
        sent, blocked = await _deliver_file(bot, 5, [("-100A", 11), ("-100B", 22)])
        assert sent.message_id == 99
        assert blocked is False
        assert bot.copy_message.await_count == 2   # used the fallback

    @pytest.mark.asyncio
    async def test_lost_access_falls_back(self):
        from handlers.download import _deliver_file
        bot = MagicMock()
        seq = {"n": 0}

        def flaky(chat, src, msg):
            seq["n"] += 1
            if seq["n"] == 1:
                raise _no_access()            # forbidden on the SOURCE channel
            return MagicMock(message_id=42)

        bot.copy_message = AsyncMock(side_effect=flaky)
        sent, blocked = await _deliver_file(bot, 5, [("-100A", 11), ("-100B", 22)])
        assert sent.message_id == 42
        assert blocked is False

    @pytest.mark.asyncio
    async def test_all_channels_fail_returns_none(self):
        from handlers.download import _deliver_file
        bot = MagicMock()
        bot.copy_message = AsyncMock(side_effect=_badrequest())
        sent, blocked = await _deliver_file(bot, 5, [("-100A", 11), ("-100B", 22)])
        assert sent is None
        assert blocked is False
        assert bot.copy_message.await_count == 2

    @pytest.mark.asyncio
    async def test_user_blocked_aborts_immediately(self):
        from handlers.download import _deliver_file
        bot = MagicMock()
        bot.copy_message = AsyncMock(side_effect=_blocked())
        sent, blocked = await _deliver_file(bot, 5, [("-100A", 11), ("-100B", 22)])
        assert sent is None
        assert blocked is True
        assert bot.copy_message.await_count == 1   # did NOT try the next channel

    @pytest.mark.asyncio
    async def test_no_locations_returns_none(self):
        from handlers.download import _deliver_file
        bot = MagicMock()
        bot.copy_message = AsyncMock()
        sent, blocked = await _deliver_file(bot, 5, [])
        assert sent is None and blocked is False
        bot.copy_message.assert_not_awaited()
