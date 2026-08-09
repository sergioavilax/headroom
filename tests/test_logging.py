"""One JSON line per request, actually emitted.

A request logger that merely *exists* logs nothing: Python's root level is ``WARNING``,
so an un-levelled ``logger.info`` disappears and the only symptom is a quiet container.
That is a bad failure to discover from the operator's terminal, so it is checked here —
and checked by capturing real records, not by asserting that a call was made.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator

import pytest

from headroom.core.log import PACKAGE_LOGGER, REQUEST_LOGGER, configure_logging
from headroom.providers.mock import MockScript

from .support.fixtures import anthropic_request
from .support.harness import GatewayHarness


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())


@pytest.fixture
def captured_logs() -> Iterator[_Capture]:
    configure_logging()
    handler = _Capture()
    REQUEST_LOGGER.addHandler(handler)
    try:
        yield handler
    finally:
        REQUEST_LOGGER.removeHandler(handler)


def test_configure_logging_gives_the_package_a_level_and_a_handler() -> None:
    configure_logging()

    assert PACKAGE_LOGGER.level == logging.INFO
    assert PACKAGE_LOGGER.handlers, "records would go nowhere"
    # Not propagated: uvicorn owns the root handlers, and propagating would print each
    # line twice in two different formats.
    assert PACKAGE_LOGGER.propagate is False
    assert REQUEST_LOGGER.isEnabledFor(logging.INFO)


def test_configure_logging_is_idempotent() -> None:
    configure_logging()
    before = len(PACKAGE_LOGGER.handlers)
    configure_logging()

    assert len(PACKAGE_LOGGER.handlers) == before


def test_the_level_can_be_raised_by_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HEADROOM_LOG_LEVEL", "warning")
    try:
        configure_logging()
        assert PACKAGE_LOGGER.level == logging.WARNING
    finally:
        monkeypatch.delenv("HEADROOM_LOG_LEVEL", raising=False)
        configure_logging()


async def test_a_request_emits_exactly_one_parseable_json_line(
    gateway: GatewayHarness, captured_logs: _Capture
) -> None:
    gateway.book.set("ok", MockScript.anthropic_stream("hello"))

    await gateway.post("/v1/messages", anthropic_request(stream=True), script="ok")

    assert len(captured_logs.lines) == 1
    logged = json.loads(captured_logs.lines[0])
    assert logged["request_id"] == gateway.last_context().request_id
    assert logged["outcome"] == "ok"
    assert logged["model"] == "mock-model-1"
    assert logged["provider"] == "mock"
    assert logged["ttft_ms"] is not None


async def test_a_failed_request_is_logged_too(
    gateway: GatewayHarness, captured_logs: _Capture
) -> None:
    """The lines worth having are the ones about requests that went wrong."""
    gateway.book.set("cut", MockScript.anthropic_stream("hello", cut_after_chunks=2))

    await gateway.post("/v1/messages", anthropic_request(stream=True), script="cut")

    logged = json.loads(captured_logs.lines[-1])
    assert logged["outcome"] == "upstream_stream_cut"
    assert logged["error_source"] == "upstream"
