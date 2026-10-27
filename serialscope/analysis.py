"""Numerics: decimation, window statistics, nearest-value lookup, spectrum, duration parsing."""
from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np


def parse_duration(text: str) -> float | None:
    """'10', '10 s', '2 min', '500 ms' -> seconds; 'all' -> None; raises ValueError."""
    s = text.strip().lower()
    if s in ("all", ""):
        return None
    m = re.fullmatch(r"([0-9]*\.?[0-9]+)\s*(ms|s|sec|m|min)?", s)
    if not m:
        raise ValueError(f"Cannot parse duration {text!r}")
    val = float(m.group(1)) * {"ms": 1e-3, "m": 60.0, "min": 60.0}.get(m.group(2) or "s", 1.0)
    if val <= 0:
        raise ValueError("Duration must be positive")
    return val


def decimate_minmax(x: np.ndarray, y: np.ndarray, max_points: int):
    """Peak-preserving decimation; keeps first/last point and per-bucket min and max."""
    n = len(x)
    if n <= max_points or max_points < 8:
        return x, y
    buckets = max_points // 2
    size = -(-n // buckets)
    m = (n // size) * size
    yy = y[:m].reshape(-1, size)
    base = np.arange(0, m, size)
    idx = [np.argmin(yy, axis=1) + base, np.argmax(yy, axis=1) + base, np.array([0, n - 1])]
    if m < n:
        tail = y[m:]
        idx.append(np.array([m + int(np.argmin(tail)), m + int(np.argmax(tail))]))
    sel = np.unique(np.concatenate(idx))
    return x[sel], y[sel]


def window_stats(t: np.ndarray, y: np.ndarray, t0: float, t1: float) -> dict | None:
    i0, i1 = np.searchsorted(t, t0, "left"), np.searchsorted(t, t1, "right")
    seg = y[i0:i1]
    seg = seg[np.isfinite(seg)]
    if seg.size == 0:
        return None
    lo, hi = float(seg.min()), float(seg.max())
    return {"count": int(seg.size), "min": lo, "max": hi, "mean": float(seg.mean()), "pp": hi - lo}


def value_at(t: np.ndarray, y: np.ndarray, tc: float, span: int = 5000):
    """Nearest finite sample to tc -> (sample_time, value) or None."""
    n = len(t)
    if n == 0:
        return None
    i = int(np.searchsorted(t, tc))
    a, b = max(0, i - span), min(n, i + span)
    fin = np.flatnonzero(np.isfinite(y[a:b])) + a
    if fin.size == 0:
        return None
    j = fin[np.argmin(np.abs(t[fin] - tc))]
    return float(t[j]), float(y[j])


@dataclass
class Spectrum:
    freq: np.ndarray
    amp: np.ndarray
    fs: float
    peak_hz: float
    peak_amp: float
    dt_cv: float   # std/median of sample spacing; large = irregular timing, FFT less reliable


def spectrum(t: np.ndarray, y: np.ndarray, max_len: int = 1 << 18) -> Spectrum:
    fin = np.isfinite(y)
    t, y = t[fin], y[fin]
    if len(t) < 16:
        raise ValueError("Need at least 16 samples in range.")
    dt = np.diff(t)
    med = float(np.median(dt))
    span = float(t[-1] - t[0])
    if med <= 0 or span <= 0:
        raise ValueError("Timestamps are not increasing.")
    cv = float(np.std(dt) / med)
    n = min(int(round(span / med)) + 1, max_len)
    fs = (n - 1) / span
    yu = np.interp(t[0] + np.arange(n) / fs, t, y)
    yu = yu - yu.mean()
    w = np.hanning(n)
    amp = 2.0 * np.abs(np.fft.rfft(yu * w)) / w.sum()
    freq = np.fft.rfftfreq(n, 1.0 / fs)
    k = int(np.argmax(amp[1:])) + 1
    delta = 0.0
    if 1 <= k < len(amp) - 1:
        a, b, c = amp[k - 1], amp[k], amp[k + 1]
        den = a - 2 * b + c
        if den != 0:
            delta = float(np.clip(0.5 * (a - c) / den, -0.5, 0.5))
    return Spectrum(freq, amp, fs, (k + delta) * fs / n, float(amp[k]), cv)
