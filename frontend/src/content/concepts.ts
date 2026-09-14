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
export const CONCEPTS: Record<string, Concept> = {
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
      'Read the dashboard as sorting exposure, not calling a top. A name high on the fragility screen is one you would want convexity on if you believe a tail event is coming at some unknown time -- the screen deliberately says nothing about when.',
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
      'premium = BlackScholesPut(spot, strike, T, r=0.04, σ);  σ = clip(annualized 20-day realized vol, 0.06, 2.0)',
    intuition:
      'Real historical option quotes are paid data the project does not yet have, so v1 prices every put with closed-form Black-Scholes (ADR 0004). The implied-volatility input is a proxy: the trailing 20-day realized volatility of the underlying (sample std of daily log returns, annualized by √252), floored at 0.06 and capped at 2.0 so a dead-calm or crashing short window cannot imply free or absurd puts. The point-in-time honesty holds -- the vol at each entry uses only past prices -- but there is a deep caveat: the whole thesis is that the market MISPRICES tail risk, and a model priced off its own vol inputs cannot see that mispricing.',
    howToRead:
      'Read the put P&L as a RELATIVE ranking of which fragility metric sorts payoffs best, never as literal expected P&L. Absolute ROI numbers will change (likely a lot) once real option quotes replace the model.',
    seeAlso: ['oom_put', 'roi_on_premium', 'in_sample', 'fragility_thesis'],
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
    formula: 'hit_rate = #{rolls with payoff > notional} / n_cycles',
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
      'The fragility screen ASSUMES the metrics are worth ranking on; the bake-off tests that prior question -- which metric actually sorted realized OOM-put payoffs best over the lookback. For each of six candidate screens (the five raw metrics plus the composite) it holds an equal-weight put basket on that screen top_k most fragile names (default 5) and reports the basket blended ROI, hit rate, bleed, per-regime verdict, Spearman, and lift. Every name is backtested once and the six screens differ only in how they rank and select from that shared pass, so it is cheap. Crucially this is an in-sample, cross-sectional association -- a chooser for which screen to rank on, never a forward predictive backtest.',
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
      'The absolute VIX level tells you the regime; the stretch z-score tells you how UNUSUAL today reading is versus its own recent history. It standardizes the latest close against the mean and sample standard deviation of the trailing 20 trading days, so a large positive z means fear has spiked well above its recent norm and a negative z means the market is unusually calm relative to itself. It is the one live computed context metric on the dashboard, point-in-time correct (only past closes enter the window).',
    howToRead:
      'Roughly, z above +2 flags an acute fear spike relative to the last month, z below 0 an unusually quiet tape. It is context for reading the regime and the screen -- a high stretch says today is far from recent normal -- not itself a trade signal. Undefined when the trailing window was flat (zero variance).',
    seeAlso: ['regime'],
  },
}

// Stable display order for the Learn tab, grouped by category in CATEGORY_ORDER.
export const CONCEPT_LIST: Concept[] = Object.values(CONCEPTS)

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
