"""Line parser. Returns (key, value) pairs; empty list means 'not numeric'.

Supported: CSV/space/tab numbers (CH1..), key=value / key:value pairs, flat or
nested JSON objects with numeric fields. Non-finite values become NaN gaps as
long as the line has at least one finite value.
"""
from __future__ import annotations

import json
import math
import re

_NUM = r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?"
_KV = re.compile(rf"(?<![\w.\-/])([A-Za-z_][\w.\-/]*)\s*[=:]\s*({_NUM})")
_SPLIT = re.compile(r"[,;\t ]+")
NAN = float("nan")


def _finite_or_nan(x: float) -> float:
    return x if math.isfinite(x) else NAN


def _flatten(obj: dict, prefix: str, out: list) -> None:
    for k, v in obj.items():
        key = f"{prefix}{k}"
        if isinstance(v, bool):
            continue
        if isinstance(v, (int, float)):
            try:
                out.append((key, _finite_or_nan(float(v))))
            except OverflowError:
                out.append((key, NAN))
        elif isinstance(v, dict):
            _flatten(v, key + ".", out)


def _dedupe(pairs: list[tuple[str, float]]) -> list[tuple[str, float]]:
    d: dict[str, float] = {}
    for k, v in pairs:
        d[k] = v  # last value wins, first position kept
    return list(d.items())


def _any_finite(pairs) -> bool:
    return any(math.isfinite(v) for _, v in pairs)


def parse_line(text: str) -> list[tuple[str, float]]:
    s = text.strip()
    if not s:
        return []
    if s[0] == "{" and s[-1] == "}":
        try:
            obj = json.loads(s)
        except ValueError:
            obj = None
        if isinstance(obj, dict):
            pairs: list[tuple[str, float]] = []
            _flatten(obj, "", pairs)
            pairs = _dedupe(pairs)
            if pairs and _any_finite(pairs):
                return pairs
    kv = [(k, float(v)) for k, v in _KV.findall(s)]
    if kv:
        return _dedupe(kv)
    toks = [t for t in _SPLIT.split(s) if t]
    try:
        vals = [float(t) for t in toks]
    except ValueError:
        return []
    pairs = [(f"CH{i + 1}", _finite_or_nan(v)) for i, v in enumerate(vals)]
    return pairs if _any_finite(pairs) else []
