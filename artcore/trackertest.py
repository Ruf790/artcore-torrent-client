# SPDX-License-Identifier: LGPL-3.0-or-later
"""'Test announce': one announce sent by hand, with everything the tracker answered — HTTP code,
headers, the raw body and what it means. libtorrent only reports 'expected value … in bencoded
string'; this shows the PHP error / HTML page / refusal behind it."""
from __future__ import annotations

import gzip
import random
import socket
import struct
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib

import libtorrent as lt

MAX_BODY = 256 << 10


_PID: bytes | None = None


def test_peer_id(prefix: str) -> bytes:
    """Same client prefix as the session (-Ag1000-), random rest — one per client run."""
    global _PID
    if _PID is None:
        chars = b"0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
        _PID = prefix.encode("latin-1")[:8] + bytes(random.choice(chars) for _ in range(12))
    return _PID


def _params(h) -> dict:
    s = h.status()
    ihs = s.info_hashes
    ih = (ihs.v1 if ihs.has_v1() else ihs.v2).to_bytes()[:20]
    return {"info_hash": ih, "uploaded": int(s.total_payload_upload), "downloaded": int(s.total_payload_download),
            "left": max(0, int(s.total_wanted - s.total_wanted_done)) if s.has_metadata else 0,
            "name": s.name}


def run(engine, key: str, url: str, timeout: float = 20.0) -> dict:
    """Blocking (call from a thread). Returns a report dict for the dialog."""
    h = engine.handles[key]
    p = _params(h)
    settings = engine.ses.get_settings()
    agent = settings.get("user_agent", "")
    fp = settings.get("peer_fingerprint", "-Ag1000-")
    pid = test_peer_id(fp)
    port = engine.ses.listen_port()
    rep = {"url": url, "agent": agent, "peer_id": pid.decode("latin-1"), "port": port, **p}
    rep["info_hash_hex"] = p["info_hash"].hex()
    t0 = time.time()
    try:
        if url.lower().startswith("udp://"):
            _udp(rep, url, p, pid, port, timeout)
        elif url.lower().startswith(("http://", "https://")):
            _http(rep, url, p, pid, port, agent, timeout)
        else:
            rep["error"] = "unsupported scheme"
    except Exception as e:
        rep["error"] = f"{type(e).__name__}: {e}"
    rep["ms"] = int((time.time() - t0) * 1000)
    return rep


# ── HTTP(S) ───────────────────────────────────────────────────
def _http(rep, url, p, pid, port, agent, timeout):
    q = {"info_hash": p["info_hash"], "peer_id": pid, "port": str(port), "uploaded": str(p["uploaded"]),
         "downloaded": str(p["downloaded"]), "left": str(p["left"]), "corrupt": "0", "key": "%08X" % random.getrandbits(32),
         "numwant": "50", "compact": "1", "no_peer_id": "1", "supportcrypto": "1"}
    qs = "&".join(f"{k}={urllib.parse.quote_from_bytes(v) if isinstance(v, bytes) else urllib.parse.quote(v)}"
                  for k, v in q.items())
    full = url + ("&" if "?" in url else "?") + qs
    rep["request"] = full
    req = urllib.request.Request(full, headers={"User-Agent": agent, "Accept-Encoding": "gzip",
                                                "Connection": "close"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))     # like libtorrent: no system proxy
    try:
        r = opener.open(req, timeout=timeout)
        status, reason, headers, body = r.status, r.reason, r.headers, r.read(MAX_BODY + 1)
    except urllib.error.HTTPError as e:
        status, reason, headers = e.code, e.reason, e.headers
        try:
            body = e.read(MAX_BODY + 1)
        except Exception:
            body = b""
    rep["status"], rep["reason"] = status, str(reason)
    rep["headers"] = {k: v for k, v in headers.items()
                      if k.lower() in ("content-type", "content-encoding", "content-length", "server",
                                       "location", "x-powered-by", "retry-after", "cf-ray")}
    enc = (headers.get("Content-Encoding") or "").lower()
    if enc == "gzip" or body[:2] == b"\x1f\x8b":
        try:
            body = gzip.decompress(body)
        except Exception:
            pass
    elif enc == "deflate":
        try:
            body = zlib.decompress(body)
        except Exception:
            pass
    rep["truncated"] = len(body) > MAX_BODY
    rep["body"] = body[:MAX_BODY]
    _explain(rep)


def _explain(rep):
    """What the body is: a bencoded answer (fields), or something that breaks clients."""
    body = rep["body"]
    rep["kind"] = "empty" if not body.strip() else "other"
    try:
        d = lt.bdecode(body)
    except Exception:
        d = None
    if isinstance(d, dict) and body.lstrip()[:1] == b"d":
        rep["kind"] = "bencode"
        f = {}
        for k, v in d.items():
            k = k.decode("utf-8", "replace") if isinstance(k, bytes) else str(k)
            if k == "peers":
                f[k] = (len(v) // 6) if isinstance(v, bytes) else len(v or [])
            elif k == "peers6":
                f[k] = len(v) // 18 if isinstance(v, bytes) else 0
            elif isinstance(v, bytes):
                f[k] = v.decode("utf-8", "replace")
            else:
                f[k] = v
        rep["fields"] = f
        # bytes after the end of the dictionary = something printed after the answer (PHP notice…)
        try:
            if lt.bencode(d) != body.strip():
                rep["trailing"] = True
        except Exception:
            pass
        return
    low = body[:4096].lower()
    if b"fatal error" in low or b"uncaught" in low or b"warning</b>" in low or b"notice</b>" in low \
            or b"deprecated</b>" in low or b"parse error" in low or b"stack trace" in low:
        rep["kind"] = "php"
    elif low.lstrip().startswith((b"<!doctype", b"<html", b"<?xml", b"<head", b"<body")) or b"<html" in low:
        rep["kind"] = "html"
    if rep["kind"] != "bencode":
        # a bencoded answer with junk in front (BOM, whitespace, notice before ob_start…)
        i = body.find(b"d8:") if b"d8:" in body else body.find(b"d14:failure")
        if i > 0:
            rep["prefix_junk"] = body[:i][:200]


# ── UDP (BEP 15) ──────────────────────────────────────────────
def _udp(rep, url, p, pid, port, timeout):
    u = urllib.parse.urlsplit(url)
    host, uport = u.hostname, u.port or 80
    rep["request"] = f"udp://{host}:{uport} (connect + announce)"
    addr = socket.getaddrinfo(host, uport, 0, socket.SOCK_DGRAM)[0]
    rep["ip"] = addr[4][0]
    s = socket.socket(addr[0], socket.SOCK_DGRAM)
    s.settimeout(min(timeout / 3, 8))
    try:
        tid = random.getrandbits(32)
        conn = None
        for _ in range(2):
            s.sendto(struct.pack(">QII", 0x41727101980, 0, tid), addr[4])
            try:
                data, _a = s.recvfrom(2048)
            except socket.timeout:
                continue
            if len(data) >= 16:
                action, rtid, conn = struct.unpack(">IIQ", data[:16])
                if action == 3:
                    rep["status"], rep["reason"] = 0, "error"
                    rep["body"] = data[8:]
                    rep["kind"] = "udp_error"
                    return
                break
        if conn is None:
            rep["error"] = "no answer to UDP connect (port closed / filtered / tracker down)"
            return
        tid = random.getrandbits(32)
        pkt = struct.pack(">QII20s20sQQQIIIiH", conn, 1, tid, p["info_hash"], pid, p["downloaded"], p["left"],
                          p["uploaded"], 0, 0, random.getrandbits(32), 50, port)
        s.sendto(pkt, addr[4])
        data, _a = s.recvfrom(65535)
        action = struct.unpack(">I", data[:4])[0]
        rep["status"], rep["reason"] = 200, "UDP"
        rep["body"] = data
        if action == 1 and len(data) >= 20:
            _a, _t, interval, leech, seed = struct.unpack(">IIIII", data[:20])
            rep["kind"] = "bencode"
            rep["fields"] = {"interval": interval, "incomplete": leech, "complete": seed,
                             "peers": (len(data) - 20) // (18 if addr[0] == socket.AF_INET6 else 6)}
        elif action == 3:
            rep["kind"] = "bencode"
            rep["fields"] = {"failure reason": data[8:].decode("utf-8", "replace")}
        else:
            rep["kind"] = "other"
    finally:
        s.close()


def body_preview(body: bytes, limit: int = 6000) -> str:
    """Text as text; binary (compact peers…) as a hex dump."""
    if not body:
        return ""
    sample = body[:limit]
    text = sample.decode("utf-8", "replace")
    bad = sum(1 for ch in text if ch == "�" or (ord(ch) < 32 and ch not in "\r\n\t"))
    if bad > len(text) * 0.1:
        lines = []
        for i in range(0, min(len(sample), 1024), 16):
            chunk = sample[i:i + 16]
            hx = " ".join(f"{b:02x}" for b in chunk)
            asc = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
            lines.append(f"{i:04x}  {hx:<47}  {asc}")
        return "\n".join(lines)
    return text + ("\n…" if len(body) > limit else "")
