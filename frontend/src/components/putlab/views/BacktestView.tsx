import type { UniverseMember } from '../../../api/client'
import { CadencePanel } from '../CadencePanel'
import { ChartCockpit } from '../ChartCockpit'
import { ConceptInfo } from '../ConceptInfo'
import { CostOverTime } from '../CostOverTime'
import { CyclesBars } from '../CyclesBars'
import { EquityCurve } from '../EquityCurve'
import { fmtPrice } from '../format'
import { MemoryTeaser } from '../MemoryTeaser'
import type { BacktestState } from '../PutLab'
import { StatBand } from '../StatBand'
import { StrategyTape } from '../StrategyTape'
import { SweepHeatmap } from '../SweepHeatmap'
import type { PutLabControls } from '../types'

// "What would this hedge have done?" -- the single-name backtest bundle laid
// out compressed: headline stats, the strategy tape, then a two-column grid
// (equity | cycles, then sweep | cadence+verdict) so it fits with little
// scroll. Loading/no-data/error states are handled here.
export function BacktestView({
  controls,
  state,
  onChange,
  universe,
}: {
  controls: PutLabControls
  state: BacktestState
  onChange: (patch: Partial<PutLabControls>) => void
  universe: UniverseMember[]
}) {
  if (state.status === 'loading') {
    return (
      <p className="putlab-status" role="status" aria-live="polite">
        Running the backtest...
      </p>
    )
  }
  if (state.status === 'error') {
    return (
      <p className="putlab-status putlab-status-error" role="alert">
        {state.message}
      </p>
    )
  }
  if (state.status === 'no-data') {
    return (
      <p className="putlab-status" role="status" aria-live="polite">
        No data yet for this asset and window &mdash; try a shorter lookback or a different asset.
      </p>
    )
  }

  return (
    <>
      <p className="strike-caption mono" aria-live="polite">
        {state.backtest.asset.toUpperCase()} {fmtPrice(state.backtest.spot)}
        <span className="lede"> &middot; {controls.moneyness_pct}% OOM &rarr; </span>
        <strong>{fmtPrice(state.backtest.spot * (1 - controls.moneyness_pct / 100))}</strong>
        <span className="lede"> strike </span>
        <span className="strike-dist">
          ({fmtPrice(state.backtest.spot * (controls.moneyness_pct / 100))} below spot)
        </span>
        <ConceptInfo id="oom_put" />
      </p>

      <StatBand backtest={state.backtest} />

      <section className="panel">
        <ChartCockpit controls={controls} onChange={onChange} universe={universe}>
          <StrategyTape
            pricePath={state.backtest.price_path}
            mtmCurve={state.backtest.mtm_curve}
            cycles={state.backtest.cycles}
          />
        </ChartCockpit>
      </section>

      {/* Balanced two-up rows: each pairs charts of similar height, so no
          column leaves a tall gap beside a short one — keeps the view dense. */}
      <div className="grid2">
        <section className="panel" style={{ marginBottom: 0 }}>
          <EquityCurve
            equityCurve={state.backtest.equity_curve}
            mtmCurve={state.backtest.mtm_curve}
            cycles={state.backtest.cycles}
          />
        </section>
        <section className="panel" style={{ marginBottom: 0 }}>
          <CostOverTime cycles={state.backtest.cycles} />
        </section>
      </div>

      <div className="grid2">
        <section className="panel" style={{ marginBottom: 0 }}>
          <CyclesBars cycles={state.backtest.cycles} notional={controls.notional} />
        </section>
        <section className="panel" style={{ marginBottom: 0 }}>
          <SweepHeatmap
            cells={state.sweep.cells}
            moneynessPct={controls.moneyness_pct}
            tenorWeeks={controls.tenor_weeks}
            onSelect={(m, t) => onChange({ moneyness_pct: m, tenor_weeks: t })}
          />
        </section>
      </div>

      <div className="grid2">
        <section className="panel" style={{ marginBottom: 0 }}>
          <CadencePanel cadence={state.cadence} />
        </section>
        <MemoryTeaser verdict={state.regimeVerdict} />
      </div>
    </>
  )
}
