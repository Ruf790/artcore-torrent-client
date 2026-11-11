"""Tabs shared by the Add dialog and Task properties, laid out like BitComet's:
Advanced (trackers, peer sources, individual speed), Publisher (+ web seeds), Download order."""
from __future__ import annotations

import html
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QGridLayout, QHBoxLayout, QHeaderView,
                               QLabel, QPlainTextEdit, QPushButton, QSpinBox, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from . import theme, widgets
from .i18n import t

C = theme.current


def _ui():
    from . import ui
    return ui


def trackers_to_text(trackers: list[tuple[str, int]]) -> str:
    """Tiers separated by blank lines (BitComet's '|' prefix is accepted on input too)."""
    lines, prev = [], None
    for url, tier in sorted(trackers, key=lambda x: x[1]):
        if prev is not None and tier != prev:
            lines.append("")
        lines.append(url)
        prev = tier
    return "\n".join(lines)


def text_to_trackers(text: str) -> list[tuple[str, int]]:
    """A blank line starts the next tier; a BitComet-style '|url' just stays in the tier above."""
    out, tier = [], 0
    for line in text.splitlines():
        line = line.strip().lstrip("|").strip()
        if not line:
            if out and out[-1][1] == tier:
                tier += 1
            continue
        out.append((line, tier))
    return out


def _grid_rows(rows) -> QGridLayout:
    g = QGridLayout()
    g.setHorizontalSpacing(14)
    g.setVerticalSpacing(6)
    for r, (k, w) in enumerate(rows):
        lab = QLabel(t(k)); lab.setObjectName("rowLbl")
        g.addWidget(lab, r, 0)
        g.addWidget(w, r, 1)
    g.setColumnStretch(1, 1)
    return g


class AdvancedTab(QWidget):
    """Tracker list + task settings (peer sources, super-seeding, individual speed limits)."""
    def __init__(self, d: dict):
        super().__init__()
        ui = _ui()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 10, 6, 6)
        lay.setSpacing(8)
        lay.addWidget(ui._section(t("tp_trackers"), "fa6s.server", "info"))
        self.trackers = QPlainTextEdit(trackers_to_text(d.get("trackers", [])))
        self.trackers.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.trackers.setMinimumHeight(130)
        lay.addWidget(self.trackers, 1)
        hint = QLabel(t("tp_trackers_hint")); hint.setObjectName("meta"); hint.setWordWrap(True)
        lay.addWidget(hint)

        lay.addWidget(ui._section(t("tp_task"), "fa6s.sliders", "primary"))
        priv = bool(d.get("private"))
        self.dht = QCheckBox(t("tp_dht"))
        self.pex = QCheckBox(t("tp_pex"))
        self.lsd = QCheckBox(t("tp_lsd"))
        for cb, k in ((self.dht, "dht"), (self.pex, "pex"), (self.lsd, "lsd")):
            cb.setChecked(bool(d.get(k, True)) and not priv)
            cb.setEnabled(not priv)
            if priv:
                cb.setToolTip(t("trs_private_msg"))
            lay.addWidget(cb)
        self.sup = QCheckBox(t("tp_super"))
        self.sup.setChecked(bool(d.get("super_seed")))
        lay.addWidget(self.sup)
        self.own = QCheckBox(t("tp_own"))
        self.own.setChecked(bool(d.get("own_limits")))
        lay.addWidget(self.own)
        row = QHBoxLayout()
        row.setContentsMargins(26, 0, 0, 0)
        self.dl = QSpinBox(); self.ul = QSpinBox()
        for sb, k, lab in ((self.dl, "dl_limit", "tp_dl"), (self.ul, "ul_limit", "tp_ul")):
            sb.setRange(0, 10_000_000)
            sb.setSuffix(" KiB/s")
            sb.setSpecialValueText(t("unlimited"))
            sb.setValue(int(d.get(k, 0)))
            row.addWidget(QLabel(t(lab)))
            row.addWidget(sb)
            row.addSpacing(18)
        row.addStretch()
        lay.addLayout(row)
        self.own.toggled.connect(lambda on: (self.dl.setEnabled(on), self.ul.setEnabled(on)))
        self.own.toggled.emit(self.own.isChecked())
        if priv:
            note = QLabel(t("tp_private_note")); note.setObjectName("meta"); note.setWordWrap(True)
            lay.addWidget(note)

    def result(self) -> dict:
        r = {"trackers": text_to_trackers(self.trackers.toPlainText()), "super_seed": self.sup.isChecked(),
             "own_limits": self.own.isChecked(), "dl_limit": self.dl.value(), "ul_limit": self.ul.value()}
        if self.dht.isEnabled():
            r.update(dht=self.dht.isChecked(), pex=self.pex.isChecked(), lsd=self.lsd.isChecked())
        return r


class PublisherTab(QWidget):
    """Publisher name / URL, creator, creation time, web seeds (editable), description."""
    def __init__(self, d: dict):
        super().__init__()
        ui = _ui()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 10, 6, 6)
        lay.setSpacing(8)
        lay.addWidget(ui._section(t("tp_pub_info"), "fa6s.bullhorn", "orange"))

        def ro(text: str, link: bool = False) -> QLabel:
            lab = QLabel()
            lab.setObjectName("rowVal")
            lab.setTextInteractionFlags(Qt.TextBrowserInteraction)
            if link and text.startswith(("http://", "https://")):
                lab.setTextFormat(Qt.RichText)
                lab.setOpenExternalLinks(True)
                lab.setText(f'<a href="{html.escape(text)}" style="color:{C["primary"]}">{html.escape(text)}</a>')
            else:
                lab.setText(text or "—")
            return lab
        created = d.get("created") or 0
        lay.addLayout(_grid_rows([
            ("tp_pub_name", ro(d.get("publisher", ""))),
            ("tp_pub_url", ro(d.get("publisher_url", ""), True)),
            ("gen_creator", ro(d.get("creator", ""))),
            ("gen_created", ro(datetime.fromtimestamp(created).strftime("%Y-%m-%d %H:%M:%S") if created else "")),
        ]))
        lay.addWidget(ui._section(t("tp_webseeds"), "fa6s.globe", "info"))
        self.seeds = QPlainTextEdit("\n".join(d.get("web_seeds", [])))
        self.seeds.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.seeds.setPlaceholderText(t("tp_webseeds_hint"))
        lay.addWidget(self.seeds, 1)
        lay.addWidget(ui._section(t("tp_desc"), "fa6s.comment", "teal"))
        desc = QPlainTextEdit(d.get("comment", ""))
        desc.setReadOnly(True)
        lay.addWidget(desc, 1)

    def result(self) -> dict:
        return {"web_seeds": [u.strip() for u in self.seeds.toPlainText().splitlines() if u.strip()]}


class OrderTab(QWidget):
    """'Download order': files in the order they should come; the top ones get the highest priority."""
    def __init__(self, d: dict):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 10, 6, 6)
        lay.setSpacing(8)
        files = d.get("files", [])           # (index, path, size, progress 0..1, priority)
        self.by_idx = {f[0]: f for f in files}
        order = [i for i in (d.get("order") or []) if i in self.by_idx]
        order += [f[0] for f in files if f[0] not in order]
        top = QHBoxLayout()
        self.enable = QCheckBox(t("tp_order_on"))
        self.enable.setChecked(bool(d.get("order")))
        top.addWidget(self.enable)
        top.addStretch()
        self.up = QPushButton(widgets.icon("fa6s.arrow-up", "primary"), "")
        self.down = QPushButton(widgets.icon("fa6s.arrow-down", "primary"), "")
        for b, tip in ((self.up, "tp_up"), (self.down, "tp_down")):
            b.setFixedWidth(44)
            b.setToolTip(t(tip))
            top.addWidget(b)
        lay.addLayout(top)
        self.tbl = QTableWidget(0, 5)
        self.tbl.setHorizontalHeaderLabels([t("tp_ord"), t("file_name"), t("col_size"), "%", t("tp_prio")])
        self.tbl.verticalHeader().hide()
        self.tbl.verticalHeader().setDefaultSectionSize(26)
        self.tbl.setShowGrid(False)
        self.tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tbl.setSelectionMode(QAbstractItemView.SingleSelection)
        hh = self.tbl.horizontalHeader()
        hh.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        for c, w in ((0, 70), (2, 100), (3, 70), (4, 130)):
            self.tbl.setColumnWidth(c, w)
        lay.addWidget(self.tbl, 1)
        hint = QLabel(t("tp_order_hint")); hint.setObjectName("meta"); hint.setWordWrap(True)
        lay.addWidget(hint)
        self.order = order
        self._fill()
        self.up.clicked.connect(lambda: self._move(-1))
        self.down.clicked.connect(lambda: self._move(1))
        for keys, step in (("Ctrl+Up", -1), ("Ctrl+Down", 1), ("Ctrl+Home", "top"), ("Ctrl+End", "end")):
            QShortcut(QKeySequence(keys), self.tbl, activated=lambda s=step: self._move(s))
        self.enable.toggled.connect(self._enabled)
        self._enabled(self.enable.isChecked())

    def _enabled(self, on: bool):
        for w in (self.up, self.down):
            w.setEnabled(on)
        self.tbl.setEnabled(on)
        self._fill()

    def set_wanted(self, wanted: set[int] | None):
        """Add dialog: files unticked on the first tab show as skipped here."""
        for i, f in list(self.by_idx.items()):
            prio = 4 if wanted is None or i in wanted else 0
            self.by_idx[i] = f[:4] + (prio,)
        self._fill()

    def _prio_text(self, pos: int, idx: int) -> str:
        from .engine import order_priorities
        f = self.by_idx[idx]
        if f[4] == 0:
            return t("prio_skip")
        if not self.enable.isChecked():
            return t("prio_normal") if f[4] == 4 else t("prio_high") if f[4] > 4 else t("prio_low")
        prios = order_priorities(self.order, [self.by_idx[i][4] if i in self.by_idx else 0
                                              for i in range(max(self.by_idx) + 1)])
        return t("tp_level", prios[idx])

    def _fill(self):
        from .ui import fmt_size
        sel = self.tbl.currentRow()
        self.tbl.setRowCount(len(self.order))
        for pos, idx in enumerate(self.order):
            _, path, size, prog, prio = self.by_idx[idx]
            name = path.replace("\\", "/").split("/")[-1]
            vals = (str(pos + 1), path, fmt_size(size), f"{prog * 100:.1f}%", self._prio_text(pos, idx))
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if c == 1:
                    it.setIcon(widgets.file_icon(name))
                    it.setToolTip(path)
                if c in (2, 3):
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if prio == 0:
                    it.setForeground(widgets.QColor(C["secondary"]))
                self.tbl.setItem(pos, c, it)
        if 0 <= sel < len(self.order):
            self.tbl.selectRow(sel)

    def _move(self, step):
        if not self.enable.isChecked():
            return
        r = self.tbl.currentRow()
        if r < 0:
            return
        idx = self.order.pop(r)
        n = {"top": 0, "end": len(self.order)}.get(step, None)
        if n is None:
            n = max(0, min(len(self.order), r + step))
        self.order.insert(n, idx)
        self._fill()
        self.tbl.selectRow(n)

    def result(self) -> dict:
        return {"order": list(self.order) if self.enable.isChecked() else None}
