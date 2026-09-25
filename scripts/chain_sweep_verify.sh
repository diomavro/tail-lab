#!/usr/bin/env bash
# Dead-man's switch for the chain sweep. Reads the lake back and asks the two
# questions nothing else on this machine asks.
#
# Why `OnFailure=` cannot replace it: that fires only when the sweep RUNS and
# FAILS. Three ways to lose a session produce no failure at all --
#   1. the timer never runs (disabled, unit deleted, daemon-reload drops it,
#      machine off past the recovery window);
#   2. the sweep "succeeds" on a session already captured -- `write_bronze`
#      no-ops on an existing ingest_date and returns the same path string it
#      returns on a real write. Measured 2026-09-19: two runs reported
#      "committed 14091 rows" and "committed 19525 rows"; the Delta log gained
#      no version and neither wrote a byte;
#   3. Cboe serves an off-session chain for some symbols and they are filed
#      under a session they never traded in. Already in the lake:
#      ingest_date=2026-09-08 holds 412 ARKK rows stamped 2026-09-04, so
#      ARKK's 2026-09-08 session does not exist and never will.
#
# TWO CHECKS, deliberately split by whether they need the market to be shut:
#
#   A. FORWARD -- is the session Cboe is serving right now in the lake, with
#      every symbol? Only valid before the US open: once the session is live,
#      Cboe serves an in-progress session the lake correctly lacks, and
#      checking then would alert on healthy state.
#   B. BACKWARD -- are the most recent partitions symbol-complete? Reads only
#      the lake, so it is valid at ANY hour.
#
# The split is what stops a late resume disarming the monitor. An earlier
# version ran only check A behind an hour guard and exited 0 when it skipped,
# so systemd recorded success, Persistent= did not re-fire, and that day was
# never verified by anything -- on exactly the days the laptop was opened late,
# which is the same condition that causes the sweep to be missed. It also only
# ever looked at the newest session, so a gap one day back was invisible
# forever: it reported "OK: 2026-09-18 fully captured" while the corrupt
# 2026-09-08 partition sat eight sessions behind it.
#
# Cboe is the source of truth for "what is the last session", which avoids
# inventing a US market-holiday calendar -- on a holiday the CDN keeps serving
# the prior session, which is exactly the right answer.
set -uo pipefail

REPO=/home/dio/Documents/apps/tail-lab
LOG="$REPO/.chain-sweep-verify.log"
#: How many recent partitions check B inspects.
LOOKBACK="${CHAIN_VERIFY_LOOKBACK:-10}"

{
  echo "=== chain sweep verify $(date -u +%FT%TZ) ==="
  cd "$REPO" || { echo "FATAL: repo not found"; exit 1; }

  # US opens 13:30 UTC; stop an hour early for safety. Bash's `[` parses
  # base-10, so a leading-zero hour like 08 is NOT an octal trap here (it
  # would be under `(( ))`).
  # WAIT FOR THE NETWORK, then defer rather than fail if it never arrives.
  #
  # Persistent=true means this almost always runs seconds after a resume,
  # when DNS is not up yet. Twice in two days that produced a RED verifier on
  # perfectly healthy data: 2026-09-22 the Cboe fetch died with "Temporary
  # failure in name resolution", and 2026-09-23 the S3 lake read died the
  # same way after the first fix guarded only the Cboe half. Each fired the
  # chain-loss alert, whose marker tells the operator to run
  # `make ingest-option-chain` -- which after the 13:30 UTC open destroyed
  # that session (until the 2026-09-25 settle-time guard). Two false alarms in two days is how the one channel that
  # must stay credible stops being read.
  #
  # A verifier that cannot reach anything has not found a gap. It has found
  # no network. Those are different answers and only one of them is an alert.
  # A deferred day is cheap: the backward check covers the last ten sessions,
  # so tomorrow's run inspects today's anyway.
  for _ in $(seq 1 30); do
    getent hosts fly.storage.tigris.dev >/dev/null 2>&1 && break
    sleep 2
  done
  if ! getent hosts fly.storage.tigris.dev >/dev/null 2>&1; then
    echo "DEFERRED: no DNS for the lake after 60s (resumed without network?)."
    echo "          Not a gap -- nothing was checked. Tomorrow's run covers today."
    echo "=== exit=0 $(date -u +%FT%TZ) ==="
    exit 0
  fi

  hour=$(date -u +%H)
  forward=1
  if [ "$hour" -ge 12 ]; then
    forward=0
    echo "NOTE: ${hour}:00 UTC is past the pre-open cutoff; running the backward check only"
  fi

  CHAIN_VERIFY_FORWARD="$forward" CHAIN_VERIFY_LOOKBACK="$LOOKBACK" \
    env -u PYTHONPATH .venv/bin/python - <<'PY'
import datetime as dt
import os
import sys

from tail_lab.config import get_lake_store
from tail_lab.contracts.option_chain import DEFAULT_SNAPSHOT_SYMBOLS
from tail_lab.ingestion.option_chain import DATASET, fetch_chain_raw, parse_cboe_chain

EXPECTED = {s.lower() for s in DEFAULT_SNAPSHOT_SYMBOLS}


def _acknowledged() -> set[tuple[str, str]]:
    """Gaps recorded as permanent in `.chain-known-gaps`.

    A gap that no action can close must not alert daily: bronze is immutable
    and nobody sells a retroactive chain, so a symbol missing from a written
    session is missing forever. Alerting about it every morning is how a
    channel stops being read -- and this one has to stay believed.
    """
    ack: set[tuple[str, str]] = set()
    try:
        with open(".chain-known-gaps") as handle:
            for line in handle:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                session, _, symbol = line.partition("\t")
                if symbol:
                    ack.add((session.strip(), symbol.strip().lower()))
    except (OSError, UnicodeDecodeError):
        # Degrade to 'nothing acknowledged', which over-reports rather than
        # under-reports. A monitor that dies on a malformed config file is
        # worse than one that alerts about a gap you already knew of.
        return set()
    return ack


ACKNOWLEDGED = _acknowledged()
store = get_lake_store()
failures: list[str] = []


def symbols_at(frame, session):
    """Symbols whose freshest quotes in this partition ARE this session.

    Per-symbol, because a partition can exist yet be missing names: the
    session is elected by majority, so laggards hide inside a partition that
    looks healthy in aggregate.
    """
    return {
        str(name).lower()
        for name, rows in frame.groupby("underlying")
        if rows["quote_date"].max().date() == session
    }


# ---- A. forward: is the session Cboe is serving actually captured? --------
if os.environ["CHAIN_VERIFY_FORWARD"] == "1":
    # The fetch is wrapped because a monitor that DIES on a transient network
    # fault is worse than no monitor. Measured 2026-09-22 10:00 CEST, on this
    # machine's first scheduled verify: a DNS failure moments after resume
    # ("Temporary failure in name resolution" for cdn.cboe.com) raised out of
    # the script, so check B -- which needs no network and is the
    # authoritative question -- never ran at all, and the non-zero exit fired
    # the CHAIN alert. The marker then told the operator today's chain was
    # permanently blank and to run `make ingest-option-chain`, which after the
    # 13:30 UTC open claimed the live session's partition (refused since the
    # 2026-09-25 settle-time guard). The data was
    # perfectly healthy. A false alarm that routes a human into the one
    # forbidden command is the worst failure this script can have.
    #
    # Unreachable Cboe is NOT evidence of a missing session. Say so, and let
    # check B decide.
    try:
        df, _ = parse_cboe_chain(fetch_chain_raw("spy"))
    except Exception as exc:  # noqa: BLE001 - any transport fault degrades the same way
        print(f"NOTE: could not reach Cboe for the forward check ({type(exc).__name__}).")
        print("      This is not evidence of a gap; the lake check below still runs.")
        df = None
    if df is None:
        pass
    elif df.empty:
        failures.append("Cboe returned no parseable SPY quotes; cannot establish the session")
    else:
        session = df["quote_date"].max().date()
        print(f"Cboe's current session: {session}")
        try:
            have = store.read_bronze_as_of(DATASET, session)
        except LookupError:
            failures.append(f"bronze has NO partition at or before {session}")
        else:
            landed = have["quote_date"].max().date()
            if landed != session:
                failures.append(
                    f"newest captured session is {landed} but Cboe is serving {session}"
                    f" -- {session} was NOT captured"
                )
            else:
                missing = sorted(EXPECTED - symbols_at(have, session))
                print(f"symbols at {session}: {len(EXPECTED) - len(missing)}/{len(EXPECTED)}")
                if missing:
                    failures.append(f"no {session} quotes for: {', '.join(missing)}")

# ---- B. backward: are the recent partitions symbol-complete? --------------
# Lake-only, so this runs at ANY hour -- it is what keeps a late resume from
# leaving a day unverified, and what would have caught the ARKK gap.
#
# Walks back over calendar days with the store's own as-of resolution rather
# than enumerating Delta partitions directly: as-of returns the latest
# partition on or before a date, so stepping backwards and collecting the
# distinct sessions it lands on enumerates the recent partitions through the
# public API, with no private attributes and no second Delta client.
lookback = int(os.environ["CHAIN_VERIFY_LOOKBACK"])
seen: set[dt.date] = set()
probe = dt.date.today()
newest_session: dt.date | None = None
while len(seen) < lookback:
    # Resolve the PARTITION (ingest_date) and step from that, not from the
    # session the rows carry. An earlier version stepped by
    # `session - 1 day`, which is only the same thing while every partition's
    # ingest_date equals its max quote_date. Give one partition a lagging
    # session and the next probe jumps BEHIND every partition in between, so
    # they are never inspected: measured on a synthetic lake of six
    # partitions -- two of them incomplete -- it examined exactly one and
    # reported everything healthy.
    try:
        snapshot_id = store.bronze_snapshot_id(DATASET, probe)
    except LookupError:
        break
    except OSError as exc:
        # A transport fault mid-walk is the same class as the DNS gate above:
        # it means we could not look, not that something is missing.
        print(f"NOTE: lake unreachable partway through the backward check ({exc.__class__.__name__}).")
        print("      Stopping the walk; this is not evidence of a gap.")
        break
    resolved = dt.date.fromisoformat(snapshot_id.split("@", 1)[1].split("#", 1)[0])
    if resolved in seen:
        break
    seen.add(resolved)
    frame = store.read_bronze_as_of(DATASET, resolved)
    session = frame["quote_date"].max().date()
    if newest_session is None:
        newest_session = session

    missing = sorted(EXPECTED - symbols_at(frame, session))
    # Never let an acknowledgement silence the NEWEST session: while Cboe is
    # still serving it a re-run can still capture it, so that gap is not
    # permanent and the file's own header forbids acknowledging it. Nothing
    # enforced that before.
    ackable = session != newest_session
    unacked = [
        m for m in missing if not (ackable and (session.isoformat(), m) in ACKNOWLEDGED)
    ]
    mark = "OK " if not missing else ("ack" if not unacked else "GAP")
    note = f"  ({len(missing) - len(unacked)} acknowledged)" if missing and not unacked else ""
    drift = f"  [ingest_date={resolved}]" if resolved != session else ""
    print(f"  {mark} {session}: {len(EXPECTED) - len(missing)}/{len(EXPECTED)}{note}{drift}")
    if unacked:
        failures.append(f"{session} is missing: {', '.join(unacked)}")
    probe = resolved - dt.timedelta(days=1)

if failures:
    print()
    for line in failures:
        print(f"FAIL: {line}")
    print("Bronze is immutable, so a partition already written cannot be completed.")
    print("For a session still being served by Cboe: make ingest-option-chain")
    sys.exit(1)
print("OK: every checked session is complete")
PY
  rc=$?
  echo "=== exit=$rc $(date -u +%FT%TZ) ==="
  exit $rc
} >> "$LOG" 2>&1
