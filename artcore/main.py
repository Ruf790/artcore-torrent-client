"""Entry point: single instance, command-line .torrent/magnet, start UI."""
from __future__ import annotations

import sys

from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from . import APP_TITLE, config, winint
from .i18n import set_lang

SERVER_NAME = "ArtCoreClient-single-instance"


def _send_to_running(items: list[str]) -> bool:
    """If the client is already running, hand it our arguments and return True."""
    sock = QLocalSocket()
    sock.connectToServer(SERVER_NAME)
    if not sock.waitForConnected(300):
        return False
    sock.write(("\n".join(items) or "--show").encode("utf-8"))
    sock.waitForBytesWritten(1000)
    sock.disconnectFromServer()
    return True


def _wait_old_exit(timeout: float = 20.0) -> None:
    """--restarted (language switch): the old copy is still saving its session — wait until its
    single-instance server is gone, so this one doesn't just hand over to it and exit."""
    import time
    end = time.time() + timeout
    while time.time() < end:
        sock = QLocalSocket()
        sock.connectToServer(SERVER_NAME)
        if not sock.waitForConnected(200):
            return
        sock.abort()
        time.sleep(0.3)


def selftest() -> int:
    """Used by CI on the built .exe: every module imports, the main window builds, the engine starts.
    Writes the result to %TEMP%/artcore_selftest.txt (a --windowed exe has no console)."""
    import os
    import tempfile
    import traceback
    out = os.path.join(tempfile.gettempdir(), "artcore_selftest.txt")
    try:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QApplication(sys.argv)
        import importlib
        import libtorrent as lt
        for mod in ("maxminddb", "psutil", "qtawesome", "artcore.widgets", "artcore.netx",
                    "artcore.importers", "artcore.updater", "artcore.dialogs",
                    "artcore.panels", "artcore.backup", "artcore.trackertest"):
            importlib.import_module(mod)
        from . import theme, VERSION
        from .engine import Engine
        from .ui import MainWindow
        theme.load_fonts()
        theme.apply(app, "dark", "vivid")
        theme.apply(app, "light", "soft")
        data = os.path.join(tempfile.gettempdir(), "artcore_selftest_data")
        cfg = config.load()
        cfg.update(dht=False, lsd=False, upnp=False, port=0, check_updates=False, geoip=False)
        eng = Engine(data, cfg)
        win = MainWindow(eng, cfg)
        win.show()
        for _ in range(20):
            app.processEvents()
        for i in range(win.tabs.count()):     # every detail tab must build and refresh
            win.tabs.setCurrentIndex(i)
            win.tick()
        toasts = taskbar = "n/a"
        if sys.platform == "win32":
            importlib.import_module("windows_toasts")
            toasts = "ok"
            winint.keep_awake(True)
            winint.keep_awake(False)
            hr = win.taskbar.set(int(win.winId()), winint.TBPF_NORMAL, 500, 1000)
            taskbar = f"{'ok' if win.taskbar.ok else 'unavailable ' + win.taskbar.diag} hr={hr & 0xFFFFFFFF:#x}"
        win.set_mini(True)
        app.processEvents()
        win.set_mini(False)
        win.quit()
        with open(out, "w", encoding="utf-8") as f:
            f.write(f"OK {VERSION} libtorrent {lt.__version__} toasts {toasts} taskbar {taskbar} font {theme.TITLE_FONT}\n")
        return 0
    except Exception:
        with open(out, "w", encoding="utf-8") as f:
            f.write("FAIL\n" + traceback.format_exc())
        return 1


def main() -> int:
    args = sys.argv[1:]
    if "--selftest" in args:
        return selftest()
    items = [a for a in args if a.startswith("magnet:") or a.lower().endswith((".torrent", ".!ac"))]
    minimized = "--minimized" in args

    winint.init_process()
    app = QApplication(sys.argv)
    app.setApplicationName(APP_TITLE)
    app.setQuitOnLastWindowClosed(False)   # keeps running in the tray

    if "--restarted" in args:
        _wait_old_exit()
    if _send_to_running(items if items or minimized else []):
        return 0

    from . import backup
    try:
        restored_from = backup.apply_pending(config.data_dir())      # "Restore from backup" staged it
    except OSError:
        restored_from = ""
    cfg = config.load()
    set_lang(cfg.get("language", "ru"))

    # Qt's own buttons (Cancel, Yes/No, file dialogs) in the same language
    if cfg.get("language", "ru") == "ru":
        from PySide6.QtCore import QLibraryInfo, QTranslator
        qt_tr = QTranslator(app)
        if qt_tr.load("qtbase_ru", QLibraryInfo.path(QLibraryInfo.TranslationsPath)):
            app.installTranslator(qt_tr)

    # imported after set_lang() so labels pick the right language
    from . import theme
    from .engine import Engine
    from .ui import MainWindow, app_icon

    app.setStyle("Fusion")
    theme.load_fonts()
    theme.apply(app, cfg.get("theme", "system"), cfg.get("ui_style", "soft"),
                mica=bool(cfg.get("mica", True)) and winint.mica_supported())
    app.setWindowIcon(app_icon())
    from . import swal
    swal.install()              # site-style (SweetAlert-like) message boxes everywhere

    engine = Engine(config.data_dir(), cfg)
    win = MainWindow(engine, cfg)
    if restored_from:
        win.log_event("system", f"Restored from backup; the previous state is in {restored_from}")
    if not (minimized and QSystemTrayIcon.isSystemTrayAvailable()):
        win.show()

    server = QLocalServer()
    QLocalServer.removeServer(SERVER_NAME)   # stale socket after a crash
    server.listen(SERVER_NAME)

    def on_conn():
        s = server.nextPendingConnection()

        def read():
            data = bytes(s.readAll()).decode("utf-8", "replace")
            win.show_normal()
            got = [line for line in data.splitlines() if line and line != "--show"]
            if got:
                win.queue_items(got)
        s.readyRead.connect(read)
    server.newConnection.connect(on_conn)
    win.server = server              # closed before a restart (language switch)

    if items:
        win.queue_items(items)

    code = app.exec()
    # Everything is saved by now (MainWindow.quit → engine.shutdown). Leave for real: closing the
    # session is time-limited, then the process ends at once — no Python clean-up that could keep
    # it hanging in Task Manager (WinRT toast callbacks, COM objects, the libtorrent destructor).
    try:
        server.close()
        engine.close(5.0)
    finally:
        sys.stdout and sys.stdout.flush()
        sys.stderr and sys.stderr.flush()
        import os
        os._exit(code)


if __name__ == "__main__":
    sys.exit(main())
