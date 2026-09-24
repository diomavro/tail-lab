#!/usr/bin/env bash
# Local substitute for GitHub's "Included usage alerts".
#
# GitHub's own 75/90/100% alerts are a UI-only toggle: there is no budgets
# API (the endpoint 404s at every API version), and the billing usage
# endpoint needs a `user` OAuth scope this machine's token does not carry.
# So the alert that would have prevented September 2026's four-day outage
# cannot be set from here at all. This is the closest thing that can.
#
# `../scripts/actions-budget.sh` reconstructs billed minutes from run data
# using only the `repo` scope, applies GitHub's real rule (billed per job,
# each job rounded UP to the whole minute), and exits non-zero above 80% of
# the allowance. Public repos are free and are excluded from the billed
# total -- as of 2026-09-23 tail-lab is public, which moved 2,037 min/month
# off the quota; quizkit and tip_app are still private and still bill.
#
# Exit code is the budget script's, so systemd's OnFailure= fires the alert.
set -uo pipefail

REPO="${TAIL_LAB_REPO:-/home/dio/Documents/apps/tail-lab}"
BUDGET_SCRIPT="$(dirname "$REPO")/scripts/actions-budget.sh"
LOG="$REPO/.actions-budget.log"

# Keep one rolled generation; this runs weekly and the report is ~20 lines.
if [ -f "$LOG" ] && [ "$(wc -c <"$LOG" 2>/dev/null || echo 0)" -gt 500000 ]; then
  mv -f "$LOG" "$LOG.1" 2>/dev/null || true
fi

{
  echo "=== actions budget check $(date -u +%FT%TZ) ==="
  if [ ! -x "$BUDGET_SCRIPT" ]; then
    echo "FATAL: $BUDGET_SCRIPT missing or not executable"
    exit 1
  fi
  "$BUDGET_SCRIPT"
  rc=$?
  echo "=== exit=$rc $(date -u +%FT%TZ) ==="
  exit $rc
} >> "$LOG" 2>&1
