#!/usr/bin/env bash
# OnFailure handler for tail-lab-chain.service.
#
# Why this exists: the chain sweep is the one job that cannot be caught up
# later (docs/adr/0020). While it ran on GitHub Actions a failure produced a
# red build and an email. Run from a local systemd timer instead, a failure
# appends to a log nobody reads and exits non-zero into the void -- the sweep
# degraded from "loud on failure" to "silent on failure", which for an
# unrecoverable job is the worst possible direction.
#
# Fired by `OnFailure=`, so it runs only when the sweep actually failed. It
# leaves three independent traces, because the failure happens at 23:35 local
# time when nobody is watching and a toast nobody sees is not an alert:
#   1. a marker FILE that persists until acknowledged (the durable one),
#   2. this script's stdout, which journald attributes to the unit,
#   3. a desktop notification at critical urgency.
#
# What each defence is for, all measured rather than assumed:
#
# * The marker is written FIRST and lands in ~6 ms, so a mid-run kill has
#   almost no window. It is written to a temp file and renamed, because a
#   plain `> "$MARKER"` opens O_TRUNC -- which destroys YESTERDAY's
#   unacknowledged marker before knowing whether today's write can succeed.
# * If the marker cannot be written to the repo, it falls back to $HOME and
#   the script exits NON-ZERO. An OnFailure handler that loses the durable
#   channel must not report success: a red alert unit is strictly better than
#   a green one with no marker.
# * `echo`, not `logger`. `logger` writes over /dev/log as _TRANSPORT=syslog
#   and journald resolves the sender's cgroup via /proc/<pid>, which has
#   usually exited -- measured, the unit fields were absent on 3 of 8 runs, so
#   `journalctl --user -u tail-lab-alert@chain` found the line barely half the
#   time. A oneshot's stdout goes to journald on the unit's own fd and is
#   always attributed.
# * `notify-send` is wrapped in `timeout`. It has no timeout of its own and
#   against a bus that accepts but never answers (the realistic post-resume
#   case) it blocks indefinitely -- measured at 60 s against a wedged socket,
#   which would leave this unit in `activating` and cause systemd to COALESCE
#   the next alert into it, disarming tomorrow.
# * There is deliberately NO `if [ -n "$DBUS_SESSION_BUS_ADDRESS" ]` guard.
#   Measured: with the variable UNSET, notify-send succeeds via
#   $XDG_RUNTIME_DIR/bus; with it set to a stale path it fails harmlessly in
#   6 ms. The guard bought nothing and suppressed the toast in exactly the
#   case it was written for.
set -uo pipefail

# Overridable ONLY so tests can sandbox it. systemd passes an absolute
# ExecStart and sets no Environment=, so production always takes the default.
REPO="${TAIL_LAB_REPO:-/home/dio/Documents/apps/tail-lab}"
WHEN=$(date -u +%FT%TZ)

# WHICH job failed. Passed by the unit as `%i` from the tail-lab-alert@
# template. This is not cosmetic: the chain and the refresh have opposite
# recovery properties, so one remedy is catastrophic advice for the other.
#
# Wiring the refresh's OnFailure= at the chain handler made a refresh failure
# write "this trading day is permanently blank -- run make ingest-option-chain".
# That is false (every refresh source serves history on demand), it OVERWRITES
# a genuine unacknowledged chain marker with text indistinguishable from the
# real thing, and the marker is durable: a human acting on it after the 13:30
# UTC open claims the live session's partition with mid-session quotes and
# no-ops that evening's real sweep. A false alarm that routes a human into the
# one forbidden command is worse than no alarm.
CONTEXT="${1:-}"
case "$CONTEXT" in
  refresh)
    MARKER="$REPO/.daily-refresh-FAILED"
    FALLBACK="$HOME/.daily-refresh-FAILED"
    HEADLINE="tail-lab daily source refresh FAILED at $WHEN"
    REMEDY="./scripts/local_daily_refresh.sh"
    URGENCY="normal"
    TOAST_TITLE="tail-lab: daily refresh failed"
    TOAST_BODY="One or more bronze sources did not refresh. They serve history on demand, so a re-run recovers them."
    LOGS="$REPO/.daily-refresh.log"
    ;;
  budget)
    # GitHub's own "Included usage alerts" (90%/100% of plan allowance) are a
    # UI-only toggle -- there is no API for them, and the billing usage
    # endpoint needs a `user` scope this machine's token does not carry. This
    # context is the local substitute: `scripts/actions-budget.sh` in the
    # sibling workspace reconstructs billed minutes from run data with no
    # extra scope, and exits non-zero above 80% of the allowance.
    #
    # It exists because hitting the limit is what killed CI, deploys and the
    # daily agent for four days in September 2026, and nothing warned first.
    MARKER="$REPO/.actions-budget-FAILED"
    FALLBACK="$HOME/.actions-budget-FAILED"
    HEADLINE="tail-lab: GitHub Actions minutes are running out ($WHEN)"
    REMEDY="../scripts/actions-budget.sh"
    URGENCY="normal"
    TOAST_TITLE="GitHub Actions budget"
    TOAST_BODY="Billed Actions minutes are above 80% of the monthly allowance. CI, deploys and the daily agent all stop at 100%."
    LOGS="$REPO/.actions-budget.log"
    ;;
  chain)
    MARKER="$REPO/.chain-sweep-FAILED"
    FALLBACK="$HOME/.chain-sweep-FAILED"
    HEADLINE="tail-lab chain sweep FAILED at $WHEN"
    REMEDY="make ingest-option-chain"
    URGENCY="critical"
    TOAST_TITLE="tail-lab: chain sweep FAILED"
    TOAST_BODY="Today's option chain was NOT captured and cannot be back-filled. Run: make ingest-option-chain"
    LOGS="$REPO/.chain-sweep-manual.log $REPO/.chain-sweep-verify.log"
    ;;
  *)
    # Deliberately NOT a re-exec with "unknown" prepended: that shifted the
    # offending value out of $1, so the marker reported context='unknown'
    # and the actual typo was lost -- destroying the one diagnostic this
    # branch exists to carry.
    BAD_CONTEXT="$CONTEXT"
    CONTEXT="unknown"
    MARKER="$REPO/.chain-sweep-FAILED"
    FALLBACK="$HOME/.chain-sweep-FAILED"
    HEADLINE="tail-lab: an UNKNOWN job failed at $WHEN (context='$BAD_CONTEXT')"
    REMEDY=""
    URGENCY="critical"
    TOAST_TITLE="tail-lab: unknown job failed"
    TOAST_BODY="A tail-lab unit failed but did not identify itself. Check: journalctl --user -u 'tail-lab-*' --since -1h"
    LOGS="$REPO/.chain-sweep-manual.log $REPO/.chain-sweep-verify.log $REPO/.daily-refresh.log"
    ;;
esac

render() {
  echo "$HEADLINE"
  echo
  # Switch the PROSE on the same three-way context as everything else.
  # An earlier version branched `refresh`-vs-everything, so the `unknown`
  # case inherited the chain's paragraph: it suppressed the command but kept
  # "the option-chain sweep is unrecoverable... re-run TODAY", which a reader
  # who knows the command by heart will act on. The panic is the dangerous
  # half, not the command line.
  case "$CONTEXT" in
    refresh)
      echo "These sources all serve history on demand, so nothing is lost --"
      echo "a re-run recovers them. This is NOT the option-chain sweep, and it"
      echo "is NOT time-critical. Do not run the chain sweep because of this."
      ;;
    budget)
      echo "Billed Actions minutes are above 80% of the monthly allowance."
      echo "At 100% every workflow in EVERY repo on this account stops --"
      echo "CI, deploys, the daily agent and the chain sweep together. That"
      echo "is what happened in September 2026 and nothing warned first."
      echo "Public repos are free and do not count; quizkit and tip_app are"
      echo "still private and still bill. Per-repo breakdown:"
      ;;
    chain)
      echo "The option-chain sweep is unrecoverable if missed: no free source"
      echo "serves a retroactive chain (docs/adr/0020). Re-run TODAY, and only"
      echo "BEFORE the 13:30 UTC open -- an intraday write claims the session's"
      echo "partition and makes the post-close run a silent no-op:"
      ;;
    *)
      echo "A tail-lab unit failed but did not identify which job it was, so"
      echo "no remedy can be printed: the chain sweep's and the refresh's are"
      echo "not interchangeable, and running the chain one by mistake after"
      echo "the 13:30 UTC open destroys that session. Identify the unit first:"
      ;;
  esac
  echo
  if [ -n "$REMEDY" ]; then
    echo "    cd $REPO && $REMEDY"
  else
    echo "    journalctl --user -u 'tail-lab-*' --since -1h"
    echo
    echo "No remedy is printed because the failing job did not identify"
    echo "itself, and the two jobs' remedies are not interchangeable."
  fi
  echo
  echo "Then delete this file:  rm $1"
  for logfile in $LOGS; do
    echo
    echo "--- last 20 lines of $logfile ---"
    tail -20 "$logfile" 2>/dev/null || echo "(not found)"
  done
}

# Durable channel first, via temp+rename so a prior unacknowledged marker
# survives a failed write.
marker_ok=0
TMP="$MARKER.tmp.$$"
if render "$MARKER" > "$TMP" 2>/dev/null && [ -s "$TMP" ] && mv -f "$TMP" "$MARKER" 2>/dev/null; then
  marker_ok=1
else
  rm -f "$TMP" 2>/dev/null
  # Same temp+rename as above, for the same reason. A plain `> "$FALLBACK"`
  # opens O_TRUNC and destroys yesterday's unacknowledged fallback marker
  # before knowing today's render can finish -- measured under a write limit,
  # it left a half-written 1024-byte marker where a 39-byte unacknowledged one
  # had been. The fallback path runs precisely when the repo is unwritable,
  # i.e. when this marker is the only channel left.
  FTMP="$FALLBACK.tmp.$$"
  if render "$FALLBACK" > "$FTMP" 2>/dev/null && [ -s "$FTMP" ] && mv -f "$FTMP" "$FALLBACK" 2>/dev/null; then
    marker_ok=2
  else
    rm -f "$FTMP" 2>/dev/null
  fi
fi

# Journal, on the unit's own stdout so it is always attributed to this unit.
case "$marker_ok" in
  1) echo "$CONTEXT FAILED at $WHEN; marker: $MARKER" ;;
  2) echo "$CONTEXT FAILED at $WHEN; repo unwritable, marker: $FALLBACK" ;;
  *) echo "$CONTEXT FAILED at $WHEN; COULD NOT WRITE ANY MARKER" ;;
esac

timeout 10 notify-send -u "$URGENCY" "$TOAST_TITLE" "$TOAST_BODY" 2>/dev/null || true

# Non-zero ONLY when the durable channel is gone -- see the header.
[ "$marker_ok" = 0 ] && exit 1
exit 0
