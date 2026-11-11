"""Update check against GitHub Releases + download of the Setup.exe."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request

from . import VERSION

REPO = "Ruf790/artcore-torrent-client"
API = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"
UA = f"ArtCoreClient/{VERSION}"


def _ver(s: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", s)[:4]) or (0,)


def check() -> dict | None:
    """Latest release if it is newer than this build: {version, notes, url, size, page}."""
    req = urllib.request.Request(API, headers={"User-Agent": UA, "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        rel = json.load(r)
    if rel.get("draft") or rel.get("prerelease"):
        return None
    tag = rel.get("tag_name", "")
    if _ver(tag) <= _ver(VERSION):
        return None
    asset = next((a for a in rel.get("assets", [])
                  if a.get("name", "").lower().startswith("artcoreclient-setup") and a["name"].lower().endswith(".exe")), None)
    return {
        "version": tag.lstrip("vV"),
        "notes": rel.get("body") or "",
        "url": asset["browser_download_url"] if asset else "",
        "size": asset.get("size", 0) if asset else 0,
        "page": rel.get("html_url") or RELEASES_PAGE,
    }


def download(url: str, size: int, progress=None) -> str:
    """Download the installer to %TEMP%; returns its path. Size is verified."""
    path = os.path.join(tempfile.gettempdir(), os.path.basename(url.split("?")[0]) or "ArtCoreClient-Setup.exe")
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    done = 0
    with urllib.request.urlopen(req, timeout=60) as r, open(path + ".part", "wb") as f:
        while True:
            chunk = r.read(256 * 1024)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if progress:
                progress(done, size)
    if size and done != size:
        os.remove(path + ".part")
        raise IOError(f"download incomplete: {done} of {size} bytes")
    os.replace(path + ".part", path)
    return path


def run_installer(path: str) -> None:
    """Start the silent update a few seconds after this process exits (call it right before
    quitting, after the session is saved and the mutex released). setup.iss restarts the client."""
    if sys.platform != "win32":
        raise OSError("installer runs on Windows only")
    cmd = f'ping -n 4 127.0.0.1 >nul & start "" "{path}" /SILENT /SP- /NOCANCEL /CLOSEAPPLICATIONS'
    subprocess.Popen(["cmd.exe", "/c", cmd], close_fds=True, creationflags=0x08000000)   # CREATE_NO_WINDOW


def can_self_update() -> bool:
    return sys.platform == "win32" and getattr(sys, "frozen", False)
