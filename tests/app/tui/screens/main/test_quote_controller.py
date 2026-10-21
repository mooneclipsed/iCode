# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for the pending-quote buffer and its composition into sent messages."""

from __future__ import annotations

import pytest

from chrys.app.tui.screens.main.input_flow import InputFlowController
from chrys.app.tui.screens.main.quotes import QuoteAddOutcome, QuoteController
from chrys.app.tui.screens.main.state import MainScreenServices, MainScreenState
from chrys.app.tui.widgets.chat.quote_format import (
    MAX_PENDING_QUOTES,
    SINGLE_QUOTE_MAX_CHARS,
    SOURCE_USER,
    Quote,
    compose_outgoing_text,
    split_selections_block,
)
from chrys.foundation.events.bus import EventBus
from chrys.foundation.events.types import InvocationMessage, UserMessage


class _RecordingView:
    """The InputFlowView surface the quote paths touch."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def set_terminal_title_for_user_message(self, text: str) -> None:
        self.calls.append(f"title:{text}")

    def current_chat_session_id(self) -> str:
        return "session"

    def add_input_history(self, text: str, *, session_id: str | None) -> None:
        self.calls.append(f"history:{text}")

    def set_retry_mode(self, enabled: bool, *, label: str = "Retry") -> None:
        self.calls.append(f"retry_mode:{enabled}")

    def start_run_status(self, label: object) -> None:
        self.calls.append("status")

    def lock_input_with_text(self) -> None:
        self.calls.append("lock_input")

    def unlock_input_keep_if_locked(self) -> None:
        self.calls.append("unlock_input")

    def restore_input_draft(self, text: str) -> bool:
        self.calls.append(f"restore_draft:{text}")
        return True

    def flash_submit_still_sending(self) -> None:
        self.calls.append("flash_still_sending")

    def unlock_input_clearing(self, text: str) -> None:
        self.calls.append(f"unlock_clearing:{text}")

    def update_toc(self) -> None:
        self.calls.append("toc")

    async def render_user_message(self, text: str, *, created_at: object, contents: object) -> None:
        self.calls.append(f"render:{text}")


def _make_flow(
    bus: EventBus, quotes: QuoteController, view: _RecordingView
) -> tuple[InputFlowController, MainScreenState]:
    state = MainScreenState()

    async def _noop_agent_message(_event: InvocationMessage) -> None:
        return None

    controller = InputFlowController(
        state=state,
        services=MainScreenServices(bus=bus),
        view=view,  # type: ignore[arg-type]
        quotes=quotes,
        start_worker=lambda work: None,
        handle_agent_message=_noop_agent_message,
        handle_error=_noop_agent_message,  # type: ignore[arg-type]
        set_agent_running=lambda _running: None,
        set_has_messages=lambda _has: None,
        clear_workspace_marker=lambda: None,
        clear_pending_questions=lambda: None,
        commit_profile_switch_marker=lambda: None,
        post_gc_message=lambda _message: None,
        debug=lambda *_args: None,
    )
    return controller, state


def test_add_accepts_and_orders_quotes() -> None:
    quotes = QuoteController()

    assert quotes.add("first", SOURCE_USER) is QuoteAddOutcome.ADDED
    assert quotes.add("second", None) is QuoteAddOutcome.ADDED
    assert quotes.quotes == (Quote("first", SOURCE_USER), Quote("second", None))


def test_add_rejects_empty_and_oversized_text() -> None:
    quotes = QuoteController()

    assert quotes.add("   \n", None) is QuoteAddOutcome.REJECTED_SINGLE_LIMIT
    assert quotes.add("x" * (SINGLE_QUOTE_MAX_CHARS + 1), None) is QuoteAddOutcome.REJECTED_SINGLE_LIMIT
    assert not quotes.quotes


def test_add_enforces_count_and_total_budgets() -> None:
    quotes = QuoteController()

    for index in range(MAX_PENDING_QUOTES):
        assert quotes.add(f"q{index}", None) is QuoteAddOutcome.ADDED
    assert quotes.add("one too many", None) is QuoteAddOutcome.REJECTED_COUNT_LIMIT

    total = QuoteController()
    filler = "x" * SINGLE_QUOTE_MAX_CHARS
    assert total.add(filler, None) is QuoteAddOutcome.ADDED
    assert total.add(filler, None) is QuoteAddOutcome.ADDED
    assert total.add("y", None) is QuoteAddOutcome.REJECTED_TOTAL_LIMIT


def test_remove_clear_and_consume_notify_once_per_real_change() -> None:
    changes: list[int] = []
    quotes = QuoteController(on_changed=lambda: changes.append(1))

    quotes.clear()
    assert changes == []
    quotes.add("q", None)
    quotes.remove_at(0)
    quotes.remove_at(0)
    assert not quotes.quotes
    assert len(changes) == 2
    quotes.add("q", None)
    quotes.consume()
    assert not quotes.quotes
    assert len(changes) == 4


@pytest.mark.asyncio
async def test_send_user_message_composes_quotes_into_wire_text() -> None:
    bus = EventBus()
    published: list[UserMessage] = []

    async def _on_message(event: UserMessage) -> None:
        published.append(event)
        # The engine's admission handler marks accepted turns; mirror it so
        # the flow reaches the open-turn rendering path.
        event.started_turn = True

    await bus.subscribe(UserMessage, _on_message)
    quotes = QuoteController()
    assert quotes.add("quoted answer", "assistant") is QuoteAddOutcome.ADDED
    view = _RecordingView()
    controller, _state = _make_flow(bus, quotes, view)

    await controller.send_user_message("what does this mean")

    assert len(published) == 1
    visible, parsed = split_selections_block(published[0].text)
    assert visible == "what does this mean"
    assert parsed == [Quote("quoted answer", "assistant")]
    # The bubble renders the wire text; history and title keep the typed text.
    assert "history:what does this mean" in view.calls
    assert "title:what does this mean" in view.calls
    assert any(call.startswith("render:what does this mean\n\n# Conversation selections:") for call in view.calls)
    assert not quotes.quotes


@pytest.mark.asyncio
async def test_send_user_message_without_a_turn_restores_wire_text_and_consumes() -> None:
    bus = EventBus()
    quotes = QuoteController()
    quotes.add("quoted", None)
    view = _RecordingView()
    controller, _state = _make_flow(bus, quotes, view)

    await controller.send_user_message("typed")

    # No handler started a turn: the full wire text returns to the composer so
    # the quoted content stays editable, and the pending quotes are consumed
    # into it (a resend must not compose them a second time).
    assert view.calls == [f"restore_draft:{compose_outgoing_text('typed', [Quote('quoted')])}"]
    assert not quotes.quotes


@pytest.mark.asyncio
async def test_send_user_message_blocked_keeps_quotes_pending() -> None:
    bus = EventBus()
    quotes = QuoteController()
    quotes.add("quoted", None)
    view = _RecordingView()
    controller, state = _make_flow(bus, quotes, view)

    async def _blocking_handler(_event: UserMessage) -> None:
        state.submit.blocked = True

    await bus.subscribe(UserMessage, _blocking_handler)

    await controller.send_user_message("typed")

    assert quotes.quotes == (
        Quote(
            "quoted",
        ),
    )
    assert not view.calls


@pytest.mark.asyncio
async def test_queue_injection_composes_quotes_into_injected_text() -> None:
    bus = EventBus()
    events: list[UserMessage] = []

    async def _on_message(event: UserMessage) -> None:
        events.append(event)

    await bus.subscribe(UserMessage, _on_message)
    quotes = QuoteController()
    quotes.add("mid-run quote", None)
    view = _RecordingView()
    controller, state = _make_flow(bus, quotes, view)

    await controller.queue_injection("while running")

    assert len(events) == 1
    visible, parsed = split_selections_block(events[0].text)
    assert visible == "while running"
    assert parsed == [Quote("mid-run quote")]
    # The pending-injection tracker holds the wire text: an undo returns it.
    assert state.pending_injection.text == events[0].text
    assert not quotes.quotes
