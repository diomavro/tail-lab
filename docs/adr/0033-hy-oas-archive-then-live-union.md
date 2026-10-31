# 33. HY OAS history: a pinned archive up to its end, the live union after it

Date: 2026-10-31

## Status

**Accepted** on the human merge of its PR. It touches guarded paths
(`docs/adr/`, `ARCHITECTURE.md`), so it ships as a **draft** PR. It amends
`docs/adr/0009` (a third stated exception).

## Context

- In April 2026 FRED cut `BAMLH0A0HYM2` to a sliding 3-year window and purged
  ALFRED.
- The `credit` dataset gets one partition per weekday, and each partition holds
  that sliding window. Reading only the newest partition loses about a day of
  history per run.
- So the credit half of the regime never saw 2008, 2011, 2016 or 2020.

## Decision

1. **The archive.** `credit_archive` is a one-off, immutable dataset from a
   pinned Wayback capture (URL and decoded sha256 in `contracts/credit.py`,
   captured 2025-11-04).
   - Licence confirmed by Dio 2026-10-31 (internal use only; values never
     served publicly). It is ingested by Dio, once, after merge
     (`make ingest-credit-archive`).
   - It is gated on matching live data within 0.005 over at least 250
     observations.
   - It refuses invalid rows and same-day re-writes.
   - Only the pinned (URL, sha256) pair is accepted. Another snapshot needs a
     reviewed code change, and none with full history exists after 2025-11-04.
   - **The archive partition must never be ignored without a replacement.**
     Ignoring it shrinks history to the live union, back to 2023-09.
2. **Precedence.**
   - The archive is authoritative for `obs_date <= archive_end`, read from the
     data.
   - Live is used after that date. Live is the union of every non-ignored
     `credit` partition `<= as_of`, with the newest partition winning and
     `vintage_date` breaking ties within one partition.
   - A value that slides out of FRED's window stays at its last partition.
3. **The HY guard.** `ingest_credit` refuses a run in which HY has no valid
   rows, so the refresh alert fires. IG is never guarded: nothing reads it.
4. **Staleness.** A print counts for 10 business days after its publication
   date (`obs_date` + 1 day). After that credit has no opinion, VIX decides, and
   one warning is logged per call.
5. **`CreditHistoryError`** is raised when the first live observation after
   `archive_end` is more than 7 days late, or missing. It is not a
   `LookupError`, so it surfaces as a 500 plus the error log rather than a 404
   or a silent VIX-only fallback.
6. **`IGNORED_BRONZE_PARTITIONS`**, keyed `(dataset, ingest_date)`, lets a bad
   partition of either dataset be dropped in a reviewed PR. The lake is never
   edited.
7. The classifier, the thresholds and the regime definition are unchanged.

**Amends ADR 0009** with exception 3, bounded by 553 of 553 identical overlap
observations (2023-09-25 to 2025-11-03). Revisions before 2023-09 are
unmeasured, because ALFRED was purged.

## Rejected, and residual risk

- **BAA10Y.** Its 2022 peak quarterly average is about the calm level, and
  2012 reads near COVID.
- **A rates-hedged HYG drawdown.** It never reached crisis before VIX did,
  matched the OAS labels on about 75% of 2008-2025 sessions, and missed
  2008-03, 2011-10 and 2016-02.
- **Carry-forward into each partition.** It could write away 3 years of
  history and exit 0, and it left poisoned partitions behind.
- **An automatic detector for valid-but-wrong FRED data.** It was designed and
  rejected over five review rounds.
  - No rule can tell a bad run from a revision.
  - Every version left cases that could not be recovered.
  - The evidence that the threat is real is nil: 0 disagreements across 28
    production partitions, and 553 of 553 identical against the archive.
- **Residual risk, accepted.** A wrong-but-valid FRED partition is not
  detected.
  - It rewrites **every observation after `archive_end` that it carries**,
    because the newest partition wins.
  - **Healed:** an observation date that a later good partition also carries is
    corrected when that partition lands.
  - **Not healed:** an observation date that no later partition carries stays
    wrong until the bad partition is ignored. That covers observations that had
    already left FRED's window, and a one-off date only the bad partition holds,
    such as a Saturday or holiday value.
  - **Effect on labels.** The wrong value is carried by the 10-business-day
    alignment and by hysteresis. In a replay, one such print moved 26 session
    labels.
  - Before about 2028-11 that set is small, because the window still reaches
    back to `archive_end`. After that it can be every post-`archive_end`
    observation older than the window.
  - **The seam check does not catch it**, because those observations are
    present, only wrong.
  - `archive_end` is fixed at 2025-11-03 by the data. Protection after it
    relies on the ignore list.

## Consequences

- About 850 of roughly 9,300 historical sessions relabel; the last 4 years are
  unchanged.
- The read grows by one file and about 30 KB per weekday. Compaction is due at
  20 MB or a 5 s cold scan, and is not built here.
- **Licence:** the data is ICE's and internal-use only. It is never committed,
  and only labels are served.
- IG is not backfilled.
- **Accepted minor gap.** A refused HY day still writes its quarantine
  partition for that date. A successful re-run on the same day then finds the
  quarantine date taken, so its own quarantined rows are not written (the
  bronze write is unaffected). Not fixed.
