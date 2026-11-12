# SPDX-License-Identifier: LGPL-3.0-or-later
"""Filter sidebar, Move dialog, Statistics window, keyboard shortcuts, .torrent links."""
from __future__ import annotations

import os
import shutil
import tempfile
from datetime import date, datetime, timedelta

from PySide6.QtCore import Qt, QRectF, QPointF, QThread, Signal, QSize, QTimer
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPen, QFontMetrics, QGuiApplication
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QDialog, QFileDialog, QFrame, QGridLayout,
                               QHBoxLayout, QHeaderView, QLabel, QMenu, QMessageBox, QPushButton,
                               QRadioButton, QTableWidget, QTableWidgetItem, QToolTip, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget, QAbstractItemView)

from . import APP_TITLE, theme, widgets
from .i18n import t
from .ui import (C, DiskBar, PathCombo, _section, _theme_title_bar, app_icon, fmt_size,
                 icon, _fmt_uptime, NO_CATEGORY, NO_TAG)


# ============================================================
#  Filter sidebar (like qBittorrent's)
# ============================================================

def _stalled(r) -> bool:
    return r.state in ("downloading", "seeding", "tracker_error", "metadata") and not r.down_rate and not r.up_rate


# key → (icon, colour, predicate on TorrentRow). Same meaning as qBittorrent's status filters.
SIDEBAR_STATES = {
    "all":         ("fa6s.layer-group", "primary", lambda r: True),
    "downloading": ("fa6s.arrow-down", "primary",
                    lambda r: r.progress < 1 and r.state not in ("error", "missing", "moving")),
    "seeding":     ("fa6s.arrow-up", "success",
                    lambda r: r.progress >= 1 and r.state in ("seeding", "finished", "tracker_error", "queued")),
    "completed":   ("fa6s.circle-check", "success", lambda r: r.progress >= 1),
    "running":     ("fa6s.play", "success",
                    lambda r: r.state not in ("stopped", "paused", "missing", "error", "queued")),
    "stopped":     ("fa6s.stop", "secondary", lambda r: r.state in ("stopped", "paused")),
    "active":      ("fa6s.bolt", "orange", lambda r: r.down_rate > 0 or r.up_rate > 0),
    "inactive":    ("fa6s.power-off", "secondary", lambda r: not (r.down_rate > 0 or r.up_rate > 0)),
    "stalled":     ("fa6s.hourglass-half", "warning", _stalled),
    "checking":    ("fa6s.rotate", "warning", lambda r: r.state == "checking"),
    "moving":      ("fa6s.truck-moving", "info", lambda r: r.state == "moving"),
    "missing":     ("fa6s.folder-closed", "danger", lambda r: r.state == "missing"),
    "error":       ("fa6s.triangle-exclamation", "danger", lambda r: r.state in ("error", "tracker_error")),
}
TRACKER_ERR = "\x00err"       # pseudo tracker: torrents whose trackers all fail
ROLE_KEY = Qt.UserRole
ROLE_SECTION = Qt.UserRole + 1


class FilterSidebar(QTreeWidget):
    """Status / Category / Tag / Tracker lists with counts. One active entry per section; the
    sections combine (Downloading + category Films + tracker X)."""
    changed = Signal(str, str)          # section, key
    action = Signal(str, str, str)      # what, section, key (context menu)

    SECTIONS = (("status", "fa6s.filter", "primary"), ("category", "fa6s.tag", "purple"),
                ("tag", "fa6s.hashtag", "teal"), ("tracker", "fa6s.server", "info"))

    def __init__(self):
        super().__init__()
        self.setObjectName("sidebar")
        self.setColumnCount(2)
        self.setHeaderHidden(True)
        self.setRootIsDecorated(False)
        self.setIndentation(6)
        self.setFocusPolicy(Qt.NoFocus)
        self.setSelectionMode(QAbstractItemView.NoSelection)
        self.setUniformRowHeights(True)
        self.setIconSize(QSize(14, 14))
        self.setMouseTracking(True)
        self.header().setStretchLastSection(False)
        self.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.setMinimumWidth(170)
        self.active = {"status": "all", "category": "", "tag": "", "tracker": ""}
        self.tops: dict[str, QTreeWidgetItem] = {}
        self._keys: dict[str, list] = {}
        for sec, ic, col in self.SECTIONS:
            top = QTreeWidgetItem([t("sb_sec_" + sec), ""])
            top.setData(0, ROLE_SECTION, sec)
            top.setFlags(Qt.ItemIsEnabled)
            f = QFont(theme.TITLE_FONT, 10); f.setWeight(QFont.DemiBold); top.setFont(0, f)
            self.addTopLevelItem(top)
            top.setExpanded(True)
            self.tops[sec] = top
            self._keys[sec] = []
        self.itemClicked.connect(self._clicked)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)
        self.retheme()

    def retheme(self):
        for sec, ic, col in self.SECTIONS:
            top = self.tops[sec]
            top.setIcon(0, widgets.icon(ic, col))
            top.setForeground(0, QColor(C["emphasis"]))
        self._style()

    # ── content ────────────────────────────────────────────────
    def _sync(self, sec: str, entries: list[tuple[str, str, str, str, int]]):
        """entries: (key, label, icon, colour, count). Rebuilt only when the key list changes."""
        top = self.tops[sec]
        keys = [e[0] for e in entries]
        if keys != self._keys[sec]:
            top.takeChildren()
            for key, label, ic, col, n in entries:
                it = QTreeWidgetItem([label, ""])
                it.setData(0, ROLE_KEY, key)
                it.setData(0, ROLE_SECTION, sec)
                it.setIcon(0, widgets.icon(ic, col))
                it.setToolTip(0, label)
                it.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
                top.addChild(it)
            self._keys[sec] = keys
            if self.active[sec] not in keys and self.active[sec] != "":
                self.active[sec] = keys[0] if sec == "status" else ""
                self.changed.emit(sec, self.active[sec])
        for i, (key, label, ic, col, n) in enumerate(entries):
            it = top.child(i)
            if it.text(0) != label:
                it.setText(0, label)
            if it.text(1) != str(n):
                it.setText(1, str(n))
        self._style(sec)

    def refresh(self, rows, categories: list[str], tags: list[str]):
        st = [(k, t("sb_" + k), ic, col, sum(1 for r in rows if fn(r)))
              for k, (ic, col, fn) in SIDEBAR_STATES.items()]
        # hide the rare states while nothing is in them (qBittorrent shows them always — too long)
        st = [e for e in st if e[4] or e[0] in ("all", "downloading", "seeding", "completed", "running",
                                                 "stopped", "active", "inactive", "error")
              or e[0] == self.active["status"]]
        self._sync("status", st)

        used_cats = {r.category for r in rows if r.category}
        cats = list(dict.fromkeys(list(categories) + sorted(used_cats)))
        self._sync("category", [("", t("sb_all"), "fa6s.layer-group", "primary", len(rows)),
                                (NO_CATEGORY, t("cat_none"), "fa6s.tag", "secondary",
                                 sum(1 for r in rows if not r.category))]
                   + [(c, c, "fa6s.tag", "purple", sum(1 for r in rows if r.category == c)) for c in cats])

        used_tags = {g for r in rows for g in r.tags}
        tg = list(dict.fromkeys(sorted(set(tags) | used_tags, key=str.lower)))
        self._sync("tag", [("", t("sb_all"), "fa6s.layer-group", "primary", len(rows)),
                           (NO_TAG, t("tags_none"), "fa6s.hashtag", "secondary", sum(1 for r in rows if not r.tags))]
                   + [(g, g, "fa6s.hashtag", "teal", sum(1 for r in rows if g in r.tags)) for g in tg])

        hosts: dict[str, int] = {}
        for r in rows:
            hosts[r.tracker_host] = hosts.get(r.tracker_host, 0) + 1
        bad = sum(1 for r in rows if r.state == "tracker_error")
        trs = [("", t("sb_all"), "fa6s.layer-group", "primary", len(rows))]
        if bad or self.active["tracker"] == TRACKER_ERR:
            trs.append((TRACKER_ERR, t("sb_tr_error"), "fa6s.circle-exclamation", "danger", bad))
        for h in sorted(hosts, key=lambda x: (x == "DHT", x.lower())):
            trs.append((h, t("sb_no_tracker") if h == "DHT" else h,
                        "fa6s.diagram-project" if h == "DHT" else "fa6s.globe", "info", hosts[h]))
        self._sync("tracker", trs)

    def _style(self, only: str | None = None):
        for sec, top in self.tops.items():
            if only and sec != only:
                continue
            for i in range(top.childCount()):
                it = top.child(i)
                on = it.data(0, ROLE_KEY) == self.active[sec]
                bg = QColor(C["primary_subtle"]) if on else QColor(0, 0, 0, 0)
                f = QFont(); f.setWeight(QFont.DemiBold if on else QFont.Normal)
                for c in (0, 1):
                    it.setBackground(c, bg)
                    it.setFont(c, f)
                fg = C["primary_text"] if on else C["body"]
                it.setForeground(0, QColor(fg))
                it.setForeground(1, QColor(C["primary_text"] if on else C["secondary"]))

    def _clicked(self, it: QTreeWidgetItem, _col: int):
        sec = it.data(0, ROLE_SECTION)
        if it.parent() is None:
            it.setExpanded(not it.isExpanded())
            return
        key = it.data(0, ROLE_KEY)
        if self.active.get(sec) == key:
            return
        self.active[sec] = key
        self._style(sec)
        self.changed.emit(sec, key)

    def reset(self):
        self.active = {"status": "all", "category": "", "tag": "", "tracker": ""}
        self._style()

    def _menu(self, pos):
        it = self.itemAt(pos)
        m = QMenu(self)
        if it is not None and it.parent() is not None:
            sec, key = it.data(0, ROLE_SECTION), it.data(0, ROLE_KEY)
            m.addSection(it.text(0))
            m.addAction(widgets.icon("fa6s.play", "success"), t("sb_m_start"), lambda: self.action.emit("start", sec, key))
            m.addAction(widgets.icon("fa6s.pause", "warning"), t("sb_m_pause"), lambda: self.action.emit("pause", sec, key))
            m.addAction(widgets.icon("fa6s.stop", "danger"), t("sb_m_stop"), lambda: self.action.emit("stop", sec, key))
            if sec == "category":
                m.addSeparator()
                m.addAction(widgets.icon("fa6s.plus", "purple"), t("cat_new"), lambda: self.action.emit("cat_new", sec, key))
                if key not in ("", NO_CATEGORY):
                    m.addAction(widgets.icon("fa6s.trash-can", "danger"), t("sb_m_cat_del"),
                                lambda: self.action.emit("cat_del", sec, key))
            if sec == "tag" and key not in ("", NO_TAG):
                m.addSeparator()
                m.addAction(widgets.icon("fa6s.trash-can", "danger"), t("sb_m_tag_del"),
                            lambda: self.action.emit("tag_del", sec, key))
            m.addSeparator()
        m.addAction(widgets.icon("fa6s.table-columns"), t("sb_hide") + "\tCtrl+B", lambda: self.action.emit("hide", "", ""))
        m.exec(self.viewport().mapToGlobal(pos))


def sidebar_match(r, active: dict) -> bool:
    """Used by FilterProxy while the sidebar is on."""
    fn = SIDEBAR_STATES.get(active["status"], SIDEBAR_STATES["all"])[2]
    if not fn(r):
        return False
    cat = active["category"]
    if cat and r.category != ("" if cat == NO_CATEGORY else cat):
        return False
    tag = active["tag"]
    if tag and not (not r.tags if tag == NO_TAG else tag in r.tags):
        return False
    tr = active["tracker"]
    if tr == TRACKER_ERR:
        return r.state == "tracker_error"
    return not tr or r.tracker_host == tr


# ============================================================
#  Move dialog
# ============================================================

class MoveDialog(QDialog):
    """Move the data of the selected torrents: target folder with recent folders, the disk strip,
    and what to do with files that are already there."""
    def __init__(self, cfg: dict, rows: list, parent=None):
        super().__init__(parent)
        _theme_title_bar(self)
        self.cfg = cfg
        self.setWindowTitle(t("mv_title"))
        self.setWindowIcon(app_icon())
        self.setMinimumWidth(620)
        self.need = sum(r.done for r in rows)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 14)
        lay.setSpacing(10)

        lay.addWidget(_section(t("mv_what"), "fa6s.truck-moving", "info"))
        if len(rows) == 1:
            what = QLabel(f"<b>{_esc(rows[0].name)}</b><br>{t('mv_now')}: {_esc(rows[0].save_path)}")
        else:
            names = "<br>".join("• " + _esc(r.name[:80]) for r in rows[:5])
            if len(rows) > 5:
                names += "<br>" + t("mv_more", len(rows) - 5)
            what = QLabel(t("mv_n", len(rows)) + "<br>" + names)
        what.setTextFormat(Qt.RichText)
        what.setWordWrap(True)
        lay.addWidget(what)
        size = QLabel(t("mv_size", fmt_size(self.need)))
        size.setObjectName("meta")
        lay.addWidget(size)

        lay.addWidget(_section(t("mv_to"), "fa6s.folder-open", "warning"))
        row = QHBoxLayout()
        cur = rows[0].save_path if rows else cfg.get("save_path", "")
        self.path = PathCombo(cfg, cur)
        self.path.setEditText("")
        self.path.lineEdit().setPlaceholderText(t("mv_ph"))
        self.path.textChanged.connect(self._update)
        row.addWidget(self.path, 1)
        b = QPushButton(icon("fa6s.folder-open", "warning"), t("set_browse"))
        b.clicked.connect(self._browse)
        row.addWidget(b)
        lay.addLayout(row)
        self.disk = DiskBar()
        lay.addWidget(self.disk)

        lay.addWidget(_section(t("mv_exist"), "fa6s.copy", "orange"))
        self.r_replace = QRadioButton(t("mv_replace"))
        self.r_keep = QRadioButton(t("mv_keep"))
        self.r_replace.setChecked(True)
        grp = QButtonGroup(self)
        grp.addButton(self.r_replace); grp.addButton(self.r_keep)
        lay.addWidget(self.r_replace)
        lay.addWidget(self.r_keep)
        note = QLabel(t("mv_note"))
        note.setObjectName("meta")
        note.setWordWrap(True)
        lay.addWidget(note)

        btns = QHBoxLayout()
        btns.addStretch()
        self.ok = QPushButton(icon("fa6s.truck-moving", "#ffffff"), t("mv_go"))
        self.ok.setObjectName("primary")
        self.ok.setDefault(True)
        self.ok.clicked.connect(self.accept)
        cancel = QPushButton(t("cancel"))
        cancel.clicked.connect(self.reject)
        btns.addWidget(cancel)
        btns.addWidget(self.ok)
        lay.addLayout(btns)
        self._update()

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, t("mv_to"), self.path.text() or self.cfg.get("save_path", ""))
        if d:
            self.path.setText(os.path.normpath(d))

    def _update(self, *_):
        p = self.folder()
        probe = p
        while probe and not os.path.exists(probe):          # a folder that will be created
            parent = os.path.dirname(probe)
            probe = "" if parent == probe else parent
        try:
            du = shutil.disk_usage(probe) if probe else None
        except OSError:
            du = None
        self.disk.set_values(du.total if du else 0, du.free if du else 0, self.need)
        self.ok.setEnabled(bool(p))

    def folder(self) -> str:
        p = self.path.text().strip().strip('"')
        return os.path.normpath(p) if p else ""

    def keep_existing(self) -> bool:
        return self.r_keep.isChecked()


def _esc(s: str) -> str:
    import html
    return html.escape(s or "")


# ============================================================
#  Statistics window
# ============================================================

class TrafficChart(QWidget):
    """Downloaded / uploaded per day (or per month) as paired bars on one axis, hover tooltip."""
    def __init__(self):
        super().__init__()
        self.setMinimumHeight(230)
        self.setMouseTracking(True)
        self.buckets: list[tuple[str, str, int, int]] = []    # (axis label, tooltip title, down, up)
        self.hover = -1

    def set_data(self, buckets):
        self.buckets = buckets
        self.update()

    def _geom(self):
        fm = QFontMetrics(self.font())
        left = fm.horizontalAdvance("999.9 MB") + 14
        plot = QRectF(left, 30, self.width() - left - 10, self.height() - 30 - 24)
        return plot

    def _max(self) -> int:
        top = max([max(d, u) for _, _, d, u in self.buckets] + [1])
        # a round axis maximum: 1, 2 or 5 × 10^n bytes in the unit that fits
        unit = 1024 ** max(0, min(4, (len(str(int(top))) - 1) // 3))
        v = top / unit
        mag = 10 ** (len(str(int(v))) - 1) if v >= 1 else 1
        for step in (1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10):
            if v <= step * mag:
                return int(step * mag * unit)
        return int(top)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        plot = self._geom()
        top = self._max()
        # legend (two series → always a legend; identity is never colour alone)
        x = plot.left()
        for key, name in (("primary", t("sw_down")), ("success", t("sw_up"))):
            p.setBrush(QColor(C[key])); p.setPen(Qt.NoPen)
            p.drawRoundedRect(QRectF(x, 9, 10, 10), 3, 3)
            p.setPen(QColor(C["body"]))
            p.drawText(QPointF(x + 15, 18), name)
            x += 15 + QFontMetrics(self.font()).horizontalAdvance(name) + 18
        # recessive grid + axis labels
        grid = QColor(C["border"]); grid.setAlpha(140)
        for i in range(5):
            y = plot.bottom() - plot.height() * i / 4
            p.setPen(QPen(grid, 1, Qt.SolidLine if i == 0 else Qt.DotLine))
            p.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            p.setPen(QColor(C["secondary"]))
            p.drawText(QRectF(0, y - 8, plot.left() - 8, 16), Qt.AlignRight | Qt.AlignVCenter,
                       fmt_size(top * i / 4) if i else "0")
        n = len(self.buckets)
        if not n:
            p.end()
            return
        slot = plot.width() / n
        bw = max(1.0, min(14.0, (slot - 6) / 2))
        label_every = max(1, int(n / max(1, plot.width() / 46)) + (1 if n > 12 else 0))
        for i, (lab, _title, d, u) in enumerate(self.buckets):
            cx = plot.left() + slot * (i + 0.5)
            if i == self.hover:
                hl = QColor(C["primary"]); hl.setAlpha(22)
                p.fillRect(QRectF(plot.left() + slot * i, plot.top(), slot, plot.height()), hl)
            for j, (val, key) in enumerate(((d, "primary"), (u, "success"))):
                h = plot.height() * val / top if top else 0
                if h <= 0:
                    continue
                h = max(h, 2)
                x0 = cx - bw - 1 if j == 0 else cx + 1         # 2px gap between the pair
                rad = min(4.0, bw / 2)
                p.save()                      # rounded data end, square foot on the baseline
                p.setClipRect(QRectF(x0 - 1, plot.top() - 1, bw + 2, plot.bottom() - plot.top() + 1))
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(C[key]))
                p.drawRoundedRect(QRectF(x0, plot.bottom() - h, bw, h + rad), rad, rad)
                p.restore()
            if i % label_every == 0 or i == n - 1 and n <= 12:
                p.setPen(QColor(C["secondary"]))
                p.drawText(QRectF(cx - 30, plot.bottom() + 4, 60, 18), Qt.AlignHCenter | Qt.AlignTop, lab)
        p.end()

    def mouseMoveEvent(self, e):
        plot = self._geom()
        n = len(self.buckets)
        i = int((e.position().x() - plot.left()) / (plot.width() / n)) if n and plot.width() > 0 else -1
        if not (0 <= i < n) or not plot.adjusted(0, -30, 0, 24).contains(e.position()):
            i = -1
        if i != self.hover:
            self.hover = i
            self.update()
            if i >= 0:
                _lab, title, d, u = self.buckets[i]
                QToolTip.showText(e.globalPosition().toPoint(),
                                  f"<b>{title}</b><br>↓ {t('sw_down')}: {fmt_size(d)}<br>↑ {t('sw_up')}: {fmt_size(u)}"
                                  + (f"<br>{t('sw_ratio')}: {u / d:.2f}" if d else ""), self)
            else:
                QToolTip.hideText()

    def leaveEvent(self, _):
        self.hover = -1
        self.update()


class StatsDialog(QDialog):
    """All-time statistics: totals, records, traffic per day / month, per tracker."""
    PERIODS = (("7", 7), ("30", 30), ("90", 90), ("year", 365))

    def __init__(self, win, parent=None):
        super().__init__(parent)
        _theme_title_bar(self)
        self.win = win
        self.setWindowTitle(t("sw_title"))
        self.setWindowIcon(app_icon())
        self.resize(880, 720)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 14)
        lay.setSpacing(10)

        self.tiles: dict[str, tuple[QLabel, QLabel]] = {}
        grid = QGridLayout()
        grid.setSpacing(10)
        specs = [("all_down", "fa6s.arrow-down", "primary"), ("all_up", "fa6s.arrow-up", "success"),
                 ("ratio", "fa6s.scale-balanced", "purple"), ("uptime", "fa6s.stopwatch", "teal"),
                 ("added", "fa6s.plus", "info"), ("completed", "fa6s.circle-check", "success"),
                 ("peak_down", "fa6s.gauge-high", "primary"), ("peak_up", "fa6s.gauge-high", "success"),
                 ("sessions", "fa6s.power-off", "secondary"), ("since", "fa6s.calendar", "orange"),
                 ("wasted", "fa6s.recycle", "danger"), ("today", "fa6s.calendar-day", "info")]
        for i, (key, ic, col) in enumerate(specs):
            card = QFrame(); card.setObjectName("card")
            cl = QHBoxLayout(card); cl.setContentsMargins(12, 9, 12, 9); cl.setSpacing(10)
            icl = QLabel(); icl.setPixmap(widgets.icon(ic, col).pixmap(18, 18)); icl.setFixedWidth(22)
            cl.addWidget(icl)
            col_l = QVBoxLayout(); col_l.setSpacing(0)
            val = QLabel("—")
            val.setStyleSheet(f'font-family:"{theme.TITLE_FONT}"; font-size: 14pt; font-weight: 600; color:{C["emphasis"]};')
            val.setTextInteractionFlags(Qt.TextSelectableByMouse)
            lab = QLabel(t("sw_" + key)); lab.setObjectName("meta")
            col_l.addWidget(val); col_l.addWidget(lab)
            cl.addLayout(col_l, 1)
            grid.addWidget(card, i // 4, i % 4)
            self.tiles[key] = (val, lab)
        lay.addLayout(grid)

        head = QHBoxLayout()
        head.addWidget(_section(t("sw_traffic"), "fa6s.chart-column", "primary"))
        head.addStretch()
        self.period = QComboBox()
        for key, _n in self.PERIODS:
            self.period.addItem(t("sw_p_" + key), key)
        self.period.setCurrentIndex(1)
        self.period.currentIndexChanged.connect(self.refresh)
        head.addWidget(self.period)
        lay.addLayout(head)
        self.chart = TrafficChart()
        lay.addWidget(self.chart, 1)
        self.period_sum = QLabel(); self.period_sum.setObjectName("meta")
        lay.addWidget(self.period_sum)

        lay.addWidget(_section(t("sg_trackers"), "fa6s.server", "info"))
        self.trk = QTableWidget(0, 5)
        self.trk.setHorizontalHeaderLabels([t("sw_t_host"), t("sw_t_n"), t("sw_up"), t("sw_down"), t("sw_ratio")])
        self.trk.verticalHeader().hide()
        self.trk.setShowGrid(False)
        self.trk.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.trk.setSelectionMode(QAbstractItemView.NoSelection)
        self.trk.setFocusPolicy(Qt.NoFocus)
        self.trk.verticalHeader().setDefaultSectionSize(26)
        hh = self.trk.horizontalHeader()
        hh.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        for c in range(1, 5):
            hh.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        self.trk.setMinimumHeight(120)
        self.trk.setMaximumHeight(170)
        lay.addWidget(self.trk)

        btns = QHBoxLayout()
        reset = QPushButton(icon("fa6s.trash-can", "danger"), t("sw_reset"))
        reset.clicked.connect(self._reset)
        cp = QPushButton(icon("fa6s.copy"), t("sw_copy"))
        cp.clicked.connect(self._copy)
        close = QPushButton(t("close")); close.setObjectName("primary"); close.setDefault(True)
        close.clicked.connect(self.close)
        btns.addWidget(reset); btns.addWidget(cp); btns.addStretch(); btns.addWidget(close)
        lay.addLayout(btns)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(2000)
        self.refresh()

    def _values(self) -> dict:
        st = self.win.engine.stats()
        dl, ul = st["all_down"], st["all_up"]
        today = st["days"].get(date.today().isoformat(), [0, 0])
        return {
            "all_down": fmt_size(dl), "all_up": fmt_size(ul),
            "ratio": f"{ul / dl:.3f}" if dl else ("∞" if ul else "—"),
            "uptime": _fmt_uptime(st["all_uptime"]),
            "added": str(st["added"]), "completed": str(st["completed"]),
            "peak_down": fmt_size(st["peak_down"]) + "/s", "peak_up": fmt_size(st["peak_up"]) + "/s",
            "sessions": str(st["sessions"]),
            "since": datetime.fromtimestamp(st["first_run"]).strftime("%d.%m.%Y"),
            "wasted": fmt_size(st["wasted"]),
            "today": f"↓ {fmt_size(today[0])}  ↑ {fmt_size(today[1])}",
        }

    def refresh(self, *_):
        vals = self._values()
        for k, (val, _lab) in self.tiles.items():
            val.setText(vals[k])
        days = self.win.engine.stats()["days"]
        key = self.period.currentData()
        today = date.today()
        buckets = []
        if key == "year":                      # 12 months
            y, m = today.year, today.month
            months = []
            for _ in range(12):
                months.append((y, m))
                m -= 1
                if m == 0:
                    y, m = y - 1, 12
            for y, m in reversed(months):
                pre = f"{y:04d}-{m:02d}-"
                d = sum(v[0] for k2, v in days.items() if k2.startswith(pre))
                u = sum(v[1] for k2, v in days.items() if k2.startswith(pre))
                lab = date(y, m, 1).strftime("%m.%y")
                buckets.append((lab, lab, d, u))
        else:
            n = int(key)
            for i in range(n - 1, -1, -1):
                d0 = today - timedelta(days=i)
                v = days.get(d0.isoformat(), [0, 0])
                buckets.append((d0.strftime("%d.%m"), d0.strftime("%d.%m.%Y"), v[0], v[1]))
        self.chart.set_data(buckets)
        sd, su = sum(b[2] for b in buckets), sum(b[3] for b in buckets)
        self.period_sum.setText(t("sw_period_sum", fmt_size(sd), fmt_size(su)))

        rows = self.win.engine.tracker_totals()
        if self.trk.rowCount() != len(rows):
            self.trk.setRowCount(len(rows))
        for r, (host, up, down, n) in enumerate(rows):
            vals = [host, str(n), fmt_size(up), fmt_size(down),
                    f"{up / down:.2f}" if down else ("∞" if up else "—")]
            for c, v in enumerate(vals):
                it = self.trk.item(r, c)
                if it is None:
                    it = QTableWidgetItem()
                    self.trk.setItem(r, c, it)
                    if c == 0:
                        it.setIcon(widgets.icon("fa6s.globe", "info"))
                it.setText(v)

    def _reset(self):
        if QMessageBox.question(self, t("sw_title"), t("sw_reset_q")) == QMessageBox.Yes:
            self.win.engine.reset_stats()
            self.refresh()

    def _copy(self):
        vals = self._values()
        lines = [f"{APP_TITLE} — {t('sw_title')}"] + [f"{t('sw_' + k)}: {v}" for k, v in vals.items()]
        QGuiApplication.clipboard().setText("\n".join(lines))

    def closeEvent(self, e):
        self.timer.stop()
        super().closeEvent(e)


# ============================================================
#  Keyboard shortcuts
# ============================================================

SHORTCUTS = [
    ("kb_g_add", [("Ctrl+O", "add_torrent"), ("Ctrl+U", "add_magnet"), ("Ctrl+V", "kb_paste"),
                  ("Ctrl+N", "create_torrent"), ("kb_drop", "kb_drop_t")]),
    ("kb_g_torrent", [("Ctrl+S", "start"), ("Ctrl+P", "pause"), ("Ctrl+Shift+S", "stop"),
                      ("Ctrl+Shift+F", "force_start"), ("Delete", "remove"), ("Shift+Delete", "kb_remove_files"),
                      ("Ctrl+R", "recheck"), ("F5", "reannounce"), ("Ctrl+Shift+M", "move_to"),
                      ("Ctrl+M", "copy_magnet"), ("Ctrl+E", "exp_menu"), ("Enter", "open_folder"), ("Alt+Enter", "properties")]),
    ("kb_g_queue", [("Ctrl+Shift+Up", "queue_up"), ("Ctrl+Shift+Down", "queue_down"),
                    ("Ctrl+Shift+Home", "queue_top"), ("Ctrl+Shift+End", "queue_bottom")]),
    ("kb_g_view", [("Ctrl+A", "select_all"), ("Ctrl+F", "kb_search"), ("Esc", "kb_esc"),
                   ("Ctrl+B", "kb_sidebar"), ("Ctrl+Shift+L", "compact_menu"), ("Ctrl+I", "sw_title"), ("Ctrl+H", "kb_history"),
                   ("Ctrl+,", "settings"), ("F1", "about"), ("Ctrl+/", "kb_title"), ("Ctrl+Q", "quit")]),
]


class ShortcutsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        _theme_title_bar(self)
        self.setWindowTitle(t("kb_title"))
        self.setWindowIcon(app_icon())
        self.setMinimumWidth(800)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 14)
        cols = QHBoxLayout()
        cols.setSpacing(28)
        halves = (SHORTCUTS[:2], SHORTCUTS[2:])
        for half in halves:
            col = QVBoxLayout()
            col.setSpacing(4)
            for title, items in half:
                col.addWidget(_section(t(title), {"kb_g_add": "fa6s.plus", "kb_g_torrent": "fa6s.file",
                                                  "kb_g_queue": "fa6s.list-ol", "kb_g_view": "fa6s.eye"}[title]))
                g = QGridLayout()
                g.setHorizontalSpacing(14)
                g.setVerticalSpacing(3)
                for r, (keys, what) in enumerate(items):
                    k = QLabel(t(keys) if keys.startswith("kb_") else keys)
                    k.setStyleSheet(f"font-family: Consolas, monospace; font-weight: 600; padding: 1px 6px;"
                                    f"border: 1px solid {C['border']}; border-radius: 4px; background:{C['tertiary_bg']};")
                    g.addWidget(k, r, 0, Qt.AlignLeft)
                    d = QLabel(t(what)); d.setWordWrap(True)
                    g.addWidget(d, r, 1)
                g.setColumnStretch(1, 1)
                col.addLayout(g)
                col.addSpacing(6)
            col.addStretch()
            cols.addLayout(col, 1)
        lay.addLayout(cols)
        btns = QHBoxLayout()
        btns.addStretch()
        close = QPushButton(t("close")); close.setObjectName("primary"); close.setDefault(True)
        close.clicked.connect(self.accept)
        btns.addWidget(close)
        lay.addLayout(btns)


# ============================================================
#  Items from drag & drop / clipboard, .torrent links
# ============================================================

def items_from_text(text: str) -> list[str]:
    """Magnet links, http(s) links and .torrent paths in pasted / dropped text (one per line)."""
    out = []
    for line in (text or "").replace("\r", "\n").split("\n"):
        s = line.strip().strip('"')
        if not s:
            continue
        low = s.lower()
        if low.startswith("magnet:?"):
            out.append(s)
        elif low.startswith(("http://", "https://")):
            out.append(s)
        elif low.startswith("file:///"):
            from PySide6.QtCore import QUrl
            p = QUrl(s).toLocalFile()
            out += torrents_in(p)
        elif os.path.exists(s):
            out += torrents_in(s)
        elif len(s) in (40, 64) and all(ch in "0123456789abcdefABCDEF" for ch in s):
            out.append("magnet:?xt=urn:btih:" + s if len(s) == 40 else "magnet:?xt=urn:btmh:1220" + s)
    return list(dict.fromkeys(out))


def torrents_in(path: str, limit: int = 200) -> list[str]:
    """A .torrent file itself, or the .torrent files in a folder (and its subfolders, 2 levels)."""
    if os.path.isfile(path):
        return [path] if path.lower().endswith(".torrent") else []
    out = []
    base = path.rstrip("\\/").count(os.sep)
    for root, dirs, files in os.walk(path):
        if root.count(os.sep) - base >= 2:
            dirs[:] = []
        for f in sorted(files):
            if f.lower().endswith(".torrent"):
                out.append(os.path.join(root, f))
                if len(out) >= limit:
                    return out
    return out


class TorrentFetcher(QThread):
    """Downloads a .torrent from a link (dropped from a browser or pasted) to a temp file."""
    done = Signal(str, str, str)     # url, temp path, error

    MAX = 20 << 20

    def __init__(self, url: str, parent=None):
        super().__init__(parent)
        self.url = url

    def run(self):
        import urllib.request
        from . import VERSION
        try:
            req = urllib.request.Request(self.url, headers={"User-Agent": f"ArtCoreClient/{VERSION}"})
            with urllib.request.urlopen(req, timeout=30) as r:
                final = r.geturl()
                if final.lower().startswith("magnet:"):
                    self.done.emit(self.url, final, "")
                    return
                data = r.read(self.MAX + 1)
            if len(data) > self.MAX:
                raise ValueError(t("dl_too_big"))
            if not data.startswith(b"d"):             # bencoded dictionary
                raise ValueError(t("dl_not_torrent"))
            fd, path = tempfile.mkstemp(suffix=".torrent", prefix="artcore_")
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            self.done.emit(self.url, path, "")
        except Exception as e:
            self.done.emit(self.url, "", str(e))


# ============================================================
#  Restore from backup
# ============================================================

class RestoreDialog(QDialog):
    """What is in the archive + where the torrents' folders are on this PC (old → new)."""
    def __init__(self, manifest: dict, parent=None):
        super().__init__(parent)
        _theme_title_bar(self)
        self.setWindowTitle(t("bk_restore_title"))
        self.setWindowIcon(app_icon())
        self.setMinimumWidth(760)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 14)
        lay.setSpacing(10)
        lay.addWidget(_section(t("bk_in_archive"), "fa6s.box-archive", "orange"))
        when = datetime.fromtimestamp(manifest.get("created", 0)).strftime("%d.%m.%Y %H:%M")
        info = QLabel(t("bk_info", when, manifest.get("version", "?"), manifest.get("torrents", 0),
                        manifest.get("pc", "—")))
        info.setWordWrap(True)
        lay.addWidget(info)

        lay.addWidget(_section(t("bk_folders"), "fa6s.folder-tree", "warning"))
        hint = QLabel(t("bk_folders_hint")); hint.setObjectName("meta"); hint.setWordWrap(True)
        lay.addWidget(hint)
        folders = sorted(manifest.get("folders", {}).items(), key=lambda kv: -kv[1])
        dflt = manifest.get("save_path", "")
        if dflt and dflt not in dict(folders):
            folders.append((dflt, 0))
        self.tbl = QTableWidget(len(folders), 3)
        self.tbl.setHorizontalHeaderLabels([t("bk_old"), t("bk_n"), t("bk_new")])
        self.tbl.verticalHeader().hide()
        self.tbl.setShowGrid(False)
        self.tbl.setSelectionMode(QAbstractItemView.NoSelection)
        self.tbl.verticalHeader().setDefaultSectionSize(34)
        hh = self.tbl.horizontalHeader()
        hh.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        hh.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.Stretch)
        self.edits = []
        for r, (old, n) in enumerate(folders):
            it = QTableWidgetItem(old)
            it.setFlags(Qt.ItemIsEnabled)
            it.setToolTip(old)
            self.tbl.setItem(r, 0, it)
            c = QTableWidgetItem(str(n) if n else "—")
            c.setFlags(Qt.ItemIsEnabled)
            self.tbl.setItem(r, 1, c)
            w = QWidget()
            hl = QHBoxLayout(w); hl.setContentsMargins(4, 2, 4, 2); hl.setSpacing(4)
            from PySide6.QtWidgets import QLineEdit
            e = QLineEdit(old)
            ok = QLabel()
            b = QPushButton(icon("fa6s.folder-open", "warning"), ""); b.setFixedWidth(38)
            b.setToolTip(t("set_browse"))
            b.clicked.connect(lambda _=False, e=e: self._browse(e))
            e.textChanged.connect(lambda _=None, e=e, ok=ok: self._mark(e, ok))
            hl.addWidget(ok); hl.addWidget(e, 1); hl.addWidget(b)
            self.tbl.setCellWidget(r, 2, w)
            self.edits.append((old, e))
            self._mark(e, ok)
        self.tbl.setMinimumHeight(min(260, 40 + 34 * max(1, len(folders))))
        lay.addWidget(self.tbl)

        warn = QLabel(t("bk_replace_note")); warn.setWordWrap(True); warn.setObjectName("meta")
        lay.addWidget(warn)
        btns = QHBoxLayout(); btns.addStretch()
        cancel = QPushButton(t("cancel")); cancel.clicked.connect(self.reject)
        ok = QPushButton(icon("fa6s.rotate-left", "#ffffff"), t("bk_restore_go"))
        ok.setObjectName("primary"); ok.setDefault(True); ok.clicked.connect(self.accept)
        btns.addWidget(cancel); btns.addWidget(ok)
        lay.addLayout(btns)

    def _browse(self, e):
        d = QFileDialog.getExistingDirectory(self, t("bk_new"), e.text() if os.path.isdir(e.text()) else "")
        if d:
            e.setText(os.path.normpath(d))

    @staticmethod
    def _mark(e, lab):
        there = os.path.isdir(e.text().strip())
        lab.setPixmap(widgets.icon("fa6s.circle-check" if there else "fa6s.circle-xmark",
                                   "success" if there else "danger").pixmap(14, 14))
        lab.setToolTip(t("bk_exists") if there else t("bk_not_exists"))

    def mapping(self) -> list[tuple[str, str]]:
        return [(old, e.text().strip()) for old, e in self.edits if e.text().strip() and e.text().strip() != old]


# ============================================================
#  Batch add (like BitComet's "Batch download")
# ============================================================

class BatchAddDialog(QDialog):
    """Several .torrent files / magnets at once: a checklist, one save folder, Download now /
    Download later. Torrents whose files are already in the folder are marked — if they are
    downloaded anyway, their data is hash-checked first and only the rest is downloaded."""
    def __init__(self, cfg: dict, items: list[str], known_keys: set[str], used_paths: list[str] = (), parent=None):
        super().__init__(parent)
        import libtorrent as lt
        from .engine import _key_of
        from .filetypes import torrent_kind
        _theme_title_bar(self)
        self.cfg = cfg
        self.setWindowTitle(t("ba_title"))
        self.setWindowIcon(app_icon())
        self.resize(860, 600)
        self.paused = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 14, 18, 12)
        lay.setSpacing(8)
        lay.addWidget(_section(t("ba_list"), "fa6s.list-check", "primary"))

        self.tree = QTreeWidget()
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels([t("col_name"), t("col_size"), t("ba_disk"), t("ba_src")])
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(True)
        self.tree.setAlternatingRowColors(False)
        hh = self.tree.header()
        hh.setStretchLastSection(True)
        hh.setSectionResizeMode(0, QHeaderView.Interactive)
        hh.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.tree.setColumnWidth(0, 300)
        self.entries: list[dict] = []
        seen: set[str] = set()
        for it in items:
            e = {"item": it, "ok": True, "note": "", "size": 0, "ti": None, "name": ""}
            try:
                if it.startswith("magnet:"):
                    atp = lt.parse_magnet_uri(it)
                    key = _key_of(atp.info_hashes)
                    e["name"] = atp.name or it[:60] + "…"
                    kind = "magnet"
                else:
                    ti = lt.torrent_info(it)
                    key = _key_of(ti.info_hashes())
                    fs = ti.files()
                    e["ti"], e["name"], e["size"] = ti, ti.name(), ti.total_size()
                    kind = torrent_kind([(fs.file_path(i), fs.file_size(i)) for i in range(min(fs.num_files(), 2000))])
                if key in known_keys:
                    e["ok"], e["note"] = False, t("ba_have")
                elif key in seen:
                    e["ok"], e["note"] = False, t("ba_dup")
                seen.add(key)
            except Exception as ex:
                e["ok"], e["note"], e["bad"] = False, t("ba_bad", ex), True
                e["name"] = os.path.basename(it)
                kind = "file"
            row = QTreeWidgetItem([e["name"], fmt_size(e["size"]) if e["size"] else "—", "",
                                   it if not e["note"] else f"{e['note']} — {it}"])
            row.setIcon(0, widgets.torrent_icon(kind))
            row.setToolTip(0, e["name"])
            row.setToolTip(3, it)
            row.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
            if e["ok"]:
                row.setFlags(row.flags() | Qt.ItemIsUserCheckable)
                row.setCheckState(0, Qt.Checked)
            else:
                row.setFlags(Qt.ItemIsEnabled)
                row.setCheckState(0, Qt.Unchecked)
                for c in range(4):
                    row.setForeground(c, QColor(C["secondary"]))
                if e.get("bad"):
                    row.setForeground(3, QColor(C["danger"]))
            self.tree.addTopLevelItem(row)
            e["row"] = row
            self.entries.append(e)
        self.tree.itemChanged.connect(lambda *_: self._update())
        lay.addWidget(self.tree, 1)
        self.exist_note = QLabel()
        self.exist_note.setWordWrap(True)
        self.exist_note.hide()
        lay.addWidget(self.exist_note)

        from PySide6.QtWidgets import QCheckBox
        self.all_cb = QCheckBox()
        self.all_cb.setTristate(False)
        self.all_cb.clicked.connect(self._toggle_all)
        lay.addWidget(self.all_cb)

        lay.addWidget(_section(t("ba_folder"), "fa6s.folder-open", "warning"))
        row = QHBoxLayout()
        row.addWidget(QLabel(t("ba_save")))
        self.path = PathCombo(cfg, cfg.get("save_path", ""), list(used_paths))
        self.path.textChanged.connect(lambda *_: self._update())
        row.addWidget(self.path, 1)
        b = QPushButton(icon("fa6s.folder-open", "warning"), "")
        b.setFixedWidth(40)
        b.setToolTip(t("set_browse"))
        b.clicked.connect(self._browse)
        row.addWidget(b)
        lay.addLayout(row)
        self.disk = DiskBar()
        lay.addWidget(self.disk)
        self._exist_timer = QTimer(self)
        self._exist_timer.setSingleShot(True)
        self._exist_timer.setInterval(300)
        self._exist_timer.timeout.connect(self._scan_existing)
        self.path.textChanged.connect(lambda *_: self._exist_timer.start())

        btns = QHBoxLayout()
        btns.addStretch()
        self.now = QPushButton(icon("fa6s.download", "#ffffff"), t("ba_now"))
        self.now.setObjectName("primary")
        self.now.setDefault(True)
        self.now.clicked.connect(lambda: self._done(False))
        self.later = QPushButton(icon("fa6s.clock", "secondary"), t("ba_later"))
        self.later.setToolTip(t("ba_later_tip"))
        self.later.clicked.connect(lambda: self._done(True))
        cancel = QPushButton(t("cancel"))
        cancel.clicked.connect(self.reject)
        btns.addWidget(self.now); btns.addWidget(self.later); btns.addWidget(cancel)
        lay.addLayout(btns)
        self._scan_existing()
        self._update()

    def _scan_existing(self):
        """Which torrents already have files in the chosen folder."""
        from .engine import Engine
        folder = self.folder()
        warn = C["warning_text"] if theme.current_name == "dark" else C["orange"]
        n = 0
        for e in self.entries:
            row = e["row"]
            e["exists"] = None
            if e["ti"] is not None and folder and os.path.isdir(folder):
                try:
                    have, total, got, full = Engine.existing_files(e["ti"], folder)
                except Exception:
                    have = 0
                if have:
                    e["exists"] = (have, total, got, full)
            ex = e["exists"]
            if ex:
                have, total, got, full = ex
                row.setText(2, t("ba_exists_full") if full else t("ba_exists", have, total, fmt_size(got)))
                row.setToolTip(2, t("ba_exists_tip"))
                row.setIcon(2, widgets.icon("fa6s.circle-check" if full else "fa6s.triangle-exclamation",
                                            "success" if full else "orange"))
                if e["ok"]:
                    row.setForeground(2, QColor(C["success_text"] if full and theme.current_name == "dark"
                                                else C["success"] if full else warn))
                    n += 1
            else:
                row.setText(2, "—" if e["ti"] is not None else "")
                row.setToolTip(2, "")
                row.setIcon(2, QIcon())
                row.setForeground(2, QColor(C["secondary"]))
        if n:
            self.exist_note.setText(f'<span style="color:{warn}">⚠ {t("ba_exists_note", n)}</span>')
            self.exist_note.show()
        else:
            self.exist_note.hide()

    def existing(self, e: dict) -> bool:
        return bool(e.get("exists"))

    def _checked(self) -> list[dict]:
        return [e for e in self.entries if e["ok"] and e["row"].checkState(0) == Qt.Checked]

    def _toggle_all(self, on: bool):
        self.tree.blockSignals(True)
        for e in self.entries:
            if e["ok"]:
                e["row"].setCheckState(0, Qt.Checked if on else Qt.Unchecked)
        self.tree.blockSignals(False)
        self._update()

    def _update(self):
        ok = [e for e in self.entries if e["ok"]]
        sel = self._checked()
        self.all_cb.blockSignals(True)
        self.all_cb.setText(t("ba_all", len(sel), len(ok)) +
                            (f"   ·   {fmt_size(sum(e['size'] for e in sel))}" if sel else ""))
        self.all_cb.setChecked(bool(ok) and len(sel) == len(ok))
        self.all_cb.setEnabled(bool(ok))
        self.all_cb.blockSignals(False)
        p = self.folder()
        probe = p
        while probe and not os.path.exists(probe):
            parent = os.path.dirname(probe)
            probe = "" if parent == probe else parent
        try:
            du = shutil.disk_usage(probe) if probe else None
        except OSError:
            du = None
        self.disk.set_values(du.total if du else 0, du.free if du else 0, sum(e["size"] for e in sel))
        self.now.setEnabled(bool(sel) and bool(p))
        self.later.setEnabled(bool(sel) and bool(p))

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, t("ba_folder"), self.folder() or self.cfg.get("save_path", ""))
        if d:
            self.path.setText(os.path.normpath(d))

    def folder(self) -> str:
        p = self.path.text().strip().strip('"')
        return os.path.normpath(p) if p else ""

    def _done(self, paused: bool):
        self.paused = paused
        self.accept()

    def selected(self) -> list[dict]:
        return self._checked()

