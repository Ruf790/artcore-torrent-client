"""Light / dark theme in the colours of artcore-gangsta.eu (Bootstrap 5.3 palette, st-ui look)."""
from __future__ import annotations

import os
import sys

from PySide6.QtGui import QColor, QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication

# Bootstrap 5.3 CSS variables, as the site uses them (data-bs-theme light / dark)
PALETTES = {
    "light": {
        "body_bg": "#ffffff", "body": "#212529", "emphasis": "#000000",
        "secondary": "#6c757d", "tertiary_bg": "#f8f9fa", "secondary_bg": "#e9ecef",
        "page_bg": "#f3f5f7", "border": "#dee2e6", "border_soft": "#edf0f2",
        "primary": "#0d6efd", "primary_hover": "#0b5ed7",
        "primary_subtle": "#cfe2ff", "primary_text": "#052c65",
        "success": "#198754", "success_subtle": "#d1e7dd", "success_text": "#0a3622",
        "danger": "#dc3545", "danger_subtle": "#f8d7da", "danger_text": "#58151c",
        "warning": "#ffc107", "warning_subtle": "#fff3cd", "warning_text": "#664d03",
        "info": "#0dcaf0", "info_subtle": "#cff4fc", "info_text": "#055160",
        "muted_subtle": "#f1f3f5", "selection": "#e7f1ff",
    },
    "dark": {
        "body_bg": "#212529", "body": "#dee2e6", "emphasis": "#ffffff",
        "secondary": "#9aa1a8", "tertiary_bg": "#2b3035", "secondary_bg": "#343a40",
        "page_bg": "#1a1d20", "border": "#495057", "border_soft": "#343a40",
        "primary": "#0d6efd", "primary_hover": "#3d8bfd",
        "primary_subtle": "#031633", "primary_text": "#6ea8fe",
        "success": "#198754", "success_subtle": "#051b11", "success_text": "#75b798",
        "danger": "#dc3545", "danger_subtle": "#2c0b0e", "danger_text": "#ea868f",
        "warning": "#ffc107", "warning_subtle": "#332701", "warning_text": "#ffda6a",
        "info": "#0dcaf0", "info_subtle": "#032830", "info_text": "#6edff6",
        "muted_subtle": "#2b3035", "selection": "#10294d",
    },
}

current: dict = dict(PALETTES["light"])
current_name = "light"
TITLE_FONT = "Oswald"


def asset(*parts: str) -> str:
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base, "assets", *parts)


def load_fonts() -> None:
    global TITLE_FONT
    fid = QFontDatabase.addApplicationFont(asset("fonts", "Oswald.ttf"))
    fams = QFontDatabase.applicationFontFamilies(fid) if fid >= 0 else []
    TITLE_FONT = fams[0] if fams else "Arial"


def system_is_dark() -> bool:
    if sys.platform == "win32":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
                return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 0
        except OSError:
            return False
    try:
        from PySide6.QtCore import Qt
        return QApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
    except Exception:
        return False


def resolve(mode: str) -> str:
    if mode in ("light", "dark"):
        return mode
    return "dark" if system_is_dark() else "light"


def apply(app: QApplication, mode: str) -> str:
    """mode: 'system' | 'light' | 'dark'. Returns the resolved name."""
    global current_name
    current_name = resolve(mode)
    current.clear()
    current.update(PALETTES[current_name])
    c = current

    pal = QPalette()
    pal.setColor(QPalette.Window, QColor(c["page_bg"]))
    pal.setColor(QPalette.WindowText, QColor(c["body"]))
    pal.setColor(QPalette.Base, QColor(c["body_bg"]))
    pal.setColor(QPalette.AlternateBase, QColor(c["tertiary_bg"]))
    pal.setColor(QPalette.Text, QColor(c["body"]))
    pal.setColor(QPalette.Button, QColor(c["tertiary_bg"]))
    pal.setColor(QPalette.ButtonText, QColor(c["body"]))
    pal.setColor(QPalette.Highlight, QColor(c["primary"]))
    pal.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    pal.setColor(QPalette.ToolTipBase, QColor(c["body_bg"]))
    pal.setColor(QPalette.ToolTipText, QColor(c["body"]))
    pal.setColor(QPalette.PlaceholderText, QColor(c["secondary"]))
    pal.setColor(QPalette.Link, QColor(c["primary_text"] if current_name == "dark" else c["primary"]))
    app.setPalette(pal)
    app.setStyleSheet(build_qss(c))
    return current_name


def build_qss(c: dict) -> str:
    return f"""
* {{ font-family: "Segoe UI", "Noto Sans", Arial; font-size: 10pt; }}
QMainWindow, QDialog, QWidget#page {{ background: {c['page_bg']}; color: {c['body']}; }}
QToolTip {{ background: {c['body_bg']}; color: {c['body']}; border: 1px solid {c['border']}; padding: 4px 6px; }}

/* cards (.st-card) */
QFrame#card {{ background: {c['body_bg']}; border: 1px solid {c['border']}; border-radius: 14px; }}
QLabel#title {{ font-family: "{TITLE_FONT}"; font-size: 17pt; font-weight: 600; color: {c['emphasis']}; }}
QLabel#meta {{ color: {c['secondary']}; font-size: 9pt; }}
QLabel#headIcon {{ background: {c['primary_subtle']}; border-radius: 12px; }}

/* KPI tiles (.st-kpi--muted) */
QFrame#kpi {{ background: {c['muted_subtle']}; border: 1px solid {c['border']}; border-radius: 12px; }}
QLabel#kpiVal {{ font-size: 15pt; font-weight: 700; color: {c['emphasis']}; background: transparent; }}
QLabel#kpiLbl {{ color: {c['secondary']}; font-size: 9pt; background: transparent; }}

/* buttons (.btn .st-pill) */
QPushButton {{
  background: {c['body_bg']}; color: {c['body']}; border: 1px solid {c['border']};
  border-radius: 15px; padding: 6px 14px; font-weight: 600;
}}
QPushButton:hover {{ background: {c['tertiary_bg']}; border-color: {c['secondary']}; }}
QPushButton:pressed {{ background: {c['secondary_bg']}; }}
QPushButton:disabled {{ color: {c['secondary']}; }}
QPushButton#primary {{ background: {c['primary']}; color: #fff; border-color: {c['primary']}; }}
QPushButton#primary:hover {{ background: {c['primary_hover']}; }}
QPushButton#danger {{ color: {c['danger_text'] if c['body_bg'] == '#212529' else c['danger']}; }}
QPushButton#danger:hover {{ background: {c['danger_subtle']}; border-color: {c['danger']}; }}

/* round icon buttons (.st-icon-btn) */
QToolButton#iconBtn {{
  background: {c['tertiary_bg']}; border: none; border-radius: 16px;
  min-width: 32px; max-width: 32px; min-height: 32px; max-height: 32px;
}}
QToolButton#iconBtn:hover {{ background: {c['primary_subtle']}; }}

/* filter pills (.st-toggle) */
QPushButton#pill {{
  background: {c['tertiary_bg']}; color: {c['secondary']}; border: 1px solid transparent;
  border-radius: 14px; padding: 5px 13px; font-weight: 600;
}}
QPushButton#pill:hover {{ color: {c['body']}; border-color: {c['border']}; }}
QPushButton#pill:checked {{ background: {c['primary_subtle']}; color: {c['primary_text']}; }}

QFrame#vsep {{ background: {c['border']}; min-width: 1px; max-width: 1px; margin: 5px 3px; }}

/* inputs */
QLineEdit, QSpinBox, QComboBox {{
  background: {c['body_bg']}; color: {c['body']}; border: 1px solid {c['border']};
  border-radius: 8px; padding: 5px 9px; selection-background-color: {c['primary']};
}}
QLineEdit#search {{ border-radius: 15px; padding-left: 12px; min-width: 200px; }}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{ border-color: {c['primary']}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{
  background: {c['body_bg']}; color: {c['body']}; border: 1px solid {c['border']};
  selection-background-color: {c['primary_subtle']}; selection-color: {c['primary_text']};
}}
QCheckBox {{ color: {c['body']}; spacing: 8px; }}

/* table */
QTableView, QTableWidget {{
  background: {c['body_bg']}; color: {c['body']}; border: none; gridline-color: transparent;
  selection-background-color: {c['selection']}; selection-color: {c['body']};
  alternate-background-color: {c['body_bg']}; outline: 0;
}}
QTableView::item, QTableWidget::item {{ border-bottom: 1px solid {c['border_soft']}; padding: 0 6px; }}
QTableView::item:selected, QTableWidget::item:selected {{ background: {c['selection']}; color: {c['emphasis']}; }}
QHeaderView {{ background: {c['body_bg']}; border: none; }}
QHeaderView::section {{
  background: {c['body_bg']}; color: {c['secondary']}; border: none;
  border-bottom: 1px solid {c['border']}; padding: 7px 6px; font-weight: 600; font-size: 9pt;
}}
QHeaderView::section:hover {{ color: {c['emphasis']}; }}
QTableCornerButton::section {{ background: {c['body_bg']}; border: none; }}

/* tabs (.st-tabs) */
QTabWidget::pane {{ border: none; border-top: 1px solid {c['border']}; background: {c['body_bg']}; }}
QTabBar {{ background: transparent; }}
QTabBar::tab {{
  background: transparent; color: {c['secondary']}; border: none; border-bottom: 2px solid transparent;
  padding: 8px 14px; margin-right: 2px; font-weight: 600;
  border-top-left-radius: 8px; border-top-right-radius: 8px;
}}
QTabBar::tab:hover {{ color: {c['emphasis']}; background: {c['tertiary_bg']}; }}
QTabBar::tab:selected {{ color: {c['primary_text'] if c['body_bg'] == '#212529' else c['primary']}; border-bottom-color: {c['primary']}; }}

QPlainTextEdit {{ background: {c['body_bg']}; color: {c['body']}; border: none; font-family: Consolas, monospace; font-size: 9pt; }}
QLabel#rowLbl {{ color: {c['secondary']}; }}
QLabel#rowVal {{ color: {c['body']}; font-weight: 600; }}

QSplitter::handle {{ background: transparent; height: 10px; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {c['secondary_bg']}; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {c['border']}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {c['secondary_bg']}; border-radius: 4px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QMenu {{ background: {c['body_bg']}; color: {c['body']}; border: 1px solid {c['border']}; border-radius: 8px; padding: 4px; }}
QMenu::item {{ padding: 6px 18px 6px 10px; border-radius: 6px; }}
QMenu::item:selected {{ background: {c['primary_subtle']}; color: {c['primary_text']}; }}
QMenu::separator {{ height: 1px; background: {c['border']}; margin: 4px 6px; }}
QStatusBar {{ background: {c['page_bg']}; color: {c['secondary']}; }}
QMessageBox QLabel {{ color: {c['body']}; }}
"""
