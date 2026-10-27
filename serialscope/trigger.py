"""Threshold trigger on one channel. Detects level crossings between consecutive samples."""
from __future__ import annotations

import math


class Trigger:
    def __init__(self, channel: int, level: float, edge: str = "rising",
                 pre_s: float = 1.0, post_s: float = 1.0):
        if edge not in ("rising", "falling", "either"):
            raise ValueError(edge)
        self.channel, self.level, self.edge = channel, level, edge
        self.pre_s, self.post_s = pre_s, post_s
        self.fired_t: float | None = None
        self._prev: tuple[float, float] | None = None

    def feed(self, t: float, y: float) -> bool:
        """Feed one sample of the trigger channel; True when this sample fires it."""
        if self.fired_t is not None or not math.isfinite(y):
            return False
        prev, self._prev = self._prev, (t, y)
        if prev is None:
            return False
        t0, y0 = prev
        lv = self.level
        rise = y0 < lv <= y
        fall = y0 > lv >= y
        if (self.edge == "rising" and rise) or (self.edge == "falling" and fall) or \
                (self.edge == "either" and (rise or fall)):
            self.fired_t = t0 + (lv - y0) / (y - y0) * (t - t0) if y != y0 else t
            return True
        return False
