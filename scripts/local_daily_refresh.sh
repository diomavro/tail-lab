#!/usr/bin/env bash
# Daily local refresh of every KEYLESS bronze source, for the window in which
# GitHub Actions is blocked on billing (HUMAN_TODO.md).
#
# WHAT THIS IS NOT: it does **not** sweep the option chain. That has its own
# unit (`tail-lab-chain.timer`) for a reason -- it is the one source nobody
# sells retroactively, so it must run AFTER the US close, and bronze
# immutability means an early write claims the session's partition and the
# post-close run then silently no-ops. Running it from here, at a morning
# hour, would destroy exactly the data this repo exists to collect. Every
# source below is the opposite: it serves history on demand, so a missed day
# costs a re-run and nothing more.
#
# Measured 2026-09-21, which is why this exists: `option_chain_snapshot`,
# `vix` and the `ohlcv_*` tables were 2-3 days old (healthy), but
# `option_quotes` and `cboe_strategy` were **31 days** stale and `rates`,
# `credit`, `vix_futures`, `vix_complex`, `event_calendar`,
# `sp500_constituents` and `mpd` had no bronze table at all.
#
# Behaviour, all deliberate:
#   * one source's failure never stops the others -- the whole point of a
#     sweep is that a dead feed costs only itself;
#   * the exit code is non-zero if ANY source failed, so `OnFailure=` fires
#     and the failure is not silent;
#   * re-running is free. Bronze is immutable, so a second run for the same
#     ingest_date is a no-op that preserves the first;
#   * sources needing a key that is not present are SKIPPED, not failed --
#     they are a HUMAN_TODO item, not a broken feed, and counting them would
#     make the alert cry wolf every single day.
set -uo pipefail

# Overridable ONLY so tests can sandbox it. systemd passes an absolute
# ExecStart and sets no Environment=, so production always takes the default.
REPO="${TAIL_LAB_REPO:-/home/dio/Documents/apps/tail-lab}"
LOG="$REPO/.daily-refresh.log"
LOCK="$REPO/.daily-refresh.lock"

# Serialise. `lake/store.py`'s write_bronze is read-then-append with NO
# compare-and-swap, so two concurrent writers both see "partition absent" and
# both append -- measured on a throwaway lake, 3 rows became 6, silently
# breaking the immutability every other guard in this repo depends on.
# systemd coalesces repeat triggers of the same unit, so the live vector is a
# HUMAN re-run overlapping the timer's 45-minute window -- which this script's
# own failure message actively invites. -n, not -w: a second run while one is
# in flight should say so and stop, not queue up behind it.
exec 9>"$LOCK" || exit 1
# -w, not -n. A legitimate overlap (a human re-running while the timer fires)
# resolves by waiting a few minutes and then proceeding. An orphaned child
# holding the lock forever does not -- and with `-n; exit 0` that case made
# every subsequent run report SUCCESS to systemd, so OnFailure= never fired
# and the refresh silently stopped happening. Waiting then failing tells the
# two apart without a heuristic.
if ! flock -w 300 9; then
  echo "=== daily refresh $(date -u +%FT%TZ): $LOCK still held after 300s ===" >> "$LOG"
  echo "    A previous run is stuck or an orphaned child holds the lock." >> "$LOG"
  echo "    Check: fuser -v $LOCK" >> "$LOG"
  exit 1
fi
SYMBOLS="${REFRESH_OHLCV_SYMBOLS:-}"

run_one() {
  local label="$1"; shift
  echo "--- $label"
  # 9>&- so the lock fd does NOT leak into make/python. Measured: children
  # inherit it, so one orphaned child pins the lock after its parent dies,
  # and from then on every timer firing takes the busy-lock path forever.
  if "$@" 9>&- >>"$LOG" 2>&1; then
    echo "    ok"
    return 0
  fi
  echo "    FAILED ($label)"
  return 1
}

# Keep the log bounded: ~20 KB a run x 250 weekdays is 5 MB a year, growing
# without limit. One rolled generation is plenty to diagnose yesterday.
if [ -f "$LOG" ] && [ "$(wc -c <"$LOG" 2>/dev/null || echo 0)" -gt 2000000 ]; then
  mv -f "$LOG" "$LOG.1" 2>/dev/null || true
fi

{
  echo "=== daily refresh $(date -u +%FT%TZ) ==="
  cd "$REPO" || { echo "FATAL: repo not found"; exit 1; }

  failed=()
  skipped=()

  # NOT in this list: `fomc` and `earnings`. Both write the SHARED
  # `event_calendar` dataset, which holds one immutable snapshot per
  # ingest_date from ONE producer (docs/DATA_CONTRACTS.md #5). Whichever runs
  # first claims the day; the second either loses its rows silently (fomc) or
  # refuses loudly (earnings, via _refuse_if_already_written_today). Measured
  # here: running both in one pass fails every single day.
  #
  # So a daily loop cannot include them without silently deciding which HALF
  # of the event calendar exists on each date -- a data question, not a
  # scheduling one. `ingestion/earnings.py`'s docstring names the real fix
  # ("combine sources into one write"); it is queued in AGENT_TODO.md. Until
  # then run whichever you want by hand, on a day the other has not claimed.
  # NOT here either: `option-quotes`. It reads a hand-downloaded,
  # hash-verified vendor parquet (data/vendor/lambdaclass-data-v1/, dated
  # 2026-08-21, last quote 2025-11-21) — a frozen file, not a feed. Ingesting
  # it daily rewrites the same 42,131 rows to object storage every weekday,
  # ~10.5M duplicate rows a year, and its "31 days stale" reading was never a
  # freshness problem. Re-run `make ingest-option-quotes` by hand when the
  # vendor file itself changes.
  #
  # `mpd` and `options-expiry` ARE here: both keyless, both absent from
  # bronze, and `research/cadence.py` falls back to a hand-maintained table
  # without the latter.
  for target in vix vix-complex vix-futures cboe-strategy \
                sp500-constituents mpd; do
    run_one "$target" make "ingest-$target" || failed+=("$target")
  done

  # OHLCV is per-symbol. Default to the chain universe so the two stay in
  # step and the request volume stays comparable to the sweep's.
  if [ -z "$SYMBOLS" ]; then
    SYMBOLS=$(env -u PYTHONPATH .venv/bin/python -c \
      "from tail_lab.contracts.option_chain import DEFAULT_SNAPSHOT_SYMBOLS as D; print(' '.join(D))" \
      2>/dev/null)
  fi
  # An empty list here would make the loop below a no-op and the whole script
  # exit 0 having refreshed no OHLCV at all -- a silent success, which is the
  # exact failure mode every other guard in this repo exists to kill. A
  # broken venv or a renamed constant is enough to cause it.
  if [ -z "$SYMBOLS" ]; then
    echo "--- ohlcv: FAILED to resolve the symbol list (empty)"
    echo "    refusing to report success on a run that refreshed no OHLCV"
    failed+=("ohlcv:symbol-list")
  fi
  for sym in $SYMBOLS; do
    run_one "ohlcv:$sym" make ingest-ohlcv "SYMBOL=$sym" || failed+=("ohlcv:$sym")
    run_one "expiry:$sym" make ingest-options-expiry "SYMBOL=$sym" || failed+=("expiry:$sym")
  done

  # FRED-backed sources. Free, but keyed, and the key lives as a GitHub repo
  # secret rather than in .env -- so locally they are not broken, they are
  # unconfigured. Skip loudly; HUMAN_TODO.md carries the item.
  # Print a token rather than relying on the exit code alone: a python that
  # cannot even import the config would exit non-zero and be indistinguishable
  # from "no key configured", silently skipping two sources forever.
  fred=$(env -u PYTHONPATH .venv/bin/python -c \
    "from tail_lab.config import get_settings; print('KEY' if get_settings().fred_api_key else 'NOKEY')" \
    2>/dev/null)
  if [ "$fred" = "KEY" ]; then
    for target in rates credit; do
      run_one "$target" make "ingest-$target" || failed+=("$target")
    done
  elif [ "$fred" = "NOKEY" ]; then
    skipped+=(rates credit)
    echo "--- rates, credit: SKIPPED (no FRED_API_KEY in .env -- see HUMAN_TODO.md)"
  else
    echo "--- rates, credit: FAILED -- the config probe itself did not run"
    failed+=("fred-probe")
  fi

  echo
  [ ${#skipped[@]} -gt 0 ] && echo "skipped (unconfigured): ${skipped[*]}"
  if [ ${#failed[@]} -gt 0 ]; then
    echo "FAILED SOURCES (${#failed[@]}): ${failed[*]}"
    echo "Each serves history on demand, so a re-run recovers them:"
    echo "    cd $REPO && ./scripts/local_daily_refresh.sh"
    echo "=== exit=1 $(date -u +%FT%TZ) ==="
    exit 1
  fi
  echo "all sources refreshed"
  echo "=== exit=0 $(date -u +%FT%TZ) ==="
} >> "$LOG" 2>&1
