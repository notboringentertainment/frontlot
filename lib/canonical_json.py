"""RFC 8785 (JSON Canonicalization Scheme) serialization for record hashing.

Rules implemented:
- Object keys sorted by UTF-16 code units (after NFC normalization).
- All strings (keys and values) NFC-normalized, escaped per JCS:
  only ``"``, ``\\`` and control characters < 0x20 are escaped
  (``\\b \\t \\n \\f \\r`` short forms, otherwise ``\\u00xx`` lowercase hex).
- Numbers per ES6 ``Number.prototype.toString``: integral values in
  [-(2**53-1), 2**53-1] print as plain integers (so ``1.0`` -> ``1``);
  other finite floats use the shortest round-trip digits with ES6 decimal /
  exponent placement (``1e21``, ``1e-7``). NaN and +/-Infinity are rejected.
- No whitespace.

Limits: Python ``int`` values outside the double-safe range are serialized
as plain digits (JCS would round them through IEEE 754); the shortest-digit
selection relies on Python's ``repr``, which matches ES6 for IEEE doubles.
Tuples are treated as arrays. Other types raise ``TypeError``.
"""

from __future__ import annotations

import hashlib
import math
import unicodedata
from decimal import Decimal
from typing import Any

_MAX_SAFE = 2**53 - 1

_SHORT_ESCAPES = {
    "\b": "\\b",
    "\t": "\\t",
    "\n": "\\n",
    "\f": "\\f",
    "\r": "\\r",
    '"': '\\"',
    "\\": "\\\\",
}


def _nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def _escape_string(s: str) -> str:
    out = ['"']
    for ch in s:
        esc = _SHORT_ESCAPES.get(ch)
        if esc is not None:
            out.append(esc)
        elif ord(ch) < 0x20:
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def _es6_number(f: float) -> str:
    if math.isnan(f) or math.isinf(f):
        raise ValueError("NaN and Infinity are not representable in canonical JSON")
    if f == 0:
        return "0"  # covers -0.0 per JCS
    if f.is_integer() and abs(f) <= _MAX_SAFE:
        return str(int(f))
    negative = f < 0
    d = Decimal(repr(abs(f)))
    _sign, digits, exp = d.as_tuple()
    s = "".join(str(x) for x in digits).lstrip("0") or "0"
    # normalize: strip trailing zeros, adjust exponent
    stripped = s.rstrip("0")
    exp += len(s) - len(stripped)
    s = stripped
    k = len(s)
    n = exp + k  # value = 0.s * 10^n
    if k <= n <= 21:
        body = s + "0" * (n - k)
    elif 0 < n <= 21:
        body = s[:n] + "." + s[n:]
    elif -6 < n <= 0:
        body = "0." + "0" * (-n) + s
    else:
        e = n - 1
        mant = s if k == 1 else s[0] + "." + s[1:]
        body = f"{mant}e{'+' if e >= 0 else '-'}{abs(e)}"
    return ("-" if negative else "") + body


def _serialize(obj: Any, out: list[str]) -> None:
    if obj is None:
        out.append("null")
    elif obj is True:
        out.append("true")
    elif obj is False:
        out.append("false")
    elif isinstance(obj, int):
        out.append(str(obj))
    elif isinstance(obj, float):
        out.append(_es6_number(obj))
    elif isinstance(obj, str):
        out.append(_escape_string(_nfc(obj)))
    elif isinstance(obj, (list, tuple)):
        out.append("[")
        for i, item in enumerate(obj):
            if i:
                out.append(",")
            _serialize(item, out)
        out.append("]")
    elif isinstance(obj, dict):
        items = []
        for key, value in obj.items():
            if not isinstance(key, str):
                raise TypeError(f"object keys must be str, got {type(key).__name__}")
            items.append((_nfc(key), value))
        items.sort(key=lambda kv: kv[0].encode("utf-16-be"))
        out.append("{")
        for i, (key, value) in enumerate(items):
            if i:
                out.append(",")
            out.append(_escape_string(key))
            out.append(":")
            _serialize(value, out)
        out.append("}")
    else:
        raise TypeError(f"type {type(obj).__name__} is not JSON-canonicalizable")


def canonical_bytes(obj: Any) -> bytes:
    """Return the RFC 8785 canonical UTF-8 encoding of ``obj``."""
    out: list[str] = []
    _serialize(obj, out)
    return "".join(out).encode("utf-8")


def record_sha256(obj: Any) -> str:
    """Hex SHA-256 of the canonical encoding of ``obj``."""
    return hashlib.sha256(canonical_bytes(obj)).hexdigest()
