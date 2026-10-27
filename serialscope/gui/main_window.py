"""Main window: wires acquisition, pipeline, plot, console and panels together.

Threads: acquisition runs in AcquisitionWorker (pure Python thread). The GUI thread polls it
on a fixed 20 ms timer (always, regardless of display state) and renders on a separate,
configurable timer. Pausing the display swaps the plotted source for a snapshot copy;
ingestion, recording and triggers are not affected.
"""
from __future__ import annotations

import json
import logging
import shutil
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import numpy as np
from PySide6.QtCore import QSettings, QStandardPaths, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QActionGroup, QDesktopServices
from PySide6.QtWidgets import (QApplication, QComboBox, QDockWidget, QDoubleSpinBox, QFileDialog, QLabel,
                               QMainWindow, QMessageBox, QSlider, QToolBar, QToolButton, QWidget)

from .. import __version__
from ..acquisition import (AcquisitionWorker, SerialSettings, SerialSource, SimConfig, SimulatorSource, list_ports)
from ..channels import ChannelRegistry
from ..config import APP_NAME
from ..logging_setup import log_dir
from ..pipeline import Pipeline
from ..session import Session, export_csv, load_session, write_session
from ..trigger import Trigger
from . import theme
from .channel_panel import ChannelPanel
from .console_pane import MAX_BLOCKS, ConsolePane
from .dialogs import SimulatorDialog, SpectrumDialog, TextDialog
from .measure_panel import MeasurePanel
from .plot_view import PlotView
from .theme import C, chip_style

log = logging.getLogger(__name__)
SIM = "__simulator__"
PARITY = {"None": "N", "Even": "E", "Odd": "O", "Mark": "M", "Space": "S"}
SHORTCUTS = """Ctrl+K / Ctrl+Shift+K   Connect / Disconnect
F5                      Run (connect, return to live, or resume display)
F6                      Pause / resume display (acquisition continues)
Ctrl+R / Ctrl+Shift+R   Start / stop recording
F8                      Play / pause playback
Ctrl+O                  Open session
Ctrl+S                  Save recording / session as...
Ctrl+E / Ctrl+Shift+E   Export CSV (session or recording / live buffer)
Ctrl+L                  Clear console
Ctrl+Shift+L            Clear live data
Ctrl+F                  Find in console
F7                      Follow live data
F9                      Cursors A/B
Ctrl+0                  Fit
Ctrl+= / Ctrl+-         Zoom time axis in / out
F4                      Arm / disarm trigger
Ctrl+Q                  Exit
Mouse: drag = pan, wheel = zoom, right-drag = scale axes."""


def hms(sec: float) -> str:
    sec = max(0.0, sec)
    return f"{int(sec // 3600):02d}:{int(sec % 3600 // 60):02d}:{sec % 60:04.1f}"


def set_chip(label: QLabel, text: str, color: str, filled: bool = False) -> None:
    label.setText(text)
    label.setStyleSheet(chip_style(color, filled))


class MainWindow(QMainWindow):
    def __init__(self, settings: QSettings):
        super().__init__()
        self.settings = settings
        self.prefs: dict = self._load_json("channel_prefs", {})
        self.registry = ChannelRegistry(self.prefs)
        self.pipeline = Pipeline(self.registry)
        self.sim_config = SimConfig.from_dict(self._load_json("sim", {}))
        self.worker: AcquisitionWorker | None = None
        self.conn_desc, self.conn_meta, self.conn_is_sim = "", {}, False
        self.wstats = dict(bytes_rx=0, tx=0, dropped=0, overlong=0, decode=0)
        self.display_paused = False
        self.snapshot: Session | None = None
        self.trigger_frozen = False
        self.pending_error: str | None = None
        self.last_recording: Path | None = None
        self.rec_started = 0.0
        self.playback: Session | None = None
        self.play_registry: ChannelRegistry | None = None
        self.play_path: Path | None = None
        self.play_head = 0.0
        self.playing = False
        self.play_speed = 1.0
        self._play_clock = 0.0
        self._pb_log_n = 0
        self._panel_ver = -1
        self._trig_ver = -1

        self.setWindowTitle(APP_NAME)
        self.resize(1480, 900)
        self.setMinimumSize(980, 620)
        self.plot = PlotView()
        self.setCentralWidget(self.plot)
        self.console = ConsolePane()
        self.console.provider = self._console_entries
        self.channels = ChannelPanel()
        self.measure = MeasurePanel()
        self._build_actions()
        self._build_toolbars()
        self._build_docks()
        self._build_menus()
        self._build_statusbar()
        self._wire()
        self._default_state = self.saveState(1)
        self._restore_settings()

        self.measure_timer = QTimer(self)
        self.measure_timer.setSingleShot(True)
        self.measure_timer.timeout.connect(self._refresh_measure)
        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(20)
        self.poll_timer.timeout.connect(self._poll)
        self.render_timer = QTimer(self)
        self.render_timer.timeout.connect(self._render)
        self.status_timer = QTimer(self)
        self.status_timer.setInterval(250)
        self.status_timer.timeout.connect(self._status_tick)
        self._set_rate(int(self._s("rate_hz", 30)))
        for t in (self.poll_timer, self.render_timer, self.status_timer):
            t.start()
        self._refresh_ports(select=self._s("port", SIM))
        self._apply_view()
        self._update_ui()
        self.say("Ready. Pick 'Simulator' or a serial port and press Connect (Ctrl+K), or open a session (Ctrl+O).")

    # ------------------------------------------------------------------ settings
    def _s(self, key, default=None):
        v = self.settings.value(key, default)
        return default if v is None else v

    def _load_json(self, key, default):
        try:
            return json.loads(self.settings.value(key, "") or "null") or default
        except (ValueError, TypeError):
            return default

    def _restore_settings(self) -> None:
        g, st = self.settings.value("geometry"), self.settings.value("state")
        if g is not None:
            self.restoreGeometry(g)
        if st is not None:
            self.restoreState(st, 1)
        self.baud_combo.setCurrentText(str(self._s("baud", "115200")))
        self.bits_combo.setCurrentText(str(self._s("databits", "8")))
        self.parity_combo.setCurrentText(str(self._s("parity", "None")))
        self.stop_combo.setCurrentText(str(self._s("stopbits", "1")))
        self.plot.set_window_text(str(self._s("window", "10 s")))
        self.plot.act_grid.setChecked(str(self._s("grid", "true")).lower() == "true")
        self.plot.act_follow.setChecked(True)
        
        theme_name = self._s("theme", "Dark")
        is_dark = theme_name == "Dark"
        self.a_theme.setChecked(is_dark)
        self.a_theme.setText("Dark Theme" if is_dark else "Light Theme")
        theme.apply_theme(QApplication.instance(), theme_name)

    def _save_settings(self) -> None:
        s = self.settings
        s.setValue("geometry", self.saveGeometry())
        s.setValue("state", self.saveState(1))
        s.setValue("port", self.port_combo.currentData() or SIM)
        s.setValue("baud", self.baud_combo.currentText())
        s.setValue("databits", self.bits_combo.currentText())
        s.setValue("parity", self.parity_combo.currentText())
        s.setValue("stopbits", self.stop_combo.currentText())
        s.setValue("window", self.plot.window_combo.currentText())
        s.setValue("grid", self.plot.act_grid.isChecked())
        s.setValue("rate_hz", self._rate)
        s.setValue("sim", json.dumps(asdict(self.sim_config)))
        s.setValue("theme", "Dark" if self.a_theme.isChecked() else "Light")
        for reg in (self.registry, self.play_registry):
            if reg is not None:
                reg.export_prefs()
        s.setValue("channel_prefs", json.dumps(self.prefs))
        s.sync()

    def recordings_dir(self) -> Path:
        custom = self._s("recordings_dir", "")
        if custom:
            return Path(custom)
        docs = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)
        return Path(docs or Path.home()) / "SerialScope" / "Recordings"

    # ------------------------------------------------------------------ construction
    def _act(self, text, slot=None, shortcut=None, tip="", checkable=False) -> QAction:
        a = QAction(text, self)
        if slot:
            a.triggered.connect(slot)
        if shortcut:
            a.setShortcut(shortcut)
        if tip:
            a.setToolTip(tip)
        a.setCheckable(checkable)
        return a

    def _build_actions(self) -> None:
        A = self._act
        self.a_open = A("Open Session...", self._open_session, "Ctrl+O")
        self.a_save_as = A("Save Recording / Session As...", self._save_as, "Ctrl+S")
        self.a_save_live = A("Save Live Buffer As...", self._save_live)
        self.a_csv = A("Export CSV (session / last recording)...", self._export_csv_session, "Ctrl+E")
        self.a_csv_live = A("Export CSV (live buffer)...", self._export_csv_live, "Ctrl+Shift+E")
        self.a_open_rec = A("Open Recordings Folder", lambda: self._open_folder(self.recordings_dir()))
        self.a_quit = A("Exit", self.close, "Ctrl+Q")
        self.a_clear_console = A("Clear Console", lambda: self._clear_console(), "Ctrl+L")
        self.a_clear_data = A("Clear Live Data", self._clear_data, "Ctrl+Shift+L")
        self.a_find = A("Find in Console", lambda: self.console.focus_find(), "Ctrl+F")
        self.a_connect = A("Connect", self._connect, "Ctrl+K", "Open the selected port (or start the simulator)")
        self.a_disconnect = A("Disconnect", self._disconnect, "Ctrl+Shift+K")
        self.a_refresh = A("Refresh Ports", lambda: self._refresh_ports())
        self.a_sim_cfg = A("Simulator Settings...", self._sim_settings)
        self.a_run = A("Run", self._run, "F5", "Connect if needed, return to live view, resume display")
        self.a_pause = A("Pause Display", self._toggle_pause, "F6",
                         "Freeze the display. Acquisition and recording continue.", True)
        self.a_rec = A("Record", self._start_recording, "Ctrl+R", "Stream incoming data to a session file")
        self.a_stop_rec = A("Stop Recording", self._stop_recording, "Ctrl+Shift+R")
        self.a_trig = A("Arm Trigger", self._toggle_trigger, "F4", "Capture around a threshold crossing", True)
        self.a_play = A("Play", self._toggle_play, "F8")
        self.a_start = A("|<", lambda: self._seek_to(0.0), tip="Go to start")
        self.a_end = A(">|", lambda: self._seek_to(1.0), tip="Go to end")
        self.a_spectrum = A("Spectrum...", self._show_spectrum)
        self.a_info = A("Session Info...", self._show_info)
        self.a_logdir = A("Open Log Folder", lambda: self._open_folder(log_dir()))
        self.a_keys = A("Keyboard Shortcuts", lambda: TextDialog("Keyboard shortcuts", SHORTCUTS, self).exec())
        self.a_about = A("About", self._about)
        self.a_reset_layout = A("Reset Layout", lambda: self.restoreState(self._default_state, 1))
        self.a_theme = A("Dark Theme", self._toggle_theme, checkable=True)
        self.a_theme.setChecked(self._s("theme", "Dark") == "Dark")

    def _build_toolbars(self) -> None:
        def tb(title, name):
            t = self.addToolBar(title)
            t.setObjectName(name)
            t.setMovable(True)
            return t

        def btn(bar, action):
            b = QToolButton()
            b.setDefaultAction(action)
            bar.addWidget(b)
            return b

        c = self.tb_conn = tb("Connection", "tb_connection")
        c.addWidget(QLabel("Port "))
        self.port_combo = QComboBox(); self.port_combo.setMinimumWidth(230)
        c.addWidget(self.port_combo)
        btn(c, self.a_refresh)
        c.addSeparator()
        c.addWidget(QLabel("Baud "))
        self.baud_combo = QComboBox(); self.baud_combo.setEditable(True); self.baud_combo.setFixedWidth(88)
        self.baud_combo.addItems(["300", "1200", "2400", "4800", "9600", "19200", "38400", "57600", "115200",
                                  "230400", "460800", "921600", "1000000", "2000000"])
        self.baud_combo.setCurrentText("115200")
        c.addWidget(self.baud_combo)
        self.bits_combo = QComboBox(); self.bits_combo.addItems(["8", "7", "6", "5"])
        self.parity_combo = QComboBox(); self.parity_combo.addItems(list(PARITY))
        self.stop_combo = QComboBox(); self.stop_combo.addItems(["1", "1.5", "2"])
        for lbl, w in (("Data ", self.bits_combo), ("Parity ", self.parity_combo), ("Stop ", self.stop_combo)):
            c.addWidget(QLabel(" " + lbl)); c.addWidget(w)
        c.addSeparator()
        btn(c, self.a_connect); btn(c, self.a_disconnect)
        self.chip_conn = QLabel(); self.chip_conn.setObjectName("chip")
        c.addWidget(self.chip_conn)

        self.addToolBarBreak()
        a = self.tb_acq = tb("Acquisition", "tb_acquisition")
        for act in (self.a_run, self.a_pause):
            btn(a, act)
        a.addSeparator()
        for act in (self.a_rec, self.a_stop_rec):
            btn(a, act)
        self.chip_rec_tb = QLabel(); self.chip_rec_tb.setObjectName("chip"); self.chip_rec_tb.setFont(theme.mono_font(9))
        a.addWidget(self.chip_rec_tb)
        self.lbl_acq = QLabel(); self.lbl_acq.setFont(theme.mono_font(9))
        a.addWidget(QLabel("  Elapsed ")); a.addWidget(self.lbl_acq)
        self.chip_mode_tb = QLabel(); self.chip_mode_tb.setObjectName("chip"); self.chip_mode_tb.setFont(theme.mono_font(9))
        a.addWidget(QLabel("  ")); a.addWidget(self.chip_mode_tb)

        p = self.tb_play = tb("Playback", "tb_playback")
        btn(p, self.a_open)
        for act in (self.a_start, self.a_play, self.a_end):
            btn(p, act)
        self.speed_combo = QComboBox()
        for s in ("0.25x", "0.5x", "1x", "2x", "5x", "10x", "50x"):
            self.speed_combo.addItem(s, float(s[:-1]))
        self.speed_combo.setCurrentText("1x")
        self.speed_combo.currentIndexChanged.connect(lambda _: setattr(self, "play_speed", self.speed_combo.currentData()))
        p.addWidget(self.speed_combo)
        self.pb_slider = QSlider(Qt.Orientation.Horizontal); self.pb_slider.setRange(0, 1000); self.pb_slider.setFixedWidth(180)
        p.addWidget(self.pb_slider)
        self.lbl_pos = QLabel("-"); self.lbl_pos.setFont(theme.mono_font(9)); self.lbl_pos.setMinimumWidth(120)
        p.addWidget(self.lbl_pos)

        self.addToolBarBreak()
        t = self.tb_trig = tb("Trigger", "tb_trigger")
        t.addWidget(QLabel("Trigger  ch "))
        self.trig_ch = QComboBox(); self.trig_ch.setMinimumWidth(100); t.addWidget(self.trig_ch)
        self.trig_edge = QComboBox(); self.trig_edge.addItems(["rising", "falling", "either"]); t.addWidget(self.trig_edge)
        t.addWidget(QLabel(" level "))
        self.trig_level = QDoubleSpinBox(); self.trig_level.setRange(-1e9, 1e9); self.trig_level.setDecimals(4)
        self.trig_level.setToolTip("Compared with the raw parsed value (display gain/offset not applied)")
        t.addWidget(self.trig_level)
        t.addWidget(QLabel(" pre ")); self.trig_pre = QDoubleSpinBox(); self.trig_pre.setRange(0.0, 600); self.trig_pre.setValue(1.0); self.trig_pre.setSuffix(" s"); t.addWidget(self.trig_pre)
        t.addWidget(QLabel(" post ")); self.trig_post = QDoubleSpinBox(); self.trig_post.setRange(0.0, 600); self.trig_post.setValue(1.0); self.trig_post.setSuffix(" s"); t.addWidget(self.trig_post)
        btn(t, self.a_trig)
        self.trig_state = QLabel(" disarmed"); t.addWidget(self.trig_state)
        t.hide()   # secondary tool: shown via Acquisition > Trigger Controls

    def _build_docks(self) -> None:
        def dock(title, name, widget, area):
            d = QDockWidget(title, self)
            d.setObjectName(name)
            d.setWidget(widget)
            self.addDockWidget(area, d)
            return d
        self.console_dock = dock("Serial console", "dock_console", self.console, Qt.DockWidgetArea.BottomDockWidgetArea)
        self.channel_dock = dock("Channels", "dock_channels", self.channels, Qt.DockWidgetArea.RightDockWidgetArea)
        self.measure_dock = dock("Cursors & statistics", "dock_measure", self.measure, Qt.DockWidgetArea.RightDockWidgetArea)
        self.splitDockWidget(self.channel_dock, self.measure_dock, Qt.Orientation.Vertical)
        self.resizeDocks([self.console_dock], [230], Qt.Orientation.Vertical)
        self.resizeDocks([self.channel_dock], [400], Qt.Orientation.Horizontal)

    def _build_menus(self) -> None:
        mb = self.menuBar()
        m = mb.addMenu("&File")
        for a in (self.a_open, self.a_save_as, self.a_save_live):
            m.addAction(a)
        m.addSeparator()
        for a in (self.a_csv, self.a_csv_live):
            m.addAction(a)
        m.addSeparator(); m.addAction(self.a_open_rec); m.addSeparator(); m.addAction(self.a_quit)
        m = mb.addMenu("&Edit")
        for a in (self.a_clear_console, self.a_clear_data, self.a_find):
            m.addAction(a)
        m = mb.addMenu("&View")
        p = self.plot
        for a in (p.act_follow, p.act_auto_y, p.act_grid, p.act_zoombox, p.act_zoom_in, p.act_zoom_out, p.act_fit):
            m.addAction(a)
        m.addSeparator()
        m.addAction(self.a_theme)
        m.addSeparator()
        rate_menu = m.addMenu("Display update rate")
        grp = QActionGroup(self)
        self._rate_actions = {}
        for hz in (5, 10, 20, 30, 60):
            a = QAction(f"{hz} Hz", self, checkable=True)
            a.triggered.connect(lambda _=False, h=hz: self._set_rate(h))
            grp.addAction(a); rate_menu.addAction(a); self._rate_actions[hz] = a
        m.addSeparator()
        for d in (self.console_dock, self.channel_dock, self.measure_dock):
            m.addAction(d.toggleViewAction())
        for tbar in (self.tb_conn, self.tb_acq, self.tb_play, self.tb_trig):
            m.addAction(tbar.toggleViewAction())
        m.addSeparator(); m.addAction(self.a_reset_layout)
        m = mb.addMenu("&Connection")
        for a in (self.a_connect, self.a_disconnect, self.a_refresh, self.a_sim_cfg):
            m.addAction(a)
        m = mb.addMenu("&Acquisition")
        for a in (self.a_run, self.a_pause):
            m.addAction(a)
        m.addSeparator()
        for a in (self.a_rec, self.a_stop_rec):
            m.addAction(a)
        m.addSeparator(); m.addAction(self.tb_trig.toggleViewAction()); m.addAction(self.a_trig)
        m.addSeparator(); m.addAction(self.a_play)
        m = mb.addMenu("A&nalysis")
        for a in (p.act_cursors, self.a_spectrum, self.a_info):
            m.addAction(a)
        m = mb.addMenu("&Tools")
        for a in (self.a_open_rec, self.a_logdir):
            m.addAction(a)
        m = mb.addMenu("&Help")
        for a in (self.a_keys, self.a_about):
            m.addAction(a)

    def _build_statusbar(self) -> None:
        sb = self.statusBar()
        self.chip_source = QLabel(); self.chip_source.setObjectName("chip")
        self.chip_mode = QLabel(); self.chip_mode.setObjectName("chip")
        self.chip_rec = QLabel(); self.chip_rec.setObjectName("chip")
        for w in (self.chip_source, self.chip_mode, self.chip_rec):
            w.setFont(theme.mono_font(9))
            sb.addWidget(w)
        self.msg = QLabel(""); sb.addWidget(self.msg, 1)
        self.counters = QLabel(""); self.counters.setFont(theme.mono_font(9))
        sb.addPermanentWidget(self.counters)

    def _wire(self) -> None:
        self.channels.changed.connect(self._channels_changed)
        self.channels.message.connect(lambda t: self.say(t, "warn"))
        self.console.send_requested.connect(self._send_command)
        self.plot.cursors_moved.connect(self._schedule_measure)
        self.plot.interacted.connect(self._schedule_measure)
        self.pb_slider.valueChanged.connect(self._slider_moved)

    def _toggle_theme(self, checked: bool) -> None:
        theme_name = "Dark" if checked else "Light"
        self.a_theme.setText("Dark Theme" if checked else "Light Theme")
        theme.apply_theme(QApplication.instance(), theme_name)
        self._apply_view()
        self.say(f"Switched to {theme_name.lower()} theme.")

    # ------------------------------------------------------------------ messages
    def say(self, text: str, level: str = "info") -> None:
        color = {"info": C["muted"], "warn": C["amber"], "error": C["red"]}[level]
        self.msg.setStyleSheet(f"color: {color}; padding-left: 8px;")
        self.msg.setText(text)
        self.msg.setToolTip(text)
        (log.error if level == "error" else log.warning if level == "warn" else log.info)(text)

    def _open_folder(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    # ------------------------------------------------------------------ connection
    def _refresh_ports(self, select: str | None = None) -> None:
        cur = select if select is not None else self.port_combo.currentData()
        self.port_combo.clear()
        self.port_combo.addItem("Simulator (no hardware)", SIM)
        for dev, desc in list_ports():
            self.port_combo.addItem(f"{dev}  {desc}", dev)
        i = self.port_combo.findData(cur)
        self.port_combo.setCurrentIndex(max(0, i))
        if select is None:
            self.say(f"{self.port_combo.count() - 1} serial port(s) found.")

    def _connect(self) -> None:
        if self.worker is not None:
            return
        sel = self.port_combo.currentData()
        if sel == SIM:
            src = SimulatorSource(self.sim_config)
            meta = {"source": src.description, "simulator": asdict(self.sim_config)}
        else:
            if not sel:
                QMessageBox.warning(self, "No port", "No serial port selected. Plug in the device and press Refresh, "
                                    "or choose the Simulator.")
                return
            try:
                baud = int(self.baud_combo.currentText().strip())
                if baud <= 0:
                    raise ValueError
            except ValueError:
                QMessageBox.warning(self, "Invalid baud rate", "Enter a positive integer baud rate.")
                return
            ss = SerialSettings(sel, baud, int(self.bits_combo.currentText()), PARITY[self.parity_combo.currentText()],
                                float(self.stop_combo.currentText()))
            src = SerialSource(ss)
            meta = {"source": src.description, "serial": asdict(ss)}
        self._exit_playback()
        self._resume_display()
        self.worker = AcquisitionWorker(src)
        self.conn_desc, self.conn_meta, self.conn_is_sim = src.description, meta, src.is_simulator
        self.wstats = dict(bytes_rx=0, tx=0, dropped=0, overlong=0, decode=0)
        self.pipeline.reset(self.worker.start_unix)
        self.console.clear()
        self.console.note(f"connecting: {src.description}")
        self.worker.start()
        self._apply_view()
        self._update_ui()
        self.say(f"Connecting: {src.description}")

    def _disconnect(self) -> None:
        w = self.worker
        if w is None:
            return
        w.stop()
        w.join(2.0)
        if w.is_alive():
            log.warning("Acquisition thread did not stop within 2 s (driver stuck?); continuing")
        self._poll()
        self._on_closed("stopped by user")

    def _on_closed(self, reason: str) -> None:
        w = self.worker
        if w is None:
            return
        self._poll_data(w)
        self.worker = None
        if self.pipeline.recording:
            self._stop_recording(f"acquisition ended: {reason}")
        tr = self.pipeline.trigger
        if tr is not None and tr.fired_t is not None and not self.trigger_frozen:
            self._freeze_for_trigger()
        self.console.note(f"disconnected ({reason})")
        self.say(f"Disconnected ({reason}). Data stays in the live buffer.")
        self._update_ui()

    def _sim_settings(self) -> None:
        d = SimulatorDialog(self.sim_config, self)
        if d.exec():
            self.sim_config = d.config()

    def _send_command(self, text: str, ending: str) -> None:
        if self.worker is None or self.conn_is_sim:
            self.say("Not connected to a serial port.", "warn")
            return
        self.worker.send((text + ending).encode("utf-8"))
        self.console.note(f"TX: {text}")

    # ------------------------------------------------------------------ polling (acquisition side)
    def _poll(self) -> None:
        w = self.worker
        if w is None:
            return
        self._poll_data(w)
        for kind, text in w.drain_events():
            if kind == "opened":
                self.console.note(f"connected: {text}")
                self.say(f"Connected: {text}")
            elif kind == "warning":
                self.console.note(f"warning: {text}")
                self.say(text, "warn")
            elif kind == "error":
                self.console.note(f"ERROR: {text}")
                self.say(text, "error")
                self.pending_error = text
            elif kind == "closed":
                self._on_closed(text)
                break
        if self.worker is not None and not self.worker.is_alive() and self.worker.events.empty():
            self._on_closed("worker exited")
        if self.pending_error:
            msg, self.pending_error = self.pending_error, None
            QTimer.singleShot(0, lambda: QMessageBox.warning(self, "Serial connection problem", msg))

    def _poll_data(self, w: AcquisitionWorker) -> None:
        recs = w.drain()
        self.wstats.update(bytes_rx=w.bytes_rx, tx=w.tx_bytes, dropped=w.dropped, overlong=w.overlong,
                           decode=w.decode_errors)
        if recs:
            new = self.pipeline.ingest(recs)
            if self.pipeline.rec_error:
                err, self.pipeline.rec_error = self.pipeline.rec_error, None
                self.say(err, "error")
                self.pending_error = err
                self._update_ui()
            if not self.display_paused and self.playback is None:
                self.console.append_lines(new)
            self._check_trigger()

    # ------------------------------------------------------------------ rendering (display side)
    def _set_rate(self, hz: int) -> None:
        self._rate = hz
        self.render_timer.setInterval(max(10, int(1000 / hz)))
        if hz in getattr(self, "_rate_actions", {}):
            self._rate_actions[hz].setChecked(True)

    def _render(self) -> None:
        if self.playback is not None and self.playing:
            self._advance_playback()
        reg = self._view()[1]
        if reg is not None and reg.version != self._panel_ver:
            self._panel_ver = reg.version
            self.channels.rebuild()
            self._schedule_measure()
        self.plot.refresh()

    def _status_tick(self) -> None:
        p, ws = self.pipeline, self.wstats
        w = self.worker
        if w is not None:
            self.lbl_acq.setText(hms(w.now()))
        elif self.playback is None:
            self.lbl_acq.setText(hms(p.last_t) if p.samples else "--:--:--.-")
        if p.recording:
            set_chip(self.chip_rec, f"REC {hms(p.last_t - p.rec_origin)} {p.writer.samples:,} smp", C["red"], True)
            set_chip(self.chip_rec_tb, f"REC {hms(p.last_t - p.rec_origin)}", C["red"], True)
        else:
            set_chip(self.chip_rec, "REC off", C["muted"])
            set_chip(self.chip_rec_tb, "REC off", C["muted"])
        reg = self.registry
        dropped = ws["dropped"] + ws["overlong"] + reg.overflow_values
        self.counters.setText(f"RX {ws['bytes_rx']:,} B   lines {p.lines:,}   samples {p.samples:,}   "
                              f"invalid {p.nonnumeric_lines:,}   dropped {dropped:,}")
        self.counters.setToolTip(
            f"Lines received: {p.lines:,}\nNumeric samples: {p.samples:,}\n"
            f"Invalid = lines without usable numbers: {p.nonnumeric_lines:,}\n"
            f"Dropped: worker queue overflow {ws['dropped']:,}, over-long lines truncated {ws['overlong']:,}, "
            f"values beyond the channel limit {reg.overflow_values:,}\n"
            f"Decode errors (bad UTF-8): {ws['decode']:,}\n"
            f"Live buffer: {p.live.n:,} samples held, {p.live.overwritten:,} older samples released from RAM "
            f"(recording is unaffected)\nConsole lines not drawn (display only): {self.console.skipped:,}\n"
            f"TX bytes: {ws['tx']:,}")
        if self.trig_ver_changed():
            cur = self.trig_ch.currentText()
            self.trig_ch.clear()
            self.trig_ch.addItems([c.name for c in reg.channels])
            self.trig_ch.setCurrentText(cur)
        self._refresh_measure()
        self._update_chips()

    def trig_ver_changed(self) -> bool:
        if self.registry.version != self._trig_ver:
            self._trig_ver = self.registry.version
            return True
        return False

    # ------------------------------------------------------------------ views / modes
    def _view(self):
        if self.playback is not None:
            return self.playback, self.play_registry, self.play_head
        if self.display_paused and self.snapshot is not None:
            return self.snapshot, self.registry, None
        return self.pipeline.live, self.registry, None

    def _console_entries(self):
        if self.playback is not None:
            s = self.playback
            j = int(np.searchsorted(s.log_t, self.play_head, "right"))
            self._pb_log_n = j
            return list(zip(s.log_t[max(0, j - MAX_BLOCKS):j].tolist(), s.log_text[max(0, j - MAX_BLOCKS):j]))
        return list(self.pipeline.log)

    def _apply_view(self) -> None:
        src, reg, head = self._view()
        self.plot.set_source(src, reg, head)
        self.channels.set_registry(reg)
        self._panel_ver = reg.version if reg is not None else -1
        self.console.start_unix = self.playback.start_unix if self.playback else self.pipeline.start_unix
        self.console.frozen = False
        self.console.rerender()
        self.console.frozen = self.display_paused and self.playback is None
        self.plot.set_frame_color(C["cyan"] if self.playback else C["amber"] if self.display_paused
                                  else C["green"] if self.worker else C["border"])
        self._schedule_measure()
        self._update_chips()

    def _update_chips(self) -> None:
        live = self.worker is not None
        if self.playback is not None:
            text, col, fill = f"PLAYBACK {self.play_speed:g}x" if self.playing else "PLAYBACK (stopped)", C["cyan"], True
        elif self.display_paused:
            text, col, fill = ("PAUSED - acquiring" if live else "PAUSED"), C["amber"], True
        elif live:
            text, col, fill = "LIVE", C["green"], True
        else:
            text, col, fill = "IDLE", C["muted"], False
        set_chip(self.chip_mode, text, col, fill)
        set_chip(self.chip_mode_tb, text, col, fill)
        set_chip(self.chip_source, self.conn_desc if live else "no source", C["text"] if live else C["muted"])
        set_chip(self.chip_conn, "CONNECTED" if live else "DISCONNECTED", C["green"] if live else C["muted"], live)
        suffix = f" - {self.play_path.name}" if self.playback is not None and self.play_path else ""
        self.setWindowTitle(f"{APP_NAME} - {text}{suffix}")

    def _update_ui(self) -> None:
        live = self.worker is not None
        rec = self.pipeline.recording
        self.a_connect.setEnabled(not live)
        self.a_disconnect.setEnabled(live)
        for w in (self.port_combo, self.baud_combo, self.bits_combo, self.parity_combo, self.stop_combo):
            w.setEnabled(not live)
        self.a_refresh.setEnabled(not live)
        self.a_sim_cfg.setEnabled(not live)
        self.a_rec.setEnabled(live and not rec)
        self.a_stop_rec.setEnabled(rec)
        self.a_pause.setEnabled(self.playback is None)
        self.a_pause.blockSignals(True)
        self.a_pause.setChecked(self.display_paused)
        self.a_pause.blockSignals(False)
        self.a_pause.setText("Resume Display" if self.display_paused else "Pause Display")
        for a in (self.a_play, self.a_start, self.a_end):
            a.setEnabled(self.playback is not None)
        self.pb_slider.setEnabled(self.playback is not None)
        self.speed_combo.setEnabled(self.playback is not None)
        self.a_play.setText("Pause" if self.playing else "Play")
        has_file = self.playback is not None or self.last_recording is not None
        self.a_save_as.setEnabled(has_file)
        self.a_csv.setEnabled(has_file)
        self.console.set_send_enabled(live and not self.conn_is_sim)
        self.a_trig.setEnabled(self.playback is None)
        self._update_chips()

    # ------------------------------------------------------------------ run / pause / clear
    def _run(self) -> None:
        if self.playback is not None:
            self._exit_playback()
        elif self.worker is None:
            self._connect()
            return
        self._resume_display()
        self.plot.act_follow.setChecked(True)
        self._update_ui()

    def _toggle_pause(self, *_) -> None:
        if self.playback is not None:
            return
        if self.display_paused:
            tr = self.pipeline.trigger
            if tr is not None and tr.fired_t is not None:
                self._rearm_trigger()
            self._resume_display()
            self.plot.act_follow.setChecked(True)
            self.say("Display resumed.")
        else:
            self._freeze_display()
            self.say("Display paused - acquisition and recording continue." if self.worker
                     else "Display paused.")
        self._update_ui()

    def _freeze_display(self) -> None:
        if self.display_paused:
            return
        self.snapshot = self.pipeline.snapshot_session()
        self.display_paused = True
        self._apply_view()

    def _resume_display(self) -> None:
        if not self.display_paused:
            return
        self.display_paused = False
        self.snapshot = None
        self.trigger_frozen = False
        self.plot.show_trigger(None)
        self._apply_view()

    def _clear_console(self) -> None:
        self.console.clear()
        if self.playback is None:
            self.pipeline.log.clear()

    def _clear_data(self) -> None:
        self._resume_display()
        self.pipeline.clear_data()
        self.console.clear()
        self.plot.invalidate()
        self.say("Live buffer cleared" + (" (recording is not affected)." if self.pipeline.recording else "."))

    # ------------------------------------------------------------------ recording
    def _start_recording(self) -> None:
        if self.worker is None or self.pipeline.recording:
            return
        path = self.recordings_dir() / f"session-{datetime.now():%Y%m%d-%H%M%S}.sscope"
        meta = dict(self.conn_meta, acquisition_start_unix=self.pipeline.start_unix, app_version=__version__)
        try:
            self.pipeline.start_recording(path, meta)
        except OSError as exc:
            log.exception("Cannot start recording")
            QMessageBox.critical(self, "Cannot start recording", f"{path}\n\n{exc}")
            return
        self.rec_started = time.time()
        self.say(f"Recording to {path}")
        self.console.note(f"recording started: {path.name}")
        self._update_ui()

    def _stop_recording(self, reason: str = "stopped by user") -> None:
        info = self.pipeline.stop_recording(reason)
        if info:
            self.last_recording = info["path"]
            self.say(f"Recording saved: {info['path']} ({info['samples']:,} samples, {info['lines']:,} lines)")
            self.console.note(f"recording stopped: {info['samples']:,} samples ({reason})")
        self._update_ui()

    # ------------------------------------------------------------------ trigger
    def _toggle_trigger(self, on: bool) -> None:
        if not on:
            self.pipeline.trigger = None
            self.trigger_frozen = False
            self.plot.show_trigger(None)
            self.trig_state.setText(" disarmed")
            return
        idx = self.trig_ch.currentIndex()
        if idx < 0 or idx >= len(self.registry):
            self.a_trig.setChecked(False)
            self.say("No channel to trigger on yet - wait for numeric data.", "warn")
            return
        self.tb_trig.show()
        if self.display_paused:
            self._resume_display()
        self.pipeline.trigger = Trigger(idx, self.trig_level.value(), self.trig_edge.currentText(),
                                        self.trig_pre.value(), self.trig_post.value())
        self.trigger_frozen = False
        self.trig_state.setText(" ARMED")
        self.say(f"Trigger armed on {self.trig_ch.currentText()} ({self.trig_edge.currentText()}, "
                 f"level {self.trig_level.value():g}).")
        self._update_ui()

    def _rearm_trigger(self) -> None:
        tr = self.pipeline.trigger
        if tr is not None:
            self.pipeline.trigger = Trigger(tr.channel, tr.level, tr.edge, tr.pre_s, tr.post_s)
            self.trig_state.setText(" ARMED")

    def _check_trigger(self) -> None:
        tr = self.pipeline.trigger
        if tr is None or tr.fired_t is None or self.trigger_frozen or self.playback is not None:
            return
        if self.pipeline.last_t >= tr.fired_t + tr.post_s:
            self._freeze_for_trigger()

    def _freeze_for_trigger(self) -> None:
        tr = self.pipeline.trigger
        self.trigger_frozen = True
        if not self.display_paused:
            self._freeze_display()
        self.plot.set_xrange(tr.fired_t - tr.pre_s, tr.fired_t + tr.post_s)
        self.plot.show_trigger(tr.fired_t)
        self.trig_state.setText(f" TRIGGERED @ {tr.fired_t:.4f} s (F6 re-arms)")
        self.say(f"Triggered at t={tr.fired_t:.4f} s. Display frozen; acquisition continues. F6 to re-arm.")
        self._update_ui()

    # ------------------------------------------------------------------ playback
    def _open_session(self) -> None:
        start = str(self.recordings_dir()) if self.recordings_dir().exists() else ""
        path, _ = QFileDialog.getOpenFileName(self, "Open session", start,
                                              "SerialScope sessions (*.sscope *.json);;All files (*)")
        if not path:
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            s = load_session(Path(path))
        except (OSError, ValueError, MemoryError) as exc:
            QApplication.restoreOverrideCursor()
            log.exception("Open session failed: %s", path)
            QMessageBox.critical(self, "Could not open session", f"{path}\n\n{exc}")
            return
        QApplication.restoreOverrideCursor()
        if s.n == 0:
            QMessageBox.information(self, "Empty session", "The file contains no numeric samples.")
            return
        self._enter_playback(s, Path(path))
        notes = []
        if s.truncated:
            notes.append("file is incomplete (recording was interrupted) - loaded what could be read")
        if s.bad_records:
            notes.append(f"{s.bad_records} unreadable record(s) skipped")
        self.say(f"Loaded {Path(path).name}: {s.n:,} samples, {len(s.log_text):,} lines, {s.t[-1] - s.t[0]:.2f} s"
                 + (f" - WARNING: {'; '.join(notes)}" if notes else "")
                 + (". Live acquisition continues in the background; press Run (F5) to return."
                    if self.worker else "."), "warn" if notes else "info")

    def _enter_playback(self, s: Session, path: Path) -> None:
        self._resume_display()
        self.playing = False
        self.playback, self.play_path = s, path
        self.play_registry = ChannelRegistry.from_keys(s.channels, self.prefs)
        self.play_head = float(s.t[-1])
        self.plot.act_follow.setChecked(True)
        self._apply_view()
        self._sync_slider()
        self._update_ui()

    def _exit_playback(self) -> None:
        if self.playback is None:
            return
        if self.play_registry is not None:
            self.play_registry.export_prefs()
        self.playing = False
        self.playback = self.play_registry = self.play_path = None
        self._apply_view()
        self._update_ui()

    def _toggle_play(self) -> None:
        s = self.playback
        if s is None:
            return
        if self.playing:
            self.playing = False
        else:
            if self.play_head >= float(s.t[-1]) - 1e-9:
                self._seek_to(0.0)
            self.playing = True
            self._play_clock = time.perf_counter()
        self._update_ui()

    def _advance_playback(self) -> None:
        s = self.playback
        now = time.perf_counter()
        head = self.play_head + (now - self._play_clock) * self.play_speed
        self._play_clock = now
        end = float(s.t[-1])
        if head >= end:
            head, self.playing = end, False
            self._update_ui()
        self._set_head(head)

    def _set_head(self, head: float) -> None:
        s = self.playback
        old_n = self._pb_log_n
        self.play_head = head
        self.plot.head_time = head
        self.plot.invalidate()
        j = int(np.searchsorted(s.log_t, head, "right"))
        if j < old_n:
            self.console.rerender()
        elif j > old_n:
            self._pb_log_n = j
            self.console.append_lines(list(zip(s.log_t[old_n:j].tolist(), s.log_text[old_n:j])))
        self._sync_slider()

    def _seek_to(self, frac: float) -> None:
        s = self.playback
        if s is not None:
            self._set_head(float(s.t[0]) + frac * float(s.t[-1] - s.t[0]))
            self._play_clock = time.perf_counter()

    def _slider_moved(self, value: int) -> None:
        self._seek_to(value / 1000.0)

    def _sync_slider(self) -> None:
        s = self.playback
        if s is None:
            self.lbl_pos.setText("-")
            return
        t0, t1 = float(s.t[0]), float(s.t[-1])
        frac = 0.0 if t1 <= t0 else (self.play_head - t0) / (t1 - t0)
        self.pb_slider.blockSignals(True)
        self.pb_slider.setValue(int(round(frac * 1000)))
        self.pb_slider.blockSignals(False)
        self.lbl_pos.setText(f"{self.play_head - t0:.2f} / {t1 - t0:.2f} s")

    # ------------------------------------------------------------------ measurement / analysis
    def _schedule_measure(self) -> None:
        if not self.measure_timer.isActive():
            self.measure_timer.start(40)

    def _refresh_measure(self) -> None:
        if not self.measure_dock.isVisible():
            return
        src, reg, _ = self._view()
        self.measure.update_measurements(src, reg, self.plot.visible_range(), self.plot.cursor_times())

    def _channels_changed(self) -> None:
        reg = self._view()[1]
        self._panel_ver = reg.version
        self.plot.invalidate()
        self._schedule_measure()

    def _range_data(self):
        src, reg, _ = self._view()
        if src is None or src.n == 0:
            return None
        cur = self.plot.cursor_times()
        lo, hi = (min(cur), max(cur)) if cur else self.plot.visible_range()
        i0, i1 = int(np.searchsorted(src.t, lo, "left")), int(np.searchsorted(src.t, hi, "right"))
        return src.t[i0:i1], src.v[i0:i1], reg

    def _show_spectrum(self) -> None:
        d = self._range_data()
        if d is None or len(d[0]) < 16:
            QMessageBox.information(self, "Spectrum", "Need at least 16 samples in the visible window "
                                    "(or between the cursors).")
            return
        t, v, reg = d
        SpectrumDialog(t, v, [reg.display_name(i) for i in range(min(len(reg), v.shape[1]))], self).exec()

    def _show_info(self) -> None:
        p = self.pipeline
        if self.playback is not None:
            s = self.playback
            lines = [f"File: {self.play_path}", f"Format version: {s.version}   Created: {s.created}",
                     f"Start (unix): {s.start_unix:.3f}   {datetime.fromtimestamp(s.start_unix):%Y-%m-%d %H:%M:%S}"
                     if s.start_unix else "Start: unknown",
                     f"Samples: {s.n:,}   Raw lines: {len(s.log_text):,}   Duration: {s.t[-1] - s.t[0]:.3f} s",
                     f"Channels: {', '.join(s.channels)}",
                     f"Complete: {'no (interrupted)' if s.truncated else 'yes'}   Unreadable records: {s.bad_records}",
                     "", "Metadata:", json.dumps(s.meta, indent=2)]
        else:
            lines = [f"Source: {self.conn_desc or 'none'}", f"Acquisition start (unix): {p.start_unix:.3f}",
                     f"Samples: {p.samples:,}   Lines: {p.lines:,}   Invalid lines: {p.nonnumeric_lines:,}",
                     f"Channels: {', '.join(self.registry.keys()) or '-'}",
                     f"Last recording: {self.last_recording or '-'}", "", "Metadata:",
                     json.dumps(self.conn_meta, indent=2)]
        TextDialog("Session info", "\n".join(lines), self).exec()

    # ------------------------------------------------------------------ files
    def _default_name(self, ext: str) -> str:
        return str(self.recordings_dir() / f"export-{datetime.now():%Y%m%d-%H%M%S}{ext}")

    def _save_as(self) -> None:
        src = self.play_path if self.playback is not None else self.last_recording
        if src is None or not Path(src).exists():
            QMessageBox.information(self, "Nothing to save", "No recording or session is available.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save session as", str(Path(src).name), "SerialScope session (*.sscope)")
        if not path:
            return
        if not Path(path).suffix:
            path += ".sscope"
        try:
            shutil.copyfile(src, path)
            self.say(f"Saved {path}")
        except OSError as exc:
            log.exception("Save failed")
            QMessageBox.critical(self, "Save failed", str(exc))

    def _save_live(self) -> None:
        if self.pipeline.live.n == 0:
            QMessageBox.information(self, "Nothing to save", "The live buffer is empty.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save live buffer as", self._default_name(".sscope"),
                                              "SerialScope session (*.sscope)")
        if not path:
            return
        try:
            write_session(Path(path), self.pipeline.snapshot_session(dict(self.conn_meta, kind="live buffer snapshot")))
            self.say(f"Saved live buffer ({self.pipeline.live.n:,} samples) to {path}. "
                     f"Only the last {self.pipeline.live.n:,} samples are held in memory.")
        except OSError as exc:
            log.exception("Save failed")
            QMessageBox.critical(self, "Save failed", str(exc))

    def _export_csv_session(self) -> None:
        try:
            s = self.playback if self.playback is not None else (
                load_session(self.last_recording) if self.last_recording else None)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Export failed", str(exc))
            return
        if s is None or s.n == 0:
            QMessageBox.information(self, "No data", "No session or recording with samples is available.")
            return
        self._write_csv(s)

    def _export_csv_live(self) -> None:
        if self.pipeline.live.n == 0:
            QMessageBox.information(self, "No data", "The live buffer is empty.")
            return
        self._write_csv(self.pipeline.snapshot_session())

    def _write_csv(self, s: Session) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export CSV", self._default_name(".csv"), "CSV files (*.csv)")
        if not path:
            return
        if not Path(path).suffix:
            path += ".csv"
        reg = ChannelRegistry.from_keys(s.channels, self.prefs)
        names = [reg.display_name(i, with_unit=True) for i in range(len(s.channels))]
        try:
            n = export_csv(Path(path), s, names)
            self.say(f"Exported {n:,} samples to {path} (raw values; display gain/offset not applied).")
        except OSError as exc:
            log.exception("CSV export failed")
            QMessageBox.critical(self, "Export failed", str(exc))

    def _about(self) -> None:
        QMessageBox.about(self, "About SerialScope",
                          f"SerialScope {__version__}\nOscilloscope-style serial monitor.\n"
                          f"Python / PySide6 / PyQtGraph / PySerial.\nLog files: {log_dir()}")

    # ------------------------------------------------------------------ shutdown
    def closeEvent(self, event) -> None:  # noqa: N802
        for t in (self.poll_timer, self.render_timer, self.status_timer, self.measure_timer):
            t.stop()
        self.playing = False
        try:
            if self.worker is not None:
                self.worker.stop()
                self.worker.join(2.0)
                self._poll_data(self.worker)
                self.worker = None
            if self.pipeline.recording:
                info = self.pipeline.stop_recording("application closed")
                log.info("Recording finalised on exit: %s", info)
            self._save_settings()
        except Exception:
            log.exception("Error during shutdown")
        event.accept()


        