"""RFC 8785 JSON Canonicalization Scheme for the hash chain (TRD §4.3).

The same bytes must come out of every implementation that verifies an
export, so this is written to the RFC rather than to ``json.dumps``:
object keys sort by UTF-16 code units, numbers use ES6 ``Number::toString``,
and strings use the short escapes plus lowercase ``\\u00xx`` for other
control characters.
"""

import json
import math
from collections.abc import Mapping

from orchestrator.errors import TraceError

MAX_SAFE_INT = 2**53

_SHORT_ESCAPES = {
    '"': '\\"',
    "\\": "\\\\",
    "\b": "\\b",
    "\f": "\\f",
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
}


def canonical_json(value: object) -> str:
    out: list[str] = []
    _emit(value, out)
    return "".join(out)


def canonical_bytes(value: object) -> bytes:
    try:
        return canonical_json(value).encode("utf-8")
    except UnicodeEncodeError as exc:
        raise TraceError(f"payload contains a lone surrogate: {exc}") from exc


def parse_canonical(text: str) -> object:
    """Parse canonical JSON so that re-canonicalising reproduces the same text.

    JSON numbers are doubles: an integer literal beyond 2^53 denotes the nearest
    double, so it is read back as a float rather than an exact Python int.
    """

    def _int(token: str) -> int | float:
        value = int(token)
        return value if abs(value) <= MAX_SAFE_INT else float(token)

    def _reject(token: str) -> object:
        raise TraceError(f"{token} is not a JSON number")

    try:
        return json.loads(text, parse_int=_int, parse_constant=_reject)
    except ValueError as exc:
        raise TraceError(f"text is not JSON: {exc}") from exc


def _emit(value: object, out: list[str]) -> None:
    if value is None:
        out.append("null")
    elif value is True:
        out.append("true")
    elif value is False:
        out.append("false")
    elif isinstance(value, int):
        out.append(_integer(value))
    elif isinstance(value, float):
        out.append(es6_number(value))
    elif isinstance(value, str):
        out.append(_quote(value))
    elif isinstance(value, Mapping):
        _emit_object(value, out)
    elif isinstance(value, list | tuple):
        out.append("[")
        for index, item in enumerate(value):
            if index:
                out.append(",")
            _emit(item, out)
        out.append("]")
    else:
        raise TraceError(f"value of type {type(value).__name__} is not JSON")


def _integer(value: int) -> str:
    """JSON numbers are doubles: an integer is accepted only when a double holds it exactly."""
    if abs(value) <= MAX_SAFE_INT:
        return str(value)
    as_float = float(value)
    if int(as_float) != value:
        raise TraceError(f"integer {value} is not exactly representable as a double")
    return es6_number(as_float)


def _emit_object(value: Mapping[object, object], out: list[str]) -> None:
    items: list[tuple[str, object]] = []
    for key, item in value.items():
        if not isinstance(key, str):
            raise TraceError(f"object key {key!r} is not a string")
        items.append((key, item))
    items.sort(key=lambda kv: kv[0].encode("utf-16-be"))
    out.append("{")
    for index, (key, item) in enumerate(items):
        if index:
            out.append(",")
        out.append(_quote(key))
        out.append(":")
        _emit(item, out)
    out.append("}")


def _quote(text: str) -> str:
    parts = ['"']
    for ch in text:
        short = _SHORT_ESCAPES.get(ch)
        if short is not None:
            parts.append(short)
        elif ord(ch) < 0x20:
            parts.append(f"\\u{ord(ch):04x}")
        else:
            parts.append(ch)
    parts.append('"')
    return "".join(parts)


def es6_number(value: float) -> str:
    """ES6 ``Number::toString`` (ECMA-262 §7.1.12.1) for a finite double."""
    if math.isnan(value) or math.isinf(value):
        raise TraceError("NaN and infinities are not JSON numbers")
    if value == 0:
        return "0"
    sign = "-" if value < 0 else ""
    text = repr(abs(value))  # shortest round-trip digits, the same digits ES6 picks
    mantissa, _, exponent_text = text.partition("e")
    exponent = int(exponent_text) if exponent_text else 0
    int_part, _, frac_part = mantissa.partition(".")
    digits = (int_part + frac_part).lstrip("0")
    e10 = exponent - len(frac_part)
    stripped = digits.rstrip("0")
    e10 += len(digits) - len(stripped)
    s = stripped
    k = len(s)
    n = k + e10  # value = s * 10^(n-k)
    if k <= n <= 21:
        return sign + s + "0" * (n - k)
    if 0 < n <= 21:
        return sign + s[:n] + "." + s[n:]
    if -6 < n <= 0:
        return sign + "0." + "0" * (-n) + s
    e = n - 1
    exp = ("+" if e >= 0 else "-") + str(abs(e))
    if k == 1:
        return sign + s + "e" + exp
    return sign + s[0] + "." + s[1:] + "e" + exp
