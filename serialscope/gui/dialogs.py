"""Small dialogs: simulator settings, spectrum, text viewer."""
from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QHBoxLayout,
                               QLabel, QPlainTextEdit, QSpinBox, QVBoxLayout)

from ..acquisition import SimConfig
from ..analysis import spectrum
from . import theme
from .theme import C


class SimulatorDialog(QDialog):
    def __init__(self, cfg: SimConfig, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Simulator settings")
        form = QFormLayout(self)
        self.rate = QDoubleSpinBox(); self.rate.setRange(1, 2000); self.rate.setDecimals(1); self.rate.setSuffix(" Hz")
        self.chan = QSpinBox(); self.chan.setRange(1, 8)
        self.noise = QDoubleSpinBox(); self.noise.setRange(0, 1); self.noise.setSingleStep(0.01); self.noise.setDecimals(3)
        self.spike = QDoubleSpinBox(); self.spike.setRange(0, 1); self.spike.setSingleStep(0.005); self.spike.setDecimals(3)
        self.garb = QDoubleSpinBox(); self.garb.setRange(0, 1); self.garb.setSingleStep(0.01); self.garb.setDecimals(3)
        self.fmt = QComboBox(); self.fmt.addItems(["csv", "kv", "json"])
        self.seed = QSpinBox(); self.seed.setRange(0, 2**31 - 1)
        self.rate.setValue(cfg.rate_hz); self.chan.setValue(cfg.channels); self.noise.setValue(cfg.noise)
        self.spike.setValue(cfg.spike_prob); self.garb.setValue(cfg.garbage_rate)
        self.fmt.setCurrentText(cfg.fmt); self.seed.setValue(cfg.seed)
        form.addRow("Sample rate", self.rate)
        form.addRow("Channels", self.chan)
        form.addRow("Noise (fraction of amplitude)", self.noise)
        form.addRow("Spike probability (ACCEL channel)", self.spike)
        form.addRow("Malformed/text line probability", self.garb)
        form.addRow("Line format", self.fmt)
        form.addRow("Random seed (same seed = same data)", self.seed)
        form.addRow(QLabel("Applies the next time the simulator is started."))
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        form.addRow(bb)

    def config(self) -> SimConfig:
        return SimConfig.from_dict(dict(rate_hz=self.rate.value(), channels=self.chan.value(), noise=self.noise.value(),
                                        spike_prob=self.spike.value(), garbage_rate=self.garb.value(),
                                        fmt=self.fmt.currentText(), seed=self.seed.value()))


class SpectrumDialog(QDialog):
    """Amplitude spectrum of one channel over a fixed time range (Hann window, uniform resampling)."""

    def __init__(self, t: np.ndarray, v: np.ndarray, names: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Spectrum (visible window)")
        self.resize(720, 460)
        self.t, self.v = t, v
        self.combo = QComboBox(); self.combo.addItems(names)
        self.info = QLabel(""); self.info.setFont(theme.mono_font(9)); self.info.setWordWrap(True)
        self.plot = pg.PlotWidget(); self.plot.setBackground(C["plot_bg"]); self.plot.showGrid(x=True, y=True, alpha=0.25)
        self.plot.setLabel("bottom", "Frequency [Hz]"); self.plot.setLabel("left", "Amplitude")
        self.curve = self.plot.plot([], [], pen=pg.mkPen(C["green"], width=1.2))
        top = QHBoxLayout(); top.addWidget(QLabel("Channel")); top.addWidget(self.combo, 1)
        lay = QVBoxLayout(self); lay.addLayout(top); lay.addWidget(self.plot, 1); lay.addWidget(self.info)
        self.combo.currentIndexChanged.connect(self._compute)
        self._compute()

    def _compute(self) -> None:
        i = self.combo.currentIndex()
        try:
            sp = spectrum(self.t, self.v[:, i])
        except ValueError as exc:
            self.curve.setData([], [])
            self.info.setText(str(exc))
            return
        self.curve.setData(sp.freq, sp.amp)
        msg = (f"Peak {sp.peak_hz:.4g} Hz, amplitude {sp.peak_amp:.4g} (raw units).  Effective rate {sp.fs:.4g} Hz, "
               f"Nyquist {sp.fs / 2:.4g} Hz.")
        if sp.dt_cv > 0.3:
            msg += (f"\nWARNING: sample spacing is irregular (std/median = {sp.dt_cv:.2f}); timestamps are host arrival "
                    f"times, so the spectrum is approximate.")
        self.info.setText(msg)


class TextDialog(QDialog):
    def __init__(self, title: str, text: str, parent=None, mono: bool = True, size=(560, 420)):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(*size)
        box = QPlainTextEdit(text)
        box.setReadOnly(True)
        if mono:
            box.setFont(theme.mono_font(9))
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self); lay.addWidget(box); lay.addWidget(bb)
