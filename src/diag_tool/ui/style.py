"""
Modern dark theme via QSS. Applied globally at startup.

Design: flat, subtle borders, blue accent (#3B82F6), generous padding.
Monospace fonts for log views (set on the tab widgets themselves via
setFont()).
"""

# Colour palette
BG           = "#1E1F22"
BG_ELEVATED  = "#2B2D31"
BG_INPUT     = "#232428"
BORDER       = "#3F4147"
BORDER_SOFT  = "#2E3035"
TEXT         = "#DBDEE1"
TEXT_MUTED   = "#949BA4"
ACCENT       = "#3B82F6"
ACCENT_HOVER = "#5A96F7"
ACCENT_TEXT  = "#FFFFFF"
DANGER       = "#DA373C"
WARNING      = "#F0B232"
SUCCESS      = "#23A55A"


STYLESHEET = f"""
* {{
    font-family: "Inter", "Segoe UI", "SF Pro Text", "Ubuntu", sans-serif;
    font-size: 10pt;
    color: {TEXT};
}}

QMainWindow, QDialog, QWidget {{
    background-color: {BG};
}}

QStatusBar {{
    background-color: {BG_ELEVATED};
    color: {TEXT_MUTED};
    border-top: 1px solid {BORDER_SOFT};
    padding: 4px 8px;
}}

/* ---- Tabs ---- */
QTabWidget::pane {{
    border: none;
    background-color: {BG};
    top: -1px;
}}
QTabBar::tab {{
    background: transparent;
    color: {TEXT_MUTED};
    padding: 10px 20px;
    border: none;
    border-bottom: 2px solid transparent;
    margin-right: 4px;
    font-weight: 500;
}}
QTabBar::tab:hover {{
    color: {TEXT};
}}
QTabBar::tab:selected {{
    color: {TEXT};
    border-bottom: 2px solid {ACCENT};
}}

/* ---- Buttons ---- */
QPushButton {{
    background-color: {BG_ELEVATED};
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 7px 14px;
    font-weight: 500;
}}
QPushButton:hover {{
    background-color: {BORDER_SOFT};
    border-color: {BORDER};
}}
QPushButton:pressed {{
    background-color: {BG_INPUT};
}}
QPushButton:disabled {{
    color: {TEXT_MUTED};
    background-color: {BG_INPUT};
}}
QPushButton[accent="true"] {{
    background-color: {ACCENT};
    color: {ACCENT_TEXT};
    border-color: {ACCENT};
}}
QPushButton[accent="true"]:hover {{
    background-color: {ACCENT_HOVER};
    border-color: {ACCENT_HOVER};
}}
QPushButton[danger="true"] {{
    background-color: transparent;
    color: {DANGER};
    border-color: {BORDER};
}}
QPushButton[danger="true"]:hover {{
    background-color: {DANGER};
    color: white;
    border-color: {DANGER};
}}

/* ---- Inputs ---- */
QLineEdit, QSpinBox, QComboBox, QPlainTextEdit, QTextEdit {{
    background-color: {BG_INPUT};
    color: {TEXT};
    border: 1px solid {BORDER_SOFT};
    border-radius: 6px;
    padding: 6px 8px;
    selection-background-color: {ACCENT};
}}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus,
QPlainTextEdit:focus, QTextEdit:focus {{
    border-color: {ACCENT};
}}
QComboBox::drop-down {{
    border: none;
    width: 22px;
}}
QComboBox::down-arrow {{
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid {TEXT_MUTED};
    margin-right: 8px;
}}
QComboBox QAbstractItemView {{
    background-color: {BG_ELEVATED};
    border: 1px solid {BORDER};
    selection-background-color: {ACCENT};
    padding: 4px;
    outline: none;
}}
QSpinBox::up-button, QSpinBox::down-button {{
    background: transparent;
    border: none;
    width: 16px;
}}
QSpinBox::up-arrow {{
    border-left: 3px solid transparent;
    border-right: 3px solid transparent;
    border-bottom: 4px solid {TEXT_MUTED};
}}
QSpinBox::down-arrow {{
    border-left: 3px solid transparent;
    border-right: 3px solid transparent;
    border-top: 4px solid {TEXT_MUTED};
}}

/* ---- Labels ---- */
QLabel {{
    background: transparent;
    color: {TEXT};
}}
QLabel[muted="true"] {{
    color: {TEXT_MUTED};
    font-size: 9pt;
}}
QLabel[heading="true"] {{
    font-size: 11pt;
    font-weight: 600;
    color: {TEXT};
    padding: 2px 0;
}}

/* ---- Group Box ---- */
QGroupBox {{
    background-color: {BG_ELEVATED};
    border: 1px solid {BORDER_SOFT};
    border-radius: 8px;
    margin-top: 16px;
    padding: 14px 12px 10px 12px;
    font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 0 8px;
    left: 8px;
    color: {TEXT};
    background-color: {BG_ELEVATED};
}}

/* ---- Table ---- */
QTableWidget {{
    background-color: {BG_ELEVATED};
    alternate-background-color: {BG_INPUT};
    gridline-color: {BORDER_SOFT};
    border: 1px solid {BORDER_SOFT};
    border-radius: 6px;
    selection-background-color: {ACCENT};
    selection-color: {ACCENT_TEXT};
}}
QTableWidget::item {{
    padding: 5px 6px;
    border: none;
}}
QHeaderView::section {{
    background-color: {BG_ELEVATED};
    color: {TEXT_MUTED};
    padding: 8px 8px;
    border: none;
    border-bottom: 1px solid {BORDER};
    font-weight: 600;
    text-transform: uppercase;
    font-size: 9pt;
}}
QTableCornerButton::section {{
    background-color: {BG_ELEVATED};
    border: none;
    border-bottom: 1px solid {BORDER};
}}

/* ---- List ---- */
QListWidget {{
    background-color: {BG_INPUT};
    border: 1px solid {BORDER_SOFT};
    border-radius: 6px;
    padding: 4px;
    outline: none;
}}
QListWidget::item {{
    padding: 6px 8px;
    border-radius: 4px;
    margin: 1px 0;
}}
QListWidget::item:hover {{
    background-color: {BORDER_SOFT};
}}
QListWidget::item:selected {{
    background-color: {ACCENT};
    color: {ACCENT_TEXT};
}}

/* ---- CheckBox ---- */
QCheckBox {{
    color: {TEXT};
    spacing: 8px;
}}
QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border: 1.5px solid {BORDER};
    border-radius: 4px;
    background: {BG_INPUT};
}}
QCheckBox::indicator:checked {{
    background-color: {ACCENT};
    border-color: {ACCENT};
}}
QCheckBox::indicator:hover {{
    border-color: {ACCENT};
}}

/* ---- Scrollbar ---- */
QScrollBar:vertical {{
    background: transparent;
    width: 12px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {BORDER};
    min-height: 30px;
    border-radius: 6px;
    margin: 2px;
}}
QScrollBar::handle:vertical:hover {{
    background: {TEXT_MUTED};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 12px;
    margin: 0;
}}
QScrollBar::handle:horizontal {{
    background: {BORDER};
    min-width: 30px;
    border-radius: 6px;
    margin: 2px;
}}
QScrollBar::handle:horizontal:hover {{
    background: {TEXT_MUTED};
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0;
}}

/* ---- Splitter ---- */
QSplitter::handle {{
    background-color: {BORDER_SOFT};
}}
QSplitter::handle:horizontal {{ width: 3px; }}
QSplitter::handle:vertical {{ height: 3px; }}
QSplitter::handle:hover {{ background-color: {ACCENT}; }}

/* ---- Inline editors inside item views ---- */
/* When a user double-clicks a QTableWidget cell, Qt inserts a
   QLineEdit as an inline editor. The global QLineEdit padding above
   (6px 8px) makes that editor unreadable inside default-height rows —
   text vanishes into the padding. Tighten it here. */
QAbstractItemView QLineEdit {{
    padding: 2px 4px;
    border-radius: 3px;
    border: 1px solid {ACCENT};
    background-color: {BG_INPUT};
}}

/* ---- Message Box ---- */
QMessageBox {{
    background-color: {BG_ELEVATED};
}}
"""


def apply(app) -> None:
    app.setStyle("Fusion")
    app.setStyleSheet(STYLESHEET)
    # Base font -- if Inter/SF Pro is missing, Qt falls back to the
    # next-best available family.
    f = app.font()
    f.setPointSize(10)
    app.setFont(f)
