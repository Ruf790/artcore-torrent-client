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


def main() -> int:
    args = sys.argv[1:]
    items = [a for a in args if a.startswith("magnet:") or a.lower().endswith(".torrent")]
    minimized = "--minimized" in args

    winint.init_process()
    app = QApplication(sys.argv)
    app.setApplicationName(APP_TITLE)
    app.setQuitOnLastWindowClosed(False)   # keeps running in the tray

    if _send_to_running(items if items or minimized else []):
        return 0

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
    theme.apply(app, cfg.get("theme", "system"))
    app.setWindowIcon(app_icon())

    engine = Engine(config.data_dir(), cfg)
    win = MainWindow(engine, cfg)
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
            for line in data.splitlines():
                if line and line != "--show":
                    win.add_item(line)
        s.readyRead.connect(read)
    server.newConnection.connect(on_conn)

    for it in items:
        win.add_item(it)

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
