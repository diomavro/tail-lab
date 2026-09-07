from __future__ import annotations

import logging

import pandas as pd
import pytest

from tail_lab.ingestion.sources import AllSourcesFailed, Source, find_header_line, first_available

_LOGGER = logging.getLogger("tail_lab.ingestion.sources_test")


def _rows(n: int = 2) -> pd.DataFrame:
    return pd.DataFrame({"a": range(n)})


def _boom() -> pd.DataFrame:
    raise RuntimeError("endpoint returned 429")


def test_first_source_wins_when_healthy() -> None:
    result, source_id = first_available(
        [Source("primary", lambda: _rows()), Source("backup", lambda: _rows(5))],
        dataset="d",
        logger=_LOGGER,
    )
    assert source_id == "primary"
    assert len(result) == 2


def test_falls_through_to_the_backup_when_primary_raises() -> None:
    """The whole point of the module: the 429 that killed Yahoo must demote
    to the next source rather than fail the ingest."""
    result, source_id = first_available(
        [Source("primary", _boom), Source("backup", lambda: _rows(5))],
        dataset="d",
        logger=_LOGGER,
    )
    assert source_id == "backup"
    assert len(result) == 5


def test_an_empty_source_is_a_failed_source() -> None:
    """A source answering 200-with-no-rows must not stop the chain. If it
    did, an empty bronze partition would be written, and because as-of
    resolution reads the latest partition it would shadow good data."""
    result, source_id = first_available(
        [Source("primary", lambda: _rows(0)), Source("backup", lambda: _rows(3))],
        dataset="d",
        logger=_LOGGER,
    )
    assert source_id == "backup"
    assert len(result) == 3


def test_exhausted_chain_raises_and_names_every_failure() -> None:
    """Fail loud: never return empty, and carry enough detail to diagnose
    which sources died and how."""
    with pytest.raises(AllSourcesFailed) as excinfo:
        first_available(
            [Source("primary", _boom), Source("backup", lambda: _rows(0))],
            dataset="vix",
            logger=_LOGGER,
        )
    err = excinfo.value
    assert err.dataset == "vix"
    assert set(err.errors) == {"primary", "backup"}
    assert "429" in err.errors["primary"]
    assert "no rows" in err.errors["backup"]
    assert "vix" in str(err)


def test_empty_source_list_raises_rather_than_returning_empty() -> None:
    with pytest.raises(AllSourcesFailed):
        first_available([], dataset="d", logger=_LOGGER)


def test_later_sources_are_not_called_once_one_succeeds() -> None:
    """Ordering is preference, and a healthy primary must not cost a request
    to every fallback behind it."""
    calls: list[str] = []

    def _tracked(name: str) -> pd.DataFrame:
        calls.append(name)
        return _rows()

    first_available(
        [
            Source("primary", lambda: _tracked("primary")),
            Source("backup", lambda: _tracked("backup")),
        ],
        dataset="d",
        logger=_LOGGER,
    )
    assert calls == ["primary"]


def test_fallback_use_is_logged_loudly(caplog: pytest.LogCaptureFixture) -> None:
    """A fallback that quietly becomes the steady state is how a dataset's
    provenance drifts without anyone deciding it should -- so the run log
    must say both which source served the data and that it was a fallback."""
    with caplog.at_level(logging.INFO, logger="tail_lab.ingestion.sources_test"):
        first_available(
            [Source("primary", _boom), Source("backup", lambda: _rows(4))],
            dataset="ohlcv_spy",
            logger=_LOGGER,
        )
    text = "\n".join(caplog.messages)
    assert "outcome=failed" in text
    assert "source=primary" in text
    assert "source=backup" in text
    assert "outcome=ok" in text
    assert "fallback=true" in text
    assert "dataset=ohlcv_spy" in text


def test_healthy_primary_is_not_logged_as_a_fallback(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO, logger="tail_lab.ingestion.sources_test"):
        first_available([Source("primary", lambda: _rows())], dataset="d", logger=_LOGGER)
    assert "fallback=true" not in "\n".join(caplog.messages)


# --- find_header_line: shared by every Cboe-CDN adapter (vix, cboe_strategy,
# mpd, vix_complex) so a preamble above the header doesn't shift a parse's
# columns ------------------------------------------------------------------


def test_find_header_line_with_no_preamble() -> None:
    assert find_header_line("DATE,CLOSE\n01/02/2026,15.0\n") == 0


def test_find_header_line_skips_a_preamble() -> None:
    raw = "some vendor banner\nanother line\nDATE,CLOSE\n01/02/2026,15.0\n"
    assert find_header_line(raw) == 2


def test_find_header_line_is_case_insensitive() -> None:
    assert find_header_line("date,close\n01/02/2026,15.0\n") == 0


def test_find_header_line_with_a_custom_prefix() -> None:
    """mpd.py's file opens with two free-text lines above a ``"market",...``
    header -- a different prefix than the ``DATE,`` every other adapter uses."""
    raw = 'preamble line one\npreamble line two\n"market","idt"\nsp12m,2026-01-02\n'
    assert find_header_line(raw, header_prefix='"market"') == 2


def test_find_header_line_defaults_to_zero_when_absent() -> None:
    assert find_header_line("no header here\njust data\n") == 0
