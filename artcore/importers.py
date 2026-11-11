"""Import torrents from other clients.

qBittorrent: BT_backup/<hash>.fastresume (+ <hash>.torrent) — libtorrent's own resume
format, so progress carries over without re-checking.
BitComet: Downloads.xml (undocumented) + torrents/*.torrent — parsed tolerantly; files are
re-checked on import because BitComet's progress format is proprietary.
"""
from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

import libtorrent as lt


@dataclass
class Candidate:
    name: str
    size: int
    save_path: str
    category: str = ""
    torrent_file: str = ""          # .torrent path (may be empty for qBittorrent magnets)
    resume_file: str = ""           # qBittorrent .fastresume
    info_hash: str = ""
    paused: bool = False
    note: str = ""                  # "" = OK, "magnet", "nopath" or "error:<text>"
    extra: dict = field(default_factory=dict)


# ── default locations ───────────────────────────────────────────
def qbittorrent_dirs() -> list[str]:
    out = []
    local = os.environ.get("LOCALAPPDATA")
    if local:
        out.append(os.path.join(local, "qBittorrent", "BT_backup"))
    home = os.path.expanduser("~")
    out.append(os.path.join(home, ".local", "share", "qBittorrent", "BT_backup"))   # Linux
    return [d for d in out if os.path.isdir(d)]


def bitcomet_dirs() -> list[str]:
    out = []
    appdata = os.environ.get("APPDATA")
    if appdata:
        out.append(os.path.join(appdata, "BitComet"))
    for pf in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
        if pf:
            out.append(os.path.join(pf, "BitComet"))
    return [d for d in out if os.path.isfile(os.path.join(d, "Downloads.xml"))
            or os.path.isdir(os.path.join(d, "torrents"))]


def client_running(names: tuple[str, ...]) -> bool:
    try:
        import psutil
        for p in psutil.process_iter(["name"]):
            if (p.info.get("name") or "").lower() in names:
                return True
    except Exception:
        pass
    return False


def _hash_of(ti: lt.torrent_info) -> str:
    ih = ti.info_hashes()
    return str(ih.v1) if ih.has_v1() else str(ih.v2)


def _s(v) -> str:
    if isinstance(v, bytes):
        return v.decode("utf-8", "replace")
    return str(v) if v is not None else ""


# ── qBittorrent ─────────────────────────────────────────────────
def scan_qbittorrent(folder: str) -> list[Candidate]:
    out = []
    for fn in sorted(os.listdir(folder)):
        if not fn.endswith(".fastresume"):
            continue
        base = fn[: -len(".fastresume")]
        rpath = os.path.join(folder, fn)
        tpath = os.path.join(folder, base + ".torrent")
        try:
            with open(rpath, "rb") as f:
                raw = f.read()
            d = lt.bdecode(raw) or {}
            d = {_s(k): v for k, v in d.items()}
            atp = lt.read_resume_data(raw)
            ti = lt.torrent_info(tpath) if os.path.isfile(tpath) else None
            name = ti.name() if ti else (atp.name or _s(d.get("qBt-name")) or base)
            size = ti.total_size() if ti else 0
            save = _s(d.get("qBt-savePath")) or atp.save_path or _s(d.get("save_path"))
            out.append(Candidate(
                name=name, size=size, save_path=save, category=_s(d.get("qBt-category")),
                torrent_file=tpath if ti else "", resume_file=rpath,
                info_hash=_hash_of(ti) if ti else base,
                paused=bool(atp.flags & lt.torrent_flags.paused),
                note="" if ti else "magnet",
            ))
        except Exception as e:
            out.append(Candidate(name=base, size=0, save_path="", resume_file=rpath, info_hash=base,
                                 note=f"error:{e}"))
    return out


# ── BitComet ────────────────────────────────────────────────────
_PATH_KEYS = ("savepath", "save_path", "savefolder", "savedir", "save_dir", "downloadpath",
              "localpath", "folder", "dir", "path")
_TORRENT_KEYS = ("torrentfile", "torrent_file", "torrent", "torrentpath", "file", "seedfile")
_HASH_KEYS = ("infohash", "info_hash", "hash", "btih")


def _basename(path: str) -> str:
    """Basename for both Windows and POSIX separators (Downloads.xml uses backslashes)."""
    return path.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]


def _flat(el: ET.Element) -> dict[str, str]:
    """Attributes + direct child texts of an element, lower-cased keys."""
    d = {k.lower(): v for k, v in el.attrib.items()}
    for ch in el:
        if ch.text and ch.text.strip() and len(ch) == 0:
            d.setdefault(ch.tag.lower(), ch.text.strip())
    return d


def scan_bitcomet(folder: str) -> list[Candidate]:
    tdir = os.path.join(folder, "torrents")
    torrents: dict[str, tuple[str, lt.torrent_info]] = {}      # hash → (path, ti)
    by_name: dict[str, str] = {}
    if os.path.isdir(tdir):
        for fn in os.listdir(tdir):
            if fn.lower().endswith(".torrent"):
                p = os.path.join(tdir, fn)
                try:
                    ti = lt.torrent_info(p)
                    h = _hash_of(ti)
                    torrents[h] = (p, ti)
                    by_name[fn.lower()] = h
                except Exception:
                    pass

    # tasks from Downloads.xml: any element that points to a .torrent or carries an info-hash
    tasks: dict[str, dict] = {}
    xml_path = os.path.join(folder, "Downloads.xml")
    if os.path.isfile(xml_path):
        try:
            root = ET.parse(xml_path).getroot()
            for el in root.iter():
                d = _flat(el)
                h = next((d[k].lower() for k in _HASH_KEYS if len(d.get(k, "")) in (40, 64)), "")
                tf = next((d[k] for k in _TORRENT_KEYS if d.get(k, "").lower().endswith(".torrent")), "")
                if not h and tf:
                    h = by_name.get(_basename(tf).lower(), "")
                    if not h and os.path.isfile(tf):
                        try:
                            ti = lt.torrent_info(tf)
                            h = _hash_of(ti)
                            torrents.setdefault(h, (tf, ti))
                        except Exception:
                            pass
                if not h:
                    continue
                save = next((d[k] for k in _PATH_KEYS if d.get(k) and not d[k].lower().endswith(".torrent")), "")
                t = tasks.setdefault(h, {})
                if save and not t.get("save"):
                    t["save"] = save
        except ET.ParseError:
            pass

    out = []
    for h, (p, ti) in sorted(torrents.items(), key=lambda kv: kv[1][1].name().lower()):
        save = tasks.get(h, {}).get("save", "")
        # BitComet may store the full path to the content; libtorrent wants the parent folder
        if save and _basename(save) == ti.name():
            save = save.rstrip("/\\")[: -len(ti.name())].rstrip("/\\") or save
        out.append(Candidate(name=ti.name(), size=ti.total_size(), save_path=save, torrent_file=p,
                             info_hash=h, note="" if save else "nopath"))
    return out


# ── adding ──────────────────────────────────────────────────────
def build_params(c: Candidate, default_save: str, recheck: bool, paused: bool) -> lt.add_torrent_params:
    if c.resume_file:
        with open(c.resume_file, "rb") as f:
            atp = lt.read_resume_data(f.read())
        if c.torrent_file and atp.ti is None:
            atp.ti = lt.torrent_info(c.torrent_file)
    else:
        atp = lt.add_torrent_params()
        atp.ti = lt.torrent_info(c.torrent_file)
    atp.save_path = c.save_path or default_save
    if recheck:
        atp.have_pieces = []
        atp.verified_pieces = []
    if paused:
        atp.flags |= lt.torrent_flags.paused
        atp.flags &= ~lt.torrent_flags.auto_managed
    else:
        atp.flags &= ~lt.torrent_flags.paused
        atp.flags |= lt.torrent_flags.auto_managed
    return atp
