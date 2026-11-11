"""BitComet-style detail tabs: Summary bars, Piece map, Task logs, peer piece bars."""
from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QHBoxLayout, QHeaderView, QLabel,
                               QScrollArea, QStyledItemDelegate, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from . import theme, widgets
from .i18n import t

C = theme.current

# piece state → colour key: empty · downloading · done · skipped
STATE_COLOR = {0: "secondary_bg", 1: "pink", 2: "primary", 3: "secondary"}


def _rounded(p: QPainter, r: QRectF) -> None:
    clip = QPainterPath()
    clip.addRoundedRect(r, 5, 5)
    p.setClipPath(clip)


class StateBar(QWidget):
    """'Downloaded pieces': done in blue, downloading in pink, skipped files gray."""
    def __init__(self):
        super().__init__()
        self.states: list[int] = []
        self.complete = False
        self.setFixedHeight(18)

    def set_states(self, states: list[int], complete: bool):
        self.states, self.complete = states, complete
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(0, 1, self.width(), self.height() - 2)
        _rounded(p, r)
        p.fillRect(r, QColor(C["secondary_bg"]))
        n = len(self.states)
        if n:
            done = QColor(C["success" if self.complete else "primary"])
            cols = max(1, int(r.width()))
            for x in range(cols):
                a = x * n // cols
                b = max((x + 1) * n // cols, a + 1)
                chunk = self.states[a:b]
                if 1 in chunk:
                    c = QColor(C["pink"])
                elif 2 in chunk:
                    c = QColor(done)
                    c.setAlphaF(0.3 + 0.7 * chunk.count(2) / len(chunk))
                elif 3 in chunk:
                    c = QColor(C["secondary"])
                    c.setAlphaF(0.45)
                else:
                    continue
                p.fillRect(QRectF(r.left() + x, r.top(), 1.2, r.height()), c)
        p.end()


class AvailBar(QWidget):
    """'Available copies': red where nobody (not even we) has the piece, deeper blue = more copies."""
    def __init__(self):
        super().__init__()
        self.avail: list[int] = []
        self.setFixedHeight(18)

    def set_avail(self, avail: list[int]):
        self.avail = avail
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(0, 1, self.width(), self.height() - 2)
        _rounded(p, r)
        p.fillRect(r, QColor(C["secondary_bg"]))
        n = len(self.avail)
        if n:
            top = max(4, max(self.avail))
            cols = max(1, int(r.width()))
            for x in range(cols):
                a = x * n // cols
                b = max((x + 1) * n // cols, a + 1)
                low = min(self.avail[a:b])
                if low <= 0:
                    c = QColor(C["danger"])
                    c.setAlphaF(0.75)
                else:
                    c = QColor(C["primary"])
                    c.setAlphaF(0.3 + 0.7 * min(1.0, low / top))
                p.fillRect(QRectF(r.left() + x, r.top(), 1.2, r.height()), c)
        p.end()


def copies(avail: list[int]) -> float:
    """Distributed copies like BitComet / qBittorrent: full copies + share of pieces above that."""
    if not avail:
        return 0.0
    low = min(avail)
    return low + sum(1 for a in avail if a > low) / len(avail)


# ── piece map ─────────────────────────────────────────────────────
class _Grid(QWidget):
    CELL = 12

    def __init__(self):
        super().__init__()
        self.states: list[int] = []
        self.complete = False

    def set_states(self, states, complete):
        resize = len(states) != len(self.states)
        self.states, self.complete = states, complete
        if resize:
            self._fit()
        self.update()

    def _fit(self):
        cols = max(1, (self.width() - 2) // self.CELL)
        rows = (len(self.states) + cols - 1) // cols
        self.setMinimumHeight(rows * self.CELL + 2)

    def resizeEvent(self, e):
        self._fit()
        super().resizeEvent(e)

    def paintEvent(self, e):
        p = QPainter(self)
        cell = self.CELL
        cols = max(1, (self.width() - 2) // cell)
        colors = {k: QColor(C[v]) for k, v in STATE_COLOR.items()}
        if self.complete:
            colors[2] = QColor(C["success"])
        edge = QPen(QColor(C["border_soft"]), 1)
        vis = e.rect()
        first = max(0, (vis.top() // cell) * cols)
        last = min(len(self.states), ((vis.bottom() // cell) + 1) * cols)
        for i in range(first, last):
            r, c = divmod(i, cols)
            rect = QRectF(1 + c * cell, 1 + r * cell, cell - 2, cell - 2)
            st = self.states[i]
            p.fillRect(rect, colors[st])
            if st == 0:
                p.setPen(edge)
                p.drawRect(rect)
            elif st == 3:
                p.setPen(QPen(QColor(C["body_bg"]), 1))
                p.drawLine(rect.topLeft(), rect.bottomRight())
                p.drawLine(rect.topRight(), rect.bottomLeft())
        p.end()


class PieceMapView(QWidget):
    """Every piece as a square, legend below — BitComet's 'Piece map'."""
    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 6)
        lay.setSpacing(6)
        self.info = QLabel()
        self.info.setObjectName("meta")
        lay.addWidget(self.info)
        self.grid = _Grid()
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setFrameShape(QScrollArea.NoFrame)
        sc.setWidget(self.grid)
        lay.addWidget(sc, 1)
        leg = QHBoxLayout()
        leg.setSpacing(18)
        for st, key in ((0, "pm_empty"), (1, "pm_downloading"), (2, "pm_done"), (3, "pm_skipped")):
            sw = QLabel()
            sw.setFixedSize(12, 12)
            border = C["border"] if st == 0 else C[STATE_COLOR[st]]
            sw.setStyleSheet(f"background:{C[STATE_COLOR[st]]}; border:1px solid {border}; border-radius:2px;")
            leg.addWidget(sw)
            leg.addWidget(QLabel(t(key)))
        leg.addStretch()
        lay.addLayout(leg)

    def show_map(self, pm: dict, complete: bool):
        from .ui import fmt_size
        st = pm.get("states", [])
        self.grid.set_states(st, complete)
        if st:
            self.info.setText(t("pm_info", pm["have"], pm["total"], fmt_size(pm["piece_len"]),
                                st.count(1)))
        else:
            self.info.setText(t("pm_none"))


# ── task logs ─────────────────────────────────────────────────────
class TaskLogView(QWidget):
    """Log of one torrent: Time / Type / Message (filled from Engine.task_log)."""
    def __init__(self):
        super().__init__()
        from .ui import LOG_ICON, LOG_KINDS, LOG_STYLE
        self.LOG_ICON, self.LOG_STYLE = LOG_ICON, LOG_STYLE
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 4)
        bar = QHBoxLayout()
        self.filter = QComboBox()
        self.filter.addItem(widgets.icon("fa6s.list"), t("log_all"), "")
        for k in LOG_KINDS:
            self.filter.addItem(widgets.icon(LOG_ICON[k], LOG_STYLE[k][1]), t("lk_" + k), k)
        self.filter.currentIndexChanged.connect(lambda _: self._reset())
        bar.addWidget(self.filter)
        bar.addStretch()
        lay.addLayout(bar)
        self.tbl = QTableWidget(0, 3)
        self.tbl.setHorizontalHeaderLabels([t("log_time"), t("log_type"), t("log_msg")])
        self.tbl.verticalHeader().hide()
        self.tbl.verticalHeader().setDefaultSectionSize(26)
        self.tbl.setShowGrid(False)
        self.tbl.setWordWrap(False)
        self.tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl.setSelectionBehavior(QAbstractItemView.SelectRows)
        hh = self.tbl.horizontalHeader()
        hh.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        hh.setHighlightSections(False)
        hh.setSectionResizeMode(2, QHeaderView.Stretch)
        self.tbl.setColumnWidth(0, 150)
        self.tbl.setColumnWidth(1, 125)
        lay.addWidget(self.tbl, 1)
        self.key, self.shown = None, 0

    def _reset(self):
        self.key, self.shown = None, 0

    def show_log(self, key: str | None, entries: list[tuple[str, str, str]]):
        if key != self.key or len(entries) < self.shown:
            self.tbl.setRowCount(0)
            self.key, self.shown = key, 0
        want = self.filter.currentData()
        sb = self.tbl.verticalScrollBar()
        follow = sb.value() >= sb.maximum() - 30
        for ts, kind, text in entries[self.shown:]:
            if want and want != kind:
                continue
            r = self.tbl.rowCount()
            self.tbl.insertRow(r)
            bg, fg = self.LOG_STYLE.get(kind, self.LOG_STYLE["system"])
            for c, v in enumerate((ts, t("lk_" + kind), text)):
                it = QTableWidgetItem(v)
                if c == 1:
                    it.setForeground(QColor(C[fg]))
                    it.setFont(QFont(it.font().family(), -1, QFont.DemiBold))
                    it.setIcon(widgets.icon(self.LOG_ICON.get(kind, "fa6s.circle-info"), fg))
                if kind == "error":
                    it.setBackground(QColor(C["danger_subtle"]))
                if c == 2:
                    it.setToolTip(text)
                self.tbl.setItem(r, c, it)
        self.shown = len(entries)
        if follow:
            self.tbl.scrollToBottom()


# ── peers: piece bar in a cell ────────────────────────────────────
class PeerPiecesDelegate(QStyledItemDelegate):
    """Draws the peer's pieces (list of 0..1 fractions in UserRole+1) as a small bar."""
    ROLE = Qt.UserRole + 1

    def paint(self, painter, option, index):
        super().paint(painter, option, index)
        data = index.data(self.ROLE) or []
        r = QRectF(option.rect).adjusted(4, 7, -4, -7)
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        _rounded(painter, r)
        painter.fillRect(r, QColor(C["secondary_bg"]))
        n = len(data)
        if n:
            w = r.width() / n
            base = QColor(C["primary"])
            for i, f in enumerate(data):
                if f > 0:
                    c = QColor(base)
                    c.setAlphaF(0.25 + 0.75 * f)
                    painter.fillRect(QRectF(r.left() + i * w, r.top(), w + 0.6, r.height()), QBrush(c))
        painter.restore()

    def sizeHint(self, option, index):
        return QSize(120, 26)
