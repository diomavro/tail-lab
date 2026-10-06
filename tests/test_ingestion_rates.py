from __future__ import annotations

import datetime as dt
import json
from collections.abc import Mapping
from pathlib import Path

import pandas as pd
import pytest
import requests

from tail_lab.ingestion import rates, sources
from tail_lab.ingestion.json_payload import json_list, json_object
from tail_lab.ingestion.rates import (
    MAX_VINTAGES_PER_REQUEST,
    MAX_WINDOW_DAYS,
    REVISION_LOOKBACK_DAYS,
    discover_revision_vintages,
    enumerate_vintage_dates,
    fetch_first_vintage,
    fetch_series_history,
    fred_getter,
    ingest_rates,
    parse_fred_observations,
    parse_fred_vintage_observations,
    parse_vintage_dates_page,
    plan_vintage_batches,
    unreconciled_obs_dates,
    validate_and_quarantine,
)
from tail_lab.lake.store import DeltaLakeStore

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str) -> dict[str, object]:
    """Real FRED responses for DGS3MO, captured 2026-10-05, around the
    2008-05-29 observation FRED revised (1.91 in the 2008-06-02 vintage,
    1.90 in 2008-06-06) and the 2008-05-26 Memorial Day "." print."""
    return json_object(json.loads((FIXTURES / name).read_text()), name)


def _real_cells() -> dict[tuple[str, str], str]:
    """``(obs_date, vintage YYYYMMDD) -> raw cell`` from the real wide payload."""
    cells: dict[tuple[str, str], str] = {}
    payload = _fixture("fred_vintage_observations_sample.json")
    for item in json_list(payload["observations"], "observations"):
        row = json_object(item, "observation")
        for column, value in row.items():
            if column != "date":
                cells[(str(row["date"]), column.removeprefix("DGS3MO_"))] = str(value)
    return cells


class FakeFred:
    """A FRED stand-in that answers from a table of published cells exactly
    the way the live API was measured to (`docs/PRIOR_ART.md` §19): paged
    ``series/vintagedates``, ``output_type=3`` wide frames holding only the
    requested vintages' cells inside the observation window, and
    ``output_type=1`` realtime windows whose periods are CLIPPED to the
    window's start, as FRED's are. It refuses the shapes the adapter must
    never send -- a multi-vintage request with no ``observation_start`` (35 s
    uncached live, against a 60 s gateway timeout), >600 vintages (an Apache
    400), more than
    ``ALFRED_MAX_VINTAGES`` in a realtime window is FRED's 400, and
    ``output_type=3`` on the series' first vintage was a 504 (~60 s uncached,
    DFF) -- so a regression to any of those shapes fails here instead of in
    production."""

    def __init__(self, series_id: str, cells: Mapping[tuple[str, str], str]) -> None:
        self.series_id = series_id
        self.cells = dict(cells)
        self.vintages = sorted({v for _, v in cells})
        self.requests: list[tuple[str, dict[str, str]]] = []

    def __call__(self, endpoint: str, params: Mapping[str, str]) -> object:
        self.requests.append((endpoint, dict(params)))
        if endpoint == "series/vintagedates":
            return self._vintagedates(params)
        if "vintage_dates" in params:
            return self._wide(params)
        return self._realtime(params)

    def _iso(self, compact: str) -> str:
        return f"{compact[:4]}-{compact[4:6]}-{compact[6:]}"

    def _vintagedates(self, params: Mapping[str, str]) -> object:
        offset, limit = int(params.get("offset", "0")), int(params["limit"])
        page = [self._iso(v) for v in self.vintages[offset : offset + limit]]
        return {
            "count": len(self.vintages),
            "offset": offset,
            "limit": limit,
            "vintage_dates": page,
        }

    def _wide(self, params: Mapping[str, str]) -> object:
        wanted = {d.replace("-", "") for d in params["vintage_dates"].split(",")}
        if len(wanted) > 600:
            raise AssertionError("Apache HTML 400: more than 600 vintages in one request")
        if len(wanted) > 1 and "observation_start" not in params:
            raise AssertionError("unbounded window over many vintages: near FRED's 504")
        start = params.get("observation_start", "1600-01-01")
        end = params["observation_end"]
        if self.vintages[0] in wanted:
            raise AssertionError("FRED 504: output_type=3 on the series' first vintage")
        rows: dict[str, dict[str, str]] = {}
        for (obs, vintage), value in sorted(self.cells.items()):
            if vintage in wanted and start <= obs <= end:
                rows.setdefault(obs, {"date": obs})[f"{self.series_id}_{vintage}"] = value
        return {"count": len(rows), "limit": 100000, "observations": list(rows.values())}

    def _realtime(self, params: Mapping[str, str]) -> object:
        first = params["realtime_start"].replace("-", "")
        last = params["realtime_end"].replace("-", "")
        if sum(first <= v <= last for v in self.vintages) > rates.ALFRED_MAX_VINTAGES:
            raise AssertionError("FRED 400: too many vintage dates in the real-time period")
        start = params.get("observation_start", "1600-01-01")
        end = params.get("observation_end", "9999-12-31")
        published: dict[str, list[tuple[str, str]]] = {}
        for (obs, vintage), value in sorted(self.cells.items(), key=lambda kv: kv[0][1]):
            if start <= obs <= end and vintage <= last:
                published.setdefault(obs, []).append((vintage, value))
        rows = []
        for obs, cells in sorted(published.items()):
            before = [c for c in cells if c[0] <= first]
            # A period already running at the window's start comes back
            # stamped with that start -- FRED's clipping, faithfully.
            periods = ([(first, before[-1][1])] if before else []) + [
                c for c in cells if c[0] > first
            ]
            rows += [
                {
                    "realtime_start": self._iso(v),
                    "realtime_end": self._iso(last),
                    "date": obs,
                    "value": x,
                }
                for v, x in periods
            ]
        return {"observations": rows}

    def wide_requests(self) -> list[dict[str, str]]:
        return [p for e, p in self.requests if e == "series/observations" and "vintage_dates" in p]


def _as_known_on(df: pd.DataFrame, day: str) -> pd.Series:
    """The point-in-time read the ``vintage_date`` column exists for: per
    obs_date, the newest value whose vintage is on or before ``day``."""
    known = df.loc[df["vintage_date"] <= pd.Timestamp(day)]
    return known.sort_values("vintage_date").groupby("obs_date")["value"].last()


# ---------------------------------------------------------------- vintage enumeration


def test_parse_vintage_dates_page_reads_the_real_envelope() -> None:
    dates, count = parse_vintage_dates_page(_fixture("fred_vintagedates_page1_sample.json"))

    assert count == 9
    assert dates[0] == dt.date(2008, 5, 27)
    assert len(dates) == 5


def test_a_vintage_list_longer_than_one_page_is_fully_enumerated() -> None:
    """FRED returns at most ``limit`` dates per page; stopping after the
    first page would silently drop every later vintage (and so every later
    revision). Serves the two real captured pages by offset."""
    pages = {
        "0": _fixture("fred_vintagedates_page1_sample.json"),
        "5": _fixture("fred_vintagedates_page2_sample.json"),
    }
    offsets: list[str] = []

    def get(endpoint: str, params: Mapping[str, str]) -> object:
        assert endpoint == "series/vintagedates"
        offsets.append(params["offset"])
        return pages[params["offset"]]

    dates = enumerate_vintage_dates("DGS3MO", get, page_limit=5)

    assert offsets == ["0", "5"]
    assert len(dates) == 9
    assert dates[-1] == dt.date(2008, 6, 6)


def test_enumeration_that_stalls_short_of_count_raises() -> None:
    def get(endpoint: str, params: Mapping[str, str]) -> object:
        if params["offset"] == "0":
            return _fixture("fred_vintagedates_page1_sample.json")
        return {"count": 9, "vintage_dates": []}

    with pytest.raises(ValueError, match="stalled"):
        enumerate_vintage_dates("DGS3MO", get, page_limit=5)


# ---------------------------------------------------------------- batching


def test_batching_splits_at_the_limit_and_covers_every_vintage_once() -> None:
    start = dt.date(2005, 6, 28)
    vintages = [start + dt.timedelta(days=i) for i in range(1 + 2 * MAX_VINTAGES_PER_REQUEST + 1)]

    batches = plan_vintage_batches(vintages)

    assert [len(b.vintage_dates) for b in batches] == [
        MAX_VINTAGES_PER_REQUEST,
        MAX_VINTAGES_PER_REQUEST,
        1,
    ]
    # Every vintage after the first, once, in order; the first is fetched
    # whole elsewhere (output_type=3 on it is FRED's 504 path).
    assert [d for b in batches for d in b.vintage_dates] == vintages[1:]
    # Every window bounded: unbounded ran 35 s uncached, near the 60 s 504.
    for b in batches:
        lookback = dt.timedelta(days=REVISION_LOOKBACK_DAYS)
        assert b.observation_start == b.vintage_dates[0] - lookback
        assert b.observation_end == b.vintage_dates[-1]


def test_batching_exactly_at_the_limit_is_one_request() -> None:
    start = dt.date(2005, 6, 28)
    vintages = [start + dt.timedelta(days=i) for i in range(1 + MAX_VINTAGES_PER_REQUEST)]

    assert [len(b.vintage_dates) for b in plan_vintage_batches(vintages)] == [
        MAX_VINTAGES_PER_REQUEST
    ]


def test_the_first_vintage_is_taken_whole_from_one_realtime_request() -> None:
    """FakeFred 504s any output_type=3 request naming the first vintage, as
    live FRED did for DFF; the first vintage must arrive anyway, stamped
    with its own date."""
    fred = FakeFred("DGS3MO", _real_cells())

    df = fetch_series_history("DGS3MO", fred, max_per_request=3)

    first = [
        p
        for e, p in fred.requests
        if p.get("realtime_start") == p.get("realtime_end") == "2008-05-27"
    ]
    assert len(first) == 1
    row = df.loc[df["obs_date"] == pd.Timestamp("2008-05-22")]
    assert row["vintage_date"].tolist() == [pd.Timestamp("2008-05-27")]


def test_a_first_vintage_stamped_with_another_date_raises() -> None:
    """Exact only because nothing precedes the first vintage; a row stamped
    otherwise means that premise broke, and storing it would fabricate."""

    def get(endpoint: str, params: Mapping[str, str]) -> object:
        return _fred_payload(("2006-01-03", "4.00", "2005-01-01"))

    with pytest.raises(ValueError, match="first vintage"):
        fetch_first_vintage("DGS3MO", get, dt.date(2008, 5, 27))


def test_discovery_never_names_the_first_vintage() -> None:
    """A date the first vintage printed as "." holds no row, so its
    (date, first vintage) pair looks unheld -- and asking output_type=3
    about the first vintage is the 504."""
    cells = _real_cells()
    cells[("2006-01-03", "20080527")] = "."
    cells[("2006-01-03", "20080606")] = "1.00"

    df = fetch_series_history("DGS3MO", FakeFred("DGS3MO", cells), max_per_request=3)

    assert df.loc[df["obs_date"] == pd.Timestamp("2006-01-03"), "value"].tolist() == [1.00]


def test_the_limit_stays_under_the_apache_ceiling() -> None:
    assert MAX_VINTAGES_PER_REQUEST <= 600


# ---------------------------------------------------------------- wide -> long melt


def test_melt_keeps_every_published_vintage_of_the_real_payload() -> None:
    raw = _fixture("fred_vintage_observations_sample.json")
    df = parse_fred_vintage_observations("DGS3MO", raw)

    published = {k: v for k, v in _real_cells().items() if v != "."}
    assert len(df) == len(published) == 10
    got = {
        (o.strftime("%Y-%m-%d"), v.strftime("%Y%m%d")): x
        for o, v, x in zip(df["obs_date"], df["vintage_date"], df["value"], strict=True)
    }
    assert got == {k: float(v) for k, v in published.items()}
    # The revised observation keeps BOTH vintages, never latest-only.
    revised = df.loc[df["obs_date"] == pd.Timestamp("2008-05-29")]
    assert revised["value"].tolist() == [1.91, 1.90]
    assert revised["vintage_date"].tolist() == [
        pd.Timestamp("2008-06-02"),
        pd.Timestamp("2008-06-06"),
    ]


def test_missing_dot_is_null_and_never_a_zero() -> None:
    """2008-05-26 (Memorial Day) is FRED's literal ".": an absence, not a
    0.0% yield. It must not become a row, and nothing may read as zero."""
    df = parse_fred_vintage_observations(
        "DGS3MO", _fixture("fred_vintage_observations_sample.json")
    )

    assert pd.Timestamp("2008-05-26") not in set(df["obs_date"])
    assert (df["value"] != 0.0).all()
    assert df["value"].notna().all()


def test_melt_keeps_malformed_cells_for_quarantine() -> None:
    raw = {
        "observations": [
            {"date": "2008-05-27", "DGS3MO_20080529": "not-a-number"},
            {"date": "garbled", "DGS3MO_20080530": "1.89"},
            {"date": "2008-05-28", "DGS3MO_2008XX30": "1.89"},
        ]
    }
    df = parse_fred_vintage_observations("DGS3MO", raw)

    assert len(df) == 3
    _, quarantined = validate_and_quarantine(df)
    assert len(quarantined) == 3


def test_melt_refuses_another_series_columns() -> None:
    raw = {"observations": [{"date": "2008-05-27", "DGS10_20080529": "3.9"}]}
    with pytest.raises(ValueError, match="foreign"):
        parse_fred_vintage_observations("DGS3MO", raw)


def test_melt_refuses_a_full_page_as_possibly_truncated() -> None:
    raw = {"limit": 1, "observations": [{"date": "2008-05-27", "DGS3MO_20080529": "1.89"}]}
    with pytest.raises(ValueError, match="truncated"):
        parse_fred_vintage_observations("DGS3MO", raw)


def test_count_above_rows_is_not_truncation() -> None:
    """Measured live: output_type=3 reports ``count`` as every obs date in
    the window, then returns zero rows when no requested vintage touched
    them. Treating that as truncation broke the first live run."""
    raw = {"count": 88, "limit": 100000, "observations": []}
    assert parse_fred_vintage_observations("DGS3MO", raw).empty


# ---------------------------------------------------------------- orchestration


def test_history_has_no_fabricated_obs_vintage_pairs() -> None:
    """Every ``(obs_date, vintage_date)`` row must be a cell FRED actually
    published -- the failure realtime-window chunking produces is a row
    stamped with a chunk boundary FRED never published under."""
    fred = FakeFred("DGS3MO", _real_cells())
    df = fetch_series_history("DGS3MO", fred, max_per_request=3)

    pairs = {
        (o.strftime("%Y-%m-%d"), v.strftime("%Y%m%d"))
        for o, v in zip(df["obs_date"], df["vintage_date"], strict=True)
    }
    published = {k for k, v in _real_cells().items() if v != "."}
    assert pairs == published
    assert all(len(p["vintage_dates"].split(",")) <= 3 for p in fred.wide_requests())
    assert not df.duplicated(subset=["obs_date", "vintage_date"]).any()


def test_a_backfill_outside_the_lookback_is_repaired_by_reconciliation() -> None:
    """Measured live: FRED added DGS3MO's 1981-09..12 observations in the
    2020-07-21 vintage, decades before that batch's window. The main pass
    cannot see it; reconciliation against the snapshot must."""
    cells = _real_cells()
    cells[("2006-01-03", "20080604")] = "4.20"
    fred = FakeFred("DGS3MO", cells)

    df = fetch_series_history("DGS3MO", fred, max_per_request=3)

    row = df.loc[df["obs_date"] == pd.Timestamp("2006-01-03")]
    assert row["value"].tolist() == [4.20]
    assert row["vintage_date"].tolist() == [pd.Timestamp("2008-06-04")]
    repair = [p for p in fred.wide_requests() if p.get("observation_start") == "2006-01-03"]
    assert repair, "the backfilled date was never re-requested"


def test_repair_recovers_intermediate_revisions_from_every_vintage_chunk() -> None:
    """The repair must ask EVERY revising vintage, not just the one that set
    the final value: 4.50 (2008-05-29) sits in an earlier chunk than 4.60
    (2008-06-06), and dropping it would make a backtest on 2008-06-01 see
    4.00 -- a value FRED had already revised away."""
    cells = _real_cells()
    cells[("2006-01-03", "20080527")] = "4.00"
    cells[("2006-01-03", "20080529")] = "4.50"
    cells[("2006-01-03", "20080606")] = "4.60"

    # One vintage per request, so the two revisers land in different chunks.
    df = fetch_series_history("DGS3MO", FakeFred("DGS3MO", cells), max_per_request=1)

    row = df.loc[df["obs_date"] == pd.Timestamp("2006-01-03")]
    assert row["value"].tolist() == [4.00, 4.50, 4.60]


def _discovery_requests(fred: FakeFred) -> list[dict[str, str]]:
    return [p for e, p in fred.requests if "realtime_start" in p and "observation_start" in p]


def _repair_vintages(fred: FakeFred, gap: str) -> list[str]:
    return [p["vintage_dates"] for p in fred.wide_requests() if p.get("observation_start") == gap]


def test_repair_asks_only_the_vintages_that_revised(monkeypatch: pytest.MonkeyPatch) -> None:
    """Measured live: re-asking all ~5,100 vintages per gap cluster took DFF
    past 30 minutes, and keeping every vintage that first-printed ANY date in
    the gap span still asked DGS30 for 941. Discovery must read every
    realtime window (each at most ALFRED_MAX_VINTAGES) and keep only the
    vintages whose cells the history does not already hold."""
    monkeypatch.setattr(rates, "ALFRED_MAX_VINTAGES", 4)
    cells = _real_cells()
    cells[("2006-01-03", "20080527")] = "4.00"  # first vintage: already held
    cells[("2006-01-03", "20080602")] = "4.50"  # first vintage of window 2
    cells[("2006-01-03", "20080606")] = "4.60"  # window 3
    # A second gap past every batch's window top, so the discovery span
    # covers all of the sample's held (date, vintage) cells -- each of which
    # names a vintage that must NOT be asked again.
    cells[("2008-06-10", "20080606")] = "1.70"
    fred = FakeFred("DGS3MO", cells)

    df = fetch_series_history("DGS3MO", fred, max_per_request=400)

    row = df.loc[df["obs_date"] == pd.Timestamp("2006-01-03")]
    assert row["value"].tolist() == [4.00, 4.50, 4.60]
    assert len(_discovery_requests(fred)) == 3  # 9 vintages in windows of 4, 4, 1
    # 05-29 is the one held-looking miss: its Memorial Day "." holds no row,
    # so it is asked again (harmlessly). 05-28/05-30/06-03/06-04/06-05 are not.
    assert _repair_vintages(fred, "2006-01-03") == ["2008-05-29,2008-06-02,2008-06-06"]
    assert df.loc[df["obs_date"] == pd.Timestamp("2008-06-10"), "value"].tolist() == [1.70]


def test_a_clipped_window_start_is_asked_about_but_never_stored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FRED stamps a period already running at a realtime window's start
    with that start. Discovery may therefore name a vintage that revised
    nothing (2008-06-02 here); the repair asks ``output_type=3`` about it,
    which answers truthfully -- so no row may appear at that vintage."""
    monkeypatch.setattr(rates, "ALFRED_MAX_VINTAGES", 4)
    cells = _real_cells()
    cells[("2006-01-03", "20080527")] = "4.00"
    cells[("2006-01-03", "20080606")] = "4.60"
    fred = FakeFred("DGS3MO", cells)

    df = fetch_series_history("DGS3MO", fred, max_per_request=400)

    assert _repair_vintages(fred, "2006-01-03") == ["2008-06-02,2008-06-06"]
    row = df.loc[df["obs_date"] == pd.Timestamp("2006-01-03")]
    assert row["vintage_date"].tolist() == [pd.Timestamp("2008-05-27"), pd.Timestamp("2008-06-06")]


def test_discovery_spans_every_gap_not_just_one() -> None:
    """Two gap dates revised by different vintages: a discovery window over
    only one of them loses the other's intermediate 3.50 while the series
    still reconciles -- a backtest on 2008-06-02 would read 3.00."""
    cells = _real_cells()
    cells[("2006-01-03", "20080527")] = "4.00"
    cells[("2006-01-03", "20080606")] = "4.60"
    cells[("2006-06-01", "20080527")] = "3.00"
    cells[("2006-06-01", "20080529")] = "3.50"
    cells[("2006-06-01", "20080606")] = "4.00"
    # Between the gaps, revised and reverted: reconciliation cannot see it,
    # but discovery over the span does, and the repair window covers it.
    # Its vintages (06-02, 06-05) revise no gap date, so only discovery over
    # the whole span -- not just the gap dates -- finds them.
    cells[("2006-03-01", "20080527")] = "2.00"
    cells[("2006-03-01", "20080602")] = "2.50"
    cells[("2006-03-01", "20080605")] = "2.00"

    df = fetch_series_history("DGS3MO", FakeFred("DGS3MO", cells), max_per_request=3)

    for day, values in (
        ("2006-01-03", [4.00, 4.60]),
        ("2006-03-01", [2.00, 2.50, 2.00]),
        ("2006-06-01", [3.00, 3.50, 4.00]),
    ):
        assert df.loc[df["obs_date"] == pd.Timestamp(day), "value"].tolist() == values


def test_repair_windows_stay_inside_the_request_budget() -> None:
    """Repair requests stay near the shape the live repair ran in ~5 s (400
    vintages x 400 days). A window's vintages x days must stay inside
    REPAIR_BUDGET_VINTAGE_DAYS, and a lone vintage may span no more than the
    five-year slice -- while every gap still lands in some window."""
    gaps = [pd.Timestamp("2000-01-03") + pd.Timedelta(days=d) for d in range(0, 3000, 7)]
    many = [dt.date(2008, 1, 1) + dt.timedelta(days=i) for i in range(400)]

    for revisers in (many, many[:1]):
        batches = rates._repair_batches(revisers, gaps, MAX_VINTAGES_PER_REQUEST)
        for b in batches:
            assert b.observation_start is not None
            days = (b.observation_end - b.observation_start).days
            assert len(b.vintage_dates) * days <= rates.REPAIR_BUDGET_VINTAGE_DAYS
            assert days <= MAX_WINDOW_DAYS
        covered = {
            g
            for g in gaps
            for b in batches
            if b.observation_start is not None
            and b.observation_start <= g.date() <= b.observation_end
        }
        assert covered == set(gaps)
    # A lone reviser gets wide windows rather than 400-day ones.
    assert len(rates._repair_batches(many[:1], gaps, MAX_VINTAGES_PER_REQUEST)) == 2


def test_discovery_refuses_a_period_starting_on_a_non_vintage_date() -> None:
    def get(endpoint: str, params: Mapping[str, str]) -> object:
        return {"observations": [{"realtime_start": "2008-05-31", "date": "2006-01-03"}]}

    with pytest.raises(ValueError, match="non-vintage"):
        discover_revision_vintages(
            "DGS3MO",
            get,
            [dt.date(2008, 5, 30)],
            parse_fred_vintage_observations("DGS3MO", {"observations": []}),
            [pd.Timestamp("2006-01-03")],
        )


def test_a_full_realtime_page_is_refused_as_possibly_truncated() -> None:
    raw = {"limit": 1, "observations": [{"realtime_start": "2008-05-30", "date": "2006-01-03"}]}
    with pytest.raises(ValueError, match="truncated"):
        rates.parse_realtime_periods(raw)


def test_history_that_cannot_be_reconciled_raises() -> None:
    fred = FakeFred("DGS3MO", _real_cells())
    real = fred._realtime

    def doctored(params: Mapping[str, str]) -> object:
        out = json_object(real(params), "realtime")
        if params["realtime_start"] == params["realtime_end"]:  # the snapshot
            last = json_list(out["observations"], "observations")[-1]
            json_object(last, "observation")["value"] = "9.99"
        return out

    fred._realtime = doctored  # type: ignore[method-assign]
    with pytest.raises(ValueError, match="still disagree"):
        fetch_series_history("DGS3MO", fred, max_per_request=3)


def test_reconcile_against_the_real_snapshot() -> None:
    """The real snapshot carries 2008-05-20/21, published in vintages before
    the sample's first one -- exactly the two dates reconciliation flags."""
    history = parse_fred_vintage_observations(
        "DGS3MO", _fixture("fred_vintage_observations_sample.json")
    )
    snapshot = parse_fred_observations("DGS3MO", _fixture("fred_snapshot_sample.json"))

    assert unreconciled_obs_dates(history, snapshot) == [
        pd.Timestamp("2008-05-20"),
        pd.Timestamp("2008-05-21"),
    ]


def test_a_row_with_an_unparseable_vintage_is_never_the_latest() -> None:
    """A NaT vintage sorts last; taken as "latest" it would reconcile a
    history whose real newest row is about to be quarantined."""
    history = parse_fred_vintage_observations(
        "DGS3MO",
        {
            "observations": [
                {"date": "2008-05-29", "DGS3MO_20080602": "1.91", "DGS3MO_2008XX06": "1.90"}
            ]
        },
    )
    snapshot = parse_fred_observations(
        "DGS3MO",
        {"observations": [{"date": "2008-05-29", "value": "1.90", "realtime_start": "2008-06-06"}]},
    )
    assert unreconciled_obs_dates(history, snapshot) == [pd.Timestamp("2008-05-29")]


def test_a_withdrawn_value_is_unreconciled_not_silently_kept() -> None:
    history = parse_fred_vintage_observations(
        "DGS3MO", {"observations": [{"date": "2008-05-27", "DGS3MO_20080529": "1.89"}]}
    )
    snapshot = parse_fred_observations(
        "DGS3MO",
        {"observations": [{"date": "2008-05-27", "value": ".", "realtime_start": "2008-06-06"}]},
    )
    assert unreconciled_obs_dates(history, snapshot) == [pd.Timestamp("2008-05-27")]


def test_point_in_time_read_returns_the_value_as_known_then(tmp_path: Path) -> None:
    """docs/adr/0009: a backtest on 2008-06-05 must see 1.91 for 2008-05-29,
    not the 1.90 FRED published the next day -- and on 2008-06-01, before
    its first print, must see nothing at all."""
    store = DeltaLakeStore(tmp_path)
    ingest_date = dt.date(2026, 10, 5)
    ingest_rates(
        store,
        ["DGS3MO"],
        ingest_date=ingest_date,
        get=FakeFred("DGS3MO", _real_cells()),
    )
    bronze = store.read_bronze_as_of("rates", ingest_date)
    obs = pd.Timestamp("2008-05-29")

    assert _as_known_on(bronze, "2008-06-05")[obs] == pytest.approx(1.91)
    assert _as_known_on(bronze, "2008-06-06")[obs] == pytest.approx(1.90)
    assert obs not in _as_known_on(bronze, "2008-06-01").index


# ---------------------------------------------------------------- single-vintage snapshot parser


def _fred_payload(*rows: tuple[str, str, str]) -> dict[str, object]:
    """A FRED output_type=1 payload from (date, value, realtime_start)."""
    return {
        "observations": [
            {"date": date, "value": value, "realtime_start": vintage, "realtime_end": "9999-12-31"}
            for date, value, vintage in rows
        ]
    }


def test_parse_snapshot_against_the_real_payload() -> None:
    df = parse_fred_observations("DGS3MO", _fixture("fred_snapshot_sample.json"))

    assert list(df.columns) == ["series_id", "obs_date", "value", "vintage_date"]
    assert len(df) == 11  # 12 dates, one of them Memorial Day's "."
    assert pd.Timestamp("2008-05-26") not in set(df["obs_date"])
    assert df.loc[df["obs_date"] == pd.Timestamp("2008-05-29"), "value"].item() == 1.90


def test_parse_snapshot_keeps_malformed_rows() -> None:
    raw = _fred_payload(
        ("2026-01-02", "4.33", "2026-01-02"),
        ("not-a-date", "4.40", "2026-01-06"),
        ("2026-01-07", "not-a-number", "2026-01-07"),
    )
    df = parse_fred_observations("DGS10", raw)

    assert len(df) == 3
    assert df["obs_date"].isna().sum() == 1
    assert df["value"].isna().sum() == 1


def test_parse_empty_source_returns_typed_empty_frame() -> None:
    df = parse_fred_observations("DGS10", {"observations": []})

    assert df.empty
    assert list(df.columns) == ["series_id", "obs_date", "value", "vintage_date"]


@pytest.mark.parametrize("junk", [None, [1], {"observations": "a string"}])
def test_a_fred_payload_of_the_wrong_shape_is_a_named_failure(junk: object) -> None:
    with pytest.raises(ValueError, match=r"FRED payload|observations"):
        parse_fred_observations("DGS10", junk)
    with pytest.raises(ValueError, match=r"FRED payload|observations"):
        parse_fred_vintage_observations("DGS10", junk)


# ---------------------------------------------------------------- validate / ingest


def test_validate_and_quarantine_splits_bad_rows() -> None:
    df = pd.DataFrame(
        {
            "series_id": ["DGS10", "DGS10", "DGS10"],
            "obs_date": pd.to_datetime(["2026-01-02", "2026-01-05", "2026-01-06"]),
            "value": [4.33, 2000.0, 4.31],
            "vintage_date": pd.to_datetime(["2026-01-02", "2026-01-05", "2026-01-06"]),
        }
    )
    valid, quarantined = validate_and_quarantine(df)

    assert len(valid) == 2
    assert len(quarantined) == 1
    assert quarantined["value"].iloc[0] == 2000.0


def _one_cell_fred(series_id: str, value: str) -> FakeFred:
    return FakeFred(series_id, {("2026-01-02", "20260102"): value})


def test_ingest_commits_whole_family_as_one_snapshot(tmp_path: Path) -> None:
    store = DeltaLakeStore(tmp_path)
    fakes = {"DGS10": _one_cell_fred("DGS10", "4.33"), "SOFR": _one_cell_fred("SOFR", "4.31")}

    def get(endpoint: str, params: Mapping[str, str]) -> object:
        return fakes[params["series_id"]](endpoint, params)

    ingest_date = dt.date(2026, 1, 6)
    result = ingest_rates(store, ["DGS10", "SOFR"], ingest_date=ingest_date, get=get)

    assert result.valid_rows == 2
    assert result.quarantined_rows == 0
    assert result.quarantine_path is None
    assert result.series_ids == ("DGS10", "SOFR")
    bronze = store.read_bronze_as_of("rates", ingest_date)
    assert set(bronze["series_id"]) == {"DGS10", "SOFR"}


def test_ingest_quarantines_bad_rows_without_losing_good_ones(tmp_path: Path) -> None:
    store = DeltaLakeStore(tmp_path)
    fred = FakeFred(
        "DGS10", {("2026-01-02", "20260102"): "4.33", ("2026-01-05", "20260105"): "2000.0"}
    )
    ingest_date = dt.date(2026, 1, 6)
    result = ingest_rates(store, ["DGS10"], ingest_date=ingest_date, get=fred)

    assert result.valid_rows == 1
    assert result.quarantined_rows == 1
    assert result.quarantine_path is not None
    assert 2000.0 not in store.read_bronze_as_of("rates", ingest_date)["value"].tolist()
    quarantined = store.read_bronze_as_of("rates__quarantine", ingest_date)
    assert quarantined["value"].tolist() == [2000.0]


def test_ingest_all_valid_writes_no_quarantine_partition(tmp_path: Path) -> None:
    store = DeltaLakeStore(tmp_path)
    ingest_date = dt.date(2026, 1, 6)
    result = ingest_rates(
        store, ["DGS10"], ingest_date=ingest_date, get=_one_cell_fred("DGS10", "4.33")
    )

    assert result.quarantine_path is None
    with pytest.raises(LookupError):
        store.read_bronze_as_of("rates__quarantine", ingest_date)


def test_ingest_uppercases_requested_series_ids(tmp_path: Path) -> None:
    store = DeltaLakeStore(tmp_path)
    result = ingest_rates(
        store, ["dgs10"], ingest_date=dt.date(2026, 1, 6), get=_one_cell_fred("DGS10", "4.33")
    )

    assert result.series_ids == ("DGS10",)
    assert result.valid_rows == 1


def test_ingest_without_api_key_or_getter_raises(tmp_path: Path) -> None:
    """FRED's endpoint is keyed, so a missing api_key must fail loudly
    rather than attempt an unauthenticated fetch."""
    with pytest.raises(ValueError, match="api_key"):
        ingest_rates(DeltaLakeStore(tmp_path), ["DGS10"], ingest_date=dt.date(2026, 1, 6))


def test_ingest_logs_the_full_run_surface(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """docs/STANDARDS.md §f: an automated action without a runtime record is
    below the bar, so the run lines are part of the contract."""
    store = DeltaLakeStore(tmp_path)
    with caplog.at_level("INFO", logger="tail_lab.ingestion.rates"):
        ingest_rates(
            store,
            ["DGS3MO"],
            ingest_date=dt.date(2026, 10, 5),
            get=FakeFred("DGS3MO", _real_cells()),
        )

    line = "\n".join(caplog.messages)
    assert "event=ingest.rates.series series_id=DGS3MO vintages=9" in line
    # 1 vintagedates page + 1 snapshot + 1 first-vintage slice + 1 batch of 8.
    assert "requests=4 " in line
    assert "revised_obs=1" in line
    assert "unreconciled=0" in line
    assert "event=ingest.rates " in line
    assert "valid_rows=10" in line
    assert "quarantined_rows=0" in line
    assert "first_obs_date=2008-05-22" in line
    assert "last_obs_date=2008-06-04" in line


# ---------------------------------------------------------------- live seam (no network)


class _Response:
    def __init__(self, content: bytes, status: int = 200, url: str = "") -> None:
        self.content = content
        self.status_code = status
        self.text = content.decode()
        self.url = url

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(
                f"{self.status_code} Error for url: {self.url}",
                response=self,  # type: ignore[arg-type]
            )

    def json(self) -> object:
        parsed: object = json.loads(self.content)
        return parsed


def test_getter_names_fred_s_empty_200_instead_of_a_decode_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A body-less 200 is a named failure, not a JSON decode error."""
    monkeypatch.setattr(rates.requests, "get", lambda *a, **k: _Response(b""))
    with pytest.raises(ValueError, match="empty 200"):
        fred_getter("k")("series/observations", {"series_id": "DGS10"})


def test_getter_sends_key_and_json_file_type(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def fake_get(url: str, *, params: dict[str, str], timeout: float) -> _Response:
        seen.update(url=url, params=params)
        return _Response(b'{"ok": 1}')

    monkeypatch.setattr(rates.requests, "get", fake_get)
    assert fred_getter("k")("series/vintagedates", {"series_id": "DGS10"}) == {"ok": 1}
    assert str(seen["url"]).endswith("/fred/series/vintagedates")
    assert seen["params"] == {"series_id": "DGS10", "api_key": "k", "file_type": "json"}


def test_getter_never_puts_the_api_key_in_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """requests' HTTPError message carries the full URL, key included; the
    first live run printed it into the log. The redacted error keeps FRED's
    own explanation, which is what a human needs."""
    key = "0123456789abcdef0123456789abcdef"
    body = b'{"error_message":"Bad Request.  There are 5122 vintage dates"}'
    url = f"https://api.stlouisfed.org/fred/series/observations?api_key={key}"
    monkeypatch.setattr(rates.requests, "get", lambda *a, **k: _Response(body, 400, url))

    with pytest.raises(requests.exceptions.HTTPError) as err:
        fred_getter(key)("series/observations", {"series_id": "DGS10"})

    assert key not in str(err.value)
    assert "5122 vintage dates" in str(err.value)
    # Nothing attached that still holds the URL.
    assert err.value.__context__ is None and err.value.__cause__ is None
    assert err.value.response is None and err.value.request is None


def test_getter_retries_a_gateway_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    def flaky(*a: object, **k: object) -> _Response:
        calls.append(1)
        return _Response(b"", 504) if len(calls) < 3 else _Response(b'{"ok": 1}')

    monkeypatch.setattr(rates.requests, "get", flaky)
    monkeypatch.setattr(sources.time, "sleep", lambda _s: None)
    assert fred_getter("k")("series/observations", {"series_id": "DGS10"}) == {"ok": 1}
    assert len(calls) == 3
