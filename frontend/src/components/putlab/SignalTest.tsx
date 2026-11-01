import { type ReactNode, useMemo, useState } from 'react'
import type { SignalPeriodRecord, SignalScreenSummary, SignalTestResult } from '../../api/client'
import { CONCEPTS, isConceptId } from '../../content/concepts'
import { ConceptInfo } from './ConceptInfo'
import { fmtFixed, fmtP, fmtPct } from './format'
import { exBestText, pct1, sentenceText, STATUS_LABEL, STATUS_TAG, windowText } from './signalWording'
import { Th } from './Th'

/* The out-of-sample signal test (docs/adr/0033): the verdict, one chart, one
 * table. Used twice on the Bake-off -- the fixed STRIP_SIGNAL_TEST read as the
 * headline, and the run at the rail's settings below it -- so it takes its
 * heading and its table label from the caller. Everything it prints is the
 * API's result; the only things added here are cadence wording and layout.
 */

const W = 1000
const H = 220
const PAD = 8
// The axis is cut here so one crash period cannot flatten the other bars: a
// bar past the cut runs to the edge and is flagged.
const CLIP_QUANTILE = 0.97

function quantile(sorted: number[], q: number): number {
  if (sorted.length === 0) return 0
  return sorted[Math.min(sorted.length - 1, Math.floor(q * sorted.length))]!
}

function ExcessChart({ periods, topK }: { periods: SignalPeriodRecord[]; topK: number }) {
  const [hover, setHover] = useState<number | null>(null)
  const model = useMemo(() => {
    if (periods.length === 0) return null
    const all = periods.flatMap((p) => [p.excess, p.band_low, p.band_high])
    const cap = Math.max(quantile(all.map(Math.abs).sort((a, b) => a - b), CLIP_QUANTILE) * 1.15, 0.05)
    const lo = Math.max(Math.min(0, ...all), -cap)
    const hi = Math.max(Math.min(Math.max(0, ...all), cap), lo + 0.05)
    const Y = (v: number) => PAD + (1 - (Math.min(hi, Math.max(lo, v)) - lo) / (hi - lo)) * (H - PAD * 2)
    const sw = (W - PAD * 2) / periods.length
    return { lo, hi, Y, sw, clipped: periods.filter((p) => p.excess > hi || p.excess < lo).length }
  }, [periods])
  if (!model) return null
  const { Y, sw, lo, hi } = model
  const hovered = hover == null ? null : periods[hover]
  const first = periods[0]!
  const last = periods[periods.length - 1]!

  return (
    <div data-testid="signal-chart">
      <div className="pl-pane-label">Per-period excess: the top {topK} minus all names, against random baskets</div>
      <div className="pl-chart">
        <svg
          viewBox={`0 0 ${W} ${H}`}
          role="img"
          aria-label={`Per-period excess return of the composite's top ${topK} over all names, with the random-basket 5th to 95th percentile band and stress episodes shaded`}
          onPointerLeave={() => setHover(null)}
          onPointerMove={(e) => {
            const rect = e.currentTarget.getBoundingClientRect()
            const x = ((e.clientX - rect.left) / rect.width) * W
            setHover(Math.min(periods.length - 1, Math.max(0, Math.floor((x - PAD) / sw))))
          }}
        >
          {[hi, lo].map((v) => (
            <line key={v} className="pl-tape-grid" x1={PAD} x2={W - PAD} y1={Y(v)} y2={Y(v)} />
          ))}
          {periods.map((p, i) =>
            p.stress ? (
              <rect key={`s${i}`} className="pl-sig-stress" x={PAD + i * sw} y={PAD} width={sw} height={H - PAD * 2} />
            ) : null,
          )}
          {periods.map((p, i) => (
            <rect
              key={`b${i}`}
              className="pl-sig-band"
              x={PAD + i * sw + sw * 0.1}
              y={Y(p.band_high)}
              width={sw * 0.8}
              height={Math.max(1, Y(p.band_low) - Y(p.band_high))}
            />
          ))}
          <line className="pl-tape-zero" x1={PAD} x2={W - PAD} y1={Y(0)} y2={Y(0)} />
          {periods.map((p, i) => (
            <rect
              key={`e${i}`}
              className={`pl-sig-bar ${p.excess >= 0 ? 'is-pos' : 'is-neg'}`}
              x={PAD + i * sw + sw * 0.25}
              y={Math.min(Y(0), Y(p.excess))}
              width={sw * 0.5}
              height={Math.max(1, Math.abs(Y(p.excess) - Y(0)))}
            />
          ))}
        </svg>
        {[hi, 0, lo].map((v) => (
          <span className="pl-ytick" key={`y${v}`} style={{ top: `${(Y(v) / H) * 100}%` }}>
            {fmtPct(v)}
          </span>
        ))}
      </div>
      <div className="pl-xticks">
        <span>{first.date}</span>
        <span aria-live="polite">
          {hovered
            ? `${hovered.date} · ${hovered.regime} · ${hovered.top_k_assets.map((a) => a.toUpperCase()).join(' ')} · excess ${pct1(hovered.excess)}`
            : ''}
        </span>
        <span>{last.expiry_date}</span>
      </div>
      <div className="pl-legend" style={{ marginTop: 6 }}>
        <span>
          <span className="pl-sig-key pl-sig-key-bar" />
          Basket minus all names
        </span>
        <span>
          <span className="pl-sig-key pl-sig-key-band" />
          Random baskets, 5th to 95th percentile
        </span>
        <span>
          <span className="pl-sig-key pl-sig-key-stress" />
          SPY&rsquo;s put paid off (payoff &gt; 0)
          <ConceptInfo id="stress_episode" />
        </span>
        {model.clipped > 0 && <span>{model.clipped} bar{model.clipped === 1 ? '' : 's'} cut at the axis edge</span>}
      </div>
    </div>
  )
}

const pText = (x: number | null) => (x == null ? '—' : fmtP(x))
const ic = (x: number | null) => (x == null ? '—' : fmtFixed(x, 2))
const share = (x: number | null) => (x == null ? '—' : `${Math.round(x * 100)}%`)
const ex = (x: number | null) => (x == null ? '—' : pct1(x))

function ScreenRow({
  s,
  i,
  r,
}: {
  s: SignalScreenSummary
  i: number
  r: SignalTestResult
}) {
  const isComposite = s.kind === 'composite'
  const isBest = s.screen === r.verdict.best_vol_baseline
  // The composite is judged on its raw p; the rest on the BH-corrected one. A
  // non-composite screen with no BH p (the API leaves it null) falls back to
  // the raw p and is labelled raw, never BH.
  const corrected = !isComposite && s.p_bh != null
  const p = corrected ? s.p_bh : s.p_random
  return (
    <tr className={isComposite ? 'is-winner' : undefined} data-screen={s.screen}>
      <td className="num dimmer">{i + 1}</td>
      <td className="nowrap">
        <strong className="bold">{s.label}</strong>
        {isComposite && <span className="pl-tag pl-tag-outline pl-cell-tag">pre-registered</span>}
        {isBest && <span className="pl-tag pl-tag-mute pl-cell-tag">best of 6, picked after the fact</span>}
      </td>
      <td className={`num ${s.basket_total_roi >= 0 ? 'pl-pos' : 'pl-neg'}`}>{pct1(s.basket_total_roi)}</td>
      <td className={`num bold ${s.excess_vs_universe >= 0 ? 'pl-pos' : 'pl-neg'}`}>{pct1(s.excess_vs_universe)}</td>
      <td className="num dim">{pct1(s.excess_vs_spy)}</td>
      <td className="num" data-testid={`p-${s.screen}`}>
        {pText(p)} <span className="dimmer">{corrected ? 'BH' : 'raw'}</span>
      </td>
      <td className="num" data-testid={`volflag-${s.screen}`}>
        {isComposite ? (
          r.verdict.vol_baseline_as_good ? (
            <span className="pl-tag pl-tag-bad">volatility did as well</span>
          ) : (
            <span className="pl-tag pl-tag-ok">no</span>
          )
        ) : (
          '—'
        )}
      </td>
      <td className="num dim">{ic(s.mean_ic)}</td>
      <td className="num dim">{share(s.ic_hit_share)}</td>
      <td className="num dim">{s.n_ic_periods}</td>
      <td className="num dim">{ex(s.excess_ex_best_period)}</td>
      <td className="num dim">{ex(s.excess_ex_best_name_period)}</td>
      <td className="num dim">
        {s.n_periods} / {r.n_episodes}
      </td>
    </tr>
  )
}

function SignalTable({ r, label }: { r: SignalTestResult; label: string }) {
  // The composite first (the pre-registered hypothesis), the rest best first.
  const rows = useMemo(() => {
    const comp = r.screens.filter((s) => s.kind === 'composite')
    const rest = r.screens.filter((s) => s.kind !== 'composite').sort((a, b) => b.excess_vs_universe - a.excess_vs_universe)
    return [...comp, ...rest]
  }, [r.screens])
  return (
    <>
      <div className="pl-scroll">
        <table className="pl-table" aria-label={label}>
          <thead>
            <tr>
              <th className="num" data-noexplain="index">
                #
              </th>
              <th data-noexplain="name">Screen</th>
              <Th concept="excess_return" className="num">
                Basket
              </Th>
              <Th concept="excess_return" className="num">
                vs all names
              </Th>
              <Th concept="excess_return" className="num">
                vs SPY
              </Th>
              <Th concept="random_baseline" className="num">
                p
              </Th>
              <Th concept="vol_baseline" className="num">
                Volatility as good?
              </Th>
              <Th concept="rank_ic" className="num">
                Mean IC
              </Th>
              <Th concept="rank_ic" className="num">
                IC &gt; 0
              </Th>
              <Th concept="rank_ic" className="num">
                IC periods
              </Th>
              <Th concept="ex_best" className="num">
                Without best period
              </Th>
              <Th concept="ex_best" className="num">
                Without best name-period
              </Th>
              <Th concept="stress_episode" className="num">
                Periods / episodes
              </Th>
            </tr>
          </thead>
          <tbody>
            {rows.map((s, i) => (
              <ScreenRow key={s.screen} s={s} i={i} r={r} />
            ))}
          </tbody>
        </table>
      </div>
      <p className="pl-micro" data-testid="signal-baselines">
        All {r.universe_size} names returned {r.universe_total_roi == null ? '—' : pct1(r.universe_total_roi)} on the
        same periods; an equal-premium SPY put {r.spy_total_roi == null ? '—' : pct1(r.spy_total_roi)}. Return is
        payoff over premium plus half-spread; commission is excluded.
      </p>
    </>
  )
}

export function SignalPanel({
  result,
  tableLabel,
  heading,
}: {
  result: SignalTestResult
  tableLabel: string
  heading: ReactNode
}) {
  const v = result.verdict
  const carried = exBestText(v)
  return (
    <div data-testid="signal-panel">
      {heading}
      <p className="pl-signal-verdict" data-testid="signal-verdict">
        <span className={STATUS_TAG[v.status]} data-testid="signal-status">
          {STATUS_LABEL[v.status]}
        </span>{' '}
        {sentenceText(v)} {carried}
      </p>
      <p className="pl-note" data-testid="signal-window">
        {windowText(result)}
        <ConceptInfo id="test_window" />
      </p>
      {result.caveats.map((id) =>
        isConceptId(id) ? (
          <p className="pl-caveat pl-bake-caveat" key={id} data-testid={`signal-caveat-${id}`}>
            <strong>{CONCEPTS[id].term}.</strong> {CONCEPTS[id].short}
            <ConceptInfo id={id} />
          </p>
        ) : (
          // A caveat the API raised that this build has no text for: show its
          // id, never drop it silently.
          <p className="pl-caveat pl-bake-caveat" key={id} data-testid={`signal-caveat-${id}`}>
            <strong>{id}.</strong> (No explanation on file for this caveat.)
          </p>
        ),
      )}
      {result.periods.length > 0 && <ExcessChart periods={result.periods} topK={result.top_k} />}
      <SignalTable r={result} label={tableLabel} />
      <p className="pl-micro" data-testid="signal-provenance">
        As of {result.as_of} · {result.snapshot_ids.length} bronze snapshot{result.snapshot_ids.length === 1 ? '' : 's'}{' '}
        · {result.code_sha === 'unknown' ? 'code sha unknown' : result.code_sha.slice(0, 7)}
      </p>
    </div>
  )
}
