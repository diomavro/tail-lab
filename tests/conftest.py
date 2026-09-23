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
def nasdaq_earnings_sample() -> dict[str, Any]:
    """Real payload from api.nasdaq.com/api/calendar/earnings, fetched live
    2026-09-06 for a weekday with a full slate: 29 rows spanning all three
    ``time`` codes (pre-market, after-hours, not-supplied)."""
    raw: dict[str, Any] = json.loads((FIXTURES_DIR / "nasdaq_earnings_sample.json").read_text())
    return raw


@pytest.fixture
def nasdaq_earnings_empty_sample() -> dict[str, Any]:
    """Real payload for a date with no scheduled earnings (a weekend,
    fetched live 2026-09-06): ``data.rows`` comes back ``null``, not ``[]``."""
    raw: dict[str, Any] = json.loads(
        (FIXTURES_DIR / "nasdaq_earnings_empty_sample.json").read_text()
    )
    return raw


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
def vix3m_sample() -> str:
    """Real Cboe VIX3M_History.csv rows (full OHLC shape): the 2009 inception
    window plus the latest 2026 rows, fetched live 2026-09-07."""
    return (FIXTURES_DIR / "vix3m_sample.csv").read_text()


@pytest.fixture
def vix9d_sample() -> str:
    """Real Cboe VIX9D_History.csv rows (full OHLC shape): the 2011 inception
    window plus the latest 2026 rows, fetched live 2026-09-07."""
    return (FIXTURES_DIR / "vix9d_sample.csv").read_text()


@pytest.fixture
def vvix_sample() -> str:
    """Real Cboe VVIX_History.csv rows (bare ``DATE,VVIX`` shape, unlike
    VIX3M/VIX9D): the 2006 inception window plus the latest 2026 rows,
    fetched live 2026-09-07."""
    return (FIXTURES_DIR / "vvix_sample.csv").read_text()


@pytest.fixture
def skew_sample() -> str:
    """Real Cboe SKEW_History.csv rows (bare ``DATE,SKEW`` shape): the 1990
    inception window plus the latest 2026 rows, fetched live 2026-09-07."""
    return (FIXTURES_DIR / "skew_sample.csv").read_text()


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
    """Directory holding a SYNTHETIC stand-in for the lambdaclass ``data-v1``
    SPY chains, generated by ``scripts/make_test_fixtures.py``.

    It was a real slice cut from the 632 MB vendor original. It is not any
    more: that file is commercial option data redistributed under
    research-only terms, so shipping a slice of it in git makes this repo a
    second republisher of someone else's paid data (`HUMAN_TODO.md`, the
    2026-08-22 licence call). Nothing in the suite needed the values to be
    real -- only correctly shaped -- so they are invented.

    Every structural quirk the tests assert on is preserved: two roll dates
    (one pre-2015 with its **Saturday** expiration, one mid-COVID), both calls
    and puts, the target monthly expiry plus deliberate decoy expiries too
    near and too far, strikes inside and outside the moneyness band, and a
    one-sided and a crossed quote. The extractor's whole job is filtering, so
    a fixture with nothing to reject would prove nothing.

    The files keep the vendor's own column names and dtypes, including the
    sentinel-filled ``mark``/IV/greek columns the contract drops
    (``docs/DATA_VERDICTS.md``).

    Verified load-bearing, not decorative: letting calls through the put-only
    filter turns two of these tests red."""
    return FIXTURES_DIR / "lambdaclass"
