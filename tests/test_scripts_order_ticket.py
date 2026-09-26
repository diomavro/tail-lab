"""scripts/order_ticket.py reads the API's JSON as untrusted input: stdlib only
(it is run as plain ``python``), so it checks the shape itself instead of
importing ``MarkedSchedule``. A wrong shape must be a named ValueError before
anything is printed as an order -- a half-read schedule printed as a ticket is
the one failure this rung of the execution ladder exists to prevent."""

from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "order_ticket", Path(__file__).resolve().parents[1] / "scripts" / "order_ticket.py"
)
assert _SPEC and _SPEC.loader
order_ticket = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(order_ticket)


class _Response(io.BytesIO):
    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


def _serve(monkeypatch: pytest.MonkeyPatch, body: object) -> None:
    monkeypatch.setattr(
        order_ticket.urllib.request,
        "urlopen",
        lambda *_a, **_k: _Response(json.dumps(body).encode()),
    )


_LEG = {
    "rank": 1,
    "asset": "spy",
    "quote_status": "quoted",
    "market_contracts": 2,
    "market_ask": 1.25,
    "listed_strike": 500.0,
    "listed_expiry": "2026-10-16",
    "premium_budget": 1000.0,
    "market_to_model_ratio": 3.0,
    "expected_roi_on_premium": 0.4,
}


def test_a_well_formed_schedule_prints_a_ticket(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _serve(monkeypatch, {"schedule_id": "s1", "as_of": "2026-09-26", "legs": [_LEG]})

    assert order_ticket.main([]) == 0
    out = capsys.readouterr().out
    assert "BUY   2 x SPY   500P 2026-10-16  @ 1.25 limit   $250 of $1,000" in out
    assert "3x the model's premium" in out


@pytest.mark.parametrize(
    "body",
    [[1, 2], {"legs": "not a list"}, {"legs": [None]}],
)
def test_a_schedule_of_the_wrong_shape_is_refused(
    monkeypatch: pytest.MonkeyPatch, body: object
) -> None:
    _serve(monkeypatch, body)
    with pytest.raises(ValueError):
        order_ticket.main([])


def test_a_non_numeric_price_on_a_placeable_leg_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """``contracts * ask`` on a string ask used to be a TypeError deep in the
    format -- or, for a string contract count, silent string repetition."""
    _serve(
        monkeypatch, {"schedule_id": "s", "as_of": "d", "legs": [{**_LEG, "market_ask": "1.25"}]}
    )
    with pytest.raises(ValueError, match="market_ask"):
        order_ticket.main([])
