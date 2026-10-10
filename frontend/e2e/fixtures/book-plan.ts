// The Book's contributions-plan payloads (GET /api/putlab/book-plan).
//
// Real results, not invented: the route's research function run on Cboe's
// published SPX, PPUT, PPUT3M and VXTH histories through 2026-10-02 with the
// default plan (PPUT, 50% hedged, $10,000 + $500/month, 10-year horizon, vs
// the S&P 500):
//
// * BOOK_PLAN -- the assumed-yield fallback (no tiingo_eod, no rates), the
//   live lake's state until its first Tiingo ingest: one row per assumed yield.
// * BOOK_PLAN_MEASURED -- the measured index leg (SPY total return,
//   fee-adjusted; see hedge-overlay.ts for how it was built): a base and a
//   conservative row, and the S&P comparator relabelled.
// * BOOK_PLAN_BILLS_REFUSED -- the same plan against T-bills, which a lake
//   without rates cannot offer.
//
// Only the provenance ids are fake.

import type { BookPlanResponse } from '../../src/api/client'

export const BOOK_PLAN: BookPlanResponse = {
  "cboe_snapshot": "cboe_strategy@2026-10-02",
  "rates_snapshot": null,
  "tiingo_snapshot": null,
  "code_sha": "e2e0000",
  "plan": {
    "as_of": "2026-10-02",
    "program": "PPUT",
    "hedge_ratio": 0.5,
    "e0": 10000.0,
    "monthly": 500.0,
    "horizon_years": 10,
    "dividend_yield": 0.019,
    "dividend": {
      "source": "assumed",
      "assumed_yield": 0.019,
      "assumed_reason": "measured dividends unavailable",
      "spans": [
        {
          "start": "1975-01-02",
          "end": "2026-10-01",
          "source": "assumed_yield",
          "days": 18900
        }
      ],
      "first_date": "1975-01-02",
      "fee_table_version": null,
      "fees": [],
      "unextended_gap_days": 0,
      "unextended_reason": null,
      "conservative_from": null,
      "conservative_reason": "sizing needs measured dividends",
      "bill_point_in_time_from": null,
      "snapshot_ids": {}
    },
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
        "available": true,
        "reason": null
      },
      {
        "key": "CLL3M",
        "label": "Cboe S&P 500 3-Month Collar 95-110 Index (from 2004-03-19)",
        "available": true,
        "reason": null
      },
      {
        "key": "CLLZ",
        "label": "Cboe S&P 500 Zero-Cost Put Spread Collar Index (from 1986-06-20)",
        "available": true,
        "reason": null
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
        "available": true,
        "reason": null
      },
      {
        "key": "PUTY",
        "label": "Cboe S&P 500 2% OTM PutWrite Index (from 1986-06-30)",
        "available": true,
        "reason": null
      }
    ],
    "window": {
      "start": "2016-10-03",
      "end": "2026-10-01",
      "years": 9.993155,
      "hedged": {
        "label": "50% hedged with PPUT",
        "contributed": 70000.0,
        "terminal_wealth": 163460.056643,
        "irr": 0.141813,
        "max_drawdown": -0.231493
      },
      "comparator": {
        "label": "S&P 500 total return (1.9% assumed yield)",
        "contributed": 70000.0,
        "terminal_wealth": 183423.127777,
        "irr": 0.160559,
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
      "p90_gap": -0.005065,
      "worst_gap": -0.033514,
      "best_gap": 0.013474
    },
    "legs": [
      {
        "key": {
          "kind": "assumed_yield",
          "dividend_yield": 0.014
        },
        "share_ahead": 0.068681,
        "median_gap": -0.012206
      },
      {
        "key": {
          "kind": "assumed_yield",
          "dividend_yield": 0.019
        },
        "share_ahead": 0.046703,
        "median_gap": -0.014816
      },
      {
        "key": {
          "kind": "assumed_yield",
          "dividend_yield": 0.024
        },
        "share_ahead": 0.024725,
        "median_gap": -0.017456
      }
    ],
    "refusal": null
  }
}

export const BOOK_PLAN_MEASURED: BookPlanResponse = {
  "cboe_snapshot": "cboe_strategy@2026-10-02",
  "rates_snapshot": "rates@2026-10-02",
  "tiingo_snapshot": "tiingo_eod@2026-10-02",
  "code_sha": "e2e0000",
  "plan": {
    "as_of": "2026-10-02",
    "program": "PPUT",
    "hedge_ratio": 0.5,
    "e0": 10000.0,
    "monthly": 500.0,
    "horizon_years": 10,
    "dividend_yield": null,
    "dividend": {
      "source": "measured",
      "assumed_yield": null,
      "assumed_reason": null,
      "spans": [
        {
          "start": "1993-01-29",
          "end": "2026-10-02",
          "source": "spy_total_return",
          "days": 12299
        }
      ],
      "first_date": "1993-01-29",
      "fee_table_version": "spy-fees-v1",
      "fees": [
        {
          "effective": "1993-01-29",
          "annual_fee": 0.002,
          "citation": "UNVERIFIED upper bound for 1993-2005: SPY's early expense ratio, set at 0.20%; to be replaced by the SPDR annual reports' figures"
        },
        {
          "effective": "2005-10-01",
          "annual_fee": 0.001,
          "citation": "SPDR Trust fiscal year to 2006-09-30: ordinary expenses 0.1204%, the excess over 0.1000% waived by the trustee (as reported by ETF Trends)"
        },
        {
          "effective": "2007-02-01",
          "annual_fee": 0.000945,
          "citation": "SPDR Trust from 2007-02-01: ordinary expenses accrue at 0.0945%, net of the trustee waiver (as reported by ETF Trends)"
        }
      ],
      "unextended_gap_days": 0,
      "unextended_reason": null,
      "conservative_from": "1993-01-29",
      "conservative_reason": null,
      "bill_point_in_time_from": "2005-06-28",
      "snapshot_ids": {
        "tiingo_eod": "tiingo_eod@2026-10-02",
        "rates": "rates@2026-10-02"
      }
    },
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
        "available": true,
        "reason": null
      },
      {
        "key": "CLL",
        "label": "Cboe S&P 500 95-110 Collar Index (CDN file from 2008-08-26)",
        "available": true,
        "reason": null
      },
      {
        "key": "CLL3M",
        "label": "Cboe S&P 500 3-Month Collar 95-110 Index (from 2004-03-19)",
        "available": true,
        "reason": null
      },
      {
        "key": "CLLZ",
        "label": "Cboe S&P 500 Zero-Cost Put Spread Collar Index (from 1986-06-20)",
        "available": true,
        "reason": null
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
        "available": true,
        "reason": null
      },
      {
        "key": "PUTY",
        "label": "Cboe S&P 500 2% OTM PutWrite Index (from 1986-06-30)",
        "available": true,
        "reason": null
      }
    ],
    "window": {
      "start": "2016-10-03",
      "end": "2026-10-01",
      "years": 9.993155,
      "hedged": {
        "label": "50% hedged with PPUT",
        "contributed": 70000.0,
        "terminal_wealth": 161748.647502,
        "irr": 0.140097,
        "max_drawdown": -0.231595
      },
      "comparator": {
        "label": "S&P 500 total return — SPY, fee-adjusted",
        "contributed": 70000.0,
        "terminal_wealth": 179593.033906,
        "irr": 0.15713,
        "max_drawdown": -0.336943
      },
      "irr_gap": -0.017033,
      "outcome": "fails"
    },
    "rolling": {
      "horizon_years": 10,
      "n_starts": 285,
      "first_start": "1993-02-01",
      "last_start": "2016-10-03",
      "share_ahead": 0.045614,
      "share_behind": 0.94386,
      "share_inconclusive": 0.010526,
      "median_gap": -0.01388,
      "p10_gap": -0.018272,
      "p90_gap": -0.004142,
      "worst_gap": -0.025432,
      "best_gap": 0.012789
    },
    "legs": [
      {
        "key": {
          "kind": "leg",
          "leg": "base"
        },
        "share_ahead": 0.045614,
        "median_gap": -0.01388
      },
      {
        "key": {
          "kind": "leg",
          "leg": "conservative"
        },
        "share_ahead": 0.052632,
        "median_gap": -0.013983
      }
    ],
    "refusal": null
  }
}

export const BOOK_PLAN_BILLS_REFUSED: BookPlanResponse = {
  "cboe_snapshot": "cboe_strategy@2026-10-02",
  "rates_snapshot": null,
  "tiingo_snapshot": null,
  "code_sha": "e2e0000",
  "plan": {
    "as_of": "2026-10-02",
    "program": "PPUT",
    "hedge_ratio": 0.5,
    "e0": 10000.0,
    "monthly": 500.0,
    "horizon_years": 10,
    "dividend_yield": 0.019,
    "dividend": {
      "source": "assumed",
      "assumed_yield": 0.019,
      "assumed_reason": "measured dividends unavailable",
      "spans": [
        {
          "start": "1975-01-02",
          "end": "2026-10-01",
          "source": "assumed_yield",
          "days": 18900
        }
      ],
      "first_date": "1975-01-02",
      "fee_table_version": null,
      "fees": [],
      "unextended_gap_days": 0,
      "unextended_reason": null,
      "conservative_from": null,
      "conservative_reason": "sizing needs measured dividends",
      "bill_point_in_time_from": null,
      "snapshot_ids": {}
    },
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
    "legs": [],
    "refusal": "T-bills: no T-bill history in the lake yet (rates not ingested)"
  }
}

/** BOOK_PLAN_MEASURED as a lake without ``rates`` serves it: no T-bills, so
 *  no conservative leg and no conservative row. */
export function bookPlanMeasuredWithoutRates(): BookPlanResponse {
  const m = BOOK_PLAN_MEASURED
  return {
    ...m,
    rates_snapshot: null,
    plan: {
      ...m.plan,
      dividend: {
        ...m.plan.dividend,
        conservative_from: null,
        conservative_reason: 'sizing needs T-bills for the cash-drag check',
        bill_point_in_time_from: null,
        snapshot_ids: { tiingo_eod: m.plan.dividend.snapshot_ids.tiingo_eod! },
      },
      legs: m.plan.legs.filter((y) => y.key.kind === 'leg' && y.key.leg === 'base'),
    },
  }
}
