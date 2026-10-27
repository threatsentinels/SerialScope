"""Raw serial console: timestamps, filter (text or regex), find, autoscroll, send bar.

The pane renders a *view* of the raw line log kept elsewhere; filtering or toggling
timestamps re-renders from that log via `provider`. Per refresh at most MAX_APPEND lines
are drawn; skipped ones stay in the log and are counted (display only).
"""
from __future__ import annotations

import re
import time
from typing import Callable, Iterable

from PySide6.QtCore import Signal
from PySide6.QtGui import QTextDocument
from PySide6.QtWidgets import (QCheckBox, QComboBox, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
                               QPushButton, QVBoxLayout, QWidget)

from . import theme
from .theme import C

MAX_APPEND = 400
MAX_BLOCKS = 10_000
ENDINGS = {"LF": "\n", "CRLF": "\r\n", "CR": "\r", "None": ""}


def fmt_time(unix: float) -> str:
    return f"{time.strftime('%H:%M:%S', time.localtime(unix))}.{int((unix % 1) * 1000):03d}"


class ConsolePane(QWidget):
    send_requested = Signal(str, str)     # text, line ending

    def __init__(self, parent=None):
        super().__init__(parent)
        self.start_unix = 0.0
        self.provider: Callable[[], Iterable[tuple[float, str]]] = lambda: []
        self.frozen = False
        self.skipped = 0
        self._regex: re.Pattern | None = None

        top = QHBoxLayout()
        top.setContentsMargins(4, 3, 4, 0)
        top.setSpacing(4)
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Filter lines (hide non-matching)")
        self.filter_edit.setClearButtonEnabled(True)
        self.regex_cb = QCheckBox("Regex")
        self.find_edit = QLineEdit()
        self.find_edit.setPlaceholderText("Find")
        self.find_edit.setClearButtonEnabled(True)
        self.find_edit.setFixedWidth(130)
        self.prev_btn, self.next_btn = QPushButton("Prev"), QPushButton("Next")
        self.time_cb = QCheckBox("Time")
        self.time_cb.setChecked(True)
        self.scroll_cb = QCheckBox("Autoscroll")
        self.scroll_cb.setChecked(True)
        self.clear_btn = QPushButton("Clear")
        self.skip_label = QLabel("")
        self.skip_label.setStyleSheet(f"color: {C['amber']};")
        for w in (self.filter_edit, self.regex_cb, self.find_edit, self.prev_btn, self.next_btn,
                  self.time_cb, self.scroll_cb, self.skip_label, self.clear_btn):
            top.addWidget(w, 1 if w is self.filter_edit else 0)

        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setFont(theme.mono_font(9))
        self.view.setMaximumBlockCount(MAX_BLOCKS)
        self.view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)

        bottom = QHBoxLayout()
        bottom.setContentsMargins(4, 0, 4, 3)
        bottom.setSpacing(4)
        self.send_edit = QLineEdit()
        self.send_edit.setPlaceholderText("Send to device (Enter)")
        self.send_edit.setFont(theme.mono_font(9))
        self.ending = QComboBox()
        self.ending.addItems(list(ENDINGS))
        self.send_btn = QPushButton("Send")
        for w in (self.send_edit, self.ending, self.send_btn):
            bottom.addWidget(w, 1 if w is self.send_edit else 0)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(3)
        lay.addLayout(top)
        lay.addWidget(self.view, 1)
        lay.addLayout(bottom)

        self.filter_edit.textChanged.connect(self._filter_changed)
        self.regex_cb.toggled.connect(self._filter_changed)
        self.time_cb.toggled.connect(lambda _: self.rerender())
        self.clear_btn.clicked.connect(self.clear)
        self.find_edit.returnPressed.connect(self.find_next)
        self.next_btn.clicked.connect(self.find_next)
        self.prev_btn.clicked.connect(self.find_prev)
        self.send_edit.returnPressed.connect(self._send)
        self.send_btn.clicked.connect(self._send)
        self.set_send_enabled(False)

    # ---- filtering ----
    def _filter_changed(self, *_):
        text = self.filter_edit.text()
        self._regex = None
        ok = True
        if text and self.regex_cb.isChecked():
            try:
                self._regex = re.compile(text, re.IGNORECASE)
            except re.error:
                ok = False
        self.filter_edit.setStyleSheet("" if ok else f"border-color: {C['red']};")
        self.rerender()

    def _matches(self, line: str) -> bool:
        text = self.filter_edit.text()
        if not text:
            return True
        if self.regex_cb.isChecked():
            return self._regex.search(line) is not None if self._regex else True
        return text.lower() in line.lower()

    def _format(self, t: float, line: str) -> str:
        return f"{fmt_time(self.start_unix + t)}  {line}" if self.time_cb.isChecked() else line

    # ---- content ----
    def append_lines(self, lines: list[tuple[float, str]]) -> None:
        if self.frozen or not lines:
            return
        if len(lines) > MAX_APPEND:
            self.skipped += len(lines) - MAX_APPEND
            self.skip_label.setText(f"{self.skipped:,} not shown")
            self.skip_label.setToolTip("Display only: too many lines per refresh. "
                                       "All lines are still stored and recorded; Clear or re-filter to redraw.")
            lines = lines[-MAX_APPEND:]
        out = [self._format(t, x) for t, x in lines if self._matches(x)]
        if out:
            self.view.appendPlainText("\n".join(out))
            if self.scroll_cb.isChecked():
                sb = self.view.verticalScrollBar()
                sb.setValue(sb.maximum())

    def rerender(self) -> None:
        if self.frozen:
            return
        entries = list(self.provider())[-MAX_BLOCKS:]
        out = [self._format(t, x) for t, x in entries if self._matches(x)]
        self.view.setPlainText("\n".join(out))
        self.skipped = 0
        self.skip_label.setText("")
        if self.scroll_cb.isChecked():
            sb = self.view.verticalScrollBar()
            sb.setValue(sb.maximum())

    def note(self, text: str) -> None:
        self.view.appendPlainText(f"# {text}")

    def clear(self) -> None:
        self.view.clear()
        self.skipped = 0
        self.skip_label.setText("")

    def set_frozen(self, frozen: bool) -> None:
        self.frozen = frozen
        if not frozen:
            self.rerender()

    # ---- find ----
    def _find(self, backward: bool) -> None:
        text = self.find_edit.text()
        if not text:
            return
        flag = QTextDocument.FindFlag.FindBackward if backward else None
        found = self.view.find(text, flag) if flag else self.view.find(text)
        if not found:       # wrap around
            from PySide6.QtGui import QTextCursor
            self.view.moveCursor(QTextCursor.MoveOperation.End if backward else QTextCursor.MoveOperation.Start)
            found = self.view.find(text, flag) if flag else self.view.find(text)
        self.find_edit.setStyleSheet("" if found else f"border-color: {C['red']};")

    def find_next(self) -> None:
        self._find(False)

    def find_prev(self) -> None:
        self._find(True)

    def focus_find(self) -> None:
        self.find_edit.setFocus()
        self.find_edit.selectAll()

    # ---- sending ----
    def set_send_enabled(self, on: bool) -> None:
        for w in (self.send_edit, self.ending, self.send_btn):
            w.setEnabled(on)
        self.send_edit.setPlaceholderText("Send to device (Enter)" if on else "Connect to a serial port to send")

    def _send(self) -> None:
        text = self.send_edit.text()
        if text:
            self.send_requested.emit(text, ENDINGS[self.ending.currentText()])
            self.send_edit.clear()
