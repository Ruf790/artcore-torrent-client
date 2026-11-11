"""Extra dialogs and editors: trackers, replace in trackers, categories.
Shared helpers (colours, icons, section titles) come from ui.py at runtime."""
from __future__ import annotations

from PySide6.QtWidgets import (QAbstractItemView, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
                               QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QPlainTextEdit,
                               QPushButton, QRadioButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
                               QWidget)

from .i18n import t


def _ui():
    from . import ui
    return ui


def _buttons(dlg: QDialog, ok_text: str | None = None) -> QDialogButtonBox:
    bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    ok = bb.button(QDialogButtonBox.Ok)
    ok.setObjectName("primary")
    if ok_text:
        ok.setText(ok_text)
    bb.accepted.connect(dlg.accept)
    bb.rejected.connect(dlg.reject)
    return bb


class TrackersDialog(QDialog):
    """Edit tracker URLs of one torrent; tiers are separated by blank lines."""
    def __init__(self, name: str, trackers: list[tuple[str, int]], parent=None):
        super().__init__(parent)
        ui = _ui()
        ui._theme_title_bar(self)
        self.setWindowTitle(t("trk_title"))
        self.setMinimumSize(640, 380)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 12, 20, 16)
        head = ui._section(name); head.setWordWrap(True)
        lay.addWidget(head)
        hint = QLabel(t("trk_hint")); hint.setObjectName("meta"); hint.setWordWrap(True)
        lay.addWidget(hint)
        self.edit = QPlainTextEdit()
        self.edit.setLineWrapMode(QPlainTextEdit.NoWrap)
        lines, prev = [], None
        for url, tier in sorted(trackers, key=lambda x: x[1]):
            if prev is not None and tier != prev:
                lines.append("")
            lines.append(url)
            prev = tier
        self.edit.setPlainText("\n".join(lines))
        lay.addWidget(self.edit, 1)
        lay.addWidget(_buttons(self))

    def result_trackers(self) -> list[tuple[str, int]]:
        out, tier = [], 0
        for line in self.edit.toPlainText().splitlines():
            line = line.strip()
            if not line:
                if out and out[-1][1] == tier:
                    tier += 1
                continue
            out.append((line, tier))
        return out


class ReplaceTrackersDialog(QDialog):
    """Find & replace in tracker URLs of selected or all torrents (changed passkey)."""
    def __init__(self, n_selected: int, n_all: int, parent=None):
        super().__init__(parent)
        ui = _ui()
        ui._theme_title_bar(self)
        self.setWindowTitle(t("rep_title"))
        self.setMinimumWidth(560)
        form = QFormLayout(self)
        form.setContentsMargins(20, 12, 20, 16)
        form.addRow(ui._section(t("rep_title")))
        hint = QLabel(t("rep_hint")); hint.setObjectName("meta"); hint.setWordWrap(True)
        form.addRow(hint)
        self.find = QLineEdit()
        self.repl = QLineEdit()
        form.addRow(t("rep_find"), self.find)
        form.addRow(t("rep_with"), self.repl)
        self.sel = QRadioButton(t("rep_scope_sel", n_selected))
        self.all = QRadioButton(t("rep_scope_all", n_all))
        (self.sel if n_selected else self.all).setChecked(True)
        self.sel.setEnabled(n_selected > 0)
        form.addRow(self.sel)
        form.addRow(self.all)
        form.addRow(_buttons(self))


class CategoriesEditor(QWidget):
    """Name + folder table for Settings."""
    def __init__(self, categories: list[dict], parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.tbl = QTableWidget(0, 2)
        self.tbl.setHorizontalHeaderLabels([t("cat_name"), t("cat_path")])
        self.tbl.verticalHeader().hide()
        self.tbl.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tbl.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.tbl.setColumnWidth(0, 150)
        self.tbl.setMinimumHeight(140)
        for c in categories:
            self._add_row(c.get("name", ""), c.get("path", ""))
        lay.addWidget(self.tbl)
        row = QHBoxLayout()
        add = QPushButton(t("cat_add")); add.clicked.connect(self._new)
        browse = QPushButton(t("set_browse")); browse.clicked.connect(self._browse)
        rm = QPushButton(t("cat_remove")); rm.setObjectName("danger"); rm.clicked.connect(self._remove)
        row.addWidget(add); row.addWidget(browse); row.addStretch(); row.addWidget(rm)
        lay.addLayout(row)

    def _add_row(self, name: str, path: str):
        r = self.tbl.rowCount()
        self.tbl.insertRow(r)
        self.tbl.setItem(r, 0, QTableWidgetItem(name))
        self.tbl.setItem(r, 1, QTableWidgetItem(path))

    def _new(self):
        name, ok = QInputDialog.getText(self, t("set_grp_cats"), t("cat_prompt"))
        if ok and name.strip() and name.strip() not in [c["name"] for c in self.categories()]:
            self._add_row(name.strip(), "")
            self.tbl.selectRow(self.tbl.rowCount() - 1)

    def _browse(self):
        r = self.tbl.currentRow()
        if r < 0:
            return
        cur = self.tbl.item(r, 1).text() if self.tbl.item(r, 1) else ""
        d = QFileDialog.getExistingDirectory(self, t("choose_folder"), cur)
        if d:
            self.tbl.setItem(r, 1, QTableWidgetItem(d))

    def _remove(self):
        for r in sorted({i.row() for i in self.tbl.selectedItems()}, reverse=True):
            self.tbl.removeRow(r)

    def categories(self) -> list[dict]:
        out = []
        for r in range(self.tbl.rowCount()):
            name = (self.tbl.item(r, 0).text() if self.tbl.item(r, 0) else "").strip()
            if name:
                out.append({"name": name, "path": (self.tbl.item(r, 1).text() if self.tbl.item(r, 1) else "").strip()})
        return out


def folder_row(parent: QWidget, value: str) -> tuple[QHBoxLayout, QLineEdit]:
    """Line edit + Browse button for a folder setting."""
    edit = QLineEdit(value)
    edit.setPlaceholderText(t("folder_off"))
    btn = QPushButton(t("set_browse"))

    def pick():
        d = QFileDialog.getExistingDirectory(parent, t("choose_folder"), edit.text())
        if d:
            edit.setText(d)
    btn.clicked.connect(pick)
    row = QHBoxLayout()
    row.addWidget(edit, 1)
    row.addWidget(btn)
    return row, edit
