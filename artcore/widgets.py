"""Icons by file type and the Files tab tree."""
from __future__ import annotations

import qtawesome as qta
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter
from PySide6.QtWidgets import (QAbstractItemView, QHeaderView, QStyle, QStyledItemDelegate,
                               QTreeWidget, QTreeWidgetItem)

from . import theme
from .i18n import t

C = theme.current

from .filetypes import KINDS as _KINDS, kind_of  # noqa: F401  (kind_of re-exported)

# torrent-level icons (name column): a bit more "object"-like than the file icons
_TORRENT_ICON = {
    "video": ("fa6s.film", "purple"), "audio": ("fa6s.music", "success"), "image": ("fa6s.image", "teal"),
    "archive": ("fa6s.box-archive", "orange"), "disc": ("fa6s.compact-disc", "info"),
    "book": ("fa6s.book", "danger"), "program": ("fa6s.gamepad", "primary"), "subtitle": ("fa6s.closed-captioning", "warning"),
    "text": ("fa6s.file-lines", "secondary"), "code": ("fa6s.file-code", "info"),
    "folder": ("fa6s.folder", "warning"), "file": ("fa6s.file", "secondary"), "magnet": ("fa6s.magnet", "purple"),
}

_cache: dict[tuple, QIcon] = {}


def clear_cache() -> None:
    _cache.clear()


def _icon(name: str, key: str) -> QIcon:
    k = (name, key, theme.current_name, theme.current_style)
    ic = _cache.get(k)
    if ic is None:
        col = C.get(key, key)
        if theme.current_style == "muted" and key not in ("warning", "danger"):
            col = C["secondary"]                 # muted look: gray icons, folders stay yellow
        ic = qta.icon(name, color=col)
        _cache[k] = ic
    return ic


def file_icon(filename: str) -> QIcon:
    kind = kind_of(filename)
    if kind == "file":
        return _icon("fa6s.file", "secondary")
    name, col, _ = _KINDS[kind]
    return _icon(name, col)


def folder_icon(open_: bool = False) -> QIcon:
    return _icon("fa6s.folder-open" if open_ else "fa6s.folder", "warning")


def torrent_icon(kind: str) -> QIcon:
    name, col = _TORRENT_ICON.get(kind, _TORRENT_ICON["file"])
    return _icon(name, col)


# ---- BitComet-style status arrows for the name column ------------------------------
# (light, dark, outline) of the glossy arrow body
_ARROW_TONES = {
    "green":  ("#8fe36a", "#2e9a1c", "#1d6b10"),
    "orange": ("#ffab2e", "#ff5e00", "#b83a00"),
    "grey":   ("#dcdcdc", "#8c8c8c", "#5f5f5f"),
    "amber":  ("#ffe77a", "#eaa400", "#9c6c00"),
    "blue":   ("#8cd0ff", "#1f7fd8", "#0d4f8c"),
}
_state_cache: dict[tuple, QIcon] = {}


def _arrow_pixmap(size: int, direction: str, tone: str):
    """A thick 3-D block arrow (as BitComet draws its task icons), drawn in a 16-unit box."""
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QLinearGradient, QPainterPath, QPen, QPixmap, QTransform
    light, dark, edge = _ARROW_TONES[tone]
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.scale(size / 16.0, size / 16.0)
    # pointing down; other directions are rotations of it
    path = QPainterPath()
    for i, (x, y) in enumerate(((5.2, 1.0), (10.8, 1.0), (10.8, 7.2), (14.8, 7.2), (8.0, 15.0),
                                (1.2, 7.2), (5.2, 7.2))):
        path.moveTo(x, y) if i == 0 else path.lineTo(x, y)
    path.closeSubpath()
    rot = {"down": 0, "up": 180, "right": -90, "left": 90}[direction]
    if rot:
        path = QTransform().translate(8, 8).rotate(rot).translate(-8, -8).map(path)
    g = QLinearGradient(QPointF(2, 0), QPointF(14, 0))      # light left side, dark right: rounded look
    g.setColorAt(0.0, QColor(light))
    g.setColorAt(0.45, QColor(light).darker(108))
    g.setColorAt(1.0, QColor(dark))
    p.setPen(QPen(QColor(edge), 1.0))
    p.setBrush(g)
    p.drawPath(path)
    gloss = QLinearGradient(QPointF(0, 0), QPointF(0, 16))   # glossy highlight on the upper half
    gloss.setColorAt(0.0, QColor(255, 255, 255, 120))
    gloss.setColorAt(0.6, QColor(255, 255, 255, 0))
    p.setPen(Qt.NoPen)
    p.setBrush(gloss)
    p.drawPath(path)
    p.end()
    return pm


def _error_pixmap(size: int):
    from PySide6.QtGui import QFont, QLinearGradient, QPen, QPixmap
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.scale(size / 16.0, size / 16.0)
    g = QLinearGradient(0, 1, 0, 15)
    g.setColorAt(0.0, QColor("#ff8a7a")); g.setColorAt(1.0, QColor("#c92a1a"))
    p.setPen(QPen(QColor("#8a1a0e"), 1.0))
    p.setBrush(g)
    p.drawEllipse(QRectF(1.5, 1.5, 13, 13))
    p.setPen(QPen(QColor("#ffffff"), 2.2, Qt.SolidLine, Qt.RoundCap))
    p.drawLine(5.4, 5.4, 10.6, 10.6)
    p.drawLine(10.6, 5.4, 5.4, 10.6)
    p.end()
    return pm


def state_icon(state: str, complete: bool) -> QIcon:
    """Name-column icon by task state, like BitComet: green ↓ downloading, orange ↑ seeding,
    grey when stopped, amber while checking, blue while moving, red ✕ on errors."""
    if state in ("error", "missing"):
        spec = ("error", "")
    elif state == "tracker_error":                 # data is fine, only the announce failed
        spec = ("up" if complete else "down", "orange" if complete else "green")
    elif state in ("downloading", "metadata"):
        spec = ("down", "green")
    elif state == "seeding":
        spec = ("up", "orange")
    elif state == "checking":
        spec = ("up" if complete else "down", "amber")
    elif state == "moving":
        spec = ("right", "blue")
    else:                                          # paused, stopped, queued, finished
        spec = ("up" if complete else "down", "grey")
    ic = _state_cache.get(spec)
    if ic is None:
        ic = QIcon()
        for sz in (16, 20, 24, 32, 40, 48):
            ic.addPixmap(_error_pixmap(sz) if spec[0] == "error" else _arrow_pixmap(sz, *spec))
        _state_cache[spec] = ic
    return ic


def icon(name: str, key: str = "secondary") -> QIcon:
    return _icon(name, key)


PRIO_ICON = {0: ("fa6s.ban", "danger"), 4: ("fa6s.minus", "secondary"), 7: ("fa6s.angles-up", "primary")}


_flags: dict[str, QIcon] = {}


def flag_path(code: str) -> str:
    """Path of the country flag PNG (flag-icons, MIT), "" if there is none."""
    import os
    path = theme.asset("flags", code.lower() + ".png") if code else ""
    return path if path and os.path.exists(path) else ""


def flag_icon(code: str) -> QIcon | None:
    """Flag at 20×15 (2× PNG for HiDPI); None for unknown codes."""
    code = (code or "").lower()
    if code not in _flags:
        from PySide6.QtGui import QPixmap
        path = flag_path(code)
        if path:
            pm = QPixmap(path)
            pm.setDevicePixelRatio(2.0)
            _flags[code] = QIcon(pm)
        else:
            _flags[code] = None
    return _flags[code]


def prio_icon(prio: int) -> QIcon:
    if prio > 4:
        prio = 7
    elif 0 < prio < 4:
        prio = 4
    return _icon(*PRIO_ICON.get(prio, PRIO_ICON[4]))


# ── Files tab ───────────────────────────────────────────────────
class _ProgressDelegate(QStyledItemDelegate):
    """Thin rounded bar + percentage, the same look as the torrent list."""
    def paint(self, painter, option, index):
        value = index.data(Qt.UserRole)
        if value is None:
            return super().paint(painter, option, index)
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        if option.state & QStyle.State_Selected:
            painter.fillRect(option.rect, QColor(C["selection"]))
        r = option.rect.adjusted(6, 0, -8, 0)
        f = painter.font(); f.setPointSizeF(8.5); painter.setFont(f)
        painter.setPen(QColor(C["body"]))
        painter.drawText(QRectF(r.left(), r.top(), 44, r.height()), Qt.AlignVCenter | Qt.AlignLeft, f"{value * 100:.1f}%")
        bar = QRectF(r.left() + 48, r.center().y() - 3, max(10, r.width() - 48), 6)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(C["secondary_bg"]))
        painter.drawRoundedRect(bar, 3, 3)
        if value > 0:
            painter.setBrush(QColor(C["success" if value >= 1 else "primary"]))
            painter.drawRoundedRect(QRectF(bar.left(), bar.top(), max(6.0, bar.width() * value), 6), 3, 3)
        painter.restore()


class FilesView(QTreeWidget):
    """Folders → files with type icons, size, progress bar and priority.
    The tree is built once per torrent; every refresh only updates numbers."""
    PRIO_TEXT = {0: "prio_skip", 4: "prio_normal", 7: "prio_high"}

    def __init__(self):
        super().__init__()
        self.setHeaderLabels([t("file_name"), t("col_size"), t("col_progress"), t("file_prio")])
        self.setUniformRowHeights(True)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setFocusPolicy(Qt.NoFocus)
        self.setAnimated(True)
        hh = self.header()
        hh.setStretchLastSection(False)
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        for c, w in ((1, 100), (2, 170), (3, 140)):
            hh.resizeSection(c, w)
        self.setItemDelegateForColumn(2, _ProgressDelegate(self))
        self.key = None
        self.leaves: dict[int, QTreeWidgetItem] = {}
        self.folders: list[QTreeWidgetItem] = []
        # expandAll() also reports leaves as "expanded" — only folders get the open/closed icon
        self.itemExpanded.connect(lambda it: it.childCount() and it.setIcon(0, folder_icon(True)))
        self.itemCollapsed.connect(lambda it: it.childCount() and it.setIcon(0, folder_icon(False)))

    def show_waiting(self):
        self.clear()
        self.key = None
        QTreeWidgetItem(self, [t("files_wait"), "", "", ""])

    def update_files(self, key: str, files: list[dict]):
        from .ui import fmt_size
        if not files:
            if self.key is not None or self.topLevelItemCount() == 0:
                self.show_waiting()
            return
        if key != self.key or len(files) != len(self.leaves):
            self._build(key, files)
        sizes: dict[int, tuple[int, float]] = {}
        for f in files:
            it = self.leaves.get(f["index"])
            if it is None:
                continue
            it.setData(2, Qt.UserRole, f["progress"])
            prio = f["priority"]
            it.setText(3, t(self.PRIO_TEXT.get(prio, "prio_high" if prio > 4 else "prio_normal")))
            it.setIcon(3, prio_icon(prio))
            it.setForeground(0, QColor(C["secondary" if prio == 0 else "body"]))
            sizes[f["index"]] = (f["size"], f["progress"])
        # folders: sum of sizes, weighted progress
        for folder in reversed(self.folders):          # deepest first
            total = done = 0
            for i in range(folder.childCount()):
                ch = folder.child(i)
                sz = ch.data(1, Qt.UserRole) or 0
                total += sz
                done += sz * (ch.data(2, Qt.UserRole) or 0)
            folder.setData(1, Qt.UserRole, total)
            folder.setText(1, fmt_size(total))
            folder.setData(2, Qt.UserRole, done / total if total else 0.0)
        self.viewport().update()

    def _build(self, key: str, files: list[dict]):
        from .ui import fmt_size
        self.clear()
        self.key = key
        self.leaves, self.folders = {}, []
        nodes: dict[tuple, QTreeWidgetItem] = {}
        for f in files:
            parts = f["path"].replace("\\", "/").split("/")
            parent, path = self.invisibleRootItem(), ()
            for part in parts[:-1]:
                path += (part,)
                node = nodes.get(path)
                if node is None:
                    node = QTreeWidgetItem(parent, [part, "", "", ""])
                    node.setIcon(0, folder_icon(False))
                    node.setData(0, Qt.UserRole, None)
                    node.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
                    nodes[path] = node
                    self.folders.append(node)
                parent = node
            leaf = QTreeWidgetItem(parent, [parts[-1], fmt_size(f["size"]), "", ""])
            leaf.setIcon(0, file_icon(parts[-1]))
            leaf.setData(0, Qt.UserRole, f["index"])
            leaf.setData(1, Qt.UserRole, f["size"])
            leaf.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
            self.leaves[f["index"]] = leaf
        if len(files) <= 300:
            self.expandAll()
        else:
            for i in range(self.topLevelItemCount()):
                self.topLevelItem(i).setExpanded(True)

    def selected_indices(self) -> list[int]:
        """File indices under the selection (a selected folder means all its files)."""
        out: set[int] = set()

        def walk(it: QTreeWidgetItem):
            idx = it.data(0, Qt.UserRole)
            if idx is not None:
                out.add(idx)
            for i in range(it.childCount()):
                walk(it.child(i))
        for it in self.selectedItems():
            walk(it)
        return sorted(out)

    def retheme(self):
        for it in self.folders:
            it.setIcon(0, folder_icon(it.isExpanded()))
        for leaf in self.leaves.values():
            leaf.setIcon(0, file_icon(leaf.text(0)))


class TrackersView(QTreeWidget):
    """Trackers tab, qBittorrent-style: DHT / PeX / LSD on top, then the trackers.
    A tracker announced more than once (hybrid v1+v2 torrent, several network interfaces)
    expands into its single announces. Built once per torrent / tracker list, then only updated."""
    COLS = ("tr_url", "tr_tier", "tr_proto", "tr_state", "tr_peers", "tr_seeds", "tr_leeches",
            "tr_downloaded", "tr_msg", "tr_next", "tr_min")
    STATUS_ICON = {
        "working": ("fa6s.circle-check", "success"), "updating": ("fa6s.rotate", "info"),
        "tracker_error": ("fa6s.circle-exclamation", "danger"), "not_working": ("fa6s.circle-xmark", "danger"),
        "not_contacted": ("fa6s.hourglass-half", "secondary"),
        "disabled": ("fa6s.ban", "secondary"), "private": ("fa6s.ban", "secondary"),
    }
    SOURCES = (("dht", "DHT", "fa6s.diagram-project"), ("pex", "PeX", "fa6s.people-arrows"),
               ("lsd", "LSD", "fa6s.house-signal"))
    NUMERIC = (1, 4, 5, 6, 7, 9, 10)

    def __init__(self):
        super().__init__()
        self.setHeaderLabels([t(k) for k in self.COLS])
        self.setUniformRowHeights(True)
        self.setRootIsDecorated(True)
        self.setFocusPolicy(Qt.NoFocus)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setAnimated(True)
        hh = self.header()
        hh.setStretchLastSection(False)
        hh.setSectionResizeMode(QHeaderView.Interactive)
        for c, w in enumerate((420, 70, 90, 230, 60, 60, 60, 110, 260, 100, 100)):
            hh.resizeSection(c, w)
        for c in self.NUMERIC:
            self.headerItem().setTextAlignment(c, Qt.AlignRight | Qt.AlignVCenter)
        self.key = None
        self._sig = None
        self.rows: dict[str, QTreeWidgetItem] = {}
        self.children_of: dict[str, list[QTreeWidgetItem]] = {}
        self.expanded: set[str] = set()
        self.itemExpanded.connect(lambda it: self.expanded.add(it.data(0, Qt.UserRole)))
        self.itemCollapsed.connect(lambda it: self.expanded.discard(it.data(0, Qt.UserRole)))

    def clear_view(self):
        self.clear()
        self.key, self._sig = None, None
        self.rows, self.children_of = {}, {}

    # ── build / update ─────────────────────────────────────────
    def _build(self, key, trackers):
        self.clear()
        if key != self.key:
            self.expanded = set()
        self.key = key
        self.rows, self.children_of = {}, {}
        for name, label, ic in self.SOURCES:
            it = QTreeWidgetItem(self, [f"** [{label}] **"] + [""] * (len(self.COLS) - 1))
            it.setIcon(0, icon(ic, "secondary"))
            it.setData(0, Qt.UserRole, name)
            self.rows[name] = it
        for tr in trackers:
            it = QTreeWidgetItem(self, [tr["url"]] + [""] * (len(self.COLS) - 1))
            it.setData(0, Qt.UserRole, tr["url"])
            self.rows[tr["url"]] = it
            kids = []
            for c in tr["children"]:
                label = c["endpoint"] or tr["url"]
                kid = QTreeWidgetItem(it, [label] + [""] * (len(self.COLS) - 1))
                kids.append(kid)
            self.children_of[tr["url"]] = kids
            if tr["url"] in self.expanded:
                it.setExpanded(True)

    def update_trackers(self, key: str, trackers: list[dict], sources: dict):
        sig = (key, tuple((tr["url"], len(tr["children"])) for tr in trackers))
        if sig != self._sig:
            self._build(key, trackers)
            self._sig = sig
        muted = QColor(C["secondary"])
        for name, _label, _ic in self.SOURCES:
            s = sources.get(name)
            it = self.rows[name]
            if not s:
                continue
            vals = {2: "", 3: t("trs_" + s["status"]),
                    4: self._n(s["peers"]), 5: self._n(s["seeds"]), 6: self._n(s["leeches"]),
                    7: t("na"), 8: t("trs_private_msg") if s["status"] == "private" else "", 9: "", 10: ""}
            self._fill(it, vals, s["status"])
            for c in range(len(self.COLS)):
                it.setForeground(c, muted)
        for tr in trackers:
            it = self.rows.get(tr["url"])
            if it is None:
                continue
            self._fill(it, self._vals(tr), tr["status"])
            it.setText(1, str(tr["tier"]))
            for kid, c in zip(self.children_of.get(tr["url"], []), tr["children"]):
                self._fill(kid, self._vals(c), c["status"])

    def _vals(self, r: dict) -> dict:
        from .ui import fmt_eta
        return {2: r["proto"], 3: t("trs_" + r["status"]), 4: self._n(r["peers"]), 5: self._n(r["seeds"]),
                6: self._n(r["leeches"]), 7: self._n(r["downloaded"]), 8: r["message"],
                9: fmt_eta(r["next"]) if r["next"] > 0 else "", 10: fmt_eta(r["min"]) if r["min"] > 0 else ""}

    @staticmethod
    def _n(v: int) -> str:
        return t("na") if v is None or v < 0 else str(v)

    def _fill(self, it: QTreeWidgetItem, vals: dict, status: str):
        for c, v in vals.items():
            if it.text(c) != v:
                it.setText(c, v)
            if c in self.NUMERIC:
                it.setTextAlignment(c, Qt.AlignRight | Qt.AlignVCenter)
        it.setToolTip(8, vals.get(8, ""))
        it.setToolTip(0, it.text(0))
        ic, col = self.STATUS_ICON.get(status, ("fa6s.circle-question", "secondary"))
        it.setIcon(3, icon(ic, col))
        bad = status in ("tracker_error", "not_working")
        it.setForeground(8, QColor(C["danger"]) if bad else QColor(C["body"]))
        if it.data(0, Qt.UserRole) not in ("dht", "pex", "lsd") and it.parent() is None:
            it.setIcon(0, icon("fa6s.circle-exclamation" if bad else "fa6s.circle-check" if status == "working"
                               else "fa6s.server", "danger" if bad else "success" if status == "working" else "secondary"))
