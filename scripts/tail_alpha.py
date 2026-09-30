"""Measure the tail index of a name's down-moves, and where its tail begins.

Read-only. Reads bronze OHLCV as-of today, so it is safe for an agent to run.

The point of the report is that it refuses as often as it answers. A Hill
estimate without a plateau, or one whose plateau sits inside the body of the
distribution, is not a measurement, and this prints that verdict rather than a
number (``docs/adr/0026`` §7).
"""

from __future__ import annotations

import datetime as dt
import sys

from tail_lab.config import get_lake_store
from tail_lab.research.backtest.put_roll import load_asof_series
from tail_lab.research.surface.hill import hill_plot, stable_k
from tail_lab.research.surface.realised import gated_realised_alpha
from tail_lab.research.surface.returns import compare_return_bases, loss_magnitudes


def main(symbol: str) -> int:
    # load_asof_series is the single chokepoint every backtest path reads
    # through, and it is where the raw-vs-adjusted close basis is decided once
    # for all of them. Reading bronze directly here would fork that decision.
    try:
        prices, _ = load_asof_series(get_lake_store(), symbol, dt.date.today())
    except LookupError:
        # A typo'd TAIL_SYMBOL is the common case; a traceback is the wrong way
        # to say so from a make target. Matches scripts/greeks_check.py.
        print(
            f"no OHLCV for {symbol.upper()} in the lake — run `make ingest-ohlcv SYMBOL={symbol.upper()}`"
        )
        return 1

    losses = [float(x) for x in loss_magnitudes(prices)]
    print(f"{symbol.upper()}: {len(prices)} closes, {len(losses)} down-moves")

    realised = gated_realised_alpha(prices)
    fit = realised.fit
    if fit is None:
        print(f"  Karamata onset : REFUSED -- {realised.refusal}")
        print("  realised alpha : REFUSED -- no Karamata region to gate on")
        return 1

    if fit.is_flat:
        print(
            f"  Karamata onset : {fit.onset * 100:.2f}% daily move "
            f"({fit.n_beyond} observations beyond, flatness {fit.flatness:.3f}, "
            f"{'converged' if fit.converged else 'DID NOT CONVERGE'})"
        )
    else:
        print(
            f"  Karamata onset : NONE -- no stretch of this sample is flat to tolerance "
            f"(best flatness {fit.flatness:.3f} over {fit.n_beyond} observations). "
            "There is no measured point where the strong Pareto law takes over."
        )

    gated = realised.plateau
    if gated is not None:
        print(
            f"  realised alpha : {gated.alpha:.3f} +/- {gated.standard_error:.3f} "
            f"(k={gated.k}, threshold {gated.threshold * 100:.2f}%)"
        )
        print(
            "                   the SE is Hill's iid formula and understates under "
            "volatility clustering"
        )
    else:
        # The ungated plateau is diagnosis only -- it is what the gate refused,
        # and is printed here rather than carried on the shared result.
        ungated = stable_k(hill_plot(losses))
        if not fit.is_flat or not fit.converged:
            found = (
                f"alpha {ungated.alpha:.2f} at a {ungated.threshold * 100:.2f}% move"
                if ungated
                else "none"
            )
            print(f"  realised alpha : REFUSED -- {realised.refusal} (plateau found: {found})")
        elif ungated is None:
            print("  realised alpha : NO PLATEAU -- this data supports no tail index")
        else:
            print(
                f"  realised alpha : REFUSED -- the first plateau (alpha "
                f"{ungated.alpha:.2f}) sits at a {ungated.threshold * 100:.2f}% move, "
                "inside the body"
            )

    bases = compare_return_bases(prices, k=min(100, len(losses) // 4))
    print(
        f"  basis check    : arithmetic {bases.alpha_arithmetic.alpha:.3f} vs "
        f"log {bases.alpha_log.alpha:.3f} (divergence {bases.divergence:+.3f})"
    )
    print("                   only the arithmetic basis is a tail index (docs/adr/0026 §5)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "SPY"))
