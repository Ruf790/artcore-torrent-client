"""File type by extension — shared by the engine (torrent kind) and the UI (icons). No Qt here."""
from __future__ import annotations

import os

# extension → (kind, icon, colour key)
KINDS = {
    "video":    ("fa6s.file-video", "purple", {"mkv", "mp4", "avi", "mov", "wmv", "m4v", "ts", "m2ts", "webm", "flv", "mpg", "mpeg", "vob", "3gp"}),
    "audio":    ("fa6s.file-audio", "success", {"flac", "mp3", "aac", "ogg", "opus", "wav", "m4a", "ape", "wv", "alac", "dsf", "dff", "ac3", "dts", "mka"}),
    "image":    ("fa6s.file-image", "teal", {"jpg", "jpeg", "png", "gif", "bmp", "webp", "tif", "tiff", "heic", "svg", "psd"}),
    "archive":  ("fa6s.file-zipper", "orange", {"zip", "rar", "7z", "tar", "gz", "bz2", "xz", "zst", "cab", "r00", "r01"}),
    "disc":     ("fa6s.compact-disc", "info", {"iso", "img", "bin", "cue", "mdf", "mds", "nrg", "vhd", "vhdx", "dmg"}),
    "subtitle": ("fa6s.closed-captioning", "warning", {"srt", "ass", "ssa", "sub", "idx", "sup", "vtt"}),
    "text":     ("fa6s.file-lines", "secondary", {"txt", "nfo", "log", "md", "sfv", "md5", "sha1", "url", "rtf", "doc", "docx"}),
    "book":     ("fa6s.book", "danger", {"pdf", "epub", "fb2", "mobi", "djvu", "azw3", "cbr", "cbz"}),
    "program":  ("fa6s.gear", "primary", {"exe", "msi", "apk", "dll", "bat", "cmd", "appx", "msix"}),
    "code":     ("fa6s.file-code", "info", {"py", "js", "html", "css", "php", "json", "xml", "c", "cpp", "h", "java"}),
}
_EXT = {ext: kind for kind, (_, _, exts) in KINDS.items() for ext in exts}


def kind_of(filename: str) -> str:
    return _EXT.get(os.path.splitext(filename)[1].lower().lstrip("."), "file")


def torrent_kind(files: list[tuple[str, int]]) -> str:
    """Kind of a whole torrent from (path, size): the type holding most of the bytes;
    'folder' when nothing dominates (mixed content)."""
    if not files:
        return "file"
    if len(files) == 1:
        return kind_of(files[0][0])
    by_kind: dict[str, int] = {}
    total = 0
    for path, size in files:
        k = kind_of(path)
        by_kind[k] = by_kind.get(k, 0) + size
        total += size
    kind, size = max(by_kind.items(), key=lambda kv: kv[1])
    return kind if kind != "file" and size >= total * 0.6 else "folder"
