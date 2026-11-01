// The pre-registered test of fragility beyond trailing vol (ADR 0035), run once
// on the lake as of 2026-10-31. The ONE place its figures live in the frontend:
// the monitoring line (signalWording.ts `beyondVolLine`) quotes them. They are
// rounded copies of docs/research/0035/REAL_RESULT.json, and
// tests/test_frontend_signal_wording_parity.py reads this file and that JSON
// and fails if they drift. Edit the figures only together with a new ADR.
export const PREREGISTERED = {
  as_of: '2026-10-31',
  status: 'no_evidence_beyond_trailing_vol',
  gamma_bar: -0.0079,
  ci90_low: -0.0332,
  ci90_high: 0.0174,
  p_value: 0.71,
  n_periods: 81,
} as const
