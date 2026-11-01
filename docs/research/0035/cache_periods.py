"""Cache the live walk-forward periods. The simulation copy has its outcomes
BLANKED (payoff = 0, spy_payoff = 0): the specification is chosen on
placebo outcomes only, and the real outcomes are read once, at the end."""
import dataclasses, datetime as dt, pickle, sys
import numpy as np
from tail_lab.api.putlab_routes import get_lake_store, universe_symbols
from tail_lab.research.backtest.strike_rule import ByDelta
from tail_lab.research.backtest.walk_forward import collect_periods
out = sys.argv[1]
c = collect_periods(get_lake_store(), symbols=universe_symbols(), as_of=dt.date(2026, 10, 31),
                    rule=ByDelta(0.10), tenor_weeks=4.0, signal_years=4.0, top_k=5)
with open(f"{out}/periods_REAL_OUTCOMES.pkl", "wb") as f:
    pickle.dump(c.periods, f)
blind = [dataclasses.replace(p, payoff=np.zeros_like(p.payoff), spy_payoff=0.0) for p in c.periods]
with open(f"{out}/periods_signals_only.pkl", "wb") as f:
    pickle.dump(blind, f)
print(len(c.periods), "periods;", sum(len(p.symbols) for p in c.periods), "name-periods")
