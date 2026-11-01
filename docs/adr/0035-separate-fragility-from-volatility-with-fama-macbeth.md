# 35. Separate fragility from volatility with Fama–MacBeth

Date: 2026-11-01

## Status

**Accepted.**
- Dio asked for this on 2026-11-01: "do the Fama–MacBeth fragility vs vol test next".
- This ADR amends `docs/adr/0033` §3, which said the signal test "does **not** statistically separate fragility from volatility".
- Dio made two calls during review:
  - the time-series bandwidth is 16, not 3, which trades power for size;
  - crash exposure a name inherits from a skewed index counts as fragility.

## Context

- **ADR 0033 compared without separating.** The walk-forward compares the composite's top-5 basket with six plain-volatility screens, but it cannot say whether fragility adds anything *beyond* vol.
- **Three earlier attempts failed simulation review and were cut.** These were quintile-matched baskets, linear rank residualisation, and squared rank residualisation. All three judged a k = 5 basket on a residual.
- **Fama–MacBeth uses the whole cross-section instead.** Each period it regresses the outcome on the composite with vol controls, then judges the time series of slopes. That is the standard tool for asking whether X prices returns once Z is controlled for (Fama & MacBeth 1973).

The plan went through four simulation rounds before freezing. It was simulated on the **real** signal panel (81 periods, 43 to 70 names each; `repro_2026-11-01.txt` and `frozen_check.txt` in `docs/research/0035/`), with the outcomes blanked and placebo outcomes planted. The rounds found:

- `rv_ratio = rv_long / rv20` exactly. The three vol signals carry two dimensions, not three.
- The vol surface explains a median 68% of the composite's rank. Identification therefore rests on the remaining third.
- The first specification falsely passed 8–31% of the time in most vol-only worlds.
- Persistent name effects inflate a per-period standard error. Residual fragility is 63% between names.
- The composite is mostly market co-movement once vol is controlled. So γ_t moves with the market: a bull sample tilts against a pass, and a bear sample would give false passes.
- The bottom of residual fragility is non-equity ETFs.

## Decision

1. **The procedure is `research/backtest/beyond_vol.py`.** It is one hard-coded path, with no options.
   - **Outcome:** the within-period rank of put ROI.
   - **Regressor:** the composite's rank.
   - **Controls:**
     - a tensor-product spline on (rank rv20, rank rv_long);
     - a spline on rank rv_ratio;
     - absolute-level splines on log rv20 and log rv_long;
     - a non-equity dummy. The non-equity set is frozen: TLT, LQD, HYG, GLD, SLV, USO, UNG, DBA, DBC.
   - **Estimation:** per-period OLS, skipping any period with fewer than 40 names. γ̄ = mean γ_t.
   - **Variance:** the larger of a name-clustered (HC2) variance and a Newey–West variance at L = 16.
   - **p-value:** one-sided, from a simulated fixed-b null (100,000 draws, seed 20261101). A pass means p < 0.05.

2. **A pass also needs three checks that can only demote, never promote.**
   - **D2, market-neutral:** regress γ_t = a + b·r_SPY,t. The test on a must also pass.
   - **D1:** γ̄ stays above 0 without its most influential period and without its most influential stress episode.
   - **D3:** equity names alone keep γ̄ above 0.

3. **The statuses are:**
   - `insufficient_data`: fewer than 24 periods, or fewer than 3 episodes. These are ADR 0033's limits.
   - `no_evidence_beyond_trailing_vol`
   - `not_robust`: a demotion fired.
   - `predicts_beyond_trailing_vol`

   A result is **confirmatory** only at the pre-registered setting (Δ0.10 · 4w · 4y · top 5) **and** on the pre-registered data date, the lake as of **2026-10-31** (`FREEZE_AS_OF` in `signal_test.py`). Only a confirmatory result is shown on the page with a p-value and a status. The API carries both fields in every mode; the page withholds them outside confirmatory mode.
   - The same setting on any other date is a **monitoring** re-run. It shows γ̄ and its 90% CI, and quotes the pre-registered result beside them, but it is not the pre-registered test and cannot confirm anything.
   - Every other rail setting is **exploratory**: γ̄ and its 90% CI only.
   - The API carries this as `beyond_vol_mode` (`confirmatory`, `monitoring` or `exploratory`).

4. **The test only runs up to 120 periods** (`MAX_BEYOND_VOL_PERIODS` in `signal_test.py`).
   - Its simulated null allocates about 2.4 MB per period up front. Measured peaks on synthetic panels: 375 MB at T = 81 and 768 MB at T = 300, each including a 224 MB baseline (`docs/research/0035/memory_2026-11-01.txt`, from `memory_check.py`).
   - A 1-week rail run has T of roughly 320–420. That would exceed the 1 GB Fly VM and be killed without a message.
   - Above the cap, `beyond_vol` is `null` and `beyond_vol_unavailable_reason` says why. The page prints the reason. The same field carries the error text if the frozen procedure refuses the periods (a `ValueError`). Any other exception propagates.

5. **Pre-registration.** The freeze was recorded before the real outcomes were read:

   | Item | Value |
   |---|---|
   | Commit | `ef31431c15c05f95b9b08db41b52997da46ff533` |
   | `beyond_vol.py` sha256 | `6648196d39638cb975fd120cce7153365a88af7f45c2dba81bf600ae6dc4744f` |
   | `walk_forward.py` sha256 | `704cc2dea6fe74ca50fa3b2f3fd7b461696b5c17972169d2a94e7cdebea270f5` at the freeze commit (it supplies the episodes and the validation) |
   | Plan sha256 | `f6bf0a400a50128598f1425f75ad5959826f0e790133dfaf68ed97c803500a63` |
   | Libraries | numpy 2.5.2, scipy 1.18.0 |
   | Critical value at T = 81 | 2.0769 (2.0925 for the a test) |

   After the freeze, `SignalTestResult` and the `walk_forward()` orchestration moved out of `walk_forward.py` into `signal_test.py`. The point was to break an import cycle with `beyond_vol.py`. Nothing `beyond_vol.py` imports was changed. The confirmatory result below was reproduced exactly on the moved code (`docs/research/0035/repro_2026-11-01.txt`, from `repro_check.py`).

   The plan, the freeze record, the real result and the simulation outputs are committed in `docs/research/0035/` (see its `README.md`).

   **Disclosure.** The choice of the rank outcome and the controls was made knowing three things:
   - about 80% of name-periods sit at −1;
   - the vol-ratio screen won the signal test;
   - the composite's walk-forward p was 0.079.

   Also, rv20 at the next entry is the realised vol of the period before it. The signals-only panel was therefore not fully blind on vol. No choice after round 3 used signals from period t+1.

## Measured size and power

These are rates of the module returning `predicts_beyond_trailing_vol`, with 2,000 reps per world on the real signal panel.

**Which build was measured.**
- The table was measured on pre-freeze builds of `beyond_vol.py` (`docs/research/0035/frozen_check.txt`; its `MODE=frozen` section is an earlier build in which no result could pass, and is superseded).
- It was then reproduced on the frozen file, sha256 `6648196d…` (`docs/research/0035/frozen_on_6648.txt`, 2,000 reps): pure noise 0.013, −15%/yr bear drift 0.060, and power 0.707 at a = 0.20 and 0.907 at a = 0.25, the same as the pre-freeze figures.
- A further rerun on `6648196d` at 1,000 reps per world is in `docs/research/0035/frozen_check_sha6648.txt`. It may be incomplete; the file shows what had finished.
- How to rerun is in the README there.

| World | Size |
|---|---|
| Pure noise and vol-only nulls | 0.013–0.020 |
| Name effects | 0.028 |
| Asset-class effects | 0.032 |
| Sharp vol cliff | 0.040 |
| Five heavy names carrying an effect | 0.036 |
| **Limit:** persistent factor on residual-fragility names | 0.047–0.074 |
| **Limit:** negatively skewed index at zero drift | 0.061 |
| Negatively skewed index at +10%/yr drift | 0.028 |
| **Limit:** Gaussian one-factor market at −15%/yr drift | 0.060 |

The skewed-index rows and the bear-drift row are different simulated worlds and are not to be read as one result.

Power to detect a pay-rate gap between the top and bottom composite quintiles, beyond what vol explains. **These figures are conditional on sufficient data**, meaning at least 3 stress episodes. In the simulation 30–40% of replications in the power worlds fall short of that, because the simulated SPY pays rarely. Unconditionally, power at a = 0.20 is 0.48. On the real panel (T = 81, 9 stress episodes; `repro_2026-11-01.txt`) the data are sufficient, so the conditional figures apply.

| Gap | Power |
|---|---|
| 8.6 pp | 0.71 |
| 10.7 pp | 0.91 |
| 12.8 pp | 0.98 |

The minimum detectable gap at 80% power is about 9.6 pp, or about 10.6 pp with name effects.

The rank outcome barely registers *how much* a put pays, only its place in the ranking. The `log1p(payoff/cost)` slope is shown but not tested.

## The first result (2026-10-31 lake, pre-registered setting)

| Item | Value |
|---|---|
| Status | `no_evidence_beyond_trailing_vol` |
| γ̄ | −0.0079 |
| 90% CI | [−0.033, +0.017] |
| One-sided p | 0.71 |
| Periods (T) | 81, none skipped |
| Market-neutral intercept a | −0.0053 (p 0.66) |
| γ̄ with no controls | −0.0059 |

The composite does not order 0.10Δ put outcomes across the cross-section, with or without vol controls. If fragility adds anything beyond trailing vol, the effect is smaller than this test can see, about 10 pp. The walk-forward's top-5 basket result is p = 0.049 on this same 2026-10-31 lake (`repro_2026-11-01.txt`; ADR 0033 recorded 0.079 on its first run). Against a composite that does not order outcomes across the cross-section, it is therefore a top-of-the-list or chance effect, not a broad ordering.

## Consequences

- The Bake-off's signal-test section states this result in one sentence, with the γ_t chart and the `fama_macbeth` concept. The concept lists the limits above.
- The pre-registered figures the page quotes beside a monitoring re-run live in one frontend constant (`preregistered.ts`). A test reads it against `docs/research/0035/REAL_RESULT.json`.
- **Changing the procedure is a new pre-registration.** It needs a new ADR, a new simulation gate and a new freeze before any real outcome is read. Tests may be added without that.
- Real-quote pricing and a point-in-time universe remain the open fixes (`AGENT_TODO.md`). Under model pricing, a pass would only ever have meant that fragile names moved more than their trailing vol implied.
