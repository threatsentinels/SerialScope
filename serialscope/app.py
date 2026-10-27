"""Application entry: logging, theme, main window, uncaught-exception handling."""
from __future__ import annotations

import logging
import os
import sys
import traceback


def run() -> int:
    os.environ.setdefault("PYQTGRAPH_QT_LIB", "PySide6")
    from .logging_setup import setup_logging
    log_path = setup_logging()
    log = logging.getLogger("serialscope")
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication, QMessageBox
    import pyqtgraph as pg
    from .gui.main_window import MainWindow
    from .gui.theme import apply_theme

    app = QApplication(sys.argv)
    app.setApplicationName("SerialScope")
    app.setOrganizationName("SerialScope")
    apply_theme(app)
    pg.setConfigOptions(antialias=False, background="#121417", foreground="#d0d4da")

    def excepthook(et, ev, tb):
        log.error("Uncaught exception", exc_info=(et, ev, tb))
        try:
            QMessageBox.critical(None, "SerialScope - unexpected error",
                                 f"{et.__name__}: {ev}\n\nDetails were written to:\n{log_path}")
        except Exception:
            traceback.print_exception(et, ev, tb)
    sys.excepthook = excepthook

    log.info("SerialScope starting (python %s)", sys.version.split()[0])
    win = MainWindow(QSettings("SerialScope", "SerialScope"))
    win.show()
    code = app.exec()
    log.info("SerialScope exit %s", code)
    return code
