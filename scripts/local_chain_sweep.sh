#!/usr/bin/env bash
# Local stand-in for .github/workflows/daily-chain-snapshot.yml.
#
# Why this exists: GitHub Actions is blocked on billing (HUMAN_TODO.md), and
# the chain sweep is the one job that cannot be caught up later -- no free
# source serves a retroactive option chain, so a weekday not swept is a
# weekday permanently blank (docs/adr/0020).
#
# Driven by cron at 21:35 UTC on weekdays, which is the same instant the
# workflow's own cron uses (90 min after the 16:00 ET close in EDT, 30 in EST).
# CRON_TZ=UTC in the crontab keeps that fixed across DST on both sides.
#
# Safe to run repeatedly: bronze is immutable, so a second write for the same
# ingest_date is a no-op that preserves the first. Running it EARLY cannot
# claim the day's partition any more: since 2026-09-25 plan_session_write
# refuses quotes that have not settled (before 16:30 ET), skipping only when
# the lake confirms the previous session and failing red otherwise. An early run is
# still useless -- keep this after the close.
#
# Exit code mirrors scripts/chain_snapshot.py's contract: non-zero whenever a
# session may be lost; 0 for a capture, a re-run no-op, or an unsettled-session
# skip whose previous session is confirmed in the lake.
set -uo pipefail
REPO=/home/dio/Documents/apps/tail-lab
LOG="$REPO/.chain-sweep-manual.log"

{
  echo "=== local chain sweep $(date -u +%FT%TZ) ==="
  cd "$REPO" || { echo "FATAL: repo not found"; exit 1; }
  make ingest-option-chain
  rc=$?
  echo "=== exit=$rc $(date -u +%FT%TZ) ==="
  exit $rc
} >> "$LOG" 2>&1
