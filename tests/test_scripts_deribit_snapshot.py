"""The daily Deribit capture's driver (``scripts/deribit_snapshot.py``,
``docs/adr/0032``): its exit codes are the contract with the scheduler, and
one book failing must never stop the others being sent.

Nothing touches the network: ``fetch_book`` (on the ingestion module, the one
reference both modes call) and ``requests.post`` are replaced. ``--local``
writes only to a tmp lake: the getters are patched, and the env override
below is the backstop if a patch is ever missed -- the checkout's ``.env``
points at production Tigris.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import os
import subprocess
import sys
import textwrap
import threading
from collections.abc import Callable, Mapping
from pathlib import Path

import pytest
import requests
import urllib3

from tail_lab.contracts.deribit_options import DERIBIT_BOOKS, REFUSAL_MARKER, DeribitApiError
from tail_lab.ingestion import deribit_options as ingestion
from tail_lab.ingestion.deribit_options import FetchedBook
from tail_lab.lake.blob_store import BlobStore
from tail_lab.lake.store import DeltaLakeStore
from tests.test_contracts_deribit_options import CREATED_MS, NOW
from tests.test_ingestion_deribit_options import Payload, raw_book, raw_book_row

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "deribit_snapshot.py"
_SPEC = importlib.util.spec_from_file_location("deribit_snapshot", SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
deribit_snapshot = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(deribit_snapshot)

BOOKS = list(DERIBIT_BOOKS)


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TAIL_LAB_LAKE_BACKEND", "local")
    monkeypatch.setenv("TAIL_LAB_LAKE_ROOT", str(tmp_path / "lake"))
    monkeypatch.setattr(deribit_snapshot, "_utcnow", lambda: NOW)


@pytest.fixture(autouse=True)
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Every backoff the POST loop takes, recorded instead of slept."""
    taken: list[float] = []
    monkeypatch.setattr(deribit_snapshot.time, "sleep", taken.append)
    return taken


def _clean_books(n: int = 120) -> dict[str, tuple[object, object]]:
    return {c: raw_book(c, n) for c in BOOKS}


def _fetch_from(
    monkeypatch: pytest.MonkeyPatch, payloads: Mapping[str, tuple[object, object] | Exception]
) -> list[str]:
    fetched: list[str] = []

    def fetch(currency: str) -> FetchedBook:
        fetched.append(currency)
        got = payloads[currency]
        if isinstance(got, Exception):
            raise got
        return FetchedBook(got[0], got[1], 2, 99)

    monkeypatch.setattr(ingestion, "fetch_book", fetch)
    return fetched


# ---- --post -------------------------------------------------------------------------


def answer(currency: str = "BTC", **overrides: object) -> dict[str, object]:
    """A committed response for ``currency``'s book."""
    body: dict[str, object] = {
        "dataset": DERIBIT_BOOKS[currency].dataset,
        "snapshot_date": "2026-10-30",
        "rows": 120,
        "quarantined": 0,
        "quarantine_key": None,
        "quarantine_written": None,
        "bronze_path": "/lake/x",
        "expired_dropped": 0,
        "committed": True,
        "already_captured": False,
        "previous_partition_exists": True,
        "quarantine_share": 0.0,
    }
    body.update(overrides)
    return body


def _response(status: int, body: object) -> requests.Response:
    resp = requests.Response()
    resp.status_code = status
    resp._content = body.encode() if isinstance(body, str) else json.dumps(body).encode()
    resp.url = "https://tail-lab.test/x"
    return resp


Step = requests.Response | Exception


class FakeApp:
    """Scripted answers per book, in order; records every POST body."""

    def __init__(self, steps: Mapping[str, list[Step]]) -> None:
        self.steps = {c.lower(): list(s) for c, s in steps.items()}
        self.posts: list[tuple[str, dict[str, object]]] = []

    def __call__(self, url: str, *, json: dict[str, object], **_kw: object) -> requests.Response:
        currency = url.rsplit("/", 1)[-1]
        self.posts.append((currency, json))
        step = self.steps[currency].pop(0)
        if isinstance(step, Exception):
            raise step
        return step


def _ok(currency: str, **overrides: object) -> requests.Response:
    return _response(200, answer(currency, **overrides))


def _run_post(
    monkeypatch: pytest.MonkeyPatch,
    btc: list[Step],
    *,
    payloads: Mapping[str, tuple[object, object] | Exception] | None = None,
    token: str = "t",
) -> tuple[int, FakeApp]:
    app = FakeApp({"BTC": btc, "ETH": [_ok("ETH")], "USDC": [_ok("USDC")]})
    monkeypatch.setattr(deribit_snapshot.requests, "post", app)
    _fetch_from(monkeypatch, payloads or _clean_books())
    code = deribit_snapshot.main(["--post", "https://tail-lab.test", "--token", token])
    return code, app


def _500() -> requests.Response:
    return _response(500, "boom")


@pytest.mark.parametrize(
    ("btc", "expected"),
    [
        ([_ok("BTC")], 0),
        ([_ok("BTC", committed=False, already_captured=True, rows=0)], 0),
        (
            [
                _500(),
                _ok(
                    "BTC",
                    committed=False,
                    already_captured=True,
                    previous_partition_exists=False,
                    captured_observed_at_ms=CREATED_MS - 3_600_000,
                ),
            ],
            0,
        ),
        ([_ok("BTC", committed=False, already_captured=True, previous_partition_exists=False)], 0),
        ([_ok("BTC", previous_partition_exists=False)], 1),
        ([_ok("BTC", previous_partition_exists=None)], 0),
        ([_response(409, {"detail": f"{REFUSAL_MARKER}stale"})], 1),
        ([_ok("BTC", quarantined=5, quarantine_share=0.025, quarantine_written=True)], 1),
        ([_ok("BTC", committed=False, rows=0, bronze_path=None, quarantine_share=1.0)], 1),
        ([_ok("BTC", quarantined=1, quarantine_share=0.005, quarantine_written=False)], 1),
        ([_response(425, {"detail": f"{REFUSAL_MARKER}too early"})], 1),
        ([_response(422, {"detail": f"{REFUSAL_MARKER}no live rows"})], 1),
        ([_response(401, {"detail": "unauthorized"})], 1),
        ([_response(404, {"detail": "not found"})], 1),
        ([_response(200, "<html>not json")], 1),
        ([_500(), _500(), _500(), _500()], 1),
        ([_response(429, {})] * 4, 1),
        ([_ok("BTC", quarantined=1, quarantine_share=0.005, quarantine_written=True)], 0),
    ],
    ids=[
        "committed",
        "already-captured",
        "captured-earlier-previous-false",
        "catch-up-previous-false",
        "committed-previous-false",
        "committed-previous-none",
        "409",
        "share-over-1pct",
        "all-quarantined",
        "quarantine-unwritten",
        "425",
        "422",
        "401",
        "404",
        "unparseable-200",
        "retries-exhausted",
        "rate-limited",
        "share-under-1pct",
    ],
)
def test_the_exit_code_table(
    monkeypatch: pytest.MonkeyPatch, btc: list[Step], expected: int
) -> None:
    code, app = _run_post(monkeypatch, btc)
    assert code == expected
    assert [c for c, _ in app.posts].count("eth") == 1
    assert [c for c, _ in app.posts].count("usdc") == 1


def test_425_says_when_to_re_run(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _run_post(monkeypatch, [_response(425, {"detail": f"{REFUSAL_MARKER}too early"})])
    assert "too early, re-run after 08:05 UTC" in capsys.readouterr().err


def test_an_unparseable_200_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """It may have landed; re-sending cannot make the body parse."""
    code, app = _run_post(monkeypatch, [_response(200, "<html>")])
    assert code == 1
    assert [c for c, _ in app.posts].count("btc") == 1


def test_a_body_the_client_cannot_encode_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    code, app = _run_post(
        monkeypatch, [requests.exceptions.InvalidJSONError("Out of range float values")]
    )
    assert code == 1
    assert [c for c, _ in app.posts].count("btc") == 1


def test_a_missing_token_exits_2_and_sends_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    fetched = _fetch_from(monkeypatch, _clean_books())
    app = FakeApp({})
    monkeypatch.setattr(deribit_snapshot.requests, "post", app)
    assert deribit_snapshot.main(["--post", "https://tail-lab.test"]) == 2
    assert fetched == [] and app.posts == []


@pytest.mark.parametrize(
    "failure", [requests.ConnectionError("dns"), DeribitApiError("empty result")]
)
def test_btc_failing_to_fetch_still_sends_eth_and_usdc(
    monkeypatch: pytest.MonkeyPatch, failure: Exception
) -> None:
    payloads: dict[str, tuple[object, object] | Exception] = {**_clean_books(), "BTC": failure}
    code, app = _run_post(monkeypatch, [], payloads=payloads)
    assert code == 1
    assert [c for c, _ in app.posts] == ["eth", "usdc"]


@pytest.mark.parametrize(
    "btc",
    [
        [_response(409, {"detail": f"{REFUSAL_MARKER}incomplete"})],
        [requests.ConnectionError("reset")] * 4,
    ],
    ids=["409", "transport"],
)
def test_btc_failing_to_post_still_sends_eth_and_usdc(
    monkeypatch: pytest.MonkeyPatch, btc: list[Step]
) -> None:
    code, app = _run_post(monkeypatch, btc)
    assert code == 1
    assert [c for c, _ in app.posts][-2:] == ["eth", "usdc"]


def test_the_posted_listed_comes_from_the_instruments(monkeypatch: pytest.MonkeyPatch) -> None:
    book, instruments = raw_book("BTC", 121)
    code, app = _run_post(
        monkeypatch, [_ok("BTC")], payloads={**_clean_books(), "BTC": (book[:120], instruments)}
    )
    assert code == 0
    body = dict(app.posts)["btc"]
    assert instruments[120]["instrument_name"] in body["listed"]  # type: ignore[operator]
    assert len(body["rows"]) == 120  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "btc",
    [[_ok("ETH")], [_response(200, {"dataset": "deribit_options_btc"})]],
    ids=["wrong-dataset", "not-a-result"],
)
def test_a_200_that_is_not_this_books_result_fails_the_book(
    monkeypatch: pytest.MonkeyPatch, btc: list[Step]
) -> None:
    assert _run_post(monkeypatch, btc)[0] == 1


def test_may_have_landed_is_not_set_by_a_429_or_a_connect_timeout() -> None:
    def http(status: int) -> requests.HTTPError:
        return requests.HTTPError(response=_response(status, ""))

    assert not deribit_snapshot._failed_after_send(http(429))
    assert not deribit_snapshot._failed_after_send(requests.exceptions.ConnectTimeout())
    assert deribit_snapshot._failed_after_send(http(502))
    assert deribit_snapshot._failed_after_send(requests.ConnectionError())
    assert deribit_snapshot._failed_after_send(requests.exceptions.ReadTimeout())


def test_book_url_lowercases_the_path() -> None:
    assert (
        deribit_snapshot.book_url("https://x.dev/", "USDC")
        == "https://x.dev/api/ingest/deribit-options/usdc"
    )


# ---- payload faults, both modes ----------------------------------------------------


def _structural(fault: str) -> tuple[object, object]:
    book, instruments = raw_book("BTC", 120)
    if fault == "null-row":
        book[3] = None  # type: ignore[call-overload]
    elif fault == "no-name":
        del book[3]["instrument_name"]
    elif fault == "int-name":
        book[3]["instrument_name"] = 5
    elif fault == "non-list":
        return {"rows": book}, instruments
    else:
        book[3]["creation_timestamp"] = {
            "str-ts": "1793364731777",
            "nan-ts": float("nan"),
            "true-ts": True,
            "huge-ts": 10**400,
        }[fault]
    return book, instruments


STRUCTURAL = [
    "null-row",
    "no-name",
    "int-name",
    "str-ts",
    "nan-ts",
    "true-ts",
    "huge-ts",
    "non-list",
]


def _local_lake(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> DeltaLakeStore:
    root = tmp_path / "lake"
    root.mkdir(exist_ok=True)
    store = DeltaLakeStore(root)
    monkeypatch.setattr("tail_lab.config.get_lake_store", lambda: store)
    monkeypatch.setattr("tail_lab.config.get_quarantine_blobs", lambda: BlobStore(root))
    return store


def _committed(store: DeltaLakeStore) -> dict[str, int]:
    out = {}
    for currency, book in DERIBIT_BOOKS.items():
        if store.bronze_ingest_dates(book.dataset):
            out[currency] = len(store.read_bronze_as_of(book.dataset, NOW.date()))
    return out


@pytest.mark.parametrize("fault", STRUCTURAL)
def test_a_structural_fault_fails_btc_only_under_post(
    monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    code, app = _run_post(monkeypatch, [], payloads={**_clean_books(), "BTC": _structural(fault)})
    assert code == 1
    assert [c for c, _ in app.posts] == ["eth", "usdc"]


@pytest.mark.parametrize("fault", STRUCTURAL)
def test_a_structural_fault_fails_btc_only_under_local(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fault: str
) -> None:
    store = _local_lake(monkeypatch, tmp_path)
    _fetch_from(monkeypatch, {**_clean_books(), "BTC": _structural(fault)})
    assert deribit_snapshot.main(["--local"]) == 1
    assert _committed(store) == {"ETH": 120, "USDC": 120}


FIELD_FAULTS: list[tuple[str, str, object, str]] = [
    ("book", "bid_price", True, "bid=True"),
    ("book", "bid_price", "0.01", "bid='0.01'"),
    ("instruments", "expiration_timestamp", True, "expiration_timestamp_ms=True"),
    ("instruments", "expiration_timestamp", 10**400, "expiration_timestamp_ms="),
    ("book", "volume_usd", 10**400, "volume_usd="),
    ("instruments", "option_type", 5, "right=5"),
    ("instruments", "option_type", "PUT", "right='PUT'"),
]


def _field_fault(side: str, field: str, value: object) -> tuple[Payload, Payload]:
    book, instruments = raw_book("BTC", 120)
    (book if side == "book" else instruments)[3][field] = value
    return book, instruments


@pytest.mark.parametrize(("side", "field", "value", "named"), FIELD_FAULTS)
def test_a_field_fault_costs_one_row_under_local(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    side: str,
    field: str,
    value: object,
    named: str,
) -> None:
    store = _local_lake(monkeypatch, tmp_path)
    _fetch_from(monkeypatch, {**_clean_books(), "BTC": _field_fault(side, field, value)})
    assert deribit_snapshot.main(["--local"]) == 0
    assert _committed(store) == {"BTC": 119, "ETH": 120, "USDC": 120}
    (blob,) = BlobStore(tmp_path / "lake").list_json("ops/quarantine/deribit_options_btc")
    (row,) = blob["rows"]  # type: ignore[misc]
    assert row["reason"] == "invalid_value"
    assert row["payload"]["invalid_fields"][0].startswith(named)


@pytest.mark.parametrize(("side", "field", "value", "named"), FIELD_FAULTS)
def test_a_field_fault_costs_one_row_under_post(
    monkeypatch: pytest.MonkeyPatch, side: str, field: str, value: object, named: str
) -> None:
    code, app = _run_post(
        monkeypatch,
        [_ok("BTC")],
        payloads={**_clean_books(), "BTC": _field_fault(side, field, value)},
    )
    assert code == 0
    rows = dict(app.posts)["btc"]["rows"]
    assert rows[3]["invalid_fields"][0].startswith(named)  # type: ignore[index]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda b: b[3].pop("mark_price"),
        lambda b: b[3].update(quote_currency=None),
        lambda b: b[3].pop("quote_currency"),
    ],
    ids=["missing-mark", "null-currency", "missing-currency"],
)
def test_a_missing_field_quarantines_that_row_and_btc_commits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mutate: Callable[[Payload], object]
) -> None:
    store = _local_lake(monkeypatch, tmp_path)
    book, instruments = raw_book("BTC", 120)
    mutate(book)
    _fetch_from(monkeypatch, {**_clean_books(), "BTC": (book, instruments)})
    assert deribit_snapshot.main(["--local"]) == 0
    assert _committed(store)["BTC"] == 119


def test_dropped_instruments_rows_are_counted_and_btc_commits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store = _local_lake(monkeypatch, tmp_path)
    book, instruments = raw_book("BTC", 120)
    _fetch_from(monkeypatch, {**_clean_books(), "BTC": (book, [None, {"strike": 1}, *instruments])})
    assert deribit_snapshot.main(["--local"]) == 0
    assert _committed(store)["BTC"] == 120
    assert "btc: instruments_dropped=2" in capsys.readouterr().out


# ---- --local ---------------------------------------------------------------------------


def test_a_btc_refusal_under_local_still_lands_eth_and_usdc(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store = _local_lake(monkeypatch, tmp_path)
    book, instruments = raw_book("BTC", 120, created_ms=CREATED_MS - 40 * 60_000)
    _fetch_from(monkeypatch, {**_clean_books(), "BTC": (book, instruments)})
    assert deribit_snapshot.main(["--local"]) == 1
    assert _committed(store) == {"ETH": 120, "USDC": 120}


def test_a_non_serialisable_quarantine_under_local_fails_btc_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from tail_lab.contracts import deribit_options as contract

    store = _local_lake(monkeypatch, tmp_path)
    real = contract.quarantine_document

    def poisoned(**kwargs: object) -> dict[str, object]:
        return {**real(**kwargs), "poison": {1}}  # type: ignore[arg-type]

    monkeypatch.setattr(contract, "quarantine_document", poisoned)
    book, instruments = raw_book("BTC", 120)
    book[2]["mark_price"] = -1.0
    _fetch_from(monkeypatch, {**_clean_books(), "BTC": (book, instruments)})
    assert deribit_snapshot.main(["--local"]) == 1
    assert _committed(store) == {"ETH": 120, "USDC": 120}


def test_a_lake_write_error_under_local_fails_that_book(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store = _local_lake(monkeypatch, tmp_path)
    real = store.write_bronze

    def fails_for_btc(dataset: str, day: dt.date, df: object) -> str:
        if dataset == "deribit_options_btc":
            raise OSError("disk full")
        return real(dataset, day, df)  # type: ignore[arg-type]

    monkeypatch.setattr(store, "write_bronze", fails_for_btc)
    _fetch_from(monkeypatch, _clean_books())
    assert deribit_snapshot.main(["--local"]) == 1
    assert _committed(store) == {"ETH": 120, "USDC": 120}


def test_local_reads_the_clock_per_book_after_its_fetch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Each fetch takes 6 minutes of a fake clock. Read once before the loop,
    the clock would put ETH (t0+6) and USDC (t0+12) past the 5-minute
    future bound and refuse both."""
    store = _local_lake(monkeypatch, tmp_path)
    clock = [NOW]

    def fetch(currency: str) -> FetchedBook:
        clock[0] += dt.timedelta(minutes=6)
        book, instruments = raw_book(currency, 120, created_ms=int(clock[0].timestamp() * 1000))
        return FetchedBook(book, instruments, 2, 0)

    monkeypatch.setattr(ingestion, "fetch_book", fetch)
    monkeypatch.setattr(deribit_snapshot, "_utcnow", lambda: clock[0])
    assert deribit_snapshot.main(["--local"]) == 0
    assert _committed(store) == {"BTC": 120, "ETH": 120, "USDC": 120}


def test_a_failed_quarantine_write_under_local_exits_1(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store = _local_lake(monkeypatch, tmp_path)
    (tmp_path / "lake" / "ops").write_text("a file, so the blob directory cannot exist")
    book, instruments = raw_book("BTC", 300)
    book[9]["mark_price"] = -1.0
    _fetch_from(monkeypatch, {**_clean_books(), "BTC": (book, instruments)})
    assert deribit_snapshot.main(["--local"]) == 1
    assert _committed(store) == {"BTC": 299, "ETH": 120, "USDC": 120}


# ---- configure_logging, in a real process ------------------------------------------

_SHIM = textwrap.dedent(
    """
    import datetime as dt, importlib.util, sys
    sys.path.insert(0, {tests_parent!r})
    import tail_lab.ingestion.deribit_options as ingestion
    from tests.test_ingestion_deribit_options import raw_book

    spec = importlib.util.spec_from_file_location("deribit_snapshot", {script!r})
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    pinned = dt.datetime(2026, 10, 30, 12, 0, tzinfo=dt.UTC)
    mod._utcnow = lambda: pinned
    created = int(pinned.timestamp() * 1000) - 60_000

    def fetch(currency):
        book, instruments = raw_book(currency, 303 if currency == "BTC" else 120, created_ms=created)
        if currency == "BTC":
            for row in book[1:4]:
                row["mark_price"] = -1.0
        return ingestion.FetchedBook(book, instruments, 2, 0)

    ingestion.fetch_book = fetch
    raise SystemExit(mod.main(["--local"]))
    """
)


def test_main_configures_logging_so_the_unwritten_rows_reach_stderr(tmp_path: Path) -> None:
    """In-process cannot test this: pytest's root logger already has
    handlers, and importing ``api.main`` sets ``_CONFIGURED``. So: a real
    process, a local lake in ``tmp_path`` (never the checkout's ``.env``),
    and a plain file named ``ops`` so the blob write fails. Without
    ``configure_logging()`` Python's last-resort handler drops INFO and the
    only copy of the three rows is lost."""
    lake = tmp_path / "lake"
    lake.mkdir()
    (lake / "ops").write_text("a file")
    shim = _SHIM.format(tests_parent=str(SCRIPT.parents[1]), script=str(SCRIPT))
    env = {**os.environ, "TAIL_LAB_LAKE_BACKEND": "local", "TAIL_LAB_LAKE_ROOT": str(lake)}
    proc = subprocess.run(
        [sys.executable, "-c", shim],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    lines = [
        line
        for line in proc.stderr.splitlines()
        if "event=ingestion.deribit_options.quarantine_unwritten " in line
    ]
    assert proc.returncode == 1, proc.stderr[-2000:]
    assert len(lines) == 3, proc.stderr[-2000:]
    key = "ops/quarantine/deribit_options_btc/2026-10-30/"
    assert all(f"capture={key}" in line for line in lines)
    store = DeltaLakeStore(lake)
    assert store.bronze_ingest_dates("deribit_options_eth") == [NOW.date()]
    assert store.bronze_ingest_dates("deribit_options_usdc") == [NOW.date()]


def test_the_fixture_rows_are_shaped_like_deribits() -> None:
    """Guard: the shared builder keeps the dropped raw columns, so the
    adapter is tested against what Deribit actually sends."""
    assert {"last", "high", "low", "mid_price"} <= set(raw_book_row("BTC-27NOV26-1000-P"))


# ---- review round 1 -------------------------------------------------------------------


def test_an_int64_edge_expiry_under_local_costs_one_row_and_the_others_land(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store = _local_lake(monkeypatch, tmp_path)
    book, instruments = raw_book("BTC", 200)
    instruments[4]["expiration_timestamp"] = None
    instruments[9]["expiration_timestamp"] = 2**63 - 1
    _fetch_from(monkeypatch, {**_clean_books(), "BTC": (book, instruments)})
    assert deribit_snapshot.main(["--local"]) == 0
    assert _committed(store) == {"BTC": 199, "ETH": 120, "USDC": 120}


def test_a_landed_retry_is_red_because_its_quarantine_is_unknown(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The landed attempt's answer was lost: whether it quarantined over 1% or
    failed its blob write is unknown, and the catch-up will only ever answer
    "already captured". So red, pointing at where to look."""
    landed = _ok("BTC", committed=False, already_captured=True, rows=0)
    code, app = _run_post(monkeypatch, [_500(), landed])
    assert code == 1
    assert [c for c, _ in app.posts].count("btc") == 2
    err = capsys.readouterr().err
    assert "::error::btc: quarantine share and blob status of the landed attempt are unknown" in err
    assert "check ops/quarantine/deribit_options_btc/2026-10-30/" in err
    assert "event=api.ingest.deribit_options.quarantine_unwritten" in err


def test_a_plain_catch_up_does_not_warn(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _ = _run_post(monkeypatch, [_ok("BTC", committed=False, already_captured=True, rows=0)])
    assert code == 0
    assert "landed attempt are unknown" not in capsys.readouterr().err


def test_retries_exhausted_says_it_may_have_landed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], sleeps: list[float]
) -> None:
    _run_post(monkeypatch, [_500(), _500(), _500(), _500()])
    assert sleeps == [15, 60, 180]
    err = capsys.readouterr().err
    assert (
        "btc: retries exhausted (may have landed; check /api/ingest/deribit-options/status)" in err
    )
    assert "app rejected the book" not in err


def test_the_root_cause_survives_a_long_outer_message() -> None:
    try:
        try:
            raise ConnectionRefusedError("the real reason")
        except ConnectionRefusedError as inner:
            raise requests.ConnectionError("x" * 500) from inner
    except requests.ConnectionError as outer:
        text = deribit_snapshot.describe(outer)
    assert text.startswith("ConnectionError: xxx")
    assert text.endswith("<- ConnectionRefusedError: the real reason")
    assert text[150:155] == " ... "
    assert len(text) == 300


def _refusal_500(detail: str) -> requests.Response:
    return _response(500, {"detail": detail})


def test_a_deterministic_app_refusal_is_not_retried(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from tail_lab.contracts.deribit_options import REFUSAL_MARKER

    code, app = _run_post(
        monkeypatch, [_refusal_500(f"{REFUSAL_MARKER}quarantine not serialisable: x")]
    )
    assert code == 1
    assert [c for c, _ in app.posts].count("btc") == 1
    assert "deterministic, nothing written" in capsys.readouterr().err


def test_a_deterministic_refusal_does_not_set_may_have_landed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tail_lab.contracts.deribit_options import REFUSAL_MARKER

    refusal = requests.HTTPError(response=_refusal_500(f"{REFUSAL_MARKER}x"))
    assert not deribit_snapshot._retryable(refusal)
    calls: list[int] = []

    def once(*_a: object) -> object:
        calls.append(1)
        raise refusal

    monkeypatch.setattr(deribit_snapshot, "_post_once", once)
    with pytest.raises(requests.HTTPError):
        deribit_snapshot._post("u", "t", {}, 1, "btc")
    assert calls == [1]


def test_an_unmarked_500_is_still_retried() -> None:
    assert deribit_snapshot._retryable(requests.HTTPError(response=_refusal_500("boom")))


def test_a_refused_connection_never_reached_the_app() -> None:
    refused = urllib3.exceptions.NewConnectionError(None, "Connection refused")  # type: ignore[arg-type]
    exc = requests.ConnectionError(
        urllib3.exceptions.MaxRetryError(None, "/x", reason=refused)  # type: ignore[arg-type]
    )
    assert not deribit_snapshot._failed_after_send(exc)
    assert deribit_snapshot._failed_after_send(requests.ConnectionError("reset by peer"))


@pytest.mark.parametrize(
    "first",
    [_500(), _response(429, {}), requests.ConnectionError("reset")],
    ids=["5xx", "429", "transport"],
)
def test_a_retryable_failure_is_retried_and_the_book_lands(
    monkeypatch: pytest.MonkeyPatch, first: Step
) -> None:
    code, app = _run_post(monkeypatch, [first, _ok("BTC")])
    assert code == 0
    assert [c for c, _ in app.posts].count("btc") == 2


def test_a_quarantine_share_of_exactly_one_percent_is_green(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ok = _ok("BTC", quarantined=1, quarantine_share=0.01, quarantine_written=True)
    assert _run_post(monkeypatch, [ok])[0] == 0


# ---- review round 2 -------------------------------------------------------------------


def test_may_have_landed_is_sticky_across_a_later_unsent_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 500 may have landed; a connect timeout after it reached nothing, and
    must not wash out what the 500 might have done."""
    landed = _ok("BTC", committed=False, already_captured=True, previous_partition_exists=False)
    code, app = _run_post(monkeypatch, [_500(), requests.ConnectTimeout("x"), landed])
    assert code == 1
    assert [c for c, _ in app.posts].count("btc") == 3


def test_the_post_line_shows_dropped_instruments(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Under --post the route never sees the dropped rows: this line is the
    only record of them."""
    book, instruments = raw_book("BTC", 120)
    _run_post(
        monkeypatch,
        [_ok("BTC")],
        payloads={**_clean_books(), "BTC": (book, [*instruments, None, {"x": 1}])},
    )
    assert "btc: fetched 120 rows, 120 listed, instruments_dropped=2" in capsys.readouterr().out


def test_a_quarantine_share_just_over_one_percent_is_red(monkeypatch: pytest.MonkeyPatch) -> None:
    ok = _ok("BTC", quarantined=2, quarantine_share=0.0101, quarantine_written=True)
    assert _run_post(monkeypatch, [ok])[0] == 1


def test_local_too_early_says_when_to_re_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _local_lake(monkeypatch, tmp_path)
    early = dt.datetime(2026, 10, 30, 7, 0, tzinfo=dt.UTC)
    created = int(early.timestamp() * 1000) - 60_000
    _fetch_from(monkeypatch, {c: raw_book(c, 50, created_ms=created) for c in BOOKS})
    monkeypatch.setattr(deribit_snapshot, "_utcnow", lambda: early)
    assert deribit_snapshot.main(["--local"]) == 1
    assert "btc: refused (425, re-run after 08:05 UTC)" in capsys.readouterr().err


def test_describe_keeps_deribits_4xx_body() -> None:
    """``_raise_for_status`` appends Deribit's JSON-RPC reason to the outer
    message and re-raises ``from`` the original; printing only the innermost
    cause lost it."""
    resp = requests.Response()
    resp.status_code = 400
    resp._content = b'{"error": {"message": "Invalid params", "code": -32602}}'
    resp.url = "https://www.deribit.com/api/v2/public/get_instruments"
    try:
        ingestion._raise_for_status(resp)
    except requests.HTTPError as exc:
        text = deribit_snapshot.describe(exc)
    assert "Invalid params" in text


def test_describe_follows_an_implicit_context() -> None:
    """A real ``requests.ConnectionError`` reaches urllib3's error through
    ``__context__`` only (raised inside ``except``, no ``from``)."""
    try:
        try:
            raise ConnectionRefusedError("the real reason")
        except ConnectionRefusedError:
            raise requests.ConnectionError("outer")  # noqa: B904 -- implicit on purpose
    except requests.ConnectionError as outer:
        text = deribit_snapshot.describe(outer)
    assert "the real reason" in text


def test_describe_survives_a_cycle_and_keeps_a_plain_message() -> None:
    a, b = ValueError("a"), ValueError("b")
    a.__context__, b.__context__ = b, a
    assert "boom" in deribit_snapshot.describe(ValueError("boom"))
    assert deribit_snapshot.describe(a).startswith("ValueError: a <- ValueError: b")


def test_a_landed_retry_still_names_a_missing_previous_day(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Red for the unknown quarantine either way -- but a lost D - 1 must
    still be named, or the operator chases the wrong failure."""
    landed = _ok("BTC", committed=False, already_captured=True, previous_partition_exists=False)
    _run_post(monkeypatch, [_500(), landed])
    assert "btc: the day before 2026-10-30 was never captured" in capsys.readouterr().err


def test_the_marker_counts_only_at_the_start_of_the_detail() -> None:
    """A proxy or upstream error that merely quotes a refusal is not the
    app's own deterministic answer, and stays retryable."""
    quoted = _response(500, {"detail": f"upstream said: {REFUSAL_MARKER}x"})
    assert deribit_snapshot._retryable(requests.HTTPError(response=quoted))


# ---- review round 3 -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("captured", "expected"),
    [
        (CREATED_MS - 3 * 3_600_000, 0),
        (CREATED_MS - 1, 0),
        (CREATED_MS, 1),
        (CREATED_MS + 19, 1),
        (CREATED_MS + 61, 1),
        (None, 1),
    ],
    ids=[
        "captured-earlier",
        "captured-just-before-this-body",
        "own-attempt-landed-at-its-minimum",
        "own-attempt-landed-inside-its-spread",
        "own-attempt-landed-at-its-maximum",
        "older-app-says-nothing",
    ],
)
def test_a_transient_failure_on_a_day_captured_earlier_is_green(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    captured: int | None,
    expected: int,
) -> None:
    """The routine 10:45 catch-up after a green 08:15: a 502 on its first
    attempt landed nothing, because the stored partition began hours earlier
    than this body. Only a stored capture that IS this body (or an app that
    does not say) leaves the landed attempt's quarantine unknown."""
    again = _ok("BTC", committed=False, already_captured=True, rows=0)
    body = json.loads(again.content)
    again._content = json.dumps({**body, "captured_observed_at_ms": captured}).encode()
    code, app = _run_post(monkeypatch, [_response(502, "bad gateway"), again])
    assert code == expected
    assert [c for c, _ in app.posts].count("btc") == 2
    earlier = "btc: an attempt failed after sending, but the day was captured by an earlier run"
    assert (earlier in capsys.readouterr().out) is (expected == 0)


# ---- round 3, test strength -------------------------------------------------------------


def test_retry_budget_stays_inside_the_stale_bound() -> None:
    """The book GET comes before ``observed_at``; the instruments GET (with
    its retries) and every POST attempt (timeouts plus backoffs) come after
    it. All of that must fit inside the app's stale bound, or a session that
    needed every retry is refused as stale on its last attempt."""
    from tail_lab.contracts.deribit_options import MAX_AGE

    fetch = (
        ingestion._FETCH_ATTEMPTS * ingestion._TIMEOUT_S
        + (ingestion._FETCH_ATTEMPTS - 1) * ingestion._FETCH_BACKOFF_S
    )
    post = sum(deribit_snapshot.POST_BACKOFF_S) + (
        deribit_snapshot.POST_ATTEMPTS * deribit_snapshot.POST_TIMEOUT_S
    )
    assert len(deribit_snapshot.POST_BACKOFF_S) == deribit_snapshot.POST_ATTEMPTS - 1
    assert fetch + post < MAX_AGE.total_seconds()


def test_exhausted_429s_say_rate_limited_not_landed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _run_post(monkeypatch, [_response(429, {})] * 4)
    err = capsys.readouterr().err
    assert "btc: rate-limited: retries exhausted (a 429 lands nothing;" in err
    assert "may have landed;" not in err
    assert "app rejected the book" not in err


def test_describe_returns_on_a_longer_cycle() -> None:
    """a -> b -> c -> b: a guard that only remembers the start would loop."""
    a, b, c = ValueError("a"), ValueError("b"), ValueError("c")
    a.__context__, b.__context__, c.__context__ = b, c, b
    out: list[str] = []
    worker = threading.Thread(target=lambda: out.append(deribit_snapshot.describe(a)), daemon=True)
    worker.start()
    worker.join(2)
    assert not worker.is_alive()
    assert out == ["ValueError: a <- ValueError: c"]


def test_describe_leaves_a_short_text_exactly_as_it_is() -> None:
    assert deribit_snapshot.describe(ValueError("boom")) == "ValueError: boom"
    try:
        try:
            raise ConnectionRefusedError("inner")
        except ConnectionRefusedError as inner:
            raise requests.ConnectionError("outer") from inner
    except requests.ConnectionError as outer:
        text = deribit_snapshot.describe(outer)
    assert text == "ConnectionError: outer <- ConnectionRefusedError: inner"


def test_describe_keeps_a_text_of_exactly_the_limit_whole() -> None:
    message = "x" * (300 - len("ValueError: "))
    assert deribit_snapshot.describe(ValueError(message)) == f"ValueError: {message}"


def test_describe_prefers_the_explicit_cause_over_the_context() -> None:
    """``raise X from Y`` inside ``except Z``: Y is the reason given, Z only
    what was being handled."""
    try:
        try:
            raise KeyError("being handled")
        except KeyError:
            raise requests.ConnectionError("outer") from ConnectionRefusedError("the cause")
    except requests.ConnectionError as outer:
        text = deribit_snapshot.describe(outer)
    assert text == "ConnectionError: outer <- ConnectionRefusedError: the cause"


def test_exhausted_429s_back_off_on_the_same_schedule(
    monkeypatch: pytest.MonkeyPatch, sleeps: list[float]
) -> None:
    _run_post(monkeypatch, [_response(429, {})] * 4)
    assert sleeps == [15, 60, 180]


def test_a_local_catch_up_on_a_captured_day_is_green(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """--local has no retries, so nothing it sends "may have landed": a second
    run on a captured day is a plain no-op, not the landed-attempt alarm."""
    _local_lake(monkeypatch, tmp_path)
    _fetch_from(monkeypatch, _clean_books())
    assert deribit_snapshot.main(["--local"]) == 0
    later = CREATED_MS + 120_000
    _fetch_from(monkeypatch, {c: raw_book(c, 120, created_ms=later) for c in BOOKS})
    monkeypatch.setattr(deribit_snapshot, "_utcnow", lambda: NOW + dt.timedelta(minutes=2))
    assert deribit_snapshot.main(["--local"]) == 0
