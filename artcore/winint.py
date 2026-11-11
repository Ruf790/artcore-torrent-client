"""Windows integration: autostart, .torrent / magnet association, taskbar id,
dark title bar, single-instance mutex for the installer. No-ops elsewhere."""
from __future__ import annotations

import os
import sys

IS_WIN = sys.platform == "win32"
APP_ID = "ArtCore.Client"
PROG_ID = "ArtCoreClient.torrent"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "ArtCore Client"
MUTEX_NAME = "ArtCoreClientMutex"   # same name as AppMutex in setup.iss

_mutex = None


def exe_command() -> str:
    """Command that starts the client (frozen .exe or python + run.py)."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    run = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "run.py")
    pyw = sys.executable.replace("python.exe", "pythonw.exe")
    return f'"{pyw}" "{run}"'


def exe_path() -> str:
    return sys.executable if getattr(sys, "frozen", False) else ""


def init_process() -> None:
    """Taskbar grouping id + mutex the installer checks before updating files."""
    global _mutex
    if not IS_WIN:
        return
    import ctypes
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except Exception:
        pass
    try:
        _mutex = ctypes.windll.kernel32.CreateMutexW(None, False, MUTEX_NAME)
    except Exception:
        pass


def _colorref(hex_color: str) -> int:
    """'#rrggbb' -> COLORREF 0x00BBGGRR."""
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return (b << 16) | (g << 8) | r


def release_mutex() -> None:
    """Let the installer run (it waits for AppMutex) — used by the self-updater before quitting."""
    global _mutex
    if IS_WIN and _mutex:
        import ctypes
        ctypes.windll.kernel32.CloseHandle(_mutex)
        _mutex = None


def set_title_bar(win_id: int, dark: bool, caption: str | None = None, text: str | None = None) -> None:
    """Dark/light title bar; on Windows 11 also paint it in the page colour,
    so it matches the window even with "Show accent color on title bars" on."""
    if not IS_WIN:
        return
    import ctypes
    from ctypes import wintypes
    hwnd = wintypes.HWND(int(win_id))
    dwm = ctypes.windll.dwmapi

    def setattr_(attr: int, value: int) -> bool:
        v = ctypes.c_int(value)
        try:
            return dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(v), ctypes.sizeof(v)) == 0
        except Exception:
            return False

    # DWMWA_USE_IMMERSIVE_DARK_MODE: 20 (Win10 2004+ / Win11), 19 (older Win10 builds)
    if not setattr_(20, 1 if dark else 0):
        setattr_(19, 1 if dark else 0)
    # Windows 11 (22000+) only; silently ignored on Windows 10
    if caption:
        setattr_(35, _colorref(caption))   # DWMWA_CAPTION_COLOR
    if text:
        setattr_(36, _colorref(text))      # DWMWA_TEXT_COLOR
    # repaint the non-client area now instead of on the next activation
    SWP_NOSIZE, SWP_NOMOVE, SWP_NOZORDER, SWP_NOACTIVATE, SWP_FRAMECHANGED = 0x1, 0x2, 0x4, 0x10, 0x20
    try:
        ctypes.windll.user32.SetWindowPos(hwnd, None, 0, 0, 0, 0,
                                          SWP_NOSIZE | SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED)
    except Exception:
        pass


# ── autostart ───────────────────────────────────────────────────
def autostart_enabled() -> bool:
    if not IS_WIN:
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, RUN_VALUE)
            return True
    except OSError:
        return False


def set_autostart(enable: bool) -> None:
    if not IS_WIN:
        return
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if enable:
            winreg.SetValueEx(k, RUN_VALUE, 0, winreg.REG_SZ, exe_command() + " --minimized")
        else:
            try:
                winreg.DeleteValue(k, RUN_VALUE)
            except OSError:
                pass


# ── file / protocol association (per user, no admin rights) ─────
def is_default_handler() -> bool:
    if not IS_WIN:
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\.torrent") as k:
            return winreg.QueryValueEx(k, "")[0] == PROG_ID
    except OSError:
        return False


def register_associations() -> None:
    if not IS_WIN:
        return
    import ctypes
    import winreg
    cmd = exe_command()
    icon = exe_path() or ""

    def setkey(path: str, values: dict[str, str]) -> None:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Classes" + "\\" + path) as k:
            for name, val in values.items():
                winreg.SetValueEx(k, name, 0, winreg.REG_SZ, val)

    setkey(".torrent", {"": PROG_ID, "Content Type": "application/x-bittorrent"})
    setkey(PROG_ID, {"": "Torrent file"})
    if icon:
        setkey(PROG_ID + r"\DefaultIcon", {"": f'"{icon}",0'})
    setkey(PROG_ID + r"\shell\open\command", {"": f'{cmd} "%1"'})
    setkey("magnet", {"": "URL:Magnet link", "URL Protocol": ""})
    if icon:
        setkey(r"magnet\DefaultIcon", {"": f'"{icon}",0'})
    setkey(r"magnet\shell\open\command", {"": f'{cmd} "%1"'})
    # tell Explorer the associations changed
    ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)


# ── firewall (for the Statistics tab) ───────────────────────────
def firewall_allowed() -> bool | None:
    """True if an inbound allow rule exists for our .exe (made by the installer
    or by the Windows "Allow access" prompt). None = unknown / not Windows."""
    if not IS_WIN:
        return None
    import subprocess
    exe = exe_path()
    try:
        out = subprocess.run(
            ["netsh", "advfirewall", "firewall", "show", "rule", "name=all", "dir=in", "verbose"],
            capture_output=True, text=True, timeout=8, creationflags=0x08000000,   # CREATE_NO_WINDOW
            encoding="oem", errors="replace").stdout
    except (OSError, subprocess.SubprocessError):
        return None
    needle = (exe or "ArtCoreClient").lower()
    blocks = out.lower().split("\n\n")
    found = [b for b in blocks if needle in b]
    # netsh output is localized: "Action: Allow" (en) / "Действие: Разрешить" (ru)
    return any(("allow" in b or "разреш" in b) for b in found)
