// The ONE source of truth for every concept the Put Lab dashboard explains.
// Feeds both the inline ⓘ popovers and the "Learn" tab. Every formula here is
// transcribed from the backend code (src/tail_lab/research/...), not
// approximated -- keep them in sync if the metrics change.
//
// Pure data, no imports.

export type ConceptCategory =
  | 'thesis'
  | 'metric'
  | 'option'
  | 'result'
  | 'regime'
  | 'bakeoff'
  | 'portfolio'
  | 'context'

export interface Concept {
  id: string // stable kebab/snake key, referenced from components (e.g. 'downside_beta')
  term: string // display name, e.g. 'Downside beta (β⁻)'
  category: ConceptCategory
  short: string // one sentence -- the gist, shown as the popover header / tooltip
  formula?: string // exact formula as a readable unicode string; omit if none
  intuition: string // 2-5 sentences: the DEEP plain-English explanation
  howToRead: string // 1-3 sentences: practical -- what a high vs low value means
  seeAlso?: string[] // related concept ids
}

// Ordered by category following CATEGORY_ORDER, so CONCEPT_LIST is already
// display-ordered.
/** Every reason a name's dividend yield can be "unknown" (and its roll priced
 *  at q = 0). One list, read by the glossary, the price build and the ranking's
 *  q = 0 note, so no copy can drop a cause; the docstring of
 *  research/surface/reading.py `rate_note_for` names the same causes. */
export const UNKNOWN_Q_CAUSES =
  'no dividend data for this name or for this date, a single dividend so far or since a long pause (no frequency to read), or a dividend the close cannot support'

export const CONCEPTS = {
  // ---------------------------------------------------------------- thesis
  fragility_thesis: {
    id: 'fragility_thesis',
    term: 'The fragility thesis',
    category: 'thesis',
    short:
      'Do not predict when a crash comes -- hold cheap convexity continuously on the names most likely to detonate when one does.',
    intuition:
      'Timing a market dislocation is notoriously hard, so this strategy refuses to try. Instead it holds out-of-the-money put convexity continuously and concentrates it on the most fragile names -- the ones whose own history says they fall hardest when the market falls. The bet is that the fragility itself produces the payoff whenever the (untimed) dislocation eventually hits, and that the market systematically underprices that tail. The whole platform is therefore a ranking-and-measurement tool, not a forecaster: it never predicts the date, only ranks who is most exposed to it.',
    howToRead:
      'Read the screen as sorting exposure, not calling a top. A name high on the fragility screen is one you would want convexity on if you believe a tail event is coming at some unknown time -- the screen deliberately says nothing about when.',
    seeAlso: ['fragility_score', 'oom_put', 'model_priced', 'in_sample'],
  },
  in_sample: {
    id: 'in_sample',
    term: 'In-sample vs out-of-sample',
    category: 'thesis',
    short:
      'The bake-off measures how well a metric sorted payoffs over the same past window it is scored on -- an association, not a forward signal.',
    intuition:
      'A fragility metric and the realized put payoff it is judged against are both computed over the same trailing lookback. That makes any winner an in-sample, cross-sectional association: it describes how well the measure sorted realized payoffs over this specific history, which is legitimate evidence for choosing which screen to rank on. It is not a walk-forward backtest and carries no guarantee the winner keeps predicting payoffs next year. Point-in-time correctness is still respected end to end -- no future data leaks into any price path, benchmark, or regime label -- but "sorted 2022-2026 payoffs well" must never be read as "will sort next year well".',
    howToRead:
      'Treat a bake-off winner as a screen chooser, not a tradable signal. If someone quotes the winning metric as proof the strategy works going forward, that is the exact overreach this caveat exists to block.',
    seeAlso: ['metric_bakeoff', 'spearman', 'model_priced'],
  },

  // ---------------------------------------------------------------- metric
  fragility_score: {
    id: 'fragility_score',
    term: 'Fragility score (composite)',
    category: 'metric',
    short:
      'A 0-100 blend of five sensitivity metrics -- each name ranked cross-sectionally against the rest of the universe, most fragile first.',
    formula:
      'fragility_score = mean over {downside_beta, co_skewness, tail_beta, downside_capture, vol_beta} of frac_rank(metric); frac_rank = 1 − pos/(N−1), pos = 0 for most fragile',
    intuition:
      'No single sensitivity metric is trusted on its own, so the composite averages five of them. Each metric is turned into a cross-sectional fractional rank -- a 0..1 position of this name against every other name in the universe, where 1 is the most fragile -- and the five ranks are averaged equally. Ranking (rather than averaging raw values) puts metrics on different scales onto common footing and makes the score robust to a single metric blowing up. Co-kurtosis is computed and displayed but deliberately left OUT of the blend (see its own entry).',
    howToRead:
      'Higher means more fragile relative to the universe: a score near 100 is among the most crash-sensitive names screened, near 0 among the most cushioned. A name with too little overlapping history to estimate the metrics shows no score and sorts last.',
    seeAlso: ['downside_beta', 'co_skewness', 'tail_beta', 'downside_capture', 'vol_beta', 'co_kurtosis'],
  },
  downside_beta: {
    id: 'downside_beta',
    term: 'Downside beta (β⁻)',
    category: 'metric',
    short:
      'Ordinary beta computed only over the days the market itself fell -- how much the name moves when things are already going wrong.',
    formula: 'β⁻ = cov(rₐ, r_b | r_b < 0) / var(r_b | r_b < 0)   (sample moments, ddof=1)',
    intuition:
      'Ordinary market beta is estimated over the whole sample, so a name that only rides the market up looks just as "sensitive" as one that plunges with it down. Downside beta (Bawa & Lindenberg 1977) restricts the same covariance/variance ratio to the subset of days the benchmark (SPY) closed below zero -- exactly the regime an OOM put is bought to pay off in. It answers "when the market is falling, how amplified is this name" rather than mixing up-days and down-days into one number.',
    howToRead:
      'Higher is more fragile. β⁻ above 1 means the name falls more than the market on down days; below 1 means it cushions. Undefined (blank) when fewer than two market down-days are in the window.',
    seeAlso: ['tail_beta', 'downside_capture', 'fragility_score'],
  },
  co_skewness: {
    id: 'co_skewness',
    term: 'Co-skewness',
    category: 'metric',
    short:
      'How the name co-moves with the squared swings of the market -- negative means it falls precisely when the market is whipping around.',
    formula:
      'coskew = E[(rₐ − μₐ)(r_b − μ_b)²] / (σₐ · σ_b²)   (population moments, ddof=0)',
    intuition:
      'Co-skewness (Harvey & Siddique 2000) pairs the name against the market benchmark deviation SQUARED, so it registers whether the name swings hardest in the same periods the market swings hardest -- regardless of the market direction. A negative co-skewness means the name tends to fall exactly when the market is already volatile: crash-prone in precisely the sense a downside put-buying screen cares about. Population moments (n divisor) are used throughout, so the self-case coskew(x, x) reduces exactly to x own biased skewness -- a checkable identity.',
    howToRead:
      'Unlike the other four, MORE NEGATIVE is more fragile here (the composite flips its sign so it ranks fragile-when-low). A large negative value flags a name that reliably breaks in turbulent markets.',
    seeAlso: ['co_kurtosis', 'fragility_score'],
  },
  co_kurtosis: {
    id: 'co_kurtosis',
    term: 'Co-kurtosis (shown, but excluded)',
    category: 'metric',
    short:
      'Tail amplification vs the market -- displayed for reference but deliberately kept OUT of the composite because it flags the wrong thing for this screen.',
    formula:
      'cokurt = E[(rₐ − μₐ)(r_b − μ_b)³] / (σₐ · σ_b³)   (population moments, ddof=0)',
    intuition:
      'Co-kurtosis (Harvey & Siddique 2000) puts the market deviation to the CUBE, which keeps its sign: a big negative market shock cubed is large and negative, and a name that also falls then produces a large positive co-kurtosis -- "this name detonates in a market tail event". The catch is what it rewards: co-kurtosis WITH the benchmark scores names that co-move with the market own tails, which makes broad indices (SPY, DIA, QQQ) look most fragile -- exactly backwards for a single-name OOM-put screen that wants names falling harder than the market. It is shown because it is a legitimate fragility lens, but it flags index-like breadth, not single-name fragility.',
    howToRead:
      'Higher is more fragile in principle, but do not rank on it here: it was the worst screen in the in-sample bake-off, and its exclusion from the composite is structural (it measures index co-movement), not curve-fit. Use it as context, not as a selector.',
    seeAlso: ['co_skewness', 'fragility_score', 'metric_bakeoff'],
  },
  tail_beta: {
    id: 'tail_beta',
    term: 'Tail beta',
    category: 'metric',
    short:
      'Beta restricted to only the market worst ~10% of days -- how much the name amplifies the market extreme moves, not merely its down days.',
    formula:
      'tail_beta = cov(rₐ, r_b | r_b ≤ q₁₀) / var(r_b | r_b ≤ q₁₀),  q₁₀ = 10th percentile of r_b   (population, ddof=0)',
    intuition:
      'Downside beta uses every market down-day; tail beta sharpens that to only the benchmark worst tail_pct percent of days (default 10%), the extreme left tail rather than the merely-below-zero days. The threshold is the 10th percentile of market returns, and days at or below it form the tail subset over which the covariance/variance beta is computed. This answers a narrower question: not "how much does the name move when the market is down" but "how much does it amplify the market WORST moves specifically" -- the sharpest tail regime an OOM put exists to pay off in.',
    howToRead:
      'Higher is more fragile. A tail beta well above 1 means the name accelerates in the market crashiest days. Needs at least eight paired observations and two days in the tail subset, else it is blank.',
    seeAlso: ['downside_beta', 'fragility_score'],
  },
  downside_capture: {
    id: 'downside_capture',
    term: 'Downside capture',
    category: 'metric',
    short:
      'On market down days, the ratio of the name average return to the market average -- above 1 means it captures more than 100% of the decline.',
    formula: 'downside_capture = mean(rₐ | r_b < 0) / mean(r_b | r_b < 0)',
    intuition:
      'The downside capture ratio is a standard fund-management statistic: on the days the benchmark fell, how much of that decline did the name "capture," as a ratio of the two mean returns. Because both means are negative on down days, a ratio above 1 means the name fell MORE than the market -- fragile in exactly the sense this screen wants -- while below 1 means it cushioned the declines. It is the most intuitive of the five and reads directly as a percentage of the market drop.',
    howToRead:
      'Higher is more fragile: above 1 = amplifies the market losses, below 1 = dampens them, near 1 = moves with it. Undefined (blank) with fewer than two market down-days.',
    seeAlso: ['downside_beta', 'fragility_score'],
  },
  vol_beta: {
    id: 'vol_beta',
    term: 'Vol beta',
    category: 'metric',
    short:
      'Beta against day-over-day VIX changes rather than the benchmark own returns -- how hard the name reacts to a pure fear spike.',
    formula: 'vol_beta = cov(rₐ, Δvix) / var(Δvix)   (sample moments, ddof=1)',
    intuition:
      'Every other metric here measures co-movement with the benchmark OWN returns or their shape. Vol beta measures something different: co-movement with the volatility factor itself -- day-over-day percentage changes in VIX -- independent of what SPY did that day. Two names can share an identical downside beta yet react very differently to a VIX spike with no accompanying SPY move; vol beta is what tells them apart. It needs no SPY history to compute (VIX is the only regressor), so it can be estimated even when the benchmark comparison cannot.',
    howToRead:
      'Unlike downside beta, MORE NEGATIVE is more fragile here (equities fall as VIX rises, so a more negative vol beta means a harder reaction to a fear spike -- the composite flips its sign so it ranks fragile-when-low, the same convention as co-skewness). Undefined (blank) when fewer than two paired observations are available or VIX was flat over the window.',
    seeAlso: ['downside_beta', 'fragility_score'],
  },

  // ---------------------------------------------------------------- option
  oom_put: {
    id: 'oom_put',
    term: 'Out-of-the-money (OOM) put',
    category: 'option',
    short:
      'A put struck a set percentage below spot -- cheap insurance that pays only if the underlying falls past the strike.',
    formula: 'strike = spot × (1 − moneyness_pct/100);   payoff at expiry = contracts × max(strike − spot_at_expiry, 0)',
    intuition:
      'An out-of-the-money put has a strike below the current spot, set here by moneyness_pct (e.g. 5 means the strike sits 5% under spot). It is worthless unless the underlying falls THROUGH that strike before expiry, which is why it is cheap -- and why buying it is a bet on a sharp drop, not a drift. The strategy spends a fixed budget (notional) on these puts, so the number of contracts is notional divided by the per-put model premium; the payoff at expiry is the intrinsic value, contracts times how far spot ended below the strike.',
    howToRead:
      'A larger moneyness_pct means a deeper, cheaper, longer-shot put (bigger payoff multiple when it hits, but hits less often). The strike shown in dollars is spot scaled down by that percentage.',
    seeAlso: ['rolling', 'model_priced', 'biggest_payoff', 'roi_on_premium'],
  },
  rolling: {
    id: 'rolling',
    term: 'Rolling the puts',
    category: 'option',
    short:
      'Buy a put, hold it to expiry, collect any payoff, immediately buy the next one -- a continuous, non-overlapping chain over the lookback.',
    formula: 'tenor_days = round(tenor_weeks × 5);  re-enter at each expiry (non-overlapping);  n_cycles rolls total',
    intuition:
      'To hold convexity continuously the engine rolls: at each entry it spends the fixed budget on a put expiring tenor_weeks out (converted to trading days at five per week), holds it to expiry, banks the intrinsic payoff, and re-enters the next roll on the expiry date. The rolls are non-overlapping -- one position at a time -- so a shorter tenor produces many more cycles over the same window (a 1-week leg rolls roughly four times as often as a 4-week leg). Only the trailing lookback_years is actually traded; earlier history exists just to warm up the volatility estimate for the first entry.',
    howToRead:
      'Shorter tenor = more rolls = more premium spent but more chances to catch a drop; longer tenor = fewer, larger bets. n_cycles is how many complete rolls fit in the window.',
    seeAlso: ['oom_put', 'model_priced', 'bleed'],
  },
  model_priced: {
    id: 'model_priced',
    term: 'Model-priced (not real quotes)',
    category: 'option',
    short:
      'Every premium is a Black-Scholes price using trailing realized volatility as an IV stand-in -- not a real historical option quote.',
    formula:
      'premium = BlackScholesPut(spot, strike, T, r=0.04, σ, q);  σ = clip(annualized 20-day realized vol, 0.06, 2.0);  q = the name\'s dividend yield',
    intuition:
      'Real historical option quotes are paid data the project does not yet have, so v1 prices every put with closed-form Black-Scholes (ADR 0004). The implied-volatility input is a proxy: the trailing 20-day realized volatility of the underlying (sample std of daily log returns, annualized by √252), floored at 0.06 and capped at 2.0 so a dead-calm or crashing short window cannot imply free or absurd puts. The point-in-time honesty holds -- the vol at each entry uses only past prices -- but there is a deep caveat: the whole thesis is that the market MISPRICES tail risk, and a model priced off its own vol inputs cannot see that mispricing.',
    howToRead:
      'Read the put P&L as a RELATIVE ranking of which fragility metric sorts payoffs best, never as literal expected P&L. Absolute ROI numbers will change (likely a lot) once real option quotes replace the model.',
    seeAlso: ['oom_put', 'roi_on_premium', 'dividend_yield', 'in_sample', 'fragility_thesis'],
  },
  dividend_yield: {
    id: 'dividend_yield',
    term: 'Dividend yield (q)',
    category: 'option',
    short:
      'The continuous yield the stock pays away to its holders -- it lowers the forward price, so every put on a payer is worth more than a q = 0 price says.',
    formula:
      'q = −ln(1 − D / S);  D = sum of the last N dividends (N = 1, 2, 4 or 12 a year), each on the share basis of the day q is measured;  S = that day\'s as-traded close',
    intuition:
      'A put is a claim on where the stock will be at expiry, and a stock that pays a dividend drops by it on the ex-date -- so a payer is expected to end lower than a non-payer with the same price and volatility, and its puts cost more. Until October 2026 this lab priced every name at q = 0, which under-priced puts on income names worst: scored against the exchange\'s own deltas, HYG was off by 0.197 and TLT by 0.060, against 0.004 for SPY. q is now read from each name\'s paid dividends (Tiingo): the last N payments at its own frequency, summed and divided by the close. It deliberately carries no forecast, so it lags a cut by up to N payments, a special dividend moves it for N payments (up if larger than a regular payment, down if smaller), and it misses a dividend falling inside a short put\'s life (the stock drops by a whole quarterly dividend, ~0.3% of spot at q ≈ 1.3%, inside a 21-day SPY put that spans an ex-date, where q·T with T = 21/252 takes only ~0.1% off the expected price -- so that put\'s model expects the stock ~0.2% of spot too high and under-prices it, while the two in three that span no ex-date expect it ~0.1% too low and over-price; the premium errors are those shifts times the delta, and they net out over a year, not roll by roll).',
    howToRead:
      `Every roll says where its q came from. "measured" is the name's own payments; "non-payer" and "suspended" are real zeros; "carried" and "stale" are the last measured value held past the data's end; "unknown" means q could not be read -- ${UNKNOWN_Q_CAUSES} -- and the roll was priced at q = 0: read those numbers as the old, cheap prices.`,
    seeAlso: ['model_priced', 'oom_put', 'put_delta'],
  },
  put_delta: {
    id: 'put_delta',
    term: 'Put delta (Δ)',
    category: 'option',
    short:
      'How much the put\'s price moves per $1 move in the stock -- and, roughly, the chance it ends in the money. A 0.10-delta put moves 10 cents per dollar.',
    formula: 'Δ = −e^(−qT) · N(−d₁),  d₁ = (ln(S/K) + (r − q + σ²/2)T) / (σ√T)',
    intuition:
      'Delta measures distance in the market\'s own units: how far the strike is, scaled by how much the stock moves. The same 5% below spot is a remote, near-worthless put when volatility is 10% and a close, expensive one when it is 40%; their deltas say so, the 5% does not. This lab uses one convention everywhere -- the Black–Scholes spot delta with the dividend yield -- and the volatility that goes in decides the answer: the backtest has only the 20-day realised vol for each past day, so its "0.10 delta" is at realised vol. The market\'s delta uses implied vol, which sits above realised and is skewed, so the market\'s 0.10-delta strike lies further below spot.',
    howToRead:
      'Closer to 0 is further out of the money and cheaper; around 0.4-0.5 is near the money (a put struck exactly at spot is not exactly 0.50: typically 0.4 to 0.5, lower for long or very volatile options; above 0.5 only when the dividend yield beats the rate by more than half the variance, q > r + σ²/2, and then mostly on short-dated puts, since the e^(−qT) factor pulls long-dated ones back under). Read every delta here with its volatility: "0.10Δ at realised vol" is not the desk\'s 10-delta put.',
    seeAlso: ['strike_rule', 'dividend_yield'],
  },
  strike_rule: {
    id: 'strike_rule',
    term: 'Strike rule',
    category: 'option',
    short:
      'How each roll picks its strike: a fixed distance below spot, or a fixed delta. One holds the distance still and lets the option change; the other holds the option still and lets the distance change.',
    formula: 'by distance: K = S · (1 − m/100);  by delta: K = S · exp(−d₁σ√T + (r − q + σ²/2)T),  d₁ = −N⁻¹(|Δ| e^(qT))',
    intuition:
      'A fixed distance is simple, but it confounds any comparison across regimes: in calm markets (12% vol) a 10%-below-spot 4-week put has a delta of 0.0006 -- a lottery ticket -- in a crisis (45% vol) 0.18, a near-the-money hedge. A verdict that a strategy "only worked in crises" may only be saying that the strike was reachable then. Picking by delta keeps the option comparable across regimes instead. Neither is free: under a delta rule a low target in high realised vol lands deeper than the flat-vol model can price (a 0.05-delta 4-week put is ~10.5% below spot at 25% vol), and the page counts those rolls beside the headline.',
    howToRead:
      'Compare a strategy under both rules before trusting a regime verdict. The ranking, the sweep grid, the Surface and the Regime view still screen by distance; only the Workspace backtest and the Bake-off can pick by delta.',
    seeAlso: ['put_delta', 'oom_put', 'model_priced', 'time_average_growth'],
  },

  // ---------------------------------------------------------------- result
  mark_to_market: {
    id: 'mark_to_market',
    term: 'Mark-to-market P&L',
    category: 'result',
    short:
      'The daily curve re-prices the open put with Black-Scholes every day, so P&L moves continuously instead of only stepping at each expiry.',
    formula: 'unrealized_k = contracts × BS(spotₖ, K, (expiry−k)/252, σₖ) − premium_paid',
    intuition:
      'The realized equity curve only knows a put value at entry (premium paid) and at expiry (intrinsic payoff), so plotting it straight-lines between those two points -- a visual lie about what happens while the position is open. The mark-to-market curve fixes that by re-pricing the SAME open put with Black-Scholes every trading day, feeding it that day current spot, the shrinking time-to-expiry, and the day vol proxy, and netting off the premium already paid. Because the model and its inputs are identical at entry and at expiry, the daily curve lands exactly on the realized equity curve value at every expiry date -- it is a strict refinement, not a different number.',
    howToRead:
      'Use the daily curve to see how the hedge actually breathed day to day -- a put can swing deep into paper profit and back to zero between rolls, which the old realized-only curve hid completely. The settlement markers (green circles) show exactly where a cycle net positive lands on this curve.',
    seeAlso: ['roi_on_premium', 'bleed', 'model_priced', 'oom_put'],
  },
  roi_on_premium: {
    id: 'roi_on_premium',
    term: 'Return on premium',
    category: 'result',
    short:
      'Total payoff minus total premium, divided by total premium -- the net return on every dollar spent on puts over the window.',
    formula:
      'roi_on_premium = (total_payoff − total_premium) / total_premium;   total_premium = n_cycles × notional',
    intuition:
      'This is the headline number: across all the rolls, how did the money spent on premium do? The denominator is the total premium budget -- the fixed per-roll notional times the number of rolls -- and the numerator is the summed intrinsic payoffs net of that budget. A hedge that never pays returns −100% (all premium burned); one whose crash payoffs exceed everything spent shows a positive ROI. It is a return on the insurance bill, not on any underlying position.',
    howToRead:
      'Negative is the normal resting state of a tail hedge -- you pay to be insured. A positive ROI means the payoffs over this window more than covered the continuous premium bleed. Remember it is model-priced, so treat it as relative.',
    seeAlso: ['bleed', 'hit_rate', 'biggest_payoff', 'model_priced', 'annualized_return'],
  },
  annualized_return: {
    id: 'annualized_return',
    term: 'Annualized return',
    category: 'result',
    short:
      'The constant yearly rate that compounds to the total return on premium over the lookback -- geometric annualization, not a simple divide-by-years.',
    formula: 'annualized_return = (1 + roi_on_premium)^(1/years) − 1',
    intuition:
      'Return on premium is a TOTAL figure over the whole lookback window (e.g. −72% over 4 years) -- it does not by itself say how that loss (or gain) was paced year to year, and a longer window will mechanically show a larger-looking total even at the same yearly rate. The annualized return answers that: the single constant yearly rate which, compounded over the lookback, reproduces the total. It is geometric, not total-divided-by-years, because losses and gains compound multiplicatively -- premium already spent does not come back to be spent again. total_roi cannot go below −1 (you cannot lose more than the premium paid), so the annualized figure is capped at −100%/yr in that limit.',
    howToRead:
      'A tail hedge is expected to bleed, so the annualized figure is usually negative -- read it as the yearly carry cost of holding the insurance. An occasional window shows a positive annualized return when a crash payoff more than covered the period spent bleeding.',
    seeAlso: ['roi_on_premium', 'bleed'],
  },
  annualized_so_far: {
    id: 'annualized_so_far',
    term: 'Annualized return so far',
    category: 'result',
    short:
      'At each roll’s expiry, the yearly rate the strategy had earned on premium from the very first entry up to that date — so you can see under what horizon it would have been good.',
    formula:
      'at roll k: roi_so_far = Σ(net of first k rolls) / (k × notional);  years_elapsed = (expiryₖ − first_entry)/365.25;  annualized_so_far = (1 + roi_so_far)^(1/years_elapsed) − 1   (skipped until years_elapsed ≥ 0.25)',
    intuition:
      'The headline annualized return is a single number over the whole lookback; this curve unrolls it through time. At every roll’s expiry it takes the cumulative net-of-brokerage P&L per premium dollar spent SO FAR and annualizes it geometrically over the actual time elapsed since the first entry. Plotted on the tape’s P&L pane against a right-hand percent axis, with the S&P buy-and-hold hurdle drawn as a dashed line, it answers "over which holding horizons did this hedge actually pay, and when did it clear the market?" The earliest points are dropped: annualizing a sub-quarter ROI raises (1+roi) to a huge power and manufactures a nonsense rate, so the curve starts once a quarter-year of horizon exists.',
    howToRead:
      'Read it as horizon-dependence, not a forecast. Where the cool line sits above the dashed S&P hurdle, holding the hedge to that date beat buying the index; where it dives, that horizon was pure carry cost. It swings a lot early (short horizons annualize violently) and settles toward the headline annualized return at the full window — and it is model-priced, so treat levels as relative.',
    seeAlso: ['annualized_return', 'roi_on_premium', 'sharpe', 'model_priced'],
  },
  sharpe: {
    id: 'sharpe',
    term: 'Sharpe ratio (annualized)',
    category: 'result',
    short:
      'Excess per-roll return over the risk-free rate, divided by the volatility of those returns, annualized — a rough risk-adjusted lens, reported for completeness.',
    formula:
      'Sharpe = (mean(r) − rf_per_roll) / std(r, ddof=1) × √(rolls_per_year);  r = net/notional per roll;  rf_per_roll = 0.04 / rolls_per_year;  rolls_per_year = n_cycles / years  (None if < 2 rolls or zero dispersion)',
    intuition:
      'Sharpe measures return per unit of risk: the average per-roll return on premium in excess of the risk-free carry (the 4% rate spread across the year’s rolls), divided by the standard deviation of those per-roll returns, then scaled by √(rolls per year) to annualize. It is a completeness stat, NOT a headline — and it fits a convex tail hedge poorly. Sharpe assumes roughly normal, symmetric returns, but this strategy’s returns are the opposite: many small premium bleeds punctuated by rare, enormous payoffs (fat right tail). That lumpiness makes the standard deviation a misleading "risk" denominator, so a low Sharpe here is not the indictment it would be for a diversified long book.',
    howToRead:
      'Use it only as a rough cross-check, not a verdict. A tail hedge can post a poor or negative Sharpe while still being valuable insurance, because Sharpe penalizes the very convexity (rare huge wins) that is the point. It is blank with fewer than two rolls or when every roll returned the same.',
    seeAlso: ['annualized_return', 'annualized_so_far', 'roi_on_premium', 'bleed'],
  },
  hit_rate: {
    id: 'hit_rate',
    term: 'Hit rate',
    category: 'result',
    short:
      'The fraction of rolls whose payoff exceeded the premium paid -- how often a cycle more than paid for itself.',
    formula: 'hit_rate = #{rolls with net > 0} / n_cycles, net = payoff − notional − brokerage',
    intuition:
      'A tail hedge is expected to lose small most of the time and win big rarely, so the hit rate is deliberately low by design. A roll "hits" only when its payoff clears the whole premium budget spent on it (net positive), not merely when the put finishes with any intrinsic value. It pairs with the biggest-payoff multiple: a good tail strategy can have a tiny hit rate and still be profitable if the rare hits are enormous.',
    howToRead:
      'Do not read a low hit rate as failure -- read it alongside ROI and biggest payoff. A 10% hit rate with a 30x biggest payoff is the convexity working as intended; a low hit rate with no big payoff is just bleed.',
    seeAlso: ['roi_on_premium', 'biggest_payoff', 'bleed'],
  },
  bleed: {
    id: 'bleed',
    term: 'Bleed (carry cost)',
    category: 'result',
    short:
      'The steady loss of premium between payoffs -- shown as the worst losing streak and, for baskets, the max drawdown of cumulative P&L.',
    formula:
      'worst_bleed_streak = longest run of consecutive rolls with net < 0;   combined_max_drawdown = min over t of (cum_pnl_t − running_peak_t) ≤ 0',
    intuition:
      'Convexity is not free: between the rare payoffs, every expiring put that finishes worthless burns its premium, and that steady drain is the carry cost of holding the hedge -- the "bleed". The single-name view reports it as the worst bleed streak, the longest run of consecutive losing rolls. The basket and portfolio views report it as a max drawdown: the largest peak-to-trough drop of the cumulative realized-P&L curve (always ≤ 0). Both answer the same practical question -- how much pain do you endure waiting for the hedge to pay?',
    howToRead:
      'Deeper (more negative) drawdown or a longer streak means more premium eaten before relief -- the sizing/tenor cost you must be willing to sit through. Compare it against the biggest payoff to judge whether the eventual convexity justifies the wait.',
    seeAlso: ['biggest_payoff', 'roi_on_premium', 'diversification', 'rolling'],
  },
  biggest_payoff: {
    id: 'biggest_payoff',
    term: 'Biggest payoff',
    category: 'result',
    short:
      'The largest single-roll payoff as a multiple of the premium spent on it -- the convexity, i.e. the size of the best hit.',
    formula: 'biggest_payoff_mult = max over rolls of (payoff / notional)',
    intuition:
      'This is the convexity in one number: the best single roll expressed as how many times the premium it returned. Tail hedging lives or dies on this -- the strategy accepts many small losses precisely because one roll can pay a large multiple when a crash hits its OOM strike. A high multiple is the whole point of buying cheap, deep puts; without it the low hit rate would just be dead money.',
    howToRead:
      'A large multiple (say 10x or more) is the convex payoff justifying the bleed; a multiple near 1 means even the best roll barely paid back, so the hedge never really fired over this window.',
    seeAlso: ['hit_rate', 'bleed', 'oom_put', 'roi_on_premium'],
  },

  // ---------------------------------------------------------------- regime
  regime: {
    id: 'regime',
    term: 'Market regime (calm / elevated / crisis)',
    category: 'regime',
    short:
      'A simple VIX-level label on every day -- the market stress backdrop each roll was entered into.',
    formula: 'calm: VIX < 17   ·   elevated: 17 ≤ VIX < 28   ·   crisis: VIX ≥ 28',
    intuition:
      'To ask whether a strategy pays off in more than one kind of market, every trading day is labeled by the level of the VIX close into one of three buckets. The thresholds are deliberately simple and documented, not fitted: the VIX long-run median sits in the high teens, and sustained readings above the high-20s mark genuine stress, so calm/elevated/crisis fall at 17 and 28. It is a market-wide backdrop, not per-asset, computed once and reused across every name.',
    howToRead:
      'Use it to see which backdrop a result came from. A payoff that only showed up in "crisis" days is a very different (and weaker) claim than one that appeared in calm and elevated markets too -- which is exactly what the verdict formalizes.',
    seeAlso: ['verdict', 'vix_stretch'],
  },
  verdict: {
    id: 'verdict',
    term: 'Verdict (confirmed / regime-only / failed)',
    category: 'regime',
    short:
      'Did the strategy pay off across multiple market regimes, in just one, or none -- the guard against a one-regime fluke.',
    formula:
      'paid off in ≥2 regimes → confirmed · exactly 1 → regime_only · 0 → failed · no labeled cycle → untested',
    intuition:
      'A strategy that made money only in crisis days may just have been lucky to span one stormy patch; collapsing that into "it works" is exactly how a backtest fools itself. So each roll is attributed to the regime it was ENTERED in (the information available when the position opened), the rolls are grouped by regime, and the strategy passes a regime if its return-on-premium there is positive. "confirmed" (paid in two or more distinct regimes) is deliberately kept separate from "regime_only" (paid in exactly one). "untested" means no roll could even be labeled.',
    howToRead:
      'Trust "confirmed" far more than "regime_only" -- the latter is a warning that the result may be an artifact of a single market episode. "failed" means it did not pay in any regime over the window.',
    seeAlso: ['regime', 'metric_bakeoff', 'diversification'],
  },

  // ---------------------------------------------------------------- bakeoff
  metric_bakeoff: {
    id: 'metric_bakeoff',
    term: 'The metric bake-off',
    category: 'bakeoff',
    short:
      'Six screens compete: hold an equal-weight put basket on each metric top-k most fragile names and see whose basket earned the best return.',
    intuition:
      'The fragility screen ASSUMES the metrics are worth ranking on; the bake-off tests that prior question -- which metric actually sorted realized OOM-put payoffs best over the lookback. For each of six candidate screens (the five raw metrics plus the composite) it holds an equal-weight put basket on that screen top_k most fragile names (default 5) and reports the basket blended ROI, hit rate, bleed, per-regime verdict, Spearman, and lift. Every name is backtested once and the six screens differ only in how they rank and select from that shared pass, so it is cheap. No commission is charged and rolls priced at the premium floor are left out: at a fixed distance below spot those two cost rules, not fragility, dominated the ranking on quiet names. Leaving floored rolls out is a selection of its own: a quiet name is judged only on the rolls it entered while lively, or dropped when it has none. A delta rule mostly avoids both, because its strike moves with the name’s vol, but at small deltas on short rolls the floor can still bind; the page says when it did. Crucially this is an in-sample, cross-sectional association -- a chooser for which screen to rank on, never a forward predictive backtest.',
    howToRead:
      'The screen with the best return-on-premium wins the bake-off; read it as "which fragility lens best sorted past payoffs," then apply the in-sample caveat before trusting it forward.',
    seeAlso: ['spearman', 'lift', 'in_sample', 'fragility_score'],
  },
  spearman: {
    id: 'spearman',
    term: 'Spearman (rank correlation)',
    category: 'bakeoff',
    short:
      'How well a metric fragility ordering lines up with each name realized put ROI -- a rank correlation, sign-invariant to the metric direction.',
    formula:
      'Spearman rank corr between (metric turned fragile-increasing) and realized put ROI, across all scored names; None if < 3 usable pairs',
    intuition:
      'Beyond the top-k basket, Spearman asks whether the metric ordered the WHOLE universe sensibly: do the names it calls more fragile actually have higher realized put ROI? Each metric is first turned fragile-increasing (co-skewness sign is flipped, since more negative = more fragile), then a Spearman rank correlation is taken against realized ROI -- so it measures agreement of ORDERINGS, robust to outliers and to any monotone rescaling of the metric. It needs at least three usable name-pairs, else it is blank.',
    howToRead:
      'A positive Spearman means more-fragile-by-this-metric names really did earn higher put ROI over the window (+1 = perfect ordering, 0 = none, negative = the metric sorted the wrong way). Like everything in the bake-off it is in-sample.',
    seeAlso: ['metric_bakeoff', 'lift', 'in_sample'],
  },
  lift: {
    id: 'lift',
    term: 'Lift vs baseline',
    category: 'bakeoff',
    short:
      'A screen basket ROI minus the buy-puts-on-everyone baseline -- the value added by selecting fragile names over holding them all.',
    formula:
      'lift_vs_baseline = basket_roi − baseline_roi;   baseline_roi = mean over all scored names of their per-name put ROI',
    intuition:
      'A screen top-k basket might look good simply because puts on everything did well that window, so lift nets that out. The baseline is the mean per-name ROI across every scored name -- equivalent to buying puts on the entire universe equal-weight -- and lift is the screen basket ROI above it. It isolates the selection skill: does ranking by this metric and holding only the most fragile beat indiscriminately buying puts on all of them?',
    howToRead:
      'Positive lift means the metric selection beat holding everyone; zero or negative means the screen added nothing over the buy-everything baseline. It is the cleanest single read of whether a fragility screen earns its keep -- in-sample.',
    seeAlso: ['metric_bakeoff', 'spearman', 'in_sample'],
  },
  multiple_testing: {
    id: 'multiple_testing',
    term: 'FDR correction (Sig. column)',
    category: 'bakeoff',
    short:
      'Testing seven screens against one window inflates how often a per-test hurdle names a winner by chance; the Sig. column shows which screens survive a correction for that.',
    formula:
      'Benjamini-Hochberg step-up procedure across every screen with a defined Spearman p-value in the comparison, at fdr_alpha = 0.05 (research/backtest/multiple_testing.py)',
    intuition:
      'Harvey, Liu & Zhu (2016) showed that once a literature has tried hundreds of factors, the conventional p < 0.05 (or t > 2.0) hurdle names far more "winners" than are real -- exactly this bake-off situation, seven screens tested against the same window. significant_raw is the uncorrected per-test reading a single test in isolation would use; significant_corrected is the same reading after Benjamini-Hochberg FDR correction across all of them.',
    howToRead:
      '"FDR-sig." survives the correction; "raw only" clears the uncorrected hurdle alone and should not be called a real winner; "n.s." clears neither. Read the corrected tag, not the raw one, before trusting a bake-off result.',
    seeAlso: ['metric_bakeoff', 'spearman', 'in_sample'],
  },

  // -------------------------------------------------------------- portfolio
  diversification: {
    id: 'diversification',
    term: 'Diversification (combined vs summed bleed)',
    category: 'portfolio',
    short:
      'A basket combined drawdown is shallower than the sum of its legs drawdowns because the legs bleed and pay at different times.',
    formula:
      'diversification benefit ⇔ combined_max_drawdown > Σ leg_max_drawdown   (both ≤ 0; combined less negative)',
    intuition:
      'Each put leg has its own worst peak-to-trough bleed, but the legs do not all bottom on the same day -- one leg payoff can offset another leg drawdown. The portfolio view sums the legs realized cumulative P&L on the union of their expiry dates and takes the max drawdown of that COMBINED curve, then compares it to the plain sum of the individual leg drawdowns. When the combined drawdown is shallower (less negative) than the summed one, that gap is the diversification benefit -- the legs staggered timing smooths the bleed. Legs are weighted by share of total capital over the window, not per-roll budget, so a fast-rolling short-tenor leg does not silently dominate.',
    howToRead:
      'A combined drawdown clearly less deep than the sum of the legs means the mix genuinely diversifies the carry cost; if they are nearly equal the legs are bleeding in lockstep and you gained little by combining them.',
    seeAlso: ['bleed', 'verdict', 'rolling'],
  },
  time_average_growth: {
    id: 'time_average_growth',
    term: 'Time-average growth',
    category: 'portfolio',
    short:
      'How fast one book actually compounds over time -- the yardstick a hedge is sized on, because a hedge that loses money most years can still make the whole book grow faster.',
    formula:
      'g(w) = (1/T) · Σₘ ln(w · pₘ + (1 − w) · eₘ);   pₘ, eₘ = month m gross returns of the program and the index;   T = the window in years;   CAGR = e^g − 1',
    intuition:
      'An average return across many investors is not what one investor gets: one investor lives through one sequence, and losses compound. Time-average growth is the rate one book compounds at along its own path. That is where a hedge can earn its keep: it costs a little every month, but by cutting the book\'s worst months it can raise the growth of the WHOLE book even while losing money on its own. The Book holds w in a Cboe hedge program and the rest in the S&P 500, rebalanced monthly, with the premium paid from the book; because g is a sum of logs of lines in w it is concave, so it has exactly one best hedge ratio, w*.',
    howToRead:
      'Read the curve, not a single return: if growth falls from the first step of the hedge ratio, the honest size is none. A peak inside 0-100% is the growth-optimal ratio, full Kelly; the Book holds half of it only when the gain over no hedge at that peak clears 1bp a year on both index legs -- a peak too shallow to clear it is still held at none.',
    seeAlso: ['fractional_kelly', 'diversification', 'strike_rule'],
  },
  fractional_kelly: {
    id: 'fractional_kelly',
    term: 'Fractional (half) Kelly',
    category: 'portfolio',
    short:
      'Holding half of the growth-optimal size: one history estimates that optimum, and full Kelly is notoriously sensitive to the error.',
    formula: 'hold = w*/2  when g(w*) − g(0) > 1bp/yr on both index legs;  0 otherwise;  0.5 when w* = 1',
    intuition:
      'Full Kelly, w*, is the ratio that maximises growth on the history it was measured on. Measured on one history, it is an estimate. Near the optimum the growth curve is close to symmetric, so half of w* gives up only about a quarter of the gain over no hedge (exactly a quarter when the curve is a parabola; the page shows the measured share) while halving the exposure to estimation error -- and an overestimated w* is the dangerous one: past the break-even, about 2w* on a near-parabolic curve, holding more of the hedge grows the book slower than holding none. The Book only sizes where w* beats NO hedge by more than 1bp a year on both its base and its conservative S&P 500 leg -- otherwise the answer is none, with the margin named.',
    howToRead:
      'The table shows w* beside the size held and the reason for the haircut. "None (0%)" is an answer, not a gap; "withheld" means an input (measured dividends, T-bills) is missing, not that the hedge failed.',
    seeAlso: ['time_average_growth', 'in_sample'],
  },

  // ------------------------------------------- out-of-sample signal test
  walk_forward: {
    id: 'walk_forward',
    term: 'Walk-forward (out-of-sample) test',
    category: 'bakeoff',
    short:
      'On each rebalance date, rank the names using only data known that day, buy one roll of puts on the top few, settle it, and repeat -- the test of whether the signal predicts rather than describes.',
    intuition:
      'The in-sample bake-off scores a metric and the payoff over the same window, so it can only describe. The walk-forward fixes the order of events: at each date t the six metrics, the composite and the volatility baselines are computed from a trailing window ending at t, the top few names get one model-priced put each, and the roll is settled at its expiry. Nothing after t can reach the pick at t. The rolls are one tenor apart, so a 4-week test rebalances every four weeks. Each screen is then judged on the same periods and the same eligible names.',
    howToRead:
      'Read it as "if I had ranked this way on every one of these dates, would the next roll have paid more than the alternatives?" A pass says the picks beat trailing-volatility pricing on the next roll. It does not say real puts would have paid: the premium is a model price and the universe is today\'s names.',
    seeAlso: ['in_sample', 'test_window', 'random_baseline', 'vol_baseline', 'survivorship', 'model_priced_signal'],
  },
  rank_ic: {
    id: 'rank_ic',
    term: 'Rank IC (information coefficient)',
    category: 'bakeoff',
    short:
      'Spearman rank correlation between a screen\'s signal and each name\'s next-roll ROI, averaged over the periods where it is defined.',
    formula: 'IC_t = Spearman(signal_it, ROI_it) across names;   mean IC = average over periods with a defined IC',
    intuition:
      'The basket compares only the top few names; the IC asks whether the screen ordered the whole eligible universe sensibly in that period. +1 is a perfect ordering, 0 none, negative the wrong way round. A period has no IC when the signal or the ROI is constant across names (for example, when every put expires worthless), so the number of periods behind the average is printed beside it.',
    howToRead:
      'The mean IC and the share of periods with a positive IC are descriptive: no p-value is attached to them. n_ic_periods is how many periods the average rests on. Most 0.10Δ puts return about -100%, so the ROIs tie often and the IC is noisy.',
    seeAlso: ['spearman', 'walk_forward', 'excess_return'],
  },
  excess_return: {
    id: 'excess_return',
    term: 'Excess return (basket vs all names)',
    category: 'bakeoff',
    short:
      'The screen\'s top-K basket total return minus the total return of buying the same puts on every eligible name, over the same periods.',
    formula: 'total = Σ payoff / Σ cost − 1,   cost = notional + model half-spread;   excess = total(basket) − total(all eligible names)',
    intuition:
      'Return is measured on the premium budget plus the half-spread, summed over all periods before dividing, so one cheap roll cannot dominate. Subtracting the all-names return isolates selection from whether puts happened to pay at all. The same totals give the excess against an equal-premium SPY put. Commission is excluded on purpose, because it depends on split-adjusted contract counts, which would leak future information.',
    howToRead:
      'Positive means the picks paid more per dollar of premium than the average name. Two baskets that both lost money can still differ by a large excess, so look at the levels too. Excess is the difference of two returns, each a ratio of totals (payoff over cost), so +100% means the basket returned 100 percentage points more than all names.',
    seeAlso: ['lift', 'random_baseline', 'ex_best'],
  },
  random_baseline: {
    id: 'random_baseline',
    term: 'Random baskets (p_random)',
    category: 'bakeoff',
    short:
      'The share of random top-K baskets, drawn each period from the same eligible names, that did at least as well as the screen\'s basket.',
    formula: 'p_random = (1 + #{random basket total ≥ screen basket total}) / (1 + N),   N = 2,000 random draws',
    intuition:
      'Puts on a few names pay rarely and hugely, so a good-looking basket can be luck. The test redraws the basket at random each period (a within-period permutation, with the same eligible set and the same costs) and asks how often luck alone matches the screen. The +1 smoothing keeps p inside (0, 1]. The grey band on the chart is the 5th to 95th percentile of random basket minus all names, period by period.',
    howToRead:
      'The composite is the pre-registered hypothesis and is judged on its raw p against 0.05. The other 12 screens are exploratory, so their p-values are Benjamini-Hochberg corrected together. A small p says the picks were not luck among these names; it does not say fragility rather than volatility is the reason.',
    seeAlso: ['multiple_testing', 'excess_return', 'vol_baseline'],
  },
  vol_baseline: {
    id: 'vol_baseline',
    term: 'Plain-volatility baselines',
    category: 'bakeoff',
    short:
      'Six screens built from volatility alone (20-day, signal-window and ratio vol, each ranked high and low), run exactly like the fragility screens.',
    intuition:
      'Fragility metrics correlate with volatility, and a model-priced put pays more on volatile names whatever the signal knows. So the test also runs six plain-volatility screens the same way and shows the best next to the composite. That best one is chosen after the fact from six, so it is always labelled "best of 6, picked after the fact" and its p-value is Benjamini-Hochberg corrected across the 12 exploratory screens.',
    howToRead:
      'If a volatility screen does as well as the composite, the composite has not shown anything beyond volatility. The test does not statistically separate fragility from volatility; it only shows the comparison.',
    seeAlso: ['rv20', 'rv_ratio', 'random_baseline', 'model_priced_signal'],
  },
  fama_macbeth: {
    id: 'fama_macbeth',
    term: 'Fragility beyond trailing vol (Fama–MacBeth)',
    category: 'bakeoff',
    short:
      'Each period, regress how each put turned out on the composite\'s rank while holding the name\'s trailing volatility fixed; the average slope γ̄ says whether fragility adds anything vol does not already say.',
    formula:
      'each period t:  rank(ROI_it) = a_t + γ_t · rank(composite_it) + controls(trailing vol, asset class) + e_it;   γ̄ = mean of γ_t over the T periods;   90% CI = γ̄ ± c · √V',
    intuition:
      'The vol baselines only compare one screen with another. This test asks the sharper question: among names with the same trailing vol (20-day, signal-window and their ratio, entered flexibly, plus a non-equity dummy), did the more fragile ones\' puts finish better? The outcome is the rank of each put\'s ROI within its period, so a few huge payoffs cannot dominate. γ̄ is the average of the per-period slopes. Its uncertainty allows for names that stay the same from period to period and for slopes that drift together over time (Newey–West at a fixed 16 lags, with a simulated fixed-b null rather than a normal table). The rule was frozen before the real outcomes were opened (ADR 0035) and has no tuning knobs. Only the pre-registered test (0.10Δ, 4 weeks, 4-year window, top 5, on data to 2026-10-31) gets a p-value and a verdict. The same setting on later data is a monitoring re-run, and any other setting is exploratory: both show γ̄ and its 90% CI only.',
    howToRead:
      'γ̄ is the average slope of the outcome rank on the composite rank, so +0.05 means a name 20 percentile points higher on fragility ends about 1 percentile point higher on ROI, at equal trailing vol. "No evidence" is not "no effect": at this sample the test would catch a pay-rate gap of about 9.6 points (top against bottom fifth of the composite) 80% of the time, 71% at 8.6 and 98% at 12.8, so anything smaller can hide inside the CI. A pass is demoted to "not robust" if it is no longer significant once the market move is netted out (the intercept test), without the single best period or best stress episode, or among equity names alone. It controls for trailing vol only: a vol effect shaped like fragility cannot be told apart from it, so read it as beyond this vol surface only. Disclosed limits, measured by simulation (nominal size 5%): persistent factors on residual-fragility names, about 7.4%; in a simulated negatively skewed index, about 6.1% at zero drift and 2.8% at +10% a year; separately, in a simulated Gaussian market drifting −15% a year, about 6.0% (a bear sample can over-pass); all other worlds tried, 4.7% or less. Blinding was not perfect (20-day vol at the next entry is the realised vol of each holding period). The rank outcome sees how often a put finishes in the money more than how much it pays; the log(1 + payoff/cost) companion is shown in the data but not tested. The premium is a model price, so a pass means fragile names moved more than their trailing vol implied; real option prices may already reflect that, and part of it may be exposure to market crashes, which counts as fragility here.',
    seeAlso: ['vol_baseline', 'walk_forward', 'random_baseline', 'model_priced_signal', 'ex_best'],
  },
  ex_best: {
    id: 'ex_best',
    term: 'Excess without the best period',
    category: 'bakeoff',
    short:
      'The screen\'s excess return recomputed after dropping its single best period, and after dropping its single best name-period.',
    intuition:
      'Put payoffs are heavy-tailed: one crash roll can carry a whole backtest. Dropping the best period, and separately the best single name-period, shows how much of the edge was one event. If either recomputed excess is zero or negative while the composite otherwise beat random baskets, the verdict is "crash carried".',
    howToRead:
      'Compare it with the headline excess. A large drop means the result rests on one episode and should not be read as a steady edge.',
    seeAlso: ['excess_return', 'stress_episode', 'walk_forward'],
  },
  stress_episode: {
    id: 'stress_episode',
    term: 'Stress episode',
    category: 'bakeoff',
    short:
      'A run of consecutive periods in which the equal-premium SPY put paid off (its payoff was above zero), shaded on the chart.',
    intuition:
      'The test needs sell-offs to say anything about a tail hedge. Episodes are read from SPY\'s same-rule put payoffs, not from VIX regimes, so they mark when the market actually fell through the strike. A period counts as stress when SPY\'s payoff is above zero, a lower bar than a name-period "paid" (payoff above its cost). Fewer than 3 episodes, or fewer than 24 periods, and the verdict is "insufficient data".',
    howToRead:
      'Shaded bars are the periods that matter most for a put strategy. Check that the excess bars there are positive; a flat result outside them is expected, since the puts mostly expire worthless.',
    seeAlso: ['walk_forward', 'ex_best', 'regime_at_entry'],
  },
  test_window: {
    id: 'test_window',
    term: 'Test window',
    category: 'bakeoff',
    short:
      'The dates the walk-forward actually covers: the signal window needs history first, so the test starts years after the data does.',
    intuition:
      'Each signal reads a trailing window of the rail\'s years (4 by default) and a name needs at least 90% of those sessions present. With about ten years of bronze prices, a 4-year signal window pushes the first test date to about April 2020, so the February–March 2020 Covid crash is outside the test. More years on the rail lengthens the in-sample window but shortens this one.',
    howToRead:
      'Read the stated first and last dates before the verdict. A short window with few stress episodes is a weak test whichever way it comes out.',
    seeAlso: ['walk_forward', 'stress_episode', 'in_sample'],
  },
  survivorship: {
    id: 'survivorship',
    term: 'Survivorship caveat',
    category: 'bakeoff',
    short:
      'The universe is today\'s list of names, chosen with hindsight; names that failed or were delisted are not in it.',
    intuition:
      'A point-in-time universe would hold the names as they stood on each test date. This one holds the names that exist now, so the test sees only survivors. Names that collapsed and left the list are missing, and those are the ones a put pays on, so the test can misstate the picks\' results in either direction. A pass here is not evidence that the same ranking would have worked on the universe as it was.',
    howToRead:
      'Treat every result as conditional on today\'s universe. The fix, a point-in-time universe, is logged but not built.',
    seeAlso: ['walk_forward', 'model_priced_signal'],
  },
  model_priced_signal: {
    id: 'model_priced_signal',
    term: 'Model-priced caveat',
    category: 'bakeoff',
    short:
      'Premiums are model prices at trailing realised volatility, not quotes, so a pass means the next move beat the trailing vol, not that real puts paid.',
    intuition:
      'No historical option chain exists for most names, so each put is priced by the model at the name\'s trailing 20-day realised volatility, with the strike picked by the run\'s strike rule (a fixed distance below spot, or a fixed delta) at that same vol. A name whose next-roll move beats its trailing vol looks like a winner, and volatility screens benefit most because the pricing vol is what they rank on. The model half-spread is charged; commission is not.',
    howToRead:
      'Read a pass as "these picks moved more than their trailing vol implied", the model-world version of cheap convexity. Whether a market quote would have been that cheap is a separate question this test cannot answer.',
    seeAlso: ['model_priced', 'vol_baseline', 'rv20', 'survivorship'],
  },
  rv20: {
    id: 'rv20',
    term: '20-day realised volatility',
    category: 'metric',
    short:
      'The annualised standard deviation of a name\'s last 20 daily log returns -- the volatility the backtest prices each put at.',
    intuition:
      'It is the only volatility every past day has, so the model prices each roll at it (clamped to 6–200% in a single-name backtest; in the signal test a name outside that range is left out that period instead). A screen on high 20-day vol picks the names the model already prices as expensive, and a screen on low 20-day vol picks the cheap ones.',
    howToRead:
      'High means the put is priced for a bigger move. It is the benchmark any fragility metric has to beat to claim it knows something volatility does not.',
    seeAlso: ['rv_ratio', 'vol_baseline', 'model_priced_signal'],
  },
  rv_ratio: {
    id: 'rv_ratio',
    term: 'Vol ratio (long / 20-day)',
    category: 'metric',
    short:
      'Realised volatility over the signal window divided by the 20-day volatility -- above 1 means the name is calmer now than usual.',
    formula: 'ratio = σ_signal-window / σ_20-day',
    intuition:
      'It compares a name\'s usual volatility with the volatility the model prices the put at. A high ratio picks names priced off an unusually quiet month, which are cheap relative to their history. Because the put is priced at the 20-day vol that sits in the denominator, this baseline benefits from the pricing model itself.',
    howToRead:
      'When this baseline wins, suspect the model-pricing artifact before a real signal. The screen is run in both directions (ratio high and ratio low).',
    seeAlso: ['rv20', 'vol_baseline', 'model_priced_signal'],
  },
  regime_at_entry: {
    id: 'regime_at_entry',
    term: 'Regime at entry',
    category: 'regime',
    short:
      'The VIX regime (calm, elevated or crisis) on the date a roll was opened, not while it ran.',
    intuition:
      'Grouping rolls by the regime they started in keeps the label knowable when the decision was made. Grouping by the regime at expiry would be look-ahead, because a roll that paid tends to end in a crisis.',
    howToRead:
      'Per-regime figures rest on few periods, especially crisis. Compare the counts before the percentages.',
    seeAlso: ['regime', 'stress_episode'],
  },
  data_coverage: {
    id: 'data_coverage',
    term: 'Data coverage',
    category: 'context',
    short:
      'Sessions present per calendar year for a dataset, against the SPY sessions expected in that year.',
    intuition:
      'A gap in a name\'s prices or dividends changes what the backtest can see, and the signal test drops a name from a period when under 90% of its signal window is present. Expected sessions are counted from SPY\'s calendar.',
    howToRead:
      'A full year reads like 252 of 252. A short year at the start is the data beginning; a short year in the middle is a gap.',
    seeAlso: ['walk_forward', 'test_window'],
  },
  name_signal_record: {
    id: 'name_signal_record',
    term: 'How a signal treated a name',
    category: 'bakeoff',
    short:
      '"k of n paid, largest +X×": in how many of its rolls the name\'s put made money, and the biggest payoff as a multiple of cost.',
    intuition:
      'It is a count, not an average: with 0.10Δ puts most name-periods return about -100%, so means and medians would hide the few rolls that pay. A roll paid when its payoff exceeded its cost. The record is shown separately for periods the screen put the name in its top K and for the rest, and a group with fewer than 5 periods is marked too few to judge.',
    howToRead:
      'It is descriptive and carries no p-value. Contribution is the sum over picked periods of (name ROI minus the universe ROI) divided by K, and can be negative.',
    seeAlso: ['hit_rate', 'biggest_payoff', 'regime_at_entry', 'walk_forward'],
  },
  usable_quote: {
    id: 'usable_quote',
    term: 'Usable quote',
    category: 'context',
    short:
      'A put quote with a positive bid and a spread within the marking bar: one a position could actually be marked on.',
    intuition:
      'A collected quote is not a tradable price. One with no bid, or a spread wider than the marking bar, says nothing about what the put was worth, so the Marks view counts a session only when some strike in the depth bucket had a usable quote.',
    howToRead:
      'Sessions are shown as years (252 sessions a year). A held cell says a strike there could be priced, not that every strike could.',
    seeAlso: ['traded_quote', 'oom_put', 'data_coverage'],
  },
  traded_quote: {
    id: 'traded_quote',
    term: 'Traded strike',
    category: 'context',
    short: 'A session in which some strike in the depth bucket printed volume.',
    intuition:
      'A stricter test of the market than a usable quote: someone actually dealt at that depth. Neither count contains the other in general, because a strike can trade with a spread wider than the marking bar.',
    howToRead:
      'Compare it with the usable count at the same depth. Many traded sessions and few usable ones means the market was there but too wide to mark against.',
    seeAlso: ['usable_quote', 'oom_put'],
  },
  // Further labels the explained-headers rule needs.
  net_pnl: {
    id: 'net_pnl',
    term: 'Net P&L',
    category: 'result',
    short:
      'Total payoffs collected minus the premium paid and the brokerage fees, over every roll in the window.',
    formula: 'net = Σ payoff − Σ premium − Σ brokerage',
    intuition:
      'It is the dollar result of buying a put every roll with a fixed premium budget. Unlike return on premium, it includes the fees, which can be a large share of a cheap put.',
    howToRead:
      'Negative is the normal case for a hedge that mostly expires worthless. Read it beside the biggest payoff and the hit rate to see whether the few payoffs were large enough.',
    seeAlso: ['roi_on_premium', 'bleed', 'biggest_payoff'],
  },
  spot: {
    id: 'spot',
    term: 'Spot price',
    category: 'option',
    short: 'The underlying\'s latest close, from which strikes are measured.',
    intuition:
      'Strike distance and the put\'s value are both stated against the spot at entry. The ranking prints the latest close so a strike percentage can be turned into a price.',
    howToRead: 'It is a price, not a signal; nothing is ranked on it.',
    seeAlso: ['oom_put', 'strike_rule'],
  },
  best_cell: {
    id: 'best_cell',
    term: 'Best cell',
    category: 'result',
    short:
      'The strike-by-tenor combination with the highest annualised return over a name\'s own sweep grid.',
    intuition:
      'The ranking scores every name over a grid of distances below spot and tenors and keeps each name\'s best. Picking the maximum of a grid after seeing the results is in-sample, so it flatters the name.',
    howToRead:
      'Use it as the place to start looking at a name, not as a forecast. The "At" column says which strike and tenor it was.',
    seeAlso: ['annualized_return', 'in_sample'],
  },

  // ---------------------------------------------------------------- context
  vix_stretch: {
    id: 'vix_stretch',
    term: 'VIX stretch (z-score)',
    category: 'context',
    short:
      'How many standard deviations today VIX close sits above or below its own trailing 20-day average -- how extended fear is right now.',
    formula:
      'z = (VIX_close − mean₂₀(VIX_close)) / std₂₀(VIX_close);   trailing 20 trading days, sample std (ddof=1)',
    intuition:
      'The absolute VIX level tells you the regime; the stretch z-score tells you how UNUSUAL today reading is versus its own recent history. It standardizes the latest close against the mean and sample standard deviation of the trailing 20 trading days, so a large positive z means fear has spiked well above its recent norm and a negative z means the market is unusually calm relative to itself. It is the one live computed context metric on this site, point-in-time correct (only past closes enter the window).',
    howToRead:
      'Roughly, z above +2 flags an acute fear spike relative to the last month, z below 0 an unusually quiet tape. It is context for reading the regime and the screen -- a high stretch says today is far from recent normal -- not itself a trade signal. Undefined when the trailing window was flat (zero variance).',
    seeAlso: ['regime'],
  },

  // ------------------------------------------------ the roll ledger columns
  // Added with the tooltip sweep (docs/adr/0033): every table header and stat
  // label explains itself. Each entry is read off the code that computes the
  // number (research/backtest/put_roll.py unless said otherwise).
  roll_dates: {
    id: 'roll_dates',
    term: 'Entry and expiry dates',
    category: 'result',
    short: 'The session a put was bought and the session it expired; the next one is bought the day this one ends.',
    intuition:
      'Rolls do not overlap. The backtest buys a put on an entry session, holds it for the tenor (weeks x 5 trading days), settles it against the close on the expiry session and buys the next. An entry is skipped while the name has too little history for a 20-day volatility.',
    howToRead:
      'The expiry of one row is the entry of the next, so the ledger reads as one continuous chain. A gap means the window ended or history was missing.',
    seeAlso: ['rolling'],
  },
  roll_spot: {
    id: 'roll_spot',
    term: 'Spot at entry',
    category: 'result',
    short: 'The underlying’s close on the entry session, the price the strike is measured from.',
    intuition:
      'Every roll prices its put off the close of its own entry day. Spot at expiry is not shown: it is only used to settle the payoff.',
    howToRead: 'Compare it with the Strike column: the gap between them is how far out of the money the put was bought.',
    seeAlso: ['strike_price'],
  },
  strike_price: {
    id: 'strike_price',
    term: 'Strike',
    category: 'option',
    short: 'The price below which the put starts to pay: it pays strike minus the price at expiry, if that is positive.',
    formula: 'payoff = max(K − S_expiry, 0) per share',
    intuition:
      'The strike is picked by the strike rule: a fixed percentage below the entry spot, or the price at which the put has a chosen Black-Scholes delta. On the quote-based surface it is a listed strike.',
    howToRead: 'A lower strike (further out of the money) is cheaper and pays only after a bigger fall.',
    seeAlso: ['strike_rule', 'oom_put'],
  },
  roll_sigma: {
    id: 'roll_sigma',
    term: 'Volatility used to price the put (σ)',
    category: 'result',
    short: 'The trailing 20-day realised volatility on the entry day, clamped to 6%-200%, which stands in for implied volatility.',
    intuition:
      'The model path has no historical option quotes, so it prices each put at the name’s own recent realised volatility. The floor and cap stop a very quiet or a very wild window from producing a free or an absurd premium.',
    howToRead: 'A higher σ means a dearer put and fewer shares covered for the same budget.',
    seeAlso: ['rv20', 'model_priced'],
  },
  roll_premium: {
    id: 'roll_premium',
    term: 'Premium (per share)',
    category: 'result',
    short: 'The price of one put at entry: the Black-Scholes model price, floored at 0.01% of spot (the real ask on a quote-based run).',
    intuition:
      'This is the price per share, not the cash spent. The cash spent is the budget; premium only sets how many shares of puts that budget buys.',
    howToRead: 'Divide the budget by this to get the Cts column.',
    seeAlso: ['model_priced', 'roll_budget'],
  },
  roll_contracts: {
    id: 'roll_contracts',
    term: 'Cts (shares covered)',
    category: 'result',
    short: 'How many shares of puts the budget buys: budget ÷ premium. It is not rounded to whole 100-share contracts.',
    formula: 'Cts = budget / premium',
    intuition:
      'The backtest spends the budget exactly. Brokerage converts this share count to real contracts by dividing by 100 when it charges commission.',
    howToRead: 'A dear put covers fewer shares, so the same fall in the stock pays less.',
    seeAlso: ['roll_budget', 'roll_premium'],
  },
  roll_cost: {
    id: 'roll_cost',
    term: 'Fill (entry brokerage)',
    category: 'result',
    short: 'The brokerage paid to buy the put: a per-contract commission plus the bid-ask half-spread on the budget.',
    intuition:
      'This column holds the cost field of the roll: commission per contract times Cts ÷ 100, plus a modelled half-spread as a share of the budget (zero spread on a quote-based run, where the real ask already includes it). It is charged on top of the budget.',
    howToRead: 'Net is the payoff minus the budget minus this.',
    seeAlso: ['roll_net', 'roll_budget'],
  },
  roll_budget: {
    id: 'roll_budget',
    term: 'Budget (premium per roll)',
    category: 'result',
    short: 'The cash set aside to buy puts on each roll: the “Premium per roll” control. Every return divides by it.',
    intuition:
      'Every roll spends the same budget, so total premium is rolls x budget. Returns on premium, hit rates and the Net column are all measured against it.',
    howToRead: 'Changing it scales every dollar figure and leaves every percentage return unchanged.',
    seeAlso: ['roi_on_premium'],
  },
  roll_payoff: {
    id: 'roll_payoff',
    term: 'Payoff',
    category: 'result',
    short: 'What the put paid at expiry: Cts x max(strike − expiry close, 0). It is zero when the stock finished above the strike.',
    formula: 'payoff = Cts x max(K − S_expiry, 0)',
    intuition: 'The put is held to expiry and settled at intrinsic value; there is no early sale on the model path.',
    howToRead: 'A roll counts toward the hit rate when its net is positive: the payoff exceeds the premium budget PLUS the entry brokerage (commission and half-spread).',
    seeAlso: ['hit_rate'],
  },
  roll_net: {
    id: 'roll_net',
    term: 'Net (per roll)',
    category: 'result',
    short: 'The roll’s result after everything: payoff − budget − brokerage. Most rolls are close to minus the budget.',
    formula: 'net = payoff − budget − brokerage',
    intuition: 'Summed over the rolls, this is the Net P&L. A single big payoff has to cover many small losses.',
    howToRead: 'Green rolls paid for themselves; the red ones are the carry cost.',
    seeAlso: ['net_pnl', 'bleed'],
  },
  rolls_count: {
    id: 'rolls_count',
    term: 'Rolls',
    category: 'result',
    short: 'How many puts were bought, held to the end and settled over the window.',
    intuition: 'Each roll is one trial. With few rolls, one lucky or unlucky one dominates every average.',
    howToRead: 'Treat a return built on a handful of rolls as an anecdote.',
    seeAlso: ['rolling'],
  },

  // ------------------------------------------------ published programs, legs
  published_total: {
    id: 'published_total',
    term: 'Published program: total return',
    category: 'context',
    short: 'What an investable put index returned over the same window: last close ÷ first close − 1.',
    intuition:
      'Cboe publishes put-writing and put-buying indices. The same dates as the backtest are read from the stored index closes. A program whose history does not cover the window is left out rather than shown as zero.',
    howToRead: 'It is what you could have bought instead, not a like-for-like of this strategy.',
    seeAlso: ['published_annual'],
  },
  published_annual: {
    id: 'published_annual',
    term: 'Published program: per year',
    category: 'context',
    short: 'The total return compounded down to a yearly rate: (1 + total)^(1/years) − 1.',
    intuition: 'Geometric annualisation over the window’s length in years, so it can sit beside the backtest’s own per-year return.',
    howToRead: 'Compare it with the strategy’s per-year return, remembering that window length is the same.',
    seeAlso: ['annualized_return', 'published_total'],
  },
  leg_weight: {
    id: 'leg_weight',
    term: 'Leg weight',
    category: 'portfolio',
    short: 'The leg’s share of the basket’s capital, normalised so all legs sum to 100%.',
    intuition: 'Each leg’s per-roll budget is notional x share ÷ its number of rolls, so a short-tenor leg does not quietly dominate.',
    howToRead: 'Weights you typed are relative; this column shows what they became.',
    seeAlso: ['diversification'],
  },
  leg_premium: {
    id: 'leg_premium',
    term: 'Leg premium',
    category: 'portfolio',
    short: 'Total premium budget the leg spent over the window: its per-roll budget x its rolls.',
    intuition: 'It is the denominator of the leg’s return on premium.',
    howToRead: 'Legs with similar premium are comparable; a leg with a tiny premium has a noisy return.',
    seeAlso: ['roi_on_premium'],
  },

  // ------------------------------------------------ the price and strike builds
  div_cash: {
    id: 'div_cash',
    term: 'Dividend paid per share',
    category: 'option',
    short: 'The cash dividend per share on its ex-date, as paid.',
    intuition: 'These payments are summed (or scaled to a full year) to get D, which gives the yield q = −ln(1 − D / S).',
    howToRead: 'Compare with the next column: a later split divides the cash paid.',
    seeAlso: ['dividend_yield'],
  },
  div_adjusted: {
    id: 'div_adjusted',
    term: 'Dividend on the current share basis',
    category: 'option',
    short: 'The same dividend restated for later splits, so it can be compared with today’s as-traded close.',
    intuition: 'A split divides the cash paid per share. The yield uses the amount on the share basis of the close it is divided by.',
    howToRead: 'If a name has not split, both columns are equal.',
    seeAlso: ['dividend_yield'],
  },
  strike_vol: {
    id: 'strike_vol',
    term: 'Volatility used for the strike',
    category: 'option',
    short: 'The backtest’s realised volatility, or the market’s implied volatility, that the target delta is solved at.',
    intuition:
      'A delta is only defined given a volatility. The backtest has only realised volatility for past days; a desk’s “10-delta put” uses implied volatility, which sits higher and is skewed.',
    howToRead: 'The higher volatility pushes the same-delta strike further below spot.',
    seeAlso: ['put_delta', 'rv20'],
  },
  strike_from_spot: {
    id: 'strike_from_spot',
    term: 'Strike distance from spot',
    category: 'option',
    short: 'How far below (or, near 0.50 delta, above) today’s spot the strike sits, in percent.',
    formula: 'distance = (1 − K / S) x 100',
    intuition: 'The same delta is a different distance in a calm and a wild market; this makes the distance explicit.',
    howToRead: 'Compare the two rows: the market’s strike for the same delta is further out.',
    seeAlso: ['strike_rule', 'strike_price'],
  },

  // ------------------------------------------------ hold size and the lump book
  hold_window: {
    id: 'hold_window',
    term: 'Window (sizing)',
    category: 'portfolio',
    short: 'The stretch of history the hedge ratio was optimised over: the letter’s own window, or the program’s full history.',
    intuition: 'A size found on one window may not hold on another, so each program is sized on both.',
    howToRead: 'If the two windows disagree, the size is fragile.',
    seeAlso: ['time_average_growth'],
  },
  w_star: {
    id: 'w_star',
    term: 'Full-Kelly hedge ratio (w*)',
    category: 'portfolio',
    short: 'The share of the book in the program that gave the highest time-average growth, found by golden-section search on 0 to 100%.',
    intuition: 'It is the growth-maximising mix on one history, monthly rebalanced. One history estimates it noisily.',
    howToRead: 'Do not hold w* itself: the Hold column shows what is recommended from it.',
    seeAlso: ['fractional_kelly', 'time_average_growth'],
  },
  hold_size: {
    id: 'hold_size',
    term: 'Recommended hold',
    category: 'portfolio',
    short: 'The hedge ratio to hold: w*/2 when w* beats no hedge by more than 1bp a year on both legs, none when it does not, 50% when w* is 100%.',
    intuition: 'Half-Kelly gives up some growth for protection against the estimate being too high. “Withheld” means the data needed to size it is missing.',
    howToRead: 'None (0%) is a real answer: the program did not earn its place.',
    seeAlso: ['fractional_kelly', 'w_star'],
  },
  hold_reason: {
    id: 'hold_reason',
    term: 'Why this size',
    category: 'portfolio',
    short: 'The rule that produced the recommended hold, with the margin it passed or failed by.',
    intuition: 'Every recommendation names its reason so a zero or a cap is not a mystery.',
    howToRead: 'Read it before acting on the number.',
    seeAlso: ['hold_size'],
  },
  hedge_ratio: {
    id: 'hedge_ratio',
    term: 'Hedge ratio',
    category: 'portfolio',
    short: 'The share of the book held in the put program, the rest in the index; rebalanced on the first day of each month.',
    intuition: 'Zero is the index alone and 100% the program alone. The rows between are the mixes tried.',
    howToRead: 'The tagged row is the mix with the best figure in the column being ranked.',
    seeAlso: ['time_average_growth'],
  },
  cagr: {
    id: 'cagr',
    term: 'Growth (CAGR)',
    category: 'portfolio',
    short: 'The geometric growth rate per year of the mix: (end value ÷ start value)^(1/years) − 1.',
    intuition: 'It is the time-average growth of the mix, the rate that actually compounds.',
    howToRead: 'Higher is better; compare mixes on the same window.',
    seeAlso: ['time_average_growth'],
  },
  ann_volatility: {
    id: 'ann_volatility',
    term: 'Volatility (annualised)',
    category: 'portfolio',
    short: 'The standard deviation of daily returns of the mix, times √252.',
    intuition: 'It measures how bumpy the ride was, whichever direction.',
    howToRead: 'A hedge should lower it; if it does not, the hedge is not buying smoothness.',
    seeAlso: ['cagr_per_vol'],
  },
  max_drawdown: {
    id: 'max_drawdown',
    term: 'Worst drawdown',
    category: 'portfolio',
    short: 'The largest peak-to-trough fall in the book’s value per unit invested, as a negative percentage.',
    intuition: 'It is measured on value per unit, so fresh contributions cannot mask a fall. Where books are marked daily it can begin with a hedge giving back its own paper gain.',
    howToRead: 'Closer to zero is better. It is each book’s own peak to its own trough, not the index’s.',
    seeAlso: ['bleed'],
  },
  cagr_per_vol: {
    id: 'cagr_per_vol',
    term: 'CAGR per unit of volatility',
    category: 'portfolio',
    short: 'Growth divided by volatility, with no risk-free rate netted. “n/a” when volatility is zero.',
    formula: 'CAGR / volatility',
    intuition: 'A crude return-for-risk ratio, the yardstick of the letter this comparison reproduces.',
    howToRead: 'Higher is better; it can rank mixes differently from growth alone.',
    seeAlso: ['cagr', 'ann_volatility'],
  },
  plan_paid_in: {
    id: 'plan_paid_in',
    term: 'Paid in',
    category: 'portfolio',
    short: 'The total of the monthly contributions the plan put in over the window.',
    intuition: 'Both arms of a plan are paid the same contributions, so the difference is what the money did.',
    howToRead: 'Ends-with below this means the plan lost money.',
    seeAlso: ['plan_ends_with'],
  },
  plan_ends_with: {
    id: 'plan_ends_with',
    term: 'Ends with',
    category: 'portfolio',
    short: 'The arm’s terminal wealth on the last day of the window.',
    intuition: 'Contributions are invested as they arrive, so this is the value of all of them at the end.',
    howToRead: 'Compare with Paid in and with the other arm.',
    seeAlso: ['plan_paid_in', 'plan_irr'],
  },
  plan_irr: {
    id: 'plan_irr',
    term: 'IRR (money-weighted return)',
    category: 'portfolio',
    short: 'The yearly rate that makes the arm’s dated contributions grow to its terminal wealth.',
    intuition: 'Because money arrives monthly, a plain growth rate would overweight the early dollars; IRR weights each by how long it was invested.',
    howToRead: 'The gap between the two arms is the plan’s verdict. A gap within 1bp a year counts as inconclusive.',
    seeAlso: ['plan_ends_with'],
  },

  // ------------------------------------------------ regimes and the surface
  regime_stretch: {
    id: 'regime_stretch',
    term: 'Regime stretch (from, to)',
    category: 'regime',
    short: 'The first and last session of an unbroken run in one VIX regime.',
    intuition: 'Regimes are labelled daily from the VIX with a one-point hysteresis band, so a stretch is a run of identical labels.',
    howToRead: 'Long calm stretches are where a put program bleeds.',
    seeAlso: ['regime'],
  },
  regime_sessions: {
    id: 'regime_sessions',
    term: 'Sessions in the stretch',
    category: 'regime',
    short: 'The number of trading sessions the regime lasted without a change of label.',
    intuition: 'Sessions are trading days with a VIX close, not calendar days.',
    howToRead: 'Longest stretches only are listed.',
    seeAlso: ['regime'],
  },
  bid_ask: {
    id: 'bid_ask',
    term: 'Bid and ask',
    category: 'option',
    short: 'The best price a buyer offers and a seller asks for the put at that strike on the quote date.',
    intuition: 'A buyer pays the ask and a seller receives the bid. The market price used on the ladder is their midpoint.',
    howToRead: 'A wide gap relative to the price means a costly, thin strike.',
    seeAlso: ['usable_quote'],
  },
  paretan_price: {
    id: 'paretan_price',
    term: 'Paretan price',
    category: 'option',
    short: 'The put price implied at this strike by extrapolating a Pareto tail from the anchor put at the fitted tail index α.',
    intuition: 'Given one anchor put and a tail exponent, the ratio of put prices at two strikes is fixed. The ladder prices every deeper strike from the anchor this way (docs/adr/0026).',
    howToRead: 'Paretan above the market mid suggests the market prices that tail cheaply, under this fit.',
    seeAlso: ['iv_ratio'],
  },
  bs_flat_price: {
    id: 'bs_flat_price',
    term: 'Black-Scholes price (flat vol)',
    category: 'option',
    short: 'The Black-Scholes price of the put at this strike using the anchor put’s implied volatility for every strike.',
    intuition: 'A flat-volatility baseline: it ignores that deeper puts trade at higher implied volatility.',
    howToRead: 'Market above this line is the skew; the Paretan line says how much of it a power-law tail explains.',
    seeAlso: ['paretan_price'],
  },
  iv_ratio: {
    id: 'iv_ratio',
    term: 'IV ratio',
    category: 'option',
    short: 'The implied volatility of the Paretan price divided by the market’s implied volatility at the same strike.',
    formula: 'Paretan IV / market IV',
    intuition: 'Prices are compared as volatilities so strikes of different depth are on one scale. It is empty when either price cannot be inverted (below intrinsic value).',
    howToRead: 'Above 1 the Paretan tail is dearer than the market; below 1, cheaper.',
    seeAlso: ['paretan_price'],
  },
  lookback_window: {
    id: 'lookback_window',
    term: 'Rolled over (look-back)',
    category: 'option',
    short: 'How many years of history the strategy is rolled across, ending on the as-of date.',
    intuition: 'Longer windows include more regimes and more rolls, but start earlier, where the data may be thinner.',
    howToRead: 'A result that changes sign with the window is not robust.',
    seeAlso: ['rolling', 'regime'],
  },

  // ------------------------------------------------ the Pitch tables
  pitch_schedule: {
    id: 'pitch_schedule',
    term: 'The schedule table',
    category: 'context',
    short: 'One row per job that runs on a timer: when it runs, what it writes, and whether anything it writes is a decision.',
    intuition: 'The Pitch asks whether Putlab produces a daily signal. The answer is read off what runs, not off intent.',
    howToRead: '“Data only” means the job collects or computes and decides nothing.',
  },
  pitch_datasets: {
    id: 'pitch_datasets',
    term: 'The dataset table',
    category: 'context',
    short: 'The stored quote snapshots the study uses: the dates they cover, what is in them and what is known to be missing.',
    intuition: 'Gaps are measured against the exchange’s session calendar, so a missing day is a day Cboe printed a close for.',
    howToRead: 'The licence allows only aggregates to leave the lake.',
  },
  pooled_regime_return: {
    id: 'pooled_regime_return',
    term: 'Pooled regime return',
    category: 'regime',
    short: 'A regime’s total net P&L over its total premium, for the rolls entered in that regime, net of modelled brokerage.',
    intuition: 'It pools dollars rather than averaging per-roll returns (regime_verdict.py). Net of brokerage, it can fall below −1.00.',
    howToRead: 'The verdict turns on the sign of this figure in each regime.',
    seeAlso: ['verdict', 'regime_at_entry'],
  },
  crisis_rolls: {
    id: 'crisis_rolls',
    term: 'Crisis rolls',
    category: 'regime',
    short: 'How many rolls were entered while the VIX regime was crisis.',
    intuition: 'The crisis slice is tiny, often one or two rolls, so it is a coin flip rather than a sample.',
    howToRead: 'Do not read the crisis return when this is single digits.',
    seeAlso: ['regime_at_entry'],
  },
  roll_window_span: {
    id: 'roll_window_span',
    term: 'First entry to last expiry',
    category: 'regime',
    short: 'The span of dates the verdict’s rolls actually cover: the first put bought to the last put expired.',
    intuition: 'The lookback window starts at an as-of-relative date, which moves the whole monthly roll calendar.',
    howToRead: 'If this ends before a drop, that drop is in none of the rolls.',
  },
  mean_roll_return: {
    id: 'mean_roll_return',
    term: 'Mean return per roll',
    category: 'result',
    short: 'What each roll paid back ÷ what it cost − 1, averaged over the rolls, after commission and at the real ask.',
    formula: 'mean over rolls of (paid back ÷ premium − 1)',
    intuition: '−1.00 means the premium was lost; +2.00 means three times the premium came back.',
    howToRead: 'Negative means the puts lost money on average; read it with the interval beside it.',
    seeAlso: ['ci95_block', 'roi_on_premium'],
  },
  ci95_block: {
    id: 'ci95_block',
    term: '95% interval (block bootstrap)',
    category: 'result',
    short: 'The middle 95% of the mean return when the rolls are resampled in blocks of consecutive rolls.',
    intuition: 'Blocks keep neighbouring rolls together so a crash that spans two rolls is not counted as two independent draws.',
    howToRead: 'An interval wholly below zero means the loss is not luck of the draw.',
    seeAlso: ['mean_roll_return'],
  },
  winners: {
    id: 'winners',
    term: 'Winners',
    category: 'result',
    short: 'Rolls whose return on premium, after commission, is above zero.',
    intuition: 'The word means only that here. A combination chosen by a search is called the pick.',
    howToRead: 'A few winners among many rolls is the normal shape of a put program.',
    seeAlso: ['hit_rate'],
  },
  best_roll: {
    id: 'best_roll',
    term: 'Best roll',
    category: 'result',
    short: 'The single best roll’s return on premium and the day it was bought; “—” when every roll lost its whole premium.',
    intuition: '+2.0 is three times the premium back. One roll often carries the whole result.',
    howToRead: 'Compare it with the mean: a best far above the mean is a result carried by one event.',
    seeAlso: ['biggest_payoff'],
  },
  median_ask: {
    id: 'median_ask',
    term: 'Median ask, % of spot',
    category: 'result',
    short: 'The typical price paid for the put, as a percentage of the underlying’s spot.',
    intuition: 'It is the median of the real ask over the rolls, so a deeper strike is cheaper.',
    howToRead: 'It is what each roll costs as a share of the index.',
  },
  refused_fills: {
    id: 'refused_fills',
    term: 'Refused',
    category: 'result',
    short: 'Sessions where no listed put passed the quote guards, so no roll could be bought.',
    intuition: 'The guards treat a spread wider than half the mid as no price at all. A refused session is skipped, never filled at a made-up price.',
    howToRead: 'A high count thins the sample and can hide the rolls that mattered.',
    seeAlso: ['usable_quote'],
  },
  filled_rolls: {
    id: 'filled_rolls',
    term: 'Filled',
    category: 'result',
    short: 'Roll dates on which a put was bought, out of all the roll dates in the study.',
    intuition: 'A roll date is refused when the nearest listed put fails the quote guards.',
    howToRead: 'A gap between filled and total is rolls the study could not make.',
    seeAlso: ['refused_fills'],
  },
  growth_vs_unhedged: {
    id: 'growth_vs_unhedged',
    term: 'Growth vs unhedged',
    category: 'portfolio',
    short: 'The hedged book’s time-average growth minus unhedged SPY’s, in percentage points a year, over one common window.',
    intuition: 'The hedged book spends a share of its wealth on puts at each roll and holds the rest in SPY with a flat dividend accrual.',
    howToRead: 'Negative means the hedge lowered long-run growth. Tinted rows grew faster than unhedged.',
    seeAlso: ['time_average_growth', 'budget_share'],
  },
  budget_share: {
    id: 'budget_share',
    term: 'Budget per roll (share of wealth)',
    category: 'portfolio',
    short: 'The share of the book’s current wealth spent on each roll’s puts; the rest stays in SPY.',
    intuition: 'It is re-set at every roll, so the premium spent in dollars grows with the book.',
    howToRead: 'A larger share is a larger bet on the hedge paying.',
    seeAlso: ['premium_per_year'],
  },
  premium_per_year: {
    id: 'premium_per_year',
    term: 'Premium spent per year',
    category: 'portfolio',
    short: 'Premium paid in a year as a share of the book’s wealth, gross: before the proceeds of selling puts back.',
    intuition: 'Because some rules sell the put back before expiry, the net carry cost is lower than this.',
    howToRead: 'It is the drag the puts must overcome to break even.',
    seeAlso: ['budget_share'],
  },
  ends_at_multiple: {
    id: 'ends_at_multiple',
    term: 'Ends at (×)',
    category: 'portfolio',
    short: 'The book’s wealth on the last date as a multiple of its starting wealth.',
    intuition: 'Drawdowns here are measured on roll dates only, because the 2008 data has no daily quotes.',
    howToRead: '1.0000 is break-even. Tinted rows end ahead of unhedged.',
    seeAlso: ['vs_unhedged_multiple'],
  },
  vs_unhedged_multiple: {
    id: 'vs_unhedged_multiple',
    term: 'vs unhedged (×)',
    category: 'portfolio',
    short: 'The hedged book’s ends-at multiple minus the unhedged book’s. It is a difference of multiples, not a rate.',
    intuition: 'Positive means the hedged book ended with more wealth.',
    howToRead: 'Check the next column before trusting a positive number.',
    seeAlso: ['ends_at_multiple', 'without_best_roll'],
  },
  without_best_roll: {
    id: 'without_best_roll',
    term: 'Without the best roll',
    category: 'portfolio',
    short: 'The same book’s ends-at multiple recomputed without its single best roll, only for a book that ended ahead of unhedged.',
    intuition: '“—” means the book did not end ahead. When this falls back below the unhedged multiple, one roll carried the result.',
    howToRead: 'A hedge that wins only with its best roll has not shown an edge.',
    seeAlso: ['ex_best'],
  },
} satisfies Record<string, Concept>

/** Every concept id. A typo at a call site is a compile error. */
export type ConceptId = keyof typeof CONCEPTS

/** Narrow a dynamic string (a data-driven key, a see-also entry) to a real id. */
export function isConceptId(id: string): id is ConceptId {
  return Object.prototype.hasOwnProperty.call(CONCEPTS, id)
}

// Stable display order for the Learn tab, grouped by category in CATEGORY_ORDER.
export const CONCEPT_LIST: Concept[] = Object.values<Concept>(CONCEPTS)

export const CATEGORY_ORDER: ConceptCategory[] = [
  'thesis',
  'metric',
  'option',
  'result',
  'regime',
  'bakeoff',
  'portfolio',
  'context',
]

export const CATEGORY_LABEL: Record<ConceptCategory, string> = {
  thesis: 'The thesis',
  metric: 'Fragility metrics',
  option: 'The put strategy',
  result: 'Reading the results',
  regime: 'Regimes & verdict',
  bakeoff: 'The metric bake-off',
  portfolio: 'Portfolio of puts',
  context: 'Market context',
}
