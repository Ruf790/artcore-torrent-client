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
    "max_connections":  200,        # all torrents together, 0 = no limit
    "conn_per_torrent": 0,          # default for every torrent, 0 = no limit
    "upload_slots":     8,          # peers we upload to at once, all torrents, 0 = no limit
    "slots_per_torrent": 0,         # … per torrent, 0 = no limit
    "dht":              True,   # private torrents never use DHT/PEX anyway
    "lsd":              True,
    "upnp":             True,
    "minimize_to_tray": True,
    "language":         "ru",
    "theme":            "system",   # system | light | dark
    "ui_style":         "soft",     # muted (gray, like the site) | soft | vivid
    "mica":             True,       # Windows 11 style: Mica backdrop (Windows 11 only, needs a restart)
    "name_icon":        "state",    # name-column icon: state (BitComet arrows) | type (file kind)
    "progress_style":   "classic",  # classic (flat, like qBittorrent) | rounded (pill with gradient)
    "show_add_dialog":  True,
    "stop_ratio":       0.0,        # pause seeding at this ratio, 0 = off
    "stop_hours":       0,          # pause after N hours of seeding, 0 = off
    "sched_enabled":    False,      # speed schedule
    "sched_from":       "08:00",
    "sched_to":         "23:00",
    "sched_dl":         0,          # KiB/s inside the window, 0 = unlimited
    "sched_ul":         0,
    "alt_on":           False,      # alternative limits («turtle»), toggled from the status bar
    "alt_dl":           1024,       # KiB/s, 0 = unlimited
    "alt_ul":           256,
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
    "toasts":           True,
    "ui_state":         {},         # window geometry, column layout, splitter, tab (base64 Qt states)
    "tracker_rules":    {},         # host → {min_hours, min_ratio, dl_limit, ul_limit}
    "ban_bad_peers":    True,       # leech-only clients (Xunlei …) and fake-progress peers
    "cross_seed":       True,       # reuse data of an existing torrent when the files match
    "tags":             [],         # known tags (also those not used by any torrent yet)
    "keep_awake":       True,       # no Windows sleep while downloading
    "keep_awake_seed":  False,      # … and while seeding with traffic
    "min_free_gb":      0,          # pause downloads below this free space, 0 = off
    "done_cmd":         "",         # run when a torrent finishes: %N name, %F full path, %D folder, %H hash, %L category
    "done_open_folder": False,
    "mini_widget":      False,      # floating speed window
    "mini_pos":         None,
    "mini_opacity":     85,         # % when the mouse is not over it
    "incomplete_ext":   False,      # unfinished files get ".!ac" until they are complete
    "speed_in_title":   True,       # speeds in the window title = taskbar button tooltip
    "recent_paths":     [],         # save folders used lately (add dialog drop-down)
    "random_port":      False,      # pick a new listen port on every start
    "utp":              True,       # uTP (µTP) peer connections besides TCP
    "proxy_type":       "none",     # none | socks4 | socks5 | http
    "proxy_host":       "",
    "proxy_port":       1080,
    "proxy_auth":       False,
    "proxy_user":       "",
    "proxy_pass":       "",
    "proxy_peers":      True,       # peer connections through the proxy too (not only trackers)
    "proxy_hostnames":  True,       # resolve tracker host names on the proxy (no DNS leak)
    "auto_trackers_on": False,      # add these trackers to every public (non-private) torrent
    "auto_trackers":    "",
    "add_subfolder":    False,      # add dialog: subfolder for a single-file torrent by default
    "delete_torrent_file": False,   # remove the .torrent file after it was added
    "export_dir":       "",         # copy the .torrent of every added torrent here, "" = off
    "export_done_dir":  "",         # … of every finished download here
    # Settings → Advanced → Disk & cache (libtorrent defaults)
    "preallocate":      False,      # reserve full file size when a torrent is added
    "disk_write_mode":  "mmap",     # mmap | auto | pwrite (pwrite = less "memory" in Task Manager)
    "disk_os_cache":    "enable",   # enable | disable | write_through
    "disk_queue_mb":    100,        # bytes waiting to be written before peers are throttled
    "aio_threads":      10,
    "hashing_threads":  1,
    "checking_mem_mb":  4,          # read-ahead while checking
    "file_pool":        40,         # open file handles
    "send_buffer_kb":   500,        # per-peer send buffer watermark
}


PORTABLE_MARKER = "portable.ini"


def portable_dir() -> str | None:
    """Portable mode: a portable.ini next to ArtCoreClient.exe keeps everything in .\\Data."""
    import sys
    if not getattr(sys, "frozen", False):
        return None
    base = os.path.dirname(os.path.abspath(sys.executable))
    return os.path.join(base, "Data") if os.path.exists(os.path.join(base, PORTABLE_MARKER)) else None


def is_portable() -> bool:
    return portable_dir() is not None


def data_dir() -> str:
    base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), ".config")
    path = portable_dir() or os.path.join(base, "ArtCoreClient")
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
