"""Light / dark theme in the ArtCore colours (Bootstrap 5.3 palette, st-ui look)."""
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
        "purple": "#6f42c1", "purple_subtle": "#e2d9f3", "purple_text": "#2c1a4d",
        "orange": "#fd7e14", "orange_subtle": "#ffe5d0", "orange_text": "#652c00",
        "teal": "#20c997", "pink": "#d63384",
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
        "purple": "#8c68cd", "purple_subtle": "#1c1327", "purple_text": "#c29ffa",
        "orange": "#fd9843", "orange_subtle": "#331904", "orange_text": "#feb272",
        "teal": "#3dd5f3", "pink": "#e685b5",
    },
}

current: dict = dict(PALETTES["light"])
current_name = "light"
current_style = "soft"        # muted (gray) | soft (coloured accents) | vivid (gradients) | site (like the site's profile page)
                              # | fluent (Windows 11)
STYLES = ("muted", "soft", "vivid", "site", "fluent")
mica_wanted = False    # Settings → "Mica" on and Windows 11
mica_window = False    # the main window was created translucent (only at start-up)


def mica_active() -> bool:
    return mica_wanted and mica_window and current_style == "fluent"

# Windows 11 (Fluent) colours: neutral layers, 4 px controls, 8 px cards; accent comes from Windows
FLUENT = {
    "light": {
        "body_bg": "#ffffff", "body": "#1b1b1b", "emphasis": "#000000", "secondary": "#5f5f5f",
        "tertiary_bg": "#f9f9f9", "secondary_bg": "#e8e8e8", "page_bg": "#f3f3f3",
        "border": "#e5e5e5", "border_soft": "#f0f0f0", "control_edge": "#cccccc", "input_edge": "#868686",
        "muted_subtle": "#f9f9f9", "hover": "#f5f5f5",
        "success": "#0f7b0f", "danger": "#c42b1c", "success_text": "#0f5a0f", "danger_text": "#8f1e14",
    },
    "dark": {
        "body_bg": "#2b2b2b", "body": "#ffffff", "emphasis": "#ffffff", "secondary": "#c8c8c8",
        "tertiary_bg": "#323232", "secondary_bg": "#3d3d3d", "page_bg": "#202020",
        "border": "#3a3a3a", "border_soft": "#333333", "control_edge": "#454545", "input_edge": "#9a9a9a",
        "muted_subtle": "#2f2f2f", "hover": "#353535",
        "success": "#6ccb5f", "danger": "#ff99a4", "success_text": "#6ccb5f", "danger_text": "#ff99a4",
    },
}
DEFAULT_ACCENT = "#0078d4"     # Windows default blue, if the system accent can't be read


def system_accent() -> tuple[str, str, str]:
    """(base, darker for light mode, lighter for dark mode) of the Windows accent colour."""
    if sys.platform == "win32":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\CurrentVersion\Explorer\Accent") as k:
                raw = winreg.QueryValueEx(k, "AccentPalette")[0]      # 8 × RGBA, light → dark
            cols = ["#%02x%02x%02x" % tuple(raw[i * 4:i * 4 + 3]) for i in range(8)]
            return cols[3], cols[4], cols[1]
        except (OSError, IndexError, TypeError):
            pass
    base = QColor(DEFAULT_ACCENT)
    return DEFAULT_ACCENT, "#005fb8", base.lighter(150).name()


def _fluent_colours(c: dict, dark: bool) -> None:
    c.update(FLUENT["dark" if dark else "light"])
    base, deep, light = system_accent()
    acc = QColor(base if dark else deep)
    c["primary"] = acc.name()
    c["primary_hover"] = acc.lighter(112).name()
    tint = QColor(acc)
    if dark:
        c["primary_text"] = light
        tint.setAlpha(60)
        c["primary_subtle"] = _blend(c["body_bg"], tint)
        c["selection"] = _blend(c["body_bg"], QColor(255, 255, 255, 22))
    else:
        c["primary_text"] = acc.darker(140).name()
        tint.setAlpha(30)
        c["primary_subtle"] = _blend(c["body_bg"], tint)
        c["selection"] = _blend(c["body_bg"], QColor(acc.red(), acc.green(), acc.blue(), 22))


def _blend(bg: str, over: QColor) -> str:
    """Solid colour of `over` (with alpha) laid on `bg` — QSS colours here must be opaque."""
    b = QColor(bg)
    a = over.alphaF()
    return QColor(round(b.red() * (1 - a) + over.red() * a), round(b.green() * (1 - a) + over.green() * a),
                  round(b.blue() * (1 - a) + over.blue() * a)).name()

# KPI tile → colour family
TONES = {"down": "primary", "up": "success", "active": "orange", "session": "purple"}
GRADIENTS = {   # vivid style: tile gradients (light and dark use the same saturated colours)
    "down": ("#0d6efd", "#4d94ff"), "up": ("#198754", "#20c997"),
    "active": ("#fd7e14", "#f5b70a"), "session": ("#6f42c1", "#d63384"),
}
TITLE_FONT = "Oswald"
_OSWALD = "Oswald"
FLUENT_TITLE = "Segoe UI Variable Display"


def asset(*parts: str) -> str:
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base, "assets", *parts)


def load_fonts() -> None:
    global TITLE_FONT
    fid = QFontDatabase.addApplicationFont(asset("fonts", "Oswald.ttf"))
    fams = QFontDatabase.applicationFontFamilies(fid) if fid >= 0 else []
    TITLE_FONT = _OSWALD = fams[0] if fams else "Arial"
    # Windows 10 has no Segoe UI Variable: fall back to plain Segoe UI
    from PySide6.QtGui import QFont
    QFont.insertSubstitution(FLUENT_TITLE, "Segoe UI")
    QFont.insertSubstitution("Segoe UI Variable Text", "Segoe UI")


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


def apply(app: QApplication, mode: str, style: str | None = None, mica: bool | None = None) -> str:
    """mode: 'system' | 'light' | 'dark'; style: muted | soft | vivid. Returns the resolved name."""
    global current_name, current_style, TITLE_FONT, mica_wanted
    current_name = resolve(mode)
    if mica is not None:
        mica_wanted = mica
    if style in STYLES:
        current_style = style
    current.clear()
    current.update(PALETTES[current_name])
    c = current
    dark = current_name == "dark"
    colourful = current_style != "muted"
    if colourful and not dark:
        c["page_bg"] = "#eef2f8"            # a hint of blue instead of plain gray
    if current_style == "site":
        c["page_bg"] = c["body_bg"]         # white page, gray cards — like the profile page
    TITLE_FONT = FLUENT_TITLE if current_style == "fluent" else _OSWALD
    if current_style == "fluent":
        _fluent_colours(c, dark)
    # icon colours that depend on the style (looked up by key like any palette colour)
    c["ic_magnet"] = c["purple"] if colourful else c["body"]
    c["ic_create"] = c["orange"] if colourful else c["body"]
    c["ic_add"] = "#ffffff"
    c["head_ic"] = "#ffffff" if current_style == "vivid" else c["secondary"]
    c["head_logo"] = "#ffffff" if colourful else c["primary_text"]

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
    app.setStyleSheet(build_qss(c, _arrow_images(c)) + style_qss(c))
    return current_name


def _arrow_images(c: dict) -> dict:
    """Chevron PNGs for spin box buttons and combo boxes (QSS needs image files)."""
    import tempfile
    import qtawesome as qta
    folder = os.path.join(tempfile.gettempdir(), "ArtCoreClient-ui")
    os.makedirs(folder, exist_ok=True)
    out = {}
    for name, icon_name in (("up", "fa6s.chevron-up"), ("down", "fa6s.chevron-down")):
        for state, color in (("", c["secondary"]), ("_off", c["border"])):
            path = os.path.join(folder, f"{name}{state}_{current_name}.png")
            qta.icon(icon_name, color=color).pixmap(20, 20).save(path)
            out[name + state] = path.replace("\\", "/")
    for name, icon_name in (("check", "fa6s.check"), ("minus", "fa6s.minus")):
        path = os.path.join(folder, f"{name}.png")
        qta.icon(icon_name, color="#ffffff").pixmap(20, 20).save(path)
        out[name] = path.replace("\\", "/")
    return out


def build_qss(c: dict, img: dict | None = None) -> str:
    img = img or {}
    return f"""
* {{ font-family: "Segoe UI", "Noto Sans", Arial; font-size: 10pt; }}
QMainWindow, QDialog, QWidget#page {{ background: {c['page_bg']}; color: {c['body']}; }}
QToolTip {{ background: {c['body_bg']}; color: {c['body']}; border: 1px solid {c['border']}; padding: 4px 6px; }}

/* cards (.st-card) */
QFrame#card, QFrame#headCard {{ background: {c['body_bg']}; border: 1px solid {c['border']}; border-radius: 14px; }}
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
QLineEdit, QSpinBox, QDoubleSpinBox, QTimeEdit, QComboBox {{
  background: {c['body_bg']}; color: {c['body']}; border: 1px solid {c['border']};
  border-radius: 8px; padding: 5px 9px; selection-background-color: {c['primary']};
}}
QLineEdit#search {{ border-radius: 15px; padding-left: 12px; min-width: 200px; }}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QTimeEdit:focus, QComboBox:focus {{ border-color: {c['primary']}; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox::down-arrow {{ image: url("{img.get('down', '')}"); width: 10px; height: 10px; }}
QComboBox::down-arrow:disabled {{ image: url("{img.get('down_off', '')}"); }}
QAbstractSpinBox {{ padding-right: 26px; }}
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{
  subcontrol-origin: border; width: 22px; border: none; border-left: 1px solid {c['border']};
  background: {c['tertiary_bg']}; margin-right: 1px;
}}
QAbstractSpinBox::up-button {{ subcontrol-position: top right; margin-top: 1px; border-top-right-radius: 7px; }}
QAbstractSpinBox::down-button {{ subcontrol-position: bottom right; margin-bottom: 1px; border-bottom-right-radius: 7px; }}
QAbstractSpinBox::up-button:hover, QAbstractSpinBox::down-button:hover {{ background: {c['primary_subtle']}; }}
QAbstractSpinBox::up-arrow {{ image: url("{img.get('up', '')}"); width: 8px; height: 8px; }}
QAbstractSpinBox::down-arrow {{ image: url("{img.get('down', '')}"); width: 8px; height: 8px; }}
QAbstractSpinBox::up-arrow:disabled, QAbstractSpinBox::up-arrow:off {{ image: url("{img.get('up_off', '')}"); }}
QAbstractSpinBox::down-arrow:disabled, QAbstractSpinBox::down-arrow:off {{ image: url("{img.get('down_off', '')}"); }}
QAbstractSpinBox:disabled {{ color: {c['secondary']}; background: {c['tertiary_bg']}; }}
QComboBox QAbstractItemView {{
  background: {c['body_bg']}; color: {c['body']}; border: 1px solid {c['border']};
  selection-background-color: {c['primary_subtle']}; selection-color: {c['primary_text']};
}}
QCheckBox {{ color: {c['body']}; spacing: 8px; }}
/* checkboxes like Bootstrap .form-check-input */
QCheckBox::indicator, QTreeView::indicator, QTableView::indicator {{
  width: 15px; height: 15px; border: 1px solid {c['secondary']}; border-radius: 4px; background: {c['body_bg']};
}}
QCheckBox::indicator:hover, QTreeView::indicator:hover, QTableView::indicator:hover {{ border-color: {c['primary']}; }}
QCheckBox::indicator:checked, QTreeView::indicator:checked, QTableView::indicator:checked {{
  background: {c['primary']}; border-color: {c['primary']}; image: url("{img.get('check', '')}");
}}
QTreeView::indicator:indeterminate, QCheckBox::indicator:indeterminate {{
  background: {c['primary']}; border-color: {c['primary']}; image: url("{img.get('minus', '')}");
}}
QCheckBox::indicator:disabled, QTableView::indicator:disabled {{ background: {c['tertiary_bg']}; border-color: {c['border']}; }}

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

/* filter sidebar (like qBittorrent's) */
QTreeWidget#sidebar {{
  background: {c['body_bg']}; color: {c['body']}; border: none;
  border-right: 1px solid {c['border']}; outline: 0; padding: 2px 4px 2px 6px;
}}
QTreeWidget#sidebar::item {{ padding: 4px 4px; border: none; border-radius: 0; }}
QTreeWidget#sidebar::item:hover {{ background: {c['tertiary_bg']}; }}

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
QDialog QPlainTextEdit {{ border: 1px solid {c['border']}; border-radius: 8px; padding: 3px 5px; }}
QDialog QPlainTextEdit:focus {{ border-color: {c['primary']}; }}
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
QStatusBar {{ background: {c['page_bg']}; color: {c['secondary']}; border-top: 1px solid {c['border']}; }}
QStatusBar::item {{ border: none; }}
QToolButton#sbItem {{ color: {c['secondary']}; font-size: 8.5pt; padding: 2px 8px; border: none;
    border-left: 1px solid {c['border']}; border-radius: 0; background: transparent; }}
QToolButton#sbItem:hover {{ background: {c['primary_subtle']}; color: {c['primary_text']}; }}
QMessageBox QLabel {{ color: {c['body']}; }}

/* details tabs as pills, like the site's pill tabs (.st-tabs) */
QTabWidget#pillTabs::pane {{ border: none; border-top: 1px solid {c['border']}; }}
QTabWidget#pillTabs QTabBar::tab {{
  background: transparent; color: {c['secondary']}; border: 1px solid transparent; border-radius: 15px;
  padding: 6px 14px; margin: 7px 4px 7px 0; font-weight: 600;
}}
QTabWidget#pillTabs QTabBar::tab:first {{ margin-left: 8px; }}
QTabWidget#pillTabs QTabBar::tab:hover {{ background: {c['tertiary_bg']}; color: {c['emphasis']}; }}
QTabWidget#pillTabs QTabBar::tab:selected {{ background: {c['primary']}; color: #ffffff; border-color: {c['primary']}; }}
"""


def style_qss(c: dict) -> str:
    """Extra rules for the colourful styles; empty for 'muted' (the site's gray look)."""
    if current_style == "muted":
        return ""
    dark = current_name == "dark"
    out = f"""
QLabel#headIcon {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {c['primary']}, stop:1 {c['purple']});
                   border-radius: 12px; }}
QPushButton#pill:checked {{ background: {c['primary']}; color: #ffffff; }}
QFrame#kpi {{ background: {c['body_bg']}; }}
QTabBar::tab:selected {{ color: {c['primary_text'] if dark else c['primary']}; border-bottom: 2px solid {c['primary']}; }}
"""
    for tone, fam in TONES.items():
        out += f"""
QFrame#kpi[tone="{tone}"] QLabel#kpiVal {{ color: {c[fam + '_text'] if dark else c[fam]}; }}
QLabel#kpiIco[tone="{tone}"] {{ background: {c[fam + '_subtle']}; border-radius: 12px; }}
"""
    if current_style == "site":
        out += f"""
QFrame#kpi {{ background: {c['tertiary_bg']}; border: 1px solid {c['border']}; border-radius: 16px; }}
QLabel#kpiVal {{ font-size: 19pt; font-weight: 700; color: {c['emphasis']}; }}
QLabel#kpiLbl {{ font-size: 10.5pt; }}
QFrame#card {{ border-radius: 16px; }}
QWidget#listHead {{ background: {c['tertiary_bg']}; border-bottom: 1px solid {c['border']};
    border-top-left-radius: 15px; border-top-right-radius: 15px; }}
QPushButton#pill {{ background: {c['body_bg']}; color: {c['body']}; border: 1px solid {c['border']};
    border-radius: 13px; padding: 5px 15px; }}
QPushButton#pill:hover {{ border-color: {c['primary']}; color: {c['primary_text'] if dark else c['primary']}; }}
QPushButton#pill:checked {{ background: {c['primary']}; border-color: {c['primary']}; color: #ffffff; }}
QFrame#kpi[tone="session"] QLabel#kpiVal, QFrame#kpi[tone="active"] QLabel#kpiVal {{ color: {c['emphasis']}; }}
"""
        for tone, fam in TONES.items():
            out += f"""
QFrame#kpi[tone="{tone}"] QLabel#kpiVal {{ color: {c['emphasis']}; }}
QLabel#kpiIco[tone="{tone}"] {{ background: {c[fam + '_subtle']}; border-radius: 12px; }}
"""
    if current_style == "fluent":
        out += fluent_qss(c, dark)
    if current_style == "vivid":
        out += """
QFrame#headCard { border: none; border-radius: 14px;
  background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #0d6efd, stop:0.55 #6f42c1, stop:1 #d63384); }
QFrame#headCard QLabel#title { color: #ffffff; }
QFrame#headCard QLabel#meta { color: rgba(255,255,255,210); }
QFrame#headCard QLabel#headIcon { background: rgba(255,255,255,46); }
QFrame#headCard QToolButton#iconBtn { background: rgba(255,255,255,38); }
QFrame#headCard QToolButton#iconBtn:hover { background: rgba(255,255,255,80); }
"""
        for tone, (a, b) in GRADIENTS.items():
            out += f"""
QFrame#kpi[tone="{tone}"] {{ border: none;
  background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {a}, stop:1 {b}); }}
QFrame#kpi[tone="{tone}"] QLabel#kpiVal {{ color: #ffffff; }}
QFrame#kpi[tone="{tone}"] QLabel#kpiLbl {{ color: rgba(255,255,255,215); }}
"""
    return out


def fluent_qss(c: dict, dark: bool) -> str:
    """Windows 11 look: Segoe UI Variable, 4 px controls with a darker bottom edge, 8 px cards,
    neutral selection, accent only where something is chosen."""
    on_acc = "#ffffff"
    shade = "rgba(255,255,255,15)" if dark else "rgba(0,0,0,10)"
    press = "rgba(255,255,255,8)" if dark else "rgba(0,0,0,6)"
    return f"""
* {{ font-family: "Segoe UI Variable Text", "Segoe UI", "Noto Sans", Arial; font-size: 10pt; }}
QFrame#card, QFrame#headCard {{ background: {c['body_bg']}; border: 1px solid {c['border']}; border-radius: 8px; }}
QLabel#title {{ font-family: "{FLUENT_TITLE}"; font-size: 16pt; font-weight: 600; color: {c['emphasis']}; }}
QLabel#headIcon {{ background: {c['primary']}; border-radius: 8px; }}
QFrame#kpi {{ background: {c['body_bg']}; border: 1px solid {c['border']}; border-radius: 8px; }}
QLabel#kpiVal {{ font-family: "{FLUENT_TITLE}"; font-size: 15pt; font-weight: 600; }}

QPushButton {{
  background: {c['tertiary_bg'] if dark else '#fbfbfb'}; color: {c['body']};
  border: 1px solid {c['border']}; border-bottom-color: {c['control_edge']};
  border-radius: 4px; padding: 5px 12px; font-weight: 400;
}}
QPushButton:hover {{ background: {c['hover']}; border-color: {c['border']}; border-bottom-color: {c['control_edge']}; }}
QPushButton:pressed {{ background: {c['page_bg']}; color: {c['secondary']}; border-bottom-color: {c['border']}; }}
QPushButton#primary {{ background: {c['primary']}; color: {on_acc}; border: 1px solid {c['primary']};
  border-bottom-color: {QColor(c['primary']).darker(125).name()}; font-weight: 600; }}
QPushButton#primary:hover {{ background: {c['primary_hover']}; }}
QPushButton#danger:hover {{ background: {c['hover']}; border-color: {c['border']}; }}

QToolButton#iconBtn {{ background: transparent; border-radius: 4px; }}
QToolButton#iconBtn:hover {{ background: {shade}; }}
QToolButton#iconBtn:pressed {{ background: {press}; }}

QPushButton#pill {{ background: transparent; color: {c['secondary']}; border: 1px solid transparent;
  border-radius: 4px; padding: 4px 11px; font-weight: 400; }}
QPushButton#pill:hover {{ background: {shade}; color: {c['body']}; border-color: transparent; }}
QPushButton#pill:checked {{ background: {c['body_bg']}; color: {c['primary_text']}; font-weight: 600;
  border: 1px solid {c['border']}; border-bottom: 2px solid {c['primary']}; }}

QLineEdit, QSpinBox, QDoubleSpinBox, QTimeEdit, QComboBox {{
  background: {c['tertiary_bg'] if dark else '#fdfdfd'}; border: 1px solid {c['border']};
  border-bottom: 1px solid {c['input_edge']}; border-radius: 4px; padding: 5px 9px;
}}
QLineEdit#search {{ border-radius: 4px; padding-left: 10px; }}
QDialog QPlainTextEdit {{ border-radius: 4px; border-bottom: 1px solid {c['input_edge']}; }}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QTimeEdit:focus, QComboBox:focus {{
  background: {c['body_bg']}; border-color: {c['border']}; border-bottom: 2px solid {c['primary']}; }}
QComboBox QAbstractItemView {{ border-radius: 8px; padding: 4px;
  selection-background-color: {c['hover']}; selection-color: {c['body']}; }}
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{ background: transparent; border-left: none; }}
QAbstractSpinBox::up-button:hover, QAbstractSpinBox::down-button:hover {{ background: {shade}; }}
QCheckBox::indicator, QTreeView::indicator, QTableView::indicator {{ border-radius: 4px; border-color: {c['input_edge']}; }}

QTableView, QTableWidget {{ selection-background-color: {c['selection']}; }}
QTableView::item, QTableWidget::item {{ border-bottom: 1px solid {c['border_soft']}; }}
QTableView::item:hover {{ background: {c['hover']}; }}
QTableView::item:selected, QTableWidget::item:selected {{ background: {c['selection']}; color: {c['emphasis']}; }}
QHeaderView::section {{ font-weight: 400; color: {c['secondary']}; padding: 7px 6px;
  border-right: 1px solid {c['border_soft']}; }}

QTreeWidget#sidebar {{ background: transparent; border-right: none; padding: 4px; }}
QTreeWidget#sidebar::item {{ padding: 5px 6px; border-radius: 4px; margin: 1px 0; border-left: 3px solid transparent; }}
QTreeWidget#sidebar::item:hover {{ background: {shade}; }}
QTreeWidget#sidebar::item:selected {{ background: {c['hover'] if not dark else c['secondary_bg']}; color: {c['emphasis']};
  border-left: 3px solid {c['primary']}; }}

QTabWidget::pane {{ border-top: 1px solid {c['border']}; }}
QTabBar::tab {{ color: {c['secondary']}; font-weight: 400; padding: 8px 12px; border-radius: 4px;
  border-bottom: 3px solid transparent; }}
QTabBar::tab:hover {{ color: {c['body']}; background: {shade}; }}
QTabBar::tab:selected {{ color: {c['emphasis']}; font-weight: 600; border-bottom: 3px solid {c['primary']}; }}

QScrollBar:vertical {{ width: 8px; margin: 3px 1px; }}
QScrollBar::handle:vertical {{ background: {c['input_edge']}; border-radius: 3px; min-height: 30px; }}
QScrollBar:horizontal {{ height: 8px; margin: 1px 3px; }}
QScrollBar::handle:horizontal {{ background: {c['input_edge']}; border-radius: 3px; min-width: 30px; }}

QMenu {{ background: {c['body_bg'] if not dark else '#2c2c2c'}; border: 1px solid {c['border']};
  border-radius: 8px; padding: 4px; }}
QMenu::item {{ padding: 6px 22px 6px 10px; border-radius: 4px; margin: 1px 0; }}
QMenu::item:selected {{ background: {shade}; color: {c['body']}; }}
QToolTip {{ border-radius: 4px; }}
QStatusBar {{ background: {c['page_bg']}; border-top: 1px solid {c['border']}; }}
QToolButton#sbItem {{ border-left: none; border-radius: 4px; margin: 1px; font-size: 9pt; color: {c['body']};
  padding: 3px 9px; }}
QToolButton#sbItem:hover {{ background: {shade}; color: {c['body']}; }}
""" + (f"""
/* Mica: DWM paints the backdrop wherever the main window itself draws nothing */
QMainWindow, QMainWindow > QWidget#page {{ background: transparent; }}
/* small text right on the backdrop turns soft (grayscale AA over DWM): the status bar stays opaque */
QStatusBar {{ background: {c['page_bg']}; }}
QFrame#card, QFrame#headCard, QFrame#kpi {{ background: {'rgba(43,43,43,235)' if dark else 'rgba(255,255,255,225)'}; }}
""" if mica_active() else "")
