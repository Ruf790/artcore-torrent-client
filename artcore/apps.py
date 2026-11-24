# SPDX-License-Identifier: LGPL-3.0-or-later
"""The other ArtCore programs: ArtCore Player (video), ArtCore Music (audio) and ArtCore Convert.

They are found through their Inno Setup uninstall entries (InstallLocation), so per-user and
all-users installs both work; the usual Program Files folders are the fallback. All three take
file paths on the command line (a running Player / Music gets them from the new instance)."""
from __future__ import annotations

import os
import subprocess
import time

# app id → (title, exe, Inno AppId, releases page)
APPS = {
    "player":  ("ArtCore Player", "ArtCorePlayer.exe", "{CC3B3D27-0C3A-4387-AF84-F55A8919C074}",
                "https://github.com/Ruf790/artcore-player/releases/latest"),
    "music":   ("ArtCore Music", "ArtCoreMusic.exe", "{713130CD-51C0-4789-A4C1-FF81BED0DAE9}",
                "https://github.com/Ruf790/artcore-music/releases/latest"),
    "convert": ("ArtCore Convert", "ArtCoreConvert.exe", "{12DF62CC-D565-46D0-9EC7-F876FE9E351F}",
                "https://github.com/Ruf790/artcore-convert/releases/latest"),
}

VIDEO_EXT = {".mkv", ".mp4", ".m4v", ".avi", ".mov", ".wmv", ".webm", ".ts", ".m2ts", ".mts", ".mpg",
             ".mpeg", ".vob", ".flv", ".3gp", ".ogv", ".rm", ".rmvb", ".divx", ".asf", ".f4v", ".hevc"}
AUDIO_EXT = {".mp3", ".flac", ".m4a", ".aac", ".ogg", ".oga", ".opus", ".wav", ".wv", ".ape", ".alac",
             ".dsf", ".dff", ".ac3", ".dts", ".eac3", ".mka", ".tta", ".wma", ".aiff", ".aif", ".mpc"}
PLAYLIST_EXT = {".cue", ".m3u", ".m3u8", ".pls"}

_cache: dict[str, tuple[float, str]] = {}      # app → (checked at, exe path or "")
CACHE_SEC = 30                                  # re-checked now and then: installed while running


def _from_registry(app_id: str) -> str:
    if os.name != "nt":
        return ""
    import winreg
    sub = r"Software\Microsoft\Windows\CurrentVersion\Uninstall" + "\\" + app_id + "_is1"
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(root, sub, 0, winreg.KEY_READ | view) as k:
                    loc = winreg.QueryValueEx(k, "InstallLocation")[0]
                    if loc:
                        return loc
            except OSError:
                continue
    return ""


def find(app: str) -> str:
    """Full path of the app's .exe, "" when it isn't installed."""
    now = time.time()
    hit = _cache.get(app)
    if hit and now - hit[0] < CACHE_SEC:
        return hit[1]
    title, exe, app_id, _ = APPS[app]
    dirs = [_from_registry(app_id)]
    for env in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
        if os.environ.get(env):
            dirs.append(os.path.join(os.environ[env], title))
    if os.environ.get("LOCALAPPDATA"):
        dirs.append(os.path.join(os.environ["LOCALAPPDATA"], "Programs", title))
    path = ""
    for d in dirs:
        if d and os.path.isfile(os.path.join(d, exe)):
            path = os.path.join(d, exe)
            break
    _cache[app] = (now, path)
    return path


def title(app: str) -> str:
    return APPS[app][0]


def releases_url(app: str) -> str:
    return APPS[app][3]


def kind_of(path: str) -> str:
    """"video" | "audio" | "" by extension."""
    ext = os.path.splitext(path)[1].lower()
    if ext in VIDEO_EXT:
        return "video"
    if ext in AUDIO_EXT or ext in PLAYLIST_EXT:
        return "audio"
    return ""


def ready_media(engine, key: str, indices: list[int] | None = None) -> dict[str, list[str]]:
    """Fully downloaded video / audio files of a torrent that are on disk:
    {"video": [...], "audio": [...]}, in the torrent's file order. Unfinished files are left out —
    the programs would play / convert a broken file."""
    out: dict[str, list[str]] = {"video": [], "audio": []}
    want = set(indices) if indices is not None else None
    for f in engine.files(key):
        if want is not None and f["index"] not in want:
            continue
        if f["progress"] < 1.0 or f["priority"] == 0:
            continue
        kind = kind_of(f["path"])
        if not kind or (kind == "audio" and os.path.splitext(f["path"])[1].lower() in PLAYLIST_EXT
                        and want is None):
            continue                     # .cue / .m3u only when picked by hand in the Files tab
        path = engine.abs_file_path(key, f["index"])
        if path and os.path.isfile(path):
            out[kind].append(os.path.normpath(path))
    return out


def launch(app: str, paths: list[str]) -> str:
    """Start the app with the files; returns "" or the error text."""
    exe = find(app)
    if not exe:
        return f"{title(app)}: not installed"
    try:
        subprocess.Popen([exe, *paths], cwd=os.path.dirname(exe), close_fds=True)
    except OSError as e:
        return str(e)
    return ""
