"""Model-priced backtests (``docs/END_STATE.md`` §1.2/§1.5, ``docs/adr/0004``).

The Put Lab engine: roll a soon-expiry out-of-the-money put strategy over a
real underlying price path, pricing each premium with a model (Black-Scholes
today) using trailing realized volatility as the IV proxy — no real
historical option quotes exist for free, so every premium here is a model
price, carried with that caveat everywhere it surfaces.

Split mirrors the rest of ``research``: the roll math is a pure function of
price/vol series (``put_roll.run_put_roll``), and the lake read (bronze
as-of a simulation date) is the orchestration step
(``put_roll.compute_put_backtest``).
"""
