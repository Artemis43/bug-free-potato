"""
utils/keyboard.py — aiogram v3 keyboard compatibility helpers.

aiogram v2 had InlineKeyboardMarkup.add() and .row() methods.
In v3, InlineKeyboardMarkup is immutable — you pass inline_keyboard= directly.

This module provides:
  - InlineBuilder: a mutable builder with .add() / .row() / .build() API
    that matches v2's InlineKeyboardMarkup behaviour exactly.
  - kb_row(*buttons): shorthand to create a single-row keyboard.
  - kb_button(text, callback_data=None, url=None): shorthand button factory.

Usage (replaces v2 InlineKeyboardMarkup):
    from utils.keyboard import InlineBuilder, kb_button

    kb = InlineBuilder()
    kb.add(kb_button("Click me", callback_data="click"))
    kb.row(
        kb_button("Left",  callback_data="left"),
        kb_button("Right", callback_data="right"),
    )
    markup = kb.build()    # → InlineKeyboardMarkup
"""
from __future__ import annotations
from typing import Optional
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton


def kb_button(
    text: str,
    *,
    callback_data: Optional[str] = None,
    url: Optional[str] = None,
) -> InlineKeyboardButton:
    """Shorthand factory for a single inline button."""
    return InlineKeyboardButton(
        text=text,
        callback_data=callback_data,
        url=url,
    )


class InlineBuilder:
    """
    Mutable builder that replicates aiogram v2's InlineKeyboardMarkup.add()
    and .row() API, producing a v3-compatible InlineKeyboardMarkup on .build().
    """

    def __init__(self) -> None:
        self._rows: list[list[InlineKeyboardButton]] = []
        self._current_row: list[InlineKeyboardButton] = []

    def add(self, *buttons: InlineKeyboardButton) -> "InlineBuilder":
        """Add buttons to the current row (like v2 .add())."""
        for btn in buttons:
            self._current_row.append(btn)
        return self

    def row(self, *buttons: InlineKeyboardButton) -> "InlineBuilder":
        """
        Flush the current row and start a new one containing *buttons*.
        Matches v2 keyboard.row(*buttons) exactly.
        """
        if self._current_row:
            self._rows.append(self._current_row)
            self._current_row = []
        if buttons:
            self._rows.append(list(buttons))
        return self

    def build(self) -> InlineKeyboardMarkup:
        """Return the completed InlineKeyboardMarkup."""
        rows = list(self._rows)
        if self._current_row:
            rows.append(self._current_row)
        return InlineKeyboardMarkup(inline_keyboard=rows)

    # Allow using the builder itself where a markup is expected
    def __call__(self) -> InlineKeyboardMarkup:
        return self.build()
