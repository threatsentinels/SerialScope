"""Cursor readout and per-channel statistics (min/max/mean/peak-to-peak) for the cursor
interval if cursors are on, otherwise for the visible window. Values include display gain/offset."""
from __future__ import annotations

from PySide6.QtWidgets import QLabel, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget

from ..analysis import value_at, window_stats
from . import theme
from .theme import C

COLS = ["Channel", "@A", "@B", "B-A", "Min", "Max", "Mean", "Pk-Pk"]


def _f(x) -> str:
    return "-" if x is None else f"{x:.5g}"


class MeasurePanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.header = QLabel("No data")
        self.header.setFont(theme.mono_font(9))
        self.header.setWordWrap(True)
        self.header.setStyleSheet(f"color: {C['muted']};")
        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.verticalHeader().setVisible(False)
        self.table.setFont(theme.mono_font(9))
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.table.horizontalHeader().setStretchLastSection(True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.addWidget(self.header)
        lay.addWidget(self.table, 1)

    def update_measurements(self, source, registry, view_range, cursors) -> None:
        if source is None or registry is None or source.n == 0:
            self.header.setText("No data")
            self.table.setRowCount(0)
            return
        t, v = source.t, source.v
        if cursors:
            ta, tb = cursors
            lo, hi = min(ta, tb), max(ta, tb)
            dt = tb - ta
            head = (f"A t={ta:.6f} s   B t={tb:.6f} s\n"
                    f"dt={dt:.6f} s   1/|dt|={(1 / abs(dt)) if dt else float('inf'):.5g} Hz\n"
                    f"Stats over cursor interval")
        else:
            lo, hi = view_range
            head = f"Stats over visible window [{lo:.3f} .. {hi:.3f}] s"
        self.header.setText(head)
        self.table.setRowCount(len(registry))
        for r, ch in enumerate(registry.channels):
            cells = [registry.display_name(r, with_unit=True)] + ["-"] * 7
            vals: dict[int, float] = {}
            tips = [""] * 8
            if r < v.shape[1] and ch.visible:
                y = v[:, r]
                if cursors:
                    for k, tc in ((1, ta), (2, tb)):
                        hit = value_at(t, y, tc)
                        if hit:
                            vals[k] = float(registry.scale(r, hit[1]))
                            cells[k] = _f(vals[k])
                            tips[k] = f"sample t = {hit[0]:.6f} s"
                    if 1 in vals and 2 in vals:
                        cells[3] = _f(vals[2] - vals[1])
                st = window_stats(t, y, lo, hi)
                if st:
                    a, b = registry.scale(r, st["min"]), registry.scale(r, st["max"])
                    cells[4], cells[5] = _f(min(a, b)), _f(max(a, b))
                    cells[6] = _f(registry.scale(r, st["mean"]))
                    cells[7] = _f(abs(b - a))
                    tips[4] = f"{st['count']} samples"
            for c, text in enumerate(cells):
                item = self.table.item(r, c)
                if item is None:
                    item = QTableWidgetItem()
                    self.table.setItem(r, c, item)
                item.setText(text)
                item.setToolTip(tips[c])
