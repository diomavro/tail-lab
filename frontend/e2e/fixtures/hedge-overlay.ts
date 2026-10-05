// The Book tab's payload (GET /api/putlab/hedge-overlay).
//
// Unlike putlab.ts these numbers are NOT invented: they are the real result of
// research/backtest/hedge_overlay.run_hedge_overlay on Cboe's published SPX,
// PPUT, PPUT3M and VXTH histories through 2026-10-02, so the spec asserts on the
// verdicts the page actually shows -- PPUT and PPUT3M fail, VXTH's letter window
// is clipped to 2006-03-31 and holds, and VXTH's full history is too close to
// call. Only the provenance ids are fake.

import type { HedgeOverlayResponse } from '../../src/api/client'

export const HEDGE_OVERLAY: HedgeOverlayResponse = {
  "cboe_snapshot": "cboe_strategy@2026-10-02",
  "code_sha": "e2e0000",
  "overlay": {
    "as_of": "2026-10-02",
    "dividend_yield": 0.019,
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
            "sensitivity": [
              {
                "dividend_yield": 0.014,
                "best_weight": 0.0,
                "margin": -0.001503,
                "outcome": "fails"
              },
              {
                "dividend_yield": 0.019,
                "best_weight": 0.0,
                "margin": -0.002035,
                "outcome": "fails"
              },
              {
                "dividend_yield": 0.024,
                "best_weight": 0.0,
                "margin": -0.002569,
                "outcome": "fails"
              }
            ]
          },
          {
            "key": "full",
            "label": "Full history",
            "requested_start": "1986-06-30",
            "requested_end": "2026-10-02",
            "start": "1986-06-30",
            "end": "2026-10-02",
            "clipped": false,
            "years": 40.257358,
            "points": [
              {
                "weight": 0.0,
                "cagr": 0.109543,
                "volatility": 0.182935,
                "max_drawdown": -0.556037,
                "cagr_per_vol": 0.598806
              },
              {
                "weight": 0.1,
                "cagr": 0.106796,
                "volatility": 0.176332,
                "max_drawdown": -0.542604,
                "cagr_per_vol": 0.605654
              },
              {
                "weight": 0.2,
                "cagr": 0.103998,
                "volatility": 0.170107,
                "max_drawdown": -0.529017,
                "cagr_per_vol": 0.611369
              },
              {
                "weight": 0.3,
                "cagr": 0.101151,
                "volatility": 0.164267,
                "max_drawdown": -0.51528,
                "cagr_per_vol": 0.61577
              },
              {
                "weight": 0.4,
                "cagr": 0.098256,
                "volatility": 0.158821,
                "max_drawdown": -0.501394,
                "cagr_per_vol": 0.61866
              },
              {
                "weight": 0.5,
                "cagr": 0.095315,
                "volatility": 0.153777,
                "max_drawdown": -0.487364,
                "cagr_per_vol": 0.619823
              },
              {
                "weight": 0.6,
                "cagr": 0.092329,
                "volatility": 0.149149,
                "max_drawdown": -0.473192,
                "cagr_per_vol": 0.619039
              },
              {
                "weight": 0.7,
                "cagr": 0.0893,
                "volatility": 0.144947,
                "max_drawdown": -0.45978,
                "cagr_per_vol": 0.616085
              },
              {
                "weight": 0.8,
                "cagr": 0.086229,
                "volatility": 0.141185,
                "max_drawdown": -0.446451,
                "cagr_per_vol": 0.610749
              },
              {
                "weight": 0.9,
                "cagr": 0.083117,
                "volatility": 0.137874,
                "max_drawdown": -0.433049,
                "cagr_per_vol": 0.602845
              },
              {
                "weight": 1.0,
                "cagr": 0.079965,
                "volatility": 0.135025,
                "max_drawdown": -0.419578,
                "cagr_per_vol": 0.592224
              }
            ],
            "best_weight": 0.0,
            "margin": -0.002747,
            "outcome": "fails",
            "best_weight_risk_adjusted": 0.5,
            "outcome_risk_adjusted": "holds",
            "sensitivity": [
              {
                "dividend_yield": 0.014,
                "best_weight": 0.0,
                "margin": -0.002193,
                "outcome": "fails"
              },
              {
                "dividend_yield": 0.019,
                "best_weight": 0.0,
                "margin": -0.002747,
                "outcome": "fails"
              },
              {
                "dividend_yield": 0.024,
                "best_weight": 0.0,
                "margin": -0.003304,
                "outcome": "fails"
              }
            ]
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
            "sensitivity": [
              {
                "dividend_yield": 0.014,
                "best_weight": 0.0,
                "margin": -0.001791,
                "outcome": "fails"
              },
              {
                "dividend_yield": 0.019,
                "best_weight": 0.0,
                "margin": -0.002324,
                "outcome": "fails"
              },
              {
                "dividend_yield": 0.024,
                "best_weight": 0.0,
                "margin": -0.002859,
                "outcome": "fails"
              }
            ]
          },
          {
            "key": "full",
            "label": "Full history",
            "requested_start": "2004-03-19",
            "requested_end": "2026-10-02",
            "start": "2004-03-19",
            "end": "2026-10-02",
            "clipped": false,
            "years": 22.537988,
            "points": [
              {
                "weight": 0.0,
                "cagr": 0.110595,
                "volatility": 0.187476,
                "max_drawdown": -0.556037,
                "cagr_per_vol": 0.589914
              },
              {
                "weight": 0.1,
                "cagr": 0.10843,
                "volatility": 0.180289,
                "max_drawdown": -0.540089,
                "cagr_per_vol": 0.601425
              },
              {
                "weight": 0.2,
                "cagr": 0.106205,
                "volatility": 0.173517,
                "max_drawdown": -0.523892,
                "cagr_per_vol": 0.612074
              },
              {
                "weight": 0.3,
                "cagr": 0.10392,
                "volatility": 0.167172,
                "max_drawdown": -0.507454,
                "cagr_per_vol": 0.621635
              },
              {
                "weight": 0.4,
                "cagr": 0.101577,
                "volatility": 0.161271,
                "max_drawdown": -0.490781,
                "cagr_per_vol": 0.629849
              },
              {
                "weight": 0.5,
                "cagr": 0.099176,
                "volatility": 0.155832,
                "max_drawdown": -0.473883,
                "cagr_per_vol": 0.636426
              },
              {
                "weight": 0.6,
                "cagr": 0.096718,
                "volatility": 0.150874,
                "max_drawdown": -0.456768,
                "cagr_per_vol": 0.641055
              },
              {
                "weight": 0.7,
                "cagr": 0.094206,
                "volatility": 0.146416,
                "max_drawdown": -0.439444,
                "cagr_per_vol": 0.64341
              },
              {
                "weight": 0.8,
                "cagr": 0.091638,
                "volatility": 0.14248,
                "max_drawdown": -0.421922,
                "cagr_per_vol": 0.643167
              },
              {
                "weight": 0.9,
                "cagr": 0.089017,
                "volatility": 0.139083,
                "max_drawdown": -0.407118,
                "cagr_per_vol": 0.640027
              },
              {
                "weight": 1.0,
                "cagr": 0.086343,
                "volatility": 0.136244,
                "max_drawdown": -0.398631,
                "cagr_per_vol": 0.633738
              }
            ],
            "best_weight": 0.0,
            "margin": -0.002165,
            "outcome": "fails",
            "best_weight_risk_adjusted": 0.7,
            "outcome_risk_adjusted": "holds",
            "sensitivity": [
              {
                "dividend_yield": 0.014,
                "best_weight": 0.0,
                "margin": -0.001612,
                "outcome": "fails"
              },
              {
                "dividend_yield": 0.019,
                "best_weight": 0.0,
                "margin": -0.002165,
                "outcome": "fails"
              },
              {
                "dividend_yield": 0.024,
                "best_weight": 0.0,
                "margin": -0.002719,
                "outcome": "fails"
              }
            ]
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
            "sensitivity": [
              {
                "dividend_yield": 0.014,
                "best_weight": 0.8,
                "margin": 0.000161,
                "outcome": "holds"
              },
              {
                "dividend_yield": 0.019,
                "best_weight": 0.4,
                "margin": 0.001249,
                "outcome": "holds"
              },
              {
                "dividend_yield": 0.024,
                "best_weight": 0.1,
                "margin": 9e-06,
                "outcome": "inconclusive"
              }
            ]
          },
          {
            "key": "full",
            "label": "Full history",
            "requested_start": "2006-03-31",
            "requested_end": "2026-10-02",
            "start": "2006-03-31",
            "end": "2026-10-02",
            "clipped": false,
            "years": 20.506502,
            "points": [
              {
                "weight": 0.0,
                "cagr": 0.111714,
                "volatility": 0.193768,
                "max_drawdown": -0.556037,
                "cagr_per_vol": 0.576535
              },
              {
                "weight": 0.1,
                "cagr": 0.111766,
                "volatility": 0.186691,
                "max_drawdown": -0.54366,
                "cagr_per_vol": 0.598667
              },
              {
                "weight": 0.2,
                "cagr": 0.111444,
                "volatility": 0.180704,
                "max_drawdown": -0.531286,
                "cagr_per_vol": 0.61672
              },
              {
                "weight": 0.3,
                "cagr": 0.110787,
                "volatility": 0.17573,
                "max_drawdown": -0.518917,
                "cagr_per_vol": 0.63044
              },
              {
                "weight": 0.4,
                "cagr": 0.109829,
                "volatility": 0.171705,
                "max_drawdown": -0.506555,
                "cagr_per_vol": 0.639638
              },
              {
                "weight": 0.5,
                "cagr": 0.108598,
                "volatility": 0.168572,
                "max_drawdown": -0.494203,
                "cagr_per_vol": 0.644222
              },
              {
                "weight": 0.6,
                "cagr": 0.107117,
                "volatility": 0.166276,
                "max_drawdown": -0.481861,
                "cagr_per_vol": 0.644212
              },
              {
                "weight": 0.7,
                "cagr": 0.105406,
                "volatility": 0.164763,
                "max_drawdown": -0.469533,
                "cagr_per_vol": 0.639746
              },
              {
                "weight": 0.8,
                "cagr": 0.103484,
                "volatility": 0.163981,
                "max_drawdown": -0.457219,
                "cagr_per_vol": 0.631075
              },
              {
                "weight": 0.9,
                "cagr": 0.101366,
                "volatility": 0.163877,
                "max_drawdown": -0.444923,
                "cagr_per_vol": 0.618548
              },
              {
                "weight": 1.0,
                "cagr": 0.099065,
                "volatility": 0.1644,
                "max_drawdown": -0.433004,
                "cagr_per_vol": 0.602586
              }
            ],
            "best_weight": 0.1,
            "margin": 5.2e-05,
            "outcome": "inconclusive",
            "best_weight_risk_adjusted": 0.5,
            "outcome_risk_adjusted": "holds",
            "sensitivity": [
              {
                "dividend_yield": 0.014,
                "best_weight": 0.2,
                "margin": 0.000819,
                "outcome": "holds"
              },
              {
                "dividend_yield": 0.019,
                "best_weight": 0.1,
                "margin": 5.2e-05,
                "outcome": "inconclusive"
              },
              {
                "dividend_yield": 0.024,
                "best_weight": 0.0,
                "margin": -0.000495,
                "outcome": "fails"
              }
            ]
          }
        ],
        "unavailable": {}
      }
    ],
    "missing": {}
  }
}
