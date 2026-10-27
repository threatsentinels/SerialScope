"""Waveform view. Plots any source exposing .t, .v, .n, .revision (live buffer, session, snapshot).

Rendering is incremental: a redraw happens only when the source revision, the view range or
the channel style changed. Each redraw touches only the visible window and decimates it.
"""
from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (QComboBox, QFrame, QHBoxLayout, QLabel, QToolButton, QVBoxLayout, QWidget)

from ..analysis import decimate_minmax, parse_duration
from ..config import MAX_PLOT_POINTS
from . import theme
from .theme import C

WINDOW_PRESETS = ["1 s", "2 s", "5 s", "10 s", "30 s", "1 min", "5 min", "All"]


class PlotView(QWidget):
    interacted = Signal()        # user panned/zoomed manually
    cursors_moved = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.source = None
        self.registry = None
        self.head_time: float | None = None   # playback position (None = newest sample)
        self.follow = True
        self.window_s: float | None = 10.0
        self._curves: list = []
        self._dirty = True
        self._busy = False
        self._last_rev = -1
        self._last_reg_version = -1

        # ---- actions (shared with the menus) ----
        def act(text, checked=None, tip="", shortcut=None):
            a = QAction(text, self)
            a.setToolTip(tip or text)
            if checked is not None:
                a.setCheckable(True)
                a.setChecked(checked)
            if shortcut:
                a.setShortcut(shortcut)
            return a
        self.act_follow = act("Follow", True, "Keep the newest data in view (F7)", "F7")
        self.act_auto_y = act("Auto Y", True, "Autoscale the vertical axis to visible data")
        self.act_grid = act("Grid", True, "Show grid")
        self.act_cursors = act("Cursors", False, "Show measurement cursors A/B (F9)", "F9")
        self.act_zoombox = act("Zoom box", False, "Left-drag draws a zoom box instead of panning")
        self.act_fit = act("Fit", None, "Fit all visible data (Ctrl+0)", "Ctrl+0")
        self.act_zoom_in = act("Zoom in (time)", None, "Halve the time span (Ctrl+=)", "Ctrl+=")
        self.act_zoom_out = act("Zoom out (time)", None, "Double the time span (Ctrl+-)", "Ctrl+-")
        self.act_follow.toggled.connect(self._follow_toggled)
        self.act_auto_y.toggled.connect(lambda _: self.invalidate())
        self.act_grid.toggled.connect(self._grid_toggled)
        self.act_cursors.toggled.connect(self._cursors_toggled)
        self.act_zoombox.toggled.connect(self._mouse_mode)
        self.act_fit.triggered.connect(self.fit)
        self.act_zoom_in.triggered.connect(lambda: self.zoom_x(0.5))
        self.act_zoom_out.triggered.connect(lambda: self.zoom_x(2.0))

        # ---- toolbar row ----
        bar = QHBoxLayout()
        bar.setContentsMargins(4, 2, 4, 2)
        bar.setSpacing(4)
        bar.addWidget(QLabel("Window"))
        self.window_combo = QComboBox()
        self.window_combo.setEditable(True)
        self.window_combo.addItems(WINDOW_PRESETS)
        self.window_combo.setCurrentText("10 s")
        self.window_combo.setFixedWidth(78)
        self.window_combo.setToolTip("Visible time window (type e.g. 45 s or 2 min, or All)")
        self.window_combo.activated.connect(lambda _: self._window_edited())
        self.window_combo.lineEdit().editingFinished.connect(self._window_edited)
        bar.addWidget(self.window_combo)
        for a in (self.act_follow, self.act_auto_y, self.act_grid, self.act_cursors, self.act_zoombox,
                  self.act_zoom_out, self.act_zoom_in, self.act_fit):
            b = QToolButton()
            b.setDefaultAction(a)
            bar.addWidget(b)
        bar.addStretch(1)
        self.readout = QLabel("")
        self.readout.setFont(theme.mono_font(9))
        self.readout.setStyleSheet(f"color: {C['muted']};")
        bar.addWidget(self.readout)

        # ---- plot ----
        self.plot = pg.PlotWidget()
        self.plot.setBackground(C["plot_bg"])
        self.plot.showGrid(x=True, y=True, alpha=0.25)
        self.plot.setMenuEnabled(False)
        self.plot.setDownsampling(auto=False)
        self.plot.setClipToView(False)
        pi = self.plot.getPlotItem()
        pi.hideButtons()
        for ax in ("bottom", "left"):
            a = pi.getAxis(ax)
            a.setPen(pg.mkPen(C["border"]))
            a.setTextPen(pg.mkPen(C["muted"]))
            a.setTickFont(theme.mono_font(8))
        pi.getAxis("bottom").enableAutoSIPrefix(False)
        pi.setLabel("bottom", "Time [s]", color=C["muted"])
        self.legend = pi.addLegend(offset=(8, 8), labelTextColor=C["text"],
                                   brush=pg.mkBrush(18, 20, 23, 200), pen=pg.mkPen(C["border"]))
        self.vb = pi.getViewBox()
        self.vb.setMouseMode(pg.ViewBox.PanMode)
        self.vb.sigRangeChanged.connect(self._range_changed)
        self.vb.sigRangeChangedManually.connect(self._manual_change)

        self.cur_a = self._make_cursor("A", C["cyan"])
        self.cur_b = self._make_cursor("B", C["amber"])
        self.trig_line = pg.InfiniteLine(angle=90, movable=False,
                                         pen=pg.mkPen(C["amber"], width=1, style=Qt.PenStyle.DashLine))
        for ln in (self.cur_a, self.cur_b, self.trig_line):
            ln.setVisible(False)
            ln.setZValue(50)
            self.plot.addItem(ln, ignoreBounds=True)
        self.plot.scene().sigMouseMoved.connect(self._mouse_moved)

        self.frame = QFrame()
        self.frame.setObjectName("plotFrame")
        fl = QVBoxLayout(self.frame)
        fl.setContentsMargins(2, 2, 2, 2)
        fl.addWidget(self.plot)
        self.set_frame_color(C["border"])
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addLayout(bar)
        lay.addWidget(self.frame, 1)

    # ---- construction helpers ----
    def _make_cursor(self, label: str, color: str) -> pg.InfiniteLine:
        ln = pg.InfiniteLine(pos=0, angle=90, movable=True, pen=pg.mkPen(color, width=1),
                             hoverPen=pg.mkPen(color, width=2), label=label,
                             labelOpts={"position": 0.96, "color": color, "movable": False})
        ln.sigPositionChanged.connect(lambda *_: self.cursors_moved.emit())
        return ln

    def set_frame_color(self, color: str) -> None:
        """Border colour tells the mode at a glance: green live, amber paused, cyan playback."""
        self.frame.setStyleSheet(f"QFrame#plotFrame {{ border: 2px solid {color}; }}")

    # ---- source / style ----
    def set_source(self, source, registry, head_time: float | None = None) -> None:
        self.source, self.registry, self.head_time = source, registry, head_time
        self._last_rev = -1
        self._last_reg_version = -1   # force curves to be rebuilt for the new registry
        self._dirty = True

    def invalidate(self) -> None:
        self._dirty = True

    def rebuild_curves(self) -> None:
        for c in self._curves:
            self.plot.removeItem(c)
            self.legend.removeItem(c)
        self._curves = []
        reg = self.registry
        if reg is None:
            return
        for ch in reg.channels:
            curve = self.plot.plot([], [], pen=pg.mkPen(ch.color, width=1.4), name=ch.name,
                                   skipFiniteCheck=True)
            curve.setVisible(ch.visible)
            self._curves.append(curve)
        self._last_reg_version = reg.version
        self._dirty = True

    # ---- user controls ----
    def _window_edited(self) -> None:
        try:
            self.window_s = parse_duration(self.window_combo.currentText())
        except ValueError:
            self.readout.setText("invalid window - use e.g. 10 s, 2 min or All")
            return
        self.act_follow.setChecked(True)
        self.invalidate()

    def set_window_text(self, text: str) -> None:
        self.window_combo.setCurrentText(text)
        self._window_edited()

    def _follow_toggled(self, on: bool) -> None:
        self.follow = on
        self.invalidate()

    def _grid_toggled(self, on: bool) -> None:
        self.plot.showGrid(x=on, y=on, alpha=0.25)

    def _mouse_mode(self, zoombox: bool) -> None:
        self.vb.setMouseMode(pg.ViewBox.RectMode if zoombox else pg.ViewBox.PanMode)

    def _cursors_toggled(self, on: bool) -> None:
        if on:
            x0, x1 = self.vb.viewRange()[0]
            self.cur_a.setValue(x0 + (x1 - x0) / 3)
            self.cur_b.setValue(x0 + 2 * (x1 - x0) / 3)
        self.cur_a.setVisible(on)
        self.cur_b.setVisible(on)
        self.cursors_moved.emit()

    def cursor_times(self) -> tuple[float, float] | None:
        if not self.act_cursors.isChecked():
            return None
        return float(self.cur_a.value()), float(self.cur_b.value())

    def show_trigger(self, t: float | None) -> None:
        if t is None:
            self.trig_line.setVisible(False)
        else:
            self.trig_line.setValue(t)
            self.trig_line.setVisible(True)

    def visible_range(self) -> tuple[float, float]:
        x0, x1 = self.vb.viewRange()[0]
        return float(x0), float(x1)

    def set_xrange(self, x0: float, x1: float) -> None:
        self.act_follow.setChecked(False)
        self._set_range(x=(x0, x1))

    def fit(self) -> None:
        src = self.source
        if src is None or src.n == 0:
            return
        self.act_follow.setChecked(False)
        self.act_auto_y.setChecked(True)
        t = src.t
        end = t[-1] if self.head_time is None else min(self.head_time, t[-1])
        self._set_range(x=(float(t[0]), max(float(end), float(t[0]) + 1e-3)))
        self.invalidate()

    def zoom_x(self, factor: float) -> None:
        x0, x1 = self.visible_range()
        if self.follow:                       # keep the newest edge fixed while following
            span = max(1e-3, (x1 - x0) * factor)
            self.window_s = span
            self.window_combo.setCurrentText(f"{span:.3g} s")
        else:
            c, half = (x0 + x1) / 2, max(5e-4, (x1 - x0) * factor / 2)
            self._set_range(x=(c - half, c + half))
        self.invalidate()

    # ---- signals from the view box ----
    def _range_changed(self, *_):
        if not self._busy:
            self._dirty = True

    def _manual_change(self, *_):
        self.act_follow.setChecked(False)
        self.act_auto_y.setChecked(False)
        self.interacted.emit()

    def _mouse_moved(self, pos) -> None:
        if self.plot.sceneBoundingRect().contains(pos):
            p = self.vb.mapSceneToView(pos)
            self.readout.setText(f"t={p.x():.4f} s   y={p.y():.5g}")
        else:
            self.readout.setText("")

    def _set_range(self, x=None, y=None) -> None:
        self._busy = True
        try:
            if x is not None:
                self.vb.setXRange(x[0], x[1], padding=0)
            if y is not None:
                self.vb.setYRange(y[0], y[1], padding=0)
        finally:
            self._busy = False

    # ---- drawing ----
    def refresh(self) -> None:
        src, reg = self.source, self.registry
        if src is None or reg is None:
            return
        if reg.version != self._last_reg_version or len(self._curves) != len(reg):
            self.rebuild_curves()
        rev = src.revision
        if rev == self._last_rev and not self._dirty:
            return
        self._last_rev, self._dirty = rev, False
        n = src.n
        if n == 0:
            for c in self._curves:
                c.setData([], [])
            return
        t, v = src.t, src.v
        head = float(t[-1]) if self.head_time is None else self.head_time
        if self.follow:
            x1 = head
            x0 = float(t[0]) if self.window_s is None else head - self.window_s
            if self.window_s is None and x1 <= x0:
                x1 = x0 + 1e-3
            self._set_range(x=(x0, x1))
        else:
            x0, x1 = self.visible_range()
        pad = 0.02 * (x1 - x0)
        i0 = int(np.searchsorted(t, x0 - pad, "left"))
        i1 = int(np.searchsorted(t, x1 + pad, "right"))
        xs = t[i0:i1]
        lo, hi = np.inf, -np.inf
        ncols = v.shape[1]
        for ci, curve in enumerate(self._curves):
            ch = reg.channels[ci]
            if not ch.visible or ci >= ncols or len(xs) == 0:
                curve.setData([], [])
                continue
            y = v[i0:i1, ci]
            ok = np.isfinite(y)
            x, y = xs[ok], y[ok]
            if len(x) == 0:
                curve.setData([], [])
                continue
            x, y = decimate_minmax(x, y, MAX_PLOT_POINTS)
            y = reg.scale(ci, y)
            curve.setData(x, y, skipFiniteCheck=True)
            inview = y[(x >= x0) & (x <= x1)]
            if inview.size:
                lo, hi = min(lo, float(inview.min())), max(hi, float(inview.max()))
        if self.auto_y_enabled() and np.isfinite(lo) and np.isfinite(hi):
            m = (hi - lo) * 0.08 or max(abs(hi) * 0.1, 0.5)
            self._set_range(y=(lo - m, hi + m))

    def auto_y_enabled(self) -> bool:
        return self.act_auto_y.isChecked()
 