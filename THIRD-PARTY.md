# Third-party components

ArtCore Client is licensed under the GNU LGPL v3 (`LICENSE`, which builds on the GNU GPL v3 in `COPYING`).
The Windows build bundles the following components under their own licenses:

| Component | License | Notes |
|---|---|---|
| [Qt 6 / PySide6](https://www.qt.io/qt-for-python) | LGPL v3 | dynamically linked DLLs in the program folder, so they can be replaced |
| [libtorrent-rasterbar](https://libtorrent.org) | BSD 3-Clause | |
| [Python](https://www.python.org) | PSF License | runtime bundled by PyInstaller |
| [PyInstaller](https://pyinstaller.org) bootloader | GPL v2 with bootloader exception | the exception allows any license for the bundled program |
| [QtAwesome](https://github.com/spyder-ide/qtawesome) | MIT | |
| [Font Awesome Free](https://fontawesome.com) icons | icons CC BY 4.0, font SIL OFL 1.1 | via QtAwesome |
| [Oswald](https://fonts.google.com/specimen/Oswald) font | SIL OFL 1.1 | `assets/fonts/OFL.txt` |
| [flag-icons](https://github.com/lipis/flag-icons) | MIT | country flags, rendered to PNG; `assets/flags/LICENSE.txt` |
| [psutil](https://github.com/giampaolo/psutil) | BSD 3-Clause | |
| [maxminddb](https://github.com/maxmind/MaxMind-DB-Reader-python) | Apache 2.0 | reader only, no MaxMind data |
| [windows-toasts](https://github.com/DatGuy1/Windows-Toasts) | Apache 2.0 | Windows only |
| [DB-IP "IP to Country Lite"](https://db-ip.com) | CC BY 4.0 | downloaded at runtime, not bundled — IP Geolocation by DB-IP |
| [Inno Setup](https://jrsoftware.org/isinfo.php) | Inno Setup License | installer only |
