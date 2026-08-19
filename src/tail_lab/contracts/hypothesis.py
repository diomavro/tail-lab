"""The canonical strategy spec + stable rule hash for the hypothesis memory
(``docs/adr/0015``).

A LEAF module: imports nothing else from ``tail_lab``. Both the memory layer
(which keys verdicts by ``rule_hash``) and any caller that wants to ask "has
this been tested?" share one canonical form here, so two phrasings of the
same Put Lab strategy — ``5`` vs ``5.0`` percent, ``4.0`` vs ``4`` weeks —
hash to the same id instead of masquerading as independent experiments.

Scope note (``docs/adr/0015``): a "rule" here is a Put Lab **backtest
parameter set**, not a general trading rule with entry/exit trees. tail-lab
never trades and has no strategy-generation loop; the unit worth remembering
is "which OOM-put configuration was tried, and how did it do, in which
regime." The richer rule grammar in Dio's original spec is deliberately out
of scope until there is something that produces those richer rules.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Set as AbstractSet
from typing import Literal

from pydantic import BaseModel, field_validator

from tail_lab.contracts.regime import RegimeLabel

#: A rule's standing across the regimes it has been tested in. ``regime_only``
#: is deliberately distinct from ``confirmed`` (``docs/adr/0015``): a strategy
#: that only paid off in one regime is not confirmed, and collapsing the two
#: is exactly how a backtest loop fools itself.
Verdict = Literal["confirmed", "regime_only", "failed", "untested"]


class RuleSpec(BaseModel):
    """A canonical Put Lab strategy: buy ``moneyness_pct``% OOM puts on
    ``asset``, ``tenor_weeks`` to expiry, rolled over ``lookback_years``.

    Fields are canonicalized on construction (asset lower-cased; the numeric
    knobs rounded to the grid the UI actually exposes) so equivalent specs
    collide under :meth:`rule_hash`.
    """

    model_config = {"frozen": True}

    asset: str
    moneyness_pct: float
    tenor_weeks: float
    lookback_years: int

    @field_validator("asset")
    @classmethod
    def _canon_asset(cls, v: str) -> str:
        return v.strip().lower()

    @field_validator("moneyness_pct", "tenor_weeks")
    @classmethod
    def _round_one_dp(cls, v: float) -> float:
        return round(float(v), 1)

    def rule_hash(self) -> str:
        """A stable, human-readable id for this rule — ``h-<8 hex>`` of the
        canonical spec. Deterministic across processes (no salting), and
        formatting-independent because the fields are already canonical."""
        canonical = json.dumps(self.model_dump(), sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return f"h-{digest[:8]}"


def verdict_from_passing_regimes(passing: AbstractSet[RegimeLabel]) -> Verdict:
    """Classify a rule from the set of distinct regimes it paid off in
    (``docs/adr/0015``): >=2 regimes is ``confirmed``, exactly one is
    ``regime_only``, none is ``failed``.

    Shared by the persistent memory (aggregating stored per-regime outcomes)
    and the Put Lab's live regime breakdown (the same classification over a
    single backtest's cycles), so the two can never disagree on where the
    ``regime_only`` / ``confirmed`` line falls. ``untested`` is not returned
    here — that is the distinct "no record at all" case the caller handles."""
    if len(passing) >= 2:
        return "confirmed"
    if len(passing) == 1:
        return "regime_only"
    return "failed"
