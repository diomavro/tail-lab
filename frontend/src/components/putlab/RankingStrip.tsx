import { useState } from 'react'
import type { RankedAsset, Verdict } from '../../api/client'
import { fmtFixed, fmtPct, fmtPrice } from './format'

/* The fragility ranking.
 *
 * Was a permanently pinned panel above the tab bar, ~232px tall and
 * drag-resizable, which ate the top third of every view and pushed the actual
 * result below the fold. It had to be pinned, because picking a name and
 * reading its result lived on two different tabs. Now that they are one view it
 * can open as a single line — the top three, clickable — and expand to the full
 * table with every metric on demand.
 *
 * The expanded table surfaces the six RankedAsset fields the old table dropped:
 * downside_beta, co_skewness, co_kurtosis, tail_beta, downside_capture and
 * vol_beta were all fetched and only fragility_score was shown.
 *
 * `best_annualized` is the headline, so a click lands on the cell the row
 * advertised rather than on the currently selected strike — see the note in
 * PutLab.selectAsset for why that distinction is load-bearing.
 */

interface Props {
  ranked: RankedAsset[]
  currentAsset: string
  onSelect: (asset: string, best?: { moneyness_pct: number; tenor_weeks: number }) => void
}

const VERDICT_TAG: Record<Verdict, string> = {
  confirmed: 'pl-tag pl-tag-ok',
  regime_only: 'pl-tag pl-tag-mute',
  failed: 'pl-tag pl-tag-bad',
  untested: 'pl-tag pl-tag-outline',
}

const num = (v: number | null, digits = 2) => (v == null ? '—' : fmtFixed(v, digits))

/** `fragility_score` is a mean fractional rank in 0..1, so it needs scaling
 *  before it reads as a score. Rounding it as-is collapses the whole universe
 *  onto 0 and 1. */
const fragility = (v: number | null) => (v == null ? '—' : String(Math.round(v * 100)))
const at = (r: RankedAsset) =>
  r.best_moneyness_pct == null || r.best_tenor_weeks == null
    ? '—'
    : `${r.best_moneyness_pct}% · ${r.best_tenor_weeks}w`

export function RankingStrip({ ranked, currentAsset, onSelect }: Props) {
  const [open, setOpen] = useState(false)

  const pick = (r: RankedAsset) =>
    onSelect(
      r.asset,
      r.best_moneyness_pct != null && r.best_tenor_weeks != null
        ? { moneyness_pct: r.best_moneyness_pct, tenor_weeks: r.best_tenor_weeks }
        : undefined,
    )

  return (
    <>
      <div className="pl-rank-strip" role="group" aria-label="Fragility ranking">
        <span className="pl-kicker">Fragility ranking</span>
        {ranked.slice(0, 3).map((r, i) => (
          <button key={r.asset} type="button" className="pl-rank-pick" onClick={() => pick(r)}>
            <span className="pl-rank-n">{i + 1}</span>{' '}
            <strong>{r.asset.toUpperCase()}</strong>{' '}
            <span className={r.best_annualized != null && r.best_annualized >= 0 ? 'pl-pos' : 'pl-neg'}>
              {r.best_annualized == null ? '—' : fmtPct(r.best_annualized)}
            </span>{' '}
            <span className="pl-rank-at">{at(r)}</span>
          </button>
        ))}
        <button
          type="button"
          className="pl-btn pl-btn-ghost pl-rank-more"
          aria-expanded={open}
          onClick={() => setOpen((v) => !v)}
        >
          {open ? 'Collapse ranking' : `All ${ranked.length} names, ten metrics`}
        </button>
      </div>
      <div className="pl-rule-hair" />

      {open && (
        <div className="pl-scroll" style={{ padding: '4px 0 20px' }}>
          <table className="pl-table" aria-label="Full fragility ranking">
            <thead>
              <tr>
                <th className="num">#</th>
                <th>Name</th>
                <th className="num">Spot</th>
                <th className="num">Fragility</th>
                <th className="num">Dn &beta;</th>
                <th className="num">Co-skew</th>
                <th className="num">Co-kurt</th>
                <th className="num">Tail &beta;</th>
                <th className="num">Dn cap</th>
                <th className="num">Vol &beta;</th>
                <th className="num">Best /yr</th>
                <th>At</th>
                <th className="num">Hit</th>
                <th className="num">Verdict</th>
              </tr>
            </thead>
            <tbody>
              {ranked.map((r, i) => (
                <tr
                  key={r.asset}
                  className={`pick${r.asset === currentAsset ? ' is-current' : ''}`}
                  onClick={() => pick(r)}
                >
                  <td className="num dimmer">{i + 1}</td>
                  <td className="nowrap">
                    <strong className="bold">{r.asset.toUpperCase()}</strong>{' '}
                    <span className="dimmer">{r.name}</span>
                  </td>
                  <td className="num dim">{fmtPrice(r.spot)}</td>
                  <td className="num bold pl-rank-frag">{fragility(r.fragility_score)}</td>
                  <td className="num dim">{num(r.downside_beta)}</td>
                  <td className="num dim">{num(r.co_skewness)}</td>
                  <td className="num dim">{num(r.co_kurtosis, 1)}</td>
                  <td className="num dim">{num(r.tail_beta)}</td>
                  <td className="num dim">{num(r.downside_capture)}</td>
                  <td className="num dim">{num(r.vol_beta)}</td>
                  <td
                    className={`num bold ${
                      r.best_annualized != null && r.best_annualized >= 0 ? 'pl-pos' : 'pl-neg'
                    }`}
                  >
                    {r.best_annualized == null ? '—' : fmtPct(r.best_annualized)}
                  </td>
                  <td className="dimmer nowrap">{at(r)}</td>
                  <td className="num dim">{Math.round(r.hit_rate * 100)}%</td>
                  <td className="num">
                    <span className={VERDICT_TAG[r.verdict]}>{r.verdict.replace('_', ' ')}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  )
}
