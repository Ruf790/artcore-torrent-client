"""Remove parts of Qt the client never uses from the PyInstaller output (dist/ArtCoreClient).
Run after PyInstaller and before Inno Setup; CI then runs `ArtCoreClient.exe --selftest`.

Usage: python tools/trim_build.py [dist/ArtCoreClient]
"""
import fnmatch
import os
import sys

# Qt libraries pulled in only through plugins we drop (Quick/QML for the virtual keyboard, PDF image
# plugin, …). Matched against file names, case-insensitive; .dll on Windows, .so* on Linux.
DROP_LIBS = [
    "*qt6quick*", "*qt6qml*", "*qt6pdf*", "*qt6opengl*", "*qt6virtualkeyboard*", "*qt6datavisualization*",
    "*qt6charts*", "*qt6graphs*", "*qt6shadertools*", "*qt6svg*", "*qt6webengine*", "*qt6multimedia*",
    "*qt6waylandclient*", "*qt6wlshellintegration*",
    "opengl32sw.dll",             # software OpenGL fallback (~20 MB); a widgets app doesn't use OpenGL
]
# plugin folders / files (relative to .../PySide6/plugins or .../PySide6/Qt/plugins)
DROP_PLUGINS = [
    "platforminputcontexts", "qmltooling", "scenegraph", "renderers", "egldeviceintegrations",
    "wayland-*", "xcbglintegrations", "networkinformation", "tls", "position", "sensors",
    "imageformats/*pdf*", "imageformats/*svg*", "imageformats/*tiff*", "imageformats/*webp*",
    "imageformats/*icns*", "imageformats/*tga*", "imageformats/*wbmp*", "iconengines/*svg*",
]
KEEP_TRANSLATIONS = ("qtbase_ru.qm", "qtbase_en.qm")


def size_of(path: str) -> int:
    total = 0
    for root, _, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def remove(path: str) -> int:
    if os.path.isdir(path):
        n = size_of(path)
        for root, dirs, files in os.walk(path, topdown=False):
            for f in files:
                os.remove(os.path.join(root, f))
            for d in dirs:
                os.rmdir(os.path.join(root, d))
        os.rmdir(path)
        return n
    n = os.path.getsize(path)
    os.remove(path)
    return n


def main(dist: str) -> None:
    before = size_of(dist)
    freed = 0
    for root, dirs, files in os.walk(dist):
        low = root.replace("\\", "/").lower()
        # plugins
        if low.endswith("/plugins"):
            for pattern in DROP_PLUGINS:
                if "/" in pattern:
                    folder, fpat = pattern.split("/", 1)
                    fdir = os.path.join(root, folder)
                    if os.path.isdir(fdir):
                        for f in os.listdir(fdir):
                            if fnmatch.fnmatch(f.lower(), fpat):
                                freed += remove(os.path.join(fdir, f))
                else:
                    for d in list(dirs):
                        if fnmatch.fnmatch(d.lower(), pattern):
                            freed += remove(os.path.join(root, d))
                            dirs.remove(d)
        # translations
        if low.endswith("/translations"):
            for f in files:
                if f.endswith(".qm") and f not in KEEP_TRANSLATIONS:
                    freed += remove(os.path.join(root, f))
            continue
        # libraries
        for f in files:
            if any(fnmatch.fnmatch(f.lower(), p) for p in DROP_LIBS):
                path = os.path.join(root, f)
                if os.path.exists(path):
                    freed += remove(path)
    after = size_of(dist)
    print(f"trim: {before / 1048576:.1f} MB -> {after / 1048576:.1f} MB (freed {freed / 1048576:.1f} MB)")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else os.path.join("dist", "ArtCoreClient"))
