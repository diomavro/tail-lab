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
