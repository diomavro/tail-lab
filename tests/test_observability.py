"""Tests for structured run logging (``observability.py``, ``docs/STANDARDS.md`` §f)."""

from __future__ import annotations

import logging

import pytest

from tail_lab.observability import configure_logging, log_event


def test_configure_logging_is_idempotent() -> None:
    configure_logging()
    configure_logging()  # second call must be a no-op, not raise or double-config
    assert logging.getLogger("tail_lab").level == logging.INFO


def test_log_event_formats_key_value_line(caplog: pytest.LogCaptureFixture) -> None:
    logger = logging.getLogger("tail_lab.test.obs")
    with caplog.at_level(logging.INFO, logger="tail_lab.test.obs"):
        log_event(
            logger,
            "putlab.backtest",
            asset="spy",
            roi=1.234567891,
            spaced="a b",
            flag=True,
            dropped=None,
        )
    msg = caplog.records[-1].getMessage()
    assert msg.startswith("event=putlab.backtest ")
    assert "asset=spy" in msg
    assert "roi=1.23457" in msg  # compact float
    assert 'spaced="a b"' in msg  # quoted because it contains a space
    assert "flag=true" in msg  # bool rendered lowercase
    assert "dropped" not in msg  # None fields are omitted, not stringified


def test_log_event_omits_none_only() -> None:
    logger = logging.getLogger("tail_lab.test.obs2")
    records: list[logging.LogRecord] = []
    handler = logging.Handler()
    handler.emit = records.append  # type: ignore[method-assign]
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        log_event(logger, "e", a=0, b=None, c=False)
    finally:
        logger.removeHandler(handler)
    msg = records[-1].getMessage()
    assert "a=0" in msg  # zero is a real value, kept
    assert "c=false" in msg  # False is a real value, kept
    assert "b=" not in msg  # only None is dropped
