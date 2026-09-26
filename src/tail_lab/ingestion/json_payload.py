"""Narrow parsed JSON from an untrusted endpoint to the shape a parser needs.

Every adapter here reads a public payload whose shape the vendor can change
without notice. Typed as ``Any``, a changed shape surfaced as whatever
``KeyError``/``TypeError``/``IndexError`` the first bad subscript happened to
raise -- which forced ``sources.first_available`` to catch *everything* just to
treat "junk payload" as a failed source, and so also swallowed real bugs. These
helpers make a wrong shape one named failure, ``ValueError``, saying which
field was wrong, so callers can catch exactly that.
"""

from __future__ import annotations

import datetime as dt
from typing import Final

__all__ = ["first_object", "json_list", "json_object", "to_float", "to_int", "utc_from_epoch"]


def json_object(value: object, what: str) -> dict[str, object]:
    """``value`` as a JSON object, or ``ValueError`` naming ``what``."""
    if not isinstance(value, dict):
        raise ValueError(f"{what} is not a JSON object (got {type(value).__name__})")
    return value


def json_list(value: object, what: str) -> list[object]:
    """``value`` as a JSON array, or ``ValueError`` naming ``what``."""
    if not isinstance(value, list):
        raise ValueError(f"{what} is not a JSON array (got {type(value).__name__})")
    return value


def first_object(value: object, what: str) -> dict[str, object]:
    """The first element of a JSON array, itself an object -- the
    ``result[0]`` idiom every chart-style payload uses."""
    items = json_list(value, what)
    if not items:
        raise ValueError(f"{what} is an empty array")
    return json_object(items[0], f"{what}[0]")


def to_float(value: object, what: str) -> float:
    """``float(value)`` for the scalar types JSON carries, else ``ValueError``.

    Strings still go through ``float`` (and raise ``ValueError`` themselves if
    unparseable), exactly as the untyped parsers accepted them; what is
    refused is a list, object or null, which ``float`` rejects with a
    ``TypeError`` -- caught before only by the catch-alls this module
    replaced. An integer too large for a float (``10**400``) raises
    ``OverflowError``, which becomes the same ``ValueError``.
    """
    if not isinstance(value, int | float | str):
        raise ValueError(f"{what} is not a number (got {type(value).__name__})")
    try:
        return float(value)
    except OverflowError as exc:
        raise ValueError(f"{what} is too large for a float") from exc


_INT64_MIN: Final = -(2**63)
_INT64_MAX: Final = 2**63 - 1


def to_int(value: object, what: str) -> int:
    """``int(value)`` under the same rules as :func:`to_float`. ``json.loads``
    accepts ``Infinity``, and ``int(inf)`` raises ``OverflowError``: that is a
    bad cell like any other, so it becomes the same ``ValueError``."""
    if not isinstance(value, int | float | str):
        raise ValueError(f"{what} is not a number (got {type(value).__name__})")
    try:
        number = int(value)
    except OverflowError as exc:
        raise ValueError(f"{what} is not a finite number ({value!r})") from exc
    # json.loads returns arbitrary-precision ints; pandas and the lake store
    # int64, and 10**400 raised OverflowError there instead, past every
    # caller's ValueError catch. Out of int64 range is a bad cell too.
    if not _INT64_MIN <= number <= _INT64_MAX:
        raise ValueError(f"{what} is outside the int64 range ({value!r})")
    return number


def utc_from_epoch(seconds: int, what: str) -> dt.datetime:
    """``datetime.fromtimestamp(seconds, UTC)``, with every way a junk epoch
    fails -- ``OverflowError``, ``OSError`` (EOVERFLOW on this platform) or
    ``ValueError`` (year out of range) -- reported as the one ``ValueError``."""
    try:
        return dt.datetime.fromtimestamp(seconds, tz=dt.UTC)
    except (OverflowError, OSError, ValueError) as exc:
        raise ValueError(f"{what} {seconds!r} is not a usable epoch timestamp") from exc
