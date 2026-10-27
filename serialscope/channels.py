"""Channel registry: maps source keys to column indices plus display style."""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Callable

import numpy as np

from .config import CHANNEL_COLORS, MAX_CHANNELS

_STYLE_FIELDS = ("name", "color", "visible", "unit", "gain", "offset")


@dataclass
class ChannelInfo:
    key: str
    name: str
    color: str
    visible: bool = True
    unit: str = ""
    gain: float = 1.0
    offset: float = 0.0


class ChannelRegistry:
    def __init__(self, prefs: dict | None = None, max_channels: int = MAX_CHANNELS):
        self.prefs: dict = prefs if prefs is not None else {}  # shared key -> style dict
        self.max_channels = max_channels
        self.channels: list[ChannelInfo] = []
        self._index: dict[str, int] = {}
        self.overflow_values = 0
        self.version = 0  # bumped on any structural or style change

    @classmethod
    def from_keys(cls, keys, prefs: dict | None = None) -> "ChannelRegistry":
        reg = cls(prefs)
        for k in keys:
            reg.index_for(k)
        return reg

    def __len__(self) -> int:
        return len(self.channels)

    def index_for(self, key: str) -> int | None:
        i = self._index.get(key)
        if i is not None:
            return i
        if len(self.channels) >= self.max_channels:
            self.overflow_values += 1
            return None
        i = len(self.channels)
        info = ChannelInfo(key=key, name=key, color=CHANNEL_COLORS[i % len(CHANNEL_COLORS)])
        saved = self.prefs.get(key)
        if isinstance(saved, dict):
            for f in _STYLE_FIELDS:
                if f in saved:
                    setattr(info, f, saved[f])
        self.channels.append(info)
        self._index[key] = i
        self.version += 1
        return i

    def clear(self) -> None:
        self.channels.clear()
        self._index.clear()
        self.overflow_values = 0
        self.version += 1

    def keys(self) -> list[str]:
        return [c.key for c in self.channels]

    def update(self, idx: int, **style) -> None:
        info = self.channels[idx]
        for k, v in style.items():
            if k in _STYLE_FIELDS:
                setattr(info, k, v)
        self.prefs[info.key] = {f: getattr(info, f) for f in _STYLE_FIELDS}
        self.version += 1

    def set_all_visible(self, visible: bool) -> None:
        for i in range(len(self.channels)):
            self.update(i, visible=visible)

    def display_name(self, idx: int, with_unit: bool = False) -> str:
        c = self.channels[idx]
        return f"{c.name} [{c.unit}]" if with_unit and c.unit else c.name

    def scale(self, idx: int, y):
        c = self.channels[idx]
        return y if (c.gain == 1.0 and c.offset == 0.0) else y * c.gain + c.offset

    def export_prefs(self) -> dict:
        for c in self.channels:
            self.prefs[c.key] = {f: getattr(c, f) for f in _STYLE_FIELDS}
        return self.prefs