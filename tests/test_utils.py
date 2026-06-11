"""
tests/test_utils.py — unit tests for pure utility modules.

These tests have NO external dependencies (no DB, no Telegram API, no network).
They run fast and are the first line of defence against regressions.

Coverage:
  - utils/bot_ref.py     → set_bot / get_bot lifecycle
  - utils/keyboard.py    → InlineBuilder API
  - utils/progress.py    → download state machine
  - utils/helpers.py     → esc() HTML escaping
"""
import pytest
from unittest.mock import MagicMock


# ── utils/bot_ref ──────────────────────────────────────────────────────────

class TestBotRef:
    def test_get_bot_before_set_raises(self):
        """get_bot() must raise RuntimeError if set_bot() was never called."""
        import utils.bot_ref as br
        original = br._bot_instance
        br._bot_instance = None  # reset for test isolation
        try:
            with pytest.raises(RuntimeError, match="not initialised"):
                br.get_bot()
        finally:
            br._bot_instance = original  # restore

    def test_set_and_get_bot(self):
        """set_bot() followed by get_bot() returns the same object."""
        import utils.bot_ref as br
        original = br._bot_instance
        mock_bot = MagicMock(name="MockBot")
        try:
            br.set_bot(mock_bot)
            assert br.get_bot() is mock_bot
        finally:
            br._bot_instance = original

    def test_set_bot_overwrite(self):
        """Calling set_bot() twice replaces the previous instance."""
        import utils.bot_ref as br
        original = br._bot_instance
        bot_a = MagicMock(name="BotA")
        bot_b = MagicMock(name="BotB")
        try:
            br.set_bot(bot_a)
            br.set_bot(bot_b)
            assert br.get_bot() is bot_b
        finally:
            br._bot_instance = original


# ── utils/keyboard ─────────────────────────────────────────────────────────

class TestInlineBuilder:
    """InlineBuilder must replicate v2 InlineKeyboardMarkup.add()/.row() behaviour."""

    def _make_btn(self, text, data=None):
        from aiogram.types import InlineKeyboardButton
        return InlineKeyboardButton(text=text, callback_data=data or text)

    def test_empty_builder_produces_empty_keyboard(self):
        from utils.keyboard import InlineBuilder
        from aiogram.types import InlineKeyboardMarkup
        kb = InlineBuilder().build()
        assert isinstance(kb, InlineKeyboardMarkup)
        assert kb.inline_keyboard == []

    def test_add_single_button(self):
        from utils.keyboard import InlineBuilder
        btn = self._make_btn("Click")
        kb = InlineBuilder().add(btn).build()
        # One row, one button
        assert len(kb.inline_keyboard) == 1
        assert kb.inline_keyboard[0][0].text == "Click"

    def test_add_multiple_buttons_same_row(self):
        from utils.keyboard import InlineBuilder
        b1 = self._make_btn("A")
        b2 = self._make_btn("B")
        kb = InlineBuilder().add(b1, b2).build()
        # Both in same row
        assert len(kb.inline_keyboard) == 1
        assert len(kb.inline_keyboard[0]) == 2

    def test_row_flushes_current_row(self):
        from utils.keyboard import InlineBuilder
        b1 = self._make_btn("R1")
        b2 = self._make_btn("R2")
        kb = InlineBuilder().add(b1).row(b2).build()
        # Two rows
        assert len(kb.inline_keyboard) == 2
        assert kb.inline_keyboard[0][0].text == "R1"
        assert kb.inline_keyboard[1][0].text == "R2"

    def test_row_without_prior_add(self):
        from utils.keyboard import InlineBuilder
        b = self._make_btn("Solo")
        kb = InlineBuilder().row(b).build()
        assert len(kb.inline_keyboard) == 1

    def test_chaining_returns_builder(self):
        from utils.keyboard import InlineBuilder
        builder = InlineBuilder()
        btn = self._make_btn("X")
        assert builder.add(btn) is builder
        assert builder.row() is builder

    def test_url_button(self):
        from utils.keyboard import InlineBuilder, kb_button
        btn = kb_button("Visit", url="https://example.com")
        kb = InlineBuilder().add(btn).build()
        assert kb.inline_keyboard[0][0].url == "https://example.com"

    def test_callback_button(self):
        from utils.keyboard import kb_button
        btn = kb_button("Tap", callback_data="action:42")
        assert btn.callback_data == "action:42"


# ── utils/progress ─────────────────────────────────────────────────────────

class TestProgress:
    """Download progress state machine — no I/O, purely in-memory."""

    def setup_method(self):
        """Reset the global state before each test."""
        import utils.progress as p
        # Clear any leftover state from previous tests
        p._active_downloads.clear()

    def test_start_and_finish_lifecycle(self):
        import utils.progress as p
        chat_id = 100
        p.start_download(chat_id, "Anatomy", 10)
        assert p.is_downloading(chat_id)
        p.finish_download(chat_id)
        assert not p.is_downloading(chat_id)

    def test_initial_state_values(self):
        import utils.progress as p
        p.start_download(111, "Physio", 20)
        state = p.get_state(111)
        assert state["folder_name"] == "Physio"
        assert state["total"] == 20
        assert state["sent"] == 0
        assert state["cancel"] is False  # actual key name in progress.py

    def test_increment_sent(self):
        import utils.progress as p
        p.start_download(222, "Biochem", 5)
        p.increment_sent(222)
        p.increment_sent(222)
        assert p.get_state(222)["sent"] == 2

    def test_cancel_signal(self):
        import utils.progress as p
        p.start_download(333, "Path", 8)
        assert not p.is_cancelled(333)  # is_cancelled exists
        p.request_cancel(333)           # correct function name
        assert p.is_cancelled(333)

    def test_cancel_nonexistent_download_is_noop(self):
        import utils.progress as p
        p.request_cancel(9999)  # should not raise
        assert not p.is_downloading(9999)

    def test_get_state_nonexistent_returns_none(self):
        import utils.progress as p
        assert p.get_state(9999) is None

    def test_progress_message_id_setter(self):
        import utils.progress as p
        p.start_download(444, "Micro", 3)
        p.set_progress_msg_id(444, 99)
        assert p.get_state(444)["msg_id"] == 99  # actual key is 'msg_id'

    def test_build_progress_text_structure(self):
        import utils.progress as p
        p.start_download(555, "Pharma", 10)
        p.increment_sent(555)
        p.increment_sent(555)
        text = p.build_progress_text(555, file_interval=60)
        assert "2" in text   # sent count
        assert "10" in text  # total count
        assert isinstance(text, str)
        assert len(text) > 0

    def test_duplicate_start_overwrites(self):
        """Starting a download when one is already active should overwrite cleanly."""
        import utils.progress as p
        p.start_download(666, "Old", 5)
        p.increment_sent(666)
        p.start_download(666, "New", 15)  # overwrite
        state = p.get_state(666)
        assert state["folder_name"] == "New"
        assert state["total"] == 15
        assert state["sent"] == 0  # reset

    def test_is_downloading_false_before_start(self):
        import utils.progress as p
        assert not p.is_downloading(77777)


# ── utils/helpers ──────────────────────────────────────────────────────────

class TestEscape:
    """esc() must produce safe HTML for Telegram HTML parse_mode."""

    def test_plain_text_unchanged(self):
        from utils.helpers import esc
        assert esc("hello world") == "hello world"

    def test_ampersand_escaped(self):
        from utils.helpers import esc
        result = esc("Tom & Jerry")
        # The raw '&' should only appear as part of '&amp;', not as standalone
        assert result == "Tom &amp; Jerry"

    def test_angle_brackets_escaped(self):
        from utils.helpers import esc
        result = esc("<b>bold</b>")
        assert "<" not in result
        assert "&lt;" in result
        assert "&gt;" in result

    def test_quotes_escaped(self):
        from utils.helpers import esc
        result = esc('"quoted"')
        assert '"' not in result

    def test_none_returns_empty_string(self):
        from utils.helpers import esc
        assert esc(None) == ""

    def test_integer_converted(self):
        from utils.helpers import esc
        assert esc(42) == "42"

    def test_injection_attempt(self):
        from utils.helpers import esc
        malicious = '<script>alert("xss")</script>'
        safe = esc(malicious)
        assert "<script>" not in safe
        assert "alert" in safe  # text is preserved, tags are not
