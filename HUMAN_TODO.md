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

- [x] **Done 2026-09-04** — verified 2026-09-19: `gh secret list` shows
      `AGENT_FIX_TOKEN` created 2026-09-04, and the loop demonstrably works.
      PR #108's `agent-review` logged `HAS_FIX_TOKEN: true`, applied its
      findings, pushed commit `99d5d83` and failed the run on purpose so the
      fresh run carried the authoritative verdict — exactly the behaviour the
      item below describes as blocked. **One caveat learned in the process:**
      the loop reverted two *correct* statements in `CLAUDE.md` while applying a
      genuine code fix, so its pushes need reading, not merging on trust.
      Original item:
      **Create `AGENT_FIX_TOKEN` and add it to the repo's Actions secrets** —
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

- [x] **Done 2026-09-03.** Free **Tiingo** API key in the `TIINGO_API_KEY`
      repo secret + local `.env`, verified live (`/api/test` → 200).
      Unblocks replacing the throttled Yahoo chart endpoint as OHLCV primary
      and the cross-source price validation Dio asked for (2026-08-20) — a
      true independent second source to reconcile against Yahoo, which
      matters because the keyless alternatives are now walled (Stooq gates
      behind a JS proof-of-work).
      **Verified working:** SPY 1993-01-29 → 2026-09-02, with `adjClose`
      and `divCash` (2008-09-15 close 120.09 vs adjClose 86.33).
      **Does NOT unblock the delisted-name backfill** (`docs/adr/0010`) —
      see the measured spot-check under "Switch OHLCV primary to Tiingo" in
      `AGENT_TODO.md`. That still needs a paid or different source, so it
      stays open below.
- [ ] **A source for delisted names**, because Tiingo is not one. Needed for
      the survivorship-bias-free backfill (`docs/adr/0010`) and therefore for
      any honest crisis-period backtest: a universe screened only on names
      that still exist in 2026 cannot see 2008. Candidates to price up:
      Sharadar SEP (Nasdaq Data Link, ~$50/mo, explicitly survivorship-free),
      Norgate (~$70/mo, US equities incl. delisted), or CRSP via an academic
      affiliation (free at some institutions — worth asking Milestone).
- [x] **Done 2026-09-03.** optionsDX corpus downloaded (83 archives, 1.1 GB)
      and moved to `data/vendor/optionsdx/`; adapter, contract, tests and
      `make ingest-optionsdx` shipped (`docs/DATA_CONTRACTS.md` #12). VIX is
      ingested — 168,351 quotes, 168/168 months, 2010-2023. **Two things left
      for you:** ~~(a) the coverage is very uneven and SPY, the benchmark, has
      only 63 of 168 months~~ and ~~(b) 18 byte-identical `(1)` duplicates are
      still sitting in `~/Downloads` (226 MB) and one `(2)` copy is in the
      vendor dir~~.

      **Both are resolved — nothing here needs you (checked 2026-09-19).**
      (a) is the figure `CLAUDE.md` records as **wrong**: it predated the rest
      of the corpus arriving on 2026-09-03. SPY is **168 of 168 months with
      zero gaps**, as are vix (168), qqq (144), nvda (96) and tsla (96)
      (`docs/DATA_CONTRACTS.md` #12, measured 2026-09-09). There is no gap to
      decide about. (b) the duplicates are gone: no optionsDX `(1)` files remain
      in `~/Downloads` and no `(2)` copy in the vendor dir.

      **What IS still open, and it is an agent job not yours:** `spx` is not
      ingested — 22 archives sit in the vendor dir but the ingest is OOM-killed
      at ~3.9 GB and needs a chunked bronze write (`AGENT_TODO.md`).
- [x] **Done 2026-09-03** — the optionsDX account and corpus. Superseded by
      the entry above, which carries the outcome; kept only as a pointer so
      the phase reads completely.
- [~] **Account created 2026-09-03 (Dio). Credentials NOT yet provisioned** —
      checked, and neither `.env` nor the repo secrets hold an Alpaca key.
      Two values, key id and secret, from the Alpaca dashboard. Paste each
      ONCE at the hidden prompt (a repeated paste is what corrupted the
      Tiingo entry — it landed as the token three times over):

      ```bash
      cd ~/Documents/apps/tail-lab
      read -rsp 'Alpaca key id: '  K && printf 'ALPACA_API_KEY_ID=%s\n'     "$K" >> .env && unset K
      read -rsp 'Alpaca secret: '  K && printf 'ALPACA_API_SECRET_KEY=%s\n' "$K" >> .env && unset K
      gh secret set ALPACA_API_KEY_ID     --repo diomavro/tail-lab
      gh secret set ALPACA_API_SECRET_KEY --repo diomavro/tail-lab
      ```

      What it buys: free historical SIP equity bars (~2016+) as a THIRD
      price source, and — the part nothing else here has — **OPRA option
      history from Feb 2024**, i.e. real expired-contract quotes. That is a
      forward-collection source that does not depend on the daily Cboe
      snapshot never missing a day (`docs/adr/0020`), and the only free way
      to check a snapshot after the fact.

      **Test this first, before any adapter is built** (`docs/DATA_SOURCING.md`
      §10): do the historical option endpoints actually return contracts that
      have since EXPIRED, or only live ones? The whole value is the former,
      it is trivial to check with a key, and the Tiingo probe today is the
      argument for checking: that source also looked fine until the specific
      question was asked, and then returned HTTP 200 with zero rows for the
      exact names the feature needed.

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

- [x] **Done 2026-09-03** — optionsDX account created and the corpus
      ingested; all ten datasets were indeed $0.00. Outcome and what is
      still open are recorded once, in the phase-1 entry above.
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
decision.

**That precondition is now met, and it points at "do not buy"
(`docs/MODEL_RESIDUAL.md`, checked 2026-09-19).** The replication tracks
**ρ = 0.9913** on PPUT over 438 monthly rolls since 1990 and **ρ = 0.9972**
on PPUT3M over 89 quarterly rolls, at tracking errors of 1.68 %/yr and
**0.98 %/yr**. By this item's own test that is "closely", so the $99/mo
buys little for *evaluation*. Two caveats before you close it for good: the
residual is **+1.34 %/yr at 5 % OTM and +2.71 %/yr at 10 %** — the model
underpays, and it **doubles as the strike goes deeper**, so it understates
its own optimism at the 20 % OOM end the S1 thesis is about; and the
*signal*-side justification is untouched by any of this. Sharadar at $9/mo is unaffected by this — it addresses G2/G3/G4
(survivorship and the broad-universe feed), not puts.

**One regression worth knowing — since fixed.** Yahoo's chart endpoint
returns 429 from *residential* IPs too, not just datacenter ones (probed
from your workstation, 2026-08-21). At the time `ingestion/ohlcv.py` still
had Yahoo as primary. **It no longer does** (checked 2026-09-19): that
module's docstring now reads "Fallback source: Yahoo's chart JSON — the
adapter's original primary", with Tiingo promoted ahead of it. Nothing to
do.

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

## Operational — found 2026-09-19

- [ ] **Decide the cadence for a scheduled OHLCV/VIX ingest, and merge the
      workflow.** There is no cron for either. `daily-chain-snapshot`,
      `daily-verdict-sweep`, `daily-agent` and `weekly-cleanup` are the only
      scheduled jobs, so price and volatility data drift until someone runs
      `make ingest-vix` / `make ingest-ohlcv` by hand.

      **Measured 2026-09-19:** bronze had last closed on **2026-08-20** — 29
      days stale. `/api/vix/stretch` in production was serving a 2026-08-20
      close of 16.01 at a z-score of −0.10; the same call after a manual
      refresh returns **2026-09-18, close 14.81, z = −0.83**. The regime
      classifier keys off VIX and every fragility metric keys off OHLCV, so
      for those 29 days the live Screen ranked 70 names on month-old returns
      and the Regime surface showed a month-old label. Both are refreshed now.

      **Cost of delay:** unlike the option chain this is fully recoverable —
      Tiingo and Cboe both serve history on demand, so a missed day is one
      `make` invocation from being caught up, and nothing is lost permanently.
      What it costs is silent wrongness: there is no alarm, the numbers look
      current, and the only symptom is a date buried in a payload. That is the
      opposite failure mode from `docs/adr/0020`'s chain sweep, which fails red
      on purpose.

      **Why this is yours and not the agent's.** The fix is a new file under
      `.github/workflows/`, which is constitutional (`docs/adr/0022`) and needs
      a human-merged PR. Two judgements are also genuinely yours: the **cadence**
      (daily after the US close mirrors the chain sweep; weekly would bound the
      drift at 7 days for a seventh of the requests), and whether your **Tiingo
      plan's rate limit** tolerates 70 symbols plus VIX in one run — the free
      tier's hourly and daily caps are the binding constraint, and a partial
      run that half-refreshes the universe is worse than a clean weekly one,
      because the Screen would then rank some names on today's data and others
      on last week's.

      Say the word and I will draft the workflow; I have not guessed at either
      number.
