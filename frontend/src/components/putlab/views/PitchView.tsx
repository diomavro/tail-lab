import { EVIDENCE as E, LIVE, gateLabel, ruleLabel, ruleShort, type Standalone } from '../../../content/pitch'
import { ConceptInfo } from '../ConceptInfo'
import { fmtFixed } from '../format'

/* The Pitch: does the put book have a genuine edge? (docs/adr/0021, 2026-10-27
 * amendment.) Verdict first, then the evidence, then how to kill or rescue it.
 *
 * Every study result is read from content/pitch_evidence.json, which
 * `make pitch-evidence` writes from the lake. Only the study's design is
 * written out in words (its four depths, three structures, six signals and
 * 35 filters, as scripts/pitch_evidence.py defines them). The few live-app
 * readings come from content/pitch.ts's LIVE block, dated and with the URL
 * that reproduces each.
 */

const SPY = E.SPY
const QQQ = E.QQQ
const T = E.timing
/** The growth comparison's spend per roll taken, as section 2 prints budgets. */
const timingBudget = `${(T.budget * 100).toFixed(2)}%`

/** Signed percent with a true minus, at `digits` decimals. */
function pct(x: number, digits = 1): string {
  return `${x < 0 ? '−' : x > 0 ? '+' : ''}${Math.abs(x * 100).toFixed(digits)}%`
}
/** Signed percentage points per year. */
function pp(x: number | null, digits = 2): string {
  if (x === null) return '—'
  return `${x < 0 ? '−' : '+'}${Math.abs(x * 100).toFixed(digits)}pp/yr`
}
function bp(x: number): string {
  return `${x < 0 ? '−' : '+'}${Math.abs(x * 10000).toFixed(1)}bp/yr`
}
function ci(c: readonly number[]): string {
  return `[${fmtFixed(c[0]!)}, ${fmtFixed(c[1]!)}]`
}
/** fmtFixed, or a dash where the generator wrote null (no roll taken). */
function num(x: number | null, digits = 2): string {
  return x === null ? '—' : fmtFixed(x, digits)
}
/** A signed difference in terminal wealth, at 5 dp so a near-tie shows as one. */
function signed5(x: number): string {
  return `${x < 0 ? '−' : '+'}${Math.abs(x).toFixed(5)}`
}
/** Timing signals read from the close the put is bought at (pitch_evidence.py). */
function sameClose(gate: string): boolean {
  return /^(put_rich|put_cost|alpha)/.test(gate)
}
function plural(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`
}
/** The long rule label, swapped for a short one on a phone (putlab.css). */
/** `budget`: on a phone the Budget/roll column is off-screen, so the short
 *  label carries it -- otherwise one rule's books read as three identical rows. */
function RuleCell({ rule, budget }: { rule: string; budget?: number }) {
  return (
    <td>
      <span className="pl-pitch-long">{ruleLabel(rule)}</span>
      <span className="pl-pitch-short">
        {ruleShort(rule)}
        {budget === undefined ? '' : ` @${(budget * 100).toFixed(2)}%`}
      </span>
    </td>
  )
}

const spyRules = Object.entries(SPY.standalone)
const spyNegative = spyRules.filter(([, s]) => s.mean_roi < 0).length
const spyCiBelowZero = spyRules.filter(([, s]) => s.mean_roi_ci95[1]! < 0).length
const qqqNegative = Object.values(QQQ.standalone).filter((s) => s.mean_roi < 0).length
const spyPositive = spyRules.filter(([, s]) => s.mean_roi >= 0)
const beaters = SPY.hedge.configs.filter((c) => c.drop_best)
const cutDd = SPY.hedge.configs.filter((c) => c.max_dd > SPY.hedge.unhedged_max_dd)
const cutDdAndLost = cutDd.filter((c) => c.edge <= 0).length
const deeperDd = SPY.hedge.configs.filter((c) => c.max_dd < SPY.hedge.unhedged_max_dd)
/** A put marked at more than this multiple of its cost on the drawdown's peak
 *  day makes the peak a paper gain, which the drawdown then gives back. */
const PAPER_PEAK = 2
/** Deeper drawdowns by what their peak was made of, read from the roll open
 *  on that day: a put marked far above its cost (a paper gain later given
 *  back), or no such put (premium bled). Not by calendar year. */
const ddPaper = deeperDd.filter((c) => (c.max_dd_peak_put?.mark_multiple ?? 0) > PAPER_PEAK)
const ddBled = deeperDd.filter((c) => !ddPaper.includes(c))
const fate = (x: number) => (x === 0 ? 'expired worthless' : `ended at ${fmtFixed(x, 2)}× its cost`)
const budgetsPerRule = new Set(SPY.hedge.configs.map((c) => c.budget)).size
const hedgeRules = [...new Set(SPY.hedge.configs.map((c) => c.rule))]
const rulesAlwaysLose = hedgeRules.filter((r) => SPY.hedge.configs.filter((c) => c.rule === r).every((c) => c.edge <= 0))
const rulesSometimesBeat = hedgeRules
  .filter((r) => !rulesAlwaysLose.includes(r))
  .map((r) => ({ rule: r, n: SPY.hedge.configs.filter((c) => c.rule === r && c.edge > 0).length }))
const lastExits = spyRules.map(([, s]) => s.last_exit).sort()
/** The dividend-inclusive yardstick for Cboe's total-return indices: SPX
 *  price growth plus the same flat yield the study accrues (log growth adds). */
const spxWithDiv = E.cboe.SPX.growth + E.dividend_yield_assumed
/** True when every config that beat unhedged did so on the same roll and
 *  falls behind without it -- the claim the verdict makes in words. */
const oneRoll =
  beaters.length > 0 &&
  beaters.every((c) => c.drop_best!.best_entry === beaters[0]!.drop_best!.best_entry && c.drop_best!.edge_without_best < 0)
const verdictChanges = LIVE.spy_verdicts.filter((v, i, a) => i > 0 && v.verdict !== a[i - 1]!.verdict).length
const periodsWithWinners = (rule: string) =>
  Object.values(SPY.standalone[rule]!.by_period).filter((v) => v.hits > 0).length
const vxth = LIVE.book.filter((b) => b.program === 'VXTH')
const survivors = T.bh.survivors.length
const sel = T.roi_diff
const selDg = T.dg
const C08 = E.crisis_2008
const c08Rules = (['5pct', '10pct', '15pct', '20pct'] as const).map((k) => [k, C08[k]] as const)
const c08Beaters = c08Rules.flatMap(([k, r]) =>
  r.books.filter((b) => b.terminal_without_best !== undefined).map((b) => ({ k, r, b })),
)
/** True when every 2008-09 book that ended ahead falls behind unhedged
 *  without its best month, and that month is the same for all of them. */
const c08OneMonth =
  c08Beaters.length > 0 &&
  c08Beaters.every((x) => x.b.terminal_without_best! < x.r.unhedged.terminal && x.r.best_entry === c08Beaters[0]!.r.best_entry)
const lastExpiry = LIVE.spy_verdicts.map((v) => v.last_expiry).reduce((a, b) => (b > a ? b : a))
const bootReps = Math.round(1 / T.bh.p_floor - 1)
const heldRules = Object.entries(T.bh.oracle).filter(([r]) => r.includes('_4w_hold'))
const soldRules = Object.entries(T.bh.oracle).filter(([r]) => !r.includes('_4w_hold'))
const notCrisisSmallest = T.bh.smallest_p.filter((x) => x.gate === 'regime=not_crisis')
const notCrisisEverySold =
  notCrisisSmallest.length === T.bh.smallest_p.length &&
  new Set(notCrisisSmallest.map((x) => x.rule)).size === soldRules.length
const qqq20 = QQQ.standalone['20pct_4w_hold']!
const qqq15 = QQQ.standalone['15pct_4w_hold']!
/** "a return of +227.5 on its premium (228.5× the premium back)": the page
 *  defines return on premium as payback ÷ cost − 1, so the multiple is +1. */
const backX = (roi: number) => `a return of +${fmtFixed(roi, 1)} on its premium (${fmtFixed(roi + 1, 1)}× the premium back)`
const ovl = T.bh.smallest_p_overlap
const spreadOff = C08.spread_guard_off
const c08Gaps = C08.months_without_roll_date
const ev20 = SPY.every_day['20pct_4w_hold']!
const ev15 = SPY.every_day['15pct_4w_hold']!
const qev20 = QQQ.every_day['20pct_4w_hold']!
const qev15 = QQQ.every_day['15pct_4w_hold']!
/** With the spread cap lifted, does every book that ends ahead still fall
 *  behind unhedged without its best roll? */
const offOneMonth = Object.values(spreadOff.depths).every((d) =>
  d.books.every((b) => b.terminal_without_best === undefined || b.terminal_without_best < d.unhedged.terminal),
)
const yearsSpan = Number(E.panels.SPY.last.slice(0, 4)) - Number(E.panels.SPY.first.slice(0, 4)) + 1
const maxRolls = Math.max(...spyRules.map(([, s]) => s.n_rolls))
const soldSpy = spyRules.filter(([r]) => !r.includes('_4w_hold'))
const soldStats = soldSpy.map(([, s]) => s)
const intrinsicExits = soldStats.reduce((n, s) => n + s.exits_at_intrinsic_itm, 0)
/** Of those, the exits with no quote at all: strike above the panel's band. */
const beyondPanel = soldStats.reduce((n, s) => n + s.exits_beyond_panel, 0)
const bestBeyond = soldSpy.filter(([, s]) => s.best_roll_beyond_panel).map(([r]) => ruleLabel(r))
const soldRolls = soldStats.reduce((n, s) => n + s.n_rolls, 0)
/** Premium a year at each budget, as a range over the rules. */
const premiumAtBudget = [...new Set(SPY.hedge.configs.map((c) => c.budget))].map((b) => {
  const xs = SPY.hedge.configs.filter((c) => c.budget === b).map((c) => c.premium_pct_per_year)
  return `${Math.min(...xs).toFixed(1)}–${Math.max(...xs).toFixed(1)}%`
})
const alphaRefused = [1 - T.alpha_coverage[1]!, 1 - T.alpha_coverage[0]!].map((x) => Math.round(x * 100))

function StandaloneRow({ rule, s }: { rule: string; s: Standalone }) {
  return (
    <tr>
      <RuleCell rule={rule} />
      <td className="num">{s.n_rolls}</td>
      <td className={`num ${s.mean_roi < 0 ? 'pl-neg' : ''}`}>{fmtFixed(s.mean_roi)}</td>
      <td className="num nowrap">{ci(s.mean_roi_ci95)}</td>
      <td className="num">{s.hits}</td>
      <td className="num nowrap">
        {fmtFixed(s.best_roi, 1)} <span className="dimmer">{s.best_entry}</span>
      </td>
      <td className="num">{s.median_ask_pct_spot.toFixed(2)}%</td>
      <td className="num">{s.fill_skips}</td>
    </tr>
  )
}

export function PitchView() {
  return (
    <article className="pl-pitch">
      <p className="pl-kicker">The Pitch · evidence as of {E.as_of}</p>
      <h2 className="pl-view-title">No demonstrable edge.</h2>
      <p className="pl-view-lede" data-testid="pitch-verdict">
        On real bid/ask quotes for SPY ({E.panels.SPY.first} to {E.panels.SPY.last}), buying out-of-the-money
        puts lost money in {spyNegative} of {spyRules.length} rules; as a hedge on the index,{' '}
        {rulesAlwaysLose.length} of {hedgeRules.length} rules lowered long-run growth at every budget tried
        {rulesSometimesBeat.map((x) => (
          <span key={x.rule}>
            {' '}
            (the other, {ruleLabel(x.rule)}, beat it at {x.n} of {budgetsPerRule} budgets
            {oneRoll ? ', on a single roll' : ''})
          </span>
        ))}
        ; and of{' '}
        {T.bh.tested} rule-and-filter combinations tested for timing, {survivors} survived a multiple-testing correction out of
        sample. There is
        no strategy here yet, and Putlab does not produce a daily trading signal.
      </p>

      <section className="pl-section" id="pitch-verdict-detail">
        <h3>The verdict, in four lines</h3>
        <ol className="pl-pitch-list">
          <li>
            <strong>As a standalone trade: no edge.</strong> {spyNegative} of {spyRules.length} SPY rules have a
            negative mean return per dollar of premium, and for {spyCiBelowZero} the whole 95% interval sits below
            zero. {spyPositive.map(([r, s]) => (
              <span key={r}>
                The exception, {ruleLabel(r)}, averages {fmtFixed(s.mean_roi)} only because{' '}
                {s.hits === 1 ? 'its single winning roll' : `the best of its ${s.hits} winning rolls`} in {s.n_rolls} made{' '}
                {backX(s.best_roi)}, bought {s.best_entry}; its interval is {ci(s.mean_roi_ci95)}.{' '}
              </span>
            ))}
            QQQ: {qqqNegative} of {Object.keys(QQQ.standalone).length} rules negative; its monthly 20% OOM
            rule had {plural(qqq20.hits, 'winning roll', 'winning rolls')} (its calendar missed the 2020 crash), and only{' '}
            {qev20.phases_positive} of {qev20.phases} phasings of that rule come out positive (
            <a href="#pitch-standalone">section 1</a>).
          </li>
          <li>
            <strong>As a hedge: insurance, not an edge.</strong> {rulesAlwaysLose.length} of {hedgeRules.length} SPY
            rules grow slower than SPY alone at every budget. Counted by book (each rule at {budgetsPerRule} budgets;
            the books of one rule share its rolls, so they are not independent evidence): {cutDd.length} of{' '}
            {SPY.hedge.configs.length} SPY books cut the worst drawdown, {cutDdAndLost} of them at a cost in growth.{' '}
            {SPY.hedge.n_beat} grew faster than holding SPY alone
            {oneRoll && (
              <>
                , and {beaters.length === 1 ? 'it rests' : beaters.length === 2 ? 'both rest' : `all ${beaters.length} rest`} on a single roll bought{' '}
                {beaters[0]!.drop_best!.best_entry}: drop it and{' '}
                {beaters.map((c) => `${pp(c.edge)} becomes ${pp(c.drop_best!.edge_without_best)}`).join(', ')}
              </>
            )}
            . QQQ: {QQQ.hedge.n_beat} of {QQQ.hedge.configs.length}. In 2008–09, on a separate monthly dataset,{' '}
            {C08.n_beat} of {C08.configs} books ended ahead of unhedged
            {c08OneMonth && <>, every one on the roll bought {c08Beaters[0]!.r.best_entry} and behind without it</>} (
            <a href="#pitch-2008">section 2b</a>).
          </li>
          <li>
            <strong>Timing: nothing survives.</strong> The best of the {sel.eligible} eligible rule-and-filter combinations (of {T.combinations})
            chosen on {T.is_period[0]!.slice(0, 4)}–{T.is_period[1]!.slice(0, 4)} improved the out-of-sample mean
            return by {num(sel.oos.roi_diff)} per dollar of premium (95% interval {ci(sel.oos_ci95)}, one-sided p ={' '}
            {sel.oos_p.toFixed(3)}). Of the {sel.is_positive} eligible combinations that improved in sample,{' '}
            {sel.is_positive_oos_positive} ({Math.round((sel.is_positive_oos_positive / sel.is_positive) * 100)}%) also
            improved out of sample; {sel.null_at_least_observed} of {sel.null_rotations} rotations of the
            out-of-sample outcomes do as well (<a href="#pitch-timing">section 3</a>). Weak evidence that in-sample
            improvement carries forward a little, not a filter that works.
          </li>
          <li>
            <strong>Daily signal: none.</strong> The app re-scores the same in-sample backtests every day. Its one
            order-shaped output always says “buy”, is priced by a model, and its verdict on SPY changed {verdictChanges} times
            in {LIVE.spy_verdicts.length} sessions although every roll behind it had expired by {lastExpiry} (below).
          </li>
        </ol>
        <p className="pl-note">
          “No demonstrable edge” is not “proven useless”. The sample holds {SPY.episodes_10pct.length} distinct
          SPY declines of 10% or more and one that paid a far-OOM put spectacularly (the roll bought{' '}
          {SPY.standalone['20pct_4w_hold']!.best_entry}). {yearsSpan} years of quotes cannot tell a strategy that pays once a decade from one that never pays. What would decide it is
          in <a href="#pitch-next">the last section</a>.
        </p>
      </section>

      <section className="pl-section" id="pitch-terms">
        <h3>Terms used below</h3>
        <ul className="pl-pitch-list">
          <li>
            <strong>Out-of-the-money (OOM) put, depth.</strong> <ConceptInfo id="oom_put" /> How far below spot the strike sits: a 10%
            OOM put on SPY at 500 has a strike near 450. Moneyness is strike ÷ spot, so 0.90 is 10% OOM. Here the
            strike is always picked by that distance. (The Workspace can instead pick the strike whose
            Black–Scholes delta, computed at the last 20 days’ realised volatility, hits a target, docs/adr/0029;
            this study does not.) The one difference from the glossary entry: nothing here is priced by a model.
            Every premium is a real ask.
          </li>
          <li>
            <strong>Roll.</strong> <ConceptInfo id="rolling" /> One put bought, then held to expiry or sold; the
            next is bought the session the last one ends.
          </li>
          <li>
            <strong>Return on premium.</strong> <ConceptInfo id="roi_on_premium" /> As in the glossary, but per roll:
            what the roll paid back ÷ what it cost − 1, after the ${E.commission_per_contract.toFixed(2)} commission,
            with the real ask paid. (The app’s figure is also net of brokerage, but its brokerage is commission plus
            a modelled half-spread on a model price.) For a held put, “paid back” is its value at expiry; for a sold
            put, the bid it was sold at, or its intrinsic value when there was no bid to sell at (method, Fills).
            −1.00 means the premium was lost; +2.00 means three times the premium came back.
          </li>
          <li>
            <strong>Time-average growth.</strong> <ConceptInfo id="time_average_growth" /> The annualised log
            return of a whole book: what compounds, and the yardstick a hedge is sized on (docs/END_STATE.md §4 Q8).
          </li>
          <li>
            <strong>VIX regimes.</strong> <ConceptInfo id="regime" /> The base thresholds are 17 (calm below,
            elevated above) and 28 (crisis above), but each close’s label carries a one-point hysteresis band
            (contracts/regime.py), labelled causally from the VIX history’s first close in 1990. The label turns
            elevated at a close of 18 or more and goes back to calm only below 16; it turns crisis at 29 or more
            and leaves crisis only below 27. So a 28.5 close after an elevated day is still elevated, and a 27.5
            close after a crisis day is still crisis.
          </li>
          <li>
            <strong>Winners.</strong> Rolls whose return on premium, after commission, is above zero. The word
            means only that here; a combination chosen by a search is called the <em>pick</em>.
          </li>
        </ul>
      </section>

      <section className="pl-section" id="pitch-daily">
        <h3>Does Putlab produce a daily signal?</h3>
        <p className="pl-note">
          No. Here is everything that runs on a schedule, and what each one produces. (Read from the repo at{' '}
          {LIVE.code_sha}; live readings on {LIVE.read_on}.)
        </p>
        <div className="pl-scroll">
          <table className="pl-table pl-pitch-stack pl-pitch-sched" data-testid="pitch-schedule">
            <thead>
              <tr>
                <th>What runs</th>
                <th>When</th>
                <th>What it produces</th>
                <th>A decision?</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>Chain sweep (daily-chain-snapshot.yml)</td>
                <td>21:30 UTC weekdays, catch-up 05:00</td>
                <td>Cboe put quotes for 24 names, stored</td>
                <td>Data only</td>
              </tr>
              <tr>
                <td>Lake refresh (local timer, scripts/local_daily_refresh.sh)</td>
                <td>Daily</td>
                <td>VIX, Cboe indices, credit, rates; prices for the 24 chain names only</td>
                <td>Data only</td>
              </tr>
              <tr>
                <td>Verdict sweep (daily-verdict-sweep.yml)</td>
                <td>Daily: scheduled 06:30 UTC; GitHub started it {LIVE.verdict_sweep_started} on 19–26 October</td>
                <td>For every name × 5% and 10% OOM, 4-week puts, 4-year window: re-runs the backtest and files a verdict</td>
                <td>No — re-scores history</td>
              </tr>
              <tr>
                <td>Screen and roll schedule (/api/putlab/leaderboard, /roll-schedule)</td>
                <td>On request; Screen pre-warmed 00:01 UTC</td>
                <td>The 10 names with the best in-sample annualised return, as “legs” to buy</td>
                <td>Order-shaped, but see below</td>
              </tr>
              <tr>
                <td>Book (/api/putlab/hedge-overlay)</td>
                <td>On request</td>
                <td>How much of a Cboe hedge program to hold</td>
                <td>No — sizes a whole program, not a trade. Recomputed on request from daily Cboe index closes, rebalanced monthly, so its full-history margins move a little each day</td>
              </tr>
            </tbody>
          </table>
        </div>
        <p className="pl-note">
          <strong>Why the roll schedule is not a signal.</strong> Its only action is{' '}
          <code>BUY_PUT</code> (research/backtest/roll_schedule.py, <code>action: Literal["BUY_PUT"]</code>), so it
          cannot say “stand aside”. It ranks names by the best in-sample annualised return with no filter on verdict
          or sign. Its premiums are Black–Scholes prices at 20-day realised volatility, not quotes. On{' '}
          {LIVE.read_on}:{' '}
          {LIVE.roll_schedule_legs.map((l) => (
            <span key={l.asset}>
              leg #{l.rank} {l.asset} ({l.rule}) expected {backX(l.expected_roi)} at a model price of $
              {l.model_premium < 0.01 ? l.model_premium.toFixed(4) : l.model_premium.toFixed(2)} a share, while the market quoted {l.market};{' '}
            </span>
          ))}
          of the ten legs, the collected chain could price {LIVE.roll_schedule_quote_status.quoted}, found{' '}
          {LIVE.roll_schedule_quote_status.illiquid} illiquid, and had no quotes for{' '}
          {LIVE.roll_schedule_quote_status.not_collected}. The schedule labels itself “a screen, not advice, and not
          a prediction”.
        </p>
        <p className="pl-note">
          <strong>Why the daily verdicts move.</strong> A verdict is <code>confirmed</code> when the backtest’s
          return on premium is positive in two or more VIX regimes, <code>regime_only</code> in exactly one,{' '}
          <code>failed</code> in none (contracts/hypothesis.py, <code>verdict_from_passing_regimes</code>). No
          significance test, no minimum sample: the crisis slice below holds 1 or 2 rolls. SPY, 5% OOM, 4 weeks,
          4-year window, by as-of date (GET /api/putlab/regime-verdict?asset=spy&amp;moneyness_pct=5&amp;as_of=…):
        </p>
        <p className="pl-pitch-scrollhint">The table scrolls sideways →</p>
        <div className="pl-scroll">
          <table className="pl-table" data-testid="pitch-flips">
            <thead>
              <tr>
                <th>As of</th>
                <th>Verdict</th>
                <th className="num">Calm: return, pooled</th>
                <th className="num">Elevated: return, pooled</th>
                <th className="num">Crisis rolls</th>
                <th>First entry → last expiry</th>
              </tr>
            </thead>
            <tbody>
              {LIVE.spy_verdicts.map((v) => (
                <tr key={v.as_of}>
                  <td>{v.as_of}</td>
                  <td>{v.verdict}</td>
                  <td className="num">{fmtFixed(v.calm)}</td>
                  <td className="num">{fmtFixed(v.elevated)}</td>
                  <td className="num">{v.crisis_n}</td>
                  <td className="nowrap">
                    {v.first_entry} → {v.last_expiry}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="pl-note">
          The two return columns are the app’s figure, not the per-roll mean defined in Terms: each regime’s total
          net P&amp;L over its total premium (regime_verdict.py), net of the app’s modelled brokerage, which is why
          one can fall below −1.00. Every run’s last roll expired by {lastExpiry}, before the first date in the table, so no price from these
          sessions is in any of them (GET /api/putlab/backtest, same parameters). What moved is the four-year
          window’s start, and with it the whole monthly roll calendar: which roll happens to be open across a past
          drop. On {LIVE.spy_verdicts[LIVE.spy_verdicts.length - 1]!.as_of} the first roll moved to{' '}
          {LIVE.spy_verdicts[LIVE.spy_verdicts.length - 1]!.first_entry} and the calm-regime return went from{' '}
          {fmtFixed(LIVE.spy_verdicts[LIVE.spy_verdicts.length - 2]!.calm)} to{' '}
          {fmtFixed(LIVE.spy_verdicts[LIVE.spy_verdicts.length - 1]!.calm)}.
        </p>
      </section>

      <section className="pl-section" id="pitch-data">
        <h3>The data</h3>
        <p className="pl-note">
          The study below uses only real end-of-day quotes. The platform’s own Workspace, Screen and Bake-off do
          not: they price every option with a model. The difference matters, so both are listed.
        </p>
        <div className="pl-scroll">
          <table className="pl-table pl-pitch-stack" data-testid="pitch-data">
            <thead>
              <tr>
                <th>Dataset (lake snapshot)</th>
                <th>Span</th>
                <th>What it is</th>
                <th>Known gaps</th>
              </tr>
            </thead>
            <tbody>
              {(['SPY', 'QQQ'] as const).map((s) => {
                const p = E.panels[s]
                return (
                  <tr key={s}>
                    <td className="pl-pitch-id">{E.snapshots[`optionsdx_quotes_${s.toLowerCase()}`]}</td>
                    <td className="nowrap">
                      {p.first} → {p.last}
                    </td>
                    <td>
                      optionsDX end-of-day {s} puts, bid and ask, strike ÷ spot 0.60–1.02, 0–120 days to expiry.{' '}
                      {p.rows.toLocaleString('en-US')} rows, {p.sessions.toLocaleString('en-US')} sessions. Licence:
                      no redistribution — only aggregates leave the lake.
                    </td>
                    <td>
                      Against the exchange’s session calendar (the days Cboe printed an SPX close):{' '}
                      {p.missing_trading_days} trading days missing; {p.sessions_on_closed_days.length} sessions dated on
                      days the exchange was shut ({p.sessions_on_closed_days.slice(0, 3).join(', ')}, …).{' '}
                      {p.closed_day_then_next_missing === 0
                        ? 'The next trading day is present after every one of them, so they are extra rows, not quotes dated a session early.'
                        : `For ${p.closed_day_then_next_missing} of them the next trading day is missing, so those look like quotes dated a session early.`}{' '}
                      The study uses the dates as given:{' '}
                      {E[s].rolls_on_closed_days.touching.toLocaleString('en-US')} of{' '}
                      {E[s].rolls_on_closed_days.rolls.toLocaleString('en-US')} rolls enter or exit on
                      one. A missing day is no session: nothing enters or is marked on it. No 2008: the panel starts in{' '}
                      {p.first.slice(0, 4)}.
                    </td>
                  </tr>
                )
              })}
              <tr>
                <td className="pl-pitch-id">{E.snapshots.option_quotes}</td>
                <td className="nowrap">2008-01 → 2025-11</td>
                <td>
                  SPY puts on monthly roll dates only ({C08.roll_dates} of them in 2008–09), the nearest listed expiry{' '}
                  {C08.nearest_dte[0]}–{C08.nearest_dte[1]} days out ({C08.dates_with_two_expiries.length} dates also
                  list a quarterly, not used); no daily quotes and no expiry-day spot. Used only for the 2008–09 check
                  (section 2b)
                </td>
                <td>
                  Licence unresolved: research use only.{' '}
                  {c08Gaps.length === 0
                    ? 'Every 2008–09 month has a roll date'
                    : `No roll date in ${c08Gaps.join(', ')}; no put held ${C08.unhedged_spans.map((u) => `${u.expiry} → ${u.next_entry}`).join(', ')}`}
                </td>
              </tr>
              <tr>
                <td className="pl-pitch-id">{E.snapshots.vix}</td>
                <td>1990 → today</td>
                <td>Cboe VIX close; the regime label (crisis entered at a close of 29, left below 27; see Terms) and the timing filters</td>
                <td>—</td>
              </tr>
              <tr>
                <td className="pl-pitch-id">{E.snapshots.rates}</td>
                <td>DGS3MO 1981 →</td>
                <td>FRED 3-month bill, for implied vol: at each entry, the newest observation FRED had published before that day</td>
                <td>Vintages begin 2005-06</td>
              </tr>
              <tr>
                <td className="pl-pitch-id">{E.snapshots.cboe_strategy}</td>
                <td>1986 → today</td>
                <td>Cboe SPX, and three hedge-program indices Cboe prices from real trades: PPUT (S&amp;P 500 plus a monthly 5% OOM put), PPUT3M (a quarterly 10% OOM put), VXTH (monthly VIX calls); SPX also serves as the exchange session calendar</td>
                <td>Benchmarks, not tradeable</td>
              </tr>
              <tr>
                <td>tiingo_eod (dividends)</td>
                <td>—</td>
                <td>Not in the lake. Every comparison here accrues a flat {pct(E.dividend_yield_assumed)} a year</td>
                <td>Missing</td>
              </tr>
              <tr>
                <td>Workspace / Screen / Bake-off</td>
                <td>Last 4 years by default</td>
                <td>
                  Black–Scholes at 20-day realised vol (clamped 6%–200%), flat 4% rate, q = 0 for all 70 names, no
                  skew. It priced a calm-market 5% OOM SPY put at about $0.03 against about $1 in the market
                  (docs/adr/0027)
                </td>
                <td>Not quotes: the verdicts they print are not evidence</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <section className="pl-section" id="pitch-method">
        <h3>The method</h3>
        <ol className="pl-pitch-list">
          <li>
            <strong>Fills.</strong> Every trade goes through the platform’s own quote guards
            (research/backtest/quote_fills.py, <code>OptionsDxQuoteSource</code>): the exact session only, the
            nearest listed expiry on or after the target, a listed strike within 1 percentage point of the requested
            distance from spot, bid above zero and spread at most 50% of mid. Buys pay the <strong>ask</strong>; sales get the{' '}
            <strong>bid</strong>. When there is no exit bid, a sale is credited the put’s intrinsic value (strike −
            spot, what exercising the American put pays; nothing if it is out of the money): that happened on{' '}
            {intrinsicExits} in-the-money exits of {soldRolls.toLocaleString('en-US')} sold rolls. {beyondPanel} of
            those are not refused bids: the panel keeps only strikes 0.60–1.02 × spot, so a put more than 2% in the
            money at exit has no quote at all. Intrinsic leaves out the time value such a put still has with 4 or 8
            weeks to run, and it happens only after spot has fallen about 6% or more since entry (the shallowest
            strike is within a point of 5% below spot, and the exit spot is over 2% below that strike)
            {bestBeyond.length > 0 ? `, the best roll of ${bestBeyond.join('; ')} among them` : ''}. So sold-rule
            returns, winners and hedge growth are biased <em>against</em> the put, by an amount the data cannot
            measure (there is no quote to measure it with); the same cut marks such a put at intrinsic on a hedge
            book’s daily path. Commission ${E.commission_per_contract.toFixed(2)} a contract on each, none at
            expiry. A refused fill tries the next session and is counted. No option is priced by a model.
          </li>
          <li>
            <strong>Twelve rules.</strong> Four depths (5, 10, 15, 20% below spot) × three structures: a 4-week put
            held to expiry; an 8-week put sold after 4 weeks; a 12-week put sold after 4 weeks. The tenor is a floor:
            the fill takes the nearest listed expiry on or after it, so the realised median is{' '}
            {SPY.standalone['5pct_4w_hold']!.median_dte}, {SPY.standalone['5pct_8w_sell4w']!.median_dte} and{' '}
            {SPY.standalone['5pct_12w_sell4w']!.median_dte} days to expiry. Each rule rolls back to back: the next put
            is bought on the session the last one ends. A roll counts only if it completes inside the panel, so the
            rules’ last exits fall between {lastExits[0]} and {lastExits[lastExits.length - 1]}, before the panel’s
            last day ({E.panels.SPY.last}).
          </li>
          <li>
            <strong>Standalone return</strong> = what the puts paid ÷ the premium, less one, per roll. The 95%
            interval is a circular block bootstrap over rolls in blocks of {E.block_rolls}, so a crash spanning two
            rolls is resampled together.
          </li>
          <li>
            <strong>Hedge.</strong> A book holds SPY plus a flat {pct(E.dividend_yield_assumed)} dividend accrual
            and spends b of its wealth on each roll’s puts, b = {[...new Set(SPY.hedge.configs.map((c) => c.budget))].map((b) => `${b * 100}%`).join(', ')} (
            {premiumAtBudget.join(', ')} of wealth a year, before resale proceeds, depending on the rule): at
            each roll, b × current wealth goes into the new puts and the rest is held in SPY. It is marked daily at
            the bid. The yardstick is time-average growth, the annualised
            log return — the one a hedge can be sized on (docs/END_STATE.md §4 Q8) — over one common window,{' '}
            {SPY.hedge.window[0]} to {SPY.hedge.window[1]}.
          </li>
          <li>
            <strong>Timing.</strong> Six signals, each known at the entry close: VIX, its stretch and the regime
            label from the <em>prior</em> close; the put’s implied vol over VIX, its cost as a share of spot, and the
            implied tail index α from the same close the put is bought at (definitions below). Thirty-five filters
            (each continuous signal at or below, or at or above, its in-sample 33rd, 50th or 67th percentile: 30;
            plus five regime sets) × 12 rules = {T.combinations} combinations. Thresholds and the pick are chosen
            on rolls entered {T.is_period[0]} to {T.is_period[1]}; the pick is then tested on{' '}
            {T.oos_period[0]} to {T.oos_period[1]}. A combination is eligible only if its filter took at least{' '}
            {T.min_is_taken} rolls in sample, which leaves {T.bh.tested} of the {T.combinations}. Two objectives: mean return on the
            rolls taken minus on all rolls; and, as a hedge, the time-average growth of SPY plus puts bought at{' '}
            {timingBudget} of wealth on each roll the filter takes, minus that of a book buying the same rule on
            every roll at {timingBudget} × the share of rolls the filter took, so the two spend the same on
            average.
          </li>
          <li>
            <strong>P-values.</strong> For one combination, the out-of-sample rolls are resampled {bootReps.toLocaleString('en-US')}{' '}
            times by circular block bootstrap in blocks of {E.block_rolls}; each resample’s statistic is the mean
            return of the rolls the filter took minus the mean of all rolls (a resample in which the filter takes
            nothing is dropped). For the growth objective each resample instead sums, over the resampled rolls,
            the per-roll log-growth difference between the filtered book and the spend-matched book, and divides by
            the out-of-sample years; its interval and p are read off that distribution the same way. The one-sided p is (k + 1) ÷ (R + 1), where k counts resamples at or below zero and R ={' '}
            {bootReps.toLocaleString('en-US')} is the number of resamples, so no p is ever 0. It is a percentile-bootstrap p — the share of the bootstrap distribution on the wrong side
            of zero — not a test under an imposed null. It approximates one only when the bootstrap distribution is
            roughly symmetric about its centre, which nothing here checks. So the p-values, the count below 0.05 and
            the Benjamini–Hochberg step at a {Math.round(T.bh.fdr * 100)}% false-discovery rate
            (research/backtest/multiple_testing.py) are a <em>screen</em> for which effects to look at, not exact
            tests.
          </li>
          <li>
            <strong>Signal definitions.</strong> <em>VIX stretch</em>: (VIX close − its 20-day mean) ÷ its 20-day
            standard deviation (transforms/vix.py). <em>Put IV ÷ VIX</em>: the bought put’s Black–Scholes implied vol
            at its ask (3-month bill rate as published before the entry day; flat {pct(E.dividend_yield_assumed)} yield) over VIX ÷ 100.{' '}
            <em>Implied tail index α</em>: the Surface’s fit (research/surface/implied_alpha.py, docs/adr/0026) of
            the Pareto exponent that best prices the same session’s deeper put mids relative to one anchor — the
            listed put nearest 5% OOM on the first expiry at least 28 days out. A lower α means the market is
            pricing a fatter tail. The fit refuses on thin or noisy chains ({alphaRefused[0] === alphaRefused[1] ? `${alphaRefused[0]}%` : `${alphaRefused[0]}–${alphaRefused[1]}%`}{' '}
            of entry sessions here, by rule), and a
            filter never takes a roll it has no reading for. <em>Regime sets</em>: calm; elevated; crisis; not calm
            (elevated or crisis); not crisis (calm or elevated).
          </li>
        </ol>
      </section>

      <section className="pl-section" id="pitch-standalone">
        <h3>1. Buying puts on their own</h3>
        <p className="pl-note">
          Return per dollar of premium: −1.00 means every dollar lost. “Best” is the single best roll’s return on
          premium and the day it was bought (+2.0 is three times the premium back). “Median ask” is the typical
          price paid, as a share of spot. “Refused” counts sessions where no listed put passed the guards.
        </p>
        <p className="pl-pitch-scrollhint">The table scrolls sideways →</p>
        <div className="pl-scroll">
          <table className="pl-table" data-testid="pitch-standalone">
            <thead>
              <tr>
                <th>SPY rule</th>
                <th className="num">Rolls</th>
                <th className="num">Mean</th>
                <th className="num">95% interval</th>
                <th className="num">Winners</th>
                <th className="num">Best</th>
                <th className="num">Median ask, % spot</th>
                <th className="num">Refused</th>
              </tr>
            </thead>
            <tbody>
              {spyRules.map(([rule, s]) => (
                <StandaloneRow key={rule} rule={rule} s={s} />
              ))}
            </tbody>
          </table>
        </div>
        <p className="pl-note">
          <strong>The calendar decides the sign of the far-OOM rules.</strong> Buying a 4-week 20% OOM put on every
          session instead of on a monthly cycle averages {fmtFixed(ev20.mean_roi)} over the{' '}
          {ev20.entry_days.toLocaleString('en-US')} sessions that could buy one (overlapping); the other{' '}
          {ev20.refused_days.toLocaleString('en-US')} of {(ev20.entry_days + ev20.refused_days).toLocaleString('en-US')}{' '}
          were refused by the same spread and bid guards that set the monthly calendar, or held a put expiring after
          the panel ends. Only {ev20.phases_positive} of {ev20.phases} ways to phase a {ev20.phases}-session cycle
          (every {ev20.phases}th session, refused ones dropped) come out positive. At 15% OOM:{' '}
          {fmtFixed(ev15.mean_roi)} over {ev15.entry_days.toLocaleString('en-US')} sessions ({ev15.refused_days.toLocaleString('en-US')}{' '}
          refused), {ev15.phases_positive} of {ev15.phases}. At 5% OOM, {SPY.every_day['5pct_4w_hold']!.phases_positive} of{' '}
          {SPY.every_day['5pct_4w_hold']!.phases}. QQQ, the same study: 20% OOM averages {fmtFixed(qev20.mean_roi)} over{' '}
          {qev20.entry_days.toLocaleString('en-US')} sessions, {qev20.phases_positive} of {qev20.phases} phases positive; 15% OOM{' '}
          {fmtFixed(qev15.mean_roi)}, {qev15.phases_positive} of {qev15.phases}.
        </p>
        <p className="pl-note">
          <strong>By period.</strong> 20% OOM held to expiry:{' '}
          {Object.entries(SPY.standalone['20pct_4w_hold']!.by_period).map(([p, v]) => (
            <span key={p}>
              {p} mean {fmtFixed(v.mean_roi)} ({plural(v.hits, 'winner', 'winners')} in {v.n});{' '}
            </span>
          ))}
          winners fall in {periodsWithWinners('20pct_4w_hold')} of {Object.keys(SPY.standalone['20pct_4w_hold']!.by_period).length}{' '}
          periods.
        </p>
        <p className="pl-note">
          <strong>QQQ.</strong> {qqqNegative} of {Object.keys(QQQ.standalone).length} rules negative. Its 20% OOM
          rule’s {fmtFixed(qqq20.mean_roi)} comes from a monthly calendar (starting {qqq20.first_entry}, with{' '}
          {qqq20.fill_skips} refused fills) none of whose rolls paid in the February–March 2020 crash (
          {plural(qqq20.hits, 'winner', 'winners')}), where SPY’s calendar (starting{' '}
          {SPY.standalone['20pct_4w_hold']!.first_entry}) caught it. That is what the phase study above predicts: only{' '}
          {qev20.phases_positive} of QQQ’s {qev20.phases} phases and {ev20.phases_positive} of SPY’s {ev20.phases} come out
          positive, so on both underlyings the evidence counts against the far-OOM held rule, and SPY’s positive
          sign is a matter of which phase its calendar fell in. QQQ’s 15% OOM rule made {backX(qqq15.best_roi)} on a roll bought{' '}
          {qqq15.best_entry}.
        </p>
      </section>

      <section className="pl-section" id="pitch-hedge">
        <h3>2. Puts as a hedge on the index</h3>
        <p className="pl-note">
          Unhedged SPY over {SPY.hedge.window[0]} to {SPY.hedge.window[1]} (price plus the flat{' '}
          {pct(E.dividend_yield_assumed)} dividend accrual): log growth {pct(SPY.hedge.unhedged_growth, 2)} a year
          (price alone {pct(SPY.hedge.unhedged_growth - E.dividend_yield_assumed, 2)}), worst drawdown {pct(SPY.hedge.unhedged_max_dd)} ({SPY.hedge.unhedged_max_dd_span[0]} to{' '}
          {SPY.hedge.unhedged_max_dd_span[1]}). Growth is shown as the change against that; drawdown is each book’s
          own. Tinted rows grew faster than unhedged SPY.
        </p>
        <p className="pl-pitch-scrollhint">The table scrolls sideways →</p>
        <div className="pl-scroll">
          <table className="pl-table" data-testid="pitch-hedge">
            <thead>
              <tr>
                <th>SPY rule</th>
                <th className="num">Growth vs unhedged</th>
                <th className="num">Budget/roll</th>
                <th className="num">Worst drawdown</th>
                <th className="num">Premium spent/yr, % of wealth (gross)</th>
              </tr>
            </thead>
            <tbody>
              {SPY.hedge.configs.map((c) => (
                <tr key={`${c.rule}@${c.budget}`} className={c.edge > 0 ? 'is-winner' : undefined}>
                  <RuleCell rule={c.rule} budget={c.budget} />
                  <td className={`num ${c.edge < 0 ? 'pl-neg' : ''}`}>{pp(c.edge)}</td>
                  <td className="num">{(c.budget * 100).toFixed(2)}%</td>
                  <td className="num">{pct(c.max_dd)}</td>
                  <td className="num">{c.premium_pct_per_year.toFixed(1)}%</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="pl-note">
          <strong>Why some hedged books fall further than SPY alone.</strong> A book’s worst drawdown runs from its
          own peak, and the dates say what each one measures:{' '}
          {deeperDd
            .map((c) => `${ruleLabel(c.rule)}, at ${(c.budget * 100).toFixed(2)}%: ${pct(c.max_dd)} from ${c.max_dd_span[0]} to ${c.max_dd_span[1]}`)
            .join('; ')}
          .{' '}
          {ddPaper.length > 0 && (
            <>
              In {ddPaper.length === deeperDd.length ? 'every one' : `${ddPaper.length} of them`} the peak is a paper
              gain: on the peak day the open put was marked at its bid far above what it cost, and most of that value
              was gone by the time it was sold or settled (
              {ddPaper
                .map(
                  (c) =>
                    `${ruleLabel(c.rule)}, at ${(c.budget * 100).toFixed(2)}%: bought ${c.max_dd_peak_put!.entry}, marked ${fmtFixed(c.max_dd_peak_put!.mark_multiple, 1)}× its cost on ${c.max_dd_span[0]}, ${fate(c.max_dd_peak_put!.exit_multiple)} on ${c.max_dd_peak_put!.exit}`,
                )
                .join('; ')}
              ). So each of these drawdowns begins with the hedge giving back its own mark, not with the market
              falling; whatever the index did next, and any premium spent before the trough, adds to it.{' '}
            </>
          )}
          {ddBled.length > 0 && (
            <>
              In {ddBled.map((c) => `${ruleLabel(c.rule)}, at ${(c.budget * 100).toFixed(2)}%`).join('; ')} no put
              was marked above {PAPER_PEAK}× its cost on the peak day: that drawdown is the index’s fall plus the
              premium spent.
            </>
          )}
        </p>
        <p className="pl-note">
          {beaters.map((c) => (
            <span key={`${c.rule}@${c.budget}`}>
              {ruleLabel(c.rule)} at {(c.budget * 100).toFixed(2)}%: {pp(c.edge)} with the{' '}
              {c.drop_best!.best_entry} roll, {pp(c.drop_best!.edge_without_best)} without it.{' '}
            </span>
          ))}
          QQQ ({QQQ.hedge.window[0]} to {QQQ.hedge.window[1]}): {QQQ.hedge.n_beat} of {QQQ.hedge.configs.length}{' '}
          configurations beat unhedged.
        </p>
        <p className="pl-note">
          <strong>Cboe’s indices, priced from real trades, agree.</strong> PPUT, PPUT3M and VXTH are built on the
          S&amp;P 500 with dividends reinvested, so the yardstick is the SPX price index plus the same{' '}
          {pct(E.dividend_yield_assumed)} yield the study accrues, as log growth: {pct(spxWithDiv, 2)} a year over {E.cboe.span[0]}{' '}
          to {E.cboe.span[1]} (the price index alone: {pct(E.cboe.SPX.growth, 2)}, drawdown {pct(E.cboe.SPX.max_dd)}).
          Against it: PPUT, the S&amp;P 500 with a monthly 5% OOM put, {pct(E.cboe.PPUT.growth, 2)} (
          {pp(E.cboe.PPUT.growth - spxWithDiv)}; drawdown {pct(E.cboe.PPUT.max_dd)}); PPUT3M{' '}
          (Cboe’s Tail Risk index: a quarterly 10% OOM put){' '}
          {pct(E.cboe.PPUT3M.growth, 2)} ({pp(E.cboe.PPUT3M.growth - spxWithDiv)}; {pct(E.cboe.PPUT3M.max_dd)}); VXTH,
          Cboe’s VIX Tail Hedge index (the S&amp;P 500 plus monthly VIX calls), {pct(E.cboe.VXTH.growth, 2)} ({pp(E.cboe.VXTH.growth - spxWithDiv)};{' '}
          {pct(E.cboe.VXTH.max_dd)}). All three trail the index with dividends; each cuts the drawdown.
          The Book tab tests the right question — does any mix of program and index out-grow the index alone — and
          on {LIVE.read_on} answered, as a margin: the CAGR (not log growth) of the best partial mix minus the better
          of index alone and program alone, with anything within ±1bp/yr inconclusive:{' '}
          {LIVE.book.map((b) => (
            <span key={`${b.program}${b.window}`}>
              {b.program} {b.window} {bp(b.margin)} ({b.outcome});{' '}
            </span>
          ))}
          at an assumed 1.9% dividend yield. The put programs fail in every window; the VIX-call program holds by{' '}
          {bp(vxth[0]!.margin)} over {vxth[0]!.window}, a decade containing 2008, and by {bp(vxth[1]!.margin)} (
          {vxth[1]!.outcome}) over its whole history.
        </p>
      </section>

      <section className="pl-section" id="pitch-2008">
        <h3>2b. The crisis the panel misses: 2008–09</h3>
        <p className="pl-note">
          A separate, smaller check on the other real-quote dataset, option_quotes: SPY puts quoted on{' '}
          {C08.roll_dates} monthly roll dates from {C08.first}, buying the nearest listed expiry ({C08.nearest_dte[0]}–
          {C08.nearest_dte[1]} days out; {C08.dates_with_two_expiries.join(' and ')} also list a quarterly, which is
          not used), through the platform’s{' '}
          <code>OptionQuotesSource</code> guards (open interest included). “Buy the next monthly put” and hold it to
          expiry. Its limits: no daily quotes, so drawdowns are measured on roll dates only; no expiry-day spot, so
          SPY at expiry is estimated as SPY at entry × the SPX move;{' '}
          {c08Gaps.length === 0
            ? 'every month has a roll date'
            : `${c08Gaps.join(', ')} ${c08Gaps.length > 1 ? 'have' : 'has'} no roll date, so ${C08.unhedged_spans
                .map((u) => `the put bought ${u.entry} runs to its ${u.expiry} expiry and none is held from then until ${u.next_entry}`)
                .join('; ')}`}
          ; the licence is unresolved, so this is research use only. Unhedged, the book ends at{' '}
          {fmtFixed(C08['5pct'].unhedged.terminal, 4)} of its starting wealth ({C08.first} → {C08['5pct'].last}), with a {pct(C08['5pct'].unhedged.max_dd)} drawdown.
        </p>
        <p className="pl-pitch-scrollhint">The table scrolls sideways →</p>
        <div className="pl-scroll">
          <table className="pl-table" data-testid="pitch-2008">
            <thead>
              <tr>
                <th>Depth</th>
                <th className="num">Filled</th>
                <th className="num">Mean</th>
                <th className="num">Winners</th>
                <th className="num">Best</th>
                <th className="num">Budget/roll</th>
                <th className="num">Ends at ×</th>
                <th className="num">vs unhedged ×</th>
                <th className="num">w/o best roll</th>
                <th className="num">Worst DD</th>
              </tr>
            </thead>
            <tbody>
              {c08Rules.flatMap(([k, r]) =>
                r.books.map((b, i) => (
                  <tr key={`${k}@${b.budget}`} className={b.terminal > r.unhedged.terminal ? 'is-winner' : undefined}>
                    <td>{i === 0 ? `${k.replace('pct', '')}% OOM` : ''}</td>
                    <td className="num">{i === 0 ? `${r.filled}/${r.rolls}` : ''}</td>
                    <td className={`num ${r.mean_roi < 0 ? 'pl-neg' : ''}`}>{i === 0 ? fmtFixed(r.mean_roi) : ''}</td>
                    <td className="num">{i === 0 ? r.hits : ''}</td>
                    <td className="num nowrap">
                      {i === 0 ? (
                        <>
                          {fmtFixed(r.best_roi, 1)} <span className="dimmer">{r.best_entry}</span>
                        </>
                      ) : (
                        ''
                      )}
                    </td>
                    <td className="num">{(b.budget * 100).toFixed(2)}%</td>
                    <td className="num">{fmtFixed(b.terminal, 4)}</td>
                    <td className="num nowrap">{signed5(b.terminal - r.unhedged.terminal)}</td>
                    <td className="num">{b.terminal_without_best === undefined ? '—' : fmtFixed(b.terminal_without_best, 4)}</td>
                    <td className="num">{pct(b.max_dd)}</td>
                  </tr>
                )),
              )}
            </tbody>
          </table>
        </div>
        <p className="pl-note">
          “Ends at” is the book’s wealth on {C08['5pct'].last} as a multiple of its starting wealth; “vs unhedged” is
          that multiple minus the unhedged book’s, not a rate. “w/o best roll” is the same book without its best roll, recomputed only for a book that
          ends ahead of unhedged; “—” means it did not. Tinted rows are the books that end ahead of unhedged. “Worst DD” is the book’s worst peak-to-trough drawdown.
        </p>
        {c08Rules
          .filter(([, r]) => r.refused.length > 0)
          .map(([k, r]) => (
            <p className="pl-note" key={k}>
              <strong>
                {k.replace('pct', '')}% OOM: {plural(r.refused.length, 'roll date', 'roll dates')} of {r.rolls} refused
              </strong>{' '}
              by the platform’s quote guards, which treat a spread wider than half the mid as no price at all:{' '}
              {r.refused.map((x) => `${x.entry}: ${x.reason}`).join('; ')}. {r.refused.length > 1 ? 'Those months are' : 'That month is'}{' '}
              unhedged.
              {r.hits === 0 && r.refused.some((x) => x.entry === C08['5pct'].best_entry) && (
                <>
                  {' '}
                  The {C08['5pct'].best_entry} roll that carries every other depth is among them, which is why this
                  row has {plural(r.hits, 'winner', 'winners')}: the deepest put was never bought in the month of
                  the crash.
                </>
              )}
            </p>
          ))}
        <p className="pl-note">
          Here the hedge does what it is bought for: {C08.n_beat} of {C08.configs} books end ahead of unhedged, and{' '}
          {c08Rules.flatMap(([, r]) => r.books.filter((b) => b.max_dd > r.unhedged.max_dd)).length} cut the
          drawdown.{' '}
          {c08OneMonth && (
            <>
              Every book that ends ahead rests on the roll bought {c08Beaters[0]!.r.best_entry}, the week Lehman failed: leave
              that month unhedged and each ends below {fmtFixed(C08['5pct'].unhedged.terminal, 4)}.{' '}
            </>
          )}
          That is the pattern of the 2010–2023 study — one crash month carries the result — seen once more, on{' '}
          {C08.roll_dates} rolls. The spread guard matters here: with it lifted, so a wide quote is bought at its
          ask, {spreadOff.n_beat} of {C08.configs} books end ahead (
          {(['5pct', '10pct', '15pct', '20pct'] as const)
            .map((k) => `${k.replace('pct', '')}% OOM ${spreadOff.depths[k]!.filled}/${spreadOff.depths[k]!.rolls} filled, ${spreadOff.depths[k]!.n_beat} of ${spreadOff.depths[k]!.books.length} ahead`)
            .join('; ')}
          ), and the best roll of every depth is bought{' '}
          {[...new Set(Object.values(spreadOff.depths).map((d) => d.best_entry))].join(' or ')}
          {offOneMonth ? '; without it, every one of those books ends behind unhedged' : ''}. It is evidence the hedge can pay in a crash, not that it pays over a cycle; whether 2008–09 and
          2010–2023 together beat unhedged growth needs one continuous book across both datasets, which has not been
          built.
        </p>
      </section>

      <section className="pl-section" id="pitch-timing">
        <h3>3. Can timing rescue it?</h3>
        <p className="pl-note">
          The in-sample pick on mean return
          {selDg.rule === sel.rule && selDg.gate === sel.gate ? ' — and on growth —' : ''} was {ruleLabel(sel.rule)},
          bought only when {gateLabel(sel.gate)}.
          {sameClose(sel.gate) && (
            <> That signal is read from the same close the put is bought at, so even this result assumes trading in the closing minutes (see “Same-close signals” below).</>
          )}{' '}
          In sample it took {sel.is.taken} of {sel.is.n} rolls and raised the mean return from{' '}
          {fmtFixed(sel.is.mean_roi_all)} to {num(sel.is.mean_roi_taken)}. Out of sample it took {sel.oos.taken}{' '}
          of {sel.oos.n}: {fmtFixed(sel.oos.mean_roi_all)} to {num(sel.oos.mean_roi_taken)}, an improvement of{' '}
          {num(sel.oos.roi_diff)} per dollar of premium, with a 95% interval of {ci(sel.oos_ci95)} (p = {sel.oos_p.toFixed(3)}),
          resting on {sel.oos.hits_taken} winning rolls.
          {sel.oos_mean_taken_without_best !== null && (
            <> Without its single best out-of-sample roll the filtered mean is {fmtFixed(sel.oos_mean_taken_without_best)}.</>
          )}{' '}
          {selDg.rule === sel.rule && selDg.gate === sel.gate
            ? 'As a hedge'
            : `The growth objective's pick, ${ruleLabel(selDg.rule)} when ${gateLabel(selDg.gate)}`}
          , at {timingBudget} of wealth per roll taken against the spend-matched book (method, Timing):{' '}
          {pp(selDg.oos.dg)} out of sample, interval{' '}
          [{pp(selDg.oos_ci95[0]!)}, {pp(selDg.oos_ci95[1]!)}], p = {selDg.oos_p.toFixed(3)}.
        </p>
        <p className="pl-note">
          Across all {sel.eligible} combinations with enough in-sample trades, {sel.is_positive} improved in sample
          and {sel.is_positive_oos_positive} of those ({Math.round((sel.is_positive_oos_positive / sel.is_positive) * 100)}%)
          also improved out of sample. Is that more than chance? The combinations share rolls, so their signs move
          together and a count of independent coins is no yardstick. The null instead rotates every rule’s
          out-of-sample outcomes by the same number of rolls against the filters’ unmoved choices — every rotation
          by {E.block_rolls} or more rolls in either direction, {sel.null_rotations} of them (a shift of fewer rolls,
          one bootstrap block, leaves clustered outcomes nearly where they were, so it is no null) — which keeps that dependence and the outcomes’ own clustering and breaks only
          the link from signal to outcome: {sel.null_at_least_observed} of {sel.null_rotations} rotations reach{' '}
          {sel.is_positive_oos_positive}. On the growth objective {selDg.is_positive_oos_positive} of{' '}
          {selDg.is_positive} persisted, reached by {selDg.null_at_least_observed} of {selDg.null_rotations}. That
          is weak evidence that in-sample improvement carries forward a little; it makes no single filter
          significant (below). (The share
          of all combinations positive out of sample is no test: most filters come with their opposite in the set —
          below and above one threshold, calm and not calm, crisis and not crisis — and when a pair splits the rolls
          between its two sides, the two differences have opposite signs, so that share sits near half whatever the
          data. Not every pair splits them. A roll with no implied-alpha or put-IV reading is left out by both sides of
          that threshold, so both sides can beat, or both trail, the mean over all rolls; and elevated has no
          opposite. Part of an alpha or put-IV filter’s apparent effect can therefore be that rolls with a reading
          differ from rolls without one, not timing on the reading’s value.) The rank correlation between in-sample and out-of-sample results is{' '}
          {fmtFixed(sel.spearman_is_oos)}: an in-sample ranking says almost nothing about the next seven years.{' '}
          {T.bh.p_below_05} of {T.bh.tested} have a raw p below 0.05 — about the {Math.round(T.bh.tested * 0.05)} that
          chance would give if these p-values were uniform under no effect and the tests independent (neither is
          established: see the method, and the tests share the same rolls). Benjamini–Hochberg at{' '}
          {Math.round(T.bh.fdr * 100)}%, used as a screen, keeps{' '}
          <strong data-testid="pitch-bh">{survivors}</strong>
          {survivors > 0 && (
            <>
              :{' '}
              {T.bh.survivors.map((s) => (
                <span key={`${s.rule}${s.gate}`}>
                  {ruleLabel(s.rule)} when {gateLabel(s.gate)} (taken {s.oos.taken}/{s.oos.n}, mean{' '}
                  {fmtFixed(s.oos.mean_roi_all)} → {num(s.oos.mean_roi_taken)});{' '}
                </span>
              ))}
              a filter that loses less is still a loss
            </>
          )}
          . Could a real effect have passed? The smallest p any test can report is {T.bh.p_floor.toFixed(5)}, under
          the first BH step ({(T.bh.fdr / T.bh.tested).toFixed(5)}), but that is not the whole answer. A
          perfect-foresight filter that takes the best third of out-of-sample rolls reaches{' '}
          {soldRules.every(([, o]) => o.p <= T.bh.p_floor * 1.0001)
            ? `the floor p = ${T.bh.p_floor.toFixed(4)}`
            : `p = ${Math.max(...soldRules.map(([, o]) => o.p)).toFixed(4)} or less`}{' '}
          on every sold rule, so those tests had power against a perfect filter. Their power against a realistic
          filter was not simulated. What was observed: the skip-crisis filter below, at{' '}
          {fmtFixed(Math.min(...notCrisisSmallest.map((x) => x.oos.roi_diff ?? 0)), 3)} to{' '}
          {fmtFixed(Math.max(...notCrisisSmallest.map((x) => x.oos.roi_diff ?? 0)), 3)} per dollar on{' '}
          {notCrisisSmallest[0]?.oos.n} out-of-sample rolls, has p ={' '}
          {Math.min(...notCrisisSmallest.map((x) => x.oos_p)).toFixed(4)} at best, about{' '}
          {Math.round(Math.min(...notCrisisSmallest.map((x) => x.oos_p)) / (T.bh.fdr / T.bh.tested))}× the first BH
          step. One p-value is one draw, not a power calculation: it does not say how often an effect of that size
          would clear the step. On the held-to-expiry rules the
          perfect filter gets only{' '}
          {heldRules.map(([r, o]) => `${o.p.toFixed(2)} (${ruleLabel(r)}, ${plural(o.oos_winners, 'winner', 'winners')})`).join(', ')}
          , because with one or a few winning rolls a resample either contains them or does not. Even a perfect
          filter there stays above the first BH step, so those {T.bh.tested - T.bh.sell_rules.tested} tests carry
          almost no power. Benjamini–Hochberg over the{' '}
          {T.bh.sell_rules.tested} sold-rule tests alone keeps {T.bh.sell_rules.survivors} (smallest p{' '}
          {T.bh.sell_rules.min_p.toFixed(4)}, first step {(T.bh.fdr / T.bh.sell_rules.tested).toFixed(5)}).
        </p>
        <p className="pl-note">
          <strong>The one consistent pattern.</strong>{' '}
          {notCrisisEverySold
            ? `The ${T.bh.smallest_p.length} smallest p-values are the same filter, once on each of the ${soldRules.length} sold rules`
            : `${notCrisisSmallest.length} of the ${T.bh.smallest_p.length} smallest p-values are the same filter, on ${new Set(notCrisisSmallest.map((x) => x.rule)).size} different sold rules`}
          : do not buy when the prior close’s regime label is crisis (entered at a VIX close of 29 or more, left only below 27: see Terms). It lifted their out-of-sample mean by{' '}
          {fmtFixed(Math.min(...notCrisisSmallest.map((x) => x.oos.roi_diff ?? 0)), 3)} to{' '}
          {fmtFixed(Math.max(...notCrisisSmallest.map((x) => x.oos.roi_diff ?? 0)), 3)} per dollar, taking{' '}
          {notCrisisSmallest[0]?.oos.taken} of {notCrisisSmallest[0]?.oos.n} rolls, with p from{' '}
          {Math.min(...notCrisisSmallest.map((x) => x.oos_p)).toFixed(4)} to{' '}
          {Math.max(...notCrisisSmallest.map((x) => x.oos_p)).toFixed(4)}.{' '}
          {ovl.same_oos_entries && ovl.same_skipped ? (
            <>
              These are not {ovl.tests} confirmations. The {ovl.tests} tests share the same {ovl.oos_entries}{' '}
              out-of-sample entry dates, the filter skips the same {ovl.skipped.length} of them (
              {ovl.skipped.join(', ')}), and their per-roll returns correlate{' '}
              {ovl.roi_corr ? `${fmtFixed(ovl.roi_corr[0]!)} at least, ${fmtFixed(ovl.roi_corr[1]!)} at the median` : ''}: one
              effect seen through {ovl.tests} nearly identical lenses, resting on those few dates.
            </>
          ) : (
            <>
              The {ovl.tests} tests {ovl.same_oos_entries ? 'share their entry dates but skip different ones' : 'run on different entry dates'}, so they are partly separate evidence.
            </>
          )}{' '}
          It still misses the BH line, and it never turns a mean positive (the best
          filtered mean is {num(Math.max(...notCrisisSmallest.map((x) => x.oos.mean_roi_taken ?? -Infinity)))}). Skipping
          crisis entries makes a losing trade lose a little less.
        </p>
      </section>

      <section className="pl-section" id="pitch-caveats">
        <h3>What this does not establish</h3>
        <ul className="pl-pitch-list">
          <li>
            <strong>No causal claim.</strong> These are historical returns of mechanical rules. Nothing here
            identifies why puts were dear or cheap; a return series is not an identification strategy.
          </li>
          <li>
            <strong>Few independent events.</strong> SPY had {SPY.episodes_10pct.length} distinct declines of 10%
            or more in the window (
            {SPY.episodes_10pct.map((e) => `${e.peak.slice(0, 7)} ${pct(e.dd, 0)}`).join(', ')}). A strategy that
            lives on tails is judged on that handful, not on the up to {maxRolls} rolls per rule.
          </li>
          <li>
            <strong>2008 is outside the main study.</strong> The daily quote panels start in 2010. 2008–09 is
            checked separately on monthly quotes (section 2b), where {C08.n_beat} of {C08.configs} books beat
            unhedged on one month; the two periods have not been joined into one book. The only window where the Book
            finds a hedge holding (VXTH, 2006–2016) also contains 2008.
          </li>
          <li>
            <strong>Simplifications.</strong> Flat {pct(E.dividend_yield_assumed)} dividend on both arms (no
            measured dividends in the lake); fractional contracts; early exercise ignored, except that a sold put with no acceptable exit bid is
            credited its exercise value (method, Fills); expiry settled on the
            panel’s close; SPY and QQQ only — no single names, so no survivorship within the sample, but the choice
            of two US index ETFs over a bull market is itself hindsight.
          </li>
          <li>
            <strong>Same-close signals.</strong> Three timing signals (put IV ÷ VIX, put cost, implied α) are read
            from the same close the put is bought at, at that close’s ask. Doing that for real means reading the
            chain and trading in the closing minutes, or trading the next session at its prices; neither has been
            tested. The VIX and regime signals use the prior close and do not have this problem.
          </li>
          <li>
            <strong>Searched space, and what changed along the way.</strong> {spyRules.length} rules × 3 budgets on
            two underlyings, the 2008–09 check ({c08Rules.length} depths × {budgetsPerRule} budgets), then {T.combinations} timing combinations.
            Every count is reported above. One change was made after seeing a result: an exploratory timing run drew
            2,000 bootstrap resamples per test and wrote p = 0 when none fell at or below zero, and with that, one
            combination (5% OOM 8-week, not-crisis filter, p reported as 0) passed BH. A p of 0 claims more than 2,000
            draws can resolve, and the floor of 1/2,001 sat above the first BH step, so no honest p could pass at all.
            The p was corrected to (k + 1) ÷ (R + 1) and R raised to {bootReps.toLocaleString('en-US')}, which puts
            the floor below the first step; under the correction the same combination’s p is{' '}
            {(T.bh.smallest_p.find((x) => x.rule === '5pct_8w_sell4w' && x.gate === 'regime=not_crisis')?.oos_p ?? NaN).toFixed(4)}.
            It is a correction of the method, not a search for a setting that kills the result — but a reader should
            know the earlier run existed.
          </li>
        </ul>
      </section>

      <section className="pl-section" id="pitch-next">
        <h3>What would make this a strategy — or kill it</h3>
        <ol className="pl-pitch-list">
          <li>
            <strong>Join 2008–09 to 2010–2023 in one book.</strong> 2008–09 alone (section 2b) favours the hedge,
            on one month; 2010–2023 alone does not, except on one month. The question that decides the hedge case is
            whether one continuous book across both — monthly quotes, then daily — beats unhedged growth at any
            budget, and whether that survives dropping its best month. It has not been run.
          </li>
          <li>
            <strong>Price the Screen on quotes, not a model.</strong> Until the Workspace verdicts use real fills,
            they cannot count as evidence. Even priced at VIX, which the Workspace does not use, a ~30-day 10% OOM
            SPY put comes out a median 7.4× too cheap against real quotes (docs/MODEL_RESIDUAL.md, 210 roll dates).
          </li>
          <li>
            <strong>Pre-register one rule and trade it on paper forward.</strong> The chain sweep has collected
            daily quotes since 2026-08-26. A single rule fixed today, marked on those quotes for two to three years,
            is the only out-of-sample test that cannot be fitted after the fact.
          </li>
          <li>
            <strong>Give the verdict a minimum sample and a test.</strong> A regime verdict on 1–2 crisis rolls
            should read “not testable”, not “failed” or “confirmed”.
          </li>
          <li>
            <strong>Ingest measured dividends</strong> (tiingo_eod), so the hedge comparisons stop resting on a
            flat yield.
          </li>
        </ol>
      </section>

      <section className="pl-section" id="pitch-reproduce">
        <h3>Reproduce every number</h3>
        <p className="pl-note">
          From a checkout whose <code>.env</code> points at the production lake (the snapshots above are not in a
          local <code>./data</code>), <code>make pitch-evidence AS_OF={E.as_of}</code> reads them, read only, and
          rewrites <code>frontend/src/content/pitch_evidence.json</code>, which is all this page reads for the
          study. The make target puts this checkout’s <code>src/</code> first on <code>PYTHONPATH</code>, and the generator stops if the <code>tail_lab</code> package it imports comes from anywhere else, so the tree hashes below identify the code that ran. That file was generated by commit{' '}
          <code data-testid="pitch-code-sha">{E.code.sha.slice(0, 12)}</code>, a branch commit that a squash merge
          does not keep; the code it ran is identified on main by its git tree hashes, src/{' '}
          <code>{E.code.tree.src.slice(0, 12)}</code> and scripts/ <code>{E.code.tree.scripts.slice(0, 12)}</code>
          {E.code.dirty ? ', with uncommitted changes — not reproducible as committed' : ''}. The live readings:{' '}
          <code>GET /api/putlab/regime-verdict</code>, <code>/api/putlab/backtest</code>,{' '}
          <code>/api/putlab/roll-schedule/marked</code> and <code>/api/putlab/hedge-overlay</code> on tail-lab.fly.dev,
          read {LIVE.read_on}. The roll engine is tested in tests/test_scripts_pitch_evidence.py.
        </p>
      </section>
    </article>
  )
}
