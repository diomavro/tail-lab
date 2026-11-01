# ADR 0035 evidence: the Fama–MacBeth fragility-beyond-vol test

The evidence behind `docs/adr/0035-*.md`, kept in the repo so every figure the page and the ADR quote can be checked. Nothing in `src/` imports anything here.

## Hashes

| File | sha256 |
|---|---|
| `PLAN_FM.md` (the pre-registered plan, byte-identical to the one hashed at the freeze) | `f6bf0a400a50128598f1425f75ad5959826f0e790133dfaf68ed97c803500a63` |
| `src/tail_lab/research/backtest/beyond_vol.py` (frozen) | `6648196d39638cb975fd120cce7153365a88af7f45c2dba81bf600ae6dc4744f` |
| `src/tail_lab/research/backtest/walk_forward.py` at the freeze commit `ef31431c15c05f95b9b08db41b52997da46ff533` | `704cc2dea6fe74ca50fa3b2f3fd7b461696b5c17972169d2a94e7cdebea270f5` |

Check them with `sha256sum docs/research/0035/PLAN_FM.md src/tail_lab/research/backtest/beyond_vol.py`. (`walk_forward.py` has changed since the freeze, moving `SignalTestResult` out to `signal_test.py`; check the freeze-time hash with `git show ef31431:src/tail_lab/research/backtest/walk_forward.py | sha256sum`.)

## Records

| File | What it is |
|---|---|
| `PLAN_FM.md` | The plan, written and hashed before the real outcomes were read. Defines the procedure, the statuses and the size/power gate. |
| `FREEZE.txt` | The freeze record: time, commit, the two source hashes, the plan hash, library versions, and the critical values at T = 81. Its plan path is the author's scratch directory at the time; the hash is what matters. |
| `REAL_RESULT.json` | The one real result: `beyond_vol_test` on the lake as of 2026-10-31, setting 0.10Δ · 4w · 4y · top 5, T = 81. Status `no_evidence_beyond_trailing_vol`, γ̄ −0.0079, 90% CI [−0.0332, +0.0174], p 0.71. The frontend's quoted copy (`frontend/src/components/putlab/preregistered.ts`) is checked against this file by `tests/test_frontend_signal_wording_parity.py`. |
| `frozen_check.txt` | Size and power, measured on **pre-freeze builds** of `beyond_vol.py`. Its `MODE=frozen` section is an earlier build in which no result could pass (one period had 39 equity names, below `N_MIN = 40`, so the equity check failed every time); it is superseded and kept for the record. Its `MODE=d3fix` section (`N_MIN` patched to 30 at run time) is the table the ADR quotes, 2,000 reps per world. The power curve and the 9.6 pp minimum detectable gap are at the end. |
| `frozen_on_6648.txt` | The size/power check **rerun on the frozen file** `6648196d…`, 2,000 reps: pure noise 0.013, −15%/yr drift 0.060, power 0.707 at a = 0.20 and 0.907 at a = 0.25. These match the pre-freeze figures. |
| `frozen_check_sha6648.txt` | A further rerun on `6648196d…` over 11 worlds, 1,000 reps each (complete). |
| `repro_2026-11-01.txt` | Output of `repro_check.py`: the confirmatory run on the moved code reproduces `REAL_RESULT.json` exactly (`identical: True`), mode `confirmatory`, T = 81, 9 stress episodes, and the signal test's own verdict (composite p = 0.049) on the same lake. |
| `memory_2026-11-01.txt` | Output of `memory_check.py`: peak memory of one `beyond_vol_test` call on synthetic panels, T = 81 and T = 300. The basis of `MAX_BEYOND_VOL_PERIODS`. |

Power figures are conditional on sufficient data (at least 3 stress episodes, as `insufficient_data` is defined in the ADR). In the simulation 30–40% of power-world replications fall short of that, because the simulated SPY pays rarely; on the real panel (T = 81, 9 episodes) the data are sufficient, so the conditional figures apply.

## Reference code (not imported, not run in CI)

These are the simulation harness. They are the final versions of the scripts, not necessarily the exact versions that wrote every output file; where an output's format differs, the table below says how its figures follow. They expect the author's scratch directory layout (`periods_signals_only.pkl` next to them, the repo at `~/Documents/apps/tail-lab`) and are kept as a record, not as a supported tool. They are outside the linted and type-checked trees.

| File | Role |
|---|---|
| `fm_sim.py` | First-round simulation of candidate specifications on placebo outcomes. |
| `fm_r2.py`, `fm_r2_sim.py` | Round 2: the panel loader, the fixed-b critical value, and the outcome worlds (`logit_world` and the nulls). |
| `adv3_sim.py` | Round 3, the adversarial worlds (persistent factor, skew, heavy names, ...). |
| `frozen_check.py` | Drives the repo's `beyond_vol_test` through those worlds. |
| `cache_periods.py` | Writes the two period caches from the lake (see below). |
| `fm_r2_gap.py` | Converts a planted effect `a` into the beyond-vol pay-rate gap between the top and bottom composite quintiles (the 2.20 / 4.37 / 8.64 / 10.72 / 12.77 / 16.76 pp figures). |
| `frozen_check_summary.py` | Summarises the raw `frozen_check_*` runs into `frozen_check.txt`: rejection rates unconditional and conditional on sufficient data, CIs, and the MDE (9.6 pp) by linear interpolation of power against the gap. The committed `frozen_check.py` prints the raw format of `frozen_on_6648.txt`; its conditional figures follow by dividing passes by sufficient-data replications (a = 0.20: 965 / 1364 = 0.707; a = 0.25: 1196 / 1319 = 0.907). |
| `repro_check.py`, `memory_check.py` | The two checks above. `repro_check.py` reads the lake as configured in `.env`; `memory_check.py` uses the test suite's synthetic `_panel`. |

## Rerunning the size check

1. Build the caches. This reads the lake and needs it to hold the data as of 2026-10-31:

   ```bash
   mkdir -p /tmp/fm && env -u PYTHONPATH .venv/bin/python docs/research/0035/cache_periods.py /tmp/fm
   ```

   It writes `periods_signals_only.pkl` (real signals, outcomes zeroed; this is what the simulations use) and `periods_REAL_OUTCOMES.pkl` (the real outcomes).

2. Copy the harness scripts next to `periods_signals_only.pkl` and run:

   ```bash
   cp docs/research/0035/*.py /tmp/fm/ && cd /tmp/fm
   SPEC_DUMMY=1 SPEC_ABS=1 NOBOOT=1 MODE=frozen env -u PYTHONPATH \
     ~/Documents/apps/tail-lab/.venv/bin/python frozen_check.py 2000
   ```

   `MODE=frozen` runs the repo file as it stands; `MODE=d3fix` patches `N_MIN` to 30. A world filter can follow the rep count. A full run takes about 10 minutes per four worlds.

## The real-outcome pickle is not committed

`periods_REAL_OUTCOMES.pkl` holds the real put outcomes. It is not in the repo (it is a binary, 700 KB, and opening it before the freeze is what the pre-registration forbids). Regenerate it with `cache_periods.py` as above; the lake snapshot as of 2026-10-31 reproduces `REAL_RESULT.json` by running `beyond_vol_test` on those periods (`collect_periods(..., as_of=date(2026, 10, 31), rule=ByDelta(0.10), tenor_weeks=4.0, signal_years=4.0, top_k=5)`).
