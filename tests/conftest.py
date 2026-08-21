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
