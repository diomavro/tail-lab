# 17. Put Lab is one workspace with one control surface

Date: 2026-08-22

## Status

Accepted

## Context

The Put Lab had grown five tabs, two control surfaces, and a permanently pinned
ranking panel. Four things were wrong with that shape, and they compounded.

**Picking a name and reading its result lived on different tabs.** Screen ranked
the universe; Backtest showed what one name's puts did. Because the two were
split, the fragility ranking had to be pinned *above the tab bar* so a pick made
on one tab could reach the other — roughly 232px of permanently reserved
vertical space on every view, including the ones that had nothing to do with
screening.

**Two control surfaces set the same four parameters.** `QuestionBar` rendered
them as a plain-English sentence on Screen and Portfolio; `ChartCockpit`
rendered them as four rails around the hero chart on Backtest. A reader had to
learn where the controls lived per tab, and the cockpit's spatial arrangement
made the eye hunt in four directions to read one position.

**The shell was a fixed `100dvh` with an inner scroller.** Header, ranking, tab
bar and question bar were all pinned above it, which on a 13" laptop left the
hero chart a few hundred pixels.

**The token layer had collisions.** `--hot` and `--loss` were the same hex, so
"hot cell" and "losing cell" were one colour meaning two things. `--accent` was
also `--warm`, so the brand colour was also the elevated-risk signal. And `--ink`
was the page *background* in light mode while being used as a *text* colour in
two rules, rendering that text near-invisible.

Separately, `fetchMetricScreen` had been in `api/client.ts` since the endpoint
shipped and was never called from anywhere, so `spearman_vs_payoff`,
`lift_vs_baseline` and `combined_max_drawdown` were computed server-side on every
request and discarded.

## Decision

**Screen and Backtest become one Workspace tab.** The fragility ranking is a
one-line strip at the top of that view — top three, clickable, expanding to the
full 13-column table on demand. A click lands the result in the same view, so
nothing needs pinning. A row's headline is `best_annualized`, the argmax of that
name's whole strike × tenor grid, so a click patches `best_moneyness_pct` and
`best_tenor_weeks` too: landing on the previously-selected strike would show a
different number from the one just clicked.

**`ParamRail` is the only control surface,** present on every tab, carrying the
universe picker, the four parameters, and the run's provenance. `QuestionBar` and
`ChartCockpit` are deleted.

**The Screen tab's deeper question gets its own tab.** "Which screen actually
picks winners?" sits *upstream* of the ranking — the ranking assumes these
metrics are worth ranking on, and the bake-off is the test of that assumption.
It is a **Bake-off** tab, and the first caller `fetchMetricScreen` has ever had.
It runs on an explicit button, not on control change: the endpoint runs ~35
backtests server-side.

**The page scrolls normally.** No fixed shell, no inner scroller.

**One token layer, one elevation.** `--ink` is text and `--paper` is ground,
always. Magenta is loss and nothing else; cyan is interactive and nothing else;
process yellow is the elevated-regime fill and is never text. Hierarchy comes
from the serif type scale and whitespace — no `box-shadow` on any surface. The
sheet (`paper` / `plate`) is a `data-theme` attribute persisted to
`localStorage`, chosen by the reader rather than inferred from the OS.

Everything stays scoped under `.putlab-root`, including a `text-align: left`
reset — `index.css` sets `#root { text-align: center }` and nothing under the
workspace had ever reset it.

## Consequences

- The strike × tenor sweep is **three bands**, not a continuous ramp: lost money
  / made money but under the benchmark / beat the benchmark. A cell that made
  money and lost to the index is a different *kind* of outcome from one that lost
  money, not a darker shade of it — a ramp cannot say that, and the ramp's
  top colour was the loss colour anyway.
- Five `RankedAsset` fields (`downside_beta`, `co_skewness`, `co_kurtosis`,
  `tail_beta`, `downside_capture`) and four `PutBacktestCycle` fields (`sigma`,
  `contracts`, `premium`, `cost`) now reach the screen. They were all being
  fetched already.
- The roll ledger prints **Fill** and **Budget** as separate columns. The old
  single "Cost" column held the cash filled while `Net` was measured against the
  per-roll budget, so subtracting the two visible columns gave the wrong answer.
- Tape markers moved to `payoff > notional`, the same basis as `hit_rate`. They
  had been on `payoff > 0`, so the dots disagreed with the stat card above them.
- Deleted: `QuestionBar`, `ChartCockpit`, `SweepHeatmap`, `StatBand`,
  `CadencePanel`, `CyclesBars`, `CostOverTime`, `RegimePanel`, `VixStretchTile`,
  `views/ScreenView`, `views/BacktestView`, `views/LearnView`, and `makeRamp` /
  `normSigned` / the imperative SVG helpers in `format.ts`. The VIX-stretch
  reading itself is not lost — it moved into the Regime view, where the old
  stylesheet's orphaned `.vix-panel .tile` rule shows it used to be.
- `frontend/e2e/` becomes a **hermetic** suite: Playwright starts the Vite dev
  server and every `/api/**` call is answered from `e2e/fixtures/`. That is what
  makes it possible to assert on both branches of the bake-off verdict, on a
  roll that returned real intrinsic value below its budget, and on all three
  sweep bands in one run — none of which a live lake can be relied on to
  produce. One opt-in spec (`smoke-live.spec.ts`, `PLAYWRIGHT_LIVE=1`) talks to
  a real backend to catch a moved route or a renamed field.
- `/api/leaderboard` (the *sensitivity* leaderboard, unrelated to
  `/api/putlab/leaderboard`) now has no caller. Its `LeaderboardTile.tsx` was
  already unreferenced before this change; it is left in the tree for a separate
  decision about whether that endpoint still earns its place.
