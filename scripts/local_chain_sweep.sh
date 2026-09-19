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
# ingest_date is a no-op that preserves the first. That also means running it
# EARLY is not safe -- an intraday write claims the day's partition and the
# post-close run then silently does nothing. Do not move this earlier.
#
# Exit code mirrors the workflow's contract: non-zero if no quotes landed.
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
