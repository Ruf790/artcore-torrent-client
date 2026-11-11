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


def icon(name: str, key: str = "secondary") -> QIcon:
    return _icon(name, key)


PRIO_ICON = {0: ("fa6s.ban", "danger"), 4: ("fa6s.minus", "secondary"), 7: ("fa6s.angles-up", "primary")}


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
