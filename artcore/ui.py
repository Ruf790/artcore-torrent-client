"""Qt (PySide6) user interface in the ArtCore style."""
from __future__ import annotations

import os
import platform
import shutil
import socket
from collections import deque
from datetime import datetime

import qtawesome as qta
from PySide6.QtCore import (Qt, QPoint, QAbstractTableModel, QModelIndex, QTimer, QUrl, QThread,
                            Signal, QRectF, QSortFilterProxyModel, QSize, QTime, QPointF)
from PySide6.QtGui import (QAction, QColor, QDesktopServices, QFont, QIcon, QPainter, QPixmap,
                           QPainterPath, QKeySequence, QGuiApplication, QFontMetrics, QPen)
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QButtonGroup, QCheckBox,
                               QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog,
                               QFormLayout, QTimeEdit,
                               QFrame, QGridLayout, QHBoxLayout, QHeaderView, QInputDialog, QLabel,
                               QLineEdit, QMainWindow, QMenu, QMessageBox,
                               QPushButton, QSpinBox, QSplitter, QStackedWidget, QStyle,
                               QStyledItemDelegate, QSystemTrayIcon, QTableView, QTableWidget,
                               QTableWidgetItem, QTabWidget, QToolButton, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget)

from . import APP_TITLE, VERSION, config, theme, winint
from .engine import (Engine, TorrentRow, create_torrent, torrent_files,
                     PRIO_SKIP, PRIO_NORMAL, PRIO_HIGH)
from .i18n import t
from . import widgets

C = theme.current   # live colour dict, updated in place on theme switch


# ============================================================
#  Formatting helpers
# ============================================================

def fmt_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.2f} {unit}"
        n /= 1024
    return f"{n:.2f} TB"


def fmt_speed(n: int) -> str:
    return "" if n <= 0 else fmt_size(n) + "/s"


def fmt_eta(sec: int) -> str:
    if sec < 0:
        return "∞" if sec == -1 else ""
    d, rem = divmod(sec, 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    if d:
        return f"{d}d {h}h"
    if h:
        return f"{h}h {m}m"
    return f"{m}m {s}s"


def fmt_ratio(r: float) -> str:
    return "∞" if r == float("inf") else f"{r:.2f}"


def app_icon(size: int = 256) -> QIcon:
    """Logo drawn in code: white 'A' on the site's primary blue."""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, size, size), size * 0.22, size * 0.22)
    p.fillPath(path, QColor("#0d6efd"))
    f = QFont(theme.TITLE_FONT, int(size * 0.5))
    f.setWeight(QFont.Bold)
    p.setFont(f)
    p.setPen(QColor("#ffffff"))
    p.drawText(QRectF(0, -size * 0.03, size, size), Qt.AlignCenter, "A")
    p.end()
    return QIcon(pm)


def icon(name: str, color_key: str = "secondary") -> QIcon:
    return qta.icon(name, color=C.get(color_key, color_key))


# ============================================================
#  Torrent table model + filter
# ============================================================

COLS = ["col_name", "col_size", "col_progress", "col_state", "col_down", "col_up",
        "col_seeds", "col_peers", "col_ratio", "col_eta", "col_tracker", "col_category", "col_tags"]
(C_NAME, C_SIZE, C_PROG, C_STATE, C_DOWN, C_UP, C_SEEDS, C_PEERS,
 C_RATIO, C_ETA, C_TRACKER, C_CAT, C_TAGS) = range(len(COLS))
NO_TAG = "\x00none"
NO_CATEGORY = "\x00none"

# state → soft badge colours (.st-soft-*)
STATE_STYLE = {
    "downloading": ("primary_subtle", "primary_text"),
    "metadata":    ("warning_subtle", "warning_text"),
    "checking":    ("warning_subtle", "warning_text"),
    "seeding":     ("success_subtle", "success_text"),
    "finished":    ("success_subtle", "success_text"),
    "paused":      ("muted_subtle",   "secondary"),
    "queued":      ("muted_subtle",   "secondary"),
    "error":       ("danger_subtle",  "danger_text"),
    "tracker_error": ("danger_subtle", "danger_text"),
}
STATE_ICON = {
    "downloading": "fa6s.arrow-down", "metadata": "fa6s.magnet", "checking": "fa6s.rotate",
    "seeding": "fa6s.arrow-up", "finished": "fa6s.check", "paused": "fa6s.pause",
    "queued": "fa6s.clock", "error": "fa6s.triangle-exclamation",
    "tracker_error": "fa6s.server",
}
FILTER_STATES = {
    "all":         None,
    "downloading": {"downloading", "metadata", "checking"},
    "seeding":     {"seeding", "finished"},
    "paused":      {"paused", "queued"},
    "error":       {"error", "tracker_error"},
}


class TorrentModel(QAbstractTableModel):
    def __init__(self):
        super().__init__()
        self.rows: list[TorrentRow] = []

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return len(COLS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return t(COLS[section])
        return None

    def data(self, index, role=Qt.DisplayRole):
        r = self.rows[index.row()]
        c = index.column()
        if role == Qt.DisplayRole:
            return {
                C_NAME: r.name, C_SIZE: fmt_size(r.size), C_PROG: f"{r.progress * 100:.1f}%",
                C_STATE: t("st_" + r.state), C_DOWN: fmt_speed(r.down_rate), C_UP: fmt_speed(r.up_rate),
                C_SEEDS: str(r.seeds), C_PEERS: str(r.peers), C_RATIO: fmt_ratio(r.ratio),
                C_ETA: fmt_eta(r.eta) if r.state == "downloading" else "", C_TRACKER: r.tracker_host,
                C_CAT: r.category, C_TAGS: ", ".join(r.tags),
            }[c]
        if role == Qt.UserRole:  # sort key
            return {
                C_NAME: r.name.lower(), C_SIZE: r.size, C_PROG: r.progress, C_STATE: r.state,
                C_DOWN: r.down_rate, C_UP: r.up_rate, C_SEEDS: r.seeds, C_PEERS: r.peers,
                C_RATIO: r.ratio if r.ratio != float("inf") else 1e18,
                C_ETA: r.eta if r.eta >= 0 else 1e18, C_TRACKER: r.tracker_host,
                C_CAT: r.category.lower(), C_TAGS: ", ".join(r.tags).lower(),
            }[c]
        if role == Qt.ForegroundRole:
            dark = theme.current_name == "dark"
            if c == C_DOWN and r.down_rate > 0:
                return QColor(C["primary_text"] if dark else C["primary"])
            if c == C_UP and r.up_rate > 0:
                return QColor(C["success_text"] if dark else C["success"])
            if c in (C_TRACKER, C_ETA, C_SIZE, C_CAT, C_TAGS):
                return QColor(C["secondary"])
        if role == Qt.DecorationRole:
            if c == C_NAME:
                return widgets.torrent_icon(r.kind)
            if c == C_CAT and r.category:
                return widgets.icon("fa6s.tag", "purple")
            if c == C_TAGS and r.tags:
                return widgets.icon("fa6s.hashtag", "teal")
            if c == C_TRACKER and r.tracker_host:
                return widgets.icon("fa6s.globe" if r.tracker_host != "DHT" else "fa6s.diagram-project", "info")
        if role == Qt.FontRole and c == C_NAME:
            f = QFont()
            f.setWeight(QFont.DemiBold)
            return f
        if role == Qt.ToolTipRole:
            if c == C_STATE and r.hnr and not r.error:
                return t("hnr_tip", r.hnr)
            return r.error or (r.name if c == C_NAME else None)
        if role == Qt.TextAlignmentRole and c not in (C_NAME, C_STATE, C_TRACKER, C_CAT, C_TAGS):
            return int(Qt.AlignRight | Qt.AlignVCenter)
        return None

    def update(self, new_rows: list[TorrentRow]) -> None:
        """Update in place so selection/sorting survive the 1 s refresh."""
        new_by_key = {r.key: r for r in new_rows}
        for i in range(len(self.rows) - 1, -1, -1):
            if self.rows[i].key not in new_by_key:
                self.beginRemoveRows(QModelIndex(), i, i)
                del self.rows[i]
                self.endRemoveRows()
        known = set()
        for i, r in enumerate(self.rows):
            self.rows[i] = new_by_key[r.key]
            known.add(r.key)
        if self.rows:
            self.dataChanged.emit(self.index(0, 0), self.index(len(self.rows) - 1, len(COLS) - 1))
        added = [r for r in new_rows if r.key not in known]
        if added:
            self.beginInsertRows(QModelIndex(), len(self.rows), len(self.rows) + len(added) - 1)
            self.rows.extend(added)
            self.endInsertRows()


class FilterProxy(QSortFilterProxyModel):
    def __init__(self):
        super().__init__()
        self.state = "all"
        self.tracker = ""
        self.category = ""
        self.tag = ""
        self.text = ""
        self.setSortRole(Qt.UserRole)

    def set_filter(self, state=None, tracker=None, text=None, category=None, tag=None):
        if tag is not None:
            self.tag = tag
        if category is not None:
            self.category = category
        if state is not None:
            self.state = state
        if tracker is not None:
            self.tracker = tracker
        if text is not None:
            self.text = text.lower().strip()
        self.invalidateFilter()

    def filterAcceptsRow(self, row, parent):
        r = self.sourceModel().rows[row]
        states = FILTER_STATES[self.state]
        return ((states is None or r.state in states)
                and (not self.tracker or r.tracker_host == self.tracker)
                and (not self.category or r.category == ("" if self.category == NO_CATEGORY else self.category))
                and (not self.tag or (not r.tags if self.tag == NO_TAG else self.tag in r.tags))
                and (not self.text or self.text in r.name.lower()))


def _row_of(index) -> TorrentRow:
    m = index.model()
    src = m.mapToSource(index) if isinstance(m, QSortFilterProxyModel) else index
    return src.model().rows[src.row()]


def _paint_cell_bg(painter, option):
    if option.state & QStyle.State_Selected:
        painter.fillRect(option.rect, QColor(C["selection"]))
    painter.setPen(QColor(C["border_soft"]))
    painter.drawLine(option.rect.bottomLeft(), option.rect.bottomRight())


class ProgressDelegate(QStyledItemDelegate):
    """Percentage + thin rounded bar, like the site's progress bars."""
    def paint(self, painter, option, index):
        r = _row_of(index)
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        _paint_cell_bg(painter, option)
        rect = option.rect.adjusted(8, 0, -8, 0)
        f = painter.font(); f.setPointSizeF(8.5); painter.setFont(f)
        tw = 46
        painter.setPen(QColor(C["body"]))
        painter.drawText(QRectF(rect.left(), rect.top(), tw, rect.height()),
                         Qt.AlignVCenter | Qt.AlignLeft, f"{r.progress * 100:.1f}%")
        bar = QRectF(rect.left() + tw + 4, rect.center().y() - 3, rect.width() - tw - 4, 6)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(C["secondary_bg"]))
        painter.drawRoundedRect(bar, 3, 3)
        if r.progress > 0:
            key = ("success" if r.state in ("seeding", "finished") else
                   "danger" if r.state == "error" else
                   "warning" if r.state == "tracker_error" else
                   "secondary" if r.state in ("paused", "queued") else "primary")
            fill = QRectF(bar.left(), bar.top(), max(6.0, bar.width() * r.progress), 6)
            if theme.current_style != "muted" and key in ("primary", "success"):
                from PySide6.QtGui import QLinearGradient
                g = QLinearGradient(fill.topLeft(), fill.topRight())
                g.setColorAt(0, QColor(C[key]))
                g.setColorAt(1, QColor(C["teal"] if key == "success" else C["purple"]))
                painter.setBrush(g)
            else:
                painter.setBrush(QColor(C[key]))
            painter.drawRoundedRect(fill, 3, 3)
        painter.restore()


class BadgeDelegate(QStyledItemDelegate):
    """Status as a soft pill badge (.st-badge .st-soft-*)."""
    def paint(self, painter, option, index):
        r = _row_of(index)
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        _paint_cell_bg(painter, option)
        bg, fg = STATE_STYLE.get(r.state, ("muted_subtle", "secondary"))
        f = painter.font(); f.setPointSizeF(8.5); f.setWeight(QFont.DemiBold); painter.setFont(f)
        text = t("st_" + r.state)
        fm = QFontMetrics(f)
        room = option.rect.width() - 12 - 36          # pill never spills past the cell
        if fm.horizontalAdvance(text) > room:
            text = fm.elidedText(text, Qt.ElideRight, max(room, 10))
        w = fm.horizontalAdvance(text) + 36
        pill = QRectF(option.rect.left() + 6, option.rect.center().y() - 10, w, 20)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(C[bg]))
        painter.drawRoundedRect(pill, 10, 10)
        ic = STATE_ICON.get(r.state, "fa6s.circle")
        painter.drawPixmap(int(pill.left() + 9), int(pill.center().y() - 5),
                           qta.icon(ic, color=C[fg]).pixmap(10, 10))
        painter.setPen(QColor(C[fg]))
        painter.drawText(pill.adjusted(16, 0, 0, 0), Qt.AlignCenter, text)
        if r.hnr and pill.right() + 18 < option.rect.right():       # still owes the tracker seeding time
            painter.drawPixmap(int(pill.right() + 6), int(pill.center().y() - 6),
                               qta.icon("fa6s.shield-halved", color=C["warning"]).pixmap(12, 12))
        painter.restore()


# ============================================================
#  Small widgets
# ============================================================

def vsep() -> QFrame:
    f = QFrame()
    f.setObjectName("vsep")
    return f


def card() -> QFrame:
    f = QFrame()
    f.setObjectName("card")
    return f


class Kpi(QFrame):
    """KPI tile. muted: gray with a faint corner icon (.st-kpi--muted);
    soft: coloured icon square + coloured value; vivid: gradient tile."""
    def __init__(self, icon_name: str, tone: str):
        super().__init__()
        self.setObjectName("kpi")
        self.setProperty("tone", tone)
        self.icon_name, self.tone = icon_name, tone
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 10, 12, 10)
        lay.setSpacing(12)
        self.ico_box = QLabel()
        self.ico_box.setObjectName("kpiIco")
        self.ico_box.setProperty("tone", tone)
        self.ico_box.setFixedSize(44, 44)
        self.ico_box.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.ico_box)
        col = QVBoxLayout()
        col.setSpacing(1)
        self.val = QLabel("—"); self.val.setObjectName("kpiVal")
        self.lbl = QLabel(); self.lbl.setObjectName("kpiLbl")
        col.addWidget(self.val)
        col.addWidget(self.lbl)
        lay.addLayout(col)
        lay.addStretch()
        self.ico = QLabel()
        self.ico.setStyleSheet("background: transparent;")
        lay.addWidget(self.ico)
        self.retheme()

    def retheme(self):
        style = theme.current_style
        fam = theme.TONES[self.tone]
        self.ico_box.setVisible(style == "soft")
        self.ico.setVisible(style != "soft")
        if style == "soft":
            col = C[fam + "_text"] if theme.current_name == "dark" else C[fam]
            self.ico_box.setPixmap(qta.icon(self.icon_name, color=col).pixmap(20, 20))
        else:
            c = QColor("#ffffff" if style == "vivid" else C["secondary"])
            c.setAlpha(110 if style == "vivid" else 70)
            self.ico.setPixmap(qta.icon(self.icon_name, color=c).pixmap(34, 34))
        # dynamic-property selectors need a re-polish after a stylesheet change
        for w in (self, self.ico_box, self.val, self.lbl):
            w.style().unpolish(w)
            w.style().polish(w)

    def set(self, val: str, lbl: str):
        self.val.setText(val)
        self.lbl.setText(lbl)


class SpeedGraph(QWidget):
    """Download / upload history, drawn like the site's charts: soft areas, thin lines."""
    def __init__(self):
        super().__init__()
        self.down: deque[int] = deque(maxlen=3600)
        self.up: deque[int] = deque(maxlen=3600)
        self.span = 300          # seconds shown
        self.setMinimumHeight(140)

    def push(self, down: int, up: int):
        self.down.append(down)
        self.up.append(up)
        if self.isVisible():
            self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        f = p.font(); f.setPointSizeF(8.5); p.setFont(f)
        fm = QFontMetrics(f)
        down = list(self.down)[-self.span:]
        up = list(self.up)[-self.span:]
        top = max(down + up + [10 * 1024]) * 1.15
        left, right, tp, bottom = 74, self.width() - 14, 30, self.height() - 22
        w, h = right - left, bottom - tp

        # grid + y labels
        p.setPen(QPen(QColor(C["border_soft"]), 1))
        for i in range(5):
            y = tp + h * i / 4
            p.drawLine(QPointF(left, y), QPointF(right, y))
            p.setPen(QColor(C["secondary"]))
            p.drawText(QRectF(0, y - 8, left - 8, 16), Qt.AlignRight | Qt.AlignVCenter,
                       fmt_size(top * (4 - i) / 4) + "/s")
            p.setPen(QPen(QColor(C["border_soft"]), 1))
        # x labels
        p.setPen(QColor(C["secondary"]))
        mins = self.span // 60
        p.drawText(QRectF(left, bottom + 4, 80, 16), Qt.AlignLeft, f"-{mins} min")
        p.drawText(QRectF(right - 80, bottom + 4, 80, 16), Qt.AlignRight, "0")

        def series(vals, color_key):
            if len(vals) < 2:
                return
            step = w / (self.span - 1)
            x0 = right - step * (len(vals) - 1)
            pts = [QPointF(x0 + i * step, bottom - h * v / top) for i, v in enumerate(vals)]
            area = QPainterPath(QPointF(pts[0].x(), bottom))
            for pt in pts:
                area.lineTo(pt)
            area.lineTo(QPointF(pts[-1].x(), bottom))
            fill = QColor(C[color_key]); fill.setAlpha(40)
            p.fillPath(area, fill)
            line = QPainterPath(pts[0])
            for pt in pts[1:]:
                line.lineTo(pt)
            p.setPen(QPen(QColor(C[color_key]), 1.6))
            p.drawPath(line)

        series(down, "primary")
        series(up, "success")

        # legend with current values
        x = left
        for key, vals, label in (("primary", down, t("kpi_down")), ("success", up, t("kpi_up"))):
            p.setPen(Qt.NoPen); p.setBrush(QColor(C[key]))
            p.drawEllipse(QPointF(x + 5, 14), 4, 4)
            txt = f"{label}  {fmt_size(vals[-1] if vals else 0)}/s"
            p.setPen(QColor(C["body"]))
            p.drawText(QPointF(x + 14, 18), txt)
            x += 14 + fm.horizontalAdvance(txt) + 24
        p.end()


class PieceBar(QWidget):
    """Which pieces are already here — one thin rounded bar."""
    def __init__(self):
        super().__init__()
        self.pieces: list[bool] = []
        self.done = False
        self.setFixedHeight(14)

    def set_pieces(self, pieces: list[bool], done: bool):
        self.pieces, self.done = pieces, done
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(0, 2, self.width(), self.height() - 4)
        clip = QPainterPath(); clip.addRoundedRect(r, 5, 5)
        p.setClipPath(clip)
        p.fillRect(r, QColor(C["secondary_bg"]))
        n = len(self.pieces)
        if n:
            base = QColor(C["success" if self.done else "primary"])
            cols = max(1, int(r.width()))
            per = n / cols
            for x in range(cols):
                a, b = int(x * per), max(int((x + 1) * per), int(x * per) + 1)
                chunk = self.pieces[a:b]
                frac = sum(chunk) / len(chunk) if chunk else 0
                if frac:
                    c = QColor(base); c.setAlphaF(0.25 + 0.75 * frac)
                    p.fillRect(QRectF(r.left() + x, r.top(), 1.2, r.height()), c)
        p.end()


LOG_KINDS = ("system", "network", "torrent", "tracker", "error")
LOG_STYLE = {   # soft badge colours per event type, like the site's .st-soft-*
    "system":  ("muted_subtle",   "secondary"),
    "network": ("info_subtle",    "info_text"),
    "torrent": ("success_subtle", "success_text"),
    "tracker": ("primary_subtle", "primary_text"),
    "error":   ("danger_subtle",  "danger_text"),
}


LOG_ICON = {"system": "fa6s.gear", "network": "fa6s.wifi", "torrent": "fa6s.download",
            "tracker": "fa6s.server", "error": "fa6s.triangle-exclamation"}


class LogView(QWidget):
    """Global log like BitComet's: No. / Time / Type / Message, coloured rows, filter."""
    MAX = 3000

    def __init__(self):
        super().__init__()
        self.entries: list[tuple[int, str, str, str]] = []   # (no, time, kind, text)
        self.counter = 0
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 4)
        bar = QHBoxLayout()
        self.filter = QComboBox()
        self.filter.addItem(widgets.icon("fa6s.list"), t("log_all"), "")
        for k in LOG_KINDS:
            self.filter.addItem(widgets.icon(LOG_ICON[k], LOG_STYLE[k][1]), t("lk_" + k), k)
        self.filter.currentIndexChanged.connect(lambda _: self.rebuild())
        bar.addWidget(self.filter)
        bar.addStretch()
        clear = QPushButton(t("log_clear"))
        clear.clicked.connect(self.clear)
        bar.addWidget(clear)
        lay.addLayout(bar)

        self.tbl = QTableWidget(0, 4)
        self.tbl.setHorizontalHeaderLabels([t("log_no"), t("log_time"), t("log_type"), t("log_msg")])
        self.tbl.verticalHeader().hide()
        self.tbl.verticalHeader().setDefaultSectionSize(26)
        self.tbl.setShowGrid(False)
        self.tbl.setWordWrap(False)
        self.tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl.setSelectionBehavior(QAbstractItemView.SelectRows)
        hh = self.tbl.horizontalHeader()
        hh.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        hh.setHighlightSections(False)
        hh.setSectionResizeMode(3, QHeaderView.Stretch)
        for c, w in ((0, 50), (1, 150), (2, 125)):
            self.tbl.setColumnWidth(c, w)
        self.tbl.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tbl.customContextMenuRequested.connect(self._menu)
        lay.addWidget(self.tbl, 1)

    def add(self, kind: str, text: str):
        self.counter += 1
        e = (self.counter, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), kind, text)
        self.entries.append(e)
        if len(self.entries) > self.MAX:
            del self.entries[: len(self.entries) - self.MAX]
            if self.tbl.rowCount() > self.MAX:
                self.tbl.removeRow(0)
        want = self.filter.currentData()
        if not want or want == kind:
            self._append_row(e)

    def _append_row(self, e):
        no, ts, kind, text = e
        r = self.tbl.rowCount()
        self.tbl.insertRow(r)
        bg, fg = LOG_STYLE.get(kind, LOG_STYLE["system"])
        for c, v in enumerate((str(no), ts, t("lk_" + kind), text)):
            it = QTableWidgetItem(v)
            if c == 2:
                it.setForeground(QColor(C[fg]))
                it.setFont(QFont(it.font().family(), -1, QFont.DemiBold))
                it.setIcon(widgets.icon(LOG_ICON.get(kind, "fa6s.circle-info"), fg))
            if kind == "error":
                it.setBackground(QColor(C["danger_subtle"]))
            if c == 3:
                it.setToolTip(text)
            self.tbl.setItem(r, c, it)
        sb = self.tbl.verticalScrollBar()
        if sb.value() >= sb.maximum() - 30:      # follow the tail unless the user scrolled up
            self.tbl.scrollToBottom()

    def rebuild(self):
        want = self.filter.currentData()
        self.tbl.setRowCount(0)
        for e in self.entries:
            if not want or want == e[2]:
                self._append_row(e)

    def clear(self):
        self.entries.clear()
        self.tbl.setRowCount(0)

    def _menu(self, pos):
        rows = sorted({i.row() for i in self.tbl.selectedItems()})
        if not rows:
            return
        m = QMenu(self)

        def copy():
            lines = ["\t".join(self.tbl.item(r, c).text() for c in range(4)) for r in rows]
            QGuiApplication.clipboard().setText("\n".join(lines))
        m.addAction(icon("fa6s.copy"), t("log_copy"), copy)
        m.exec(self.tbl.viewport().mapToGlobal(pos))


def _local_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))          # no packet is sent; just picks the outgoing interface
            return s.getsockname()[0]
    except OSError:
        return "—"


def _fmt_uptime(sec: int) -> str:
    d, rem = divmod(sec, 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    return (f"{d}d " if d else "") + f"{h}:{m:02d}:{s:02d}"


def _country_strip(by_country: dict[str, list], total: int, top: int = 10) -> str:
    """HTML: flag + code + count for the biggest countries, then "+N more"."""
    items = sorted(by_country.items(), key=lambda kv: -kv[1][1])
    parts = []
    for code, (name, n) in items[:top]:
        path = widgets.flag_path(code)
        img = (f'<img src="{QUrl.fromLocalFile(path).toString()}" width="20" height="15" style="vertical-align:middle">'
               if path else "&#127760;")
        label = code or t("geo_unknown")
        parts.append(f'<span title="{name}">{img}&nbsp;<b>{label}</b>&nbsp;{n}</span>')
    rest = len(items) - top
    head = t("geo_summary", total, len([c for c in by_country if c]))
    tail = f" &nbsp; {t('geo_more', rest)}" if rest > 0 else ""
    return f"{head} &nbsp;&nbsp; " + " &nbsp;·&nbsp; ".join(parts) + tail


class MiniWidget(QWidget):
    """Floating always-on-top speed window (BitComet-style): drag to move, double-click opens the
    client, right-click for a menu. Small graph of the last two minutes behind the numbers."""
    def __init__(self, win: "MainWindow"):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.win = win
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(196, 58)
        self.down: deque[int] = deque(maxlen=120)
        self.up: deque[int] = deque(maxlen=120)
        self._drag = None
        self.setCursor(Qt.SizeAllCursor)
        self.setToolTip(t("mini_tip"))

    def push(self, down: int, up: int):
        self.down.append(down)
        self.up.append(up)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setPen(QPen(QColor(C["border"]), 1))
        p.setBrush(QColor(C["body_bg"]))
        p.drawRoundedRect(r, 10, 10)
        # sparkline
        vals_d, vals_u = list(self.down), list(self.up)
        top = max(vals_d + vals_u + [32 * 1024]) * 1.1
        if len(vals_d) > 1:
            for vals, key in ((vals_d, "primary"), (vals_u, "success")):
                path = QPainterPath()
                n = len(vals)
                for i, v in enumerate(vals):
                    x = r.left() + 4 + (r.width() - 8) * (i + 120 - n) / 119
                    y = r.bottom() - 4 - (r.height() - 10) * v / top
                    path.lineTo(x, y) if i else path.moveTo(x, y)
                col = QColor(C[key]); col.setAlpha(110)
                p.setPen(QPen(col, 1.4))
                p.setBrush(Qt.NoBrush)
                p.drawPath(path)
        f = p.font(); f.setPointSizeF(9.5); f.setWeight(QFont.DemiBold); p.setFont(f)
        d = vals_d[-1] if vals_d else 0
        u = vals_u[-1] if vals_u else 0
        for i, (txt, key, ic) in enumerate(((fmt_speed(d) or "0 B/s", "primary", "fa6s.arrow-down"),
                                            (fmt_speed(u) or "0 B/s", "success", "fa6s.arrow-up"))):
            y = r.top() + 7 + i * 22
            p.drawPixmap(int(r.left() + 10), int(y + 3), qta.icon(ic, color=C[key]).pixmap(12, 12))
            p.setPen(QColor(C[key]))
            p.drawText(QRectF(r.left() + 28, y, r.width() - 36, 18), Qt.AlignVCenter | Qt.AlignLeft, txt)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._drag is not None:
            self.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, e):
        if self._drag is not None:
            self._drag = None
            self.win.cfg["mini_pos"] = [self.x(), self.y()]
            config.save(self.win.cfg)

    def mouseDoubleClickEvent(self, e):
        self.win.show_normal()

    def contextMenuEvent(self, e):
        m = QMenu(self)
        m.addAction(icon("fa6s.window-maximize"), t("show"), self.win.show_normal)
        m.addAction(icon("fa6s.eye-slash"), t("mini_hide"), lambda: self.win.set_mini(False))
        m.exec(e.globalPos())


class StatsView(QTableWidget):
    """Global statistics like BitComet's Statistics tab, grouped Item / Value rows."""
    def __init__(self):
        super().__init__(0, 2)
        self.setHorizontalHeaderLabels([t("stat_item"), t("stat_value")])
        self.verticalHeader().hide()
        self.verticalHeader().setDefaultSectionSize(28)
        self.setShowGrid(False)
        self.setFocusPolicy(Qt.NoFocus)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setSelectionMode(QAbstractItemView.NoSelection)
        hh = self.horizontalHeader()
        hh.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        hh.setHighlightSections(False)
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        self.setColumnWidth(0, 300)
        self.setIconSize(QSize(20, 15))            # country flags are 4:3
        self.firewall: bool | None = None
        self._fw_checked = False
        try:
            import psutil
            self.proc = psutil.Process()
            self.proc.cpu_percent(None)          # prime the CPU counter
        except Exception:
            self.proc = None

    @property
    def SECTION_ICONS(self):
        return {t("sg_general"): ("fa6s.circle-info", "primary"), t("sg_network"): ("fa6s.network-wired", "info"),
                t("sg_speed"): ("fa6s.gauge-high", "orange"), t("sg_system"): ("fa6s.microchip", "purple"),
                t("sg_totals"): ("fa6s.square-poll-vertical", "success"),
                t("sg_trackers"): ("fa6s.server", "info"),
                t("sg_countries"): ("fa6s.earth-europe", "info")}

    def set_rows(self, rows: list[tuple[str, str] | str]):
        """str = section header, (label, value) = item row."""
        if self.rowCount() != len(rows):
            self.setRowCount(len(rows))
            self.clearSpans()
        for i, r in enumerate(rows):
            if isinstance(r, str):
                if self.columnSpan(i, 0) != 2:
                    self.setSpan(i, 0, 1, 2)
                it = self._cell(i, 0)
                it.setText(r)
                it.setIcon(widgets.icon(*self.SECTION_ICONS.get(r, ("fa6s.circle-info", "primary"))))
                f = QFont(theme.TITLE_FONT, 11); f.setWeight(QFont.DemiBold)
                it.setFont(f)
                it.setForeground(QColor(C["emphasis"]))
                it.setBackground(QColor(C["tertiary_bg"]))
            else:
                if self.columnSpan(i, 0) != 1:
                    self.setSpan(i, 0, 1, 1)
                for c, v in enumerate(r[:2]):
                    it = self._cell(i, c)
                    it.setText("    " + v if c == 0 else v)
                    it.setForeground(QColor(C["secondary"] if c == 0 else C["body"]))
                    it.setFont(QFont())
                    it.setBackground(QColor(0, 0, 0, 0))
                    if c == 0:
                        it.setIcon((widgets.flag_icon(r[2]) or QIcon()) if len(r) > 2 else QIcon())

    def _country_rows(self, win) -> list:
        """Peers of all torrents by country (top 15), refreshed every 5 s."""
        import time as _t
        if win.geo is None or win.geo.status != "ready":
            return []
        if _t.time() - getattr(self, "_geo_at", 0) > 5:
            self._geo_at = _t.time()
            counts: dict[str, list] = {}
            for key in list(win.engine.handles):
                for p in win.engine.peers(key):
                    code, name = win.geo.lookup(p["host"])
                    ent = counts.setdefault(code, [name, 0, 0, 0])
                    ent[1] += 1
                    ent[2] += p["down"]
                    ent[3] += p["up"]
            self._geo_rows = sorted(counts.items(), key=lambda kv: -kv[1][1])[:15]
        rows = getattr(self, "_geo_rows", [])
        if not rows:
            return []
        return [t("sg_countries")] + [
            (f"{name or t('geo_unknown')} ({code})" if code else t("geo_unknown"),
             t("st_country_v", n, fmt_size(d) + "/s", fmt_size(u) + "/s"), code)
            for code, (name, n, d, u) in rows]

    def _cell(self, r: int, c: int) -> QTableWidgetItem:
        it = self.item(r, c)
        if it is None:
            it = QTableWidgetItem()
            self.setItem(r, c, it)
        return it

    @staticmethod
    def _geo_text(win) -> str:
        g = win.geo
        if g is None:
            return t("pm_off")
        if g.status == "ready":
            return t("geo_ready")
        if g.status.startswith("error:"):
            return t("geo_error", g.status[6:][:80])
        return t("geo_loading")

    def refresh(self, win: "MainWindow"):
        eng, cfg = win.engine, win.cfg
        st = eng.stats()
        rows = win.model.rows
        active = sum(1 for r in rows if r.state in ("downloading", "seeding", "metadata", "checking", "tracker_error"))
        if not self._fw_checked and winint.IS_WIN:
            self._fw_checked = True
            self.firewall = winint.firewall_allowed()

        def limit(kib):
            return t("st_limit", fmt_size(kib * 1024) + "/s") if kib else t("st_nolimit")

        pm = st["portmap"]
        if not cfg.get("upnp", True):
            pm_text = t("pm_off")
        elif not pm:
            pm_text = t("pm_wait")
        else:
            pm_text = ", ".join(t("pm_ok", k, v.split(":", 1)[1]) if v.startswith("ok:") else t("pm_err", k)
                                for k, v in sorted(pm.items()))
        fw = {True: t("fw_ok"), False: t("fw_no"), None: t("fw_unknown")}[self.firewall]
        sched_on = win._sched_state
        if cfg.get("sched_enabled"):
            sched = t("st_sched_on" if sched_on else "st_sched_idle", cfg.get("sched_from"), cfg.get("sched_to"))
        else:
            sched = t("pm_off")
        dl_lim = cfg.get("sched_dl", 0) if sched_on else cfg.get("dl_limit", 0)
        ul_lim = cfg.get("sched_ul", 0) if sched_on else cfg.get("ul_limit", 0)

        cpu = mem = "—"
        if self.proc is not None:
            try:
                cpu = t("st_cpu_v", f"{self.proc.cpu_percent(None) / max(1, os.cpu_count() or 1):.1f}", os.cpu_count())
                mem = fmt_size(self.proc.memory_info().rss)
            except Exception:
                pass
        free = free_space(cfg.get("save_path", ""))
        all_dl, all_ul = st["all_down"], st["all_up"]
        ratio = (all_ul / all_dl) if all_dl else 0.0
        import libtorrent as lt

        self.set_rows([
            t("sg_general"),
            (t("st_version"), f"{APP_TITLE} {VERSION} · libtorrent {lt.__version__} · Python {platform.python_version()}"),
            (t("st_uptime"), _fmt_uptime(st["uptime"])),
            (t("st_tasks"), t("st_tasks_v", len(rows), active)),
            (t("st_cfg"), config.data_dir()),
            t("sg_network"),
            (t("st_lan"), _local_ip()),
            (t("st_wan"), st["wan_ip"] or t("st_wan_wait")),
            (t("st_port"), t("st_port_open" if st["incoming"] else "st_port_wait", st["port"])),
            (t("st_upnp"), pm_text),
            (t("st_firewall"), fw),
            (t("st_iface"), (cfg.get("bind_interface") + (f" ({eng.bound_ip})" if eng.bind_ok else " — " + t("meta_vpn_down", "").strip(" —")))
                            if cfg.get("bind_interface") else t("iface_any")),
            (t("st_enc"), t("enc_" + cfg.get("encryption", "prefer"))),
            (t("st_ipf"), t("st_ipf_v", eng.ip_filter_rules) if cfg.get("ip_filter") else t("pm_off")),
            (t("st_geo"), self._geo_text(win)),
            (t("st_dht"), str(st["dht_nodes"]) if cfg.get("dht") else t("meta_dht_off")),
            (t("st_conns"), str(st["peers"])),
            t("sg_speed"),
            (t("st_down"), f"{fmt_size(st['down_rate'])}/s  ·  {limit(dl_lim)}"),
            (t("st_up"), f"{fmt_size(st['up_rate'])}/s  ·  {limit(ul_lim)}"),
            (t("st_sched"), sched),
            t("sg_system"),
            (t("st_os"), f"{platform.system()} {platform.release()} ({platform.version()}, {platform.machine()})"),
            (t("st_cpu"), cpu),
            (t("st_mem"), mem),
            (t("st_disk"), fmt_size(free) if free is not None else "—"),
            t("sg_totals"),
            (t("st_all_down"), f"{fmt_size(all_dl)}  {t('st_session', fmt_size(st['session_down']))}"),
            (t("st_all_up"), f"{fmt_size(all_ul)}  {t('st_session', fmt_size(st['session_up']))}"),
            (t("st_ratio"), f"{ratio:.3f}"),
            t("sg_trackers"),
            *[(f"{host}  ({n})", t("st_tracker_v", fmt_size(up), fmt_size(down),
                                     f"{up / down:.2f}" if down else ("∞" if up else "—")))
              for host, up, down, n in eng.tracker_totals()],
            *self._country_rows(win),
        ])


# ============================================================
#  Dialogs
# ============================================================

def _section(text: str, icon_name: str | None = None, color: str = "primary") -> QLabel:
    """Section title in Oswald; with an icon it becomes an HTML label with the icon inline."""
    lab = QLabel()
    lab.setStyleSheet(f'font-family:"{theme.TITLE_FONT}"; font-size: 12.5pt; font-weight: 600; '
                      f'color:{C["emphasis"]}; padding-top: 8px;')
    if icon_name:
        import base64
        from PySide6.QtCore import QBuffer, QByteArray, QIODevice
        ba = QByteArray(); buf = QBuffer(ba); buf.open(QIODevice.WriteOnly)
        widgets.icon(icon_name, color).pixmap(18, 18).save(buf, "PNG")
        b64 = base64.b64encode(bytes(ba)).decode()
        lab.setTextFormat(Qt.RichText)
        lab.setText(f'<img src="data:image/png;base64,{b64}" width="18" height="18" '
                    f'style="vertical-align:middle">&nbsp;&nbsp;{text}')
    else:
        lab.setText(text)
    return lab


def _theme_title_bar(w: QWidget) -> None:
    winint.set_title_bar(int(w.winId()), theme.current_name == "dark", C["page_bg"], C["emphasis"])


class SettingsDialog(QDialog):
    def __init__(self, cfg: dict, parent=None):
        super().__init__(parent)
        _theme_title_bar(self)
        self.setWindowTitle(t("settings"))
        self.setMinimumWidth(560)
        self.cfg = dict(cfg)
        from PySide6.QtWidgets import QScrollArea
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 14)
        content = QWidget(); content.setObjectName("page")
        form = QFormLayout(content)
        form.setContentsMargins(20, 12, 20, 8)
        form.setVerticalSpacing(8)
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)
        self.resize(620, min(820, int(QGuiApplication.primaryScreen().availableGeometry().height() * 0.85)))

        def spin(val, lo, hi):
            s = QSpinBox()
            s.setRange(lo, hi)
            s.setValue(int(val))
            return s

        # ── general
        form.addRow(_section(t("set_grp_general"), "fa6s.sliders", "primary"))
        self.path = QLineEdit(cfg["save_path"])
        browse = QPushButton(t("set_browse"))
        browse.clicked.connect(self._browse)
        row = QHBoxLayout(); row.addWidget(self.path); row.addWidget(browse)
        form.addRow(t("set_save_path"), row)
        self.theme = QComboBox()
        for k in ("theme_system", "theme_light", "theme_dark"):
            self.theme.addItem(t(k))
        self.theme.setCurrentIndex({"system": 0, "light": 1, "dark": 2}.get(cfg.get("theme", "system"), 0))
        form.addRow(t("set_theme"), self.theme)
        self.style_combo = QComboBox()
        for k in ("muted", "soft", "vivid"):
            self.style_combo.addItem(t("style_" + k), k)
        self.style_combo.setCurrentIndex(max(0, self.style_combo.findData(cfg.get("ui_style", "soft"))))
        form.addRow(t("set_style"), self.style_combo)
        self.lang = QComboBox(); self.lang.addItems(["Русский", "English"])
        self.lang.setCurrentIndex(1 if cfg.get("language") == "en" else 0)
        form.addRow(t("set_lang"), self.lang)
        if config.is_portable():
            pl = QLabel(t("set_portable", config.data_dir())); pl.setObjectName("meta"); pl.setWordWrap(True)
            form.addRow(pl)
        self.tray = QCheckBox(t("set_tray")); self.tray.setChecked(cfg["minimize_to_tray"])
        form.addRow(self.tray)
        self.toasts = None
        if winint.IS_WIN:
            self.toasts = QCheckBox(t("set_toasts")); self.toasts.setChecked(cfg.get("toasts", True))
            form.addRow(self.toasts)
        self.show_add = QCheckBox(t("set_show_add")); self.show_add.setChecked(cfg.get("show_add_dialog", True))
        form.addRow(self.show_add)
        urow = QHBoxLayout()
        self.upd = QCheckBox(t("set_updates")); self.upd.setChecked(cfg.get("check_updates", True))
        urow.addWidget(self.upd, 1)
        ub = QPushButton(t("upd_check_now"))
        ub.clicked.connect(lambda: parent.check_updates(manual=True) if parent is not None else None)
        urow.addWidget(ub)
        form.addRow(urow)

        # ── connection
        form.addRow(_section(t("set_grp_conn"), "fa6s.plug", "info"))
        self.port     = spin(cfg["port"], 1024, 65535)
        self.dl       = spin(cfg["dl_limit"], 0, 10_000_000)
        self.ul       = spin(cfg["ul_limit"], 0, 10_000_000)
        self.act_dl   = spin(cfg["active_downloads"], 1, 100)
        self.act_seed = spin(cfg["active_seeds"], 1, 1000)
        form.addRow(t("set_port"), self.port)
        form.addRow(t("set_dl_limit"), self.dl)
        form.addRow(t("set_ul_limit"), self.ul)
        form.addRow(t("set_active_dl"), self.act_dl)
        form.addRow(t("set_active_seed"), self.act_seed)
        # ── seeding
        self.dht  = QCheckBox(t("set_dht"));  self.dht.setChecked(cfg["dht"])
        self.upnp = QCheckBox(t("set_upnp")); self.upnp.setChecked(cfg["upnp"])
        form.addRow(self.dht)
        form.addRow(self.upnp)

        from .netx import interfaces
        from .dialogs import folder_row
        form.addRow(_section(t("set_grp_net"), "fa6s.shield-halved", "success"))
        self.iface = QComboBox()
        self.iface.addItem(t("iface_any"), "")
        names = [n for n, _ in interfaces()]
        for n, ip in interfaces():
            self.iface.addItem(f"{n}  ({ip})", n)
        cur = cfg.get("bind_interface", "")
        if cur and cur not in names:
            self.iface.addItem(t("iface_missing", cur), cur)
        self.iface.setCurrentIndex(max(0, self.iface.findData(cur)))
        form.addRow(t("set_iface"), self.iface)
        ih = QLabel(t("iface_hint")); ih.setObjectName("meta"); ih.setWordWrap(True)
        form.addRow(ih)
        self.enc = QComboBox()
        for k in ("prefer", "require", "disable"):
            self.enc.addItem(t("enc_" + k), k)
        self.enc.setCurrentIndex(max(0, self.enc.findData(cfg.get("encryption", "prefer"))))
        form.addRow(t("set_enc"), self.enc)
        irow, self.ipf = folder_row(self, cfg.get("ip_filter", ""))
        # the browse button of folder_row picks folders — swap it for a file picker
        irow.itemAt(1).widget().clicked.disconnect()
        irow.itemAt(1).widget().clicked.connect(self._pick_ipf)
        self.ipf.setPlaceholderText("https://… / C:\\ipfilter.dat")
        form.addRow(t("set_ipfilter"), irow)
        self.geoip = QCheckBox(t("set_geoip")); self.geoip.setChecked(cfg.get("geoip", True))
        form.addRow(self.geoip)

        form.addRow(_section(t("set_grp_seed"), "fa6s.seedling", "success"))
        self.stop_ratio = QDoubleSpinBox(); self.stop_ratio.setRange(0, 1000); self.stop_ratio.setSingleStep(0.1)
        self.stop_ratio.setValue(float(cfg.get("stop_ratio", 0)))
        self.stop_hours = spin(cfg.get("stop_hours", 0), 0, 100_000)
        form.addRow(t("pr_ratio"), self.stop_ratio)
        form.addRow(t("pr_hours"), self.stop_hours)
        hint = QLabel(t("pr_hint")); hint.setObjectName("meta"); hint.setWordWrap(True)
        form.addRow(hint)

        from .dialogs import TrackerRulesEditor
        form.addRow(_section(t("set_grp_trackers"), "fa6s.server", "info"))
        hosts = sorted({r.tracker_host for r in parent.model.rows}) if parent is not None else []
        self.tr_rules = TrackerRulesEditor(cfg.get("tracker_rules", {}), hosts)
        form.addRow(self.tr_rules)
        th = QLabel(t("tr_rules_hint")); th.setObjectName("meta"); th.setWordWrap(True)
        form.addRow(th)

        form.addRow(_section(t("set_grp_protect"), "fa6s.user-shield", "danger"))
        self.ban_bad = QCheckBox(t("set_ban_bad")); self.ban_bad.setChecked(cfg.get("ban_bad_peers", True))
        brow = QHBoxLayout()
        brow.addWidget(self.ban_bad, 1)
        self._engine = parent.engine if parent is not None else None
        nb = len(self._engine.bans) if self._engine else 0
        self.unban = QPushButton(t("set_unban", nb))
        self.unban.setEnabled(nb > 0)
        self.unban.clicked.connect(self._clear_bans)
        brow.addWidget(self.unban)
        form.addRow(brow)
        self.cross = QCheckBox(t("set_cross")); self.cross.setChecked(cfg.get("cross_seed", True))
        form.addRow(self.cross)

        form.addRow(_section(t("set_grp_power"), "fa6s.bolt", "warning"))
        self.awake = QCheckBox(t("set_awake")); self.awake.setChecked(cfg.get("keep_awake", True))
        self.awake_seed = QCheckBox(t("set_awake_seed")); self.awake_seed.setChecked(cfg.get("keep_awake_seed", False))
        form.addRow(self.awake)
        form.addRow(self.awake_seed)
        self.min_free = spin(cfg.get("min_free_gb", 0), 0, 100_000)
        self.min_free.setSuffix(" GB"); self.min_free.setSpecialValueText(t("pm_off"))
        form.addRow(t("set_min_free"), self.min_free)
        self.done_open = QCheckBox(t("set_done_open")); self.done_open.setChecked(cfg.get("done_open_folder", False))
        form.addRow(self.done_open)
        self.done_cmd = QLineEdit(cfg.get("done_cmd", ""))
        self.done_cmd.setPlaceholderText('"C:\\Program Files\\Windows Defender\\MpCmdRun.exe" -Scan -ScanType 3 -File "%F"')
        form.addRow(t("set_done_cmd"), self.done_cmd)
        dh = QLabel(t("set_done_hint")); dh.setObjectName("meta"); dh.setWordWrap(True)
        form.addRow(dh)
        self.mini_cb = QCheckBox(t("set_mini")); self.mini_cb.setChecked(cfg.get("mini_widget", False))
        form.addRow(self.mini_cb)
        self.title_speed = QCheckBox(t("set_title_speed")); self.title_speed.setChecked(cfg.get("speed_in_title", True))
        form.addRow(self.title_speed)

        form.addRow(_section(t("set_grp_sched"), "fa6s.clock", "orange"))
        self.sch_on = QCheckBox(t("sch_enable")); self.sch_on.setChecked(cfg.get("sched_enabled", False))
        form.addRow(self.sch_on)
        self.sch_from = QTimeEdit(QTime.fromString(cfg.get("sched_from", "08:00"), "HH:mm"))
        self.sch_to = QTimeEdit(QTime.fromString(cfg.get("sched_to", "23:00"), "HH:mm"))
        for te in (self.sch_from, self.sch_to):
            te.setDisplayFormat("HH:mm")
        trow = QHBoxLayout()
        trow.addWidget(self.sch_from); trow.addWidget(QLabel(t("sch_to"))); trow.addWidget(self.sch_to); trow.addStretch()
        form.addRow(t("sch_from"), trow)
        self.sch_dl = spin(cfg.get("sched_dl", 0), 0, 10_000_000)
        self.sch_ul = spin(cfg.get("sched_ul", 0), 0, 10_000_000)
        form.addRow(t("sch_dl"), self.sch_dl)
        form.addRow(t("sch_ul"), self.sch_ul)

        from .dialogs import CategoriesEditor, folder_row
        form.addRow(_section(t("set_grp_cats"), "fa6s.tags", "purple"))
        self.cats = CategoriesEditor(cfg.get("categories", []))
        form.addRow(self.cats)

        form.addRow(_section(t("set_grp_folders"), "fa6s.folder-open", "warning"))
        wrow, self.watch = folder_row(self, cfg.get("watch_dir", ""))
        form.addRow(t("set_watch"), wrow)
        self.watch_cat = QComboBox()
        self.watch_cat.addItem(t("cat_none"), "")
        for c in cfg.get("categories", []):
            self.watch_cat.addItem(c["name"], c["name"])
        self.watch_cat.setCurrentIndex(max(0, self.watch_cat.findData(cfg.get("watch_category", ""))))
        form.addRow(t("set_watch_cat"), self.watch_cat)
        mrow, self.move_done = folder_row(self, cfg.get("move_done_dir", ""))
        form.addRow(t("set_move_done"), mrow)
        self.move_bycat = QCheckBox(t("set_move_bycat"))
        self.move_bycat.setChecked(cfg.get("move_done_by_category", True))
        form.addRow(self.move_bycat)

        # ── windows
        self.autostart = None
        if winint.IS_WIN:
            form.addRow(_section(t("set_grp_windows"), "fa6b.windows", "info"))
            self.autostart = QCheckBox(t("set_autostart"))
            self.autostart.setChecked(winint.autostart_enabled())
            form.addRow(self.autostart)
            self.assoc_btn = QPushButton(t("set_assoc_btn"))
            self.assoc_btn.clicked.connect(self._assoc)
            if not winint.exe_path():
                self.assoc_btn.setEnabled(False)
                self.assoc_btn.setToolTip(t("assoc_portable"))
            elif winint.is_default_handler():
                self.assoc_btn.setText(t("assoc_default"))
            form.addRow(t("set_assoc"), self.assoc_btn)

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setObjectName("primary")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        bb.setContentsMargins(20, 0, 20, 0)
        outer.addWidget(bb)

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, t("choose_folder"), self.path.text())
        if d:
            self.path.setText(d)

    def _pick_ipf(self):
        f, _ = QFileDialog.getOpenFileName(self, t("set_ipfilter"), "",
                                           "IP filter (*.dat *.p2p *.txt *.gz *.zip);;* (*)")
        if f:
            self.ipf.setText(f)

    def _clear_bans(self):
        if self._engine:
            self._engine.clear_bans()
        self.unban.setText(t("set_unban", 0))
        self.unban.setEnabled(False)

    def _assoc(self):
        try:
            winint.register_associations()
            self.assoc_btn.setText(t("assoc_default"))
            QMessageBox.information(self, APP_TITLE, t("assoc_done"))
        except OSError as e:
            QMessageBox.warning(self, APP_TITLE, str(e))

    def result_cfg(self) -> dict:
        if self.autostart is not None:
            try:
                winint.set_autostart(self.autostart.isChecked())
            except OSError:
                pass
        c = self.cfg
        c.update(save_path=self.path.text(), port=self.port.value(), dl_limit=self.dl.value(),
                 ul_limit=self.ul.value(), active_downloads=self.act_dl.value(),
                 active_seeds=self.act_seed.value(), dht=self.dht.isChecked(),
                 lsd=self.dht.isChecked(), upnp=self.upnp.isChecked(),
                 minimize_to_tray=self.tray.isChecked(),
                 show_add_dialog=self.show_add.isChecked(), check_updates=self.upd.isChecked(),
                 toasts=self.toasts.isChecked() if self.toasts is not None else c.get("toasts", True),
                 stop_ratio=self.stop_ratio.value(), stop_hours=self.stop_hours.value(),
                 sched_enabled=self.sch_on.isChecked(),
                 sched_from=self.sch_from.time().toString("HH:mm"),
                 sched_to=self.sch_to.time().toString("HH:mm"),
                 sched_dl=self.sch_dl.value(), sched_ul=self.sch_ul.value(),
                 categories=self.cats.categories(),
                 tracker_rules=self.tr_rules.rules(), ban_bad_peers=self.ban_bad.isChecked(),
                 cross_seed=self.cross.isChecked(),
                 keep_awake=self.awake.isChecked(), keep_awake_seed=self.awake_seed.isChecked(),
                 min_free_gb=self.min_free.value(), done_open_folder=self.done_open.isChecked(),
                 done_cmd=self.done_cmd.text().strip(), mini_widget=self.mini_cb.isChecked(),
                 speed_in_title=self.title_speed.isChecked(),
                 watch_dir=self.watch.text().strip(), watch_category=self.watch_cat.currentData() or "",
                 move_done_dir=self.move_done.text().strip(), move_done_by_category=self.move_bycat.isChecked(),
                 bind_interface=self.iface.currentData() or "", encryption=self.enc.currentData(),
                 ip_filter=self.ipf.text().strip(), geoip=self.geoip.isChecked(),
                 theme=("system", "light", "dark")[self.theme.currentIndex()],
                 ui_style=self.style_combo.currentData(),
                 language="en" if self.lang.currentIndex() == 1 else "ru")
        return c


def free_space(path: str) -> int | None:
    p = os.path.abspath(path or ".")
    while p and not os.path.exists(p):
        parent = os.path.dirname(p)
        if parent == p:
            return None
        p = parent
    try:
        return shutil.disk_usage(p).free
    except OSError:
        return None


class AddTorrentDialog(QDialog):
    """Choose folder and files before adding (.torrent), or just folder for a magnet."""
    def __init__(self, cfg: dict, name: str, ti=None, parent=None, cross: dict | None = None,
                 scrape_src: tuple[list[str], bytes] | None = None):
        super().__init__(parent)
        _theme_title_bar(self)
        self.setWindowTitle(t("ad_title"))
        self.cross_cb = None
        self.setMinimumWidth(680)
        self.ti = ti
        self.leaves: list[tuple[QTreeWidgetItem, int, int]] = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 14, 20, 16)
        lay.setSpacing(10)

        head = _section(name)
        head.setWordWrap(True)
        lay.addWidget(head)
        # seeds / leechers asked from the tracker in the background (scrape)
        self.health = QLabel(); self.health.setObjectName("meta"); self.health.setTextFormat(Qt.RichText)
        self._scrape_res = None
        if scrape_src and scrape_src[0]:
            self.health.setText("&#8987; " + t("scr_wait"))
            lay.addWidget(self.health)
            import threading
            from .netx import scrape

            def work():
                try:
                    self._scrape_res = ("ok", scrape(*scrape_src))
                except Exception as e:
                    self._scrape_res = ("ok", {"error": str(e), "host": ""})
            threading.Thread(target=work, daemon=True).start()
            self._scrape_timer = QTimer(self)
            self._scrape_timer.timeout.connect(self._scrape_done)
            self._scrape_timer.start(200)
        if cross:
            self.cross_cb = QCheckBox(t("cross_offer", cross["name"], cross["save_path"]))
            self.cross_cb.setChecked(True)
            self.cross_cb.setStyleSheet(f"color:{C['success_text'] if theme.current_name == 'dark' else C['success']};"
                                        "font-weight:600;")
            lay.addWidget(self.cross_cb)

        self.cfg = cfg
        crow = QHBoxLayout()
        crow.addWidget(QLabel(t("cat_menu")))
        self.cat = QComboBox()
        self.cat.addItem(t("cat_none"), "")
        for c in cfg.get("categories", []):
            self.cat.addItem(icon("fa6s.tag"), c["name"], c["name"])
        self.cat.currentIndexChanged.connect(self._cat_changed)
        crow.addWidget(self.cat, 1)
        if len(cfg.get("categories", [])):
            lay.addLayout(crow)

        row = QHBoxLayout()
        row.addWidget(QLabel(t("ad_save_to")))
        self.path = QLineEdit(cfg.get("save_path", ""))
        self.path.textChanged.connect(self._update)
        row.addWidget(self.path, 1)
        b = QPushButton(t("set_browse"))
        b.clicked.connect(self._browse)
        row.addWidget(b)
        lay.addLayout(row)

        if ti is not None:
            self.tree = QTreeWidget()
            self.tree.setHeaderLabels([t("file_name"), t("col_size")])
            self.tree.setRootIsDecorated(True)
            self.tree.setUniformRowHeights(True)
            hh = self.tree.header()
            hh.setStretchLastSection(False)
            hh.setSectionResizeMode(0, QHeaderView.Stretch)
            hh.setSectionResizeMode(1, QHeaderView.ResizeToContents)
            self._fill(torrent_files(ti))
            self.tree.itemChanged.connect(lambda *_: self._update())
            lay.addWidget(self.tree, 1)
            self.setMinimumHeight(540)

            sel = QHBoxLayout()
            ball = QPushButton(t("ad_all"));  ball.clicked.connect(lambda: self._check_all(True))
            bnone = QPushButton(t("ad_none")); bnone.clicked.connect(lambda: self._check_all(False))
            sel.addWidget(ball)
            sel.addWidget(bnone)
            sel.addStretch()
            self.sel_lbl = QLabel(); self.sel_lbl.setObjectName("meta")
            sel.addWidget(self.sel_lbl)
            lay.addLayout(sel)
        else:
            note = QLabel(t("ad_magnet_note")); note.setObjectName("meta"); note.setWordWrap(True)
            lay.addWidget(note)

        bottom = QHBoxLayout()
        self.paused = QCheckBox(t("ad_paused"))
        bottom.addWidget(self.paused)
        bottom.addStretch()
        self.free_lbl = QLabel(); self.free_lbl.setObjectName("meta")
        bottom.addWidget(self.free_lbl)
        lay.addLayout(bottom)

        if self.cross_cb is not None:          # cross-seed uses the other torrent's folder and all files
            def cross_toggled(on):
                self.path.setEnabled(not on)
                if ti is not None:
                    self.tree.setEnabled(not on)
            self.cross_cb.toggled.connect(cross_toggled)
            cross_toggled(True)

        self.bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.ok = self.bb.button(QDialogButtonBox.Ok)
        self.ok.setText(t("add_btn"))
        self.ok.setObjectName("primary")
        self.bb.accepted.connect(self.accept)
        self.bb.rejected.connect(self.reject)
        lay.addWidget(self.bb)
        self._update()

    def _scrape_done(self):
        if self._scrape_res is None:
            return
        self._scrape_timer.stop()
        res = self._scrape_res[1]
        dark = theme.current_name == "dark"
        if not res:
            self.health.setText(t("scr_none"))
        elif "error" in res:
            self.health.setText(f'<span style="color:{C["danger"]}">{t("scr_error", res["host"], res["error"][:120])}</span>')
        else:
            alive = res["seeds"] > 0
            col = C["success_text" if dark else "success"] if alive else C["danger"]
            self.health.setText(f'<span style="color:{col};font-weight:600">'
                                f'{t("scr_alive" if alive else "scr_dead")}</span> &nbsp; '
                                + t("scr_counts", res["seeds"], res["peers"], res["done"], res["host"]))

    def use_cross(self) -> bool:
        return self.cross_cb is not None and self.cross_cb.isChecked()

    def _cat_changed(self):
        name = self.cat.currentData() or ""
        path = next((c.get("path", "") for c in self.cfg.get("categories", []) if c["name"] == name), "")
        self.path.setText(path or self.cfg.get("save_path", ""))

    def _fill(self, files):
        folders: dict[tuple, QTreeWidgetItem] = {}
        ic_dir = widgets.folder_icon(False)
        self.tree.blockSignals(True)
        for idx, path, size in files:
            parts = path.replace("\\", "/").split("/")
            parent, key = self.tree.invisibleRootItem(), ()
            for part in parts[:-1]:
                key += (part,)
                it = folders.get(key)
                if it is None:
                    it = QTreeWidgetItem(parent, [part, ""])
                    it.setFlags(it.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsAutoTristate)
                    it.setCheckState(0, Qt.Checked)
                    it.setIcon(0, ic_dir)
                    folders[key] = it
                parent = it
            leaf = QTreeWidgetItem(parent, [parts[-1], fmt_size(size)])
            leaf.setFlags(leaf.flags() | Qt.ItemIsUserCheckable)
            leaf.setCheckState(0, Qt.Checked)
            leaf.setIcon(0, widgets.file_icon(parts[-1]))
            leaf.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
            self.leaves.append((leaf, idx, size))

        leaf_size = {id(lf): sz for lf, _, sz in self.leaves}

        def folder_size(it: QTreeWidgetItem) -> int:
            if it.childCount() == 0:
                return leaf_size.get(id(it), 0)
            total = sum(folder_size(it.child(i)) for i in range(it.childCount()))
            it.setText(1, fmt_size(total))
            it.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
            return total

        root = self.tree.invisibleRootItem()
        for i in range(root.childCount()):
            folder_size(root.child(i))
        if len(files) <= 300:
            self.tree.expandAll()
        else:
            for i in range(root.childCount()):
                root.child(i).setExpanded(True)
        self.tree.blockSignals(False)

    def _check_all(self, on: bool):
        root = self.tree.invisibleRootItem()
        for i in range(root.childCount()):
            root.child(i).setCheckState(0, Qt.Checked if on else Qt.Unchecked)
        for leaf, _, _ in self.leaves:          # single-file / flat torrents have no folder items
            leaf.setCheckState(0, Qt.Checked if on else Qt.Unchecked)
        self._update()

    def selected_size(self) -> int:
        return sum(sz for lf, _, sz in self.leaves if lf.checkState(0) == Qt.Checked)

    def _update(self):
        need = 0
        if self.ti is not None:
            need = self.selected_size()
            total = sum(sz for _, _, sz in self.leaves)
            self.sel_lbl.setText(t("ad_selected", fmt_size(need), fmt_size(total)))
        free = free_space(self.path.text())
        if free is None:
            self.free_lbl.setText("")
        elif need > free:
            self.free_lbl.setText(f'<span style="color:{C["danger"]}">{t("ad_no_space")} · {t("ad_free", fmt_size(free))}</span>')
        else:
            self.free_lbl.setText(t("ad_free", fmt_size(free)))
        self.ok.setEnabled(self.ti is None or need > 0 or not self.leaves)
        self.ok.setToolTip("" if self.ok.isEnabled() else t("ad_nothing"))

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, t("choose_folder"), self.path.text())
        if d:
            self.path.setText(d)

    def priorities(self) -> list[int] | None:
        """None = everything selected (keep libtorrent defaults)."""
        if self.ti is None or all(lf.checkState(0) == Qt.Checked for lf, _, _ in self.leaves):
            return None
        prios = [PRIO_NORMAL] * self.ti.num_files()
        for lf, idx, _ in self.leaves:
            if lf.checkState(0) != Qt.Checked:
                prios[idx] = PRIO_SKIP
        return prios


class PropertiesDialog(QDialog):
    def __init__(self, name: str, props: dict, parent=None):
        super().__init__(parent)
        _theme_title_bar(self)
        self.setWindowTitle(t("pr_title"))
        self.setMinimumWidth(520)
        form = QFormLayout(self)
        form.setContentsMargins(20, 12, 20, 16)
        head = _section(name); head.setWordWrap(True)
        form.addRow(head)

        form.addRow(_section(t("pr_speed"), "fa6s.gauge-high", "orange"))
        self.dl = QSpinBox(); self.dl.setRange(0, 10_000_000); self.dl.setValue(props["dl_limit"])
        self.ul = QSpinBox(); self.ul.setRange(0, 10_000_000); self.ul.setValue(props["ul_limit"])
        form.addRow(t("pr_dl"), self.dl)
        form.addRow(t("pr_ul"), self.ul)
        self.sup = QCheckBox(t("pr_super")); self.sup.setChecked(props["super_seed"])
        form.addRow(self.sup)

        form.addRow(_section(t("pr_stop"), "fa6s.hand", "danger"))
        self.mode = QComboBox()
        for k in ("pr_mode_global", "pr_mode_custom", "pr_mode_never"):
            self.mode.addItem(t(k))
        self.mode.setCurrentIndex(("global", "custom", "never").index(props["stop_mode"]))
        form.addRow(t("pr_mode"), self.mode)
        self.ratio = QDoubleSpinBox(); self.ratio.setRange(0, 1000); self.ratio.setSingleStep(0.1)
        self.ratio.setDecimals(2); self.ratio.setValue(props["stop_ratio"])
        self.hours = QSpinBox(); self.hours.setRange(0, 100_000); self.hours.setValue(props["stop_hours"])
        form.addRow(t("pr_ratio"), self.ratio)
        form.addRow(t("pr_hours"), self.hours)
        hint = QLabel(t("pr_hint")); hint.setObjectName("meta"); hint.setWordWrap(True)
        form.addRow(hint)
        self.mode.currentIndexChanged.connect(self._mode_changed)
        self._mode_changed()

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setObjectName("primary")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        form.addRow(bb)

    def _mode_changed(self):
        custom = self.mode.currentIndex() == 1
        self.ratio.setEnabled(custom)
        self.hours.setEnabled(custom)

    def result_props(self) -> dict:
        return {"dl_limit": self.dl.value(), "ul_limit": self.ul.value(), "super_seed": self.sup.isChecked(),
                "stop_mode": ("global", "custom", "never")[self.mode.currentIndex()],
                "stop_ratio": self.ratio.value(), "stop_hours": self.hours.value()}


class HashThread(QThread):
    done = Signal(str, str)          # out_path, error
    progress = Signal(int, int)      # pieces hashed, total

    def __init__(self, **kw):
        super().__init__()
        self.kw = kw

    def run(self):
        try:
            self.done.emit(create_torrent(progress=lambda d, n: self.progress.emit(d, n), **self.kw), "")
        except Exception as e:
            self.done.emit("", str(e))


class CreateTorrentDialog(QDialog):
    def __init__(self, cfg: dict, parent=None):
        super().__init__(parent)
        from PySide6.QtWidgets import QPlainTextEdit, QProgressBar
        from .engine import PIECE_SIZES
        _theme_title_bar(self)
        self.setWindowTitle(t("ct_title"))
        self.setMinimumWidth(640)
        self.cfg = cfg
        self.out_path = ""
        self.src_path = ""
        form = QFormLayout(self)
        form.setContentsMargins(20, 12, 20, 16)
        form.setRowWrapPolicy(QFormLayout.WrapAllRows)      # labels above the fields: more room for paths
        form.setVerticalSpacing(6)
        form.addRow(_section(t("ct_title")))

        self.src = QLineEdit()
        bf = QPushButton(t("ct_file"));   bf.clicked.connect(self._pick_file)
        bd = QPushButton(t("ct_folder")); bd.clicked.connect(self._pick_dir)
        row = QHBoxLayout(); row.addWidget(self.src); row.addWidget(bf); row.addWidget(bd)
        form.addRow(t("ct_source"), row)

        def text_box(value, ph, h=58):
            e = QPlainTextEdit(value)
            e.setPlaceholderText(ph)
            e.setFixedHeight(h)
            e.setLineWrapMode(QPlainTextEdit.NoWrap)
            return e

        self.announce = text_box(cfg.get("announce_url", ""), "https://tracker.example/announce.php?passkey=…")
        form.addRow(t("ct_announce_n"), self.announce)

        opts = QHBoxLayout()
        self.piece = QComboBox()
        for size in PIECE_SIZES:
            self.piece.addItem(t("ct_piece_auto") if size == 0 else fmt_size(size).replace(".00", ""), size)
        self.piece.setCurrentIndex(max(0, self.piece.findData(cfg.get("ct_piece", 0))))
        self.fmt = QComboBox()
        for k in ("v1", "hybrid", "v2"):
            self.fmt.addItem(t("ct_fmt_" + k), k)
        self.fmt.setCurrentIndex(max(0, self.fmt.findData(cfg.get("ct_format", "v1"))))
        opts.addWidget(self.piece)
        opts.addWidget(QLabel(t("ct_format")))
        opts.addWidget(self.fmt, 1)
        form.addRow(t("ct_piece"), opts)

        self.webseeds = text_box("", "https://mirror.example/files/", 46)
        form.addRow(t("ct_webseeds"), self.webseeds)
        self.source = QLineEdit(cfg.get("ct_source", ""))
        self.source.setToolTip(t("ct_source_hint"))
        form.addRow(t("ct_src_tag"), self.source)
        self.comment = QLineEdit()
        form.addRow(t("ct_comment"), self.comment)
        self.private = QCheckBox(t("ct_private")); self.private.setChecked(True)
        form.addRow(self.private)
        self.seed = QCheckBox(t("ct_seed")); self.seed.setChecked(True)
        form.addRow(self.seed)
        self.bar = QProgressBar(); self.bar.setTextVisible(True); self.bar.hide()
        form.addRow(self.bar)

        self.bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.bb.button(QDialogButtonBox.Ok).setObjectName("primary")
        self.bb.accepted.connect(self._go)
        self.bb.rejected.connect(self.reject)
        form.addRow(self.bb)

    def _pick_file(self):
        f, _ = QFileDialog.getOpenFileName(self, t("ct_source"))
        if f:
            self.src.setText(f)

    def _pick_dir(self):
        d = QFileDialog.getExistingDirectory(self, t("ct_source"))
        if d:
            self.src.setText(d)

    def _go(self):
        src = self.src.text().strip()
        if not src or not os.path.exists(src):
            return
        name = os.path.basename(os.path.normpath(src))
        out, _ = QFileDialog.getSaveFileName(self, t("ct_title"), name + ".torrent", t("torrent_filter"))
        if not out:
            return
        announce = self.announce.toPlainText().strip()
        self.cfg.update(announce_url=announce, ct_piece=self.piece.currentData(),
                        ct_format=self.fmt.currentData(), ct_source=self.source.text().strip())
        self.bb.setEnabled(False)
        self.setWindowTitle(t("ct_working"))
        self.bar.setRange(0, 0)
        self.bar.show()
        self.src_path = src
        self.th = HashThread(src=src, announce=announce, out_path=out, private=self.private.isChecked(),
                             comment=self.comment.text().strip(), piece_size=self.piece.currentData(),
                             fmt=self.fmt.currentData(), web_seeds=self.webseeds.toPlainText().splitlines(),
                             source=self.source.text().strip())
        self.th.progress.connect(self._progress)
        self.th.done.connect(self._finished)
        self.th.start()

    def _progress(self, done, total):
        self.bar.setRange(0, total)
        self.bar.setValue(done)

    def _finished(self, out, err):
        self.bb.setEnabled(True)
        self.bar.hide()
        self.setWindowTitle(t("ct_title"))
        if err:
            QMessageBox.warning(self, t("ct_title"), err)
            return
        self.out_path = out
        QMessageBox.information(self, t("ct_title"), t("ct_done", out))
        self.accept()


class UpdateDialog(QDialog):
    """Release notes + download & silent install of the new Setup.exe."""
    progress = Signal(int, int)
    finished_dl = Signal(str, str)        # path, error

    def __init__(self, info: dict, parent=None):
        super().__init__(parent)
        from PySide6.QtWidgets import QProgressBar, QTextBrowser
        from . import updater
        _theme_title_bar(self)
        self.info, self.updater, self.path = info, updater, ""
        self.setWindowTitle(t("upd_title"))
        self.resize(620, 460)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 12, 20, 16)
        lay.addWidget(_section(t("upd_available", info["version"])))
        lab = QLabel(t("upd_text", info["version"], VERSION)); lab.setObjectName("meta")
        lay.addWidget(lab)
        notes = QTextBrowser()
        notes.setOpenExternalLinks(True)
        notes.setMarkdown(info.get("notes") or "")
        lay.addWidget(notes, 1)
        self.bar = QProgressBar(); self.bar.hide()
        lay.addWidget(self.bar)
        self.note = QLabel(t("upd_restart")); self.note.setObjectName("meta"); self.note.setWordWrap(True)
        lay.addWidget(self.note)
        self.bb = QDialogButtonBox()
        self.can_install = updater.can_self_update() and bool(info.get("url"))
        self.go = self.bb.addButton(t("upd_install" if self.can_install else "upd_page"), QDialogButtonBox.AcceptRole)
        self.go.setObjectName("primary")
        self.bb.addButton(t("upd_later"), QDialogButtonBox.RejectRole)
        self.bb.accepted.connect(self._go)
        self.bb.rejected.connect(self.reject)
        self.note.setVisible(self.can_install)
        lay.addWidget(self.bb)
        self.progress.connect(self._on_progress)
        self.finished_dl.connect(self._on_done)

    def _go(self):
        if not self.can_install:
            QDesktopServices.openUrl(QUrl(self.info["page"]))
            self.reject()
            return
        import threading
        self.go.setEnabled(False)
        self.bar.setRange(0, max(1, self.info.get("size", 0)))
        self.bar.show()

        def work():
            try:
                p = self.updater.download(self.info["url"], self.info.get("size", 0),
                                          lambda d, n: self.progress.emit(d, n))
                self.finished_dl.emit(p, "")
            except Exception as e:
                self.finished_dl.emit("", str(e))
        threading.Thread(target=work, daemon=True).start()

    def _on_progress(self, done, total):
        if total:
            self.bar.setMaximum(total)
        self.bar.setValue(min(done, self.bar.maximum()))
        self.bar.setFormat(t("upd_downloading", fmt_size(done), fmt_size(total or done)))

    def _on_done(self, path, err):
        if err:
            self.go.setEnabled(True)
            QMessageBox.warning(self, t("upd_title"), err)
            return
        self.path = path
        self.accept()


# ============================================================
#  Main window
# ============================================================

class MainWindow(QMainWindow):
    def __init__(self, engine: Engine, cfg: dict):
        super().__init__()
        self.engine = engine
        self.cfg = cfg
        self._quitting = False
        self._themed: list[tuple] = []        # (setter, icon name, colour key) for re-theming
        self.taskbar = winint.Taskbar()
        self._tb_state = None
        self._awake = None
        self.mini: MiniWidget | None = None
        self.after_done = "none"                 # sleep | hibernate | shutdown, one-shot
        self._had_dl = False
        self._countdown = None
        self._session_up0 = engine.ses.status().total_payload_upload
        self._sched_state: bool | None = None   # None = not applied yet

        self.setWindowTitle(APP_TITLE)
        self.setWindowIcon(app_icon())
        self.resize(1200, 780)
        self.setMinimumSize(1060, 600)
        self.setAcceptDrops(True)

        page = QWidget(); page.setObjectName("page")
        root = QVBoxLayout(page)
        root.setContentsMargins(16, 14, 16, 12)
        root.setSpacing(12)
        self.setCentralWidget(page)

        root.addWidget(self._build_header())
        root.addLayout(self._build_actions())
        root.addLayout(self._build_kpis())

        split = QSplitter(Qt.Vertical)
        self.split = split
        split.setChildrenCollapsible(False)
        split.addWidget(self._build_list())
        split.addWidget(self._build_details())
        split.setSizes([470, 210])
        root.addWidget(split, 1)

        self._build_tray()
        from .notify import Notifier
        self.notifier = Notifier(theme.asset("icon.ico"))
        self.notifier.activated.connect(self.on_toast)
        self.restore_ui_state()
        self.geo = None
        self._ipf_result = None
        self.start_network_extras()
        self.refresh_category_combo()
        self.retheme()
        self._log_startup()
        self._ticks = 0
        self._update_result = None
        import time as _time
        from . import updater as _upd
        if self.cfg.get("check_updates", True) and _upd.can_self_update() \
                and _time.time() - self.cfg.get("last_update_check", 0) > self.UPDATE_EVERY / 2:
            self.check_updates()

        self._tr_bad: set[str] = set()          # torrents already toasted for a tracker error
        if self.cfg.get("mini_widget"):
            self.set_mini(True)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(1000)
        self.tick()

    # ── building blocks ────────────────────────────────────────
    def _themed_icon(self, setter, name, key="secondary"):
        self._themed.append((setter, name, key))

    def _build_header(self) -> QFrame:
        h = card()
        h.setObjectName("headCard")
        lay = QHBoxLayout(h)
        lay.setContentsMargins(18, 14, 14, 14)
        lay.setSpacing(14)
        self.head_icon = QLabel()
        self.head_icon.setObjectName("headIcon")
        self.head_icon.setFixedSize(50, 50)
        self.head_icon.setAlignment(Qt.AlignCenter)
        self._themed_icon(lambda ic: self.head_icon.setPixmap(ic.pixmap(24, 24)),
                          "fa6s.cloud-arrow-down", "head_logo")
        lay.addWidget(self.head_icon)

        col = QVBoxLayout(); col.setSpacing(2)
        title = QLabel(APP_TITLE); title.setObjectName("title")
        self.meta = QLabel(); self.meta.setObjectName("meta"); self.meta.setTextFormat(Qt.RichText)
        col.addWidget(title)
        col.addWidget(self.meta)
        lay.addLayout(col)
        lay.addStretch()

        def ibtn(name, tip, slot):
            b = QToolButton(); b.setObjectName("iconBtn"); b.setToolTip(tip)
            b.setCursor(Qt.PointingHandCursor); b.setIconSize(QSize(15, 15))
            b.clicked.connect(slot)
            self._themed_icon(b.setIcon, name, "head_ic")
            lay.addWidget(b)
            return b

        self.update_pill = QPushButton()
        self.update_pill.setObjectName("primary")
        self.update_pill.setCursor(Qt.PointingHandCursor)
        self.update_pill.clicked.connect(self.show_update)
        self.update_pill.hide()
        self._themed_icon(self.update_pill.setIcon, "fa6s.circle-arrow-up", "#ffffff")
        lay.addWidget(self.update_pill)
        self.power_pill = QPushButton()              # armed "when everything is downloaded" action
        self.power_pill.setObjectName("danger")
        self.power_pill.setCursor(Qt.PointingHandCursor)
        self.power_pill.setToolTip(t("pw_cancel_tip"))
        self.power_pill.clicked.connect(lambda: self.set_after_done("none"))
        self.power_pill.hide()
        self._themed_icon(self.power_pill.setIcon, "fa6s.power-off", "danger")
        lay.addWidget(self.power_pill)
        ibtn("fa6s.file-import", t("imp_title"), self.import_dialog)
        self.theme_btn = ibtn("fa6s.moon", t("theme_toggle"), self.toggle_theme)
        ibtn("fa6s.gear", t("settings"), self.settings_dialog)
        return h

    def _build_actions(self) -> QHBoxLayout:
        lay = QHBoxLayout()
        lay.setSpacing(6)

        def btn(key, name, slot, obj="", color="body", shortcut=None):
            b = QPushButton(t(key))
            if obj:
                b.setObjectName(obj)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(slot)
            self._themed_icon(b.setIcon, name, "#ffffff" if obj == "primary" else color)
            if shortcut:
                a = QAction(self); a.setShortcut(QKeySequence(shortcut)); a.triggered.connect(slot)
                self.addAction(a)
            lay.addWidget(b)
            return b

        btn("add_torrent", "fa6s.plus", self.add_torrent_dialog, "primary", shortcut="Ctrl+O")
        btn("add_magnet", "fa6s.magnet", self.add_magnet_dialog, color="ic_magnet", shortcut="Ctrl+U")
        btn("create_torrent", "fa6s.file-circle-plus", self.create_torrent_dialog, color="ic_create", shortcut="Ctrl+N")
        lay.addWidget(vsep())
        btn("start", "fa6s.play", lambda: self._each(self.engine.resume), color="success")
        btn("pause", "fa6s.pause", lambda: self._each(self.engine.pause), color="secondary")
        btn("remove", "fa6s.trash-can", self.remove_selected, "danger", color="danger", shortcut="Delete")
        lay.addStretch()

        self.search = QLineEdit()
        self.search.setObjectName("search")
        self.search.setPlaceholderText(t("search_ph"))
        self.search.setClearButtonEnabled(True)
        self.search_act = self.search.addAction(QIcon(), QLineEdit.LeadingPosition)
        self._themed_icon(self.search_act.setIcon, "fa6s.magnifying-glass")
        self.search.setFixedWidth(250)
        self.search.textChanged.connect(lambda s: self.proxy.set_filter(text=s))
        lay.addWidget(self.search)
        return lay

    def _build_kpis(self) -> QHBoxLayout:
        lay = QHBoxLayout()
        lay.setSpacing(12)
        self.k_down = Kpi("fa6s.arrow-down", "down")
        self.k_up = Kpi("fa6s.arrow-up", "up")
        self.k_active = Kpi("fa6s.bolt", "active")
        self.k_session = Kpi("fa6s.chart-line", "session")
        for k in (self.k_down, self.k_up, self.k_active, self.k_session):
            lay.addWidget(k, 1)
        return lay

    def _build_list(self) -> QFrame:
        box = card()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(1, 10, 1, 1)
        lay.setSpacing(8)

        pills = QHBoxLayout()
        pills.setContentsMargins(12, 0, 12, 0)
        pills.setSpacing(6)
        self.pill_group = QButtonGroup(self)
        self.pills: dict[str, QPushButton] = {}
        pill_icons = {"all": ("fa6s.layer-group", "primary"), "downloading": ("fa6s.arrow-down", "primary"),
                      "seeding": ("fa6s.arrow-up", "success"), "paused": ("fa6s.pause", "secondary"),
                      "error": ("fa6s.triangle-exclamation", "danger")}
        for key in FILTER_STATES:
            b = QPushButton(t("f_" + key))
            b.setObjectName("pill")
            self._themed_icon(b.setIcon, *pill_icons[key])
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=key: self.proxy.set_filter(state=k))
            self.pill_group.addButton(b)
            self.pills[key] = b
            pills.addWidget(b)
        self.pills["all"].setChecked(True)
        pills.addStretch()
        # one client, many trackers: filter the list by tracker domain
        self.tracker_combo = QComboBox()
        self.tracker_combo.setMinimumWidth(190)
        self.tracker_combo.addItem(t("tracker_all"), "")
        self.tracker_combo.currentIndexChanged.connect(
            lambda _: self.proxy.set_filter(tracker=self.tracker_combo.currentData() or ""))
        self._themed_icon(lambda ic: [self.tracker_combo.setItemIcon(i, ic) for i in range(self.tracker_combo.count())],
                          "fa6s.server")
        self.cat_combo = QComboBox()
        self.cat_combo.setMinimumWidth(170)
        self.cat_combo.currentIndexChanged.connect(
            lambda _: self.proxy.set_filter(category=self.cat_combo.currentData() or ""))
        self._themed_icon(lambda ic: [self.cat_combo.setItemIcon(i, ic) for i in range(self.cat_combo.count())],
                          "fa6s.tag")
        self.tag_combo = QComboBox()
        self.tag_combo.setMinimumWidth(150)
        self.tag_combo.currentIndexChanged.connect(
            lambda _: self.proxy.set_filter(tag=self.tag_combo.currentData() or ""))
        self._themed_icon(lambda ic: [self.tag_combo.setItemIcon(i, ic) for i in range(self.tag_combo.count())],
                          "fa6s.hashtag")
        self._tags_shown: list[str] | None = None
        pills.addWidget(self.cat_combo)
        pills.addWidget(self.tag_combo)
        pills.addWidget(self.tracker_combo)
        lay.addLayout(pills)

        self.model = TorrentModel()
        self.proxy = FilterProxy()
        self.proxy.setSourceModel(self.model)
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(38)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.setFocusPolicy(Qt.NoFocus)
        self.table.setItemDelegateForColumn(C_PROG, ProgressDelegate(self.table))
        self.table.setItemDelegateForColumn(C_STATE, BadgeDelegate(self.table))
        hh = self.table.horizontalHeader()
        hh.setSortIndicator(-1, Qt.AscendingOrder)   # insertion order until a header is clicked
        hh.setHighlightSections(False)
        hh.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        hh.setSectionResizeMode(C_NAME, QHeaderView.Stretch)
        hh.setSectionsMovable(True)
        hh.setContextMenuPolicy(Qt.CustomContextMenu)
        hh.customContextMenuRequested.connect(self.columns_menu)
        for c, w in ((C_SIZE, 90), (C_PROG, 150), (C_STATE, 145), (C_DOWN, 95), (C_UP, 95),
                     (C_SEEDS, 55), (C_PEERS, 55), (C_RATIO, 65), (C_ETA, 80), (C_TRACKER, 150), (C_CAT, 110)):
            self.table.setColumnWidth(c, w)
        self._default_header = hh.saveState()
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.context_menu)
        self.table.doubleClicked.connect(lambda _: self.open_folder())
        self.table.selectionModel().selectionChanged.connect(lambda *_: self.refresh_details())

        # empty state (.st-empty)
        empty = QWidget()
        el = QVBoxLayout(empty)
        el.addStretch()
        self.empty_ico = QLabel(); self.empty_ico.setAlignment(Qt.AlignCenter)
        self.empty_ico.setFixedSize(60, 60)
        self._themed_icon(lambda ic: self.empty_ico.setPixmap(ic.pixmap(26, 26)), "fa6s.inbox")
        e1 = QLabel(t("empty_title")); e1.setAlignment(Qt.AlignCenter)
        e1.setStyleSheet("font-weight: 600; font-size: 11pt;")
        e2 = QLabel(t("empty_text")); e2.setObjectName("meta"); e2.setAlignment(Qt.AlignCenter)
        el.addWidget(self.empty_ico, 0, Qt.AlignHCenter)
        el.addWidget(e1)
        el.addWidget(e2)
        el.addStretch()

        self.list_stack = QStackedWidget()
        self.list_stack.addWidget(self.table)
        self.list_stack.addWidget(empty)
        lay.addWidget(self.list_stack, 1)
        return box

    def _build_details(self) -> QFrame:
        box = card()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(1, 6, 1, 1)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)

        gen = QWidget()
        grid = QGridLayout(gen)
        grid.setContentsMargins(16, 10, 16, 10)
        grid.setHorizontalSpacing(24)
        grid.setVerticalSpacing(6)
        self.g: dict[str, QLabel] = {}
        columns = [   # three columns like BitComet's summary: transfer · torrent · metadata
            ["gen_path", "gen_size", "gen_downloaded", "gen_uploaded", "gen_ratio", "gen_active", "gen_hnr"],
            ["gen_tracker", "gen_error", "gen_added", "gen_completed", "gen_private", "gen_creator", "gen_tags"],
            ["gen_hash1", "gen_hash2", "gen_piece_info", "gen_files", "gen_created", "gen_comment", "gen_cat"],
        ]
        gen_icons = {"gen_path": ("fa6s.folder-open", "warning"), "gen_size": ("fa6s.hard-drive", "secondary"),
                     "gen_downloaded": ("fa6s.arrow-down", "primary"), "gen_uploaded": ("fa6s.arrow-up", "success"),
                     "gen_ratio": ("fa6s.scale-balanced", "purple"), "gen_tracker": ("fa6s.server", "info"),
                     "gen_error": ("fa6s.circle-exclamation", "danger"),
                     "gen_active": ("fa6s.stopwatch", "teal"), "gen_added": ("fa6s.calendar-plus", "primary"),
                     "gen_completed": ("fa6s.calendar-check", "success"), "gen_private": ("fa6s.lock", "orange"),
                     "gen_creator": ("fa6s.wrench", "secondary"), "gen_hash1": ("fa6s.fingerprint", "purple"),
                     "gen_hash2": ("fa6s.fingerprint", "pink"), "gen_piece_info": ("fa6s.cubes", "orange"),
                     "gen_files": ("fa6s.copy", "info"), "gen_created": ("fa6s.calendar", "secondary"),
                     "gen_comment": ("fa6s.comment", "teal"), "gen_hnr": ("fa6s.shield-halved", "warning"),
                     "gen_tags": ("fa6s.hashtag", "teal"), "gen_cat": ("fa6s.tag", "purple")}
        cells = [(k, r, col * 2) for col, ks in enumerate(columns) for r, k in enumerate(ks)]
        for k, r, c in cells:
            lab = QLabel(t(k)); lab.setObjectName("rowLbl")
            ic = QLabel()
            self._themed_icon(lambda q, ic=ic: ic.setPixmap(q.pixmap(14, 14)), *gen_icons[k])
            lab_w = QWidget()
            lab_box = QHBoxLayout(lab_w); lab_box.setContentsMargins(0, 0, 0, 0); lab_box.setSpacing(7)
            lab_box.addWidget(ic); lab_box.addWidget(lab); lab_box.addStretch()
            val = QLabel(); val.setObjectName("rowVal")
            val.setTextInteractionFlags(Qt.TextSelectableByMouse)
            val.setMinimumWidth(40)
            grid.addWidget(lab_w, r, c)
            grid.addWidget(val, r, c + 1)
            self.g[k] = val
        plab = QWidget()
        pbox = QHBoxLayout(plab); pbox.setContentsMargins(0, 0, 0, 0); pbox.setSpacing(7)
        pic = QLabel()
        self._themed_icon(lambda q: pic.setPixmap(q.pixmap(14, 14)), "fa6s.puzzle-piece", "orange")
        ptxt = QLabel(t("gen_pieces")); ptxt.setObjectName("rowLbl")
        pbox.addWidget(pic); pbox.addWidget(ptxt); pbox.addStretch()
        self.piecebar = PieceBar()
        grid.addWidget(plab, 7, 0)
        grid.addWidget(self.piecebar, 7, 1, 1, 5)
        grid.setColumnStretch(1, 2)
        grid.setColumnStretch(3, 2)
        grid.setColumnStretch(5, 3)
        grid.setRowStretch(8, 1)
        self.tabs.addTab(gen, t("tab_general"))

        self.files_tbl = widgets.FilesView()
        self.files_tbl.setContextMenuPolicy(Qt.CustomContextMenu)
        self.files_tbl.customContextMenuRequested.connect(self.files_menu)
        self.tabs.addTab(self.files_tbl, t("tab_files"))

        self.peers_tbl = self._mk_table(["peer_ip", "peer_country", "peer_client", "col_progress", "col_down", "col_up"], stretch=2)
        self.peers_tbl.setColumnWidth(0, 170)
        self.peers_tbl.setColumnWidth(1, 170)
        self.peers_tbl.setContextMenuPolicy(Qt.CustomContextMenu)
        self.peers_tbl.customContextMenuRequested.connect(self.peers_menu)
        self.peers_tbl.setIconSize(QSize(20, 15))
        peers_page = QWidget()
        pl = QVBoxLayout(peers_page)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(0)
        self.peers_summary = QLabel()           # "peers by country" strip with flags
        self.peers_summary.setObjectName("meta")
        self.peers_summary.setTextFormat(Qt.RichText)
        self.peers_summary.setContentsMargins(14, 8, 14, 8)
        self.peers_summary.setWordWrap(True)
        pl.addWidget(self.peers_summary)
        pl.addWidget(self.peers_tbl, 1)
        self.tabs.addTab(peers_page, t("tab_peers"))
        self.tr_tbl = self._mk_table(["tr_url", "tr_tier", "tr_msg"], stretch=0)
        self.tr_tbl.horizontalHeader().setSectionResizeMode(2, QHeaderView.Interactive)
        self.tr_tbl.setColumnWidth(1, 80)
        self.tr_tbl.setColumnWidth(2, 380)
        self.tabs.addTab(self.tr_tbl, t("tab_trackers"))

        speed = QWidget()
        sl = QVBoxLayout(speed)
        sl.setContentsMargins(12, 8, 12, 8)
        top = QHBoxLayout()
        top.addStretch()
        self.range_combo = QComboBox()
        for m in (5, 30, 60):
            self.range_combo.addItem(t("gr_range", m), m * 60)
        self.range_combo.currentIndexChanged.connect(
            lambda _: (setattr(self.graph, "span", self.range_combo.currentData()), self.graph.update()))
        top.addWidget(self.range_combo)
        sl.addLayout(top)
        self.graph = SpeedGraph()
        sl.addWidget(self.graph, 1)
        self.tabs.addTab(speed, t("tab_speed"))
        self.stats_view = StatsView()
        self.tabs.addTab(self.stats_view, t("tab_stats"))
        self.log = LogView()
        self.tabs.addTab(self.log, t("tab_log"))
        for i, name in enumerate(("fa6s.circle-info", "fa6s.folder-tree", "fa6s.users", "fa6s.server",
                                  "fa6s.chart-area", "fa6s.chart-simple", "fa6s.list")):
            self._themed_icon(lambda ic, i=i: self.tabs.setTabIcon(i, ic), name)
        self.tabs.currentChanged.connect(lambda _: self.refresh_details())
        lay.addWidget(self.tabs)
        return box

    def _mk_table(self, keys, stretch):
        w = QTableWidget(0, len(keys))
        w.setHorizontalHeaderLabels([t(k) for k in keys])
        w.verticalHeader().hide()
        w.verticalHeader().setDefaultSectionSize(30)
        w.setShowGrid(False)
        w.setFocusPolicy(Qt.NoFocus)
        w.setEditTriggers(QAbstractItemView.NoEditTriggers)
        w.setSelectionBehavior(QAbstractItemView.SelectRows)
        hh = w.horizontalHeader()
        hh.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        hh.setHighlightSections(False)
        hh.setSectionResizeMode(stretch, QHeaderView.Stretch)
        return w

    TRAY_LIMITS = (0, 100, 500, 1024, 2048, 5120, 10240)      # KiB/s

    def _build_tray(self):
        self.tray = QSystemTrayIcon(app_icon(), self)
        self.tray_menu = QMenu()
        self.tray_menu.aboutToShow.connect(self._fill_tray_menu)
        self._fill_tray_menu()
        self.tray.setContextMenu(self.tray_menu)
        self.tray.activated.connect(lambda r: self.show_normal() if r == QSystemTrayIcon.Trigger else None)
        self.tray.show()

    def _fill_tray_menu(self):
        """Rebuilt on every open so the check marks show the current limits."""
        m = self.tray_menu
        m.clear()
        m.addAction(icon("fa6s.window-maximize"), t("show"), self.show_normal)
        m.addAction(icon("fa6s.plus"), t("add_torrent"), self.add_torrent_dialog)
        m.addSeparator()
        for key, cfg_key, ico in (("tray_dl", "dl_limit", "fa6s.arrow-down"), ("tray_ul", "ul_limit", "fa6s.arrow-up")):
            sub = m.addMenu(icon(ico), t(key))
            cur = int(self.cfg.get(cfg_key, 0))
            for kib in self.TRAY_LIMITS:
                a = sub.addAction(t("tray_unlimited") if kib == 0 else fmt_size(kib * 1024).replace(".00", "") + "/s")
                a.setCheckable(True)
                a.setChecked(kib == cur)
                a.triggered.connect(lambda _=False, k=cfg_key, v=kib: self.set_limit(k, v))
        m.addSeparator()
        a = m.addAction(icon("fa6s.gauge-high"), t("mini_toggle"))
        a.setCheckable(True)
        a.setChecked(self.mini is not None and self.mini.isVisible())
        a.toggled.connect(self.set_mini)
        pw = m.addMenu(icon("fa6s.power-off"), t("pw_menu"))
        for kind in ("none", "sleep", "hibernate", "shutdown"):
            act = pw.addAction(t("pw_" + kind))
            act.setCheckable(True)
            act.setChecked(self.after_done == kind)
            act.triggered.connect(lambda _=False, k=kind: self.set_after_done(k))
        m.addSeparator()
        m.addAction(icon("fa6s.pause"), t("tray_pause_all"), lambda: self._all(self.engine.pause))
        m.addAction(icon("fa6s.play", "success"), t("tray_resume_all"), lambda: self._all(self.engine.resume))
        m.addSeparator()
        m.addAction(icon("fa6s.power-off", "danger"), t("quit"), self.quit)

    # ── mini widget / power / taskbar ──────────────────────────
    def set_mini(self, on: bool):
        if on and self.mini is None:
            self.mini = MiniWidget(self)
            pos = self.cfg.get("mini_pos")
            geo = QGuiApplication.primaryScreen().availableGeometry()
            if pos and any(sc.availableGeometry().contains(QPoint(*pos)) for sc in QGuiApplication.screens()):
                self.mini.move(*pos)
            else:
                self.mini.move(geo.right() - self.mini.width() - 24, geo.bottom() - self.mini.height() - 24)
        if self.mini is not None:
            self.mini.setVisible(on)
        if self.cfg.get("mini_widget") != on:
            self.cfg["mini_widget"] = on
            config.save(self.cfg)

    def set_after_done(self, kind: str):
        self.after_done = kind
        self._had_dl = any(r.state in ("downloading", "metadata") for r in self.model.rows)
        if kind == "none":
            self.power_pill.hide()
            if self._countdown is not None:
                self._countdown.close()
        else:
            self.power_pill.setText(t("pw_pill_" + kind))
            self.power_pill.show()
            self.log_event("system", t("pw_armed", t("pw_" + kind)))

    def _tick_extras(self, rows, down: int, up: int):
        dl = [r for r in rows if r.state in ("downloading", "metadata")
              or (r.state == "tracker_error" and r.progress < 1)]
        # no sleep while downloading (optionally while seeding with traffic)
        need = bool(dl) and self.cfg.get("keep_awake", True) or bool(self.cfg.get("keep_awake_seed") and up > 0)
        if need != self._awake:
            winint.keep_awake(need)
            self._awake = need
        # speeds in the title = taskbar button tooltip
        title = APP_TITLE
        if self.cfg.get("speed_in_title", True) and (down > 0 or up > 0):
            title = f"↓ {fmt_size(down)}/s  ↑ {fmt_size(up)}/s — {APP_TITLE}"
        if self.windowTitle() != title:
            self.setWindowTitle(title)
        # taskbar button progress
        if self.taskbar.ok:
            if dl:
                total = sum(r.size for r in dl) or 1
                done = sum(int(r.size * r.progress) for r in dl)
                bad = any(r.state == "tracker_error" for r in dl)
                st = (winint.TBPF_ERROR if bad else winint.TBPF_NORMAL, done * 1000 // total)
            else:
                st = (winint.TBPF_NOPROGRESS, 0)
            if st != self._tb_state:
                self._tb_state = st
                self.taskbar.set(int(self.winId()), st[0], st[1], 1000)
        if self.mini is not None and self.mini.isVisible():
            self.mini.push(down, up)
        # disk space
        while self.engine.space_events:
            kind, name, free = self.engine.space_events.pop(0)
            msg = t("space_" + kind, name, fmt_size(free))
            self.log_event("error" if kind == "paused" else "torrent", msg)
            if kind == "paused":
                self.notify(msg, "", folder=False)
        # power action once everything is downloaded
        if self.after_done != "none":
            if dl:
                self._had_dl = True
            elif self._had_dl and self._countdown is None:
                self._start_power_countdown()

    def _start_power_countdown(self, seconds: int = 60):
        kind = self.after_done
        box = QMessageBox(QMessageBox.Warning, APP_TITLE, "", QMessageBox.NoButton, self)
        now_btn = box.addButton(t("pw_now"), QMessageBox.AcceptRole)
        box.addButton(t("pw_cancel"), QMessageBox.RejectRole)
        box.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        left = [seconds]

        def fire():
            timer.stop()
            box.done(0)
            self._countdown = None
            self.set_after_done("none")
            self.log_event("system", t("pw_fired", t("pw_" + kind)))
            try:
                self.engine.save_all()
                winint.power_action(kind)
            except Exception as e:
                self.log_event("error", str(e))

        def step():
            left[0] -= 1
            if left[0] <= 0:
                fire()
            else:
                box.setText(t("pw_countdown", t("pw_" + kind), left[0]))

        def closed(_):
            timer.stop()
            if self._countdown is box:
                self._countdown = None
                if box.clickedButton() is now_btn:
                    fire()
                else:
                    self.set_after_done("none")

        timer = QTimer(box)
        timer.timeout.connect(step)
        box.setText(t("pw_countdown", t("pw_" + kind), seconds))
        box.finished.connect(closed)
        self._countdown = box
        timer.start(1000)
        self.show_normal()
        box.show()

    def _run_done_action(self, key: str):
        row = next((r for r in self.engine.rows() if r.key == key), None)
        if row is None:
            return
        full = os.path.join(row.save_path, row.name)
        if self.cfg.get("done_open_folder"):
            QDesktopServices.openUrl(QUrl.fromLocalFile(row.save_path))
        cmd = self.cfg.get("done_cmd", "").strip()
        if cmd:
            import subprocess
            for ph, val in (("%N", row.name), ("%F", full), ("%D", row.save_path), ("%H", row.key), ("%L", row.category)):
                cmd = cmd.replace(ph, val)
            try:
                subprocess.Popen(cmd, shell=True, creationflags=0x08000000 if winint.IS_WIN else 0)
                self.log_event("system", t("done_cmd_run", cmd))
            except Exception as e:
                self.log_event("error", t("done_cmd_fail", e))

    def set_limit(self, cfg_key: str, kib: int):
        self.cfg[cfg_key] = kib
        config.save(self.cfg)
        self.engine.apply_settings(self.cfg)
        self._sched_state = None               # schedule re-applies its own limits if active

    def _all(self, fn):
        for k in list(self.engine.handles):
            fn(k)
        self.tick()

    # ── updates ────────────────────────────────────────────────
    UPDATE_EVERY = 6 * 3600     # also re-checked while running: the client usually lives in the tray for days

    def _maybe_check_updates(self):
        import time as _time
        from . import updater as _upd
        if self.cfg.get("check_updates", True) and _upd.can_self_update() \
                and _time.time() - self.cfg.get("last_update_check", 0) > self.UPDATE_EVERY:
            self.check_updates()

    def check_updates(self, manual: bool = False):
        import threading
        import time as _time
        from . import updater

        def work():
            try:
                self._update_result = ("ok", updater.check(), manual)
            except Exception as e:
                self._update_result = ("error", str(e), manual)
        self.cfg["last_update_check"] = int(_time.time())
        config.save(self.cfg)
        threading.Thread(target=work, daemon=True).start()

    def _handle_update_result(self):
        kind, val, manual = self._update_result
        self._update_result = None
        if kind == "error":
            self.log_event("error", t("upd_error", val))
            if manual:
                QMessageBox.warning(self, t("upd_title"), t("upd_error", val))
        elif val:
            self.update_info = val
            self.update_pill.setText(t("upd_available", val["version"]))
            self.update_pill.show()
            self.log_event("system", t("upd_available", val["version"]))
            if manual:
                self.show_update()
            elif self.cfg.get("update_notified") != val["version"]:
                self.cfg["update_notified"] = val["version"]       # one toast per new version
                config.save(self.cfg)
                text = t("upd_available", val["version"])
                shown = (self.cfg.get("toasts", True) and self.notifier.available
                         and self.notifier.show(APP_TITLE, text, [(t("upd_install"), "update:")]))
                if not shown and self.tray.isVisible():
                    self.tray.showMessage(APP_TITLE, text, app_icon(), 8000)
        elif manual:
            QMessageBox.information(self, t("upd_title"), t("upd_none", VERSION))

    def show_update(self):
        info = getattr(self, "update_info", None)
        if not info:
            return
        from .updater import can_self_update
        if not can_self_update():           # portable / source run: just open the release page
            QDesktopServices.openUrl(QUrl(info["page"]))
            return
        d = UpdateDialog(info, self)
        if d.exec() and d.path:
            self.quit(installer=d.path)

    # ── theme ──────────────────────────────────────────────────
    def retheme(self):
        widgets.clear_cache()
        if hasattr(self, "files_tbl"):
            self.files_tbl.retheme()
        for setter, name, key in self._themed:
            setter(icon(name, key))
        for k in (self.k_down, self.k_up, self.k_active, self.k_session):
            k.retheme()
        self.empty_ico.setStyleSheet(f"background:{C['tertiary_bg']}; border-radius: 16px;")
        dark = theme.current_name == "dark"
        self.theme_btn.setIcon(icon("fa6s.sun" if dark else "fa6s.moon", "head_ic"))
        winint.set_title_bar(int(self.winId()), dark, C["page_bg"], C["emphasis"])
        self.table.viewport().update()

    def toggle_theme(self):
        self.cfg["theme"] = "light" if theme.current_name == "dark" else "dark"
        config.save(self.cfg)
        theme.apply(QApplication.instance(), self.cfg["theme"], self.cfg.get("ui_style", "soft"))
        self.retheme()

    # ── helpers ────────────────────────────────────────────────
    def selected_keys(self) -> list[str]:
        return [_row_of(idx).key for idx in self.table.selectionModel().selectedRows()]

    def _each(self, fn):
        for k in self.selected_keys():
            fn(k)
        self.tick()

    def _log_startup(self):
        import libtorrent as lt
        self.log_event("system", t("log_started", APP_TITLE, VERSION))
        self.log_event("system", f"libtorrent {lt.__version__}, Python {platform.python_version()}, "
                                 f"{platform.system()} {platform.release()} ({platform.version()})")
        try:
            import psutil
            ram = fmt_size(psutil.virtual_memory().total)
        except Exception:
            ram = "—"
        self.log_event("system", f"CPU: {platform.processor() or platform.machine()}, {os.cpu_count()} threads, RAM: {ram}")
        self.log_event("system", f"{t('st_cfg')}: {config.data_dir()}")

    def log_event(self, kind: str, text: str):
        self.log.add(kind, text)

    # ── adding ─────────────────────────────────────────────────
    def add_item(self, item: str):
        """Path to .torrent or magnet URI (dialog, drag & drop, command line, Explorer)."""
        import libtorrent as lt
        is_magnet = item.startswith("magnet:")
        try:
            save_path, paused, prios, category = None, False, None, ""
            ti = None if is_magnet else lt.torrent_info(item)
            cross = self.engine.find_cross(ti) if ti is not None and self.cfg.get("cross_seed", True) else None
            if self.cfg.get("show_add_dialog", True):
                if is_magnet:
                    atp = lt.parse_magnet_uri(item)
                    ihs = atp.info_hashes
                    ih = (ihs.v1 if ihs.has_v1() else ihs.v2).to_bytes()[:20]
                    d = AddTorrentDialog(self.cfg, atp.name or item[:80], None, self,
                                         scrape_src=(list(atp.trackers), ih))
                else:
                    ihs = ti.info_hashes()
                    ih = (ihs.v1 if ihs.has_v1() else ihs.v2).to_bytes()[:20]
                    d = AddTorrentDialog(self.cfg, ti.name(), ti, self, cross=cross,
                                         scrape_src=([tr.url for tr in ti.trackers()], ih))
                self.show_normal()
                if not d.exec():
                    return
                save_path, paused, prios = d.path.text().strip() or None, d.paused.isChecked(), d.priorities()
                category = d.cat.currentData() or ""
                if not d.use_cross():
                    cross = None
            seed_mode = False
            if not cross and ti is not None:
                # files of this torrent already in the folder: check them, or trust them as complete
                folder = save_path or self.cfg.get("save_path") or os.path.expanduser("~/Downloads")
                have, total, got, full = self.engine.existing_files(ti, folder)
                if have:
                    box = QMessageBox(QMessageBox.Question, t("ex_title"),
                                      t("ex_text", have, total, fmt_size(got), folder), QMessageBox.NoButton, self)
                    b_check = box.addButton(t("ex_check"), QMessageBox.AcceptRole)
                    b_seed = box.addButton(t("ex_seed"), QMessageBox.ActionRole) if full else None
                    box.addButton(t("cancel"), QMessageBox.RejectRole)
                    box.setDefaultButton(b_check)
                    if full:
                        box.setInformativeText(t("ex_seed_hint"))
                    self.show_normal()
                    box.exec()
                    if box.clickedButton() is b_seed and b_seed is not None:
                        seed_mode = True
                    elif box.clickedButton() is not b_check:
                        return
            if cross:
                key = self.engine.add_cross(item, cross, paused=paused)
                self.log_event("torrent", t("cross_found", cross["name"]))
            elif is_magnet:
                key = self.engine.add_magnet(item, save_path=save_path, paused=paused)
            else:
                key = self.engine.add_torrent_file(item, save_path=save_path, paused=paused, file_priorities=prios,
                                                   seed_mode=seed_mode)
            if category:
                self.engine.set_category(key, category)
            if is_magnet:
                label = lt.parse_magnet_uri(item).name or item[:60] + "…"
            else:
                label = os.path.basename(item)
            self.log_event("torrent", t("log_added", label))
        except Exception as e:
            QMessageBox.warning(self, APP_TITLE, t("add_error", e))
        self.tick()

    def add_torrent_dialog(self):
        files, _ = QFileDialog.getOpenFileNames(self, t("add_torrent"), "", t("torrent_filter"))
        for f in files:
            self.add_item(f)

    def add_magnet_dialog(self):
        clip = QGuiApplication.clipboard().text().strip()
        text, ok = QInputDialog.getText(self, t("add_magnet"), t("magnet_prompt"),
                                        text=clip if clip.startswith("magnet:") else "")
        if ok and text.strip():
            self.add_item(text.strip())

    def create_torrent_dialog(self):
        d = CreateTorrentDialog(self.cfg, self)
        if d.exec() and d.out_path:
            config.save(self.cfg)
            if d.seed.isChecked():
                try:
                    self.engine.add_torrent_file(d.out_path, save_path=os.path.dirname(os.path.abspath(d.src_path)))
                except Exception as e:
                    QMessageBox.warning(self, APP_TITLE, t("add_error", e))

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() or e.mimeData().hasText():
            e.acceptProposedAction()

    def dropEvent(self, e):
        md = e.mimeData()
        if md.hasUrls():
            for u in md.urls():
                if u.isLocalFile() and u.toLocalFile().lower().endswith(".torrent"):
                    self.add_item(u.toLocalFile())
                elif u.scheme() == "magnet":
                    self.add_item(u.toString())
        elif md.text().startswith("magnet:"):
            self.add_item(md.text().strip())

    # ── removing / menu ────────────────────────────────────────
    def remove_selected(self):
        keys = self.selected_keys()
        if not keys:
            return
        rows = {r.key: r for r in self.model.rows}
        owe = [rows[k] for k in keys if k in rows and self.engine.hnr_left(k)]
        text = t("remove_text", len(keys))
        if owe:
            lines = "\n".join(f"• {r.name[:70]} — {self.engine.hnr_left(r.key)}" for r in owe[:8])
            text += "\n\n" + t("hnr_remove_warn", lines)
        box = QMessageBox(QMessageBox.Warning if owe else QMessageBox.Question, t("remove_title"), text,
                          QMessageBox.Yes | QMessageBox.No, self)
        cb = QCheckBox(t("remove_files"))
        box.setCheckBox(cb)
        if box.exec() == QMessageBox.Yes:
            for k in keys:
                self.engine.remove(k, delete_files=cb.isChecked())
            self.tick()

    def open_folder(self):
        for k in self.selected_keys()[:1]:
            row = next((r for r in self.model.rows if r.key == k), None)
            if row:
                QDesktopServices.openUrl(QUrl.fromLocalFile(row.save_path))

    def copy_magnet(self):
        import libtorrent as lt
        keys = self.selected_keys()
        if keys:
            QGuiApplication.clipboard().setText(lt.make_magnet_uri(self.engine.handles[keys[0]]))

    def context_menu(self, pos):
        if not self.selected_keys():
            return
        m = QMenu(self)
        m.addAction(icon("fa6s.play", "success"), t("start"), lambda: self._each(self.engine.resume))
        m.addAction(icon("fa6s.pause"), t("pause"), lambda: self._each(self.engine.pause))
        m.addSeparator()
        m.addAction(icon("fa6s.folder-open"), t("open_folder"), self.open_folder)
        m.addAction(icon("fa6s.rotate"), t("recheck"), lambda: self._each(self.engine.recheck))
        m.addAction(icon("fa6s.bullhorn"), t("reannounce"), lambda: self._each(self.engine.reannounce))
        m.addAction(icon("fa6s.link"), t("copy_magnet"), self.copy_magnet)
        m.addAction(icon("fa6s.sliders"), t("properties"), self.properties_dialog)
        m.addSeparator()
        cm = m.addMenu(icon("fa6s.tag"), t("cat_menu"))
        cm.addAction(t("cat_none"), lambda: self.set_category_selected(""))
        for c in self.cfg.get("categories", []):
            cm.addAction(c["name"], lambda n=c["name"]: self.set_category_selected(n))
        cm.addSeparator()
        cm.addAction(icon("fa6s.plus"), t("cat_new"), self.new_category_for_selected)
        m.addAction(icon("fa6s.hashtag", "teal"), t("tags_menu"), self.tags_dialog)
        m.addAction(icon("fa6s.folder-tree"), t("move_to"), self.move_selected)
        m.addAction(icon("fa6s.server"), t("edit_trackers"), self.edit_trackers)
        m.addAction(icon("fa6s.right-left"), t("replace_trackers"), self.replace_trackers)
        m.addSeparator()
        m.addAction(icon("fa6s.trash-can", "danger"), t("remove"), self.remove_selected)
        m.exec(self.table.viewport().mapToGlobal(pos))

    def start_network_extras(self):
        """GeoIP database and IP filter list load in background threads."""
        import threading
        from .netx import GeoIP, load_ip_filter
        if self.cfg.get("geoip", True):
            if self.geo is None:
                self.geo = GeoIP(config.data_dir(), self.cfg.get("language", "ru"))
            self.geo.start()
        else:
            self.geo = None
        src = self.cfg.get("ip_filter", "").strip()
        if not src:
            if self.engine.ip_filter_rules:
                self.engine.set_ip_filter([])
            return

        def work():
            try:
                self._ipf_result = ("ok", load_ip_filter(src, config.data_dir()))
            except Exception as e:
                self._ipf_result = ("error", str(e))
        threading.Thread(target=work, daemon=True).start()

    def import_dialog(self):
        from .dialogs import ImportDialog
        d = ImportDialog(self.engine, self.cfg, self)
        if d.exec():
            done, skipped = d.run_import()
            config.save(self.cfg)
            self.refresh_category_combo()
            self.log_event("torrent", t("imp_done", done, skipped))
            QMessageBox.information(self, t("imp_title"), t("imp_done", done, skipped))
            self.tick()

    # ── notifications ──────────────────────────────────────────
    def notify(self, text: str, key: str, folder: bool):
        """Windows toast with buttons; tray balloon if toasts are off or unavailable."""
        if self.cfg.get("toasts", True) and self.notifier.available:
            buttons = ([(t("nt_open_folder"), f"open:{key}")] if folder else []) + [(t("nt_show"), f"show:{key}")]
            if self.notifier.show(APP_TITLE, text, buttons):
                return
        if self.tray.isVisible():
            self.tray.showMessage(APP_TITLE, text, app_icon(), 5000)

    def on_toast(self, arg: str):
        action, _, key = arg.partition(":")
        if action == "update":
            self.show_normal()
            self.show_update()
            return
        if action == "open" and key in self.engine.handles:
            row = next((r for r in self.engine.rows() if r.key == key), None)
            if row:
                QDesktopServices.openUrl(QUrl.fromLocalFile(row.save_path))
            return
        self.show_normal()
        if key:
            for i in range(self.proxy.rowCount()):
                if _row_of(self.proxy.index(i, 0)).key == key:
                    self.table.selectRow(i)
                    break

    # ── window / column state ──────────────────────────────────
    def save_ui_state(self):
        from PySide6.QtCore import QByteArray
        def b64(ba: QByteArray) -> str:
            return bytes(ba.toBase64()).decode()
        self.cfg["ui_state"] = {
            "geometry": b64(self.saveGeometry()),
            "header": b64(self.table.horizontalHeader().saveState()),
            "columns": len(COLS),
            "splitter": b64(self.split.saveState()),
            "tab": self.tabs.currentIndex(),
            "files_header": b64(self.files_tbl.header().saveState()),
        }
        config.save(self.cfg)

    def restore_ui_state(self):
        from PySide6.QtCore import QByteArray
        st = self.cfg.get("ui_state") or {}

        def ba(key):
            v = st.get(key)
            return QByteArray.fromBase64(v.encode()) if v else None
        if ba("geometry") is not None:
            self.restoreGeometry(ba("geometry"))
        if ba("header") is not None and st.get("columns") == len(COLS):   # a new column = start fresh
            self.table.horizontalHeader().restoreState(ba("header"))
        if ba("splitter") is not None:
            self.split.restoreState(ba("splitter"))
        if ba("files_header") is not None:
            self.files_tbl.header().restoreState(ba("files_header"))
        if isinstance(st.get("tab"), int):
            self.tabs.setCurrentIndex(st["tab"])

    def columns_menu(self, pos):
        hh = self.table.horizontalHeader()
        m = QMenu(self)
        m.addSection(t("cols_menu"))
        for c in range(len(COLS)):
            if c == C_NAME:
                continue
            a = m.addAction(t(COLS[c]))
            a.setCheckable(True)
            a.setChecked(not hh.isSectionHidden(c))
            a.toggled.connect(lambda on, c=c: hh.setSectionHidden(c, not on))
        m.addSeparator()
        m.addAction(icon("fa6s.rotate-left"), t("cols_reset"), lambda: hh.restoreState(self._default_header))
        m.exec(hh.mapToGlobal(pos))

    def tags_dialog(self):
        from .dialogs import TagsDialog
        keys = self.selected_keys()
        if not keys:
            return
        rows = {r.key: r for r in self.model.rows}
        sets = [set(rows[k].tags) for k in keys if k in rows]
        all_on = set.intersection(*sets) if sets else set()
        some = set.union(*sets) - all_on if sets else set()
        d = TagsDialog(self.engine.all_tags(), all_on, some, self)
        if not d.exec():
            return
        states = d.states()
        for k in keys:
            cur = set(rows[k].tags) if k in rows else set()
            for tag, st in states.items():
                if st == Qt.Checked:
                    cur.add(tag)
                elif st == Qt.Unchecked:
                    cur.discard(tag)
            self.engine.set_tags(k, list(cur))
        self.cfg["tags"] = sorted(states, key=str.lower)
        config.save(self.cfg)
        self.tick()

    def refresh_tag_combo(self):
        tags = self.engine.all_tags()
        if tags == self._tags_shown:
            return
        self._tags_shown = tags
        cur = self.tag_combo.currentData()
        ic = icon("fa6s.hashtag")
        self.tag_combo.blockSignals(True)
        self.tag_combo.clear()
        self.tag_combo.addItem(ic, t("tags_all"), "")
        for tag in tags:
            self.tag_combo.addItem(ic, tag, tag)
        self.tag_combo.addItem(ic, t("tags_none"), NO_TAG)
        self.tag_combo.setCurrentIndex(max(0, self.tag_combo.findData(cur)))
        self.tag_combo.blockSignals(False)
        self.proxy.set_filter(tag=self.tag_combo.currentData() or "")

    def peers_menu(self, pos):
        it = self.peers_tbl.itemAt(pos)
        if it is None:
            return
        cell = self.peers_tbl.item(it.row(), 0)
        ip = cell.data(Qt.UserRole) if cell else ""
        if not ip:
            return
        m = QMenu(self)
        m.addAction(icon("fa6s.ban", "danger"), t("peer_ban", ip),
                    lambda: (self.engine.ban(ip, t("peer_ban_manual")), self.tick()))
        m.addAction(icon("fa6s.copy"), t("peer_copy_ip"), lambda: QGuiApplication.clipboard().setText(ip))
        m.exec(self.peers_tbl.viewport().mapToGlobal(pos))

    def set_category_selected(self, name: str):
        for k in self.selected_keys():
            self.engine.set_category(k, name)
        self.tick()

    def new_category_for_selected(self):
        name, ok = QInputDialog.getText(self, t("set_grp_cats"), t("cat_prompt"))
        name = name.strip()
        if not ok or not name:
            return
        if name not in [c["name"] for c in self.cfg.get("categories", [])]:
            self.cfg.setdefault("categories", []).append({"name": name, "path": ""})
            config.save(self.cfg)
            self.refresh_category_combo()
        self.set_category_selected(name)

    def move_selected(self):
        keys = self.selected_keys()
        if not keys:
            return
        row = next((r for r in self.model.rows if r.key == keys[0]), None)
        d = QFileDialog.getExistingDirectory(self, t("move_to"), row.save_path if row else "")
        if d:
            for k in keys:
                self.engine.move(k, d)

    def edit_trackers(self):
        from .dialogs import TrackersDialog
        keys = self.selected_keys()
        if not keys:
            return
        row = next((r for r in self.model.rows if r.key == keys[0]), None)
        d = TrackersDialog(row.name if row else keys[0], self.engine.trackers_raw(keys[0]), self)
        if d.exec():
            self.engine.set_trackers(keys[0], d.result_trackers())
            self.tick()

    def replace_trackers(self):
        from .dialogs import ReplaceTrackersDialog
        sel = self.selected_keys()
        d = ReplaceTrackersDialog(len(sel), len(self.engine.handles), self)
        if d.exec() and d.find.text():
            keys = sel if d.sel.isChecked() else list(self.engine.handles)
            n = self.engine.replace_in_trackers(keys, d.find.text(), d.repl.text())
            self.log_event("tracker", t("rep_done", n))
            QMessageBox.information(self, t("rep_title"), t("rep_done", n))
            self.tick()

    def refresh_category_combo(self):
        cur = self.cat_combo.currentData()
        self.cat_combo.blockSignals(True)
        self.cat_combo.clear()
        tag = icon("fa6s.tag")
        self.cat_combo.addItem(tag, t("cat_all"), "")
        for c in self.cfg.get("categories", []):
            self.cat_combo.addItem(tag, c["name"], c["name"])
        self.cat_combo.addItem(tag, t("cat_none"), NO_CATEGORY)
        self.cat_combo.setCurrentIndex(max(0, self.cat_combo.findData(cur)))
        self.cat_combo.blockSignals(False)
        self.proxy.set_filter(category=self.cat_combo.currentData() or "")

    def properties_dialog(self):
        keys = self.selected_keys()
        if not keys:
            return
        key = keys[0]
        row = next((r for r in self.model.rows if r.key == key), None)
        d = PropertiesDialog(row.name if row else key, self.engine.props(key), self)
        if d.exec():
            p = d.result_props()
            for k in keys:                       # same settings for every selected torrent
                self.engine.set_props(k, p)

    def settings_dialog(self):
        d = SettingsDialog(self.cfg, self)
        if d.exec():
            old_theme = (self.cfg.get("theme"), self.cfg.get("ui_style"))
            old_net = (self.cfg.get("ip_filter"), self.cfg.get("geoip"))
            self.cfg.update(d.result_cfg())
            config.save(self.cfg)
            self.engine.apply_settings(self.cfg)
            self.set_mini(bool(self.cfg.get("mini_widget")))
            self._sched_state = None             # re-apply schedule limits on next tick
            self.refresh_category_combo()
            if (self.cfg.get("ip_filter"), self.cfg.get("geoip")) != old_net:
                self.start_network_extras()
            if (self.cfg.get("theme"), self.cfg.get("ui_style")) != old_theme:
                theme.apply(QApplication.instance(), self.cfg["theme"], self.cfg.get("ui_style", "soft"))
                self.retheme()

    # ── refresh ────────────────────────────────────────────────
    def tick(self):
        self._ticks = getattr(self, "_ticks", 0) + 1
        if self._ticks % 600 == 0:
            self._maybe_check_updates()
        if self._ticks % 5 == 0:                 # watch folder + VPN binding every 5 s
            self.engine.scan_watch_dir()
            ok = self.engine.check_binding()
            if ok is not None and self.tray.isVisible():
                bind = self.cfg.get("bind_interface", "")
                self.tray.showMessage(APP_TITLE, t("meta_vpn_ok" if ok else "meta_vpn_down", bind), app_icon(), 5000)
        if self._update_result is not None:
            self._handle_update_result()
        if self._ipf_result is not None:
            kind, val = self._ipf_result
            self._ipf_result = None
            if kind == "ok":
                self.engine.set_ip_filter(val)
                self.log_event("network", t("ipf_loaded", len(val)))
            else:
                self.log_event("error", t("ipf_error", val))
        self.engine.poll()
        rows = self.engine.rows()
        self.model.update(rows)
        self.list_stack.setCurrentIndex(0 if rows else 1)

        for key, b in self.pills.items():
            states = FILTER_STATES[key]
            n = len(rows) if states is None else sum(1 for r in rows if r.state in states)
            b.setText(f"{t('f_' + key)}  {n}")

        hosts = sorted({r.tracker_host for r in rows})
        have = [self.tracker_combo.itemData(i) for i in range(1, self.tracker_combo.count())]
        if hosts != have:
            cur = self.tracker_combo.currentData()
            self.tracker_combo.blockSignals(True)
            while self.tracker_combo.count() > 1:
                self.tracker_combo.removeItem(1)
            srv = icon("fa6s.server")
            for h in hosts:
                self.tracker_combo.addItem(srv, h, h)
            self.tracker_combo.setCurrentIndex(max(0, self.tracker_combo.findData(cur)))
            self.tracker_combo.blockSignals(False)
            self.proxy.set_filter(tracker=self.tracker_combo.currentData() or "")

        while self.engine.messages:
            self.log_event(*self.engine.messages.pop(0))
        while self.engine.finished_names:
            name, key = self.engine.finished_names.pop(0)
            self.log_event("torrent", t("finished_note", name))
            self.notify(t("finished_note", name), key, folder=True)
            self._run_done_action(key)

        bad = {r.key for r in rows if r.state == "tracker_error"}
        for r in rows:
            if r.key in bad and r.key not in self._tr_bad:
                self.notify(f"{t('toast_tr_error')}: {r.name}\n{r.error}", r.key, folder=False)
        self._tr_bad = bad

        self.refresh_tag_combo()
        while self.engine.cross_results:
            name, key, prog = self.engine.cross_results.pop(0)
            msg = t("cross_ok", name) if prog >= 1.0 else t("cross_partial", name, f"{prog:.1%}")
            self.log_event("torrent" if prog >= 1.0 else "error", msg)
            self.notify(msg, key, folder=False)

        while self.engine.stopped_names:
            name, key = self.engine.stopped_names.pop(0)
            self.log_event("torrent", t("stopped_note", name))
            self.notify(t("stopped_note", name), key, folder=False)

        sched = self.apply_schedule()

        st = self.engine.ses.status()
        down, up = st.payload_download_rate, st.payload_upload_rate
        self.graph.push(down, up)
        self._tick_extras(rows, down, up)
        active = sum(1 for r in rows if r.state in ("downloading", "seeding", "metadata", "checking", "tracker_error"))
        self.k_down.set(fmt_size(down) + "/s", t("kpi_down"))
        self.k_up.set(fmt_size(up) + "/s", t("kpi_up"))
        self.k_active.set(str(active), t("kpi_active", len(rows)))
        self.k_session.set(fmt_size(st.total_payload_upload - self._session_up0), t("kpi_session"))

        dht = t("meta_dht", st.dht_nodes) if self.cfg.get("dht") else t("meta_dht_off")
        dot = f'<span style="color:{C["border"]}">&nbsp;•&nbsp;</span>'
        parts = [t("subtitle"), t("meta_version", VERSION), t("meta_port", self.engine.ses.listen_port()), dht]
        if config.is_portable():
            parts.insert(2, t("meta_portable"))
        if self.engine.bans:
            parts.append(t("meta_bans", len(self.engine.bans)))
        if sched:
            parts.append(f'<span style="color:{C["warning_text"]}">&#9200; {t("meta_sched")}</span>')
        bind = self.cfg.get("bind_interface", "")
        if bind:
            if self.engine.bind_ok:
                parts.append(f'<span style="color:{C["success_text"] if theme.current_name == "dark" else C["success"]}">'
                             f'&#128274; {t("meta_vpn_ok", bind)}</span>')
            else:
                parts.append(f'<span style="color:{C["danger"]}">&#9888; {t("meta_vpn_down", bind)}</span>')
        self.meta.setText(dot.join(parts))
        self.tray.setToolTip(f"{APP_TITLE}\n↓ {fmt_size(down)}/s  ↑ {fmt_size(up)}/s")
        self.refresh_details()

    def refresh_details(self):
        if self.tabs.currentIndex() == 5:        # global, works without a selected torrent
            self.stats_view.refresh(self)
        keys = self.selected_keys()
        row = next((r for r in self.model.rows if keys and r.key == keys[0]), None)
        if row is None:
            for lab in self.g.values():
                lab.setText("—")
            self.peers_tbl.setRowCount(0)
            self.peers_summary.hide()
            self.tr_tbl.setRowCount(0)
            self.files_tbl.clear()
            self.files_tbl.key = None
            self.piecebar.set_pieces([], False)
            return
        trs = self.engine.trackers(row.key)
        self.g["gen_path"].setText(row.save_path)
        self.g["gen_size"].setText(fmt_size(row.size))
        self.g["gen_downloaded"].setText(fmt_size(row.downloaded))
        self.g["gen_uploaded"].setText(fmt_size(row.uploaded))
        self.g["gen_ratio"].setText(fmt_ratio(row.ratio))
        self.g["gen_tracker"].setText(trs[0]["url"] if trs else "DHT")
        self.g["gen_error"].setText(row.error or "—")
        self.g["gen_error"].setStyleSheet(f"color:{C['danger']};" if row.error else "")
        inf = self.engine.info(row.key)
        def when(ts):
            return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S") if ts and ts > 0 else "—"
        def dur(sec):
            h, m = divmod(sec // 60, 60)
            return f"{h}:{m:02d}:{sec % 60:02d}"
        vals = {
            "gen_active": t("gen_active_val", dur(inf.get("active", 0)), dur(inf.get("seeding", 0))),
            "gen_added": when(inf.get("added")),
            "gen_completed": when(inf.get("completed")),
            "gen_private": "—" if inf.get("private") is None else t("yes" if inf["private"] else "no"),
            "gen_creator": inf.get("creator") or "—",
            "gen_hash1": inf.get("v1") or "—",
            "gen_hash2": inf.get("v2") or "—",
            "gen_piece_info": (f"{inf['pieces']} × {fmt_size(inf['piece_len'])}" if inf.get("pieces") else "—"),
            "gen_files": str(inf.get("files") or "—"),
            "gen_created": when(inf.get("created")),
            "gen_comment": inf.get("comment") or "—",
            "gen_hnr": row.hnr or ("✓" if self.engine.tracker_rule(row.tracker_host) else "—"),
            "gen_tags": ", ".join(row.tags) or "—",
            "gen_cat": row.category or "—",
        }
        for k, v in vals.items():
            lab = self.g[k]
            if lab.text() != v:
                lab.setText(v)
                lab.setToolTip(v if len(v) > 30 else "")

        tab = self.tabs.currentIndex()
        if tab == 0:
            self.piecebar.set_pieces(self.engine.pieces(row.key), row.progress >= 1.0)
        elif tab == 1:
            self.refresh_files(row.key)
        elif tab == 2:
            peers = self.engine.peers(row.key)
            self.peers_tbl.setRowCount(len(peers))
            by_country: dict[str, list] = {}
            for p in peers:
                code, cname = self.geo.lookup(p["host"]) if self.geo else ("", "")
                ent = by_country.setdefault(code, [cname, 0])
                ent[1] += 1
            self.peers_summary.setText(_country_strip(by_country, len(peers)))
            self.peers_summary.setVisible(bool(peers))
            for i, p in enumerate(peers):
                code, cname = self.geo.lookup(p["host"]) if self.geo else ("", "")
                country = cname or code
                for j, v in enumerate((p["ip"], country, p["client"], f"{p['progress'] * 100:.1f}%",
                                       fmt_speed(p["down"]), fmt_speed(p["up"]))):
                    it = QTableWidgetItem(v)
                    if j == 0:
                        it.setData(Qt.UserRole, p["host"])
                        it.setIcon(widgets.icon("fa6s.circle-user" if p["progress"] < 1 else "fa6s.seedling",
                                                "primary" if p["progress"] < 1 else "success"))
                    elif j == 1 and code:
                        it.setIcon(widgets.flag_icon(code) or widgets.icon("fa6s.earth-europe", "info"))
                        it.setToolTip(f"{code} — {cname}")
                    self.peers_tbl.setItem(i, j, it)
        elif tab == 3:
            self.tr_tbl.setRowCount(len(trs))
            for i, tr in enumerate(trs):
                bad = bool(tr["message"])
                for j, v in enumerate((tr["url"], str(tr["tier"]), tr["message"] or t("tr_ok"))):
                    it = QTableWidgetItem(v)
                    it.setToolTip(v)
                    if j == 0:
                        it.setIcon(widgets.icon("fa6s.circle-exclamation" if bad else "fa6s.circle-check",
                                                "danger" if bad else "success"))
                    elif j == 2:
                        it.setForeground(QColor(C["danger" if bad else "success"]))
                    self.tr_tbl.setItem(i, j, it)

    # ── speed schedule ─────────────────────────────────────────
    def apply_schedule(self) -> bool:
        """Switch between normal and scheduled global limits; returns True inside the window."""
        cfg = self.cfg
        active = False
        if cfg.get("sched_enabled"):
            now = datetime.now().strftime("%H:%M")
            a, b = cfg.get("sched_from", "08:00"), cfg.get("sched_to", "23:00")
            active = (a <= now < b) if a <= b else (now >= a or now < b)   # window may cross midnight
        if active != self._sched_state:
            if active:
                self.engine.set_global_limits(cfg.get("sched_dl", 0), cfg.get("sched_ul", 0))
            else:
                self.engine.set_global_limits(cfg.get("dl_limit", 0), cfg.get("ul_limit", 0))
            if self._sched_state is not None or active:
                self.log_event("system", t("sch_on" if active else "sch_off"))
            self._sched_state = active
        return active

    # ── files tab ──────────────────────────────────────────────
    PRIO_TEXT = {PRIO_SKIP: "prio_skip", PRIO_NORMAL: "prio_normal", PRIO_HIGH: "prio_high"}

    def refresh_files(self, key: str):
        self.files_tbl.update_files(key, self.engine.files(key))

    def files_menu(self, pos):
        key = self.files_tbl.key
        idxs = self.files_tbl.selected_indices()
        if not key or not idxs:
            return
        m = QMenu(self)
        for prio, name, col in ((PRIO_HIGH, "fa6s.angles-up", "primary"),
                                (PRIO_NORMAL, "fa6s.minus", "secondary"),
                                (PRIO_SKIP, "fa6s.ban", "danger")):
            m.addAction(icon(name, col), t(self.PRIO_TEXT[prio]),
                        lambda p=prio: (self.engine.set_file_priority(key, idxs, p), self.refresh_files(key)))
        m.addSeparator()
        m.addAction(icon("fa6s.folder-open", "warning"), t("open_folder"), self.open_folder)
        m.exec(self.files_tbl.viewport().mapToGlobal(pos))

    # ── window / quit ──────────────────────────────────────────
    def show_normal(self):
        self.show()
        self.setWindowState(self.windowState() & ~Qt.WindowMinimized)
        self.raise_()
        self.activateWindow()

    def closeEvent(self, e):
        if not self._quitting and self.cfg.get("minimize_to_tray") and QSystemTrayIcon.isSystemTrayAvailable():
            e.ignore()
            self.hide()
            return
        self.quit()
        e.accept()

    def quit(self, installer: str = ""):
        if self._quitting:
            return
        self._quitting = True
        try:
            self.save_ui_state()
        except Exception:
            pass
        self.timer.stop()
        self.tray.hide()
        if self.mini is not None:
            self.mini.hide()
        winint.keep_awake(False)
        self.engine.shutdown()
        if installer:                            # self-update: session saved, let the installer in
            from . import updater
            winint.release_mutex()
            updater.run_installer(installer)
        QApplication.quit()
