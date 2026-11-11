"""Extra dialogs and editors: trackers, replace in trackers, categories, import.
Shared helpers (colours, icons, section titles) come from ui.py at runtime."""
from __future__ import annotations

import os

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


class ImportDialog(QDialog):
    """Import torrents from qBittorrent / BitComet with a preview list."""
    SOURCES = (("qBittorrent", ("qbittorrent.exe", "qbittorrent")), ("BitComet", ("bitcomet.exe",)))

    def __init__(self, engine, cfg: dict, parent=None):
        super().__init__(parent)
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QCheckBox, QComboBox
        from . import importers
        ui = _ui()
        ui._theme_title_bar(self)
        self.Qt, self.importers, self.engine, self.cfg = Qt, importers, engine, cfg
        self.cands: list = []
        self.imported = 0
        self.setWindowTitle(t("imp_title"))
        self.resize(900, 600)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 12, 20, 16)
        lay.addWidget(ui._section(t("imp_title")))

        form = QFormLayout()
        self.source = QComboBox()
        for name, _ in self.SOURCES:
            self.source.addItem(name)
        self.source.currentIndexChanged.connect(self._source_changed)
        form.addRow(t("imp_source"), self.source)
        frow, self.folder = folder_row(self, "")
        scan = QPushButton(t("imp_scan")); scan.setObjectName("primary"); scan.clicked.connect(self.scan)
        frow.addWidget(scan)
        form.addRow(t("imp_folder"), frow)
        lay.addLayout(form)

        self.warn = QLabel(); self.warn.setWordWrap(True)
        lay.addWidget(self.warn)

        self.tbl = QTableWidget(0, 5)
        self.tbl.setHorizontalHeaderLabels([t("file_name"), t("imp_col_size"), t("imp_col_path"),
                                            t("cat_menu"), t("imp_col_note")])
        self.tbl.verticalHeader().hide()
        self.tbl.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
        hh = self.tbl.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        for c, w in ((1, 90), (2, 220), (3, 90), (4, 200)):
            self.tbl.setColumnWidth(c, w)
        lay.addWidget(self.tbl, 1)
        self.count = QLabel(); self.count.setObjectName("meta")
        lay.addWidget(self.count)

        opts = QFormLayout()
        self.recheck = QCheckBox(t("imp_recheck"))
        self.paused = QCheckBox(t("imp_paused"))
        opts.addRow(self.recheck)
        opts.addRow(self.paused)
        drow, self.default = folder_row(self, cfg.get("save_path", ""))
        opts.addRow(t("imp_default"), drow)
        lay.addLayout(opts)
        lay.addWidget(_buttons(self, t("imp_btn")))
        self._source_changed()

    def _source_changed(self):
        idx = self.source.currentIndex()
        dirs = self.importers.qbittorrent_dirs() if idx == 0 else self.importers.bitcomet_dirs()
        self.folder.setText(dirs[0] if dirs else "")
        bc = idx == 1
        self.recheck.setChecked(bc)
        self.recheck.setEnabled(not bc)
        self.recheck.setToolTip(t("imp_recheck_bc") if bc else "")
        self.scan()

    def scan(self):
        ui = _ui()
        Qt = self.Qt
        name, procs = self.SOURCES[self.source.currentIndex()]
        msgs = []
        if self.importers.client_running(procs):
            msgs.append(f'<span style="color:{ui.C["danger"]}">{t("imp_running", name)}</span>')
        if self.source.currentIndex() == 1:
            msgs.append(t("imp_recheck_bc"))
        folder = self.folder.text().strip()
        self.cands = []
        if folder and os.path.isdir(folder):
            self.cands = (self.importers.scan_qbittorrent(folder) if self.source.currentIndex() == 0
                          else self.importers.scan_bitcomet(folder))
        else:
            msgs.append(t("imp_not_found"))
        self.warn.setText("<br>".join(msgs))
        self.warn.setVisible(bool(msgs))

        self.tbl.setRowCount(len(self.cands))
        for r, c in enumerate(self.cands):
            dup = c.info_hash in self.engine.handles
            broken = c.note.startswith("error:") or (not c.torrent_file and not c.resume_file)
            if dup:
                note = t("imp_already")
            elif c.note.startswith("error:"):
                note = t("imp_note_error", c.note[6:])
            else:
                note = t("imp_note_" + c.note) if c.note else ""
            vals = (c.name, ui.fmt_size(c.size) if c.size else "", c.save_path, c.category, note)
            for col, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if col == 0:
                    it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
                    it.setCheckState(Qt.Unchecked if (dup or broken) else Qt.Checked)
                    if dup or broken:
                        it.setFlags(it.flags() & ~Qt.ItemIsEnabled)
                if col == 4 and note:
                    it.setToolTip(note)
                    it.setForeground(ui.QColor(ui.C["danger" if broken else "secondary"]))
                self.tbl.setItem(r, col, it)
        self.count.setText(t("imp_found", len(self.cands)))

    def run_import(self) -> tuple[int, int]:
        Qt = self.Qt
        done = skipped = 0
        default = self.default.text().strip() or self.cfg.get("save_path", "")
        cats = {c["name"] for c in self.cfg.get("categories", [])}
        for r, c in enumerate(self.cands):
            it = self.tbl.item(r, 0)
            if it is None or it.checkState() != Qt.Checked:
                continue
            try:
                atp = self.importers.build_params(c, default, self.recheck.isChecked(), self.paused.isChecked())
                key = self.engine.add_params(atp)
                if c.category:
                    self.engine.set_category(key, c.category)
                    if c.category not in cats:
                        self.cfg.setdefault("categories", []).append({"name": c.category, "path": ""})
                        cats.add(c.category)
                done += 1
            except Exception as e:
                skipped += 1
                self.engine._log("error", f"import {c.name}: {e}")
        return done, skipped
