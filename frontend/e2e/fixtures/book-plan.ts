// The Book's contributions-plan payloads (GET /api/putlab/book-plan).
//
// Real results, not invented: the route run on Cboe's published SPX, PPUT,
// PPUT3M and VXTH histories through 2026-10-02 with the default plan (PPUT,
// 50% hedged, $10,000 + $500/month, 10-year horizon, vs the S&P 500),
// and the same plan against T-bills, which this lake cannot offer yet (no
// rates). Only the provenance ids are fake.

import type { BookPlanResponse } from '../../src/api/client'

export const BOOK_PLAN: BookPlanResponse = {
  "cboe_snapshot": "cboe_strategy@2026-10-02",
  "rates_snapshot": null,
  "code_sha": "e2e0000",
  "plan": {
    "as_of": "2026-10-02",
    "program": "PPUT",
    "hedge_ratio": 0.5,
    "e0": 10000.0,
    "monthly": 500.0,
    "horizon_years": 10,
    "dividend_yield": 0.019,
    "comparator": "spx",
    "comparators": [
      {
        "key": "spx",
        "label": "S&P 500",
        "available": true,
        "reason": null
      },
      {
        "key": "cash",
        "label": "Cash",
        "available": true,
        "reason": null
      },
      {
        "key": "bills",
        "label": "T-bills",
        "available": false,
        "reason": "no T-bill history in the lake yet (rates not ingested)"
      },
      {
        "key": "CLL",
        "label": "Cboe S&P 500 95-110 Collar Index (CDN file from 2008-08-26)",
        "available": false,
        "reason": "CLL is not in the Cboe snapshot"
      },
      {
        "key": "CLL3M",
        "label": "Cboe S&P 500 3-Month Collar 95-110 Index (from 2004-03-19)",
        "available": false,
        "reason": "CLL3M is not in the Cboe snapshot"
      },
      {
        "key": "CLLZ",
        "label": "Cboe S&P 500 Zero-Cost Put Spread Collar Index (from 1986-06-20)",
        "available": false,
        "reason": "CLLZ is not in the Cboe snapshot"
      },
      {
        "key": "CLLR",
        "label": "Cboe Russell 2000 Zero-Cost Put Spread Collar Index (from 2001-01-31)",
        "available": false,
        "reason": "CLLR is not in the Cboe snapshot"
      },
      {
        "key": "PUT",
        "label": "Cboe S&P 500 PutWrite Index (CDN file from 1991-03-04)",
        "available": false,
        "reason": "PUT is not in the Cboe snapshot"
      },
      {
        "key": "PUTY",
        "label": "Cboe S&P 500 2% OTM PutWrite Index (from 1986-06-30)",
        "available": false,
        "reason": "PUTY is not in the Cboe snapshot"
      }
    ],
    "window": {
      "start": "2016-10-03",
      "end": "2026-10-01",
      "years": 9.993155,
      "hedged": {
        "label": "50% hedged with PPUT",
        "contributed": 70000.0,
        "terminal_wealth": 163460.316294,
        "irr": 0.141813,
        "max_drawdown": -0.231493
      },
      "comparator": {
        "label": "S&P 500 total return (1.9% assumed yield)",
        "contributed": 70000.0,
        "terminal_wealth": 183423.718303,
        "irr": 0.16056,
        "max_drawdown": -0.338093
      },
      "irr_gap": -0.018747,
      "outcome": "fails"
    },
    "rolling": {
      "horizon_years": 10,
      "n_starts": 364,
      "first_start": "1986-07-01",
      "last_start": "2016-10-03",
      "share_ahead": 0.046703,
      "share_behind": 0.953297,
      "share_inconclusive": 0.0,
      "median_gap": -0.014816,
      "p10_gap": -0.022603,
      "p90_gap": -0.005066,
      "worst_gap": -0.033514,
      "best_gap": 0.013474
    },
    "by_yield": [
      {
        "dividend_yield": 0.014,
        "share_ahead": 0.068681,
        "median_gap": -0.012206
      },
      {
        "dividend_yield": 0.019,
        "share_ahead": 0.046703,
        "median_gap": -0.014816
      },
      {
        "dividend_yield": 0.024,
        "share_ahead": 0.024725,
        "median_gap": -0.017456
      }
    ],
    "refusal": null
  }
}

export const BOOK_PLAN_BILLS_REFUSED: BookPlanResponse = {
  "cboe_snapshot": "cboe_strategy@2026-10-02",
  "rates_snapshot": null,
  "code_sha": "e2e0000",
  "plan": {
    "as_of": "2026-10-02",
    "program": "PPUT",
    "hedge_ratio": 0.5,
    "e0": 10000.0,
    "monthly": 500.0,
    "horizon_years": 10,
    "dividend_yield": 0.019,
    "comparator": "bills",
    "comparators": [
      {
        "key": "spx",
        "label": "S&P 500",
        "available": true,
        "reason": null
      },
      {
        "key": "cash",
        "label": "Cash",
        "available": true,
        "reason": null
      },
      {
        "key": "bills",
        "label": "T-bills",
        "available": false,
        "reason": "no T-bill history in the lake yet (rates not ingested)"
      },
      {
        "key": "CLL",
        "label": "Cboe S&P 500 95-110 Collar Index (CDN file from 2008-08-26)",
        "available": false,
        "reason": "CLL is not in the Cboe snapshot"
      },
      {
        "key": "CLL3M",
        "label": "Cboe S&P 500 3-Month Collar 95-110 Index (from 2004-03-19)",
        "available": false,
        "reason": "CLL3M is not in the Cboe snapshot"
      },
      {
        "key": "CLLZ",
        "label": "Cboe S&P 500 Zero-Cost Put Spread Collar Index (from 1986-06-20)",
        "available": false,
        "reason": "CLLZ is not in the Cboe snapshot"
      },
      {
        "key": "CLLR",
        "label": "Cboe Russell 2000 Zero-Cost Put Spread Collar Index (from 2001-01-31)",
        "available": false,
        "reason": "CLLR is not in the Cboe snapshot"
      },
      {
        "key": "PUT",
        "label": "Cboe S&P 500 PutWrite Index (CDN file from 1991-03-04)",
        "available": false,
        "reason": "PUT is not in the Cboe snapshot"
      },
      {
        "key": "PUTY",
        "label": "Cboe S&P 500 2% OTM PutWrite Index (from 1986-06-30)",
        "available": false,
        "reason": "PUTY is not in the Cboe snapshot"
      }
    ],
    "window": null,
    "rolling": null,
    "by_yield": [],
    "refusal": "T-bills: no T-bill history in the lake yet (rates not ingested)"
  }
}
