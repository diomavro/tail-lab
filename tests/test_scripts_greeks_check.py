"""`make greeks-check`: our delta against the exchange's, with measured q.

It is the one independent check that the dividend yield moves our model toward
the market's (the HYG 0.11 -> 0.02 result in PR #157). A script that silently
priced at q = 0, or mislabelled where q came from, would report that check as
passed on the old numbers.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pandas as pd
import pytest

from tail_lab.contracts.option_chain import DATASET
from tail_lab.lake.store import DeltaLakeStore
from tail_lab.research import dividends
from tail_lab.research.option_pricer import BlackScholesPricer
from tests.test_research_dividends import seed_tiingo_eod

SESSION = dt.date(2026, 10, 9)


def _load() -> ModuleType:
    path = Path(__file__).parents[1] / "scripts" / "greeks_check.py"
    spec = importlib.util.spec_from_file_location("greeks_check", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["greeks_check"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def lake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> DeltaLakeStore:
    """HYG pays 6% (measured from Tiingo); the exchange's deltas are the model's
    AT that 6%, so measured scoring is exact and q = 0 scoring is not."""
    dividends._memo.clear()
    store = DeltaLakeStore(tmp_path)
    seed_tiingo_eod(store, ["hyg"], SESSION, annual_yield=0.06, close=80.0)
    q = dividends.dividend_lookup(store, "hyg", SESSION).lookup(SESSION).q
    expiry = SESSION + dt.timedelta(days=60)
    t = (expiry - SESSION).days / 365.25
    rows = [
        {
            "underlying": "HYG",
            "quote_date": pd.Timestamp(SESSION),
            "expiration": pd.Timestamp(expiry),
            "strike": float(k),
            "bid": 1.0,
            "ask": 1.1,
            "volume": 10,
            "open_interest": 500,
            "spot": 80.0,
            "iv": 0.12,
            "delta": BlackScholesPricer()
            .greeks_put(spot=80.0, strike=float(k), t_years=t, r=0.04, sigma=0.12, q=q)
            .delta,
            "theo": None,
        }
        for k in range(72, 82)
    ]
    store.write_bronze(DATASET, SESSION, pd.DataFrame(rows))
    gc = _load()
    monkeypatch.setattr(gc, "get_lake_store", lambda: store)
    return store


def _run(capsys: pytest.CaptureFixture[str], *flags: str) -> str:
    gc = sys.modules["greeks_check"]
    assert gc.main(["--as-of", SESSION.isoformat(), *flags]) == 0
    return capsys.readouterr().out


def _hyg_row(out: str) -> list[str]:
    return next(line for line in out.splitlines() if line.startswith("HYG")).split()


def test_measured_q_reproduces_the_exchanges_delta(
    lake: DeltaLakeStore, capsys: pytest.CaptureFixture[str]
) -> None:
    row = _hyg_row(_run(capsys))
    _, _, median, _, q, source = row
    assert float(median) < 1e-5
    assert float(q) == pytest.approx(0.0619, abs=1e-3) and source == "measured"


def test_no_dividends_rescoring_shows_the_old_bias_and_never_reads_yields(
    lake: DeltaLakeStore, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    gc = sys.modules["greeks_check"]

    def boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("--no-dividends must not read the yields")

    monkeypatch.setattr(gc, "dividend_lookup", boom)
    out = _run(capsys, "--no-dividends")
    _, _, median, _, q, source = _hyg_row(out)
    assert float(median) > 0.005  # a 6% payer scored at q = 0 is visibly off
    assert (float(q), source) == (0.0, "-")
    assert "q = 0 for every name" in out
