"""RSS / Atom feeds: reading, a feeds window and automatic downloading of matching items.

Feeds live in cfg["rss_feeds"] as dicts:
    {"name", "url", "enabled", "auto", "include", "exclude", "category", "primed"}
Items already handled are remembered in cfg["rss_seen"] (capped), so nothing is added twice.
"""
from __future__ import annotations

import os
import re
import tempfile
import time
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

from PySide6.QtCore import QThread, QTimer, Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QDialog, QFormLayout, QHBoxLayout, QHeaderView,
                               QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
                               QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from .i18n import t
from .widgets import icon

UA = "Mozilla/5.0 (compatible; ArtCore RSS)"
SEEN_CAP = 3000
MAX_PER_CHECK = 20      # a feed that suddenly posts hundreds of matching items never floods the client
MAX_RETRIES = 3         # a .torrent that cannot be downloaded is tried again at the next checks


# ── parsing ─────────────────────────────────────────────────────────────────
def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower() if isinstance(tag, str) else ""


def _text(el) -> str:
    return (el.text or "").strip() if el is not None else ""


def _pick_link(candidates: list[tuple[str, str]]) -> str:
    """candidates: (kind, url). A magnet or a .torrent enclosure wins over a plain page link."""
    for _, u in candidates:
        if u.startswith("magnet:"):
            return u
    for kind, u in candidates:
        if kind == "enclosure" and (u.lower().split("?")[0].endswith(".torrent") or "torrent" in u.lower()):
            return u
    for kind, u in candidates:
        if kind == "enclosure":
            return u
    for _, u in candidates:
        if u.lower().split("?")[0].endswith(".torrent"):
            return u
    return candidates[0][1] if candidates else ""


def _date(s: str) -> float:
    if not s:
        return 0.0
    try:
        return parsedate_to_datetime(s).timestamp()
    except Exception:
        pass
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}))?", s)
    if m:
        import datetime
        a = [int(x or 0) for x in m.groups()]
        try:
            return datetime.datetime(a[0], a[1], a[2], a[3], a[4]).timestamp()
        except ValueError:
            return 0.0
    return 0.0


def parse_feed(data: bytes) -> list[dict]:
    """RSS 2.0 and Atom → [{title, link, guid, date}] newest first as the feed gives them."""
    root = ET.fromstring(data)
    items = []
    for el in root.iter():
        name = _local(el.tag)
        if name not in ("item", "entry"):
            continue
        title = link_page = guid = date = ""
        cands: list[tuple[str, str]] = []
        for ch in el:
            n = _local(ch.tag)
            if n == "title":
                title = _text(ch)
            elif n == "guid" or n == "id":
                guid = _text(ch)
            elif n in ("pubdate", "updated", "published", "date"):
                date = date or _text(ch)
            elif n == "link":
                href = ch.get("href") or _text(ch)
                if href:
                    rel = (ch.get("rel") or "").lower()
                    cands.append(("enclosure" if rel == "enclosure" else "link", href.strip()))
                    link_page = link_page or href.strip()
            elif n == "enclosure":
                u = ch.get("url") or ""
                if u:
                    cands.append(("enclosure", u.strip()))
            elif n in ("magneturi", "magnet"):
                if _text(ch).startswith("magnet:"):
                    cands.append(("magnet", _text(ch)))
            elif n in ("description", "summary", "content"):
                m = re.search(r"(magnet:\?[^\s\"'<>]+)", (ch.text or "").replace("&amp;", "&"))
                if m:
                    cands.append(("magnet", m.group(1)))
        if guid.startswith("magnet:"):
            cands.append(("magnet", guid))
        link = _pick_link(cands)
        if not title and not link:
            continue
        items.append({"title": title or link, "link": link, "guid": guid or link or title,
                      "date": _date(date), "page": link_page})
    return items


def matches(title: str, include: str, exclude: str) -> bool:
    """Empty include = everything; regular expressions, case-insensitive; a bad pattern never matches."""
    try:
        if include.strip() and not re.search(include, title, re.I):
            return False
        if exclude.strip() and re.search(exclude, title, re.I):
            return False
    except re.error:
        return False
    return True


# ── network, off the GUI thread ─────────────────────────────────────────────
class FetchThread(QThread):
    """kind "feed": result is a list of items; kind "file": result is a path of a downloaded .torrent."""
    done = Signal(object, object, str)          # tag, result, error

    def __init__(self, tag, url: str, kind: str = "feed", parent=None):
        super().__init__(parent)
        self.tag, self.url, self.kind = tag, url, kind

    def run(self):
        try:
            if not self.url.lower().startswith(("http://", "https://")):      # never file:// or ftp://
                raise ValueError("only http:// and https:// addresses are allowed")
            req = urllib.request.Request(self.url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=20) as r:
                data = r.read(8 * 1024 * 1024)
            if self.kind == "feed":
                self.done.emit(self.tag, parse_feed(data), "")
            else:
                if not data.startswith(b"d"):
                    raise ValueError("not a .torrent file")
                fd, path = tempfile.mkstemp(suffix=".torrent", prefix="rss_")
                with os.fdopen(fd, "wb") as f:
                    f.write(data)
                self.done.emit(self.tag, path, "")
        except Exception as e:          # network, XML, anything: shown to the person, never raised
            self.done.emit(self.tag, None, str(e) or e.__class__.__name__)


# ── automatic downloading ───────────────────────────────────────────────────
class RssManager:
    """Checks the feeds on a timer and adds new matching items without asking."""

    def __init__(self, win):
        self.win = win
        self._threads: list[FetchThread] = []
        self._quiet = False
        self._pending: set[str] = set()        # .torrent downloads in flight (not "seen" until they arrive)
        self._fails: dict[str, int] = {}
        self._last = 0.0
        self.timer = QTimer(win)
        self.timer.timeout.connect(self._tick)
        self.timer.start(30_000)

    @property
    def cfg(self) -> dict:
        return self.win.cfg

    def _tick(self) -> None:
        if not self.cfg.get("rss_enabled"):
            return
        minutes = max(5, int(self.cfg.get("rss_interval", 30)))
        if time.time() - self._last >= minutes * 60:
            self.check_now()

    def check_now(self) -> None:
        self._last = time.time()
        for feed in self.cfg.get("rss_feeds", []):
            if feed.get("enabled", True) and feed.get("auto") and feed.get("url"):
                self._start(FetchThread(("auto", feed["url"]), feed["url"], "feed"), self._feed_done)

    def _start(self, th: FetchThread, slot) -> None:
        th.done.connect(slot)
        th.finished.connect(lambda th=th: self._threads.remove(th) if th in self._threads else None)
        self._threads.append(th)
        th.start()

    def _feed_done(self, tag, items, err) -> None:
        feed = next((f for f in self.cfg.get("rss_feeds", []) if f.get("url") == tag[1]), None)
        if feed is None or err or items is None:
            return
        seen = self.cfg.setdefault("rss_seen", [])
        seen_set = set(seen)
        if not feed.get("primed"):
            # first look at a feed: what is already there is the backlog, not "new" — remember, don't download
            feed["primed"] = True
            for it in items:
                if it["guid"] not in seen_set:
                    seen.append(it["guid"])
            self._trim()
            self.win.save_config()
            return
        todo = [it for it in items
                if it["guid"] not in seen_set and it["guid"] not in self._pending and it["link"]
                and matches(it["title"], feed.get("include", ""), feed.get("exclude", ""))]
        capped = len(todo) > MAX_PER_CHECK
        todo = todo[:MAX_PER_CHECK]                    # the rest is picked up at the next check
        self._quiet = len(todo) > 3                    # a pile of items gets one summary, not a toast each
        try:
            for it in todo:
                g = it["guid"]
                if it["link"].lower().startswith(("http://", "https://")):
                    self._pending.add(g)               # "seen" only once the .torrent has really arrived
                else:
                    seen.append(g); seen_set.add(g)    # magnet (added at once) or a link we cannot use
                self._add(it, feed)
        finally:
            self._quiet = False
        if len(todo) > 3:
            self.win.toast("success", t("rss_title"), t("rss_added_n", len(todo)))
        if capped:
            self.win.toast("info", t("rss_title"), t("rss_capped", len(todo)))
        self._trim()
        self.win.save_config()

    def _trim(self) -> None:
        seen = self.cfg.get("rss_seen", [])
        if len(seen) > SEEN_CAP:
            del seen[:len(seen) - SEEN_CAP]

    def _add(self, it: dict, feed: dict) -> None:
        link = it["link"]
        if link.startswith("magnet:"):
            self._added(self.win.engine.add_magnet(link), it, feed)
        elif link.lower().startswith(("http://", "https://")):
            self._start(FetchThread((it, feed), link, "file"), self._file_done)

    def _mark_seen(self, guid: str) -> None:
        seen = self.cfg.setdefault("rss_seen", [])
        if guid not in seen:
            seen.append(guid)
        self._trim()
        self.win.save_config()

    def _failed(self, it: dict, err) -> None:
        """Not downloaded: stays unseen (so the next check tries again) until MAX_RETRIES is used up."""
        g = it["guid"]
        self._pending.discard(g)
        self._fails[g] = self._fails.get(g, 0) + 1
        if self._fails[g] >= MAX_RETRIES:
            self._mark_seen(g)
            self._fails.pop(g, None)
            self.win.toast("error", t("rss_title"), t("rss_gave_up", MAX_RETRIES, f"{it['title'][:60]}: {err}"))
        else:
            self.win.toast("error", t("rss_title"), t("rss_err", f"{it['title'][:60]}: {err}"))

    def _file_done(self, tag, path, err) -> None:
        it, feed = tag
        if err or not path:
            self._failed(it, err)
            return
        try:
            key = self.win.engine.add_torrent_file(path)
        except Exception as e:
            self._failed(it, e)
            return
        finally:
            try:
                os.remove(path)
            except OSError:
                pass
        self._pending.discard(it["guid"])
        self._fails.pop(it["guid"], None)
        self._mark_seen(it["guid"])
        self._added(key, it, feed)

    def _added(self, key: str, it: dict, feed: dict) -> None:
        if feed.get("category"):
            try:
                self.win.engine.set_category(key, feed["category"])
            except Exception:
                pass
        if not self._quiet:
            self.win.toast("success", t("rss_title"), t("rss_added", it["title"][:80]))
        self.win.tick()


# ── windows ─────────────────────────────────────────────────────────────────
class FeedEdit(QDialog):
    def __init__(self, parent, categories: list[str], feed: dict | None = None):
        super().__init__(parent)
        self.setWindowTitle(t("rss_edit"))
        self.setMinimumWidth(520)
        f = feed or {}
        form = QFormLayout(self)
        self.name = QLineEdit(f.get("name", ""))
        self.url = QLineEdit(f.get("url", ""))
        self.url.setPlaceholderText("https://")
        self.include = QLineEdit(f.get("include", ""))
        self.exclude = QLineEdit(f.get("exclude", ""))
        self.auto = QCheckBox(t("rss_auto")); self.auto.setChecked(bool(f.get("auto", False)))
        self.enabled = QCheckBox(t("rss_enabled")); self.enabled.setChecked(bool(f.get("enabled", True)))
        self.category = QLineEdit(f.get("category", ""))
        self.category.setPlaceholderText(", ".join(categories[:4]))
        form.addRow(t("rss_name"), self.name)
        form.addRow(t("rss_url"), self.url)
        form.addRow(t("rss_include"), self.include)
        form.addRow(t("rss_exclude"), self.exclude)
        form.addRow(t("rss_category"), self.category)
        form.addRow(self.enabled)
        form.addRow(self.auto)
        hint = QLabel(t("rss_hint")); hint.setObjectName("meta"); hint.setWordWrap(True)
        form.addRow(hint)
        row = QHBoxLayout(); row.addStretch()
        ok = QPushButton(t("ok")); ok.setDefault(True); ok.clicked.connect(self._accept)
        cancel = QPushButton(t("cancel")); cancel.clicked.connect(self.reject)
        row.addWidget(ok); row.addWidget(cancel)
        form.addRow(row)

    def _accept(self) -> None:
        url = self.url.text().strip()
        if not url.lower().startswith(("http://", "https://")):
            QMessageBox.warning(self, t("rss_title"), t("rss_bad_url"))
            return
        for pat in (self.include.text(), self.exclude.text()):
            try:
                re.compile(pat)
            except re.error as e:
                QMessageBox.warning(self, t("rss_title"), t("rss_bad_regex", e))
                return
        self.accept()

    def result_feed(self, old: dict | None) -> dict:
        d = dict(old or {})
        d.update(name=self.name.text().strip() or self.url.text().strip(), url=self.url.text().strip(),
                 include=self.include.text().strip(), exclude=self.exclude.text().strip(),
                 category=self.category.text().strip(), auto=self.auto.isChecked(),
                 enabled=self.enabled.isChecked())
        if d["url"] != (old or {}).get("url"):
            d["primed"] = False
        return d


class RssDialog(QDialog):
    """Feeds on the left, what the chosen feed contains on the right; double click adds an item."""

    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.setWindowTitle(t("rss_title"))
        self.resize(980, 600)
        self._threads: list[FetchThread] = []
        self._items: list[dict] = []
        lay = QVBoxLayout(self)
        split = QSplitter(Qt.Horizontal)
        lay.addWidget(split, 1)

        left = QWidget(); ll = QVBoxLayout(left); ll.setContentsMargins(0, 0, 6, 0)
        self.feeds = QListWidget()
        self.feeds.currentRowChanged.connect(lambda _r: self.refresh())
        ll.addWidget(self.feeds, 1)
        row = QHBoxLayout()
        for key, ic, fn in (("rss_add", "fa6s.plus", self.add_feed), ("rss_edit", "fa6s.pencil", self.edit_feed),
                            ("rss_remove", "fa6s.trash-can", self.remove_feed)):
            b = QPushButton(icon(ic), ""); b.setToolTip(t(key)); b.clicked.connect(lambda _=False, f=fn: f())
            row.addWidget(b)
        row.addStretch()
        ll.addLayout(row)
        split.addWidget(left)

        right = QWidget(); rl = QVBoxLayout(right); rl.setContentsMargins(6, 0, 0, 0)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels([t("rss_item"), t("rss_date"), t("rss_match")])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().hide()
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        hh.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.doubleClicked.connect(lambda _i: self.download())
        rl.addWidget(self.table, 1)
        self.status = QLabel(""); self.status.setObjectName("meta")
        rl.addWidget(self.status)
        btns = QHBoxLayout()
        self.btn_refresh = QPushButton(icon("fa6s.arrows-rotate"), " " + t("rss_refresh"))
        self.btn_refresh.clicked.connect(self.refresh)
        self.btn_get = QPushButton(icon("fa6s.download", "success"), " " + t("rss_download"))
        self.btn_get.clicked.connect(self.download)
        close = QPushButton(t("close")); close.clicked.connect(self.accept)
        btns.addWidget(self.btn_refresh); btns.addWidget(self.btn_get); btns.addStretch(); btns.addWidget(close)
        rl.addLayout(btns)
        split.addWidget(right)
        split.setSizes([260, 700])
        self._fill_feeds()

    # feeds list
    @property
    def feed_list(self) -> list[dict]:
        return self.win.cfg.setdefault("rss_feeds", [])

    def _fill_feeds(self, select: int = 0) -> None:
        self.feeds.blockSignals(True)
        self.feeds.clear()
        for f in self.feed_list:
            it = QListWidgetItem(icon("fa6s.rss", "orange" if f.get("enabled", True) else "secondary"), f["name"])
            it.setToolTip(f["url"])
            self.feeds.addItem(it)
        self.feeds.blockSignals(False)
        if self.feed_list:
            self.feeds.setCurrentRow(min(select, len(self.feed_list) - 1))
        self.btn_refresh.setEnabled(bool(self.feed_list))
        self.btn_get.setEnabled(bool(self.feed_list))
        if not self.feed_list:
            self.table.setRowCount(0)
            self.status.setText(t("rss_empty"))

    def _categories(self) -> list[str]:
        return [c["name"] for c in self.win.cfg.get("categories", [])]

    def add_feed(self) -> None:
        d = FeedEdit(self, self._categories())
        if d.exec():
            self.feed_list.append(d.result_feed(None))
            self.win.save_config()
            self._fill_feeds(len(self.feed_list) - 1)

    def edit_feed(self) -> None:
        r = self.feeds.currentRow()
        if r < 0:
            return
        d = FeedEdit(self, self._categories(), self.feed_list[r])
        if d.exec():
            self.feed_list[r] = d.result_feed(self.feed_list[r])
            self.win.save_config()
            self._fill_feeds(r)

    def remove_feed(self) -> None:
        r = self.feeds.currentRow()
        if r >= 0:
            del self.feed_list[r]
            self.win.save_config()
            self._fill_feeds(max(0, r - 1))

    # items
    def refresh(self) -> None:
        r = self.feeds.currentRow()
        if r < 0 or r >= len(self.feed_list):
            return
        feed = self.feed_list[r]
        self.status.setText(t("rss_loading"))
        self.table.setRowCount(0)
        th = FetchThread(feed["url"], feed["url"], "feed", self)
        th.done.connect(self._loaded)
        th.finished.connect(lambda th=th: self._threads.remove(th) if th in self._threads else None)
        self._threads.append(th)
        th.start()

    def _loaded(self, tag, items, err) -> None:
        r = self.feeds.currentRow()
        if r < 0 or self.feed_list[r]["url"] != tag:
            return                                  # another feed was chosen meanwhile
        if err:
            self.status.setText(t("rss_err", err))
            return
        feed = self.feed_list[r]
        self._items = items
        import datetime
        self.table.setRowCount(len(items))
        for i, it in enumerate(items):
            ok = matches(it["title"], feed.get("include", ""), feed.get("exclude", ""))
            when = datetime.datetime.fromtimestamp(it["date"]).strftime("%d.%m.%Y %H:%M") if it["date"] else ""
            for c, txt in enumerate((it["title"], when, "✓" if ok and (feed.get("include") or feed.get("exclude")) else "")):
                cell = QTableWidgetItem(txt)
                cell.setToolTip(it["link"])
                self.table.setItem(i, c, cell)
        self.status.setText(t("rss_count", len(items)))

    def download(self) -> None:
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        links = [self._items[r]["link"] for r in rows if r < len(self._items) and self._items[r]["link"]]
        if not links:
            return
        seen = self.win.cfg.setdefault("rss_seen", [])
        for r in rows:
            g = self._items[r]["guid"]
            if g not in seen:
                seen.append(g)
        self.win.save_config()
        self.win.add_items(links)
