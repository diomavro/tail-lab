import { useState } from 'react'
import type {
  CadenceResponse,
  PutBacktestResponse,
  RegimeVerdictResponse,
  SweepResponse,
  UniverseMember,
} from '../../../api/client'
import { CadencePanel } from '../CadencePanel'
import { ChartCockpit } from '../ChartCockpit'
import { ConceptInfo } from '../ConceptInfo'
import { CostOverTime } from '../CostOverTime'
import { CyclesBars } from '../CyclesBars'
import { fmtPrice } from '../format'
import { MemoryTeaser } from '../MemoryTeaser'
import type { ResourceState } from '../PutLab'
import { StatBand } from '../StatBand'
import { StrategyTape } from '../StrategyTape'
import { SweepHeatmap } from '../SweepHeatmap'
import type { PutLabControls } from '../types'

// "What would this hedge have done?" -- two always-visible main plots: the
// cockpit-framed strategy tape (with its P&L pane) and the strike x tenor sweep
// heatmap. Everything else (cost, cycles, cadence, the memory-layer verdict)
// collapses behind a "More detail" toggle, hidden by default. Cumulative P&L
// lives ONLY in the tape's P&L pane -- the standalone EquityCurve is gone from
// this view (Portfolio still owns it). Each resource renders on its own
// loading/ready/no-data/error state, so the tape shows the moment the backtest
// resolves even while the sweep is still loading.
export function BacktestView({
  controls,
  backtest,
  sweep,
  cadence,
  regimeVerdict,
  onChange,
  universe,
}: {
  controls: PutLabControls
  backtest: ResourceState<PutBacktestResponse>
  sweep: ResourceState<SweepResponse>
  cadence: ResourceState<CadenceResponse>
  regimeVerdict: RegimeVerdictResponse | null
  onChange: (patch: Partial<PutLabControls>) => void
  universe: UniverseMember[]
}) {
  const [showDetail, setShowDetail] = useState(false)

  // The caption, stats, and tape all need the backtest, so it gates the view.
  if (backtest.status === 'loading') {
    return (
      <p className="putlab-status" role="status" aria-live="polite">
        Running the backtest...
      </p>
    )
  }
  if (backtest.status === 'error') {
    return (
      <p className="putlab-status putlab-status-error" role="alert">
        {backtest.message}
      </p>
    )
  }
  if (backtest.status === 'no-data') {
    return (
      <p className="putlab-status" role="status" aria-live="polite">
        No data yet for this asset and window &mdash; try a shorter lookback or a different asset.
      </p>
    )
  }
  const bt = backtest.data

  return (
    <>
      <p className="strike-caption mono" aria-live="polite">
        {bt.asset.toUpperCase()} {fmtPrice(bt.spot)}
        <span className="lede"> &middot; {controls.moneyness_pct}% OOM &rarr; </span>
        <strong>{fmtPrice(bt.spot * (1 - controls.moneyness_pct / 100))}</strong>
        <span className="lede"> strike </span>
        <span className="strike-dist">
          ({fmtPrice(bt.spot * (controls.moneyness_pct / 100))} below spot)
        </span>
        <ConceptInfo id="oom_put" />
      </p>

      <StatBand backtest={bt} />

      {/* Main plot 1: the strategy tape (keeps its cumulative-P&L pane). */}
      <section className="panel">
        <ChartCockpit controls={controls} onChange={onChange} universe={universe}>
          <StrategyTape
            pricePath={bt.price_path}
            mtmCurve={bt.mtm_curve}
            cycles={bt.cycles}
            annualizedSoFar={bt.annualized_so_far}
            benchmarkAnnualized={bt.benchmark_annualized}
          />
        </ChartCockpit>
      </section>

      {/* Main plot 2: the strike x tenor sweep, full-width so the cells are
          large and the annualized numbers legible. Renders on its own state so
          a still-loading sweep never blocks the tape above. */}
      <section className="panel">
        {sweep.status === 'ready' ? (
          <SweepHeatmap
            cells={sweep.data.cells}
            moneynessPct={controls.moneyness_pct}
            tenorWeeks={controls.tenor_weeks}
            benchmarkSymbol={sweep.data.benchmark_symbol}
            benchmarkAnnualized={sweep.data.benchmark_annualized}
            onSelect={(m, t) => onChange({ moneyness_pct: m, tenor_weeks: t })}
          />
        ) : sweep.status === 'error' ? (
          <p className="putlab-status putlab-status-error" role="alert">
            {sweep.message}
          </p>
        ) : (
          <p className="putlab-status" role="status" aria-live="polite">
            {sweep.status === 'no-data'
              ? 'No sweep for this asset and window yet.'
              : 'Loading the strike × tenor sweep…'}
          </p>
        )}
      </section>

      <button
        type="button"
        className="detail-toggle"
        aria-expanded={showDetail}
        onClick={() => setShowDetail((v) => !v)}
      >
        <span className="detail-toggle-caret" aria-hidden="true">
          {showDetail ? '▾' : '▸'}
        </span>
        {showDetail ? 'Hide detail' : 'More detail'}
      </button>

      {showDetail && (
        <>
          {/* Balanced two-up rows: cost | cycles, then cadence | memory. */}
          <div className="grid2">
            <section className="panel" style={{ marginBottom: 0 }}>
              <CostOverTime cycles={bt.cycles} />
            </section>
            <section className="panel" style={{ marginBottom: 0 }}>
              <CyclesBars cycles={bt.cycles} notional={controls.notional} />
            </section>
          </div>
          <div className="grid2">
            <section className="panel" style={{ marginBottom: 0 }}>
              {cadence.status === 'ready' ? (
                <CadencePanel cadence={cadence.data} />
              ) : (
                <p className="putlab-status" role="status" aria-live="polite">
                  {cadence.status === 'error' ? 'Cadence unavailable.' : 'Loading cadence…'}
                </p>
              )}
            </section>
            <MemoryTeaser verdict={regimeVerdict} />
          </div>
        </>
      )}
    </>
  )
}
