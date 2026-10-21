# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Wire and display format for quoted conversation selections.

Quoting a transcript selection appends a machine-parseable fenced block to
the outgoing user text, following the same shape as the desktop client's
``userselect`` blocks: a ``# Conversation selections:`` header, then a
``chrys-conversation-selections`` fence wrapping a JSON array of
``{"source": ..., "text": ...}`` entries. The model reads the block as plain
text; the TUI parses it back to render a quote view instead of raw JSON.

This module is pure logic on purpose: the widget layer (``UserMessage``) and
the screen layer (the pending-quote controller) both import it, so it may
not import either of them.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from chrys.foundation.i18n import msg

if TYPE_CHECKING:
    from chrys.foundation.i18n import MessageRef

SOURCE_USER = "user"
SOURCE_ASSISTANT = "assistant"
_QUOTE_SOURCES = frozenset({SOURCE_USER, SOURCE_ASSISTANT})

# Display labels for the wire sources; shared by the transcript bubble view
# and the quote-manager dialog, which live in different TUI layers.
SOURCE_USER_LABEL = msg("tui.quote.source_user", fallback="User message")
SOURCE_ASSISTANT_LABEL = msg("tui.quote.source_assistant", fallback="Assistant message")
SOURCE_UNKNOWN_LABEL = msg("tui.quote.source_unknown", fallback="Conversation")


def source_label_reference(source: str | None) -> MessageRef:
    """The localized label for a quote's wire ``source`` value."""
    if source == SOURCE_USER:
        return SOURCE_USER_LABEL.bind()
    if source == SOURCE_ASSISTANT:
        return SOURCE_ASSISTANT_LABEL.bind()
    return SOURCE_UNKNOWN_LABEL.bind()


SELECTIONS_HEADER = "# Conversation selections:"
SELECTIONS_FENCE = "chrys-conversation-selections"

# Mirrors the desktop client's quote budgets.
SINGLE_QUOTE_MAX_CHARS = 8_000
MAX_PENDING_QUOTES = 8
TOTAL_QUOTE_MAX_CHARS = 16_000

_PREVIEW_MAX_CHARS = 80

# One trailing block at the very end of the message, preceded by a blank
# line. Must stay in sync with ``compose_outgoing_text``.
_TRAILING_SELECTIONS = re.compile(
    rf"(?:\n\n)?{re.escape(SELECTIONS_HEADER)}\n"
    rf"```{SELECTIONS_FENCE}\n"
    rf"(.*?)\n"
    rf"```\s*$",
    re.DOTALL,
)


@dataclass(frozen=True, slots=True)
class Quote:
    """One quoted conversation snippet bound for the wire block."""

    text: str
    source: str | None = None

    def wire_dict(self) -> dict[str, str]:
        """The JSON entry for this quote; ``source`` is omitted when unknown."""
        if self.source is None:
            return {"text": self.text}
        return {"source": self.source, "text": self.text}


def compose_outgoing_text(typed: str, quotes: list[Quote] | tuple[Quote, ...]) -> str:
    """Append the selections block for *quotes* to *typed*.

    Without quotes the text is returned untouched; with quotes it is
    right-stripped and the block follows a blank line, so the writer and the
    ``_TRAILING_SELECTIONS`` reader agree byte-for-byte.
    """
    if not quotes:
        return typed
    payload = json.dumps([quote.wire_dict() for quote in quotes], ensure_ascii=False)
    block = f"{SELECTIONS_HEADER}\n```{SELECTIONS_FENCE}\n{payload}\n```"
    base = typed.rstrip()
    return f"{base}\n\n{block}" if base else block


def split_selections_block(text: str) -> tuple[str, list[Quote]]:
    """Split *text* into (visible text, quotes) when it ends in a block.

    A block whose JSON is malformed — or whose entries are not plain
    ``{"source"?, "text"}`` objects — is left in place as ordinary message
    text: rendering must never silently drop user content.
    """
    match = _TRAILING_SELECTIONS.search(text)
    if match is None:
        return text, []
    try:
        entries = json.loads(match.group(1))
    except ValueError:
        return text, []
    if not isinstance(entries, list) or not entries:
        return text, []
    quotes: list[Quote] = []
    for entry in entries:
        if not isinstance(entry, dict):
            return text, []
        source = entry.get("source")
        quote_text = entry.get("text")
        if set(entry) - {"source", "text"}:
            return text, []
        if not isinstance(quote_text, str) or (
            source is not None and (not isinstance(source, str) or source not in _QUOTE_SOURCES)
        ):
            return text, []
        quotes.append(Quote(text=quote_text, source=source))
    visible = text[: match.start()].rstrip("\n")
    return visible, quotes


def quote_preview(text: str, max_chars: int = _PREVIEW_MAX_CHARS) -> str:
    """Collapse *text* into one short line for chip and dialog rows."""
    collapsed = " ".join(text.split())
    if len(collapsed) <= max_chars:
        return collapsed
    return collapsed[: max_chars - 1].rstrip() + "…"
