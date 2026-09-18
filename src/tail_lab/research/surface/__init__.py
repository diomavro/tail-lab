"""The Surface -- the strike ladder and the tail behind it (``docs/adr/0021``).

Paretan tail analytics after Taleb, Yarckin, Mann, Delic & Spitznagel, *Tail
Option Pricing Under Power Laws* (arXiv 1908.02347v3, rev. March 2023). The
paper prices a deep out-of-the-money option **relative to a market-quoted anchor
strike**, parameterised by the tail index ``alpha`` alone -- no mean, no
volatility, and no requirement that variance be finite.

Nothing here implements ``research.option_pricer.OptionPricer``, deliberately:
that ABC prices a contract from state ``(spot, strike, t_years, r, sigma, q)``,
whereas this heuristic needs ``(anchor_strike, anchor_price, alpha, spot)`` and
has no ``sigma``, no rate and no clock. See ``docs/adr/0026``.
"""
