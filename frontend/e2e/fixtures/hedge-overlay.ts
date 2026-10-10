// The Book tab's payloads (GET /api/putlab/hedge-overlay).
//
// Unlike putlab.ts these numbers are NOT invented: they are the real result of
// research/backtest/hedge_overlay.run_hedge_overlay on Cboe's published SPX,
// PPUT, PPUT3M and VXTH histories through 2026-10-02 (only the provenance ids
// are fake), in the shapes the index leg can take (research/backtest/index_leg):
//
// * HEDGE_OVERLAY -- the ASSUMED fallback the live lake serves until its first
//   tiingo_eod ingest: SPX plus a flat 1.9% yield, re-run at 1.4/1.9/2.4%, and
//   no recommended size. PPUT and PPUT3M fail, VXTH's letter window is clipped
//   to 2006-03-31 and holds, VXTH's full history is too close to call.
// * HEDGE_OVERLAY_MEASURED -- SPY's measured total return (one Tiingo fetch of
//   SPY, cut at 2026-10-02), fee-adjusted, with the conservative cash-drag leg
//   on DGS3MO (the numbers were computed reading rates as of 2026-10-09, the
//   lake's first rates ingest; the cited id is dated 2026-10-02, because a
//   payload as of 2026-10-02 can cite no snapshot from after it). Full history starts at SPY's 1993 listing. The size:
//   0 for PPUT and PPUT3M ("growth falls at every step"), 0 for VXTH's full
//   history (margin under the 1bp bar), half of w* for VXTH's letter window.
// * hedgeOverlayWithoutRates() -- the measured payload as a lake without
//   rates serves it: the base leg only, and every size withheld.

import type { HedgeOverlayResponse } from '../../src/api/client'

export const HEDGE_OVERLAY: HedgeOverlayResponse = {
  "cboe_snapshot": "cboe_strategy@2026-10-02",
  "tiingo_snapshot": null,
  "rates_snapshot": null,
  "code_sha": "e2e0000",
  "overlay": {
    "as_of": "2026-10-02",
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
    "weights": [
      0.0,
      0.1,
      0.2,
      0.3,
      0.4,
      0.5,
      0.6,
      0.7,
      0.8,
      0.9,
      1.0
    ],
    "programs": [
      {
        "index_symbol": "PPUT",
        "description": "Cboe S&P 500 5% Put Protection Index (from 1986-06-30)",
        "first_date": "1986-06-30",
        "windows": [
          {
            "key": "cole",
            "label": "The letter's window (2005 to Mar 2016)",
            "requested_start": "2005-01-03",
            "requested_end": "2016-03-31",
            "start": "2005-01-03",
            "end": "2016-03-31",
            "clipped": false,
            "years": 11.238877,
            "points": [
              {
                "weight": 0.0,
                "cagr": 0.069016,
                "volatility": 0.199963,
                "max_drawdown": -0.556037,
                "cagr_per_vol": 0.345143
              },
              {
                "weight": 0.1,
                "cagr": 0.066981,
                "volatility": 0.192109,
                "max_drawdown": -0.542604,
                "cagr_per_vol": 0.348661
              },
              {
                "weight": 0.2,
                "cagr": 0.06487,
                "volatility": 0.184647,
                "max_drawdown": -0.529017,
                "cagr_per_vol": 0.351319
              },
              {
                "weight": 0.3,
                "cagr": 0.062685,
                "volatility": 0.177582,
                "max_drawdown": -0.51528,
                "cagr_per_vol": 0.35299
              },
              {
                "weight": 0.4,
                "cagr": 0.060427,
                "volatility": 0.170925,
                "max_drawdown": -0.501394,
                "cagr_per_vol": 0.353532
              },
              {
                "weight": 0.5,
                "cagr": 0.0581,
                "volatility": 0.164686,
                "max_drawdown": -0.487364,
                "cagr_per_vol": 0.35279
              },
              {
                "weight": 0.6,
                "cagr": 0.055703,
                "volatility": 0.158879,
                "max_drawdown": -0.473192,
                "cagr_per_vol": 0.3506
              },
              {
                "weight": 0.7,
                "cagr": 0.053239,
                "volatility": 0.153519,
                "max_drawdown": -0.45978,
                "cagr_per_vol": 0.346789
              },
              {
                "weight": 0.8,
                "cagr": 0.050709,
                "volatility": 0.148625,
                "max_drawdown": -0.446451,
                "cagr_per_vol": 0.341189
              },
              {
                "weight": 0.9,
                "cagr": 0.048116,
                "volatility": 0.144214,
                "max_drawdown": -0.433049,
                "cagr_per_vol": 0.33364
              },
              {
                "weight": 1.0,
                "cagr": 0.045459,
                "volatility": 0.140305,
                "max_drawdown": -0.419578,
                "cagr_per_vol": 0.324004
              }
            ],
            "best_weight": 0.0,
            "margin": -0.002035,
            "outcome": "fails",
            "best_weight_risk_adjusted": 0.4,
            "outcome_risk_adjusted": "holds",
            "legs": [
              {
                "key": {
                  "kind": "assumed_yield",
                  "dividend_yield": 0.014
                },
                "best_weight": 0.0,
                "margin": -0.001503,
                "outcome": "fails"
              },
              {
                "key": {
                  "kind": "assumed_yield",
                  "dividend_yield": 0.019
                },
                "best_weight": 0.0,
                "margin": -0.002035,
                "outcome": "fails"
              },
              {
                "key": {
                  "kind": "assumed_yield",
                  "dividend_yield": 0.024
                },
                "best_weight": 0.0,
                "margin": -0.002569,
                "outcome": "fails"
              }
            ],
            "sizing": {
              "recommended_ratio": null,
              "reason": "sizing needs measured dividends",
              "at_cap": false,
              "w_star_grid": 0.0,
              "w_star": 0.0,
              "g0": 0.069016,
              "g_star": 0.069016,
              "g_half": 0.069016,
              "half_keeps": null,
              "break_even": null,
              "break_even_status": "none",
              "naive_kelly": -3.002562,
              "margin_by_leg": {
                "assumed": 0.0
              },
              "grid_note": null,
              "halves": [
                {
                  "start": "2005-01-03",
                  "end": "2010-07-01",
                  "w_star": 0.342741
                },
                {
                  "start": "2010-08-02",
                  "end": "2016-03-01",
                  "w_star": 0.0
                }
              ],
              "decisive_months": [],
              "curve": [
                {
                  "weight": 0.0,
                  "cagr": 0.069016
                },
                {
                  "weight": 0.025,
                  "cagr": 0.068514
                },
                {
                  "weight": 0.05,
                  "cagr": 0.068008
                },
                {
                  "weight": 0.075,
                  "cagr": 0.067497
                },
                {
                  "weight": 0.1,
                  "cagr": 0.066981
                },
                {
                  "weight": 0.125,
                  "cagr": 0.06646
                },
                {
                  "weight": 0.15,
                  "cagr": 0.065935
                },
                {
                  "weight": 0.175,
                  "cagr": 0.065405
                },
                {
                  "weight": 0.2,
                  "cagr": 0.06487
                },
                {
                  "weight": 0.225,
                  "cagr": 0.064331
                },
                {
                  "weight": 0.25,
                  "cagr": 0.063787
                },
                {
                  "weight": 0.275,
                  "cagr": 0.063238
                },
                {
                  "weight": 0.3,
                  "cagr": 0.062685
                },
                {
                  "weight": 0.325,
                  "cagr": 0.062127
                },
                {
                  "weight": 0.35,
                  "cagr": 0.061565
                },
                {
                  "weight": 0.375,
                  "cagr": 0.060998
                },
                {
                  "weight": 0.4,
                  "cagr": 0.060427
                },
                {
                  "weight": 0.425,
                  "cagr": 0.059852
                },
                {
                  "weight": 0.45,
                  "cagr": 0.059272
                },
                {
                  "weight": 0.475,
                  "cagr": 0.058688
                },
                {
                  "weight": 0.5,
                  "cagr": 0.0581
                },
                {
                  "weight": 0.525,
                  "cagr": 0.057507
                },
                {
                  "weight": 0.55,
                  "cagr": 0.05691
                },
                {
                  "weight": 0.575,
                  "cagr": 0.056308
                },
                {
                  "weight": 0.6,
                  "cagr": 0.055703
                },
                {
                  "weight": 0.625,
                  "cagr": 0.055093
                },
                {
                  "weight": 0.65,
                  "cagr": 0.054479
                },
                {
                  "weight": 0.675,
                  "cagr": 0.053861
                },
                {
                  "weight": 0.7,
                  "cagr": 0.053239
                },
                {
                  "weight": 0.725,
                  "cagr": 0.052613
                },
                {
                  "weight": 0.75,
                  "cagr": 0.051982
                },
                {
                  "weight": 0.775,
                  "cagr": 0.051348
                },
                {
                  "weight": 0.8,
                  "cagr": 0.050709
                },
                {
                  "weight": 0.825,
                  "cagr": 0.050067
                },
                {
                  "weight": 0.85,
                  "cagr": 0.04942
                },
                {
                  "weight": 0.875,
                  "cagr": 0.04877
                },
                {
                  "weight": 0.9,
                  "cagr": 0.048116
                },
                {
                  "weight": 0.925,
                  "cagr": 0.047457
                },
                {
                  "weight": 0.95,
                  "cagr": 0.046795
                },
                {
                  "weight": 0.975,
                  "cagr": 0.046129
                },
                {
                  "weight": 1.0,
                  "cagr": 0.045459
                }
              ]
            }
          },
          {
            "key": "full",
            "label": "Full history",
            "requested_start": "1986-06-30",
            "requested_end": "2026-10-02",
            "start": "1986-06-30",
            "end": "2026-10-01",
            "clipped": false,
            "years": 40.25462,
            "points": [
              {
                "weight": 0.0,
                "cagr": 0.109348,
                "volatility": 0.182941,
                "max_drawdown": -0.556037,
                "cagr_per_vol": 0.59772
              },
              {
                "weight": 0.1,
                "cagr": 0.106602,
                "volatility": 0.176337,
                "max_drawdown": -0.542604,
                "cagr_per_vol": 0.604537
              },
              {
                "weight": 0.2,
                "cagr": 0.103806,
                "volatility": 0.170112,
                "max_drawdown": -0.529017,
                "cagr_per_vol": 0.610222
              },
              {
                "weight": 0.3,
                "cagr": 0.10096,
                "volatility": 0.164272,
                "max_drawdown": -0.51528,
                "cagr_per_vol": 0.614594
              },
              {
                "weight": 0.4,
                "cagr": 0.098067,
                "volatility": 0.158825,
                "max_drawdown": -0.501394,
                "cagr_per_vol": 0.617455
              },
              {
                "weight": 0.5,
                "cagr": 0.095128,
                "volatility": 0.153781,
                "max_drawdown": -0.487364,
                "cagr_per_vol": 0.618591
              },
              {
                "weight": 0.6,
                "cagr": 0.092144,
                "volatility": 0.149152,
                "max_drawdown": -0.473192,
                "cagr_per_vol": 0.617781
              },
              {
                "weight": 0.7,
                "cagr": 0.089116,
                "volatility": 0.14495,
                "max_drawdown": -0.45978,
                "cagr_per_vol": 0.614803
              },
              {
                "weight": 0.8,
                "cagr": 0.086046,
                "volatility": 0.141188,
                "max_drawdown": -0.446451,
                "cagr_per_vol": 0.609446
              },
              {
                "weight": 0.9,
                "cagr": 0.082936,
                "volatility": 0.137877,
                "max_drawdown": -0.433049,
                "cagr_per_vol": 0.601524
              },
              {
                "weight": 1.0,
                "cagr": 0.079786,
                "volatility": 0.135028,
                "max_drawdown": -0.419578,
                "cagr_per_vol": 0.590888
              }
            ],
            "best_weight": 0.0,
            "margin": -0.002745,
            "outcome": "fails",
            "best_weight_risk_adjusted": 0.5,
            "outcome_risk_adjusted": "holds",
            "legs": [
              {
                "key": {
                  "kind": "assumed_yield",
                  "dividend_yield": 0.014
                },
                "best_weight": 0.0,
                "margin": -0.002191,
                "outcome": "fails"
              },
              {
                "key": {
                  "kind": "assumed_yield",
                  "dividend_yield": 0.019
                },
                "best_weight": 0.0,
                "margin": -0.002745,
                "outcome": "fails"
              },
              {
                "key": {
                  "kind": "assumed_yield",
                  "dividend_yield": 0.024
                },
                "best_weight": 0.0,
                "margin": -0.003302,
                "outcome": "fails"
              }
            ],
            "sizing": {
              "recommended_ratio": null,
              "reason": "sizing needs measured dividends",
              "at_cap": false,
              "w_star_grid": 0.0,
              "w_star": 0.0,
              "g0": 0.109348,
              "g_star": 0.109348,
              "g_half": 0.109348,
              "half_keeps": null,
              "break_even": null,
              "break_even_status": "none",
              "naive_kelly": -5.95102,
              "margin_by_leg": {
                "assumed": 0.0
              },
              "grid_note": null,
              "halves": [
                {
                  "start": "1986-06-30",
                  "end": "2006-07-03",
                  "w_star": 0.0
                },
                {
                  "start": "2006-08-01",
                  "end": "2026-09-01",
                  "w_star": 0.0
                }
              ],
              "decisive_months": [],
              "curve": [
                {
                  "weight": 0.0,
                  "cagr": 0.109348
                },
                {
                  "weight": 0.025,
                  "cagr": 0.108666
                },
                {
                  "weight": 0.05,
                  "cagr": 0.107981
                },
                {
                  "weight": 0.075,
                  "cagr": 0.107293
                },
                {
                  "weight": 0.1,
                  "cagr": 0.106602
                },
                {
                  "weight": 0.125,
                  "cagr": 0.105908
                },
                {
                  "weight": 0.15,
                  "cagr": 0.10521
                },
                {
                  "weight": 0.175,
                  "cagr": 0.10451
                },
                {
                  "weight": 0.2,
                  "cagr": 0.103806
                },
                {
                  "weight": 0.225,
                  "cagr": 0.103099
                },
                {
                  "weight": 0.25,
                  "cagr": 0.102389
                },
                {
                  "weight": 0.275,
                  "cagr": 0.101676
                },
                {
                  "weight": 0.3,
                  "cagr": 0.10096
                },
                {
                  "weight": 0.325,
                  "cagr": 0.100242
                },
                {
                  "weight": 0.35,
                  "cagr": 0.09952
                },
                {
                  "weight": 0.375,
                  "cagr": 0.098795
                },
                {
                  "weight": 0.4,
                  "cagr": 0.098067
                },
                {
                  "weight": 0.425,
                  "cagr": 0.097337
                },
                {
                  "weight": 0.45,
                  "cagr": 0.096603
                },
                {
                  "weight": 0.475,
                  "cagr": 0.095867
                },
                {
                  "weight": 0.5,
                  "cagr": 0.095128
                },
                {
                  "weight": 0.525,
                  "cagr": 0.094386
                },
                {
                  "weight": 0.55,
                  "cagr": 0.093641
                },
                {
                  "weight": 0.575,
                  "cagr": 0.092894
                },
                {
                  "weight": 0.6,
                  "cagr": 0.092144
                },
                {
                  "weight": 0.625,
                  "cagr": 0.091391
                },
                {
                  "weight": 0.65,
                  "cagr": 0.090635
                },
                {
                  "weight": 0.675,
                  "cagr": 0.089877
                },
                {
                  "weight": 0.7,
                  "cagr": 0.089116
                },
                {
                  "weight": 0.725,
                  "cagr": 0.088352
                },
                {
                  "weight": 0.75,
                  "cagr": 0.087586
                },
                {
                  "weight": 0.775,
                  "cagr": 0.086818
                },
                {
                  "weight": 0.8,
                  "cagr": 0.086046
                },
                {
                  "weight": 0.825,
                  "cagr": 0.085273
                },
                {
                  "weight": 0.85,
                  "cagr": 0.084496
                },
                {
                  "weight": 0.875,
                  "cagr": 0.083717
                },
                {
                  "weight": 0.9,
                  "cagr": 0.082936
                },
                {
                  "weight": 0.925,
                  "cagr": 0.082152
                },
                {
                  "weight": 0.95,
                  "cagr": 0.081366
                },
                {
                  "weight": 0.975,
                  "cagr": 0.080577
                },
                {
                  "weight": 1.0,
                  "cagr": 0.079786
                }
              ]
            }
          }
        ],
        "unavailable": {}
      },
      {
        "index_symbol": "PPUT3M",
        "description": "Cboe S&P 500 Tail Risk Index (from 2004-03-19)",
        "first_date": "2004-03-19",
        "windows": [
          {
            "key": "cole",
            "label": "The letter's window (2005 to Mar 2016)",
            "requested_start": "2005-01-03",
            "requested_end": "2016-03-31",
            "start": "2005-01-03",
            "end": "2016-03-31",
            "clipped": false,
            "years": 11.238877,
            "points": [
              {
                "weight": 0.0,
                "cagr": 0.069016,
                "volatility": 0.199963,
                "max_drawdown": -0.556037,
                "cagr_per_vol": 0.345143
              },
              {
                "weight": 0.1,
                "cagr": 0.066692,
                "volatility": 0.190673,
                "max_drawdown": -0.540089,
                "cagr_per_vol": 0.349774
              },
              {
                "weight": 0.2,
                "cagr": 0.064285,
                "volatility": 0.181844,
                "max_drawdown": -0.523892,
                "cagr_per_vol": 0.353515
              },
              {
                "weight": 0.3,
                "cagr": 0.061794,
                "volatility": 0.173494,
                "max_drawdown": -0.507454,
                "cagr_per_vol": 0.356174
              },
              {
                "weight": 0.4,
                "cagr": 0.059222,
                "volatility": 0.165644,
                "max_drawdown": -0.490781,
                "cagr_per_vol": 0.357526
              },
              {
                "weight": 0.5,
                "cagr": 0.05657,
                "volatility": 0.15832,
                "max_drawdown": -0.473883,
                "cagr_per_vol": 0.357313
              },
              {
                "weight": 0.6,
                "cagr": 0.053838,
                "volatility": 0.151554,
                "max_drawdown": -0.456768,
                "cagr_per_vol": 0.355243
              },
              {
                "weight": 0.7,
                "cagr": 0.051029,
                "volatility": 0.145381,
                "max_drawdown": -0.439444,
                "cagr_per_vol": 0.351002
              },
              {
                "weight": 0.8,
                "cagr": 0.048143,
                "volatility": 0.139841,
                "max_drawdown": -0.421922,
                "cagr_per_vol": 0.34427
              },
              {
                "weight": 0.9,
                "cagr": 0.045181,
                "volatility": 0.134973,
                "max_drawdown": -0.407118,
                "cagr_per_vol": 0.334739
              },
              {
                "weight": 1.0,
                "cagr": 0.042143,
                "volatility": 0.130819,
                "max_drawdown": -0.398631,
                "cagr_per_vol": 0.322151
              }
            ],
            "best_weight": 0.0,
            "margin": -0.002324,
            "outcome": "fails",
            "best_weight_risk_adjusted": 0.4,
            "outcome_risk_adjusted": "holds",
            "legs": [
              {
                "key": {
                  "kind": "assumed_yield",
                  "dividend_yield": 0.014
                },
                "best_weight": 0.0,
                "margin": -0.001791,
                "outcome": "fails"
              },
              {
                "key": {
                  "kind": "assumed_yield",
                  "dividend_yield": 0.019
                },
                "best_weight": 0.0,
                "margin": -0.002324,
                "outcome": "fails"
              },
              {
                "key": {
                  "kind": "assumed_yield",
                  "dividend_yield": 0.024
                },
                "best_weight": 0.0,
                "margin": -0.002859,
                "outcome": "fails"
              }
            ],
            "sizing": {
              "recommended_ratio": null,
              "reason": "sizing needs measured dividends",
              "at_cap": false,
              "w_star_grid": 0.0,
              "w_star": 0.0,
              "g0": 0.069016,
              "g_star": 0.069016,
              "g_half": 0.069016,
              "half_keeps": null,
              "break_even": null,
              "break_even_status": "none",
              "naive_kelly": -2.775179,
              "margin_by_leg": {
                "assumed": 0.0
              },
              "grid_note": null,
              "halves": [
                {
                  "start": "2005-01-03",
                  "end": "2010-07-01",
                  "w_star": 0.205065
                },
                {
                  "start": "2010-08-02",
                  "end": "2016-03-01",
                  "w_star": 0.0
                }
              ],
              "decisive_months": [],
              "curve": [
                {
                  "weight": 0.0,
                  "cagr": 0.069016
                },
                {
                  "weight": 0.025,
                  "cagr": 0.068443
                },
                {
                  "weight": 0.05,
                  "cagr": 0.067865
                },
                {
                  "weight": 0.075,
                  "cagr": 0.067281
                },
                {
                  "weight": 0.1,
                  "cagr": 0.066692
                },
                {
                  "weight": 0.125,
                  "cagr": 0.066098
                },
                {
                  "weight": 0.15,
                  "cagr": 0.065499
                },
                {
                  "weight": 0.175,
                  "cagr": 0.064894
                },
                {
                  "weight": 0.2,
                  "cagr": 0.064285
                },
                {
                  "weight": 0.225,
                  "cagr": 0.06367
                },
                {
                  "weight": 0.25,
                  "cagr": 0.06305
                },
                {
                  "weight": 0.275,
                  "cagr": 0.062424
                },
                {
                  "weight": 0.3,
                  "cagr": 0.061794
                },
                {
                  "weight": 0.325,
                  "cagr": 0.061159
                },
                {
                  "weight": 0.35,
                  "cagr": 0.060518
                },
                {
                  "weight": 0.375,
                  "cagr": 0.059873
                },
                {
                  "weight": 0.4,
                  "cagr": 0.059222
                },
                {
                  "weight": 0.425,
                  "cagr": 0.058566
                },
                {
                  "weight": 0.45,
                  "cagr": 0.057906
                },
                {
                  "weight": 0.475,
                  "cagr": 0.05724
                },
                {
                  "weight": 0.5,
                  "cagr": 0.05657
                },
                {
                  "weight": 0.525,
                  "cagr": 0.055894
                },
                {
                  "weight": 0.55,
                  "cagr": 0.055214
                },
                {
                  "weight": 0.575,
                  "cagr": 0.054529
                },
                {
                  "weight": 0.6,
                  "cagr": 0.053838
                },
                {
                  "weight": 0.625,
                  "cagr": 0.053143
                },
                {
                  "weight": 0.65,
                  "cagr": 0.052443
                },
                {
                  "weight": 0.675,
                  "cagr": 0.051739
                },
                {
                  "weight": 0.7,
                  "cagr": 0.051029
                },
                {
                  "weight": 0.725,
                  "cagr": 0.050315
                },
                {
                  "weight": 0.75,
                  "cagr": 0.049596
                },
                {
                  "weight": 0.775,
                  "cagr": 0.048872
                },
                {
                  "weight": 0.8,
                  "cagr": 0.048143
                },
                {
                  "weight": 0.825,
                  "cagr": 0.047409
                },
                {
                  "weight": 0.85,
                  "cagr": 0.046671
                },
                {
                  "weight": 0.875,
                  "cagr": 0.045928
                },
                {
                  "weight": 0.9,
                  "cagr": 0.045181
                },
                {
                  "weight": 0.925,
                  "cagr": 0.044428
                },
                {
                  "weight": 0.95,
                  "cagr": 0.043671
                },
                {
                  "weight": 0.975,
                  "cagr": 0.04291
                },
                {
                  "weight": 1.0,
                  "cagr": 0.042143
                }
              ]
            }
          },
          {
            "key": "full",
            "label": "Full history",
            "requested_start": "2004-03-19",
            "requested_end": "2026-10-02",
            "start": "2004-03-19",
            "end": "2026-10-01",
            "clipped": false,
            "years": 22.53525,
            "points": [
              {
                "weight": 0.0,
                "cagr": 0.110246,
                "volatility": 0.187487,
                "max_drawdown": -0.556037,
                "cagr_per_vol": 0.588019
              },
              {
                "weight": 0.1,
                "cagr": 0.108086,
                "volatility": 0.180299,
                "max_drawdown": -0.540089,
                "cagr_per_vol": 0.599484
              },
              {
                "weight": 0.2,
                "cagr": 0.105866,
                "volatility": 0.173526,
                "max_drawdown": -0.523892,
                "cagr_per_vol": 0.610087
              },
              {
                "weight": 0.3,
                "cagr": 0.103586,
                "volatility": 0.167181,
                "max_drawdown": -0.507454,
                "cagr_per_vol": 0.619604
              },
              {
                "weight": 0.4,
                "cagr": 0.101247,
                "volatility": 0.16128,
                "max_drawdown": -0.490781,
                "cagr_per_vol": 0.627775
              },
              {
                "weight": 0.5,
                "cagr": 0.098851,
                "volatility": 0.15584,
                "max_drawdown": -0.473883,
                "cagr_per_vol": 0.634314
              },
              {
                "weight": 0.6,
                "cagr": 0.096399,
                "volatility": 0.150881,
                "max_drawdown": -0.456768,
                "cagr_per_vol": 0.638907
              },
              {
                "weight": 0.7,
                "cagr": 0.093891,
                "volatility": 0.146423,
                "max_drawdown": -0.439444,
                "cagr_per_vol": 0.641231
              },
              {
                "weight": 0.8,
                "cagr": 0.091328,
                "volatility": 0.142486,
                "max_drawdown": -0.421922,
                "cagr_per_vol": 0.640963
              },
              {
                "weight": 0.9,
                "cagr": 0.088712,
                "volatility": 0.139089,
                "max_drawdown": -0.407118,
                "cagr_per_vol": 0.637806
              },
              {
                "weight": 1.0,
                "cagr": 0.086043,
                "volatility": 0.13625,
                "max_drawdown": -0.398631,
                "cagr_per_vol": 0.631507
              }
            ],
            "best_weight": 0.0,
            "margin": -0.00216,
            "outcome": "fails",
            "best_weight_risk_adjusted": 0.7,
            "outcome_risk_adjusted": "holds",
            "legs": [
              {
                "key": {
                  "kind": "assumed_yield",
                  "dividend_yield": 0.014
                },
                "best_weight": 0.0,
                "margin": -0.001607,
                "outcome": "fails"
              },
              {
                "key": {
                  "kind": "assumed_yield",
                  "dividend_yield": 0.019
                },
                "best_weight": 0.0,
                "margin": -0.00216,
                "outcome": "fails"
              },
              {
                "key": {
                  "kind": "assumed_yield",
                  "dividend_yield": 0.024
                },
                "best_weight": 0.0,
                "margin": -0.002714,
                "outcome": "fails"
              }
            ],
            "sizing": {
              "recommended_ratio": null,
              "reason": "sizing needs measured dividends",
              "at_cap": false,
              "w_star_grid": 0.0,
              "w_star": 0.0,
              "g0": 0.110246,
              "g_star": 0.110246,
              "g_half": 0.110246,
              "half_keeps": null,
              "break_even": null,
              "break_even_status": "none",
              "naive_kelly": -3.793521,
              "margin_by_leg": {
                "assumed": 0.0
              },
              "grid_note": null,
              "halves": [
                {
                  "start": "2004-03-19",
                  "end": "2015-05-01",
                  "w_star": 0.0
                },
                {
                  "start": "2015-06-01",
                  "end": "2026-09-01",
                  "w_star": 0.0
                }
              ],
              "decisive_months": [],
              "curve": [
                {
                  "weight": 0.0,
                  "cagr": 0.110246
                },
                {
                  "weight": 0.025,
                  "cagr": 0.109712
                },
                {
                  "weight": 0.05,
                  "cagr": 0.109174
                },
                {
                  "weight": 0.075,
                  "cagr": 0.108632
                },
                {
                  "weight": 0.1,
                  "cagr": 0.108086
                },
                {
                  "weight": 0.125,
                  "cagr": 0.107537
                },
                {
                  "weight": 0.15,
                  "cagr": 0.106984
                },
                {
                  "weight": 0.175,
                  "cagr": 0.106427
                },
                {
                  "weight": 0.2,
                  "cagr": 0.105866
                },
                {
                  "weight": 0.225,
                  "cagr": 0.105302
                },
                {
                  "weight": 0.25,
                  "cagr": 0.104733
                },
                {
                  "weight": 0.275,
                  "cagr": 0.104161
                },
                {
                  "weight": 0.3,
                  "cagr": 0.103586
                },
                {
                  "weight": 0.325,
                  "cagr": 0.103007
                },
                {
                  "weight": 0.35,
                  "cagr": 0.102424
                },
                {
                  "weight": 0.375,
                  "cagr": 0.101837
                },
                {
                  "weight": 0.4,
                  "cagr": 0.101247
                },
                {
                  "weight": 0.425,
                  "cagr": 0.100654
                },
                {
                  "weight": 0.45,
                  "cagr": 0.100057
                },
                {
                  "weight": 0.475,
                  "cagr": 0.099456
                },
                {
                  "weight": 0.5,
                  "cagr": 0.098851
                },
                {
                  "weight": 0.525,
                  "cagr": 0.098244
                },
                {
                  "weight": 0.55,
                  "cagr": 0.097632
                },
                {
                  "weight": 0.575,
                  "cagr": 0.097017
                },
                {
                  "weight": 0.6,
                  "cagr": 0.096399
                },
                {
                  "weight": 0.625,
                  "cagr": 0.095777
                },
                {
                  "weight": 0.65,
                  "cagr": 0.095152
                },
                {
                  "weight": 0.675,
                  "cagr": 0.094523
                },
                {
                  "weight": 0.7,
                  "cagr": 0.093891
                },
                {
                  "weight": 0.725,
                  "cagr": 0.093255
                },
                {
                  "weight": 0.75,
                  "cagr": 0.092616
                },
                {
                  "weight": 0.775,
                  "cagr": 0.091974
                },
                {
                  "weight": 0.8,
                  "cagr": 0.091328
                },
                {
                  "weight": 0.825,
                  "cagr": 0.090679
                },
                {
                  "weight": 0.85,
                  "cagr": 0.090027
                },
                {
                  "weight": 0.875,
                  "cagr": 0.089371
                },
                {
                  "weight": 0.9,
                  "cagr": 0.088712
                },
                {
                  "weight": 0.925,
                  "cagr": 0.08805
                },
                {
                  "weight": 0.95,
                  "cagr": 0.087384
                },
                {
                  "weight": 0.975,
                  "cagr": 0.086715
                },
                {
                  "weight": 1.0,
                  "cagr": 0.086043
                }
              ]
            }
          }
        ],
        "unavailable": {}
      },
      {
        "index_symbol": "VXTH",
        "description": "Cboe VIX Tail Hedge Index (from 2006-03-31)",
        "first_date": "2006-03-31",
        "windows": [
          {
            "key": "cole",
            "label": "The letter's window (2005 to Mar 2016)",
            "requested_start": "2005-01-03",
            "requested_end": "2016-03-31",
            "start": "2006-03-31",
            "end": "2016-03-31",
            "clipped": true,
            "years": 10.001369,
            "points": [
              {
                "weight": 0.0,
                "cagr": 0.067412,
                "volatility": 0.209035,
                "max_drawdown": -0.556037,
                "cagr_per_vol": 0.322491
              },
              {
                "weight": 0.1,
                "cagr": 0.067943,
                "volatility": 0.203698,
                "max_drawdown": -0.54366,
                "cagr_per_vol": 0.33355
              },
              {
                "weight": 0.2,
                "cagr": 0.068326,
                "volatility": 0.198887,
                "max_drawdown": -0.531286,
                "cagr_per_vol": 0.343541
              },
              {
                "weight": 0.3,
                "cagr": 0.068563,
                "volatility": 0.194603,
                "max_drawdown": -0.518917,
                "cagr_per_vol": 0.352325
              },
              {
                "weight": 0.4,
                "cagr": 0.068661,
                "volatility": 0.190847,
                "max_drawdown": -0.506555,
                "cagr_per_vol": 0.359768
              },
              {
                "weight": 0.5,
                "cagr": 0.068622,
                "volatility": 0.187619,
                "max_drawdown": -0.494203,
                "cagr_per_vol": 0.365751
              },
              {
                "weight": 0.6,
                "cagr": 0.068451,
                "volatility": 0.184917,
                "max_drawdown": -0.481861,
                "cagr_per_vol": 0.37017
              },
              {
                "weight": 0.7,
                "cagr": 0.068151,
                "volatility": 0.182738,
                "max_drawdown": -0.469533,
                "cagr_per_vol": 0.372947
              },
              {
                "weight": 0.8,
                "cagr": 0.067727,
                "volatility": 0.181074,
                "max_drawdown": -0.457219,
                "cagr_per_vol": 0.37403
              },
              {
                "weight": 0.9,
                "cagr": 0.067181,
                "volatility": 0.179916,
                "max_drawdown": -0.444923,
                "cagr_per_vol": 0.3734
              },
              {
                "weight": 1.0,
                "cagr": 0.066516,
                "volatility": 0.179253,
                "max_drawdown": -0.433004,
                "cagr_per_vol": 0.371073
              }
            ],
            "best_weight": 0.4,
            "margin": 0.001249,
            "outcome": "holds",
            "best_weight_risk_adjusted": 0.8,
            "outcome_risk_adjusted": "holds",
            "legs": [
              {
                "key": {
                  "kind": "assumed_yield",
                  "dividend_yield": 0.014
                },
                "best_weight": 0.8,
                "margin": 0.000161,
                "outcome": "holds"
              },
              {
                "key": {
                  "kind": "assumed_yield",
                  "dividend_yield": 0.019
                },
                "best_weight": 0.4,
                "margin": 0.001249,
                "outcome": "holds"
              },
              {
                "key": {
                  "kind": "assumed_yield",
                  "dividend_yield": 0.024
                },
                "best_weight": 0.1,
                "margin": 9e-06,
                "outcome": "inconclusive"
              }
            ],
            "sizing": {
              "recommended_ratio": null,
              "reason": "sizing needs measured dividends",
              "at_cap": false,
              "w_star_grid": 0.4,
              "w_star": 0.421108,
              "g0": 0.067412,
              "g_star": 0.068664,
              "g_half": 0.068357,
              "half_keeps": 0.755375,
              "break_even": 0.860303,
              "break_even_status": "found",
              "naive_kelly": 0.412111,
              "margin_by_leg": {
                "assumed": 0.001252
              },
              "grid_note": null,
              "halves": [
                {
                  "start": "2006-03-31",
                  "end": "2011-02-01",
                  "w_star": 1.0
                },
                {
                  "start": "2011-03-01",
                  "end": "2016-03-01",
                  "w_star": 0.0
                }
              ],
              "decisive_months": [
                {
                  "month": "2015-09",
                  "w_star_without": 1.0,
                  "delta": 0.578892
                },
                {
                  "month": "2008-09",
                  "w_star_without": 0.0,
                  "delta": -0.421108
                },
                {
                  "month": "2008-10",
                  "w_star_without": 0.0,
                  "delta": -0.421108
                }
              ],
              "curve": [
                {
                  "weight": 0.0,
                  "cagr": 0.067412
                },
                {
                  "weight": 0.025,
                  "cagr": 0.067559
                },
                {
                  "weight": 0.05,
                  "cagr": 0.067697
                },
                {
                  "weight": 0.075,
                  "cagr": 0.067825
                },
                {
                  "weight": 0.1,
                  "cagr": 0.067943
                },
                {
                  "weight": 0.125,
                  "cagr": 0.068053
                },
                {
                  "weight": 0.15,
                  "cagr": 0.068153
                },
                {
                  "weight": 0.175,
                  "cagr": 0.068244
                },
                {
                  "weight": 0.2,
                  "cagr": 0.068326
                },
                {
                  "weight": 0.225,
                  "cagr": 0.068398
                },
                {
                  "weight": 0.25,
                  "cagr": 0.068462
                },
                {
                  "weight": 0.275,
                  "cagr": 0.068517
                },
                {
                  "weight": 0.3,
                  "cagr": 0.068563
                },
                {
                  "weight": 0.325,
                  "cagr": 0.068601
                },
                {
                  "weight": 0.35,
                  "cagr": 0.068629
                },
                {
                  "weight": 0.375,
                  "cagr": 0.068649
                },
                {
                  "weight": 0.4,
                  "cagr": 0.068661
                },
                {
                  "weight": 0.425,
                  "cagr": 0.068664
                },
                {
                  "weight": 0.45,
                  "cagr": 0.068658
                },
                {
                  "weight": 0.475,
                  "cagr": 0.068644
                },
                {
                  "weight": 0.5,
                  "cagr": 0.068622
                },
                {
                  "weight": 0.525,
                  "cagr": 0.068591
                },
                {
                  "weight": 0.55,
                  "cagr": 0.068553
                },
                {
                  "weight": 0.575,
                  "cagr": 0.068506
                },
                {
                  "weight": 0.6,
                  "cagr": 0.068451
                },
                {
                  "weight": 0.625,
                  "cagr": 0.068388
                },
                {
                  "weight": 0.65,
                  "cagr": 0.068317
                },
                {
                  "weight": 0.675,
                  "cagr": 0.068238
                },
                {
                  "weight": 0.7,
                  "cagr": 0.068151
                },
                {
                  "weight": 0.725,
                  "cagr": 0.068057
                },
                {
                  "weight": 0.75,
                  "cagr": 0.067955
                },
                {
                  "weight": 0.775,
                  "cagr": 0.067845
                },
                {
                  "weight": 0.8,
                  "cagr": 0.067727
                },
                {
                  "weight": 0.825,
                  "cagr": 0.067602
                },
                {
                  "weight": 0.85,
                  "cagr": 0.067469
                },
                {
                  "weight": 0.875,
                  "cagr": 0.067328
                },
                {
                  "weight": 0.9,
                  "cagr": 0.067181
                },
                {
                  "weight": 0.925,
                  "cagr": 0.067026
                },
                {
                  "weight": 0.95,
                  "cagr": 0.066863
                },
                {
                  "weight": 0.975,
                  "cagr": 0.066693
                },
                {
                  "weight": 1.0,
                  "cagr": 0.066516
                }
              ]
            }
          },
          {
            "key": "full",
            "label": "Full history",
            "requested_start": "2006-03-31",
            "requested_end": "2026-10-02",
            "start": "2006-03-31",
            "end": "2026-10-01",
            "clipped": false,
            "years": 20.503765,
            "points": [
              {
                "weight": 0.0,
                "cagr": 0.11133,
                "volatility": 0.193781,
                "max_drawdown": -0.556037,
                "cagr_per_vol": 0.574517
              },
              {
                "weight": 0.1,
                "cagr": 0.111392,
                "volatility": 0.186704,
                "max_drawdown": -0.54366,
                "cagr_per_vol": 0.596624
              },
              {
                "weight": 0.2,
                "cagr": 0.111079,
                "volatility": 0.180715,
                "max_drawdown": -0.531286,
                "cagr_per_vol": 0.614665
              },
              {
                "weight": 0.3,
                "cagr": 0.110433,
                "volatility": 0.175741,
                "max_drawdown": -0.518917,
                "cagr_per_vol": 0.628383
              },
              {
                "weight": 0.4,
                "cagr": 0.109485,
                "volatility": 0.171717,
                "max_drawdown": -0.506555,
                "cagr_per_vol": 0.637591
              },
              {
                "weight": 0.5,
                "cagr": 0.108264,
                "volatility": 0.168583,
                "max_drawdown": -0.494203,
                "cagr_per_vol": 0.642196
              },
              {
                "weight": 0.6,
                "cagr": 0.106792,
                "volatility": 0.166287,
                "max_drawdown": -0.481861,
                "cagr_per_vol": 0.642218
              },
              {
                "weight": 0.7,
                "cagr": 0.105092,
                "volatility": 0.164774,
                "max_drawdown": -0.469533,
                "cagr_per_vol": 0.637794
              },
              {
                "weight": 0.8,
                "cagr": 0.10318,
                "volatility": 0.163992,
                "max_drawdown": -0.457219,
                "cagr_per_vol": 0.629175
              },
              {
                "weight": 0.9,
                "cagr": 0.101071,
                "volatility": 0.163889,
                "max_drawdown": -0.444923,
                "cagr_per_vol": 0.616707
              },
              {
                "weight": 1.0,
                "cagr": 0.098781,
                "volatility": 0.164412,
                "max_drawdown": -0.433004,
                "cagr_per_vol": 0.600812
              }
            ],
            "best_weight": 0.1,
            "margin": 6.1e-05,
            "outcome": "inconclusive",
            "best_weight_risk_adjusted": 0.6,
            "outcome_risk_adjusted": "holds",
            "legs": [
              {
                "key": {
                  "kind": "assumed_yield",
                  "dividend_yield": 0.014
                },
                "best_weight": 0.2,
                "margin": 0.000838,
                "outcome": "holds"
              },
              {
                "key": {
                  "kind": "assumed_yield",
                  "dividend_yield": 0.019
                },
                "best_weight": 0.1,
                "margin": 6.1e-05,
                "outcome": "inconclusive"
              },
              {
                "key": {
                  "kind": "assumed_yield",
                  "dividend_yield": 0.024
                },
                "best_weight": 0.0,
                "margin": -0.000485,
                "outcome": "fails"
              }
            ],
            "sizing": {
              "recommended_ratio": null,
              "reason": "sizing needs measured dividends",
              "at_cap": false,
              "w_star_grid": 0.1,
              "w_star": 0.065133,
              "g0": 0.11133,
              "g_star": 0.111415,
              "g_half": 0.111394,
              "half_keeps": 0.753336,
              "break_even": 0.132008,
              "break_even_status": "found",
              "naive_kelly": 0.01548,
              "margin_by_leg": {
                "assumed": 8.5e-05
              },
              "grid_note": null,
              "halves": [
                {
                  "start": "2006-03-31",
                  "end": "2016-05-02",
                  "w_star": 0.266945
                },
                {
                  "start": "2016-06-01",
                  "end": "2026-09-01",
                  "w_star": 0.018607
                }
              ],
              "decisive_months": [
                {
                  "month": "2015-09",
                  "w_star_without": 0.199609,
                  "delta": 0.134476
                },
                {
                  "month": "2021-12",
                  "w_star_without": 0.13516,
                  "delta": 0.070027
                },
                {
                  "month": "2008-09",
                  "w_star_without": 0.0,
                  "delta": -0.065133
                }
              ],
              "curve": [
                {
                  "weight": 0.0,
                  "cagr": 0.11133
                },
                {
                  "weight": 0.025,
                  "cagr": 0.111383
                },
                {
                  "weight": 0.05,
                  "cagr": 0.111411
                },
                {
                  "weight": 0.075,
                  "cagr": 0.111413
                },
                {
                  "weight": 0.1,
                  "cagr": 0.111392
                },
                {
                  "weight": 0.125,
                  "cagr": 0.111347
                },
                {
                  "weight": 0.15,
                  "cagr": 0.11128
                },
                {
                  "weight": 0.175,
                  "cagr": 0.11119
                },
                {
                  "weight": 0.2,
                  "cagr": 0.111079
                },
                {
                  "weight": 0.225,
                  "cagr": 0.110948
                },
                {
                  "weight": 0.25,
                  "cagr": 0.110796
                },
                {
                  "weight": 0.275,
                  "cagr": 0.110624
                },
                {
                  "weight": 0.3,
                  "cagr": 0.110433
                },
                {
                  "weight": 0.325,
                  "cagr": 0.110223
                },
                {
                  "weight": 0.35,
                  "cagr": 0.109995
                },
                {
                  "weight": 0.375,
                  "cagr": 0.109748
                },
                {
                  "weight": 0.4,
                  "cagr": 0.109485
                },
                {
                  "weight": 0.425,
                  "cagr": 0.109204
                },
                {
                  "weight": 0.45,
                  "cagr": 0.108907
                },
                {
                  "weight": 0.475,
                  "cagr": 0.108593
                },
                {
                  "weight": 0.5,
                  "cagr": 0.108264
                },
                {
                  "weight": 0.525,
                  "cagr": 0.107918
                },
                {
                  "weight": 0.55,
                  "cagr": 0.107558
                },
                {
                  "weight": 0.575,
                  "cagr": 0.107183
                },
                {
                  "weight": 0.6,
                  "cagr": 0.106792
                },
                {
                  "weight": 0.625,
                  "cagr": 0.106388
                },
                {
                  "weight": 0.65,
                  "cagr": 0.10597
                },
                {
                  "weight": 0.675,
                  "cagr": 0.105538
                },
                {
                  "weight": 0.7,
                  "cagr": 0.105092
                },
                {
                  "weight": 0.725,
                  "cagr": 0.104633
                },
                {
                  "weight": 0.75,
                  "cagr": 0.104161
                },
                {
                  "weight": 0.775,
                  "cagr": 0.103677
                },
                {
                  "weight": 0.8,
                  "cagr": 0.10318
                },
                {
                  "weight": 0.825,
                  "cagr": 0.102671
                },
                {
                  "weight": 0.85,
                  "cagr": 0.102149
                },
                {
                  "weight": 0.875,
                  "cagr": 0.101616
                },
                {
                  "weight": 0.9,
                  "cagr": 0.101071
                },
                {
                  "weight": 0.925,
                  "cagr": 0.100515
                },
                {
                  "weight": 0.95,
                  "cagr": 0.099948
                },
                {
                  "weight": 0.975,
                  "cagr": 0.09937
                },
                {
                  "weight": 1.0,
                  "cagr": 0.098781
                }
              ]
            }
          }
        ],
        "unavailable": {}
      }
    ],
    "missing": {}
  }
}

export const HEDGE_OVERLAY_MEASURED: HedgeOverlayResponse = {
  "cboe_snapshot": "cboe_strategy@2026-10-02",
  "tiingo_snapshot": "tiingo_eod@2026-10-02",
  "rates_snapshot": "rates@2026-10-02",
  "code_sha": "e2e0000",
  "overlay": {
    "as_of": "2026-10-02",
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
    "weights": [
      0.0,
      0.1,
      0.2,
      0.3,
      0.4,
      0.5,
      0.6,
      0.7,
      0.8,
      0.9,
      1.0
    ],
    "programs": [
      {
        "index_symbol": "PPUT",
        "description": "Cboe S&P 500 5% Put Protection Index (from 1986-06-30)",
        "first_date": "1986-06-30",
        "windows": [
          {
            "key": "cole",
            "label": "The letter's window (2005 to Mar 2016)",
            "requested_start": "2005-01-03",
            "requested_end": "2016-03-31",
            "start": "2005-01-03",
            "end": "2016-03-31",
            "clipped": false,
            "years": 11.238877,
            "points": [
              {
                "weight": 0.0,
                "cagr": 0.071366,
                "volatility": 0.199264,
                "max_drawdown": -0.551412,
                "cagr_per_vol": 0.358149
              },
              {
                "weight": 0.1,
                "cagr": 0.069082,
                "volatility": 0.191077,
                "max_drawdown": -0.538355,
                "cagr_per_vol": 0.361541
              },
              {
                "weight": 0.2,
                "cagr": 0.066726,
                "volatility": 0.183378,
                "max_drawdown": -0.525164,
                "cagr_per_vol": 0.363869
              },
              {
                "weight": 0.3,
                "cagr": 0.064298,
                "volatility": 0.176169,
                "max_drawdown": -0.511841,
                "cagr_per_vol": 0.364979
              },
              {
                "weight": 0.4,
                "cagr": 0.061801,
                "volatility": 0.169457,
                "max_drawdown": -0.49839,
                "cagr_per_vol": 0.364701
              },
              {
                "weight": 0.5,
                "cagr": 0.059237,
                "volatility": 0.163251,
                "max_drawdown": -0.484812,
                "cagr_per_vol": 0.362856
              },
              {
                "weight": 0.6,
                "cagr": 0.056607,
                "volatility": 0.157565,
                "max_drawdown": -0.471112,
                "cagr_per_vol": 0.359258
              },
              {
                "weight": 0.7,
                "cagr": 0.053912,
                "volatility": 0.152412,
                "max_drawdown": -0.457862,
                "cagr_per_vol": 0.353727
              },
              {
                "weight": 0.8,
                "cagr": 0.051155,
                "volatility": 0.147808,
                "max_drawdown": -0.44515,
                "cagr_per_vol": 0.346093
              },
              {
                "weight": 0.9,
                "cagr": 0.048337,
                "volatility": 0.143767,
                "max_drawdown": -0.432387,
                "cagr_per_vol": 0.336218
              },
              {
                "weight": 1.0,
                "cagr": 0.045459,
                "volatility": 0.140305,
                "max_drawdown": -0.419578,
                "cagr_per_vol": 0.324004
              }
            ],
            "best_weight": 0.0,
            "margin": -0.002284,
            "outcome": "fails",
            "best_weight_risk_adjusted": 0.3,
            "outcome_risk_adjusted": "holds",
            "legs": [
              {
                "key": {
                  "kind": "leg",
                  "leg": "base"
                },
                "best_weight": 0.0,
                "margin": -0.002284,
                "outcome": "fails"
              },
              {
                "key": {
                  "kind": "leg",
                  "leg": "conservative"
                },
                "best_weight": 0.0,
                "margin": -0.002293,
                "outcome": "fails"
              }
            ],
            "sizing": {
              "recommended_ratio": 0.0,
              "reason": "growth falls at every step of the hedge ratio: no hedge grows fastest",
              "at_cap": false,
              "w_star_grid": 0.0,
              "w_star": 0.0,
              "g0": 0.071366,
              "g_star": 0.071366,
              "g_half": 0.071366,
              "half_keeps": null,
              "break_even": null,
              "break_even_status": "none",
              "naive_kelly": -3.447386,
              "margin_by_leg": {
                "base": 0.0,
                "conservative": 0.0
              },
              "grid_note": null,
              "halves": [
                {
                  "start": "2005-01-03",
                  "end": "2010-07-01",
                  "w_star": 0.165739
                },
                {
                  "start": "2010-08-02",
                  "end": "2016-03-01",
                  "w_star": 0.0
                }
              ],
              "decisive_months": [],
              "curve": [
                {
                  "weight": 0.0,
                  "cagr": 0.071366
                },
                {
                  "weight": 0.025,
                  "cagr": 0.070802
                },
                {
                  "weight": 0.05,
                  "cagr": 0.070233
                },
                {
                  "weight": 0.075,
                  "cagr": 0.06966
                },
                {
                  "weight": 0.1,
                  "cagr": 0.069082
                },
                {
                  "weight": 0.125,
                  "cagr": 0.0685
                },
                {
                  "weight": 0.15,
                  "cagr": 0.067913
                },
                {
                  "weight": 0.175,
                  "cagr": 0.067321
                },
                {
                  "weight": 0.2,
                  "cagr": 0.066726
                },
                {
                  "weight": 0.225,
                  "cagr": 0.066125
                },
                {
                  "weight": 0.25,
                  "cagr": 0.06552
                },
                {
                  "weight": 0.275,
                  "cagr": 0.064911
                },
                {
                  "weight": 0.3,
                  "cagr": 0.064298
                },
                {
                  "weight": 0.325,
                  "cagr": 0.06368
                },
                {
                  "weight": 0.35,
                  "cagr": 0.063058
                },
                {
                  "weight": 0.375,
                  "cagr": 0.062432
                },
                {
                  "weight": 0.4,
                  "cagr": 0.061801
                },
                {
                  "weight": 0.425,
                  "cagr": 0.061166
                },
                {
                  "weight": 0.45,
                  "cagr": 0.060527
                },
                {
                  "weight": 0.475,
                  "cagr": 0.059884
                },
                {
                  "weight": 0.5,
                  "cagr": 0.059237
                },
                {
                  "weight": 0.525,
                  "cagr": 0.058585
                },
                {
                  "weight": 0.55,
                  "cagr": 0.05793
                },
                {
                  "weight": 0.575,
                  "cagr": 0.05727
                },
                {
                  "weight": 0.6,
                  "cagr": 0.056607
                },
                {
                  "weight": 0.625,
                  "cagr": 0.055939
                },
                {
                  "weight": 0.65,
                  "cagr": 0.055267
                },
                {
                  "weight": 0.675,
                  "cagr": 0.054592
                },
                {
                  "weight": 0.7,
                  "cagr": 0.053912
                },
                {
                  "weight": 0.725,
                  "cagr": 0.053229
                },
                {
                  "weight": 0.75,
                  "cagr": 0.052542
                },
                {
                  "weight": 0.775,
                  "cagr": 0.05185
                },
                {
                  "weight": 0.8,
                  "cagr": 0.051155
                },
                {
                  "weight": 0.825,
                  "cagr": 0.050456
                },
                {
                  "weight": 0.85,
                  "cagr": 0.049754
                },
                {
                  "weight": 0.875,
                  "cagr": 0.049047
                },
                {
                  "weight": 0.9,
                  "cagr": 0.048337
                },
                {
                  "weight": 0.925,
                  "cagr": 0.047623
                },
                {
                  "weight": 0.95,
                  "cagr": 0.046906
                },
                {
                  "weight": 0.975,
                  "cagr": 0.046184
                },
                {
                  "weight": 1.0,
                  "cagr": 0.045459
                }
              ]
            }
          },
          {
            "key": "full",
            "label": "Full history",
            "requested_start": "1993-01-29",
            "requested_end": "2026-10-02",
            "start": "1993-01-29",
            "end": "2026-10-01",
            "clipped": false,
            "years": 33.670089,
            "points": [
              {
                "weight": 0.0,
                "cagr": 0.109677,
                "volatility": 0.18526,
                "max_drawdown": -0.551412,
                "cagr_per_vol": 0.592016
              },
              {
                "weight": 0.1,
                "cagr": 0.106727,
                "volatility": 0.178541,
                "max_drawdown": -0.538355,
                "cagr_per_vol": 0.597773
              },
              {
                "weight": 0.2,
                "cagr": 0.103726,
                "volatility": 0.17218,
                "max_drawdown": -0.525164,
                "cagr_per_vol": 0.602425
              },
              {
                "weight": 0.3,
                "cagr": 0.100675,
                "volatility": 0.166185,
                "max_drawdown": -0.511841,
                "cagr_per_vol": 0.605799
              },
              {
                "weight": 0.4,
                "cagr": 0.097576,
                "volatility": 0.160565,
                "max_drawdown": -0.49839,
                "cagr_per_vol": 0.607704
              },
              {
                "weight": 0.5,
                "cagr": 0.09443,
                "volatility": 0.155331,
                "max_drawdown": -0.484812,
                "cagr_per_vol": 0.607932
              },
              {
                "weight": 0.6,
                "cagr": 0.09124,
                "volatility": 0.150496,
                "max_drawdown": -0.471112,
                "cagr_per_vol": 0.606265
              },
              {
                "weight": 0.7,
                "cagr": 0.088006,
                "volatility": 0.146074,
                "max_drawdown": -0.457862,
                "cagr_per_vol": 0.602478
              },
              {
                "weight": 0.8,
                "cagr": 0.084731,
                "volatility": 0.142081,
                "max_drawdown": -0.44515,
                "cagr_per_vol": 0.596353
              },
              {
                "weight": 0.9,
                "cagr": 0.081414,
                "volatility": 0.138532,
                "max_drawdown": -0.432387,
                "cagr_per_vol": 0.587691
              },
              {
                "weight": 1.0,
                "cagr": 0.078058,
                "volatility": 0.13544,
                "max_drawdown": -0.419578,
                "cagr_per_vol": 0.576326
              }
            ],
            "best_weight": 0.0,
            "margin": -0.00295,
            "outcome": "fails",
            "best_weight_risk_adjusted": 0.5,
            "outcome_risk_adjusted": "holds",
            "legs": [
              {
                "key": {
                  "kind": "leg",
                  "leg": "base"
                },
                "best_weight": 0.0,
                "margin": -0.00295,
                "outcome": "fails"
              },
              {
                "key": {
                  "kind": "leg",
                  "leg": "conservative"
                },
                "best_weight": 0.0,
                "margin": -0.002966,
                "outcome": "fails"
              }
            ],
            "sizing": {
              "recommended_ratio": 0.0,
              "reason": "growth falls at every step of the hedge ratio: no hedge grows fastest",
              "at_cap": false,
              "w_star_grid": 0.0,
              "w_star": 0.0,
              "g0": 0.109677,
              "g_star": 0.109677,
              "g_half": 0.109677,
              "half_keeps": null,
              "break_even": null,
              "break_even_status": "none",
              "naive_kelly": -6.087533,
              "margin_by_leg": {
                "base": 0.0,
                "conservative": 0.0
              },
              "grid_note": null,
              "halves": [
                {
                  "start": "1993-01-29",
                  "end": "2009-10-01",
                  "w_star": 0.0
                },
                {
                  "start": "2009-11-02",
                  "end": "2026-09-01",
                  "w_star": 0.0
                }
              ],
              "decisive_months": [],
              "curve": [
                {
                  "weight": 0.0,
                  "cagr": 0.109677
                },
                {
                  "weight": 0.025,
                  "cagr": 0.108944
                },
                {
                  "weight": 0.05,
                  "cagr": 0.108208
                },
                {
                  "weight": 0.075,
                  "cagr": 0.107469
                },
                {
                  "weight": 0.1,
                  "cagr": 0.106727
                },
                {
                  "weight": 0.125,
                  "cagr": 0.105981
                },
                {
                  "weight": 0.15,
                  "cagr": 0.105233
                },
                {
                  "weight": 0.175,
                  "cagr": 0.104481
                },
                {
                  "weight": 0.2,
                  "cagr": 0.103726
                },
                {
                  "weight": 0.225,
                  "cagr": 0.102968
                },
                {
                  "weight": 0.25,
                  "cagr": 0.102206
                },
                {
                  "weight": 0.275,
                  "cagr": 0.101442
                },
                {
                  "weight": 0.3,
                  "cagr": 0.100675
                },
                {
                  "weight": 0.325,
                  "cagr": 0.099904
                },
                {
                  "weight": 0.35,
                  "cagr": 0.099131
                },
                {
                  "weight": 0.375,
                  "cagr": 0.098355
                },
                {
                  "weight": 0.4,
                  "cagr": 0.097576
                },
                {
                  "weight": 0.425,
                  "cagr": 0.096794
                },
                {
                  "weight": 0.45,
                  "cagr": 0.096009
                },
                {
                  "weight": 0.475,
                  "cagr": 0.095221
                },
                {
                  "weight": 0.5,
                  "cagr": 0.09443
                },
                {
                  "weight": 0.525,
                  "cagr": 0.093637
                },
                {
                  "weight": 0.55,
                  "cagr": 0.092841
                },
                {
                  "weight": 0.575,
                  "cagr": 0.092042
                },
                {
                  "weight": 0.6,
                  "cagr": 0.09124
                },
                {
                  "weight": 0.625,
                  "cagr": 0.090436
                },
                {
                  "weight": 0.65,
                  "cagr": 0.089629
                },
                {
                  "weight": 0.675,
                  "cagr": 0.088819
                },
                {
                  "weight": 0.7,
                  "cagr": 0.088006
                },
                {
                  "weight": 0.725,
                  "cagr": 0.087191
                },
                {
                  "weight": 0.75,
                  "cagr": 0.086374
                },
                {
                  "weight": 0.775,
                  "cagr": 0.085553
                },
                {
                  "weight": 0.8,
                  "cagr": 0.084731
                },
                {
                  "weight": 0.825,
                  "cagr": 0.083905
                },
                {
                  "weight": 0.85,
                  "cagr": 0.083077
                },
                {
                  "weight": 0.875,
                  "cagr": 0.082247
                },
                {
                  "weight": 0.9,
                  "cagr": 0.081414
                },
                {
                  "weight": 0.925,
                  "cagr": 0.080579
                },
                {
                  "weight": 0.95,
                  "cagr": 0.079741
                },
                {
                  "weight": 0.975,
                  "cagr": 0.078901
                },
                {
                  "weight": 1.0,
                  "cagr": 0.078058
                }
              ]
            }
          }
        ],
        "unavailable": {}
      },
      {
        "index_symbol": "PPUT3M",
        "description": "Cboe S&P 500 Tail Risk Index (from 2004-03-19)",
        "first_date": "2004-03-19",
        "windows": [
          {
            "key": "cole",
            "label": "The letter's window (2005 to Mar 2016)",
            "requested_start": "2005-01-03",
            "requested_end": "2016-03-31",
            "start": "2005-01-03",
            "end": "2016-03-31",
            "clipped": false,
            "years": 11.238877,
            "points": [
              {
                "weight": 0.0,
                "cagr": 0.071366,
                "volatility": 0.199264,
                "max_drawdown": -0.551412,
                "cagr_per_vol": 0.358149
              },
              {
                "weight": 0.1,
                "cagr": 0.068791,
                "volatility": 0.189706,
                "max_drawdown": -0.535827,
                "cagr_per_vol": 0.362618
              },
              {
                "weight": 0.2,
                "cagr": 0.066135,
                "volatility": 0.180687,
                "max_drawdown": -0.520016,
                "cagr_per_vol": 0.366019
              },
              {
                "weight": 0.3,
                "cagr": 0.0634,
                "volatility": 0.172223,
                "max_drawdown": -0.503985,
                "cagr_per_vol": 0.368128
              },
              {
                "weight": 0.4,
                "cagr": 0.060587,
                "volatility": 0.164335,
                "max_drawdown": -0.487743,
                "cagr_per_vol": 0.368683
              },
              {
                "weight": 0.5,
                "cagr": 0.057698,
                "volatility": 0.157049,
                "max_drawdown": -0.471298,
                "cagr_per_vol": 0.367391
              },
              {
                "weight": 0.6,
                "cagr": 0.054734,
                "volatility": 0.150395,
                "max_drawdown": -0.454657,
                "cagr_per_vol": 0.363933
              },
              {
                "weight": 0.7,
                "cagr": 0.051695,
                "volatility": 0.144409,
                "max_drawdown": -0.437829,
                "cagr_per_vol": 0.357975
              },
              {
                "weight": 0.8,
                "cagr": 0.048583,
                "volatility": 0.139127,
                "max_drawdown": -0.420825,
                "cagr_per_vol": 0.349199
              },
              {
                "weight": 0.9,
                "cagr": 0.045399,
                "volatility": 0.134585,
                "max_drawdown": -0.40669,
                "cagr_per_vol": 0.337323
              },
              {
                "weight": 1.0,
                "cagr": 0.042143,
                "volatility": 0.130819,
                "max_drawdown": -0.398631,
                "cagr_per_vol": 0.322151
              }
            ],
            "best_weight": 0.0,
            "margin": -0.002575,
            "outcome": "fails",
            "best_weight_risk_adjusted": 0.4,
            "outcome_risk_adjusted": "holds",
            "legs": [
              {
                "key": {
                  "kind": "leg",
                  "leg": "base"
                },
                "best_weight": 0.0,
                "margin": -0.002575,
                "outcome": "fails"
              },
              {
                "key": {
                  "kind": "leg",
                  "leg": "conservative"
                },
                "best_weight": 0.0,
                "margin": -0.002584,
                "outcome": "fails"
              }
            ],
            "sizing": {
              "recommended_ratio": 0.0,
              "reason": "growth falls at every step of the hedge ratio: no hedge grows fastest",
              "at_cap": false,
              "w_star_grid": 0.0,
              "w_star": 0.0,
              "g0": 0.071366,
              "g_star": 0.071366,
              "g_half": 0.071366,
              "half_keeps": null,
              "break_even": null,
              "break_even_status": "none",
              "naive_kelly": -3.163471,
              "margin_by_leg": {
                "base": 0.0,
                "conservative": 0.0
              },
              "grid_note": null,
              "halves": [
                {
                  "start": "2005-01-03",
                  "end": "2010-07-01",
                  "w_star": 0.059683
                },
                {
                  "start": "2010-08-02",
                  "end": "2016-03-01",
                  "w_star": 0.0
                }
              ],
              "decisive_months": [],
              "curve": [
                {
                  "weight": 0.0,
                  "cagr": 0.071366
                },
                {
                  "weight": 0.025,
                  "cagr": 0.07073
                },
                {
                  "weight": 0.05,
                  "cagr": 0.070089
                },
                {
                  "weight": 0.075,
                  "cagr": 0.069442
                },
                {
                  "weight": 0.1,
                  "cagr": 0.068791
                },
                {
                  "weight": 0.125,
                  "cagr": 0.068134
                },
                {
                  "weight": 0.15,
                  "cagr": 0.067473
                },
                {
                  "weight": 0.175,
                  "cagr": 0.066806
                },
                {
                  "weight": 0.2,
                  "cagr": 0.066135
                },
                {
                  "weight": 0.225,
                  "cagr": 0.065459
                },
                {
                  "weight": 0.25,
                  "cagr": 0.064777
                },
                {
                  "weight": 0.275,
                  "cagr": 0.064091
                },
                {
                  "weight": 0.3,
                  "cagr": 0.0634
                },
                {
                  "weight": 0.325,
                  "cagr": 0.062704
                },
                {
                  "weight": 0.35,
                  "cagr": 0.062003
                },
                {
                  "weight": 0.375,
                  "cagr": 0.061298
                },
                {
                  "weight": 0.4,
                  "cagr": 0.060587
                },
                {
                  "weight": 0.425,
                  "cagr": 0.059872
                },
                {
                  "weight": 0.45,
                  "cagr": 0.059152
                },
                {
                  "weight": 0.475,
                  "cagr": 0.058428
                },
                {
                  "weight": 0.5,
                  "cagr": 0.057698
                },
                {
                  "weight": 0.525,
                  "cagr": 0.056964
                },
                {
                  "weight": 0.55,
                  "cagr": 0.056225
                },
                {
                  "weight": 0.575,
                  "cagr": 0.055482
                },
                {
                  "weight": 0.6,
                  "cagr": 0.054734
                },
                {
                  "weight": 0.625,
                  "cagr": 0.053981
                },
                {
                  "weight": 0.65,
                  "cagr": 0.053224
                },
                {
                  "weight": 0.675,
                  "cagr": 0.052462
                },
                {
                  "weight": 0.7,
                  "cagr": 0.051695
                },
                {
                  "weight": 0.725,
                  "cagr": 0.050924
                },
                {
                  "weight": 0.75,
                  "cagr": 0.050148
                },
                {
                  "weight": 0.775,
                  "cagr": 0.049368
                },
                {
                  "weight": 0.8,
                  "cagr": 0.048583
                },
                {
                  "weight": 0.825,
                  "cagr": 0.047794
                },
                {
                  "weight": 0.85,
                  "cagr": 0.047
                },
                {
                  "weight": 0.875,
                  "cagr": 0.046202
                },
                {
                  "weight": 0.9,
                  "cagr": 0.045399
                },
                {
                  "weight": 0.925,
                  "cagr": 0.044592
                },
                {
                  "weight": 0.95,
                  "cagr": 0.04378
                },
                {
                  "weight": 0.975,
                  "cagr": 0.042964
                },
                {
                  "weight": 1.0,
                  "cagr": 0.042143
                }
              ]
            }
          },
          {
            "key": "full",
            "label": "Full history",
            "requested_start": "2004-03-19",
            "requested_end": "2026-10-02",
            "start": "2004-03-19",
            "end": "2026-10-01",
            "clipped": false,
            "years": 22.53525,
            "points": [
              {
                "weight": 0.0,
                "cagr": 0.110574,
                "volatility": 0.186615,
                "max_drawdown": -0.551412,
                "cagr_per_vol": 0.592522
              },
              {
                "weight": 0.1,
                "cagr": 0.108374,
                "volatility": 0.179318,
                "max_drawdown": -0.535827,
                "cagr_per_vol": 0.604366
              },
              {
                "weight": 0.2,
                "cagr": 0.106115,
                "volatility": 0.172482,
                "max_drawdown": -0.520016,
                "cagr_per_vol": 0.615223
              },
              {
                "weight": 0.3,
                "cagr": 0.103798,
                "volatility": 0.166119,
                "max_drawdown": -0.503985,
                "cagr_per_vol": 0.624841
              },
              {
                "weight": 0.4,
                "cagr": 0.101424,
                "volatility": 0.160244,
                "max_drawdown": -0.487743,
                "cagr_per_vol": 0.632933
              },
              {
                "weight": 0.5,
                "cagr": 0.098995,
                "volatility": 0.154876,
                "max_drawdown": -0.471298,
                "cagr_per_vol": 0.639186
              },
              {
                "weight": 0.6,
                "cagr": 0.09651,
                "volatility": 0.150031,
                "max_drawdown": -0.454657,
                "cagr_per_vol": 0.643267
              },
              {
                "weight": 0.7,
                "cagr": 0.093972,
                "volatility": 0.145729,
                "max_drawdown": -0.437829,
                "cagr_per_vol": 0.644839
              },
              {
                "weight": 0.8,
                "cagr": 0.091381,
                "volatility": 0.141988,
                "max_drawdown": -0.420825,
                "cagr_per_vol": 0.643581
              },
              {
                "weight": 0.9,
                "cagr": 0.088737,
                "volatility": 0.138824,
                "max_drawdown": -0.40669,
                "cagr_per_vol": 0.639209
              },
              {
                "weight": 1.0,
                "cagr": 0.086043,
                "volatility": 0.13625,
                "max_drawdown": -0.398631,
                "cagr_per_vol": 0.631507
              }
            ],
            "best_weight": 0.0,
            "margin": -0.0022,
            "outcome": "fails",
            "best_weight_risk_adjusted": 0.7,
            "outcome_risk_adjusted": "holds",
            "legs": [
              {
                "key": {
                  "kind": "leg",
                  "leg": "base"
                },
                "best_weight": 0.0,
                "margin": -0.0022,
                "outcome": "fails"
              },
              {
                "key": {
                  "kind": "leg",
                  "leg": "conservative"
                },
                "best_weight": 0.0,
                "margin": -0.002216,
                "outcome": "fails"
              }
            ],
            "sizing": {
              "recommended_ratio": 0.0,
              "reason": "growth falls at every step of the hedge ratio: no hedge grows fastest",
              "at_cap": false,
              "w_star_grid": 0.0,
              "w_star": 0.0,
              "g0": 0.110574,
              "g_star": 0.110574,
              "g_half": 0.110574,
              "half_keeps": null,
              "break_even": null,
              "break_even_status": "none",
              "naive_kelly": -3.953262,
              "margin_by_leg": {
                "base": 0.0,
                "conservative": 0.0
              },
              "grid_note": null,
              "halves": [
                {
                  "start": "2004-03-19",
                  "end": "2015-05-01",
                  "w_star": 0.0
                },
                {
                  "start": "2015-06-01",
                  "end": "2026-09-01",
                  "w_star": 0.0
                }
              ],
              "decisive_months": [],
              "curve": [
                {
                  "weight": 0.0,
                  "cagr": 0.110574
                },
                {
                  "weight": 0.025,
                  "cagr": 0.110029
                },
                {
                  "weight": 0.05,
                  "cagr": 0.109481
                },
                {
                  "weight": 0.075,
                  "cagr": 0.108929
                },
                {
                  "weight": 0.1,
                  "cagr": 0.108374
                },
                {
                  "weight": 0.125,
                  "cagr": 0.107814
                },
                {
                  "weight": 0.15,
                  "cagr": 0.107251
                },
                {
                  "weight": 0.175,
                  "cagr": 0.106685
                },
                {
                  "weight": 0.2,
                  "cagr": 0.106115
                },
                {
                  "weight": 0.225,
                  "cagr": 0.105541
                },
                {
                  "weight": 0.25,
                  "cagr": 0.104963
                },
                {
                  "weight": 0.275,
                  "cagr": 0.104382
                },
                {
                  "weight": 0.3,
                  "cagr": 0.103798
                },
                {
                  "weight": 0.325,
                  "cagr": 0.10321
                },
                {
                  "weight": 0.35,
                  "cagr": 0.102618
                },
                {
                  "weight": 0.375,
                  "cagr": 0.102023
                },
                {
                  "weight": 0.4,
                  "cagr": 0.101424
                },
                {
                  "weight": 0.425,
                  "cagr": 0.100822
                },
                {
                  "weight": 0.45,
                  "cagr": 0.100216
                },
                {
                  "weight": 0.475,
                  "cagr": 0.099607
                },
                {
                  "weight": 0.5,
                  "cagr": 0.098995
                },
                {
                  "weight": 0.525,
                  "cagr": 0.098379
                },
                {
                  "weight": 0.55,
                  "cagr": 0.097759
                },
                {
                  "weight": 0.575,
                  "cagr": 0.097136
                },
                {
                  "weight": 0.6,
                  "cagr": 0.09651
                },
                {
                  "weight": 0.625,
                  "cagr": 0.095881
                },
                {
                  "weight": 0.65,
                  "cagr": 0.095248
                },
                {
                  "weight": 0.675,
                  "cagr": 0.094612
                },
                {
                  "weight": 0.7,
                  "cagr": 0.093972
                },
                {
                  "weight": 0.725,
                  "cagr": 0.093329
                },
                {
                  "weight": 0.75,
                  "cagr": 0.092683
                },
                {
                  "weight": 0.775,
                  "cagr": 0.092034
                },
                {
                  "weight": 0.8,
                  "cagr": 0.091381
                },
                {
                  "weight": 0.825,
                  "cagr": 0.090725
                },
                {
                  "weight": 0.85,
                  "cagr": 0.090066
                },
                {
                  "weight": 0.875,
                  "cagr": 0.089403
                },
                {
                  "weight": 0.9,
                  "cagr": 0.088737
                },
                {
                  "weight": 0.925,
                  "cagr": 0.088069
                },
                {
                  "weight": 0.95,
                  "cagr": 0.087396
                },
                {
                  "weight": 0.975,
                  "cagr": 0.086721
                },
                {
                  "weight": 1.0,
                  "cagr": 0.086043
                }
              ]
            }
          }
        ],
        "unavailable": {}
      },
      {
        "index_symbol": "VXTH",
        "description": "Cboe VIX Tail Hedge Index (from 2006-03-31)",
        "first_date": "2006-03-31",
        "windows": [
          {
            "key": "cole",
            "label": "The letter's window (2005 to Mar 2016)",
            "requested_start": "2005-01-03",
            "requested_end": "2016-03-31",
            "start": "2006-03-31",
            "end": "2016-03-31",
            "clipped": true,
            "years": 10.001369,
            "points": [
              {
                "weight": 0.0,
                "cagr": 0.069832,
                "volatility": 0.208234,
                "max_drawdown": -0.551412,
                "cagr_per_vol": 0.335353
              },
              {
                "weight": 0.1,
                "cagr": 0.070109,
                "volatility": 0.202558,
                "max_drawdown": -0.539429,
                "cagr_per_vol": 0.346117
              },
              {
                "weight": 0.2,
                "cagr": 0.07024,
                "volatility": 0.197509,
                "max_drawdown": -0.527466,
                "cagr_per_vol": 0.355626
              },
              {
                "weight": 0.3,
                "cagr": 0.070229,
                "volatility": 0.193087,
                "max_drawdown": -0.515523,
                "cagr_per_vol": 0.363714
              },
              {
                "weight": 0.4,
                "cagr": 0.07008,
                "volatility": 0.18929,
                "max_drawdown": -0.503602,
                "cagr_per_vol": 0.370226
              },
              {
                "weight": 0.5,
                "cagr": 0.069799,
                "volatility": 0.186115,
                "max_drawdown": -0.491705,
                "cagr_per_vol": 0.375029
              },
              {
                "weight": 0.6,
                "cagr": 0.069387,
                "volatility": 0.183556,
                "max_drawdown": -0.479834,
                "cagr_per_vol": 0.378016
              },
              {
                "weight": 0.7,
                "cagr": 0.06885,
                "volatility": 0.181604,
                "max_drawdown": -0.467991,
                "cagr_per_vol": 0.379121
              },
              {
                "weight": 0.8,
                "cagr": 0.06819,
                "volatility": 0.180247,
                "max_drawdown": -0.456177,
                "cagr_per_vol": 0.378315
              },
              {
                "weight": 0.9,
                "cagr": 0.067411,
                "volatility": 0.17947,
                "max_drawdown": -0.444394,
                "cagr_per_vol": 0.375612
              },
              {
                "weight": 1.0,
                "cagr": 0.066516,
                "volatility": 0.179253,
                "max_drawdown": -0.433004,
                "cagr_per_vol": 0.371073
              }
            ],
            "best_weight": 0.2,
            "margin": 0.000408,
            "outcome": "holds",
            "best_weight_risk_adjusted": 0.7,
            "outcome_risk_adjusted": "holds",
            "legs": [
              {
                "key": {
                  "kind": "leg",
                  "leg": "base"
                },
                "best_weight": 0.2,
                "margin": 0.000408,
                "outcome": "holds"
              },
              {
                "key": {
                  "kind": "leg",
                  "leg": "conservative"
                },
                "best_weight": 0.2,
                "margin": 0.000388,
                "outcome": "holds"
              }
            ],
            "sizing": {
              "recommended_ratio": 0.121028,
              "reason": "half of the full-Kelly w*: one history estimates w*, and full Kelly is sensitive to that error",
              "at_cap": false,
              "w_star_grid": 0.2,
              "w_star": 0.242057,
              "g0": 0.069832,
              "g_star": 0.070252,
              "g_half": 0.070148,
              "half_keeps": 0.75315,
              "break_even": 0.49021,
              "break_even_status": "found",
              "naive_kelly": 0.211662,
              "margin_by_leg": {
                "base": 0.00042,
                "conservative": 0.000396
              },
              "grid_note": null,
              "halves": [
                {
                  "start": "2006-03-31",
                  "end": "2011-02-01",
                  "w_star": 1.0
                },
                {
                  "start": "2011-03-01",
                  "end": "2016-03-01",
                  "w_star": 0.0
                }
              ],
              "decisive_months": [
                {
                  "month": "2015-09",
                  "w_star_without": 1.0,
                  "delta": 0.757943
                },
                {
                  "month": "2009-11",
                  "w_star_without": 0.572093,
                  "delta": 0.330037
                },
                {
                  "month": "2007-07",
                  "w_star_without": 0.0,
                  "delta": -0.242057
                }
              ],
              "curve": [
                {
                  "weight": 0.0,
                  "cagr": 0.069832
                },
                {
                  "weight": 0.025,
                  "cagr": 0.069915
                },
                {
                  "weight": 0.05,
                  "cagr": 0.069989
                },
                {
                  "weight": 0.075,
                  "cagr": 0.070053
                },
                {
                  "weight": 0.1,
                  "cagr": 0.070109
                },
                {
                  "weight": 0.125,
                  "cagr": 0.070155
                },
                {
                  "weight": 0.15,
                  "cagr": 0.070192
                },
                {
                  "weight": 0.175,
                  "cagr": 0.07022
                },
                {
                  "weight": 0.2,
                  "cagr": 0.07024
                },
                {
                  "weight": 0.225,
                  "cagr": 0.07025
                },
                {
                  "weight": 0.25,
                  "cagr": 0.070251
                },
                {
                  "weight": 0.275,
                  "cagr": 0.070244
                },
                {
                  "weight": 0.3,
                  "cagr": 0.070229
                },
                {
                  "weight": 0.325,
                  "cagr": 0.070204
                },
                {
                  "weight": 0.35,
                  "cagr": 0.070171
                },
                {
                  "weight": 0.375,
                  "cagr": 0.07013
                },
                {
                  "weight": 0.4,
                  "cagr": 0.07008
                },
                {
                  "weight": 0.425,
                  "cagr": 0.070022
                },
                {
                  "weight": 0.45,
                  "cagr": 0.069956
                },
                {
                  "weight": 0.475,
                  "cagr": 0.069881
                },
                {
                  "weight": 0.5,
                  "cagr": 0.069799
                },
                {
                  "weight": 0.525,
                  "cagr": 0.069708
                },
                {
                  "weight": 0.55,
                  "cagr": 0.069609
                },
                {
                  "weight": 0.575,
                  "cagr": 0.069502
                },
                {
                  "weight": 0.6,
                  "cagr": 0.069387
                },
                {
                  "weight": 0.625,
                  "cagr": 0.069265
                },
                {
                  "weight": 0.65,
                  "cagr": 0.069134
                },
                {
                  "weight": 0.675,
                  "cagr": 0.068996
                },
                {
                  "weight": 0.7,
                  "cagr": 0.06885
                },
                {
                  "weight": 0.725,
                  "cagr": 0.068696
                },
                {
                  "weight": 0.75,
                  "cagr": 0.068535
                },
                {
                  "weight": 0.775,
                  "cagr": 0.068366
                },
                {
                  "weight": 0.8,
                  "cagr": 0.06819
                },
                {
                  "weight": 0.825,
                  "cagr": 0.068006
                },
                {
                  "weight": 0.85,
                  "cagr": 0.067815
                },
                {
                  "weight": 0.875,
                  "cagr": 0.067617
                },
                {
                  "weight": 0.9,
                  "cagr": 0.067411
                },
                {
                  "weight": 0.925,
                  "cagr": 0.067198
                },
                {
                  "weight": 0.95,
                  "cagr": 0.066978
                },
                {
                  "weight": 0.975,
                  "cagr": 0.066751
                },
                {
                  "weight": 1.0,
                  "cagr": 0.066516
                }
              ]
            }
          },
          {
            "key": "full",
            "label": "Full history",
            "requested_start": "2006-03-31",
            "requested_end": "2026-10-02",
            "start": "2006-03-31",
            "end": "2026-10-01",
            "clipped": false,
            "years": 20.503765,
            "points": [
              {
                "weight": 0.0,
                "cagr": 0.111465,
                "volatility": 0.192795,
                "max_drawdown": -0.551412,
                "cagr_per_vol": 0.578151
              },
              {
                "weight": 0.1,
                "cagr": 0.111502,
                "volatility": 0.185672,
                "max_drawdown": -0.539429,
                "cagr_per_vol": 0.600531
              },
              {
                "weight": 0.2,
                "cagr": 0.111169,
                "volatility": 0.179662,
                "max_drawdown": -0.527466,
                "cagr_per_vol": 0.618764
              },
              {
                "weight": 0.3,
                "cagr": 0.110504,
                "volatility": 0.174695,
                "max_drawdown": -0.515523,
                "cagr_per_vol": 0.63255
              },
              {
                "weight": 0.4,
                "cagr": 0.10954,
                "volatility": 0.170712,
                "max_drawdown": -0.503602,
                "cagr_per_vol": 0.641663
              },
              {
                "weight": 0.5,
                "cagr": 0.108305,
                "volatility": 0.167657,
                "max_drawdown": -0.491705,
                "cagr_per_vol": 0.645991
              },
              {
                "weight": 0.6,
                "cagr": 0.106822,
                "volatility": 0.165475,
                "max_drawdown": -0.479834,
                "cagr_per_vol": 0.645545
              },
              {
                "weight": 0.7,
                "cagr": 0.105111,
                "volatility": 0.164115,
                "max_drawdown": -0.467991,
                "cagr_per_vol": 0.640475
              },
              {
                "weight": 0.8,
                "cagr": 0.103191,
                "volatility": 0.163521,
                "max_drawdown": -0.456177,
                "cagr_per_vol": 0.631058
              },
              {
                "weight": 0.9,
                "cagr": 0.101076,
                "volatility": 0.163638,
                "max_drawdown": -0.444394,
                "cagr_per_vol": 0.617681
              },
              {
                "weight": 1.0,
                "cagr": 0.098781,
                "volatility": 0.164412,
                "max_drawdown": -0.433004,
                "cagr_per_vol": 0.600812
              }
            ],
            "best_weight": 0.1,
            "margin": 3.7e-05,
            "outcome": "inconclusive",
            "best_weight_risk_adjusted": 0.5,
            "outcome_risk_adjusted": "holds",
            "legs": [
              {
                "key": {
                  "kind": "leg",
                  "leg": "base"
                },
                "best_weight": 0.1,
                "margin": 3.7e-05,
                "outcome": "inconclusive"
              },
              {
                "key": {
                  "kind": "leg",
                  "leg": "conservative"
                },
                "best_weight": 0.1,
                "margin": 2.2e-05,
                "outcome": "inconclusive"
              }
            ],
            "sizing": {
              "recommended_ratio": 0.0,
              "reason": "margin +0.69bp/yr on the base leg, +0.61bp/yr after the cash-drag add-back: not above the 1bp/yr bar",
              "at_cap": false,
              "w_star_grid": 0.1,
              "w_star": 0.059069,
              "g0": 0.111465,
              "g_star": 0.111534,
              "g_half": 0.111517,
              "half_keeps": 0.753036,
              "break_even": 0.119575,
              "break_even_status": "found",
              "naive_kelly": 0.008297,
              "margin_by_leg": {
                "base": 6.9e-05,
                "conservative": 6.1e-05
              },
              "grid_note": null,
              "halves": [
                {
                  "start": "2006-03-31",
                  "end": "2016-05-02",
                  "w_star": 0.087396
                },
                {
                  "start": "2016-06-01",
                  "end": "2026-09-01",
                  "w_star": 0.052339
                }
              ],
              "decisive_months": [
                {
                  "month": "2015-09",
                  "w_star_without": 0.194379,
                  "delta": 0.13531
                },
                {
                  "month": "2021-12",
                  "w_star_without": 0.128638,
                  "delta": 0.069569
                },
                {
                  "month": "2022-05",
                  "w_star_without": 0.123861,
                  "delta": 0.064792
                }
              ],
              "curve": [
                {
                  "weight": 0.0,
                  "cagr": 0.111465
                },
                {
                  "weight": 0.025,
                  "cagr": 0.111511
                },
                {
                  "weight": 0.05,
                  "cagr": 0.111533
                },
                {
                  "weight": 0.075,
                  "cagr": 0.111529
                },
                {
                  "weight": 0.1,
                  "cagr": 0.111502
                },
                {
                  "weight": 0.125,
                  "cagr": 0.111452
                },
                {
                  "weight": 0.15,
                  "cagr": 0.111379
                },
                {
                  "weight": 0.175,
                  "cagr": 0.111284
                },
                {
                  "weight": 0.2,
                  "cagr": 0.111169
                },
                {
                  "weight": 0.225,
                  "cagr": 0.111032
                },
                {
                  "weight": 0.25,
                  "cagr": 0.110875
                },
                {
                  "weight": 0.275,
                  "cagr": 0.110699
                },
                {
                  "weight": 0.3,
                  "cagr": 0.110504
                },
                {
                  "weight": 0.325,
                  "cagr": 0.110289
                },
                {
                  "weight": 0.35,
                  "cagr": 0.110057
                },
                {
                  "weight": 0.375,
                  "cagr": 0.109807
                },
                {
                  "weight": 0.4,
                  "cagr": 0.10954
                },
                {
                  "weight": 0.425,
                  "cagr": 0.109255
                },
                {
                  "weight": 0.45,
                  "cagr": 0.108954
                },
                {
                  "weight": 0.475,
                  "cagr": 0.108637
                },
                {
                  "weight": 0.5,
                  "cagr": 0.108305
                },
                {
                  "weight": 0.525,
                  "cagr": 0.107956
                },
                {
                  "weight": 0.55,
                  "cagr": 0.107593
                },
                {
                  "weight": 0.575,
                  "cagr": 0.107215
                },
                {
                  "weight": 0.6,
                  "cagr": 0.106822
                },
                {
                  "weight": 0.625,
                  "cagr": 0.106415
                },
                {
                  "weight": 0.65,
                  "cagr": 0.105994
                },
                {
                  "weight": 0.675,
                  "cagr": 0.105559
                },
                {
                  "weight": 0.7,
                  "cagr": 0.105111
                },
                {
                  "weight": 0.725,
                  "cagr": 0.10465
                },
                {
                  "weight": 0.75,
                  "cagr": 0.104177
                },
                {
                  "weight": 0.775,
                  "cagr": 0.10369
                },
                {
                  "weight": 0.8,
                  "cagr": 0.103191
                },
                {
                  "weight": 0.825,
                  "cagr": 0.10268
                },
                {
                  "weight": 0.85,
                  "cagr": 0.102157
                },
                {
                  "weight": 0.875,
                  "cagr": 0.101623
                },
                {
                  "weight": 0.9,
                  "cagr": 0.101076
                },
                {
                  "weight": 0.925,
                  "cagr": 0.100519
                },
                {
                  "weight": 0.95,
                  "cagr": 0.09995
                },
                {
                  "weight": 0.975,
                  "cagr": 0.099371
                },
                {
                  "weight": 1.0,
                  "cagr": 0.098781
                }
              ]
            }
          }
        ],
        "unavailable": {}
      }
    ],
    "missing": {}
  }
}

/** The measured payload without ``rates``: the conservative leg cannot be
 *  built, so each window keeps its base row and the size is withheld. */
export function hedgeOverlayWithoutRates(): HedgeOverlayResponse {
  const m = HEDGE_OVERLAY_MEASURED
  return {
    ...m,
    rates_snapshot: null,
    overlay: {
      ...m.overlay,
      dividend: {
        ...m.overlay.dividend,
        conservative_from: null,
        conservative_reason: 'sizing needs T-bills for the cash-drag check',
        bill_point_in_time_from: null,
        snapshot_ids: { tiingo_eod: m.overlay.dividend.snapshot_ids.tiingo_eod! },
      },
      programs: m.overlay.programs.map((p) => ({
        ...p,
        windows: p.windows.map((w) => ({
          ...w,
          legs: w.legs.filter((r) => r.key.kind === 'leg' && r.key.leg === 'base'),
          sizing: {
            ...w.sizing,
            recommended_ratio: null,
            reason: 'sizing needs T-bills for the cash-drag check',
            margin_by_leg: { base: w.sizing.margin_by_leg.base! },
          },
        })),
      })),
    },
  }
}
