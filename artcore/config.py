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
    "show_add_dialog":  True,
    "stop_ratio":       0.0,        # pause seeding at this ratio, 0 = off
    "stop_hours":       0,          # pause after N hours of seeding, 0 = off
    "sched_enabled":    False,      # speed schedule
    "sched_from":       "08:00",
    "sched_to":         "23:00",
    "sched_dl":         0,          # KiB/s inside the window, 0 = unlimited
    "sched_ul":         0,
    "categories":       [],         # [{"name": ..., "path": ...}]
    "watch_dir":        "",
    "watch_category":   "",
    "move_done_dir":    "",
    "move_done_by_category": True,
    "bind_interface":   "",         # adapter name, "" = any
    "encryption":       "prefer",   # prefer | require | disable
    "ip_filter":        "",         # file path or URL, "" = off
    "geoip":            True,
    "check_updates":    True,
    "last_update_check": 0,
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
    cfg = json.loads(json.dumps(DEFAULTS))   # deep copy: lists inside must not be shared
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
