"""JSON config + data folder (%APPDATA%\\ArtCoreClient on Windows)."""
from __future__ import annotations

import json
import os

DEFAULTS = {
    "save_path":        os.path.join(os.path.expanduser("~"), "Downloads"),
    "port":             51413,
    "dl_limit":         0,      # KiB/s, 0 = unlimited
    "ul_limit":         0,
    "active_downloads": 5,
    "active_seeds":     20,
    "dht":              True,   # private torrents never use DHT/PEX anyway
    "lsd":              True,
    "upnp":             True,
    "minimize_to_tray": True,
    "language":         "ru",
    "theme":            "system",   # system | light | dark
}


def data_dir() -> str:
    base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), ".config")
    path = os.path.join(base, "ArtCoreClient")
    os.makedirs(path, exist_ok=True)
    return path


def _installer_language() -> str | None:
    """Language picked in the installer (setup.iss writes it to HKCU)."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\ArtCore\ArtCore Client") as k:
            return "en" if winreg.QueryValueEx(k, "Language")[0] == "en" else "ru"
    except (ImportError, OSError):
        return None


def load() -> dict:
    cfg = dict(DEFAULTS)
    try:
        with open(os.path.join(data_dir(), "config.json"), encoding="utf-8") as f:
            cfg.update(json.load(f))
    except (FileNotFoundError, ValueError):
        cfg["language"] = _installer_language() or cfg["language"]   # first run
    return cfg


def save(cfg: dict) -> None:
    path = os.path.join(data_dir(), "config.json")
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(path + ".tmp", path)
