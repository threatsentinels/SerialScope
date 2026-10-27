"""Acquisition thread (pure Python, no Qt), serial source, simulator source.

The worker never touches the GUI. It stamps each line with seconds since start of the
acquisition (time.perf_counter) and hands batches to the GUI through a bounded,
lock-protected queue; overflow is counted in `dropped`, never silent. Events (opened,
warning, error, closed) travel through a separate queue.
"""
from __future__ import annotations

import json
import logging
import math
import queue
import random
import re
import threading
import time
from collections import deque
from dataclasses import dataclass, asdict, fields

from .config import MAX_LINE_BYTES, WORKER_QUEUE_LIMIT
from .parser import parse_line

log = logging.getLogger(__name__)


def list_ports() -> list[tuple[str, str]]:
    try:
        from serial.tools import list_ports as lp
        ports = [(p.device, p.description or "") for p in lp.comports()]
    except Exception:
        log.exception("Could not enumerate serial ports")
        return []
    ports.sort(key=lambda p: [int(x) if x.isdigit() else x for x in re.split(r"(\d+)", p[0])])
    return ports


def friendly_serial_error(exc: BaseException, port: str) -> str:
    msg = str(exc)
    low = msg.lower()
    if isinstance(exc, PermissionError) or "access is denied" in low or "permission" in low or "busy" in low:
        return (f"Cannot open {port}: access denied. The port is probably in use by another "
                f"program (Arduino IDE, another terminal) - close it and try again.")
    if isinstance(exc, FileNotFoundError) or "could not open port" in low or "cannot find" in low \
            or "no such file" in low:
        return f"Cannot open {port}: port not found. The device may be unplugged - refresh the port list."
    if isinstance(exc, ValueError):
        return f"Invalid serial settings for {port}: {msg}"
    return f"Serial error on {port}: {msg}"


class LineSplitter:
    """Splits a byte stream into lines on LF, CRLF or CR. Empty lines are skipped.
    Returns (end_offset_within_this_chunk, line_bytes) so timestamps can be interpolated."""
    _TERM = re.compile(rb"\r\n|\n|\r")

    def __init__(self, max_len: int = MAX_LINE_BYTES):
        self.max_len = max_len
        self.buf = b""
        self.overlong = 0

    def feed(self, data: bytes) -> list[tuple[int, bytes]]:
        carry = len(self.buf)
        buf = self.buf + data
        out, pos = [], 0
        for m in self._TERM.finditer(buf):
            line = buf[pos:m.start()]
            pos = m.end()
            if len(line) > self.max_len:
                self.overlong += 1
                line = line[: self.max_len]
            if line:
                out.append((max(1, m.end() - carry), line))
        rest = buf[pos:]
        if len(rest) > self.max_len:       # no terminator in sight: flush truncated
            self.overlong += 1
            out.append((len(buf) - carry, rest[: self.max_len]))
            rest = b""
        self.buf = rest
        return out


# ---------------------------------------------------------------- sources
@dataclass
class SerialSettings:
    port: str
    baud: int = 115200
    bytesize: int = 8
    parity: str = "N"
    stopbits: float = 1


class SerialSource:
    description = "serial"
    is_simulator = False

    def __init__(self, settings: SerialSettings, serial_factory=None):
        self.s = settings
        self._factory = serial_factory
        self.ser = None
        self.splitter = LineSplitter()
        self.decode_errors = 0
        self._t_prev = 0.0
        self._last_t = 0.0
        self.description = f"{settings.port} @ {settings.baud} {settings.bytesize}{settings.parity}{settings.stopbits:g}"

    def open(self) -> None:
        factory = self._factory
        if factory is None:
            import serial
            factory = serial.Serial
        s = self.s
        try:
            self.ser = factory(s.port, s.baud, bytesize=s.bytesize, parity=s.parity,
                               stopbits=s.stopbits, timeout=0.05, write_timeout=1.0)
        except Exception as exc:
            raise ConnectionError(friendly_serial_error(exc, s.port)) from exc

    def poll(self, worker: "AcquisitionWorker") -> list[tuple[float, str]]:
        ser = self.ser
        data = ser.read(max(1, ser.in_waiting))
        tn = worker.now()
        if not data:
            self._t_prev = tn
            return []
        worker.bytes_rx += len(data)
        out = []
        for end, raw in self.splitter.feed(data):
            t = max(self._last_t, self._t_prev + (tn - self._t_prev) * end / len(data))
            self._last_t = t
            text = raw.decode("utf-8", errors="replace")
            if "\ufffd" in text:
                self.decode_errors += 1
            out.append((t, text))
        worker.overlong = self.splitter.overlong
        worker.decode_errors = self.decode_errors
        self._t_prev = tn
        return out

    def write(self, data: bytes) -> bool:
        self.ser.write(data)
        return True

    def close(self) -> None:
        if self.ser is not None:
            try:
                self.ser.close()
            except Exception:
                log.exception("Error closing serial port")


NAMES = ["TEMP", "VOLT", "ACCEL", "LIGHT", "PRESS", "FLOW", "LEVEL", "RPM"]
FREQS = [0.1, 5.0, 2.0, 0.3, 0.7, 1.1, 3.0, 8.0]
GARBAGE = ["ERR: sensor timeout", "12.4,abc,0.8", "@@@@", "boot v1.2 ready"]


@dataclass
class SimConfig:
    rate_hz: float = 50.0
    channels: int = 3
    noise: float = 0.05
    spike_prob: float = 0.01
    fmt: str = "csv"          # csv | kv | json
    garbage_rate: float = 0.0  # probability of an extra malformed/text line after a sample
    seed: int = 1

    @classmethod
    def from_dict(cls, d: dict | None) -> "SimConfig":
        c = cls()
        for f in fields(cls):
            if d and f.name in d:
                try:
                    setattr(c, f.name, type(getattr(c, f.name))(d[f.name]))
                except (TypeError, ValueError):
                    pass
        c.rate_hz = min(max(c.rate_hz, 1.0), 2000.0)
        c.channels = min(max(c.channels, 1), len(NAMES))
        return c


class Simulator:
    """Deterministic: output depends only on the config (incl. seed) and sample index."""

    def __init__(self, cfg: SimConfig):
        self.cfg = cfg
        self.rng = random.Random(cfg.seed)
        self.i = 0

    def next(self) -> list[tuple[float, str]]:
        c, rng = self.cfg, self.rng
        t = self.i / c.rate_hz
        self.i += 1
        vals = []
        for k in range(c.channels):
            base = (25.0, 3.3, 0.0, 400.0, 101.3, 12.0, 50.0, 1500.0)[k]
            amp = (2.0, 0.2, 1.2, 40.0, 0.5, 3.0, 10.0, 200.0)[k]
            v = base + amp * math.sin(2 * math.pi * FREQS[k] * t) + c.noise * amp * rng.gauss(0, 1)
            if k == 2 and rng.random() < c.spike_prob:
                v += rng.choice((-1, 1)) * rng.uniform(3.0, 5.0)
            vals.append(v)
        if c.fmt == "kv":
            line = ",".join(f"{NAMES[k]}={v:.4f}" for k, v in enumerate(vals))
        elif c.fmt == "json":
            line = json.dumps({NAMES[k]: round(v, 4) for k, v in enumerate(vals)})
        else:
            line = ",".join(f"{v:.4f}" for v in vals)
        out = [(t, line)]
        if c.garbage_rate > 0 and rng.random() < c.garbage_rate:
            out.append((t, rng.choice(GARBAGE)))
        return out


class SimulatorSource:
    is_simulator = True

    def __init__(self, cfg: SimConfig):
        self.cfg = cfg
        self.sim = Simulator(cfg)
        self.description = f"Simulator {cfg.rate_hz:g} Hz, {cfg.channels} ch, {cfg.fmt}, seed {cfg.seed}"

    def open(self) -> None:
        pass

    def poll(self, worker: "AcquisitionWorker"):
        due = int(worker.now() * self.cfg.rate_hz) + 1
        out: list = []
        burst = 0
        while self.sim.i < due and burst < 5000:   # cap the catch-up burst per poll
            out.extend(self.sim.next())
            burst += 1
        if not out:
            worker.stop_event.wait(0.002)
        worker.bytes_rx += sum(len(text) + 1 for _, text in out)
        return out

    def write(self, data: bytes) -> bool:
        return False

    def close(self) -> None:
        pass


# ---------------------------------------------------------------- worker
class AcquisitionWorker(threading.Thread):
    def __init__(self, source, queue_limit: int = WORKER_QUEUE_LIMIT):
        super().__init__(name="acquisition", daemon=True)
        self.source = source
        self.stop_event = threading.Event()
        self.events: queue.SimpleQueue = queue.SimpleQueue()
        self._out: deque = deque()
        self._lock = threading.Lock()
        self._limit = queue_limit
        self._outgoing: queue.SimpleQueue = queue.SimpleQueue()
        self.t0 = time.perf_counter()
        self.start_unix = time.time()   # sampled together with t0: wall = start_unix + t
        self.bytes_rx = self.tx_bytes = 0
        self.overlong = self.decode_errors = self.dropped = 0

    def now(self) -> float:
        return time.perf_counter() - self.t0

    def stop(self) -> None:
        self.stop_event.set()

    def send(self, data: bytes) -> None:
        self._outgoing.put(data)

    def drain(self) -> list:
        with self._lock:
            out, self._out = list(self._out), deque()
        return out

    def drain_events(self) -> list[tuple[str, str]]:
        out = []
        while True:
            try:
                out.append(self.events.get_nowait())
            except queue.Empty:
                return out

    def _push(self, lines) -> None:
        recs = [(t, text, parse_line(text)) for t, text in lines]
        with self._lock:
            self._out.extend(recs)
            over = len(self._out) - self._limit
            if over > 0:
                for _ in range(over):
                    self._out.popleft()
                self.dropped += over

    def _send_pending(self) -> None:
        while True:
            try:
                data = self._outgoing.get_nowait()
            except queue.Empty:
                return
            try:
                if self.source.write(data):
                    self.tx_bytes += len(data)
                else:
                    self.events.put(("warning", "The simulator ignores transmitted data."))
            except OSError as exc:
                self.events.put(("warning", f"Write failed: {exc}"))

    def run(self) -> None:
        try:
            self.source.open()
        except Exception as exc:
            log.error("Open failed: %s", exc)
            self.events.put(("error", str(exc)))
            self.events.put(("closed", "open failed"))
            return
        self.events.put(("opened", self.source.description))
        reason = "stopped"
        try:
            while not self.stop_event.is_set():
                self._send_pending()
                lines = self.source.poll(self)
                if lines:
                    self._push(lines)
        except Exception as exc:   # unplugged device, driver error, ...
            reason = "connection lost"
            if not self.stop_event.is_set():
                log.exception("Acquisition failed")
                self.events.put(("error", f"Connection lost: {exc}. The device may have been unplugged."))
        finally:
            try:
                self.source.close()
            finally:
                self.events.put(("closed", reason))
