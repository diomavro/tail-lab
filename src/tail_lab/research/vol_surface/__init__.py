"""The implied-volatility surface in log-forward moneyness (``docs/adr/0036``).

``coords`` turns quotes into the surface's coordinates (forward, ``k``, ``w``),
``svi`` fits and checks one expiry's smile, and ``read`` assembles a session's
slices from the forward-collected Cboe chain.
"""
