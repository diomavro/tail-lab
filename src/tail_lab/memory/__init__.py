"""The hypothesis memory (``docs/adr/0015``).

A persistence layer, peer to ``feedback`` in the import-linter stack: it
imports only ``lake`` (blob persistence) and ``contracts`` (the rule spec +
regime label). It records every Put Lab backtest as a verdict keyed by
``(rule_hash, regime)`` so repeats are cheap to recognize and a strategy that
only paid off in one market regime is flagged ``regime_only``, never
``confirmed``. Scoped to tail-lab's wall: research/backtest verdicts only, no
live-fills or broker state (tail-lab never trades).
"""
