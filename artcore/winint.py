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


def set_dark_title_bar(win_id: int, dark: bool) -> None:
    if not IS_WIN:
        return
    import ctypes
    from ctypes import wintypes
    val = ctypes.c_int(1 if dark else 0)
    for attr in (20, 19):   # DWMWA_USE_IMMERSIVE_DARK_MODE (Win10 2004+ / older builds)
        try:
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(wintypes.HWND(int(win_id)), attr,
                                                          ctypes.byref(val), ctypes.sizeof(val)) == 0:
                return
        except Exception:
            return


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
