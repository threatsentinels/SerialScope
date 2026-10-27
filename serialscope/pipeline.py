"""GUI-independent data pipeline: records in -> channels, live buffer, log, recording, trigger.

Records are (t, text, pairs): t = seconds since acquisition start (monotonic clock),
text = raw line, pairs = parsed [(key, value)] (empty if not numeric).
Ingestion never depends on what the display is doing (pause/playback).
"""
from __future__ import annotations

import logging
import math
from collections import deque
from pathlib import Path

import numpy as np

from .buffers import SampleBuffer
from .channels import ChannelRegistry
from .config import CONSOLE_LOG_LINES, LIVE_BUFFER_SAMPLES
from .session import Session, SessionWriter
from .trigger import Trigger

log = logging.getLogger(__name__)


class Pipeline:
    def __init__(self, registry: ChannelRegistry, live_keep: int = LIVE_BUFFER_SAMPLES,
                 log_lines: int = CONSOLE_LOG_LINES):
        self.registry = registry
        self.live = SampleBuffer(live_keep)
        self.log: deque[tuple[float, str]] = deque(maxlen=log_lines)
        self.start_unix = 0.0
        self.last_t = 0.0
        self.lines = self.numeric_lines = self.nonnumeric_lines = self.samples = 0
        self.writer: SessionWriter | None = None
        self.rec_origin = 0.0
        self.rec_error: str | None = None
        self.trigger: Trigger | None = None

    # ---- lifecycle -------------------------------------------------------
    def reset(self, start_unix: float) -> None:
        """New acquisition: time restarts at zero, channels and data are cleared."""
        self.registry.clear()
        self.clear_data()
        self.start_unix = start_unix
        self.last_t = 0.0

    def clear_data(self) -> None:
        self.live.clear()
        self.log.clear()
        self.lines = self.numeric_lines = self.nonnumeric_lines = self.samples = 0
        if self.trigger is not None:
            self.trigger.fired_t = None
            self.trigger._prev = None

    # ---- ingestion -------------------------------------------------------
    def ingest(self, records) -> list[tuple[float, str]]:
        reg, live = self.registry, self.live
        new_lines = []
        for t, text, pairs in records:
            self.lines += 1
            self.last_t = max(self.last_t, t)
            self.log.append((t, text))
            new_lines.append((t, text))
            idx_pairs = []
            for key, val in pairs:
                i = reg.index_for(key)
                if i is not None:
                    idx_pairs.append((i, val))
            if idx_pairs:
                self.numeric_lines += 1
                self.samples += 1
                live.append(t, idx_pairs)
                tr = self.trigger
                if tr is not None and tr.fired_t is None:
                    for i, val in idx_pairs:
                        if i == tr.channel:
                            tr.feed(t, val)
            else:
                self.nonnumeric_lines += 1
            if self.writer is not None:   # may be cleared mid-batch by a write failure
                self._record(t, text, idx_pairs)
        if self.writer is not None:
            try:
                self.writer.flush()
            except OSError as exc:
                self._fail_recording(exc)
        return new_lines

    def _record(self, t: float, text: str, idx_pairs) -> None:
        w = self.writer
        try:
            tt = max(0.0, t - self.rec_origin)
            w.define_channels(self.registry.keys())
            w.write_line(tt, text)
            if idx_pairs:
                row: list = [None] * len(self.registry)
                for i, val in idx_pairs:
                    row[i] = val if math.isfinite(val) else None
                w.write_sample(tt, row)
        except OSError as exc:
            self._fail_recording(exc)

    def _fail_recording(self, exc: OSError) -> None:
        self.rec_error = f"Recording stopped because writing failed: {exc}"
        log.error(self.rec_error)
        w, self.writer = self.writer, None
        try:
            if w is not None:
                w.close("write error")
        except OSError:
            pass

    # ---- recording -------------------------------------------------------
    @property
    def recording(self) -> bool:
        return self.writer is not None

    def start_recording(self, path: Path, meta: dict | None = None) -> None:
        if self.writer is not None:
            raise RuntimeError("Already recording")
        self.rec_error = None
        self.rec_origin = self.last_t
        self.writer = SessionWriter(path, self.start_unix + self.rec_origin, meta)
        self.writer.define_channels(self.registry.keys())

    def stop_recording(self, reason: str = "stopped by user") -> dict | None:
        w, self.writer = self.writer, None
        if w is None:
            return None
        try:
            w.close(reason)
        except OSError as exc:
            self.rec_error = f"Could not finalise recording: {exc}"
            log.error(self.rec_error)
        return {"path": w.path, "samples": w.samples, "lines": w.lines, "reason": reason}

    # ---- snapshots -------------------------------------------------------
    def snapshot_session(self, meta: dict | None = None) -> Session:
        """Copy of the live buffer + log as a Session (also used for the paused view)."""
        n = len(self.registry)
        t = self.live.t.copy()
        v = self.live.v[:, :n].copy()
        lt = np.array([x[0] for x in self.log], dtype=np.float64)
        lx = [x[1] for x in self.log]
        return Session(t=t, v=v, channels=self.registry.keys(), log_t=lt, log_text=lx,
                       start_unix=self.start_unix, meta=meta or {})
