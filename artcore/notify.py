"""Windows toast notifications with buttons (Open folder / Show), tray balloon fallback."""
from __future__ import annotations

import os
import sys

from PySide6.QtCore import QObject, Signal

from . import APP_TITLE
from .winint import APP_ID


class Notifier(QObject):
    """emit activated("open:<key>" | "show:<key>" | "") when the user clicks a toast."""
    activated = Signal(str)

    def __init__(self, icon_path: str = ""):
        super().__init__()
        self.toaster = None
        self.error = ""
        if sys.platform != "win32":
            self.error = "not windows"
            return
        try:
            self._register_aumid(icon_path)
            from windows_toasts import InteractableWindowsToaster
            self.toaster = InteractableWindowsToaster(APP_TITLE, notifierAUMID=APP_ID)
        except Exception as e:                  # missing package, old Windows, broken WinRT…
            self.error = str(e)
            self.toaster = None

    @staticmethod
    def _register_aumid(icon_path: str) -> None:
        """Unpackaged apps need their AUMID in the registry for toasts with buttons
        (the installer also sets it on the Start-menu shortcut)."""
        import winreg
        key = rf"Software\Classes\AppUserModelId\{APP_ID}"
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key) as k:
            winreg.SetValueEx(k, "DisplayName", 0, winreg.REG_SZ, APP_TITLE)
            if icon_path and os.path.isfile(icon_path):
                winreg.SetValueEx(k, "IconUri", 0, winreg.REG_SZ, os.path.abspath(icon_path))

    @property
    def available(self) -> bool:
        return self.toaster is not None

    def show(self, title: str, text: str, buttons: list[tuple[str, str]] | None = None) -> bool:
        """buttons: [(label, argument)]. Returns False if toasts are unavailable (caller falls back)."""
        if self.toaster is None:
            return False
        try:
            from windows_toasts import Toast, ToastButton
            toast = Toast([title, text])
            for label, arg in buttons or []:
                toast.AddAction(ToastButton(label, arguments=arg))
            # called on a WinRT thread — the signal is queued to the Qt main thread
            toast.on_activated = lambda args: self.activated.emit(getattr(args, "arguments", "") or "")
            self.toaster.show_toast(toast)
            return True
        except Exception as e:
            self.error = str(e)
            return False
