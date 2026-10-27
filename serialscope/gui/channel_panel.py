"""Channel table: visibility, colour, name, unit, display gain/offset (display only; data stays raw)."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QColorDialog, QHBoxLayout, QPushButton, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from . import theme

COLS = ["On", "Colour", "Name", "Unit", "Gain", "Offset"]


class ChannelPanel(QWidget):
    changed = Signal()
    message = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.registry = None
        self._building = False
        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.verticalHeader().setVisible(False)
        self.table.setFont(theme.mono_font(9))
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        hh = self.table.horizontalHeader()
        hh.setStretchLastSection(True)
        for i, w in enumerate((34, 50, 110, 50, 56, 56)):
            self.table.setColumnWidth(i, w)
        self.table.itemChanged.connect(self._item_changed)
        self.table.cellClicked.connect(self._cell_clicked)
        all_btn, none_btn = QPushButton("Show all"), QPushButton("Hide all")
        all_btn.clicked.connect(lambda: self._set_all(True))
        none_btn.clicked.connect(lambda: self._set_all(False))
        row = QHBoxLayout()
        row.addWidget(all_btn)
        row.addWidget(none_btn)
        row.addStretch(1)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.addWidget(self.table, 1)
        lay.addLayout(row)

    def set_registry(self, registry) -> None:
        self.registry = registry
        self.rebuild()

    def rebuild(self) -> None:
        reg = self.registry
        self._building = True
        try:
            self.table.setRowCount(0 if reg is None else len(reg))
            if reg is None:
                return
            for r, ch in enumerate(reg.channels):
                on = QTableWidgetItem()
                on.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
                on.setCheckState(Qt.CheckState.Checked if ch.visible else Qt.CheckState.Unchecked)
                swatch = QTableWidgetItem()
                swatch.setFlags(Qt.ItemFlag.ItemIsEnabled)
                swatch.setBackground(QBrush(QColor(ch.color)))
                swatch.setToolTip("Click to change colour")
                name = QTableWidgetItem(ch.name)
                name.setForeground(QBrush(QColor(ch.color)))
                name.setToolTip(f"Source key: {ch.key}")
                cells = [on, swatch, name, QTableWidgetItem(ch.unit),
                         QTableWidgetItem(f"{ch.gain:g}"), QTableWidgetItem(f"{ch.offset:g}")]
                for c, item in enumerate(cells):
                    self.table.setItem(r, c, item)
                self.table.setRowHeight(r, 22)
        finally:
            self._building = False

    def _set_all(self, on: bool) -> None:
        if self.registry is not None:
            self.registry.set_all_visible(on)
            self.rebuild()
            self.changed.emit()

    def _cell_clicked(self, row: int, col: int) -> None:
        if col != 1 or self.registry is None or row >= len(self.registry):
            return
        cur = QColor(self.registry.channels[row].color)
        col_new = QColorDialog.getColor(cur, self, "Channel colour")
        if col_new.isValid():
            self.registry.update(row, color=col_new.name())
            self.rebuild()
            self.changed.emit()

    def _restore(self, item: QTableWidgetItem, text: str) -> None:
        self._building = True
        try:
            item.setText(text)
        finally:
            self._building = False

    def _item_changed(self, item: QTableWidgetItem) -> None:
        # No rebuild here: replacing items from inside their own change signal is unsafe.
        if self._building or self.registry is None:
            return
        r, c, reg = item.row(), item.column(), self.registry
        if r >= len(reg):
            return
        ch = reg.channels[r]
        if c == 0:
            reg.update(r, visible=item.checkState() == Qt.CheckState.Checked)
        elif c == 2:
            name = item.text().strip() or ch.key
            if name != item.text():
                self._restore(item, name)
            reg.update(r, name=name)
        elif c == 3:
            reg.update(r, unit=item.text().strip())
        elif c in (4, 5):
            field = "gain" if c == 4 else "offset"
            bad = item.text()
            try:
                val = float(bad)
                if val != val or abs(val) == float("inf") or (c == 4 and val == 0):
                    raise ValueError
            except ValueError:
                self._restore(item, f"{getattr(ch, field):g}")
                self.message.emit(f"Invalid {field}: {bad!r} (gain must be a non-zero number)")
                return
            reg.update(r, **{field: val})
        self.changed.emit()
