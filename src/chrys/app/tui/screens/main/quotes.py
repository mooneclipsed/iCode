# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Pending conversation quotes waiting to ride the next user message.

The controller owns the ordered pending list and the add limits; the wire
format itself lives in :mod:`chrys.app.tui.widgets.chat.quote_format`, which
both this layer and the transcript widget import. UI refreshes (the input
chip) ride the ``on_changed`` callback instead of a widget reference, so the
controller stays free of concrete widgets.
"""

from __future__ import annotations

from collections.abc import Callable
from enum import Enum

from chrys.app.tui.widgets.chat.quote_format import (
    MAX_PENDING_QUOTES,
    SINGLE_QUOTE_MAX_CHARS,
    TOTAL_QUOTE_MAX_CHARS,
    Quote,
    compose_outgoing_text,
)
from chrys.foundation.i18n import MessageRef, msg

_QUOTE_LIMIT_SINGLE = msg(
    "tui.quote.limit_single",
    fallback="A quote can contain at most 8,000 characters.",
)
_QUOTE_LIMIT_COUNT = msg(
    "tui.quote.limit_count",
    fallback="You can attach at most 8 conversation quotes.",
)
_QUOTE_LIMIT_TOTAL = msg(
    "tui.quote.limit_total",
    fallback="Conversation quotes can contain at most 16,000 characters in total.",
)


def quote_limit_notice(outcome: QuoteAddOutcome) -> MessageRef:
    """The user-facing notice explaining a rejected quote add."""
    if outcome is QuoteAddOutcome.REJECTED_COUNT_LIMIT:
        return _QUOTE_LIMIT_COUNT.bind()
    if outcome is QuoteAddOutcome.REJECTED_TOTAL_LIMIT:
        return _QUOTE_LIMIT_TOTAL.bind()
    return _QUOTE_LIMIT_SINGLE.bind()


class QuoteAddOutcome(Enum):
    """Why an add attempt succeeded or was rejected."""

    ADDED = "added"
    REJECTED_SINGLE_LIMIT = "rejected_single_limit"
    REJECTED_COUNT_LIMIT = "rejected_count_limit"
    REJECTED_TOTAL_LIMIT = "rejected_total_limit"


class QuoteController:
    """The pending-quote buffer behind the input chip."""

    def __init__(self, on_changed: Callable[[], None] | None = None) -> None:
        self._quotes: list[Quote] = []
        self._on_changed = on_changed

    @property
    def quotes(self) -> tuple[Quote, ...]:
        """The pending quotes in the order they were added."""
        return tuple(self._quotes)

    def __len__(self) -> int:
        return len(self._quotes)

    def set_on_changed(self, on_changed: Callable[[], None]) -> None:
        """Bind the UI refresh callback (the screen wires it after mount)."""
        self._on_changed = on_changed

    def add(self, text: str, source: str | None) -> QuoteAddOutcome:
        """Add one quoted selection, enforcing the add limits."""
        stripped = text.strip()
        if not stripped:
            return QuoteAddOutcome.REJECTED_SINGLE_LIMIT
        if len(text) > SINGLE_QUOTE_MAX_CHARS:
            return QuoteAddOutcome.REJECTED_SINGLE_LIMIT
        if len(self._quotes) >= MAX_PENDING_QUOTES:
            return QuoteAddOutcome.REJECTED_COUNT_LIMIT
        if sum(len(quote.text) for quote in self._quotes) + len(text) > TOTAL_QUOTE_MAX_CHARS:
            return QuoteAddOutcome.REJECTED_TOTAL_LIMIT
        self._quotes.append(Quote(text=text, source=source))
        self._notify_changed()
        return QuoteAddOutcome.ADDED

    def remove_at(self, index: int) -> None:
        """Drop the pending quote at *index* when it exists."""
        if 0 <= index < len(self._quotes):
            del self._quotes[index]
            self._notify_changed()

    def clear(self) -> None:
        """Drop every pending quote (send, session switch, restore)."""
        if self._quotes:
            self._quotes.clear()
            self._notify_changed()

    def compose_outgoing(self, typed: str) -> str:
        """Compose the wire text for *typed* with the pending quotes."""
        return compose_outgoing_text(typed, self._quotes)

    def consume(self) -> None:
        """Clear the buffer once its quotes ride a published message."""
        self.clear()

    def _notify_changed(self) -> None:
        if self._on_changed is not None:
            self._on_changed()
