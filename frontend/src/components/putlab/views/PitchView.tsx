import { EVIDENCE as E, LIVE, gateLabel, ruleLabel, ruleShort, type Standalone } from '../../../content/pitch'
import { fmtFixed } from '../format'

/* The Pitch: does the put book have a genuine edge? (docs/adr/0021, 2026-10-27
 * amendment.) Verdict first, then the evidence, then how to kill or rescue it.
 *
 * Every study number is read from content/pitch_evidence.json, which
 * `make pitch-evidence` writes from the lake; nothing here types a study
 * figure in. The few live-app readings come from content/pitch.ts's LIVE block,
 * dated and with the URL that reproduces each.
 */

const SPY = E.SPY
const QQQ = E.QQQ
const T = E.timing

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
function plural(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`
}
/** The long rule label, swapped for a short one on a phone (putlab.css). */
function RuleCell({ rule }: { rule: string }) {
  return (
    <td>
      <span className="pl-pitch-long">{ruleLabel(rule)}</span>
      <span className="pl-pitch-short">{ruleShort(rule)}</span>
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
const qqq20 = QQQ.standalone['20pct_4w_hold']!
const qqq15 = QQQ.standalone['15pct_4w_hold']!

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
        On real bid/ask quotes for SPY ({SPY.standalone['5pct_4w_hold']!.first_entry} to{' '}
        {SPY.standalone['5pct_4w_hold']!.last_exit}), buying out-of-the-money puts lost money in{' '}
        {spyNegative} of {spyRules.length} rules; as a hedge on the index it lowered long-run growth in{' '}
        {SPY.hedge.configs.length - SPY.hedge.n_beat} of {SPY.hedge.configs.length} configurations; and of{' '}
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
                {s.hits === 1 ? 'its single winning roll' : `the best of its ${s.hits} winning rolls`} in {s.n_rolls} returned +{fmtFixed(s.best_roi, 1)}× its
                premium (bought {s.best_entry}); its interval is {ci(s.mean_roi_ci95)}.{' '}
              </span>
            ))}
            QQQ: {qqqNegative} of {Object.keys(QQQ.standalone).length} rules negative — but its 20% OTM rule’s{' '}
            {fmtFixed(qqq20.mean_roi)} is not evidence against SPY’s exception: {qqq20.fill_skips} refused fills
            shifted its roll calendar so that no roll it held caught February 2020 ({plural(qqq20.hits, 'winner', 'winners')}),
            while QQQ’s 15% OTM rule returned +{fmtFixed(qqq15.best_roi, 1)}× on a roll bought {qqq15.best_entry}.
          </li>
          <li>
            <strong>As a hedge: insurance, not an edge.</strong> {cutDd.length} of {SPY.hedge.configs.length} SPY
            books cut the worst drawdown, {cutDdAndLost} of them at a cost in growth. {SPY.hedge.n_beat} grew faster
            than holding SPY alone
            {oneRoll && (
              <>
                , and {beaters.length === 1 ? 'it rests' : beaters.length === 2 ? 'both rest' : `all ${beaters.length} rest`} on a single roll bought{' '}
                {beaters[0]!.drop_best!.best_entry}: drop it and{' '}
                {beaters.map((c) => `${pp(c.drop_best!.edge)} becomes ${pp(c.drop_best!.edge_without_best)}`).join(', ')}
              </>
            )}
            . QQQ: {QQQ.hedge.n_beat} of {QQQ.hedge.configs.length}. In 2008–09, on a separate monthly dataset,{' '}
            {C08.n_beat} of {C08.configs} books ended ahead of unhedged
            {c08OneMonth && <>, every one on the roll bought {c08Beaters[0]!.r.best_entry} and behind without it</>} (
            <a href="#pitch-2008">section 2b</a>).
          </li>
          <li>
            <strong>Timing: nothing survives.</strong> The best of {T.combinations} rule-and-filter combinations
            chosen on {T.is_period[0]!.slice(0, 4)}–{T.is_period[1]!.slice(0, 4)} improved the out-of-sample mean
            return by {num(sel.oos.roi_diff)} (95% interval {ci(sel.oos_ci95)}, one-sided p ={' '}
            {sel.oos_p.toFixed(3)}); {Math.round(sel.share_positive_oos * 100)}% of all combinations were positive
            out of sample, which is what a coin gives.
          </li>
          <li>
            <strong>Daily signal: none.</strong> The app re-scores the same in-sample backtests every day. Its one
            order-shaped output always says “buy”, is priced by a model, and its verdict on SPY changed {verdictChanges} times
            in {LIVE.spy_verdicts.length} sessions although every roll behind it had expired by {lastExpiry} (below).
          </li>
        </ol>
        <p className="pl-note">
          “No demonstrable edge” is not “proven useless”. The sample holds {SPY.episodes_10pct.length} distinct
          SPY declines of 10% or more and one that paid a far-OTM put spectacularly (February 2020). Fourteen years
          of quotes cannot tell a strategy that pays once a decade from one that never pays. What would decide it is
          in <a href="#pitch-next">the last section</a>.
        </p>
      </section>

      <section className="pl-section" id="pitch-terms">
        <h3>Terms used below</h3>
        <ul className="pl-pitch-list">
          <li>
            <strong>OTM, depth.</strong> How far below spot the strike sits: a 10% OTM put on SPY at 500 has a strike
            near 450. Strikes here are always picked by this distance, never by delta (the Workspace can also pick by
            delta at realised vol, docs/adr/0029; this study does not).
          </li>
          <li>
            <strong>Roll.</strong> One put bought, then held to expiry or sold; the next is bought the session the
            last one ends.
          </li>
          <li>
            <strong>Return on premium</strong> = what the roll paid back ÷ what it cost − 1, after commission. For a
            held put, “paid back” is its value at expiry; for a sold put, the bid it was sold at. −1.00 means the
            premium was lost; +2.00 means three times the premium came back.
          </li>
          <li>
            <strong>Time-average growth</strong> = the annualised log return of a whole book. It is what compounds,
            and the only yardstick a hedge can be sized on (docs/END_STATE.md §4 Q8).
          </li>
          <li>
            <strong>VIX regimes.</strong> Calm: VIX under 17. Elevated: 17 to 28. Crisis: 28 and above
            (contracts/regime.py, with a one-point hysteresis band so a VIX near a boundary does not flip daily).
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
          <table className="pl-table">
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
                <td>For every name × 5% and 10% OTM, 4-week puts, 4-year window: re-runs the backtest and files a verdict</td>
                <td>No — re-scores history</td>
              </tr>
              <tr>
                <td>Screen and roll schedule (/api/putlab/leaderboard, /roll-schedule)</td>
                <td>On request; leaderboard pre-warmed 00:01 UTC</td>
                <td>The 10 names with the best in-sample annualised return, as “legs” to buy</td>
                <td>Order-shaped, but see below</td>
              </tr>
              <tr>
                <td>Book (/api/putlab/hedge-overlay)</td>
                <td>On request</td>
                <td>How much of a Cboe hedge program to hold</td>
                <td>Static: changes only when month-end index data arrives</td>
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
              leg #{l.rank} {l.asset} ({l.rule}) expected {fmtFixed(l.expected_roi)}× on premium at a model price of $
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
          significance test, no minimum sample: the crisis slice below holds 1 or 2 rolls. SPY, 5% OTM, 4 weeks,
          4-year window, by as-of date (GET /api/putlab/regime-verdict?asset=spy&amp;moneyness_pct=5&amp;as_of=…):
        </p>
        <div className="pl-scroll">
          <table className="pl-table" data-testid="pitch-flips">
            <thead>
              <tr>
                <th>As of</th>
                <th>Verdict</th>
                <th className="num">Calm ROI</th>
                <th className="num">Elevated ROI</th>
                <th className="num">Crisis rolls</th>
                <th>Rolls scored</th>
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
          Every run’s last roll expired by {lastExpiry}, before the first date in the table, so no price from these
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
          <table className="pl-table">
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
                    <td className="nowrap">{E.snapshots[`optionsdx_quotes_${s.toLowerCase()}`]}</td>
                    <td className="nowrap">
                      {p.first} → {p.last}
                    </td>
                    <td>
                      optionsDX end-of-day {s} puts, bid and ask, 0.60–1.02 moneyness, 0–120 days.{' '}
                      {p.rows.toLocaleString('en-US')} rows, {p.sessions.toLocaleString('en-US')} sessions. Licence:
                      no redistribution — only aggregates leave the lake.
                    </td>
                    <td>
                      Against the exchange’s session calendar (the days Cboe printed an SPX close):{' '}
                      {p.missing_trading_days} trading days missing; {p.sessions_on_closed_days.length} sessions dated on
                      days the exchange was shut ({p.sessions_on_closed_days.slice(0, 3).join(', ')}, …). No 2008: the
                      panel starts in {p.first.slice(0, 4)}.
                    </td>
                  </tr>
                )
              })}
              <tr>
                <td className="nowrap">{E.snapshots.option_quotes}</td>
                <td className="nowrap">2008-01 → 2025-11</td>
                <td>
                  SPY puts on monthly roll dates only ({C08.roll_dates} of them in 2008–09), one expiry each; no daily
                  quotes and no expiry-day spot. Used only for the 2008–09 check (section 2b)
                </td>
                <td>Licence unresolved: research use only. 2008-03 has no roll date</td>
              </tr>
              <tr>
                <td className="nowrap">{E.snapshots.vix}</td>
                <td>1990 → today</td>
                <td>Cboe VIX close; the regime bands (calm under 17, crisis from 28) and the timing filters</td>
                <td>—</td>
              </tr>
              <tr>
                <td className="nowrap">{E.snapshots.rates}</td>
                <td>DGS3MO 1981 →</td>
                <td>FRED 3-month bill, first vintage of each observation, for implied vol</td>
                <td>Vintages begin 2005-06</td>
              </tr>
              <tr>
                <td className="nowrap">{E.snapshots.cboe_strategy}</td>
                <td>1986 → today</td>
                <td>Cboe SPX, PPUT, PPUT3M, VXTH: hedge-program indices Cboe prices from real trades; SPX also serves as the exchange session calendar</td>
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
                  skew. It priced a calm-market 5% OTM SPY put at about $0.03 against about $1 in the market
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
            <strong>bid</strong>; commission ${E.commission_per_contract.toFixed(2)} a contract on each, none at
            expiry. A refused fill tries the next session and is counted. No option is priced by a model.
          </li>
          <li>
            <strong>Twelve rules.</strong> Four depths (5, 10, 15, 20% below spot) × three structures: a 4-week put
            held to expiry; an 8-week put sold after 4 weeks; a 12-week put sold after 4 weeks. The tenor is a floor:
            the fill takes the nearest listed expiry on or after it, so the realised median is{' '}
            {SPY.standalone['5pct_4w_hold']!.median_dte}, {SPY.standalone['5pct_8w_sell4w']!.median_dte} and{' '}
            {SPY.standalone['5pct_12w_sell4w']!.median_dte} days to expiry. Each rule rolls back to back: the next put
            is bought on the session the last one ends.
          </li>
          <li>
            <strong>Standalone return</strong> = what the puts paid ÷ the premium, less one, per roll. The 95%
            interval is a circular block bootstrap over rolls in blocks of {E.block_rolls}, so a crash spanning two
            rolls is resampled together.
          </li>
          <li>
            <strong>Hedge.</strong> A book holds SPY plus a flat {pct(E.dividend_yield_assumed)} dividend accrual
            and spends b of its wealth on each roll’s puts, b = 0.25%, 0.5% or 1% (about 3%, 6% or 12% a year). It is
            marked daily at the bid and rebalanced at each roll. The yardstick is time-average growth, the annualised
            log return — the one a hedge can be sized on (docs/END_STATE.md §4 Q8) — over one common window,{' '}
            {SPY.hedge.window[0]} to {SPY.hedge.window[1]}.
          </li>
          <li>
            <strong>Timing.</strong> Six signals, each known at the entry close: VIX, its stretch and the regime
            label from the <em>prior</em> close; the put’s implied vol over VIX, its cost as a share of spot, and the
            implied tail index α from the same close the put is bought at (definitions below). Thirty-five filters
            (each continuous signal at or below, or at or above, its in-sample 33rd, 50th or 67th percentile: 30;
            plus five regime sets) × 12 rules = {T.combinations} combinations. Thresholds and the winner are chosen
            on rolls entered {T.is_period[0]} to {T.is_period[1]}; the winner is then tested on{' '}
            {T.oos_period[0]} to {T.oos_period[1]}. A combination is eligible only if its filter took at least 8
            rolls in sample, which leaves {T.bh.tested} of the {T.combinations}. Two objectives: mean return on the
            rolls taken minus on all rolls, and growth against a book spending the same total.
          </li>
          <li>
            <strong>P-values.</strong> For one combination, the out-of-sample rolls are resampled {bootReps.toLocaleString('en-US')}{' '}
            times by circular block bootstrap in blocks of {E.block_rolls}; each resample’s statistic is the mean
            return of the rolls the filter took minus the mean of all rolls (a resample in which the filter takes
            nothing is dropped). The one-sided p is (b + 1) ÷ (R + 1), where b counts resamples at or below zero, so
            no p is ever 0. It is a percentile-bootstrap p — the share of the bootstrap distribution on the wrong side
            of zero — not a test under an imposed null, so read it as a ranking of evidence. Every eligible
            combination’s p then goes through Benjamini–Hochberg at a {Math.round(T.bh.fdr * 100)}% false-discovery
            rate (research/backtest/multiple_testing.py).
          </li>
          <li>
            <strong>Signal definitions.</strong> <em>VIX stretch</em>: (VIX close − its 20-day mean) ÷ its 20-day
            standard deviation (transforms/vix.py). <em>Put IV ÷ VIX</em>: the bought put’s Black–Scholes implied vol
            at its ask (3-month bill rate, first vintage; flat {pct(E.dividend_yield_assumed)} yield) over VIX ÷ 100.{' '}
            <em>Implied tail index α</em>: the Surface’s fit (research/surface/implied_alpha.py, docs/adr/0026) of
            the Pareto exponent that best prices the same session’s deeper put mids relative to one anchor — the
            listed put nearest 5% OTM on the first expiry at least 28 days out. A lower α means the market is
            pricing a fatter tail. The fit refuses on thin or noisy chains (about 3 entry sessions in 10 here), and a
            filter never takes a roll it has no reading for. <em>Regime sets</em>: calm; elevated; crisis; not calm
            (elevated or crisis); not crisis (calm or elevated).
          </li>
        </ol>
      </section>

      <section className="pl-section" id="pitch-standalone">
        <h3>1. Buying puts on their own</h3>
        <p className="pl-note">
          Return per dollar of premium: −1.00 means every dollar lost. “Best” is the single best roll and the day
          it was bought. “Refused” counts sessions where no listed put passed the guards.
        </p>
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
                <th className="num">Ask, % spot</th>
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
          <strong>The calendar decides the sign of the far-OTM rules.</strong> Buying a 4-week 20% OTM put on every
          session instead of on a monthly cycle ({SPY.every_day['20pct_4w_hold']!.entry_days.toLocaleString('en-US')}{' '}
          entries, overlapping) averages {fmtFixed(SPY.every_day['20pct_4w_hold']!.mean_roi)}, and only{' '}
          {SPY.every_day['20pct_4w_hold']!.phases_positive} of {SPY.every_day['20pct_4w_hold']!.phases} ways to
          phase a 20-session cycle come out positive. At 15% OTM: {fmtFixed(SPY.every_day['15pct_4w_hold']!.mean_roi)}
          , {SPY.every_day['15pct_4w_hold']!.phases_positive} of 20. At 5% OTM,{' '}
          {SPY.every_day['5pct_4w_hold']!.phases_positive} of 20.
        </p>
        <p className="pl-note">
          <strong>By period.</strong> 20% OTM held to expiry:{' '}
          {Object.entries(SPY.standalone['20pct_4w_hold']!.by_period).map(([p, v]) => (
            <span key={p}>
              {p} mean {fmtFixed(v.mean_roi)} ({plural(v.hits, 'winner', 'winners')} in {v.n});{' '}
            </span>
          ))}
          winners fall in {periodsWithWinners('20pct_4w_hold')} of {Object.keys(SPY.standalone['20pct_4w_hold']!.by_period).length}{' '}
          periods.
        </p>
      </section>

      <section className="pl-section" id="pitch-hedge">
        <h3>2. Puts as a hedge on the index</h3>
        <p className="pl-note">
          Unhedged SPY over {SPY.hedge.window[0]} to {SPY.hedge.window[1]}: growth {pct(SPY.hedge.unhedged_growth, 2)}{' '}
          a year, worst drawdown {pct(SPY.hedge.unhedged_max_dd)} ({SPY.hedge.unhedged_max_dd_span[0]} to{' '}
          {SPY.hedge.unhedged_max_dd_span[1]}). Growth is shown as the change against that; drawdown is each book’s
          own.
        </p>
        <div className="pl-scroll">
          <table className="pl-table" data-testid="pitch-hedge">
            <thead>
              <tr>
                <th>SPY rule</th>
                <th className="num">Budget/roll</th>
                <th className="num">Premium/yr</th>
                <th className="num">Growth vs unhedged</th>
                <th className="num">Worst drawdown</th>
              </tr>
            </thead>
            <tbody>
              {SPY.hedge.configs.map((c) => (
                <tr key={`${c.rule}@${c.budget}`} className={c.edge > 0 ? 'is-winner' : undefined}>
                  <RuleCell rule={c.rule} />
                  <td className="num">{(c.budget * 100).toFixed(2)}%</td>
                  <td className="num">{c.premium_pct_per_year.toFixed(1)}%</td>
                  <td className={`num ${c.edge < 0 ? 'pl-neg' : ''}`}>{pp(c.edge)}</td>
                  <td className="num">{pct(c.max_dd)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="pl-note">
          <strong>Why some hedged books fall further than SPY alone.</strong> A book’s worst drawdown runs from its
          own peak, and the dates say what each one measures:{' '}
          {deeperDd
            .map((c) => `${ruleShort(c.rule)} at ${(c.budget * 100).toFixed(2)}%, ${pct(c.max_dd)} from ${c.max_dd_span[0]} to ${c.max_dd_span[1]}`)
            .join('; ')}
          .
          The March 2020 peaks are a held put marked at its bid the day before it expired into a rebound: the gain on
          paper was given back at settlement, then the index fell further. The ones that start in 2015 are premium —
          up to 12% of the book a year — spent through a falling market.
        </p>
        <p className="pl-note">
          {beaters.map((c) => (
            <span key={`${c.rule}@${c.budget}`}>
              {ruleLabel(c.rule)} at {(c.budget * 100).toFixed(2)}%: {pp(c.drop_best!.edge)} with the{' '}
              {c.drop_best!.best_entry} roll, {pp(c.drop_best!.edge_without_best)} without it.{' '}
            </span>
          ))}
          QQQ ({QQQ.hedge.window[0]} to {QQQ.hedge.window[1]}): {QQQ.hedge.n_beat} of {QQQ.hedge.configs.length}{' '}
          configurations beat unhedged.
        </p>
        <p className="pl-note">
          <strong>Cboe’s indices, priced from real trades, agree.</strong> Over {E.cboe.span[0]} to {E.cboe.span[1]}:
          SPX price index {pct(E.cboe.SPX.growth, 2)} a
          year (drawdown {pct(E.cboe.SPX.max_dd)}); PPUT, the S&amp;P 500 with a monthly 5% OTM put,{' '}
          {pct(E.cboe.PPUT.growth, 2)} ({pct(E.cboe.PPUT.max_dd)}); PPUT3M {pct(E.cboe.PPUT3M.growth, 2)} (
          {pct(E.cboe.PPUT3M.max_dd)}); VXTH, the index with VIX calls, {pct(E.cboe.VXTH.growth, 2)} (
          {pct(E.cboe.VXTH.max_dd)}). SPX here is price only and PPUT includes dividends, so the gap is understated.
          The Book tab tests the right question — does any mix of program and index out-grow the index alone — and
          on {LIVE.read_on} answered:{' '}
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
          {C08.roll_dates} monthly roll dates from {C08.first}, one expiry each (26–40 days), through the platform’s{' '}
          <code>OptionQuotesSource</code> guards (open interest included). “Buy the next monthly put” and hold it to
          expiry. Its limits: no daily quotes, so drawdowns are measured on roll dates only; no expiry-day spot, so
          SPY at expiry is estimated as SPY at entry × the SPX move; 2008-03 has no roll date, so that month is
          unhedged; the licence is unresolved, so this is research use only. Unhedged, the book ends at{' '}
          {fmtFixed(C08['5pct'].unhedged.terminal, 3)} of its starting wealth, with a {pct(C08['5pct'].unhedged.max_dd)} drawdown.
        </p>
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
                <th className="num">Ends at</th>
                <th className="num">Without best roll</th>
                <th className="num">Worst drawdown</th>
              </tr>
            </thead>
            <tbody>
              {c08Rules.flatMap(([k, r]) =>
                r.books.map((b, i) => (
                  <tr key={`${k}@${b.budget}`} className={b.terminal > r.unhedged.terminal ? 'is-winner' : undefined}>
                    <td>{i === 0 ? `${k.replace('pct', '')}% OTM` : ''}</td>
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
                    <td className="num">{fmtFixed(b.terminal, 3)}</td>
                    <td className="num">{b.terminal_without_best === undefined ? '—' : fmtFixed(b.terminal_without_best, 3)}</td>
                    <td className="num">{pct(b.max_dd)}</td>
                  </tr>
                )),
              )}
            </tbody>
          </table>
        </div>
        <p className="pl-note">
          Here the hedge does what it is bought for: {C08.n_beat} of {C08.configs} books end ahead of unhedged, and{' '}
          {c08Rules.flatMap(([, r]) => r.books.filter((b) => b.max_dd > r.unhedged.max_dd)).length} cut the
          drawdown.{' '}
          {c08OneMonth && (
            <>
              Every book that ends ahead rests on the roll bought {c08Beaters[0]!.r.best_entry}, the week Lehman failed: leave
              that month unhedged and each ends below {fmtFixed(C08['5pct'].unhedged.terminal, 3)}.{' '}
            </>
          )}
          That is the pattern of the 2010–2023 study — one crash month carries the result — seen once more, on 23
          rolls. It is evidence the hedge can pay in a crash, not that it pays over a cycle; whether 2008–09 and
          2010–2023 together beat unhedged growth needs one continuous book across both datasets, which has not been
          built.
        </p>
      </section>

      <section className="pl-section" id="pitch-timing">
        <h3>3. Can timing rescue it?</h3>
        <p className="pl-note">
          The in-sample winner on mean return
          {selDg.rule === sel.rule && selDg.gate === sel.gate ? ' — and on growth —' : ''} was {ruleLabel(sel.rule)},
          bought only when {gateLabel(sel.gate)}.
          In sample it took {sel.is.taken} of {sel.is.n} rolls and raised the mean return from{' '}
          {fmtFixed(sel.is.mean_roi_all)} to {num(sel.is.mean_roi_taken)}. Out of sample it took {sel.oos.taken}{' '}
          of {sel.oos.n}: {fmtFixed(sel.oos.mean_roi_all)} to {num(sel.oos.mean_roi_taken)}, an improvement of{' '}
          {num(sel.oos.roi_diff)} with a 95% interval of {ci(sel.oos_ci95)} (p = {sel.oos_p.toFixed(3)}),
          resting on {sel.oos.hits_taken} winning rolls.
          {sel.oos_mean_taken_without_best !== null && (
            <> Without its single best out-of-sample roll the filtered mean is {fmtFixed(sel.oos_mean_taken_without_best)}.</>
          )}{' '}
          {selDg.rule === sel.rule && selDg.gate === sel.gate
            ? 'As a hedge'
            : `The growth objective's winner, ${ruleLabel(selDg.rule)} when ${gateLabel(selDg.gate)}`}
          , against a book spending the same: {pp(selDg.oos.dg)} out of sample, interval{' '}
          [{pp(selDg.oos_ci95[0]!)}, {pp(selDg.oos_ci95[1]!)}], p = {selDg.oos_p.toFixed(3)}.
        </p>
        <p className="pl-note">
          Across all {sel.eligible} combinations with enough in-sample trades, {Math.round(sel.share_positive_oos * 100)}%
          improved out of sample, and the rank correlation between in-sample and out-of-sample results is{' '}
          {fmtFixed(sel.spearman_is_oos)}: an in-sample ranking says almost nothing about the next seven years.{' '}
          {T.bh.p_below_05} of {T.bh.tested} have a raw p below 0.05 — about the {Math.round(T.bh.tested * 0.05)} that
          chance alone would give if the tests were independent (they are not: they share the same rolls). Benjamini–Hochberg at {Math.round(T.bh.fdr * 100)}% keeps{' '}
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
          perfect-foresight filter that takes the best third of out-of-sample rolls reaches p ={' '}
          {Math.max(...soldRules.map(([, o]) => o.p)).toFixed(4)} or less on every sold rule, so those tests had power;
          on the held-to-expiry rules it gets only{' '}
          {heldRules.map(([r, o]) => `${o.p.toFixed(2)} (${ruleShort(r)}, ${plural(o.oos_winners, 'winner', 'winners')})`).join(', ')}
          , because with one or a few winning rolls a resample either contains them or does not. Even a perfect
          filter there stays above the first BH step, so those {T.bh.tested - T.bh.sell_rules.tested} tests carry
          almost no power. Benjamini–Hochberg over the{' '}
          {T.bh.sell_rules.tested} sold-rule tests alone keeps {T.bh.sell_rules.survivors} (smallest p{' '}
          {T.bh.sell_rules.min_p.toFixed(4)}, first step {(T.bh.fdr / T.bh.sell_rules.tested).toFixed(5)}).
        </p>
        <p className="pl-note">
          <strong>The one consistent pattern.</strong> {notCrisisSmallest.length} of the{' '}
          {T.bh.smallest_p.length} smallest p-values are the same filter — do not buy when the prior close’s regime
          is crisis (VIX at or above 28) — on {new Set(notCrisisSmallest.map((x) => x.rule)).size} different sold
          rules: it lifted their out-of-sample mean by{' '}
          {fmtFixed(Math.min(...notCrisisSmallest.map((x) => x.oos.roi_diff ?? 0)), 3)} to{' '}
          {fmtFixed(Math.max(...notCrisisSmallest.map((x) => x.oos.roi_diff ?? 0)), 3)} per dollar, taking{' '}
          {notCrisisSmallest[0]?.oos.taken} of {notCrisisSmallest[0]?.oos.n} rolls, with p from{' '}
          {Math.min(...notCrisisSmallest.map((x) => x.oos_p)).toFixed(4)} to{' '}
          {Math.max(...notCrisisSmallest.map((x) => x.oos_p)).toFixed(4)}. One direction on every rule is a coherent
          effect, not scattered hits; it still misses the BH line, and it never turns a mean positive (the best
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
            lives on tails is judged on that handful, not on the ~180 rolls.
          </li>
          <li>
            <strong>2008 is outside the main study.</strong> The daily quote panels start in 2010. 2008–09 is
            checked separately on monthly quotes (section 2b), where {C08.n_beat} of {C08.configs} books beat
            unhedged on one month; the two periods have not been joined into one book. The only window where the Book
            finds a hedge holding (VXTH, 2006–2016) also contains 2008.
          </li>
          <li>
            <strong>Simplifications.</strong> Flat {pct(E.dividend_yield_assumed)} dividend on both arms (no
            measured dividends in the lake); fractional contracts; early exercise ignored; expiry settled on the
            panel’s close; SPY and QQQ only — no single names, so no survivorship within the sample, but the choice
            of two US index ETFs over a bull market is itself hindsight.
          </li>
          <li>
            <strong>Searched space, and what changed along the way.</strong> {spyRules.length} rules × 3 budgets on
            two underlyings, the 2008–09 check (4 depths × 3 budgets), then {T.combinations} timing combinations.
            Every count is reported above. One change was made after seeing a result: an exploratory timing run drew
            2,000 bootstrap resamples per test and wrote p = 0 when none fell at or below zero, and with that, one
            combination (5% OTM 8-week, not-crisis filter, p reported as 0) passed BH. A p of 0 claims more than 2,000
            draws can resolve, and the floor of 1/2,001 sat above the first BH step, so no honest p could pass at all.
            The p was corrected to (b + 1) ÷ (R + 1) and R raised to {bootReps.toLocaleString('en-US')}, which puts
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
            they cannot count as evidence. Even priced at VIX, which the Workspace does not use, a ~30-day 10% OTM
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
          <code>make pitch-evidence AS_OF={E.as_of}</code> reads the snapshots named above (lake read only) and
          rewrites <code>frontend/src/content/pitch_evidence.json</code>, which is all this page reads for the
          study. That file was generated by commit <code data-testid="pitch-code-sha">{E.code.sha.slice(0, 12)}</code>
          {E.code.dirty ? ' with uncommitted changes to scripts/ or src/ — not reproducible as committed' : ''}. The live readings: <code>GET /api/putlab/regime-verdict</code>,{' '}
          <code>/api/putlab/roll-schedule/marked</code> and <code>/api/putlab/hedge-overlay</code> on tail-lab.fly.dev,
          read {LIVE.read_on}. The roll engine is tested in tests/test_scripts_pitch_evidence.py.
        </p>
      </section>
    </article>
  )
}
