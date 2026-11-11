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


# ── power ──────────────────────────────────────────────────────
_ES_CONTINUOUS, _ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001


def keep_awake(on: bool) -> None:
    """Stop Windows from going to sleep while on (display may still turn off).
    Per-thread state: always call from the UI thread."""
    if not IS_WIN:
        return
    import ctypes
    ctypes.windll.kernel32.SetThreadExecutionState(_ES_CONTINUOUS | (_ES_SYSTEM_REQUIRED if on else 0))


def power_action(kind: str) -> None:
    """sleep | hibernate | shutdown."""
    if not IS_WIN:
        raise OSError("Windows only")
    import ctypes
    import subprocess
    if kind == "shutdown":
        subprocess.Popen(["shutdown.exe", "/s", "/t", "0"], creationflags=0x08000000)
    elif kind in ("sleep", "hibernate"):
        # SetSuspendState(bHibernate, bForce, bWakeupEventsDisabled)
        ctypes.windll.powrprof.SetSuspendState(kind == "hibernate", False, False)


# ── taskbar button progress (ITaskbarList3) ────────────────────
TBPF_NOPROGRESS, TBPF_INDETERMINATE, TBPF_NORMAL, TBPF_ERROR, TBPF_PAUSED = 0, 1, 2, 4, 8


class Taskbar:
    """Progress on the taskbar button, like Explorer's file copy. Raw COM via ctypes (Qt 6 dropped
    QWinTaskbarProgress). Every call is best-effort: a failure just leaves the button plain."""
    _SET_VALUE, _SET_STATE = 9, 10      # vtable: IUnknown 0-2, HrInit 3, AddTab…MarkFullscreenWindow 4-8

    def __init__(self):
        self.ptr = None
        self.ok = False
        self.diag = ""
        if not IS_WIN:
            return
        try:
            import ctypes
            import uuid

            def guid(text: str):
                return (ctypes.c_ubyte * 16).from_buffer_copy(uuid.UUID(text).bytes_le)

            ole32 = ctypes.windll.ole32
            ole32.CoInitialize(None)          # S_FALSE when Qt already did it — fine
            clsid = guid("56FDF344-FD6D-11d0-958A-006097C9A090")          # CLSID_TaskbarList
            ptr = ctypes.c_void_p()
            hr = -1
            # ITaskbarList3, else ITaskbarList4 (same vtable start)
            for iid_text in ("ea1afb91-9e28-4b86-90e9-9e9f8a5eee6f", "c43dc798-95d1-4bea-9030-bb99e2983a1a"):
                iid = guid(iid_text)
                hr = ole32.CoCreateInstance(ctypes.byref(clsid), None, 1, ctypes.byref(iid), ctypes.byref(ptr))
                if hr == 0 and ptr.value:
                    break
            if hr != 0 or not ptr.value:
                self.diag = f"CoCreateInstance {hr & 0xFFFFFFFF:#x}"
                return
            self.ptr = ptr
            hr = self._call(3, ())                # HrInit
            self.ok = hr == 0
            self.diag = "" if self.ok else f"HrInit {hr & 0xFFFFFFFF:#x}"
        except Exception as e:
            self.ptr = None
            self.diag = repr(e)

    def _call(self, idx: int, args: tuple, argtypes: tuple = ()):
        import ctypes
        vtbl = ctypes.cast(self.ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        fn = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)(vtbl[idx])
        return fn(self.ptr, *args)

    def set(self, hwnd: int, state: int, done: int = 0, total: int = 0) -> int:
        if not self.ok:
            return -1
        import ctypes
        from ctypes import wintypes
        try:
            hr = self._call(self._SET_STATE, (hwnd, state), (wintypes.HWND, ctypes.c_int))
            if state in (TBPF_NORMAL, TBPF_ERROR, TBPF_PAUSED) and total > 0:
                hr = self._call(self._SET_VALUE, (hwnd, done, total),
                                (wintypes.HWND, ctypes.c_ulonglong, ctypes.c_ulonglong))
            return hr
        except Exception:
            return -1
