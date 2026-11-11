"""Qt (PySide6) user interface in the ArtCore style."""
from __future__ import annotations

import os
import shutil
from datetime import datetime

import qtawesome as qta
from PySide6.QtCore import (Qt, QAbstractTableModel, QModelIndex, QTimer, QUrl, QThread,
                            Signal, QRectF, QSortFilterProxyModel, QSize)
from PySide6.QtGui import (QAction, QColor, QDesktopServices, QFont, QIcon, QPainter, QPixmap,
                           QPainterPath, QKeySequence, QGuiApplication, QFontMetrics)
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QButtonGroup, QCheckBox,
                               QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
                               QFrame, QGridLayout, QHBoxLayout, QHeaderView, QInputDialog, QLabel,
                               QLineEdit, QMainWindow, QMenu, QMessageBox, QPlainTextEdit,
                               QPushButton, QSpinBox, QSplitter, QStackedWidget, QStyle,
                               QStyledItemDelegate, QSystemTrayIcon, QTableView, QTableWidget,
                               QTableWidgetItem, QTabWidget, QToolButton, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget)

from . import APP_TITLE, VERSION, config, theme, winint
from .engine import (Engine, TorrentRow, create_torrent, torrent_files,
                     PRIO_SKIP, PRIO_NORMAL, PRIO_HIGH)
from .i18n import t

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
        "col_seeds", "col_peers", "col_ratio", "col_eta", "col_tracker"]
(C_NAME, C_SIZE, C_PROG, C_STATE, C_DOWN, C_UP, C_SEEDS, C_PEERS,
 C_RATIO, C_ETA, C_TRACKER) = range(len(COLS))

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
}
FILTER_STATES = {
    "all":         None,
    "downloading": {"downloading", "metadata", "checking"},
    "seeding":     {"seeding", "finished"},
    "paused":      {"paused", "queued"},
    "error":       {"error"},
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
            }[c]
        if role == Qt.UserRole:  # sort key
            return {
                C_NAME: r.name.lower(), C_SIZE: r.size, C_PROG: r.progress, C_STATE: r.state,
                C_DOWN: r.down_rate, C_UP: r.up_rate, C_SEEDS: r.seeds, C_PEERS: r.peers,
                C_RATIO: r.ratio if r.ratio != float("inf") else 1e18,
                C_ETA: r.eta if r.eta >= 0 else 1e18, C_TRACKER: r.tracker_host,
            }[c]
        if role == Qt.ForegroundRole:
            dark = theme.current_name == "dark"
            if c == C_DOWN and r.down_rate > 0:
                return QColor(C["primary_text"] if dark else C["primary"])
            if c == C_UP and r.up_rate > 0:
                return QColor(C["success_text"] if dark else C["success"])
            if c in (C_TRACKER, C_ETA, C_SIZE):
                return QColor(C["secondary"])
        if role == Qt.FontRole and c == C_NAME:
            f = QFont()
            f.setWeight(QFont.DemiBold)
            return f
        if role == Qt.ToolTipRole:
            return r.error or (r.name if c == C_NAME else None)
        if role == Qt.TextAlignmentRole and c not in (C_NAME, C_STATE, C_TRACKER):
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
        self.text = ""
        self.setSortRole(Qt.UserRole)

    def set_filter(self, state=None, tracker=None, text=None):
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
                   "secondary" if r.state in ("paused", "queued") else "primary")
            painter.setBrush(QColor(C[key]))
            painter.drawRoundedRect(QRectF(bar.left(), bar.top(), max(6.0, bar.width() * r.progress), 6), 3, 3)
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
        w = QFontMetrics(f).horizontalAdvance(text) + 20
        pill = QRectF(option.rect.left() + 6, option.rect.center().y() - 10, w, 20)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(C[bg]))
        painter.drawRoundedRect(pill, 10, 10)
        painter.setPen(QColor(C[fg]))
        painter.drawText(pill, Qt.AlignCenter, text)
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
    """Gray KPI tile with a faint corner icon (.st-kpi--muted)."""
    def __init__(self, icon_name: str):
        super().__init__()
        self.setObjectName("kpi")
        self.icon_name = icon_name
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 10, 12, 10)
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
        c = QColor(C["secondary"])
        c.setAlpha(70)
        self.ico.setPixmap(qta.icon(self.icon_name, color=c).pixmap(34, 34))

    def set(self, val: str, lbl: str):
        self.val.setText(val)
        self.lbl.setText(lbl)


# ============================================================
#  Dialogs
# ============================================================

def _section(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setStyleSheet(f'font-family:"{theme.TITLE_FONT}"; font-size: 12.5pt; font-weight: 600; '
                      f'color:{C["emphasis"]}; padding-top: 8px;')
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
        form = QFormLayout(self)
        form.setContentsMargins(20, 12, 20, 16)
        form.setVerticalSpacing(8)

        def spin(val, lo, hi):
            s = QSpinBox()
            s.setRange(lo, hi)
            s.setValue(int(val))
            return s

        # ── general
        form.addRow(_section(t("set_grp_general")))
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
        self.lang = QComboBox(); self.lang.addItems(["Русский", "English"])
        self.lang.setCurrentIndex(1 if cfg.get("language") == "en" else 0)
        form.addRow(t("set_lang"), self.lang)
        self.tray = QCheckBox(t("set_tray")); self.tray.setChecked(cfg["minimize_to_tray"])
        form.addRow(self.tray)
        self.show_add = QCheckBox(t("set_show_add")); self.show_add.setChecked(cfg.get("show_add_dialog", True))
        form.addRow(self.show_add)

        # ── connection
        form.addRow(_section(t("set_grp_conn")))
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
        self.dht  = QCheckBox(t("set_dht"));  self.dht.setChecked(cfg["dht"])
        self.upnp = QCheckBox(t("set_upnp")); self.upnp.setChecked(cfg["upnp"])
        form.addRow(self.dht)
        form.addRow(self.upnp)

        # ── windows
        self.autostart = None
        if winint.IS_WIN:
            form.addRow(_section(t("set_grp_windows")))
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
        form.addRow(bb)

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, t("choose_folder"), self.path.text())
        if d:
            self.path.setText(d)

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
                 show_add_dialog=self.show_add.isChecked(),
                 theme=("system", "light", "dark")[self.theme.currentIndex()],
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
    def __init__(self, cfg: dict, name: str, ti=None, parent=None):
        super().__init__(parent)
        _theme_title_bar(self)
        self.setWindowTitle(t("ad_title"))
        self.setMinimumWidth(680)
        self.ti = ti
        self.leaves: list[tuple[QTreeWidgetItem, int, int]] = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 14, 20, 16)
        lay.setSpacing(10)

        head = _section(name)
        head.setWordWrap(True)
        lay.addWidget(head)

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

        self.bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.ok = self.bb.button(QDialogButtonBox.Ok)
        self.ok.setText(t("add_btn"))
        self.ok.setObjectName("primary")
        self.bb.accepted.connect(self.accept)
        self.bb.rejected.connect(self.reject)
        lay.addWidget(self.bb)
        self._update()

    def _fill(self, files):
        folders: dict[tuple, QTreeWidgetItem] = {}
        ic_dir, ic_file = icon("fa6s.folder", "warning"), icon("fa6s.file")
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
            leaf.setIcon(0, ic_file)
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


class HashThread(QThread):
    done = Signal(str, str)   # out_path, error

    def __init__(self, src, announce, out, private, comment):
        super().__init__()
        self.args = (src, announce, out, private, comment)

    def run(self):
        try:
            self.done.emit(create_torrent(*self.args), "")
        except Exception as e:
            self.done.emit("", str(e))


class CreateTorrentDialog(QDialog):
    def __init__(self, cfg: dict, parent=None):
        super().__init__(parent)
        _theme_title_bar(self)
        self.setWindowTitle(t("ct_title"))
        self.setMinimumWidth(600)
        self.cfg = cfg
        self.out_path = ""
        self.src_path = ""
        form = QFormLayout(self)
        form.setContentsMargins(20, 12, 20, 16)
        form.addRow(_section(t("ct_title")))

        self.src = QLineEdit()
        bf = QPushButton(t("ct_file"));   bf.clicked.connect(self._pick_file)
        bd = QPushButton(t("ct_folder")); bd.clicked.connect(self._pick_dir)
        row = QHBoxLayout(); row.addWidget(self.src); row.addWidget(bf); row.addWidget(bd)
        form.addRow(t("ct_source"), row)

        self.announce = QLineEdit(cfg.get("announce_url", ""))
        self.announce.setPlaceholderText("https://tracker.example/announce.php?passkey=…")
        form.addRow(t("ct_announce"), self.announce)
        self.comment = QLineEdit()
        form.addRow(t("ct_comment"), self.comment)
        self.private = QCheckBox(t("ct_private")); self.private.setChecked(True)
        form.addRow(self.private)
        self.seed = QCheckBox(t("ct_seed")); self.seed.setChecked(True)
        form.addRow(self.seed)

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
        self.cfg["announce_url"] = self.announce.text().strip()
        self.bb.setEnabled(False)
        self.setWindowTitle(t("ct_working"))
        self.src_path = src
        self.th = HashThread(src, self.announce.text().strip(), out, self.private.isChecked(),
                             self.comment.text().strip())
        self.th.done.connect(self._finished)
        self.th.start()

    def _finished(self, out, err):
        self.bb.setEnabled(True)
        self.setWindowTitle(t("ct_title"))
        if err:
            QMessageBox.warning(self, t("ct_title"), err)
            return
        self.out_path = out
        QMessageBox.information(self, t("ct_title"), t("ct_done", out))
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
        self._session_up0 = engine.ses.status().total_payload_upload

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
        split.setChildrenCollapsible(False)
        split.addWidget(self._build_list())
        split.addWidget(self._build_details())
        split.setSizes([470, 210])
        root.addWidget(split, 1)

        self._build_tray()
        self.retheme()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(1000)
        self.tick()

    # ── building blocks ────────────────────────────────────────
    def _themed_icon(self, setter, name, key="secondary"):
        self._themed.append((setter, name, key))

    def _build_header(self) -> QFrame:
        h = card()
        lay = QHBoxLayout(h)
        lay.setContentsMargins(18, 14, 14, 14)
        lay.setSpacing(14)
        self.head_icon = QLabel()
        self.head_icon.setObjectName("headIcon")
        self.head_icon.setFixedSize(50, 50)
        self.head_icon.setAlignment(Qt.AlignCenter)
        self._themed_icon(lambda ic: self.head_icon.setPixmap(ic.pixmap(24, 24)),
                          "fa6s.cloud-arrow-down", "primary_text")
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
            self._themed_icon(b.setIcon, name)
            lay.addWidget(b)
            return b

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
        btn("add_magnet", "fa6s.magnet", self.add_magnet_dialog, shortcut="Ctrl+U")
        btn("create_torrent", "fa6s.file-circle-plus", self.create_torrent_dialog, shortcut="Ctrl+N")
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
        self.k_down = Kpi("fa6s.arrow-down")
        self.k_up = Kpi("fa6s.arrow-up")
        self.k_active = Kpi("fa6s.bolt")
        self.k_session = Kpi("fa6s.chart-line")
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
        for key in FILTER_STATES:
            b = QPushButton(t("f_" + key))
            b.setObjectName("pill")
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
        for c, w in ((C_SIZE, 90), (C_PROG, 150), (C_STATE, 120), (C_DOWN, 95), (C_UP, 95),
                     (C_SEEDS, 55), (C_PEERS, 55), (C_RATIO, 65), (C_ETA, 80), (C_TRACKER, 150)):
            self.table.setColumnWidth(c, w)
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
        keys = ["gen_path", "gen_size", "gen_downloaded", "gen_uploaded", "gen_ratio", "gen_tracker", "gen_error"]
        for i, k in enumerate(keys):
            lab = QLabel(t(k)); lab.setObjectName("rowLbl")
            val = QLabel(); val.setObjectName("rowVal")
            val.setTextInteractionFlags(Qt.TextSelectableByMouse)
            r, c = (i, 0) if i < 4 else (i - 4, 2)
            grid.addWidget(lab, r, c)
            grid.addWidget(val, r, c + 1)
            self.g[k] = val
        grid.setColumnStretch(1, 2)
        grid.setColumnStretch(3, 2)
        grid.setRowStretch(4, 1)
        self.tabs.addTab(gen, t("tab_general"))

        self.files_tbl = self._mk_table(["file_name", "col_size", "col_progress", "file_prio"], stretch=0)
        self.files_tbl.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.files_tbl.setContextMenuPolicy(Qt.CustomContextMenu)
        self.files_tbl.customContextMenuRequested.connect(self.files_menu)
        for c, w in ((1, 100), (2, 90), (3, 120)):
            self.files_tbl.setColumnWidth(c, w)
        self.tabs.addTab(self.files_tbl, t("tab_files"))

        self.peers_tbl = self._mk_table(["peer_ip", "peer_client", "col_progress", "col_down", "col_up"], stretch=1)
        self.tabs.addTab(self.peers_tbl, t("tab_peers"))
        self.tr_tbl = self._mk_table(["tr_url", "tr_tier", "tr_msg"], stretch=0)
        self.tabs.addTab(self.tr_tbl, t("tab_trackers"))
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.tabs.addTab(self.log, t("tab_log"))
        for i, name in enumerate(("fa6s.circle-info", "fa6s.folder-tree", "fa6s.users", "fa6s.server", "fa6s.list")):
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

    def _build_tray(self):
        self.tray = QSystemTrayIcon(app_icon(), self)
        menu = QMenu()
        menu.addAction(t("show"), self.show_normal)
        menu.addAction(t("add_torrent"), self.add_torrent_dialog)
        menu.addSeparator()
        menu.addAction(t("quit"), self.quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda r: self.show_normal() if r == QSystemTrayIcon.Trigger else None)
        self.tray.show()

    # ── theme ──────────────────────────────────────────────────
    def retheme(self):
        for setter, name, key in self._themed:
            setter(icon(name, key))
        for k in (self.k_down, self.k_up, self.k_active, self.k_session):
            k.retheme()
        self.empty_ico.setStyleSheet(f"background:{C['tertiary_bg']}; border-radius: 16px;")
        dark = theme.current_name == "dark"
        self.theme_btn.setIcon(icon("fa6s.sun" if dark else "fa6s.moon"))
        winint.set_title_bar(int(self.winId()), dark, C["page_bg"], C["emphasis"])
        self.table.viewport().update()

    def toggle_theme(self):
        self.cfg["theme"] = "light" if theme.current_name == "dark" else "dark"
        config.save(self.cfg)
        theme.apply(QApplication.instance(), self.cfg["theme"])
        self.retheme()

    # ── helpers ────────────────────────────────────────────────
    def selected_keys(self) -> list[str]:
        return [_row_of(idx).key for idx in self.table.selectionModel().selectedRows()]

    def _each(self, fn):
        for k in self.selected_keys():
            fn(k)
        self.tick()

    def log_line(self, s: str):
        self.log.appendPlainText(f"[{datetime.now():%H:%M:%S}] {s}")

    # ── adding ─────────────────────────────────────────────────
    def add_item(self, item: str):
        """Path to .torrent or magnet URI (dialog, drag & drop, command line, Explorer)."""
        import libtorrent as lt
        is_magnet = item.startswith("magnet:")
        try:
            save_path, paused, prios = None, False, None
            if self.cfg.get("show_add_dialog", True):
                if is_magnet:
                    name = lt.parse_magnet_uri(item).name or item[:80]
                    d = AddTorrentDialog(self.cfg, name, None, self)
                else:
                    ti = lt.torrent_info(item)
                    d = AddTorrentDialog(self.cfg, ti.name(), ti, self)
                self.show_normal()
                if not d.exec():
                    return
                save_path, paused, prios = d.path.text().strip() or None, d.paused.isChecked(), d.priorities()
            if is_magnet:
                self.engine.add_magnet(item, save_path=save_path, paused=paused)
            else:
                self.engine.add_torrent_file(item, save_path=save_path, paused=paused, file_priorities=prios)
            self.log_line(item if is_magnet else os.path.basename(item))
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
        box = QMessageBox(QMessageBox.Question, t("remove_title"), t("remove_text", len(keys)),
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
        m.addSeparator()
        m.addAction(icon("fa6s.trash-can", "danger"), t("remove"), self.remove_selected)
        m.exec(self.table.viewport().mapToGlobal(pos))

    def settings_dialog(self):
        d = SettingsDialog(self.cfg, self)
        if d.exec():
            old_theme = self.cfg.get("theme")
            self.cfg.update(d.result_cfg())
            config.save(self.cfg)
            self.engine.apply_settings(self.cfg)
            if self.cfg.get("theme") != old_theme:
                theme.apply(QApplication.instance(), self.cfg["theme"])
                self.retheme()

    # ── refresh ────────────────────────────────────────────────
    def tick(self):
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
            self.log_line(self.engine.messages.pop(0))
        while self.engine.finished_names:
            name = self.engine.finished_names.pop(0)
            self.log_line(t("finished_note", name))
            if self.tray.isVisible():
                self.tray.showMessage(APP_TITLE, t("finished_note", name), app_icon(), 5000)

        st = self.engine.ses.status()
        down, up = st.payload_download_rate, st.payload_upload_rate
        active = sum(1 for r in rows if r.state in ("downloading", "seeding", "metadata", "checking"))
        self.k_down.set(fmt_size(down) + "/s", t("kpi_down"))
        self.k_up.set(fmt_size(up) + "/s", t("kpi_up"))
        self.k_active.set(str(active), t("kpi_active", len(rows)))
        self.k_session.set(fmt_size(st.total_payload_upload - self._session_up0), t("kpi_session"))

        dht = t("meta_dht", st.dht_nodes) if self.cfg.get("dht") else t("meta_dht_off")
        dot = f'<span style="color:{C["border"]}">&nbsp;•&nbsp;</span>'
        self.meta.setText(dot.join([t("subtitle"), t("meta_version", VERSION),
                                    t("meta_port", self.engine.ses.listen_port()), dht]))
        self.tray.setToolTip(f"{APP_TITLE}\n↓ {fmt_size(down)}/s  ↑ {fmt_size(up)}/s")
        self.refresh_details()

    def refresh_details(self):
        keys = self.selected_keys()
        row = next((r for r in self.model.rows if keys and r.key == keys[0]), None)
        if row is None:
            for lab in self.g.values():
                lab.setText("—")
            self.peers_tbl.setRowCount(0)
            self.tr_tbl.setRowCount(0)
            self.files_tbl.setRowCount(0)
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

        tab = self.tabs.currentIndex()
        if tab == 1:
            self.refresh_files(row.key)
        elif tab == 2:
            peers = self.engine.peers(row.key)
            self.peers_tbl.setRowCount(len(peers))
            for i, p in enumerate(peers):
                for j, v in enumerate((p["ip"], p["client"], f"{p['progress'] * 100:.1f}%",
                                       fmt_speed(p["down"]), fmt_speed(p["up"]))):
                    self.peers_tbl.setItem(i, j, QTableWidgetItem(v))
        elif tab == 3:
            self.tr_tbl.setRowCount(len(trs))
            for i, tr in enumerate(trs):
                for j, v in enumerate((tr["url"], str(tr["tier"]), tr["message"])):
                    self.tr_tbl.setItem(i, j, QTableWidgetItem(v))

    # ── files tab ──────────────────────────────────────────────
    PRIO_TEXT = {PRIO_SKIP: "prio_skip", PRIO_NORMAL: "prio_normal", PRIO_HIGH: "prio_high"}

    def refresh_files(self, key: str):
        files = self.engine.files(key)
        tbl = self.files_tbl
        if not files:
            tbl.setRowCount(1)
            for j, v in enumerate((t("files_wait"), "", "", "")):
                tbl.setItem(0, j, QTableWidgetItem(v))
            return
        # keep the same items (and selection) between 1 s refreshes
        same = (tbl.rowCount() == len(files) and tbl.item(0, 0) is not None
                and tbl.item(0, 0).data(Qt.UserRole) == (key, files[0]["index"]))
        if not same:
            tbl.setRowCount(len(files))
        ic = icon("fa6s.file")
        for i, f in enumerate(files):
            prio = f["priority"]
            label = t(self.PRIO_TEXT.get(prio, "prio_high" if prio > PRIO_NORMAL else "prio_normal"))
            vals = (f["path"], fmt_size(f["size"]), f"{f['progress'] * 100:.1f}%", label)
            for j, v in enumerate(vals):
                it = tbl.item(i, j) if same else None
                if it is None:
                    it = QTableWidgetItem()
                    if j == 0:
                        it.setIcon(ic)
                    if j in (1, 2):
                        it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    tbl.setItem(i, j, it)
                it.setText(v)
                it.setData(Qt.UserRole, (key, f["index"]))
                skip = prio == PRIO_SKIP
                it.setForeground(QColor(C["secondary"]) if skip else
                                 QColor(C["primary_text"] if theme.current_name == "dark" else C["primary"])
                                 if (j == 3 and prio > PRIO_NORMAL) else QColor(C["body"]))

    def files_menu(self, pos):
        rows = sorted({i.row() for i in self.files_tbl.selectedItems()})
        data = [self.files_tbl.item(r, 0).data(Qt.UserRole) for r in rows if self.files_tbl.item(r, 0)]
        data = [d for d in data if d]
        if not data:
            return
        key = data[0][0]
        idxs = [d[1] for d in data]
        m = QMenu(self)
        for prio, name, col in ((PRIO_HIGH, "fa6s.angles-up", "primary"),
                                (PRIO_NORMAL, "fa6s.minus", "secondary"),
                                (PRIO_SKIP, "fa6s.ban", "danger")):
            m.addAction(icon(name, col), t(self.PRIO_TEXT[prio]),
                        lambda p=prio: (self.engine.set_file_priority(key, idxs, p), self.refresh_files(key)))
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

    def quit(self):
        if self._quitting:
            return
        self._quitting = True
        self.timer.stop()
        self.tray.hide()
        self.engine.shutdown()
        QApplication.quit()
