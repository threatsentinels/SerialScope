"""Bounded live sample buffer.

Layout: arrays of 2*keep rows; when full, the newest `keep` rows are moved to the
front. So between `keep` and `2*keep` recent samples are retained and the number of
discarded samples is reported in `overwritten`. Display-only: recordings are lossless
and go to disk.
"""
from __future__ import annotations

import numpy as np

from .config import LIVE_BUFFER_SAMPLES, MAX_CHANNELS


class SampleBuffer:
    def __init__(self, keep: int = LIVE_BUFFER_SAMPLES, max_channels: int = MAX_CHANNELS):
        self.keep = keep
        self.max_channels = max_channels
        self._t = np.empty(2 * keep, dtype=np.float64)
        self._v = np.empty((2 * keep, max_channels), dtype=np.float64)
        self._n = 0
        self.total = 0        # samples ever appended (since clear)
        self.overwritten = 0  # samples dropped from this buffer to bound memory

    @property
    def n(self) -> int:
        return self._n

    @property
    def revision(self) -> int:
        return self.total

    @property
    def t(self) -> np.ndarray:
        return self._t[: self._n]

    @property
    def v(self) -> np.ndarray:
        return self._v[: self._n]

    def append(self, t: float, pairs) -> None:
        if self._n >= 2 * self.keep:
            k = self.keep
            self._t[:k] = self._t[self._n - k: self._n]
            self._v[:k] = self._v[self._n - k: self._n]
            self.overwritten += self._n - k
            self._n = k
        n = self._n
        self._t[n] = t
        row = self._v[n]
        row.fill(np.nan)
        for i, x in pairs:
            row[i] = x
        self._n = n + 1
        self.total += 1

    def clear(self) -> None:
        self._n = 0
        self.total = 0
        self.overwritten = 0

    def index_range(self, t0: float, t1: float) -> tuple[int, int]:
        t = self.t
        return int(np.searchsorted(t, t0, "left")), int(np.searchsorted(t, t1, "right"))
