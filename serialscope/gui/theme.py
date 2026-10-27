
"""Dark graphite and light themes: Fusion style + palette + compact stylesheet.
No gradients, no glow.
"""
from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette


DARK = dict(
    bg="#222428",
    panel="#2a2d32",
    panel_alt="#31353b",
    border="#3d4148",
    text="#d0d4da",
    muted="#8a919b",
    field="#1a1c20",
    plot_bg="#121417",
    grid="#2e3238",
    green="#5fd068",
    amber="#e5a823",
    cyan="#38bdd0",
    red="#e2574c",
    select="#2f4f5c",
)

LIGHT = dict(
    bg="#f3f4f6",
    panel="#ffffff",
    panel_alt="#e9edf1",
    border="#cbd1d8",
    text="#252a31",
    muted="#68717d",
    field="#ffffff",
    plot_bg="#ffffff",
    grid="#e1e5ea",
    green="#218739",
    amber="#946200",
    cyan="#087f95",
    red="#c9362b",
    select="#c9e5ef",
)

THEMES = {
    "Dark": DARK,
    "Light": LIGHT,
}

# Default theme; updated whenever apply_theme() is called.
C = DARK

_MONO = [
    "Cascadia Mono",
    "Consolas",
    "DejaVu Sans Mono",
    "Menlo",
    "Courier New",
]

_UI = [
    "Segoe UI",
    "Noto Sans",
    "Helvetica Neue",
    "Arial",
]


def _pick(candidates: list[str]) -> str | None:
    have = set(QFontDatabase.families())
    return next((font for font in candidates if font in have), None)


def mono_font(size: int = 9) -> QFont:
    family = _pick(_MONO)

    font = (
        QFont(family, size)
        if family
        else QFontDatabase.systemFont(
            QFontDatabase.SystemFont.FixedFont
        )
    )

    font.setStyleHint(QFont.StyleHint.Monospace)
    return font


def ui_font(size: int = 9) -> QFont:
    family = _pick(_UI)
    return QFont(family, size) if family else QFont()


def build_stylesheet(colors: dict[str, str]) -> str:
    """Build the complete application stylesheet for a theme."""
    c = colors

    return f"""
QMainWindow, QDialog {{
    background: {c['bg']};
}}

QWidget {{
    color: {c['text']};
}}

QToolTip {{
    background: {c['panel_alt']};
    color: {c['text']};
    border: 1px solid {c['border']};
}}

QMenuBar {{
    background: {c['panel']};
    border-bottom: 1px solid {c['border']};
}}

QMenuBar::item {{
    padding: 3px 8px;
    background: transparent;
}}

QMenuBar::item:selected {{
    background: {c['select']};
}}

QMenu {{
    background: {c['panel']};
    color: {c['text']};
    border: 1px solid {c['border']};
    padding: 2px;
}}

QMenu::item {{
    padding: 3px 22px 3px 22px;
}}

QMenu::item:selected {{
    background: {c['select']};
}}

QMenu::item:disabled {{
    color: {c['muted']};
}}

QMenu::separator {{
    height: 1px;
    background: {c['border']};
    margin: 3px 4px;
}}

QToolBar {{
    background: {c['panel']};
    border: none;
    border-bottom: 1px solid {c['border']};
    spacing: 3px;
    padding: 2px 4px;
}}

QToolBar::separator {{
    width: 1px;
    background: {c['border']};
    margin: 3px 4px;
}}

QToolButton, QPushButton {{
    background: {c['panel_alt']};
    color: {c['text']};
    border: 1px solid {c['border']};
    border-radius: 2px;
    padding: 2px 8px;
    min-height: 18px;
}}

QToolButton:hover, QPushButton:hover {{
    border-color: {c['muted']};
}}

QToolButton:pressed, QPushButton:pressed {{
    background: {c['field']};
}}

QToolButton:checked {{
    background: {c['select']};
    border-color: {c['cyan']};
}}

QToolButton:disabled, QPushButton:disabled {{
    color: {c['muted']};
    background: {c['panel']};
    border-color: {c['panel_alt']};
}}

QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
    background: {c['field']};
    color: {c['text']};
    border: 1px solid {c['border']};
    border-radius: 2px;
    padding: 1px 4px;
    min-height: 18px;
    selection-background-color: {c['select']};
    selection-color: {c['text']};
}}

QLineEdit:focus, QComboBox:focus,
QSpinBox:focus, QDoubleSpinBox:focus {{
    border-color: {c['cyan']};
}}

QLineEdit:disabled, QComboBox:disabled,
QSpinBox:disabled, QDoubleSpinBox:disabled {{
    color: {c['muted']};
}}

QComboBox QAbstractItemView {{
    background: {c['panel']};
    color: {c['text']};
    selection-background-color: {c['select']};
    selection-color: {c['text']};
    border: 1px solid {c['border']};
}}

QCheckBox {{
    spacing: 5px;
}}

QRadioButton {{
    spacing: 5px;
}}

QDockWidget {{
    titlebar-close-icon: none;
}}

QDockWidget::title {{
    background: {c['panel_alt']};
    padding: 3px 6px;
    border: 1px solid {c['border']};
    border-bottom: none;
}}

QStatusBar {{
    background: {c['panel']};
    border-top: 1px solid {c['border']};
}}

QStatusBar::item {{
    border: none;
}}

QTableWidget, QTableView, QPlainTextEdit, QTextEdit {{
    background: {c['field']};
    color: {c['text']};
    border: 1px solid {c['border']};
    gridline-color: {c['panel_alt']};
    selection-background-color: {c['select']};
    selection-color: {c['text']};
}}

QHeaderView::section {{
    background: {c['panel_alt']};
    color: {c['muted']};
    border: none;
    border-right: 1px solid {c['border']};
    border-bottom: 1px solid {c['border']};
    padding: 2px 5px;
}}

QScrollBar:vertical {{
    background: {c['bg']};
    width: 11px;
    margin: 0;
}}

QScrollBar:horizontal {{
    background: {c['bg']};
    height: 11px;
    margin: 0;
}}

QScrollBar::handle {{
    background: {c['border']};
    min-height: 24px;
    min-width: 24px;
    border-radius: 2px;
}}

QScrollBar::handle:hover {{
    background: {c['muted']};
}}

QScrollBar::add-line, QScrollBar::sub-line {{
    width: 0;
    height: 0;
}}

QSlider::groove:horizontal {{
    background: {c['field']};
    height: 4px;
    border: 1px solid {c['border']};
}}

QSlider::handle:horizontal {{
    background: {c['cyan']};
    width: 8px;
    margin: -5px 0;
    border-radius: 1px;
}}

QLabel#chip {{
    border: 1px solid {c['border']};
    border-radius: 2px;
    padding: 1px 6px;
    background: {c['field']};
}}

QSplitter::handle {{
    background: {c['border']};
}}
"""


def apply_theme(app, theme_name: str = "Dark") -> None:
    """Apply a theme to a QApplication instance."""
    global C

    theme_name = theme_name if theme_name in THEMES else "Dark"
    C = THEMES[theme_name]

    app.setStyle("Fusion")

    palette = QPalette()

    for role, key in (
        (QPalette.ColorRole.Window, "bg"),
        (QPalette.ColorRole.Base, "field"),
        (QPalette.ColorRole.AlternateBase, "panel"),
        (QPalette.ColorRole.Button, "panel_alt"),
        (QPalette.ColorRole.ToolTipBase, "panel_alt"),
        (QPalette.ColorRole.Highlight, "select"),
    ):
        palette.setColor(role, QColor(C[key]))

    for role in (
        QPalette.ColorRole.WindowText,
        QPalette.ColorRole.Text,
        QPalette.ColorRole.ButtonText,
        QPalette.ColorRole.ToolTipText,
        QPalette.ColorRole.HighlightedText,
    ):
        palette.setColor(role, QColor(C["text"]))

    palette.setColor(
        QPalette.ColorGroup.Disabled,
        QPalette.ColorRole.WindowText,
        QColor(C["muted"]),
    )
    palette.setColor(
        QPalette.ColorGroup.Disabled,
        QPalette.ColorRole.Text,
        QColor(C["muted"]),
    )
    palette.setColor(
        QPalette.ColorGroup.Disabled,
        QPalette.ColorRole.ButtonText,
        QColor(C["muted"]),
    )

    app.setPalette(palette)
    app.setFont(ui_font(9))
    app.setStyleSheet(build_stylesheet(C))


def chip_style(color: str, filled: bool = False) -> str:
    """Return a status-chip stylesheet using the active theme."""
    if filled:
        return (
            f"color: {C['plot_bg']}; "
            f"background: {color}; "
            f"border: 1px solid {color}; "
            "border-radius: 2px; "
            "padding: 1px 6px; "
            "font-weight: bold;"
        )

    return (
        f"color: {color}; "
        f"background: {C['field']}; "
        f"border: 1px solid {C['border']}; "
        "border-radius: 2px; "
        "padding: 1px 6px;"
    )
