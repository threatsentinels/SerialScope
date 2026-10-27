"""Persistent rotating log file: %LOCALAPPDATA%\\SerialScope\\logs on Windows, ~/.serialscope/logs elsewhere."""
from __future__ import annotations

import logging
import logging.handlers
import os
import sys
from pathlib import Path


def log_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    d = Path(base) / "SerialScope" / "logs" if base else Path.home() / ".serialscope" / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def setup_logging(level: int = logging.INFO) -> Path | None:
    root = logging.getLogger()
    root.setLevel(level)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    path = None
    try:
        path = log_dir() / "serialscope.log"
        fh = logging.handlers.RotatingFileHandler(path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except OSError:
        path = None
    if sys.stderr is not None:   # None in a windowed PyInstaller build
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(fmt)
        root.addHandler(sh)
    return path
