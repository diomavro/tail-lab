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
def cboe_pput_sample() -> str:
    """Real PPUT rows straight from Cboe's CDN: the 1986 inception days plus
    the Sep-Oct 2008 crisis window, so the parser is pinned against actual
    source formatting (and against a period the strategy actually cares
    about) rather than a hand-invented shape."""
    return (FIXTURES_DIR / "cboe_pput_sample.csv").read_text()
