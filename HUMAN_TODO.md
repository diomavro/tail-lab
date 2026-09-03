# Human backlog

This is Dio's queue — account setup, paid services, and anything else only
a human can action. It is separate from `AGENT_TODO.md` (`docs/adr/0002`).
**The agent appends items here when it hits a real-world blocker; it never
acts on, removes, or reorders anything in this file.**

## Setup needed for v1

- [x] Create the GitHub repo `diomavro/tail-lab`, push `main`, enable
      Actions-can-create-PRs, add `CLAUDE_CODE_OAUTH_TOKEN`.
      **Done 2026-08-17** (private repo, PR-creation enabled, secret reuses
      the subscription token from the quizkit setup). Remaining before the
      daily agent runs: commit its GitHub Actions workflow (mirrors quizkit's
      hardened pattern; uses `docs/AGENT_MISSION.md` as the prompt).
- [x] ~~Create a lakeFS Cloud account + repo for the tail-lab lakehouse~~.
      **No longer needed — 2026-08-18**: the decision changed (`docs/adr/0012`
      supersedes `docs/adr/0006`'s lakeFS plan). lakeFS Cloud isn't free at
      this scale and self-hosting lakeFS OSS is a service to run for no
      strong reason here; the lakehouse is plain immutable Parquet on Tigris
      instead, with point-in-time/immutability enforced in application code.
- [x] Create the object-storage bucket (Fly Tigris) that the lakehouse lives
      on, and generate its access credentials.
      **Done 2026-08-18**: bucket `tail-lab-lake` provisioned on Fly Tigris
      (endpoint `https://fly.storage.tigris.dev`, region `auto`). Credentials
      are staged as Fly secrets on the `tail-lab` app for prod and in a
      gitignored `.env` at the repo root for local dev
      (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_ENDPOINT_URL_S3`,
      `AWS_REGION`, `TAIL_LAB_S3_BUCKET`, `TAIL_LAB_LAKE_BACKEND=tigris`).
- [x] Deploy the Fly.io app for the API/dashboard. **Done 2026-08-18** —
      live at https://tail-lab.fly.dev on the Tigris backend (SPA + `/api/*`,
      health-checked). The lake is seeded with VIX; the dashboard renders a
      real z-score. Redeploy with `flyctl deploy -a tail-lab --remote-only`.
      (The daily *agent* still never deploys — deploys stay human/operator.)
      *Superseded 2026-08-19 by `docs/adr/0016`: green `main` now
      auto-deploys via `.github/workflows/deploy.yml` once `FLY_API_TOKEN`
      is provisioned (item below); the agent itself still never deploys.*
- [x] Get a free FRED API key — unblocks the rates (`docs/DATA_CONTRACTS.md`
      #3) and credit (#4) ingestion adapters. **Done — already existed** in
      the `fred-data` MCP config (`~/.claude.json`); reused it and set it as
      the `FRED_API_KEY` repo secret (2026-08-17). For local dev, export
      `FRED_API_KEY` from that same value.

- [ ] **Create `AGENT_FIX_TOKEN` and add it to the repo's Actions secrets** —
      the one thing standing between the design review being an alarm and being
      a loop (`docs/adr/0024`). A fine-grained PAT scoped to `diomavro/tail-lab`
      with **Contents: read & write** is enough; nothing else.

      Why a PAT at all: a push made with the default `GITHUB_TOKEN` does not
      trigger workflows (GitHub's recursion prevention, `docs/adr/0016`), so a
      fix pushed by CI would never be re-reviewed and the loop would stall after
      one round — while *looking* like it worked.

      Blast radius, stated plainly because this repo has deliberately kept
      privileged tokens out of CI (`docs/adr/0019`): it can push to an `agent/*`
      branch and nothing else. Everything on that path is still gated by full
      CI, by `constitution-guard` (which blocks `.github/workflows/**` and every
      rule document), and by the review itself. It cannot deploy, cannot reach a
      broker, cannot touch the constitution.

      Until it exists the review job runs exactly as it does today — it reviews,
      it blocks, and it says in the log that the fix loop is off.

- [x] **Done** (verified 2026-09-02: the GitHub Actions secret exists and
      `GET /api/feedback` returns 401 rather than 404, so the Fly secret is set
      too — the item had simply never been ticked). Provision
      `TAIL_LAB_FEEDBACK_TOKEN` (`docs/adr/0014`, in-app feedback):
      generate a random secret and set it in **two** places — a Fly secret
      on the `tail-lab` app (`flyctl secrets set TAIL_LAB_FEEDBACK_TOKEN=...
      -a tail-lab`, so `GET /api/feedback` and the resolve endpoint stop
      404ing in prod) and a GitHub Actions repo secret of the same name +
      value (so `.github/workflows/daily-agent.yml`'s `Run the platform
      agent` step can read it — it's the one new secret the daily agent
      gets, deliberately not FRED/AWS/deploy creds). Until this is set, the
      feedback panel's writes (`POST /api/feedback`) still work — only the
      agent's read/resolve calls no-op (404, treated as "nothing pending").
- [x] Provision `FLY_API_TOKEN` as a GitHub Actions repo secret
      (`flyctl tokens create deploy -a tail-lab`) so the new CD workflow
      (`.github/workflows/deploy.yml`, `docs/adr/0016`) can auto-deploy
      every green `main` commit. Until set, the workflow no-ops with a
      visible notice. This token lives only in the deploy workflow — the
      daily agent never receives it.

## Data sourcing (research 2026-08-19 — see `docs/DATA_SOURCING.md`)

Phase 1 — free accounts (~30 min total, all $0):

- [ ] Get a free **Tiingo** API key (tiingo.com) → repo secret
      `TIINGO_API_KEY` + local `.env`. Unblocks replacing the throttled
      Yahoo chart endpoint as OHLCV primary (500 unique symbols/month,
      30+ yrs history) and the delisted-name backfill (`docs/adr/0010`).
      **Also unblocks the cross-source price validation Dio asked for**
      (2026-08-20): a true independent second source to reconcile against
      Yahoo. Keyless second sources are now walled (Stooq gates behind a JS
      proof-of-work); until Tiingo, the single-source guard is the bad-tick /
      stale-feed detector in `research/data_quality.py` +
      `GET /api/putlab/data-quality`, which catches print errors without
      flagging real crashes.
- [x] **Done 2026-09-03.** optionsDX corpus downloaded (83 archives, 1.1 GB)
      and moved to `data/vendor/optionsdx/`; adapter, contract, tests and
      `make ingest-optionsdx` shipped (`docs/DATA_CONTRACTS.md` #12). VIX is
      ingested — 168,351 quotes, 168/168 months, 2010-2023. **Two things left
      for you:** (a) the coverage is very uneven and SPY, the benchmark, has
      only 63 of 168 months — deciding whether to fill those gaps is a
      judgement about how much a market-priced SPY backtest is worth to you;
      (b) 18 byte-identical `(1)` duplicates are still sitting in `~/Downloads`
      (226 MB) and one `(2)` copy is in the vendor dir, all safe to delete —
      left alone rather than deleting your files unasked.
- [ ] Create a free **optionsDX** account (optionsdx.com) and download
      the free SPY/SPX/QQQ EOD option-chain zips (2010–2023, bid/ask +
      IV + greeks); drop them somewhere the agent can ingest from (e.g.
      upload to Tigris under `raw-drops/optionsdx/`). Unblocks validating
      a real-quote `OptionPricer` v2 against the BS proxy at $0.
- [ ] Create a free **Alpaca** account (data-only, no funding) → API
      keys as repo secrets. Free historical SIP equity bars (~2016+) and
      OPRA option history (Feb 2024+); second forward-collection source.

Phase 2/3 — paid decisions (small):

- [ ] Subscribe to **Sharadar "Prices"** direct at sharadar.com —
      **$9/mo**, personal license. Closes survivorship bias (15k delisted
      names to Dec 1998), point-in-time S&P 500 constituents, and gives a
      dependable bulk-CSV broad-universe feed in one. Verify at checkout
      the plan includes the SP500 constituents table.
- [ ] Decide the real-quote historical options buy (`docs/adr/0004`,
      supersedes the old "budget for ORATS/CBOE/OptionMetrics" item):
      recommended **ORATS Data API $99/mo for 2–3 months** (~$200–300,
      EOD chains 2007→present, full US universe) — first check their
      bulk-download/fair-use terms; fallback: historicaloptiondata.com
      one-off ($945 5-yr / $1,495 full-history L2 CSVs, 2002+).
- [ ] (When ready to actually trade) open an **IBKR** account via IBKR
      Ireland — execution venue only, not a data source (no broker serves
      expired-option history); US listed options are Hungary-eligible;
      OPRA live data ~$10/mo, commission-waivable.

## Historical puts — revised after the 2026-08-21 deep dive

`docs/DATA_SOURCING.md` §9 supersedes the options half of the Phase 1–3
queue above. Short version: for **evaluating** a put-buying tail strategy,
free data now covers the entire history including 2008, so the ORATS
$99/mo and the $945/$1,495 one-offs are no longer on the critical path.
Two free Cboe taps need no account at all and are queued for the agent
(`AGENT_TODO.md`); only these two items need you.

- [ ] Create the free **optionsDX** account (as already listed above) —
      now higher priority, because it is the validation set for the
      skew-aware pricer, not just a nice-to-have. All ten of their
      datasets list at **$0.00** (SPY, SPX, VIX, QQQ, TSLA, AAPL, NVDA,
      UVXY, SLV, BTC); SPX is stated as **2010–2023** EOD with bid/ask,
      IV and greeks. **While you are logged in, check which years are
      actually free** — the shop shows a "$0.00 – $50.00" range per
      product and the per-year split is only visible in the variant
      selector (`docs/DATA_SOURCING.md` §9.6).
- [ ] Request the **historicaloptiondata.com free data** (name + email at
      `historicaloptiondata.com/free-data/`, files land at
      dnfilevault.com). Full-format L2 EOD chains, **January 2003 →
      present**, one rotating symbol per calendar month — their own
      examples are **DIA for December 2008** and RUT for January 2009.
      This is the only free source found with real 2008–2009 option
      quotes. **When you request it, ask whether you can pick a past
      month** (Sep–Dec 2008 would be worth more than the current one);
      if not, it is still worth taking whatever month is on offer.

**Not needed now (was Phase 3):** ORATS at $99/mo. Re-decide only after
the agent has measured the model-vs-PPUT residual — if the proxy pricer
tracks Cboe's real-transaction PPUT/PPUT3M series closely, the paid
chains buy little for evaluation and their real justification is the
*signal* side (full-universe cross-sectional chains), which is a separate
decision. Sharadar at $9/mo is unaffected by this — it addresses G2/G3/G4
(survivorship and the broad-universe feed), not puts.

**One regression worth knowing:** Yahoo's chart endpoint now returns 429
from *residential* IPs too, not just datacenter ones (probed from your
workstation, 2026-08-21). `ingestion/ohlcv.py` still has Yahoo as
primary, so the Tiingo key above is now a genuine fix rather than an
upgrade.

## One licence call (2026-08-21 second deep dive, `docs/DATA_SOURCING.md` §10)

- [x] **Decided 2026-08-22: use it, do not depend on it.** (Dio.) Cleared
      for the private lake and the private dashboard; not republished, not a
      source of record, and nothing in `research/` or `api/` may require it —
      `ingestion/option_quotes.py` reads it from a local hash-verified
      download and every consumer degrades if the snapshot is absent. The
      realistic risk is not legal but disappearance: its own upstream vanished
      in 2026, and the SHA-256 pin protects against tampering, not deletion.
      If a clean posture is ever wanted, an Alpha Vantage subscription buys
      the same data under your own terms.

      Original decision text, kept for the reasoning:
      Decide whether tail-lab may use the **lambdaclass `data-v1`** option
      chains (SPY 2008–2025, QQQ 2011–2025, IWM 2008–2025; free, no account,
      SHA-256 pinned GitHub Release assets). This closes the 2008–2009 gap
      that every paid option in §3 was priced to close — but it is
      redistributed **"for research and educational reproducibility only"**,
      with a standing takedown offer to any rights-holder.

      **Two of the three unknowns are now closed (2026-08-21,
      `docs/DATA_VERDICTS.md`).** *Quality:* the chains reproduce Cboe's
      `PPUT` at **ρ=0.9927, tracking error 1.63%/yr** over 207 monthly rolls
      and 17.8 years, with zero unpriceable rolls — the quotes are real
      (though `mark`/IV/greeks are sentinel-filled before 2011 and must not
      be used). *Provenance:* the schema is a field-for-field match with
      **Alpha Vantage's `HISTORICAL_OPTIONS`** endpoint, so this is a
      commercial vendor's premium data, mirrored twice.

      **That cuts both ways, and the licence call is now yours alone.** A
      named vendor with published methodology is a much better quality story
      than an anonymous scrape — and a slightly worse redistribution story,
      because Alpha Vantage's terms, not merely "factual market data", are
      what the mirror's takedown offer is hedging. Reading: fine for a
      private lake feeding a private dashboard and for validating the model
      pricer over 2008–2009; not something to republish, and not something to
      make source-of-record. If you want a clean posture instead, an Alpha
      Vantage subscription buys the same data under your own terms.
      The agent has been told to build no adapter until you have answered
      this.
      **Nothing is blocked on you** — the free Cboe benchmark path (§9) works
      regardless; this only decides whether real 2008 chains join it.
