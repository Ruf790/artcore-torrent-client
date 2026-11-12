# SPDX-License-Identifier: LGPL-3.0-or-later
"""Backup / restore of the whole client state as one .zip: settings, torrents (resume data with
the .torrent inside), per-torrent options, history, statistics, bans.

Restoring happens in two steps: the archive is unpacked into <data>/restore_pending and the client
restarts; on the next start — before anything is loaded — the current files are moved aside to
<data>/before_restore_<time> and the pending ones take their place."""
from __future__ import annotations

import json
import ntpath
import os
import posixpath
import shutil
import time
import zipfile

import libtorrent as lt

from . import VERSION

MANIFEST = "artcore-backup.json"
PENDING = "restore_pending"
# downloaded again by themselves / temporary — not worth carrying around
SKIP_FILES = {"dbip-country-lite.mmdb", "ipfilter.cache", "single.lock"}
SKIP_DIRS = {PENDING}
SKIP_PREFIX = ("before_restore_",)


def _walk(data_dir: str):
    for root, dirs, files in os.walk(data_dir):
        rel_root = os.path.relpath(root, data_dir)
        if rel_root == ".":
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(SKIP_PREFIX)]
        for f in files:
            if f in SKIP_FILES or f.endswith((".tmp", ".part")):
                continue
            full = os.path.join(root, f)
            yield full, os.path.normpath(os.path.join(rel_root, f)).replace(os.sep, "/")


def create(data_dir: str, out_path: str, info: dict | None = None) -> dict:
    """Write the archive (the caller has saved resume data, stats and config first)."""
    n_torrents = 0
    total = 0
    tmp = out_path + ".part"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for full, arc in _walk(data_dir):
            if arc.startswith("resume/") and arc.endswith(".fastresume"):
                n_torrents += 1
            total += os.path.getsize(full)
            z.write(full, arc)
        manifest = {"app": "ArtCore Client", "version": VERSION, "created": int(time.time()),
                    "torrents": n_torrents, "bytes": total, **(info or {})}
        z.writestr(MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=1))
    os.replace(tmp, out_path)
    return manifest


def read_manifest(path: str) -> dict:
    """Manifest + the save folders used by the torrents in the archive (for remapping)."""
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        if MANIFEST not in names or "config.json" not in names:
            raise ValueError("not an ArtCore Client backup")
        m = json.loads(z.read(MANIFEST).decode("utf-8"))
        folders: dict[str, int] = {}
        for n in names:
            if n.startswith("resume/") and n.endswith(".fastresume"):
                try:
                    atp = lt.read_resume_data(z.read(n))
                    folders[atp.save_path] = folders.get(atp.save_path, 0) + 1
                except Exception:
                    pass
        cfg = json.loads(z.read("config.json").decode("utf-8"))
    m["folders"] = folders
    m["save_path"] = cfg.get("save_path", "")
    return m


def _remap(path: str, mapping: list[tuple[str, str]]) -> str:
    """Replace the longest matching old folder prefix (case-insensitive, either slash)."""
    if not path:
        return path
    norm = path.replace("/", "\\").rstrip("\\")
    best = None
    for old, new in mapping:
        o = old.replace("/", "\\").rstrip("\\")
        if not o or o == new.replace("/", "\\").rstrip("\\"):
            continue
        if norm.lower() == o.lower() or norm.lower().startswith(o.lower() + "\\"):
            if best is None or len(o) > len(best[0]):
                best = (o, new)
    if best is None:
        return path
    rest = norm[len(best[0]):].lstrip("\\")
    if "\\" in best[1] or ":" in best[1] or os.sep == "\\":
        return ntpath.join(best[1], rest) if rest else best[1]
    return posixpath.join(best[1], rest.replace("\\", "/")) if rest else best[1]


def _remap_cfg(cfg: dict, mapping) -> dict:
    for k in ("save_path", "move_done_dir", "watch_dir", "export_dir", "export_done_dir", "last_export_dir"):
        if cfg.get(k):
            cfg[k] = _remap(cfg[k], mapping)
    cfg["recent_paths"] = [_remap(p, mapping) for p in cfg.get("recent_paths", [])]
    for c in cfg.get("categories", []):
        if c.get("path"):
            c["path"] = _remap(c["path"], mapping)
    return cfg


def stage(path: str, data_dir: str, mapping: list[tuple[str, str]], keep_ui: dict | None = None) -> int:
    """Unpack into <data>/restore_pending, folders remapped. Returns the number of torrents."""
    dest = os.path.join(data_dir, PENDING)
    shutil.rmtree(dest, ignore_errors=True)
    os.makedirs(dest)
    n = 0
    with zipfile.ZipFile(path) as z:
        for info in z.infolist():
            name = info.filename
            if name == MANIFEST or name.endswith("/"):
                continue
            # no absolute paths / .. — the archive may come from anywhere
            parts = [p for p in name.replace("\\", "/").split("/") if p not in ("", ".", "..")]
            if not parts or ":" in parts[0]:
                continue
            target = os.path.join(dest, *parts)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            data = z.read(info)
            if (parts[0] in ("resume", "history")) and name.endswith(".fastresume") and mapping:
                try:
                    atp = lt.read_resume_data(data)
                    atp.save_path = _remap(atp.save_path, mapping)
                    data = lt.write_resume_data_buf(atp)
                except Exception:
                    pass
            elif name == "config.json":
                cfg = json.loads(data.decode("utf-8"))
                cfg = _remap_cfg(cfg, mapping)
                if keep_ui is not None:       # window layout of this PC, not of the old one
                    cfg["ui_state"] = keep_ui
                data = json.dumps(cfg, ensure_ascii=False, indent=2).encode("utf-8")
            elif name == "history.json" and mapping:
                try:
                    hist = json.loads(data.decode("utf-8"))
                    for h in hist:
                        if isinstance(h, dict) and h.get("save_path"):
                            h["save_path"] = _remap(h["save_path"], mapping)
                    data = json.dumps(hist, ensure_ascii=False).encode("utf-8")
                except Exception:
                    pass
            if parts[0] == "resume" and name.endswith(".fastresume"):
                n += 1
            with open(target, "wb") as f:
                f.write(data)
    return n


def apply_pending(data_dir: str) -> str:
    """At start-up, before config / engine load: swap in a staged restore. Returns the folder the
    previous state was moved to, or "" if there was nothing to restore."""
    src = os.path.join(data_dir, PENDING)
    if not os.path.isdir(src) or not os.path.isfile(os.path.join(src, "config.json")):
        return ""
    aside = os.path.join(data_dir, "before_restore_" + time.strftime("%Y%m%d_%H%M%S"))
    os.makedirs(aside, exist_ok=True)
    for name in os.listdir(data_dir):
        if name in (PENDING,) or name.startswith(SKIP_PREFIX) or name in SKIP_FILES:
            continue
        shutil.move(os.path.join(data_dir, name), os.path.join(aside, name))
    for name in os.listdir(src):
        shutil.move(os.path.join(src, name), os.path.join(data_dir, name))
    shutil.rmtree(src, ignore_errors=True)
    return aside
