"""Session file (.sscope, format v2 = JSON Lines), loader, writer and CSV export.

Records (one JSON object per line):
  header  {"format","version","created","start_unix","app","meta"}
  channel {"k":"c","i":idx,"key":name}
  sample  {"k":"s","t":sec_since_start,"v":[value|null,...]}   (index = channel idx)
  line    {"k":"l","t":sec_since_start,"x":"raw text"}
  end     {"k":"e","reason":...,"samples":N,"lines":M}
Absolute time of any record = start_unix + t. A file cut off by a crash still loads.
Legacy v1 (single JSON object) files are still readable.
"""
from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from . import __version__
from .config import SESSION_FORMAT, SESSION_VERSION


@dataclass
class Session:
    t: np.ndarray
    v: np.ndarray                      # shape (n, channels), NaN = no value
    channels: list[str]
    log_t: np.ndarray
    log_text: list[str]
    start_unix: float = 0.0
    meta: dict = field(default_factory=dict)
    created: str = ""
    version: int = SESSION_VERSION
    truncated: bool = False            # file ended without an end record / partial last line
    bad_records: int = 0
    end_info: dict = field(default_factory=dict)
    revision: int = 0

    @property
    def n(self) -> int:
        return int(self.t.shape[0])


def _num(x: float):
    return round(x, 6)


class SessionWriter:
    """Incremental writer used while recording; memory use is constant."""

    def __init__(self, path: Path, start_unix: float, meta: dict | None = None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._f = open(self.path, "w", encoding="utf-8", newline="\n", buffering=1 << 16)
        self.samples = 0
        self.lines = 0
        self.defined = 0
        self.closed = False
        hdr = {"format": SESSION_FORMAT, "version": SESSION_VERSION,
               "created": datetime.now().astimezone().isoformat(timespec="seconds"),
               "start_unix": round(start_unix, 6), "app": __version__, "meta": meta or {}}
        self._f.write(json.dumps(hdr) + "\n")

    def define_channels(self, keys: list[str]) -> None:
        while self.defined < len(keys):
            self._f.write(json.dumps({"k": "c", "i": self.defined, "key": keys[self.defined]}) + "\n")
            self.defined += 1

    def write_line(self, t: float, text: str) -> None:
        self._f.write('{"k":"l","t":%s,"x":%s}\n' % (_num(t), json.dumps(text, ensure_ascii=False)))
        self.lines += 1

    def write_sample(self, t: float, row: list) -> None:
        self._f.write('{"k":"s","t":%s,"v":%s}\n' % (_num(t), json.dumps(row)))
        self.samples += 1

    def flush(self) -> None:
        self._f.flush()

    def close(self, reason: str = "stopped") -> None:
        if self.closed:
            return
        self.closed = True
        try:
            self._f.write(json.dumps({"k": "e", "reason": reason, "samples": self.samples,
                                      "lines": self.lines}) + "\n")
        finally:
            self._f.close()


def _row_from(values) -> list:
    return [None if (x is None or not math.isfinite(x)) else x for x in values]


def write_session(path: Path, s: Session, reason: str = "saved") -> None:
    """Write an in-memory Session (e.g. a live-buffer snapshot) in the same format."""
    w = SessionWriter(path, s.start_unix, s.meta)
    try:
        w.define_channels(list(s.channels))
        events = [(float(t), 0, i) for i, t in enumerate(s.t)] + \
                 [(float(t), 1, i) for i, t in enumerate(s.log_t)]
        events.sort(key=lambda e: (e[0], e[1]))
        for t, kind, i in events:
            if kind == 0:
                w.write_sample(t, _row_from(s.v[i].tolist()))
            else:
                w.write_line(t, s.log_text[i])
    finally:
        w.close(reason)


def _build(ts, rows, keys, lt, lx, **kw) -> Session:
    ncols = max([len(keys)] + [len(r) for r in rows]) if rows else len(keys)
    v = np.full((len(rows), ncols), np.nan)
    for i, r in enumerate(rows):
        for j, x in enumerate(r):
            if x is not None:
                v[i, j] = x
    keys = list(keys) + [f"CH{j + 1}" for j in range(len(keys), ncols)]
    return Session(t=np.asarray(ts, dtype=np.float64), v=v, channels=keys,
                   log_t=np.asarray(lt, dtype=np.float64), log_text=lx, **kw)


def _load_v1(d: dict) -> Session:
    samples = [s for s in d.get("samples", []) if isinstance(s, dict) and isinstance(s.get("values"), list)]
    if not samples:
        raise ValueError("Session contains no samples.")
    t0 = float(samples[0].get("t", 0.0))
    ts = [float(s.get("t", 0.0)) - t0 for s in samples]
    unix0 = float(samples[0].get("timestamp", 0.0))
    rows = [[float(x) if isinstance(x, (int, float)) and math.isfinite(x) else None for x in s["values"]]
            for s in samples]
    logs = [x for x in d.get("logs", []) if isinstance(x, dict) and "text" in x]
    lt = [float(x.get("timestamp", unix0)) - unix0 for x in logs]
    lx = [str(x["text"]) for x in logs]
    return _build(ts, rows, [str(c) for c in d.get("channels", [])], lt, lx,
                  start_unix=unix0, created=str(d.get("created_at", "")), version=1,
                  meta={"note": "converted from legacy v1 session"})


def load_session(path: Path) -> Session:
    path = Path(path)
    with open(path, "r", encoding="utf-8") as f:
        first = f.readline()
        if not first.strip():
            raise ValueError("File is empty.")
        try:
            hdr = json.loads(first)
        except ValueError:
            hdr = None          # maybe a pretty-printed legacy v1 file
        if hdr is None:
            try:
                hdr = json.loads(first + f.read())
            except ValueError as exc:
                raise ValueError("Not a SerialScope session file.") from exc
        if not isinstance(hdr, dict) or hdr.get("format") != SESSION_FORMAT:
            raise ValueError("Not a SerialScope session file.")
        if hdr.get("version") == 1:
            return _load_v1(hdr)
        if hdr.get("version") != SESSION_VERSION:
            raise ValueError(f"Unsupported session version {hdr.get('version')!r}.")
        keys: list[str] = []
        ts, rows, lt, lx = [], [], [], []
        bad, end, pending_bad = 0, {}, False
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            if pending_bad:       # a bad record that was not the last one
                bad += 1
                pending_bad = False
            try:
                r = json.loads(raw)
                k = r["k"]
                if k == "s":
                    ts.append(float(r["t"]))
                    rows.append(r["v"])
                elif k == "l":
                    lt.append(float(r["t"]))
                    lx.append(str(r["x"]))
                elif k == "c":
                    keys.append(str(r["key"]))
                elif k == "e":
                    end = r
            except (ValueError, KeyError, TypeError):
                pending_bad = True
        truncated = pending_bad or not end
        return _build(ts, rows, keys, lt, lx, start_unix=float(hdr.get("start_unix", 0.0)),
                      meta=hdr.get("meta", {}) or {}, created=str(hdr.get("created", "")),
                      truncated=truncated, bad_records=bad, end_info=end)


def iso_from_unix(u: float) -> str:
    return datetime.fromtimestamp(u, tz=timezone.utc).astimezone().isoformat(timespec="milliseconds")


def export_csv(path: Path, s: Session, names: list[str] | None = None) -> int:
    """Columns: elapsed_s, unix_time, iso_time, one per channel. Empty cell = no value.
    Values are the raw parsed numbers (display gain/offset is not applied)."""
    names = names or s.channels
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["elapsed_s", "unix_time", "iso_time", *names[: s.v.shape[1]]])
        for i in range(s.n):
            t = float(s.t[i])
            u = s.start_unix + t
            row = ["" if not math.isfinite(x) else repr(float(x)) for x in s.v[i]]
            w.writerow([f"{t:.6f}", f"{u:.6f}", iso_from_unix(u) if s.start_unix else "", *row])
    return s.n
