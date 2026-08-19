"""Structured application logging (``docs/STANDARDS.md`` §f, ``docs/adr/0016``).

Dio steers the platform asynchronously, so every automated action must leave a
detailed, reviewable record. This module is the *one* logging configuration
the composition roots call (``api/main.py`` today, CLI entry points later) plus
a small helper that emits one JSON-friendly ``key=value`` line per event, so a
backtest / mart build / ingestion run records its inputs and identity in a
grep-able, machine-parsable form — never a bare ``print`` and never a silent
success path.
"""

from __future__ import annotations

import logging
from typing import Any

#: Guard so repeated composition-root imports don't stack handlers.
_CONFIGURED = False


def configure_logging(level: int = logging.INFO) -> None:
    """Configure application logging once, at the composition root.

    Uses :func:`logging.basicConfig` (root handler + format); ``tail_lab.*``
    loggers propagate to it, so there is a single place logs are formatted and
    a single stream (stdout, which Fly captures) they land on. Idempotent."""
    global _CONFIGURED
    if _CONFIGURED:
        return
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logging.getLogger("tail_lab").setLevel(level)
    _CONFIGURED = True


def _fmt(value: Any) -> str:
    """Render one field value: compact floats, and quote anything with spaces
    so ``key=value`` stays unambiguously parseable."""
    if isinstance(value, bool):
        rendered = "true" if value else "false"
    elif isinstance(value, float):
        rendered = f"{value:.6g}"
    else:
        rendered = str(value)
    return f'"{rendered}"' if (" " in rendered or "=" in rendered) else rendered


def log_event(logger: logging.Logger, event: str, **fields: Any) -> None:
    """Emit one structured line: ``event=<event> k=v k=v ...`` at INFO.

    Field order is preserved (call with the most identifying fields first).
    Values that are ``None`` are dropped, so an absent id doesn't read as the
    literal string "None"."""
    parts = " ".join(f"{k}={_fmt(v)}" for k, v in fields.items() if v is not None)
    logger.info("event=%s %s", event, parts)
