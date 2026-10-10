import type {
  AccuracyResponse,
  PutBacktestResponse,
  PutLabLeaderboardResponse,
  RegimeTimelineView,
  RegimeVerdictResponse,
  SweepResponse,
} from '../../../api/client'
import { useState } from 'react'
import { AccuracyPanel } from '../AccuracyPanel'
import { ConceptInfo } from '../ConceptInfo'
import { fmtDollar, fmtFixed, fmtMult, fmtPct, fmtPrice } from '../format'
import { Ledger } from '../Ledger'
import { MemoryTeaser } from '../MemoryTeaser'
import { PriceBuild } from '../PriceBuild'
import { StrikeBuild } from '../StrikeBuild'
import type { ResourceState } from '../PutLab'
import { RankingStrip } from '../RankingStrip'
import { StrategyTape } from '../StrategyTape'
import { SweepGrid } from '../SweepGrid'
import { StandaloneNote } from '../StandaloneNote'
import { deltaLabel, type PutLabControls } from '../types'

/* Screen and Backtest, merged.
 *
 * Reading order is the argument: which name (ranking strip) -> is it worth it
 * (the spread) -> what it cost to find out (stat row) -> what happened (tape) ->
 * would another cell have been better (sweep) -> how wrong could this be
 * (accuracy) -> did it only work in one regime (memory) -> every roll (ledger).
 *
 * The hero is the annualized spread against the benchmark, not return on
 * premium. A tail hedge's raw ROI is almost always negative, which tells a
 * reader nothing on its own; the honest question is whether it beat the plain
 * long they could have held instead, and that is one number.
 *
 * Six equal-weight stat cards with a gradient wash on one of them is not a
 * hierarchy. One large figure, then a quiet rule-separated row.
 */

interface Props {
  controls: PutLabControls
  onChange: (patch: Partial<PutLabControls>) => void
  backtest: ResourceState<PutBacktestResponse>
  sweep: ResourceState<SweepResponse>
  accuracy: ResourceState<AccuracyResponse>
  ranking: ResourceState<PutLabLeaderboardResponse>
  regimeVerdict: ResourceState<RegimeVerdictResponse>
  regimes: RegimeTimelineView | null
  onSelectAsset: (asset: string, best?: { moneyness_pct: number; tenor_weeks: number }) => void
}

/** Points of annualized return, signed. Points, not percent: a difference of
 *  two rates is a spread, and printing it with a % sign invites reading it as a
 *  ratio of the two. */
function signedPts(x: number): string {
  return `${x >= 0 ? '+' : '−'}${Math.abs(x * 100).toFixed(1)}`
}
function rate(x: number): string {
  return `${x >= 0 ? '+' : '−'}${Math.abs(x * 100).toFixed(1)}%`
}

export function WorkspaceView({
  controls,
  onChange,
  backtest,
  sweep,
  accuracy,
  ranking,
  regimes,
  regimeVerdict,
  onSelectAsset,
}: Props) {
  // Which explanations are open: kept here, above the Result that a re-run
  // unmounts, so changing a control never closes what explains the change.
  const [explainOpen, setExplainOpen] = useState({ price: false, strike: false })
  return (
    <div>
      <StandaloneNote />
      {ranking.status === 'ready' && controls.strike_rule === 'delta' && (
        <p className="pl-note" data-testid="rank-rule-note">
          The ranking screens every name on a grid of distances below spot (each row&rsquo;s best cell), not by delta;
          opening a row switches the strike back to distance.
        </p>
      )}
      {ranking.status === 'ready' && (
        <RankingStrip
          ranked={ranking.data.ranked}
          currentAsset={controls.asset}
          onSelect={onSelectAsset}
          snapshotIds={ranking.data.snapshot_ids}
          codeSha={ranking.data.code_sha}
        />
      )}

      {backtest.status === 'loading' && (
        <p className="pl-status" role="status" aria-live="polite">
          Running the backtest…
        </p>
      )}
      {backtest.status === 'error' && (
        <p className="pl-status pl-status-error" role="alert">
          {backtest.message}
        </p>
      )}
      {backtest.status === 'no-data' && (
        <p className="pl-status" role="status" aria-live="polite">
          No data yet for this asset and window &mdash; try a shorter lookback or a different asset.
        </p>
      )}

      {backtest.status === 'ready' && (
        <Result
          explainOpen={explainOpen}
          onExplain={(patch) => setExplainOpen((o) => ({ ...o, ...patch }))}
          bt={backtest.data}
          controls={controls}
          onChange={onChange}
          sweep={sweep}
          accuracy={accuracy}
          regimes={regimes}
          regimeVerdict={regimeVerdict}
        />
      )}
    </div>
  )
}

function Result({
  explainOpen,
  onExplain,
  bt,
  controls,
  onChange,
  sweep,
  accuracy,
  regimes,
  regimeVerdict,
}: {
  explainOpen: { price: boolean; strike: boolean }
  onExplain: (patch: Partial<{ price: boolean; strike: boolean }>) => void
  bt: PutBacktestResponse
  controls: PutLabControls
  onChange: (patch: Partial<PutLabControls>) => void
  sweep: ResourceState<SweepResponse>
  accuracy: ResourceState<AccuracyResponse>
  regimes: RegimeTimelineView | null
  regimeVerdict: ResourceState<RegimeVerdictResponse>
}) {
  const [showLedger, setShowLedger] = useState(false)
  const lastCycle = bt.cycles[bt.cycles.length - 1]
  const bench = bt.benchmark_annualized
  const spread = bench == null ? null : bt.annualized_return - bench
  const optimism = accuracy.status === 'ready' ? accuracy.data.model.expected_optimism : null
  // accuracy.select_reference picks by strike AND tenor, so name the program it
  // served rather than implying the one nearest the strike alone.
  const reference = (accuracy.status === 'ready' && accuracy.data.model.reference) || 'the Cboe program'
  const benchSymbol = sweep.status === 'ready' ? sweep.data.benchmark_symbol : 'benchmark'
  const bleedMonths = ((bt.worst_bleed_streak * controls.tenor_weeks) / 4.33).toFixed(0)

  const stats: { k: string; v: string; cls?: string; note: string; concept?: string }[] = [
    {
      k: 'Return on premium',
      v: fmtPct(bt.roi_on_premium),
      cls: bt.roi_on_premium >= 0 ? 'pl-pos' : 'pl-neg',
      // roi_on_premium divides by n_cycles x notional and carries no brokerage
      // term -- so the basis is named here and the fee figure lives on Net P&L.
      // Also names priced_from: a reader seeing "+1867%" with no qualifier
      // cannot tell whether that denominator was priced by the model or a
      // real quote (AGENT_TODO.md, "roi_on_premium needs its denominator
      // named on the surface").
      note: `${bt.priced_from === 'market' ? 'Market-priced' : 'Model-priced'} · ${bt.n_cycles} rolls × ${fmtDollar(bt.notional)} budget`,
      concept: 'roi_on_premium',
    },
    {
      k: 'Net P&L',
      v: fmtDollar(bt.net_pnl),
      cls: bt.net_pnl >= 0 ? 'pl-pos' : 'pl-neg',
      note: `on ${fmtDollar(bt.total_premium)} premium + ${fmtDollar(bt.total_brokerage)} fees`,
    },
    {
      k: 'Hit rate',
      v: `${Math.round(bt.hit_rate * 100)}%`,
      // Not "rolls that finished in the money": intrinsic value alone is not a
      // hit, the payoff has to clear the premium budget.
      note: 'rolls whose payoff cleared the budget',
      concept: 'hit_rate',
    },
    {
      k: 'Biggest payoff',
      v: fmtMult(bt.biggest_payoff_mult),
      note: 'best single roll, on its premium budget',
    },
    {
      k: 'Worst bleed',
      v: `${bt.worst_bleed_streak}×`,
      cls: bt.worst_bleed_streak > 10 ? 'pl-neg' : undefined,
      note: `≈${bleedMonths} months of paying`,
    },
    {
      k: 'Sharpe',
      v: bt.sharpe_ratio == null ? '—' : fmtFixed(bt.sharpe_ratio),
      note: 'rough for a convex payoff',
    },
  ]

  return (
    <>
      <div className="pl-hero">
        <div>
          <div className="pl-kicker" style={{ marginBottom: 6 }}>
            Annualized spread vs {benchSymbol}
          </div>
          <div className="pl-hero-fig">
            <span className={`pl-hero-num ${spread != null && spread >= 0 ? 'pl-pos' : 'pl-neg'}`}>
              {spread == null ? '—' : signedPts(spread)}
            </span>
            <span className="pl-hero-unit">
              points per year, {bt.lookback_years}y rolled, gross of brokerage
            </span>
          </div>
          <p className="pl-hero-sentence" data-testid="hero-sentence">
            {bt.asset.toUpperCase()} at {fmtPrice(bt.spot)},{' '}
            {bt.strike_rule === 'delta' && bt.target_delta != null && lastCycle ? (
              <>
                struck at {deltaLabel(bt.target_delta)} &mdash; the latest roll at {fmtPrice(lastCycle.strike)},{' '}
                {(lastCycle.entry_moneyness_pct ?? 0) >= 0
                  ? `${(lastCycle.entry_moneyness_pct ?? 0).toFixed(1)}% below`
                  : `${(-(lastCycle.entry_moneyness_pct ?? 0)).toFixed(1)}% above`}{' '}
                its entry spot.
              </>
            ) : (
              <>
                struck {fmtPrice(bt.spot * (1 - (bt.moneyness_pct ?? 0) / 100))} &mdash; {bt.moneyness_pct}% out of
                the money.
              </>
            )}{' '}
            {bt.n_cycles} rolls, of which{' '}
            {Math.round(bt.hit_rate * bt.n_cycles)} paid back, and the worst drought ran{' '}
            {bt.worst_bleed_streak} rolls.
          </p>
          {bt.strike_rule === 'delta' && (bt.beyond_model_depth_share ?? 0) > 0 && (
            <p className="pl-note pl-neg" data-testid="hero-depth">
              {Math.round((bt.beyond_model_depth_share ?? 0) * bt.cycles.length)} of these {bt.cycles.length} rolls
              struck deeper than the flat-vol model can price, so their payoffs &mdash; and this headline &mdash; are
              inflated (see &ldquo;How the strike is chosen&rdquo;).
            </p>
          )}
        </div>
        <dl className="pl-brokenout">
          <div className="pl-kicker">Broken out</div>
          <div className="pl-brokenout-row">
            <dt>This hedge</dt>
            <dd className={bt.annualized_return >= 0 ? 'pl-pos' : 'pl-neg'}>{rate(bt.annualized_return)}</dd>
          </div>
          <div className="pl-brokenout-row">
            <dt>Benchmark long</dt>
            <dd>{bench == null ? '—' : rate(bench)}</dd>
          </div>
          <div className="pl-brokenout-row">
            <dt data-testid="hero-error-bar">
              Error bar
              {controls.strike_rule === 'delta' &&
                ` (${reference}, nearest ${controls.moneyness_pct}% below spot and this tenor, not this delta)`}
            </dt>
            <dd>{optimism == null ? '—' : rate(optimism)}</dd>
          </div>
        </dl>
      </div>

      <div className="pl-stats">
        {stats.map((s) => (
          <div key={s.k}>
            <div className="pl-stat-k">
              {s.k}
              {s.concept && <ConceptInfo id={s.concept} />}
            </div>
            <div className={`pl-stat-v ${s.cls ?? ''}`}>{s.v}</div>
            <div className="pl-stat-note">{s.note}</div>
          </div>
        ))}
      </div>

      <section className="pl-section">
        <PriceBuild bt={bt} open={explainOpen.price} onToggle={(open) => onExplain({ price: open })} />
      </section>

      <section className="pl-section">
        <StrikeBuild
          bt={bt}
          regimes={regimes?.segments ?? []}
          modelPricedMaxMoneynessPct={sweep.status === 'ready' ? sweep.data.model_priced_max_moneyness_pct : null}
          open={explainOpen.strike}
          onToggle={(open) => onExplain({ strike: open })}
        />
      </section>

      <section className="pl-section">
        <div className="pl-section-head">
          <h3>The tape</h3>
          <div className="pl-legend">
            <span>
              <span className="pl-swatch-line" />
              Underlying
            </span>
            <span>
              <span className="pl-swatch-dash" />
              Benchmark hurdle
            </span>
            <span>
              <span className="pl-swatch-dot" />
              Payoff cleared the budget
            </span>
            <span>
              <span className="pl-swatch-box pl-swatch-elevated" />
              Elevated
            </span>
            <span>
              <span className="pl-swatch-box pl-swatch-crisis" />
              Crisis
            </span>
          </div>
        </div>
        <p className="pl-note" style={{ marginBottom: 12 }}>
          {bt.strike_rule === 'delta' && bt.target_delta != null
            ? `Every ${bt.tenor_weeks} weeks the ladder resets at ${deltaLabel(bt.target_delta)}, so its distance below spot moves with the market.`
            : `Every ${bt.tenor_weeks} weeks the ladder resets ${bt.moneyness_pct}% below spot.`}
          The lower pane is cumulative P&amp;L on premium; the thin line is the annualized rate
          earned so far, converging on the headline. Bands are VIX regime &mdash; calm below 17,
          elevated to 28, crisis above.
        </p>
        <StrategyTape
          pricePath={bt.price_path}
          mtmCurve={bt.mtm_curve}
          cycles={bt.cycles}
          annualizedSoFar={bt.annualized_so_far}
          benchmarkAnnualized={bt.benchmark_annualized}
          notional={bt.notional}
          regimes={regimes?.segments ?? []}
        />
      </section>

      <section className="pl-section">
        <div className="pl-section-head">
          <h3>Strike &times; tenor</h3>
        </div>
        <p className="pl-note" style={{ marginBottom: 14 }}>
          The whole grid, re-run at this premium and window. Density inside each band tracks
          magnitude; the band itself is the verdict. Click any cell to move the position there.
          {controls.strike_rule === 'delta' &&
            ' This is a grid of distances below spot, not deltas: a click switches the strike back to distance.'}
        </p>
        {sweep.status === 'ready' ? (
          <SweepGrid
            cells={sweep.data.cells}
            moneynessPct={controls.strike_rule === 'delta' ? Number.NaN : controls.moneyness_pct}
            tenorWeeks={controls.tenor_weeks}
            benchmarkSymbol={sweep.data.benchmark_symbol}
            benchmarkAnnualized={sweep.data.benchmark_annualized}
            modelPricedMaxMoneynessPct={sweep.data.model_priced_max_moneyness_pct}
            onSelect={(m, t) => onChange({ strike_rule: 'moneyness', moneyness_pct: m, tenor_weeks: t })}
          />
        ) : sweep.status === 'error' ? (
          <p className="pl-status pl-status-error" role="alert">
            {sweep.message}
          </p>
        ) : (
          <p className="pl-status" role="status" aria-live="polite">
            {sweep.status === 'no-data'
              ? 'No sweep for this asset and window yet.'
              : 'Loading the strike × tenor sweep…'}
          </p>
        )}
      </section>

      {/* Directly under the headline numbers, never behind a toggle: the size of
          a result's error is part of the result, and a collapsed panel is a
          filed one. */}
      <section className="pl-section">
        {controls.strike_rule === 'delta' && (
          <p className="pl-note" data-testid="accuracy-rule-note">
            Measured on {reference}, the program nearest {controls.moneyness_pct}% below spot and this tenor: the
            error bar is read for a distance, not a delta.
          </p>
        )}
        <AccuracyPanel accuracy={accuracy} />
      </section>

      <section className="pl-section">
        <MemoryTeaser verdict={regimeVerdict} />
      </section>

      <section className="pl-section">
        <div className="pl-section-head">
          <h3>The ledger</h3>
          <button
            type="button"
            className="pl-btn pl-btn-ghost"
            aria-expanded={showLedger}
            onClick={() => setShowLedger((v) => !v)}
          >
            {showLedger ? `Hide ${bt.n_cycles} rolls` : `Show all ${bt.n_cycles} rolls`}
          </button>
        </div>
        <p className="pl-note" style={{ marginBottom: 12 }}>
          Every roll the backtest actually placed. <strong>Fill</strong> is the cash the contracts
          cost; <strong>Net</strong> is measured against the per-roll <strong>Budget</strong>, which
          is the basis every return on this page divides by.
        </p>
        {showLedger && <Ledger cycles={bt.cycles} notional={bt.notional} />}
      </section>
    </>
  )
}
