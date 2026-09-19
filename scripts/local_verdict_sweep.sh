#!/usr/bin/env bash
# Local stand-in for .github/workflows/daily-verdict-sweep.yml, which cannot
# run while GitHub Actions is blocked on billing (HUMAN_TODO.md).
#
# Deterministic HTTP sweep against the live app -- it records one (rule, regime)
# verdict per (symbol, moneyness) cell into the hypothesis memory
# (docs/adr/0015). Unlike the chain sweep this one IS catch-up-able: verdicts
# are recomputed from data already in the lake, so a missed day costs a day of
# run_count, not a day of data.
#
# The token comes from .env (TAIL_LAB_FEEDBACK_TOKEN), the same secret the
# workflow reads; without it the memory routes 404 by design.
set -uo pipefail
BASE="${1:-https://tail-lab.fly.dev}"
REPO=/home/dio/Documents/apps/tail-lab
cd "$REPO" || exit 1

TOKEN=$(grep -E '^TAIL_LAB_FEEDBACK_TOKEN=' .env 2>/dev/null | cut -d= -f2-)
if [ -z "$TOKEN" ]; then
  echo "no TAIL_LAB_FEEDBACK_TOKEN in .env -- the memory routes will 404" >&2
  exit 1
fi

symbols=$(curl -s "$BASE/api/putlab/universe" \
  | python3 -c "import sys,json;print(' '.join(m['symbol'].lower() for m in json.load(sys.stdin)))")
if [ -z "$symbols" ]; then
  echo "could not fetch the universe from $BASE" >&2; exit 1
fi

tmp=$(mktemp); trap 'rm -f "$tmp"' EXIT
n=0; ok=0
for s in $symbols; do
  for m in 5 10; do
    code=$(curl -s -o "$tmp" -w "%{http_code}" -X POST \
      -H "Authorization: Bearer $TOKEN" \
      "$BASE/api/putlab/memory/record?asset=$s&moneyness_pct=$m&tenor_weeks=4&years=4")
    n=$((n + 1))
    if [ "$code" = "200" ]; then
      ok=$((ok + 1))
      v=$(python3 -c "import json;print(json.load(open('$tmp'))['verdict'])" 2>/dev/null || echo "?")
      echo "recorded $s ${m}% OOM / 4w -> $v"
    else
      echo "skip $s ${m}% OOM / 4w -> HTTP $code"
    fi
  done
done
echo "verdict sweep done: recorded $ok of $n (rule, regime) verdicts"
[ "$ok" -gt 0 ]
