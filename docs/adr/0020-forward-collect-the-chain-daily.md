# 20. Forward-collect the option chain daily, and treat a missed day as a failure

Date: 2026-08-26

## Status

**Accepted.** Implemented in `contracts/option_chain.py`,
`ingestion/option_chain.py`, `api/ingest_routes.py`,
`scripts/chain_snapshot.py` and `.github/workflows/daily-chain-snapshot.yml`.

## Context

`README.md` lists six core datasets. Five of them — OHLCV, the volatility
complex, rates, credit, the event calendar — share a property nobody wrote
down: **they can be backfilled.** FRED, Yahoo and Cboe all serve history on
demand, so a day nobody ran `make ingest-rates` is a missing *invocation*, not
a missing *fact*. Run it tomorrow and yesterday appears.

Dataset #6 — "forward-collected option chains for the narrow tradable set" —
does not share it. No free source sells a retroactive chain. The one real
quote history this platform has is a licence-limited vendor parquet a human
downloaded by hand (`docs/DATA_VERDICTS.md`), whose own upstream has already
vanished once. A session that goes un-snapshotted is gone permanently, and the
only way to own five years of quotes is to have started five years ago.

That asymmetry had gone unnoticed because nothing in the platform *needed*
chains yet: backtests are model-priced (`docs/adr/0004`) and the model-vs-market
residual is measured against the vendor slice (`docs/MODEL_RESIDUAL.md`). So the
dataset stayed at the bottom of the backlog behind items with visible payoffs.

This is backwards. The value of starting the collection is not what it answers
this week; it is that **the option to answer anything at all in 2029 expires a
little more every day it is not running.** Every other backlog item can be done
later at the same cost. This one cannot be done later at any cost.

Two constraints shaped the design rather than the intent:

**`api` may never import `ingestion`** (import-linter, `pyproject.toml`). So the
obvious "add an endpoint that runs the adapter" is not available.

**The write needs credentials; the fetch does not.** Cboe's chain CDN is
keyless. Only the final bronze write touches object storage. Granting CI
long-lived Tigris keys for the sake of one PUT would put data credentials in the
same GitHub Actions that `automerge.yml` merges agent PRs into — the precise
hazard `docs/adr/0019` refused for a brokerage credential, for the same reason.

## Decision

**1. Cboe's public delayed-quote CDN is the source.**
`https://cdn.cboe.com/api/global/delayed_quotes/options/{SYMBOL}.json`, keyless,
one document per underlying carrying bid/ask/size, open interest, volume, and
Cboe's own IV and greeks. It is the exchange's feed rather than a reseller's.
Yahoo's `/v7/finance/options`, named as the fallback in
`contracts/options_calendar`, now answers 401 without a cookie+crumb pair; Cboe
needs neither.

**2. Store a slice, not the chain.** Puts only, strikes within 0.40–1.05 of
spot, tenors 0–180 days. SPY's full document is 5.9 MB and 13,288 contracts;
the slice is ~3,600 rows. The bands match `contracts/option_quotes` so the
vendor back-history and this forward collection union on their shared columns
without a translation layer.

**3. Cboe's zero-filled greeks are stored as null, not zero.** A listed put
cannot have 0.0 implied volatility, so a zero there is a sentinel. Storing it
as a number is the exact error `docs/DATA_VERDICTS.md` caught in the vendor
file. Because Cboe computes the block together, a zero IV nulls delta and theo
on the same row.

**4. The sweep is split at the credential seam**, exactly as
`daily-verdict-sweep.yml` is. The workflow does the credential-free
fetch-and-slice; the live app, which already holds the Tigris keys, does the
write via one narrow token-gated endpoint that accepts one dataset validated
against one contract. CI holds a bearer token and no data credentials. A
general "write rows to bronze" endpoint was rejected: nothing needs one, and it
would be a far larger thing to have to trust.

**5. One partition per day, for the whole set.** Bronze is immutable and
re-ingesting an `ingest_date` is a no-op (`lake/store.py`), so a per-symbol POST
would silently persist only the first symbol. The sweep accumulates and writes
once — which also means a half-finished sweep cannot look complete to an as-of
read (`docs/adr/0009`).

**5a. The partition is the session, not the clock.** *(Amended 2026-08-27,
after the first scheduled run.)* `ingest_date` is derived from the quotes'
own `quote_date`, never from the runner's wall clock. GitHub ran the 21:30 UTC
cron at **00:57 the next day** — scheduled workflows drift under load — and a
clock-derived partition turns that drift into two bugs at once: the session
lands under tomorrow's key, and tomorrow's real sweep then no-ops against the
key that already exists (bronze is immutable) and is lost. Deriving the
partition from the quotes makes a late run land correctly, makes a re-run of
the same session no-op the way immutability intends, and makes
`read_bronze_as_of(session)` mean "the chain as it closed that session"
rather than "whenever the runner happened to fire".

**6. An empty sweep is a failure, not a no-op.** Below 200 rows across two
dozen liquid chains the script exits non-zero and the workflow goes red. Every
other scheduled job here may legitimately do nothing; this one may not, and the
alert has to fire on the day it can still be acted on.

**7. The forward-collected set is narrower than the screening universe** — 24
names, not 70 (`DEFAULT_SNAPSHOT_SYMBOLS`). "Screen broad, trade narrow"
(`docs/adr/0008`) already scopes dataset #6 this way, and one-write-per-day
makes it one HTTP body that a 1 GB Fly machine has to parse.

Measured on 2026-08-26, all 24 chains: **20,882 rows, 4.68 MB of JSON, 25.5
seconds**. The distribution is heavily skewed — SPY (3,561), QQQ (2,876) and
GLD (2,070) are a third of it, while a typical single name is under 600 rows.
So widening toward the full 70 is affordable (the 46 additions are mostly
thin chains) and is a queued increment; the constraint that will bite first is
POST body size, not fetch time.

**8. Freshness is public.** `GET /api/ingest/option-chain/status` reports the
last session collected and how stale it is. The daily agent reads it before it
picks any work, and a gap outranks whatever was next on its backlog.

## Consequences

- **The clock starts now, and the dataset compounds.** From the first green run
  the platform accumulates something it cannot buy: a point-in-time record of
  what the put wing actually cost, on names chosen for tail sensitivity. In a
  year that is the dataset that turns `docs/adr/0004`'s model-priced caveat from
  a measured constant into a measured *function* of regime and name.
- **The model-vs-market residual becomes a live measurement.** `make skew`
  currently reports the gap on 210 historical SPY dates. The same computation
  will run forward on 24 names, daily, free.
- **The first scheduled run found the clock bug, and it was the expensive
  kind: silent.** Nothing went red. The sweep reported success, 21,176 rows
  landed, and the only visible symptom was a row count that changed on a
  partition that is supposed to be immutable. Had it not been caught, the
  Thursday session would have been dropped without a single failing check —
  the exact loss this ADR exists to prevent, produced by the mechanism meant
  to prevent it.
- **A red workflow is now a real alarm.** Everywhere else in this repo a failed
  scheduled job can be re-run tomorrow. Here it cannot, so this is the one red
  build that should interrupt a day.
- **Delayed quotes are 15 minutes stale, and that is fine.** The sweep runs
  21:30 UTC on weekdays — 30 to 90 minutes after the 16:00 ET close depending on
  DST — so it records settled end-of-day marks, not an intraday snapshot.
- **What this does not do.** It does not make backtests market-priced. Two
  dozen names collected from today build no history for last year, and nothing
  in `research/` or `api/` may depend on this dataset existing until it is deep
  enough to be worth depending on. It is a deposit, not a feature.
