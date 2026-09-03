from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def vix_yahoo_sample() -> dict[str, Any]:
    raw: dict[str, Any] = json.loads((FIXTURES_DIR / "vix_yahoo_sample.json").read_text())
    return raw


@pytest.fixture
def ohlcv_yahoo_sample() -> dict[str, Any]:
    raw: dict[str, Any] = json.loads((FIXTURES_DIR / "ohlcv_yahoo_sample.json").read_text())
    return raw


@pytest.fixture
def options_expiry_yahoo_sample() -> dict[str, Any]:
    raw: dict[str, Any] = json.loads(
        (FIXTURES_DIR / "options_expiry_yahoo_sample.json").read_text()
    )
    return raw


@pytest.fixture
def fomc_calendar_sample() -> str:
    """Real markup from federalreserve.gov/monetarypolicy/fomccalendars.htm --
    the full 2024 panel (fetched 2026-09-01), covering a plain two-day
    meeting, four Summary-of-Economic-Projections meetings (the ``*`` flag),
    and one month-spanning meeting (``Apr/May``, ``30-1``) -- the one shape
    a hand-written fixture would be tempted to skip."""
    return (FIXTURES_DIR / "fomc_calendar_sample.html").read_text()


@pytest.fixture
def cboe_pput_sample() -> str:
    """Real PPUT rows straight from Cboe's CDN: the 1986 inception days plus
    the Sep-Oct 2008 crisis window, so the parser is pinned against actual
    source formatting (and against a period the strategy actually cares
    about) rather than a hand-invented shape."""
    return (FIXTURES_DIR / "cboe_pput_sample.csv").read_text()


@pytest.fixture
def cboe_vix_sample() -> str:
    """Real Cboe VIX_History.csv rows: the 1990 inception days plus the
    Oct-2008 and Mar-2020 spikes, so the parser is pinned against actual
    source formatting and against the regimes the platform exists to study."""
    return (FIXTURES_DIR / "cboe_vix_sample.csv").read_text()


@pytest.fixture
def nasdaq_ohlcv_sample() -> dict[str, Any]:
    """A real Nasdaq historical payload (SPY), kept intact down to the ``$``
    prefixes and thousands separators the parser has to strip."""
    raw: dict[str, Any] = json.loads((FIXTURES_DIR / "nasdaq_ohlcv_sample.json").read_text())
    return raw


@pytest.fixture
def sp500_constituents_sample() -> str:
    """Real rows from `fja05680/sp500`'s historical-components CSV: the 1996
    inception window, a 2007 pre-crisis slice, and the latest 2026 rows -- so
    the parser is pinned against actual source formatting (quoted,
    comma-joined ticker lists) rather than a hand-invented shape."""
    return (FIXTURES_DIR / "sp500_constituents_sample.csv").read_text()


@pytest.fixture
def cboe_chain_sample() -> dict[str, Any]:
    """A real Cboe delayed-quote chain slice (SPY) spanning six expiries,
    with two contracts each -- enough to prove de-duplication works."""
    raw: dict[str, Any] = json.loads((FIXTURES_DIR / "cboe_chain_sample.json").read_text())
    return raw


@pytest.fixture
def vix_futures_h2020_sample() -> str:
    """Real Cboe VX_2020-03-18.csv rows: the Mar-2020 (H) contract's listing
    window (including a genuine no-trade day, OHLC all zero-filled) plus its
    final three weeks into expiry, spanning the COVID vol spike -- so the
    parser is pinned against actual source formatting and against the
    zero-fill convention it has to detect."""
    return (FIXTURES_DIR / "vix_futures_h2020_sample.csv").read_text()


@pytest.fixture
def mpd_stats_sample() -> str:
    """Real Minneapolis Fed MPD rows: the whole Sep-Oct 2008 crisis window for
    ``sp12m`` (the market this platform cares about), plus one row each for a
    single-name market (``bac``), an inflation market (``infl1y``, whose
    ``lg_change_decr``/``lg_change_incr`` mean something different from the
    equity markets' -- see the source's own preamble note) and a row with a
    blank (``NA``) ``maturity_target``, so the parser is pinned against every
    quirk the real file actually contains rather than a hand-invented shape."""
    return (FIXTURES_DIR / "mpd_stats_sample.csv").read_text()


@pytest.fixture
def lambdaclass_vendor_dir() -> Path:
    """Directory holding a small **real** slice of the lambdaclass ``data-v1``
    SPY chains, cut straight from the 632 MB original: two roll dates (one
    pre-2015 with its Saturday expiration, one mid-COVID), each carrying both
    calls and puts across the target monthly expiry *and* deliberate decoy
    expiries that are too near and too far. The extractor's whole job is
    filtering, so a fixture with nothing to reject would prove nothing.

    The files keep the vendor's own column names and dtypes, including the
    sentinel-filled ``mark``/IV/greek columns the contract drops
    (``docs/DATA_VERDICTS.md``)."""
    return FIXTURES_DIR / "lambdaclass"
