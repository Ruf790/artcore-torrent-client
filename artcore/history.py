"""Torrent history: the list of removed torrents, each can be brought back."""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QMenu, QMessageBox, QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout)

from .i18n import t
from .widgets import icon


def _size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.2f} {unit}"
        n /= 1024
    return str(n)


class HistoryDialog(QDialog):
    COLS = ("hist_name", "hist_size", "hist_done", "hist_removed", "hist_files", "hist_tracker", "hist_path")

    def __init__(self, engine, parent=None, on_restored=None):
        super().__init__(parent)
        self.engine = engine
        self.on_restored = on_restored
        self.setWindowTitle(t("history"))
        self.setWindowIcon(icon("fa6s.clock-rotate-left", "primary"))
        self.resize(980, 520)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 14, 14, 12)
        lay.setSpacing(10)

        top = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText(t("hist_search"))
        self.search.setClearButtonEnabled(True)
        self.search.addAction(icon("fa6s.magnifying-glass", "secondary"), QLineEdit.LeadingPosition)
        self.search.textChanged.connect(self._filter)
        top.addWidget(self.search, 1)
        self.count = QLabel()
        self.count.setObjectName("meta")
        top.addWidget(self.count)
        lay.addLayout(top)

        self.tree = QTreeWidget()
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(False)
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.setHeaderLabels([t(k) for k in self.COLS])
        self.tree.setSortingEnabled(True)
        self.tree.sortByColumn(3, Qt.DescendingOrder)
        hh = self.tree.header()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        hh.setStretchLastSection(False)
        for c, w in enumerate((0, 85, 65, 125, 160, 130, 200)):
            if w:
                self.tree.setColumnWidth(c, w)
        self.tree.itemDoubleClicked.connect(lambda *_: self.restore())
        self.tree.itemSelectionChanged.connect(self._sel_changed)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._menu)
        lay.addWidget(self.tree, 1)

        self.empty = QLabel(t("hist_empty"))
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setObjectName("meta")
        lay.addWidget(self.empty)

        btns = QHBoxLayout()
        self.b_restore = QPushButton(icon("fa6s.rotate-left", "#ffffff"), t("hist_restore"))
        self.b_restore.setObjectName("primary")
        self.b_restore.clicked.connect(lambda: self.restore())
        self.b_paused = QPushButton(icon("fa6s.stop", "secondary"), t("hist_restore_paused"))
        self.b_paused.clicked.connect(lambda: self.restore(paused=True))
        self.b_forget = QPushButton(icon("fa6s.xmark", "danger"), t("hist_forget"))
        self.b_forget.clicked.connect(self.forget)
        self.b_clear = QPushButton(icon("fa6s.trash-can", "danger"), t("hist_clear"))
        self.b_clear.clicked.connect(self.clear)
        for b in (self.b_restore, self.b_paused, self.b_forget):
            btns.addWidget(b)
        btns.addStretch()
        btns.addWidget(self.b_clear)
        close = QPushButton(t("close"))
        close.clicked.connect(self.accept)
        btns.addWidget(close)
        lay.addLayout(btns)
        self.reload()

    # ── list ───────────────────────────────────────────────────
    def reload(self):
        self.tree.setSortingEnabled(False)
        self.tree.clear()
        for e in self.engine.history:
            removed = datetime.fromtimestamp(e.get("removed", 0))
            vals = (e.get("name", ""), _size(e.get("size", 0)), f"{e.get('progress', 0) * 100:.0f}%",
                    removed.strftime("%d.%m.%Y %H:%M"),
                    t("hist_files_del" if e.get("deleted_files") else "hist_files_kept"),
                    e.get("tracker", ""), e.get("save_path", ""))
            it = _Item(vals)
            it.setData(0, Qt.UserRole, e.get("key"))
            it.setData(1, Qt.UserRole, e.get("size", 0))
            it.setData(2, Qt.UserRole, e.get("progress", 0))
            it.setData(3, Qt.UserRole, e.get("removed", 0))
            it.setIcon(0, icon("fa6s.file-arrow-down", "primary"))
            it.setIcon(4, icon("fa6s.trash-can" if e.get("deleted_files") else "fa6s.hard-drive",
                               "danger" if e.get("deleted_files") else "success"))
            it.setToolTip(0, e.get("name", ""))
            it.setToolTip(6, e.get("save_path", ""))
            for c in (1, 2):
                it.setTextAlignment(c, Qt.AlignRight | Qt.AlignVCenter)
            self.tree.addTopLevelItem(it)
        self.tree.setSortingEnabled(True)
        n = len(self.engine.history)
        self.count.setText(t("hist_count", n))
        self.tree.setVisible(bool(n))
        self.search.setVisible(bool(n))
        self.empty.setVisible(not n)
        self.b_clear.setEnabled(bool(n))
        self._filter(self.search.text())
        self._sel_changed()

    def _filter(self, text: str):
        text = text.strip().lower()
        for i in range(self.tree.topLevelItemCount()):
            it = self.tree.topLevelItem(i)
            it.setHidden(bool(text) and text not in it.text(0).lower())

    def _sel_changed(self):
        on = bool(self._keys())
        for b in (self.b_restore, self.b_paused, self.b_forget):
            b.setEnabled(on)

    def _keys(self) -> list[str]:
        return [it.data(0, Qt.UserRole) for it in self.tree.selectedItems()]

    def _menu(self, pos):
        if not self._keys():
            return
        m = QMenu(self)
        m.addAction(icon("fa6s.rotate-left", "primary"), t("hist_restore"), lambda: self.restore())
        m.addAction(icon("fa6s.stop", "secondary"), t("hist_restore_paused"), lambda: self.restore(paused=True))
        m.addSeparator()
        m.addAction(icon("fa6s.xmark", "danger"), t("hist_forget"), self.forget)
        m.exec(self.tree.viewport().mapToGlobal(pos))

    # ── actions ────────────────────────────────────────────────
    def restore(self, paused: bool = False):
        keys = self._keys()
        if not keys:
            return
        names, errors = [], []
        for k in keys:
            e = next((e for e in self.engine.history if e.get("key") == k), {})
            try:
                self.engine.restore_from_history(k, paused=paused)
                names.append(e.get("name", k))
            except Exception as ex:
                errors.append(f"{e.get('name', k)}: {ex}")
        if self.on_restored:
            self.on_restored(names)
        self.reload()
        if errors:
            QMessageBox.warning(self, t("history"), "\n".join(errors))

    def forget(self):
        keys = self._keys()
        if keys:
            self.engine.forget_history(keys)
            self.reload()

    def clear(self):
        if QMessageBox.question(self, t("history"), t("hist_clear_q")) == QMessageBox.Yes:
            self.engine.clear_history()
            self.reload()


class _Item(QTreeWidgetItem):
    """Sorts size / progress / date by value, not by text."""
    def __lt__(self, other):
        c = self.treeWidget().sortColumn() if self.treeWidget() else 0
        if c in (1, 2, 3):
            return (self.data(c, Qt.UserRole) or 0) < (other.data(c, Qt.UserRole) or 0)
        return self.text(c).lower() < other.text(c).lower()
