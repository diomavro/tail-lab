"""The hypothesis memory store (``docs/adr/0015``).

Records every Put Lab backtest as a per-``(rule_hash, regime)`` node persisted
as a JSON blob on the same object storage the lakehouse uses (the ``feedback``
pattern), and answers "has this been tried, and what happened?" without
re-running anything. DuckDB is the query engine for the cross-regime rollups
(``coverage_by_regime``), reading the persisted records — no database service
to run (``docs/adr/0015``, the storage decision).

Two ideas carry the value:

- **``run_count``** — recording the same ``(rule, regime)`` again bumps a
  counter instead of forging a second independent confirmation. Two runs in
  ``crisis`` is still one regime's worth of evidence.
- **``regime_only`` != ``confirmed``** — :meth:`verdict_for_rule` aggregates a
  rule's per-regime outcomes: it "passed" a regime if it net-paid there
  (``roi_on_premium > 0``). Passing in >=2 distinct regimes is ``confirmed``;
  in exactly one is ``regime_only``; in none is ``failed``.
"""

from __future__ import annotations

import datetime as dt

import duckdb
import pandas as pd
from pydantic import BaseModel

from tail_lab.contracts.hypothesis import RuleSpec, Verdict
from tail_lab.contracts.regime import RegimeLabel
from tail_lab.lake.blob_store import BlobStore

#: Prefix every memory blob is written under, relative to the lake root:
#: ``<lake_root>/ops/memory/`` locally, ``s3://<bucket>/ops/memory/`` in prod.
_PREFIX = "ops/memory"


class RegimeOutcome(BaseModel):
    """One rule's recorded outcome in one regime — a memory node."""

    rule_hash: str
    spec: RuleSpec
    regime: RegimeLabel
    roi_on_premium: float
    n_cycles: int
    hit_rate: float
    biggest_payoff_mult: float
    run_count: int
    first_seen: dt.datetime
    last_seen: dt.datetime
    last_run_id: str

    @property
    def paid_off(self) -> bool:
        """Did the strategy net-pay in this regime (premium recovered)?"""
        return self.roi_on_premium > 0.0


class HypothesisMemory:
    """Record and recall Put Lab backtest verdicts on top of a
    :class:`~tail_lab.lake.blob_store.BlobStore`."""

    def __init__(self, blob_store: BlobStore) -> None:
        self._blobs = blob_store

    @staticmethod
    def _key(rule_hash: str, regime: RegimeLabel) -> str:
        return f"{_PREFIX}/{rule_hash}__{regime}.json"

    def record(
        self,
        spec: RuleSpec,
        regime: RegimeLabel,
        *,
        roi_on_premium: float,
        n_cycles: int,
        hit_rate: float,
        biggest_payoff_mult: float,
        run_id: str,
    ) -> RegimeOutcome:
        """Upsert this ``(rule, regime)`` outcome. A first sighting creates the
        node with ``run_count == 1``; a repeat bumps ``run_count`` and refreshes
        the metrics/``last_seen`` rather than pretending to be new evidence."""
        rule_hash = spec.rule_hash()
        now = dt.datetime.now(dt.UTC)
        existing = self.lookup(rule_hash, regime)
        outcome = RegimeOutcome(
            rule_hash=rule_hash,
            spec=spec,
            regime=regime,
            roi_on_premium=roi_on_premium,
            n_cycles=n_cycles,
            hit_rate=hit_rate,
            biggest_payoff_mult=biggest_payoff_mult,
            run_count=(existing.run_count + 1) if existing else 1,
            first_seen=existing.first_seen if existing else now,
            last_seen=now,
            last_run_id=run_id,
        )
        self._blobs.write_json(self._key(rule_hash, regime), outcome.model_dump(mode="json"))
        return outcome

    def lookup(self, rule_hash: str, regime: RegimeLabel) -> RegimeOutcome | None:
        """The stored outcome for ``(rule_hash, regime)``, or ``None`` if this
        exact pair was never tested."""
        try:
            raw = self._blobs.read_json(self._key(rule_hash, regime))
        except LookupError:
            return None
        return RegimeOutcome(**raw)

    def all_outcomes(self) -> list[RegimeOutcome]:
        """Every recorded node — negative results included; nothing is ever
        pruned from the retrieval path (``docs/adr/0015``)."""
        return [RegimeOutcome(**raw) for raw in self._blobs.list_json(_PREFIX)]

    def outcomes_for_rule(self, rule_hash: str) -> list[RegimeOutcome]:
        """This rule's outcomes across every regime it has been tried in."""
        return [o for o in self.all_outcomes() if o.rule_hash == rule_hash]

    def verdict_for_rule(self, rule_hash: str) -> Verdict:
        """Aggregate a rule's standing across regimes (see module docstring)."""
        outcomes = self.outcomes_for_rule(rule_hash)
        if not outcomes:
            return "untested"
        passing_regimes = {o.regime for o in outcomes if o.paid_off}
        if len(passing_regimes) >= 2:
            return "confirmed"
        if len(passing_regimes) == 1:
            return "regime_only"
        return "failed"

    def coverage_by_regime(self) -> pd.DataFrame:
        """DuckDB rollup over the persisted nodes: per regime, how many rules
        recorded, how many paid off, total runs, and average ROI. This is the
        query-engine half of the store (``docs/adr/0015``) — the coverage map
        the research loop reads to see which regimes are thin on evidence."""
        outcomes = self.all_outcomes()
        empty = pd.DataFrame(columns=["regime", "rules", "paid_off", "total_runs", "avg_roi"])
        if not outcomes:
            return empty
        frame = pd.DataFrame(
            [
                {
                    "regime": o.regime,
                    "paid_off": int(o.paid_off),
                    "run_count": o.run_count,
                    "roi_on_premium": o.roi_on_premium,
                }
                for o in outcomes
            ]
        )
        con = duckdb.connect()
        try:
            con.register("nodes", frame)
            return con.execute(
                """
                SELECT regime,
                       count(*)              AS rules,
                       sum(paid_off)         AS paid_off,
                       sum(run_count)        AS total_runs,
                       avg(roi_on_premium)   AS avg_roi
                FROM nodes
                GROUP BY regime
                ORDER BY regime
                """
            ).df()
        finally:
            con.close()
