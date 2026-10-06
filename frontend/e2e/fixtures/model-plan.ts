// The Book's model-priced plan payload (GET /api/putlab/book-plan/model).
//
// Real route output, not invented: Cboe's SPX and the lake's VIX through
// 2026-10-02, default plan (5% OTM, all of $500/month on puts, $10,000
// start, 10-year horizon). Only the provenance ids are fake.

import type { ModelPlanResponse } from '../../src/api/client'

export const MODEL_PLAN: ModelPlanResponse = {
  "cboe_snapshot": "cboe_strategy@2026-10-02",
  "vix_snapshot": "vix@2026-10-02",
  "code_sha": "e2e0000",
  "plan": {
    "as_of": "2026-10-02",
    "accounting": "contribution-funded: a share of each month's X buys puts, the rest the S&P 500; payoffs reinvested in the S&P 500",
    "moneyness_pct": 5.0,
    "e0": 10000.0,
    "monthly": 500.0,
    "put_share": 1.0,
    "horizon_years": 10,
    "dividend_yield": 0.019,
    "accuracy": {
      "moneyness_pct": 5.0,
      "vol_gap": 0.022,
      "calm_ratio": 0.84,
      "stress_ratio": 1.06,
      "statement": "Model-priced: Black-Scholes at the VIX plus the measured skew gap (+2.2 vol points at 5% OTM, docs/MODEL_RESIDUAL.md), at a flat 4% rate. Checked against 214 real monthly SPY put mids (2008-2025), it pays a median 0.84x the market in calm months (VIX below 17) -- most so in the zero-rate years, and less than a buyer pays at the ask -- which flatters the puts, and 1.06x in stress (VIX 28 and up). Drawdowns are sampled at monthly rolls, so they read shallower than daily ones. An estimate, not a quote."
    },
    "window": {
      "start": "2016-09-16",
      "end": "2026-09-18",
      "years": 10.004107,
      "hedged": {
        "label": "S&P 500 + 100% of each month on puts (model)",
        "contributed": 70000.0,
        "terminal_wealth": 195368.437858,
        "irr": 0.170678,
        "max_drawdown": -0.472886
      },
      "comparator": {
        "label": "S&P 500 only",
        "contributed": 70000.0,
        "terminal_wealth": 184233.394149,
        "irr": 0.161158,
        "max_drawdown": -0.308413
      },
      "irr_gap": 0.00952,
      "outcome": "holds"
    },
    "rolling": {
      "horizon_years": 10,
      "n_starts": 321,
      "first_start": "1990-01-19",
      "last_start": "2016-09-16",
      "share_ahead": 0.068536,
      "share_behind": 0.928349,
      "share_inconclusive": 0.003115,
      "median_gap": -0.068034,
      "p10_gap": -0.126041,
      "p90_gap": -0.003482,
      "worst_gap": -0.203746,
      "best_gap": 0.010898
    },
    "refusal": null
  }
}
