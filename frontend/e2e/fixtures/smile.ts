// /api/putlab/vol-surface payloads for the Surface tab's Smile section.
import type { SmileSlice, VolSurfaceResponse } from '../../src/api/client'

const svi = { a: 0.004, b: 0.05, rho: -0.7, m: 0.02, sigma: 0.1 }

function slice(expiration: string, t: number, over: Partial<SmileSlice> = {}): SmileSlice {
  const ks = [-0.3, -0.2, -0.1, -0.05, 0.02, 0.06, 0.1, 0.15]
  return {
    expiration,
    t_years: t,
    forward: 502.3,
    forward_source: 'parity',
    parity_refusal: null,
    points: ks.map((k) => ({ k, iv: 0.18 - 0.4 * k + 0.6 * k * k, right: k < 0 ? 'P' : 'C' })),
    fit: 'svi',
    svi,
    rmse_vol_points: 0.21,
    atm_vol: 0.1834,
    atm_skew: -0.52,
    atm_curvature: 1.9,
    risk_reversal_25: -0.0412,
    butterfly_ok: true,
    butterfly_min_g: 0.61,
    butterfly_n_invalid: 0,
    lee_ok: true,
    refusal: null,
    ...over,
  }
}

export const SMILE_FITTED: VolSurfaceResponse = {
  symbol: 'spy',
  as_of: '2026-11-05',
  session: '2026-11-04',
  spot: 500,
  r: 0.04,
  q: 0,
  q_source: 'unknown',
  two_wing: true,
  one_wing_reason: null,
  slices: [
    slice('2026-11-06', 2 / 365.25, {
      points: [],
      fit: null,
      svi: null,
      rmse_vol_points: null,
      atm_vol: null,
      atm_skew: null,
      atm_curvature: null,
      risk_reversal_25: null,
      butterfly_ok: null,
      butterfly_min_g: null,
      butterfly_n_invalid: null,
      lee_ok: null,
      forward_source: 'carry_q0',
      refusal: 'tenor_below_3_days',
    }),
    slice('2026-12-04', 30 / 365.25),
    slice('2027-02-03', 91 / 365.25, { butterfly_ok: false, butterfly_min_g: -0.0329 }),
    slice('2027-05-04', 181 / 365.25),
  ],
  calendar_violations: [[30 / 365.25, 91 / 365.25]],
  refusal: null,
}

export const SMILE_REFUSED: VolSurfaceResponse = {
  ...SMILE_FITTED,
  symbol: 'tsla',
  session: null,
  spot: null,
  two_wing: false,
  slices: [],
  calendar_violations: [],
  refusal: 'no tsla put quotes on or before 2026-11-05',
}

/** A session captured before the call wing (ADR 0031): puts only, so the
 * slices are one-wing quadratics -- ATM shape, no SVI, no risk reversal. */
export const SMILE_ONE_WING: VolSurfaceResponse = {
  ...SMILE_FITTED,
  two_wing: false,
  one_wing_reason: 'no call quotes for the 2026-10-20 session',
  session: '2026-10-20',
  calendar_violations: [],
  slices: [
    slice('2026-11-20', 31 / 365.25, {
      fit: 'one_wing_quadratic',
      svi: null,
      forward_source: 'carry_q0',
      rmse_vol_points: null,
      atm_curvature: 1.1,
      risk_reversal_25: null,
      butterfly_ok: null,
      butterfly_min_g: null,
      butterfly_n_invalid: null,
      lee_ok: null,
      points: [-0.2, -0.1, -0.05, -0.02, 0.01, 0.02].map((k) => ({ k, iv: 0.2 - 0.5 * k, right: 'P' as const })),
    }),
  ],
}
