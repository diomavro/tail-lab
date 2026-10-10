#!/usr/bin/env bash
# Weekly refresh of `tiingo_eod` (docs/DATA_CONTRACTS.md #14): every universe
# symbol's full daily history, dividends and splits, from Tiingo.
#
# Why its own script and timer rather than a line in local_daily_refresh.sh:
# Tiingo's free tier allows ~50 requests an hour and a run is one request per
# universe symbol, so the ingest throttles itself to 45/hour -- about 95
# minutes for the 70 names. Inside the daily script it would hold that run's
# lock for an hour and a half; sharing the daily lock file would make the
# daily run's own 300 s wait time out and fire its alert. The race the lock
# prevents (write_bronze has no compare-and-swap, so two writers double a
# partition) is per dataset, so this dataset gets its own lock.
#
# Behaviour, all deliberate:
#   * ONE locking layer. This script takes the lock and runs the ingest
#     directly -- never through `make`, because `make ingest-tiingo-eod` runs
#     this script, and a child flock on the same file would block on the fd
#     its parent still holds: a deadlock that ends only at TimeoutStartSec.
#   * no key is SKIPPED, not failed -- it is a HUMAN_TODO item, not a broken
#     feed (the FRED pattern in local_daily_refresh.sh). A probe that crashes is
#     a failure: it cannot be told apart from "unconfigured" otherwise.
#   * a failed fetch fails the run (non-zero, so OnFailure= fires); the ingest
#     itself has already refused to write a partition missing a symbol.
#   * re-running the same day is a logged no-op: the ingest skips a day whose
#     partition already exists rather than spending 95 minutes to no-op.
set -uo pipefail

# Overridable ONLY so tests can sandbox it; systemd sets neither.
REPO="${TAIL_LAB_REPO:-/home/dio/Documents/apps/tail-lab}"
LOCK_WAIT="${TAIL_LAB_LOCK_WAIT:-300}"
LOG="$REPO/.tiingo-refresh.log"
LOCK="$REPO/.tiingo-refresh.lock"

exec 9>"$LOCK" || exit 1
# -w, not -n: a legitimate overlap waits; an orphaned holder fails loudly
# instead of every later run reporting success (local_daily_refresh.sh).
if ! flock -w "$LOCK_WAIT" 9; then
  echo "=== tiingo refresh $(date -u +%FT%TZ): $LOCK still held after ${LOCK_WAIT}s ===" >> "$LOG"
  echo "    A previous run is in flight (it takes ~95 min) or an orphaned child holds the lock." >> "$LOG"
  echo "    Check: fuser -v $LOCK" >> "$LOG"
  exit 1
fi

# Bounded log, rolled only while holding the lock: a second run waiting on it
# must not rename the log out from under the run in flight.
if [ -f "$LOG" ] && [ "$(wc -c <"$LOG" 2>/dev/null || echo 0)" -gt 2000000 ]; then
  mv -f "$LOG" "$LOG.1" 2>/dev/null || true
fi

{
  echo "=== tiingo refresh $(date -u +%FT%TZ) ==="
  cd "$REPO" || { echo "FATAL: repo not found"; exit 1; }

  key=$(env -u PYTHONPATH .venv/bin/python -c \
    "from tail_lab.config import get_settings; print('KEY' if get_settings().tiingo_api_key else 'NOKEY')" \
    9>&-)  # stderr reaches the log: a crashed probe shows its traceback
  if [ "$key" = "NOKEY" ]; then
    echo "--- tiingo_eod: SKIPPED (no TIINGO_API_KEY in .env -- see HUMAN_TODO.md)"
    echo "=== exit=0 $(date -u +%FT%TZ) ==="
    exit 0
  fi
  if [ "$key" != "KEY" ]; then
    echo "--- tiingo_eod: FAILED -- the config probe itself did not run"
    echo "=== exit=1 $(date -u +%FT%TZ) ==="
    exit 1
  fi

  echo "--- tiingo_eod (throttled to 45 requests/hour; a manual use of the key this hour can trip a 429)"
  # 9>&- so the lock fd does not leak into python: an orphaned child would
  # otherwise pin the lock after this script dies.
  if env -u PYTHONPATH .venv/bin/python -c \
    "from tail_lab.ingestion.tiingo_eod import ingest_tiingo_eod; from tail_lab.config import get_lake_store, get_settings; from tail_lab.observability import configure_logging; configure_logging(); r = ingest_tiingo_eod(get_lake_store(), api_key=get_settings().tiingo_api_key); print('skipped: partition exists' if r.skipped else f'committed {r.valid_rows} rows for {len(r.symbols)} symbols -> {r.bronze_path} ({r.quarantined_rows} quarantined, withheld: {list(r.withheld_symbols)})')" \
    9>&-; then
    echo "=== exit=0 $(date -u +%FT%TZ) ==="
    exit 0
  fi
  echo "    FAILED (tiingo_eod). Tiingo serves history on demand -- a missed week is recoverable:"
  echo "    cd $REPO && ./scripts/weekly_tiingo_refresh.sh"
  echo "=== exit=1 $(date -u +%FT%TZ) ==="
  exit 1
} >> "$LOG" 2>&1
