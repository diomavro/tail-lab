"""How wrong is this result? — the accuracy context every result must carry.

The README's constitution says accuracy is *surfaced, not filed*: a backtest
figure shown without the known size of its error is a number pretending to be
a measurement. This module assembles the four kinds of context that make a
Put Lab result readable, all from things the platform already measures:

1. **The model-vs-market residual**, weighted for the *regime mix of this
   window* rather than quoted as a global average. That distinction is the
   whole point: the residual is +1.55%/yr in calm markets and **-1.46%/yr in
   crisis** (``docs/MODEL_RESIDUAL.md``), so a window spanning March 2020 and
   a window spanning 2017 are wrong in opposite directions.
2. **The published benchmarks** — Cboe's own put programs over the same
   window. The honest question for a put strategy is not "did it make money"
   but "did it beat the put program you could have bought instead".
3. **Data-quality flags** on the price series the backtest actually read.
4. **The assumptions**, each with what it is worth where that is known.

Nothing here is new measurement. It is the plumbing that carries measurements
already made to the place the result is read, which is the only place they
change a decision.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Sequence
from typing import Literal

import pandas as pd
from pydantic import BaseModel

from tail_lab.contracts.cboe_strategy import DATASET as CBOE_STRATEGY_DATASET
from tail_lab.contracts.cboe_strategy import STRATEGY_INDEX_CATALOGUE
from tail_lab.contracts.optionsdx import DATASET as OPTIONSDX_DATASET
from tail_lab.contracts.optionsdx import month_coverage
from tail_lab.contracts.regime import REGIME_LABELS, RegimeLabel
from tail_lab.lake.store import LakeStore
from tail_lab.research.backtest.index_replication import (
    IndexReplicationResult,
    compute_index_replication,
)
from tail_lab.research.backtest.put_roll import DEFAULT_RATE, IV_CAP, IV_FLOOR, IV_WINDOW
from tail_lab.research.data_quality import assess_asset_quality
from tail_lab.research.regimes.timeline import compute_regime_timeline

logger = logging.getLogger("tail_lab.research.accuracy")

#: The published programs whose replication residuals serve as the model's
#: error bar, and the (moneyness %, tenor weeks) each one measures the model at.
#: Two points, not one: PPUT is monthly 5% OTM and PPUT3M quarterly **10%**
#: OTM, and the residual is materially larger at the deeper strike (+2.71%/yr
#: vs +1.34%/yr) because that is where the volatility skew a flat-vol model
#: ignores actually lives. Quoting the 5% figure at a 20%-OTM tail hedge would
#: understate its optimism by half, so the report picks the nearer reference.
RESIDUAL_PROGRAMS: tuple[str, ...] = ("PPUT", "PPUT3M")
REFERENCE_PARAMS: dict[str, tuple[float, float]] = {
    "PPUT": (5.0, 4.3),
    "PPUT3M": (10.0, 13.0),
}

#: Assets the PPUT residual describes directly rather than by analogy.
INDEX_PROXIES: frozenset[str] = frozenset({"SPY", "SPX", "IVV", "VOO", "^GSPC"})

#: Parameter distance within which a reference program's residual is treated as
#: directly applicable rather than as an analogy.
DIRECT_MONEYNESS_TOLERANCE_PCT = 2.0
DIRECT_TENOR_TOLERANCE_WEEKS = 3.0

#: Cboe programs shown as the "you could have bought this instead" hurdle.
BENCHMARK_PROGRAMS: tuple[str, ...] = ("PPUT", "PPUT3M", "CLL", "VXTH", "SPX")

#: Below this many trading days a window is too short to characterize.
MIN_WINDOW_DAYS = 20


class RegimeShare(BaseModel):
    """Fraction of the backtest window spent in one market regime."""

    regime: RegimeLabel
    n_days: int
    share: float


class ModelAccuracy(BaseModel):
    """The model-vs-market residual, specialized to one backtest window."""

    #: Expected optimism of the model over *this* window, in return per year.
    #: Positive means the model prices puts too cheap, so the backtest's
    #: returns are overstated by roughly this much. ``None`` when the residual
    #: could not be measured (no Cboe snapshot in the lake).
    expected_optimism: float | None
    applicability: str  # "direct" | "indicative" | "unmeasured"
    #: The published program this error bar was measured on, or ``None`` when
    #: none was available. Named explicitly rather than parsed back out of
    #: ``basis`` — a log field or a UI badge should never depend on prose.
    reference: str | None
    regime_mix: list[RegimeShare]
    residual_by_regime: dict[str, float]
    basis: str
    caveat: str


class BenchmarkComparison(BaseModel):
    """A published index over the same window as the backtest."""

    index_symbol: str
    label: str
    total_return: float
    annualized: float


class Assumption(BaseModel):
    """One input the result rests on, and what it is worth."""

    name: str
    value: str
    #: What moving this assumption does to the result, where measured.
    leverage: str | None = None


class QuoteCoverage(BaseModel):
    """Whether REAL option quotes exist for this name, and whether the number
    on screen actually used them.

    Those are two different questions and the second is the one that can
    mislead. Ingesting a fourteen-year market-priced panel does not make a
    displayed backtest market-priced: the roll engine still prices every leg
    with Black-Scholes at a flat vol (`docs/adr/0004`). A surface that showed
    only "real quotes: yes" would invite exactly the reading the constitution
    forbids -- a number looking more trustworthy than it is.

    So ``priced_from`` describes the RESULT and ``months_present`` describes the
    DATA, and they are reported side by side precisely because they disagree
    today.

    **Counts are scoped to the report's own window**, like every other block in
    :class:`AccuracyReport` (``regime_mix`` and ``benchmark_comparisons`` are
    both windowed). Whole-panel counts next to a windowed result would be a
    plausible number answering a question nobody asked: a 2010-2023 panel is
    total coverage of a 2015 backtest and *zero* coverage of a one-year window
    ending today, and only the windowed number tells the reader which. The
    panel's full extent is still reported, as ``panel_first_month`` /
    ``panel_last_month``, because "held, but not here" and "not held at all"
    call for different next actions.
    """

    #: What produced the premiums behind the figures on screen.
    priced_from: Literal["model", "market"]
    #: Whether a real-quote panel exists in the lake for this underlying *at
    #: all* -- independent of whether it overlaps this report's window.
    real_quotes_available: bool
    #: Months in the report's window, and how many of them are held/absent.
    #: ``months_present + months_missing == window_months``.
    window_months: int = 0
    months_present: int = 0
    months_missing: int = 0
    #: Every month of the window is held.
    complete: bool = False
    #: Full extent of the stored panel (``YYYYMM``), which may lie entirely
    #: outside the window above.
    panel_first_month: str | None = None
    panel_last_month: str | None = None
    note: str = ""


class AccuracyReport(BaseModel):
    """Everything a reader needs to know how far this result sits from truth."""

    asset: str
    as_of: dt.date
    years: float
    window_start: dt.date
    model: ModelAccuracy
    benchmarks: list[BenchmarkComparison]
    data_quality_flags: int | None
    data_quality_note: str
    assumptions: list[Assumption]
    quote_coverage: QuoteCoverage


def regime_mix(timeline: pd.Series, *, start: dt.date, end: dt.date) -> list[RegimeShare]:
    """Share of trading days in each regime over ``[start, end]``.

    Pure. Regimes with no days are omitted rather than reported as 0%, so the
    surface shows what the window *was*, not a fixed three-row table mostly
    full of zeros.
    """
    window = timeline.loc[
        (timeline.index >= pd.Timestamp(start)) & (timeline.index <= pd.Timestamp(end))
    ]
    total = len(window)
    if total == 0:
        return []
    counts = window.value_counts()
    shares = [
        RegimeShare(regime=label, n_days=int(counts[label]), share=float(counts[label]) / total)
        for label in REGIME_LABELS
        if label in counts.index
    ]
    return sorted(shares, key=lambda s: s.share, reverse=True)


def blend_residual(
    residual_by_regime: dict[str, float], mix: Sequence[RegimeShare]
) -> float | None:
    """The residual this window should expect, weighted by its regime mix.

    Returns ``None`` when no regime in the mix has a measured residual — an
    honest gap beats a number blended from nothing. Regimes present in the
    window but absent from the measurement are dropped and the remaining
    weights renormalized, so the answer is "the residual over the part of this
    window we can speak to".
    """
    usable = [s for s in mix if s.regime in residual_by_regime]
    weight = sum(s.share for s in usable)
    if not usable or weight <= 0.0:
        return None
    return sum(residual_by_regime[s.regime] * s.share for s in usable) / weight


def reference_distance(symbol: str, *, moneyness_pct: float, tenor_weeks: float) -> float:
    """How far a run sits from a reference program, in relative parameter space.

    Moneyness and tenor are put on a common footing by scaling each difference
    by the reference's own value, so "twice as deep" and "twice as long" count
    the same. An unknown symbol is infinitely far away rather than silently
    scoring zero.
    """
    params = REFERENCE_PARAMS.get(symbol)
    if params is None:
        return float("inf")
    ref_moneyness, ref_tenor = params
    return (
        abs(moneyness_pct - ref_moneyness) / ref_moneyness
        + abs(tenor_weeks - ref_tenor) / ref_tenor
    )


def select_reference(
    replications: Sequence[IndexReplicationResult],
    *,
    moneyness_pct: float,
    tenor_weeks: float,
) -> IndexReplicationResult | None:
    """The published program closest to this run's strike and tenor. Pure."""
    usable = [r for r in replications if r.index_symbol in REFERENCE_PARAMS]
    if not usable:
        return None
    return min(
        usable,
        key=lambda r: reference_distance(
            r.index_symbol, moneyness_pct=moneyness_pct, tenor_weeks=tenor_weeks
        ),
    )


def _applicability(reference: str, asset: str, *, moneyness_pct: float, tenor_weeks: float) -> str:
    """Whether ``reference``'s residual describes this run directly or by analogy."""
    same_underlying = asset.upper().lstrip("^") in {a.lstrip("^") for a in INDEX_PROXIES}
    ref_moneyness, ref_tenor = REFERENCE_PARAMS[reference]
    similar_strike = abs(moneyness_pct - ref_moneyness) <= DIRECT_MONEYNESS_TOLERANCE_PCT
    similar_tenor = abs(tenor_weeks - ref_tenor) <= DIRECT_TENOR_TOLERANCE_WEEKS
    return "direct" if (same_underlying and similar_strike and similar_tenor) else "indicative"


def _caveat(applicability: str, asset: str, reference: str) -> str:
    if applicability == "unmeasured":
        return (
            "No Cboe strategy snapshot in the lake, so the model's error has not been "
            "measured for this run. Treat the returns as unqualified."
        )
    if applicability == "direct":
        return (
            f"Measured on {reference}, which runs the same underlying, strike and tenor as "
            "this run, so it applies directly. It is still a replication, not a "
            "decomposition: settlement convention and the flat rate are folded in "
            "alongside the pricing error."
        )
    ref_moneyness, ref_tenor = REFERENCE_PARAMS[reference]
    return (
        f"Measured on {reference} — S&P 500, {ref_moneyness:.0f}% OTM, ~{ref_tenor:.0f}-week "
        f"puts — not on {asset.upper()} at these parameters. The residual grows with strike "
        "depth (+1.34%/yr at 5% OTM, +2.71%/yr at 10%), and single-name skew is steeper "
        "still, so read this as an indicative floor on the optimism, not a correction to "
        "subtract."
    )


def model_accuracy(
    replications: Sequence[IndexReplicationResult],
    mix: Sequence[RegimeShare],
    *,
    asset: str,
    moneyness_pct: float,
    tenor_weeks: float,
) -> ModelAccuracy:
    """Assemble the model's error bar for one run, from the nearest reference
    program available. Pure."""
    replication = select_reference(
        replications, moneyness_pct=moneyness_pct, tenor_weeks=tenor_weeks
    )
    if replication is None:
        return ModelAccuracy(
            expected_optimism=None,
            applicability="unmeasured",
            regime_mix=list(mix),
            residual_by_regime={},
            reference=None,
            basis="not measured",
            caveat=_caveat("unmeasured", asset, ""),
        )

    by_regime: dict[str, float] = {
        str(bucket.key): bucket.annualized_drag
        for bucket in replication.by_regime
        if bucket.key in REGIME_LABELS
    }
    applicability = _applicability(
        replication.index_symbol, asset, moneyness_pct=moneyness_pct, tenor_weeks=tenor_weeks
    )
    low, high = (
        (
            min(p.annualized_drag for p in replication.dividend_sensitivity),
            max(p.annualized_drag for p in replication.dividend_sensitivity),
        )
        if replication.dividend_sensitivity
        else (replication.annualized_drag,) * 2
    )
    return ModelAccuracy(
        expected_optimism=blend_residual(by_regime, mix),
        applicability=applicability,
        reference=replication.index_symbol,
        regime_mix=list(mix),
        residual_by_regime=by_regime,
        basis=(
            f"{replication.index_symbol} replication, {replication.n_rolls} rolls, "
            f"{replication.start:%Y} to {replication.end:%Y}; whole-window residual "
            f"{replication.annualized_drag * 100:+.2f}%/yr "
            f"(dividend assumption moves it {low * 100:+.2f}% to {high * 100:+.2f}%)"
        ),
        caveat=_caveat(applicability, asset, replication.index_symbol),
    )


def benchmark_comparisons(
    bronze: pd.DataFrame, *, start: dt.date, end: dt.date, years: float
) -> list[BenchmarkComparison]:
    """Each catalogued program's return over the same window. Pure.

    A program whose history does not cover the window is omitted, not shown as
    zero: PPUT reaches 1986 but CLL only 2008, and a silent 0% would read as
    "flat" rather than "not applicable".
    """
    out: list[BenchmarkComparison] = []
    for symbol in BENCHMARK_PROGRAMS:
        rows = bronze[bronze["index_symbol"] == symbol]
        if rows.empty:
            continue
        window = rows[
            (rows["trade_date"] >= pd.Timestamp(start)) & (rows["trade_date"] <= pd.Timestamp(end))
        ].sort_values("trade_date")
        if len(window) < MIN_WINDOW_DAYS:
            continue
        first = float(window["close"].iloc[0])
        last = float(window["close"].iloc[-1])
        if first <= 0.0:
            continue
        total = last / first - 1.0
        out.append(
            BenchmarkComparison(
                index_symbol=symbol,
                label=STRATEGY_INDEX_CATALOGUE.get(symbol, symbol),
                total_return=total,
                annualized=(1.0 + total) ** (1.0 / years) - 1.0 if years > 0 else total,
            )
        )
    return out


def standing_assumptions(*, rate: float, expected_optimism: float | None) -> list[Assumption]:
    """The inputs a Put Lab result rests on, each with its known leverage."""
    optimism = (
        f"the model's premium runs {expected_optimism * 100:+.2f}%/yr optimistic over this window"
        if expected_optimism is not None
        else "unmeasured for this window"
    )
    return [
        Assumption(
            name="Option premiums",
            value="Black-Scholes model price, not a traded quote",
            leverage=optimism,
        ),
        Assumption(
            name="Implied volatility",
            value=f"{IV_WINDOW}-day trailing realized vol, clamped to "
            f"[{IV_FLOOR:.0%}, {IV_CAP:.0%}]",
            leverage="a proxy for ATM implied vol; it carries no skew, which is the "
            "leading explanation for the residual above",
        ),
        Assumption(
            name="Risk-free rate",
            value=f"flat {rate:.1%}",
            leverage="stands in for a curve that ranged 0% to 6.5% over the measured window",
        ),
        Assumption(
            name="Prices",
            value="raw closes, never dividend-adjusted",
            leverage="deliberate: strikes must be set off prices that actually traded "
            "(docs/DISCOVERIES.md #1)",
        ),
        Assumption(
            name="Brokerage",
            value="$0.65/contract plus a tenor- and moneyness-scaled half-spread, paid at entry",
            leverage=None,
        ),
    ]


def _months_between(start: dt.date, end: dt.date) -> list[str]:
    """Every ``YYYYMM`` from ``start``'s month to ``end``'s month, inclusive."""
    months: list[str] = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        months.append(f"{year}{month:02d}")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return months


def quote_coverage(
    store: LakeStore, *, asset: str, window_start: dt.date, as_of: dt.date
) -> QuoteCoverage:
    """What real-quote data covers this report's window, and what priced it.

    Degrades like every other block here: a missing panel is reported, never
    raised, so the surface cannot go silent (which would be indistinguishable
    from "no problem"). The caller wraps this too -- belt and braces, because
    an exception escaping here would take down the whole panel rather than
    this block.

    **Reads one column, not the panel.** ``read_bronze_as_of`` materialises and
    caches the entire partition, which is fine for the ~1k-row datasets it was
    written for and ruinous here: the SPY panel is 3.3M rows and the app runs
    on a 1 GB machine (``fly.toml``). Coverage needs the distinct *months* of
    ``quote_date`` and nothing else, so it projects to that one column and
    deduplicates to days (~3.5k values over fourteen years) before any Python
    objects are built.
    """
    window = _months_between(window_start, as_of)
    dataset = f"{OPTIONSDX_DATASET}_{asset.lower()}"
    absent_note = (
        "No real option quotes cover this window. Every premium behind these "
        "figures is a Black-Scholes price at a flat volatility, and the "
        "returns are a relative ranking rather than P&L."
    )
    try:
        column = store.read_bronze_column_as_of(dataset, as_of, "quote_date")
    except (LookupError, KeyError):
        return QuoteCoverage(
            priced_from="model",
            real_quotes_available=False,
            window_months=len(window),
            months_missing=len(window),
            note=absent_note,
        )

    # Deduplicate to distinct DAYS in pandas first. Building a date object per
    # row would be millions of allocations to answer a question with at most a
    # few hundred distinct answers.
    unique_days = pd.to_datetime(pd.Series(column).dropna().unique())
    present_all, _ = month_coverage([ts.date() for ts in unique_days])
    if not present_all:
        return QuoteCoverage(
            priced_from="model",
            real_quotes_available=False,
            window_months=len(window),
            months_missing=len(window),
            note=absent_note,
        )

    held = set(present_all)
    in_window = [m for m in window if m in held]
    missing = [m for m in window if m not in held]
    complete = bool(window) and not missing

    if not in_window:
        gap_note = (
            f"held for {present_all[0]} to {present_all[-1]}, which lies "
            "entirely outside this window"
        )
    elif complete:
        gap_note = f"complete across all {len(window)} months of this window"
    else:
        gap_note = f"{len(missing)} of the {len(window)} months in this window MISSING"

    return QuoteCoverage(
        priced_from="model",
        real_quotes_available=True,
        window_months=len(window),
        months_present=len(in_window),
        months_missing=len(missing),
        complete=complete,
        panel_first_month=present_all[0],
        panel_last_month=present_all[-1],
        note=(
            f"Real quotes for this name are {gap_note}. "
            "The figures on screen do NOT use them either way — the roll engine "
            "still prices every leg with Black-Scholes at a flat volatility "
            "(docs/adr/0004). Holding the data is not the same as using it."
        ),
    )


def compute_accuracy_report(
    store: LakeStore,
    *,
    asset: str,
    as_of: dt.date,
    years: float,
    moneyness_pct: float,
    tenor_weeks: float,
    rate: float = DEFAULT_RATE,
    replications: Sequence[IndexReplicationResult] | None = None,
) -> AccuracyReport:
    """The full accuracy context for one Put Lab run, read point-in-time.

    Every piece degrades independently: a missing Cboe snapshot costs the
    residual and the benchmarks but not the data-quality scan, and a missing
    VIX snapshot costs the regime mix but not the benchmarks. That is
    deliberate — the constitution's requirement is that the surface never goes
    silent, and a report that raised on any missing input would do exactly
    that.

    ``replications`` is injectable so the caller can share the (expensive)
    replications across many runs; omitted, they are computed here. A program
    that cannot be replicated is dropped rather than failing the report — with
    two references, losing one still leaves an error bar.
    """
    window_start = as_of - dt.timedelta(days=round(years * 365.25))

    if replications is None:
        replications = []
        for symbol in RESIDUAL_PROGRAMS:
            try:
                replications.append(
                    compute_index_replication(store, index_symbol=symbol, as_of=as_of)
                )
            except (LookupError, KeyError):
                continue

    try:
        mix = regime_mix(compute_regime_timeline(store, as_of=as_of), start=window_start, end=as_of)
    except LookupError:
        mix = []

    try:
        bronze = store.read_bronze_as_of(CBOE_STRATEGY_DATASET, as_of)
        benchmarks = benchmark_comparisons(bronze, start=window_start, end=as_of, years=years)
    except LookupError:
        benchmarks = []

    try:
        quality = assess_asset_quality(store, asset=asset, as_of=as_of)
        flags: int | None = quality.n_suspicious
        note = (
            f"{quality.n_suspicious} suspicious bar(s) in {quality.n_bars} scanned"
            if quality.n_suspicious
            else f"clean across {quality.n_bars} bars"
        )
    except LookupError:
        flags, note = None, "no price snapshot to scan"

    # Broad except, deliberately. The sibling blocks above catch LookupError
    # because that is the only way their inputs go missing; this one reads a
    # multi-million-row vendor panel over S3, so it can also fail on memory, a
    # transport error, or a schema drift in a dataset no test fixture covers.
    # Whatever goes wrong, the panel must still render saying the result is
    # model-priced -- an accuracy panel that 500s is the one outcome worse than
    # an incomplete one, because the surface then shows nothing at all.
    try:
        coverage = quote_coverage(store, asset=asset, window_start=window_start, as_of=as_of)
    except Exception:
        logger.exception("accuracy.quote_coverage_failed", extra={"asset": asset})
        coverage = QuoteCoverage(
            priced_from="model",
            real_quotes_available=False,
            note=(
                "Could not determine what real quote data is held for this name. "
                "The figures on screen are Black-Scholes prices at a flat "
                "volatility either way (docs/adr/0004)."
            ),
        )

    model = model_accuracy(
        replications, mix, asset=asset, moneyness_pct=moneyness_pct, tenor_weeks=tenor_weeks
    )
    return AccuracyReport(
        asset=asset.upper(),
        as_of=as_of,
        years=years,
        window_start=window_start,
        model=model,
        benchmarks=benchmarks,
        data_quality_flags=flags,
        data_quality_note=note,
        assumptions=standing_assumptions(rate=rate, expected_optimism=model.expected_optimism),
        quote_coverage=coverage,
    )
