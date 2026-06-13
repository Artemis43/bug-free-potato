"""
tests/test_storage.py — multi-channel storage: registry helpers, channel
command parsing, and the upload fan-out logic in handlers/document.

DB and network are fully mocked — no real Postgres or Telegram calls.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ── utils/storage registry ──────────────────────────────────────────────────

class TestStorageRegistry:
    def test_list_active_only_adds_filter(self):
        import utils.storage as storage
        with patch("utils.storage.db_fetchall", return_value=[]) as m:
            storage.list_storage_channels(active_only=True)
        sql = m.call_args[0][0]
        assert "active = TRUE" in sql
        assert "ORDER BY id" in sql

    def test_list_all_has_no_active_filter(self):
        import utils.storage as storage
        with patch("utils.storage.db_fetchall", return_value=[]) as m:
            storage.list_storage_channels()
        sql = m.call_args[0][0]
        assert "WHERE" not in sql

    def test_add_channel_returns_id_and_stringifies_chat_id(self):
        import utils.storage as storage
        with patch("utils.storage.db_execute_returning", return_value=(7,)) as m:
            new_id = storage.add_storage_channel(-100123456789, "Backup")
        assert new_id == 7
        params = m.call_args[0][1]
        assert params[0] == "-100123456789"   # stored as TEXT
        assert params[1] == "Backup"

    def test_record_file_locations_inserts_each_copy(self):
        import utils.storage as storage
        with patch("utils.storage.db_execute") as m:
            storage.record_file_locations(5, [(1, 100), (2, 200), (3, 300)])
        assert m.call_count == 3

    def test_get_file_locations_active_only_filters(self):
        import utils.storage as storage
        with patch("utils.storage.db_fetchall", return_value=[]) as m:
            storage.get_file_locations(5, active_only=True)
        sql = m.call_args[0][0]
        assert "active = TRUE" in sql

    def test_get_file_locations_can_include_disabled(self):
        import utils.storage as storage
        with patch("utils.storage.db_fetchall", return_value=[]) as m:
            storage.get_file_locations(5, active_only=False)
        sql = m.call_args[0][0]
        assert "active = TRUE" not in sql


# ── handlers/channels command parsing ───────────────────────────────────────

class TestChannelCommandParsing:
    def test_parse_trailing_id_valid(self):
        from handlers.channels import _parse_trailing_id
        assert _parse_trailing_id("/removechannel_12") == 12
        assert _parse_trailing_id("/togglechannel_3") == 3

    def test_parse_trailing_id_invalid(self):
        from handlers.channels import _parse_trailing_id
        assert _parse_trailing_id("/removechannel_") is None
        assert _parse_trailing_id("/removechannel") is None
        assert _parse_trailing_id("") is None
        assert _parse_trailing_id(None) is None


# ── handlers/document upload fan-out ────────────────────────────────────────

def _doc_message():
    msg = MagicMock()
    msg.document.file_id   = "FILEID"
    msg.document.file_name = "notes.pdf"
    msg.video = None
    msg.photo = None
    msg.caption = None
    return msg


class TestUploadFanout:
    @pytest.mark.asyncio
    async def test_fans_out_to_all_channels(self):
        import handlers.document as doc
        msg = _doc_message()
        bot = MagicMock()
        bot.send_document = AsyncMock(side_effect=lambda *a, **k: MagicMock(message_id=999))
        channels = [(1, "-100A", "A", True), (2, "-100B", "B", True), (3, "-100C", "C", True)]

        with patch("handlers.document.get_bot", return_value=bot), \
             patch("handlers.document._build_caption", return_value="cap"), \
             patch("handlers.document.db_execute_returning", return_value=(42,)) as ins, \
             patch("handlers.document.record_file_locations") as rec:
            result = await doc._store_file(msg, folder_id=9, channels=channels)

        assert bot.send_document.await_count == 3            # one send per channel
        assert result == ("notes.pdf", 3, 3)                 # name, replicas, total
        ins.assert_called_once()
        assert len(rec.call_args[0][1]) == 3                 # 3 locations recorded

    @pytest.mark.asyncio
    async def test_partial_failure_still_stores_surviving_copies(self):
        import handlers.document as doc
        msg = _doc_message()
        bot = MagicMock()
        seq = {"n": 0}

        def flaky(*a, **k):
            seq["n"] += 1
            if seq["n"] == 2:               # second channel is "down"
                raise Exception("channel down")
            return MagicMock(message_id=500 + seq["n"])

        bot.send_document = AsyncMock(side_effect=flaky)
        channels = [(1, "-100A", "A", True), (2, "-100B", "B", True), (3, "-100C", "C", True)]

        with patch("handlers.document.get_bot", return_value=bot), \
             patch("handlers.document._build_caption", return_value="cap"), \
             patch("handlers.document.db_execute_returning", return_value=(42,)), \
             patch("handlers.document.record_file_locations") as rec, \
             patch("handlers.document.create_pending_replications") as pend:
            result = await doc._store_file(msg, folder_id=9, channels=channels)

        assert result == ("notes.pdf", 2, 3)                 # 2 of 3 survived
        assert len(rec.call_args[0][1]) == 2
        pend.assert_called_once_with(42, {2})                # channel 2 was missed

    @pytest.mark.asyncio
    async def test_all_channels_fail_returns_none_and_writes_nothing(self):
        import handlers.document as doc
        msg = _doc_message()
        bot = MagicMock()
        bot.send_document = AsyncMock(side_effect=Exception("down"))
        channels = [(1, "-100A", "A", True)]

        with patch("handlers.document.get_bot", return_value=bot), \
             patch("handlers.document._build_caption", return_value="cap"), \
             patch("handlers.document.db_execute_returning") as ins, \
             patch("handlers.document.record_file_locations") as rec:
            result = await doc._store_file(msg, folder_id=9, channels=channels)

        assert result is None
        ins.assert_not_called()      # no logical file row if nothing was stored
        rec.assert_not_called()
