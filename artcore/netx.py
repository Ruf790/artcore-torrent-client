"""Network extras: interface binding (VPN kill-switch), IP filter lists, peer country (DB-IP Lite)."""
from __future__ import annotations

import gzip
import io
import ipaddress
import os
import re
import threading
import time
import urllib.request
import zipfile

USER_AGENT = "ArtCoreClient (+https://github.com/Ruf790/artcore-torrent-client)"


# ── interfaces ──────────────────────────────────────────────────
def interfaces() -> list[tuple[str, str]]:
    """(name, IPv4) of interfaces that have an IPv4 address, loopback excluded.
    On Windows the names are the adapter names from Network Connections ("Ethernet", "NordLynx")."""
    import socket
    try:
        import psutil
        stats = psutil.net_if_stats()
        out = []
        for name, addrs in psutil.net_if_addrs().items():
            if name in stats and not stats[name].isup:
                continue
            for a in addrs:
                if a.family == socket.AF_INET and not a.address.startswith("127."):
                    out.append((name, a.address))
                    break
        return sorted(out)
    except Exception:
        return []


def interface_ip(name: str) -> str | None:
    return next((ip for n, ip in interfaces() if n == name), None)


# ── IP filter ───────────────────────────────────────────────────
_RANGE = re.compile(r"(\d{1,3}(?:\.\d{1,3}){3})\s*-\s*(\d{1,3}(?:\.\d{1,3}){3})")
_CIDR = re.compile(r"^\s*(\d{1,3}(?:\.\d{1,3}){3}/\d{1,2})\s*$")


def _norm(ip: str) -> str:
    # eMule .dat pads octets with zeros ("001.009.096.105") — ipaddress rejects that
    return ".".join(str(int(p)) for p in ip.split("."))


def parse_ip_filter(text: str) -> list[tuple[str, str]]:
    """eMule ipfilter.dat ("a - b , level , name", level < 128 = blocked),
    PeerGuardian .p2p ("name:a-b") and plain CIDR / "a - b" lines."""
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line[0] in "#;/":
            continue
        m = _RANGE.search(line)
        if m:
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 2 and parts[1].isdigit() and int(parts[1]) >= 128:
                continue                                   # .dat: level >= 128 means "allowed"
            try:
                a, b = _norm(m.group(1)), _norm(m.group(2))
                ipaddress.IPv4Address(a), ipaddress.IPv4Address(b)
                out.append((a, b))
            except ValueError:
                pass
            continue
        m = _CIDR.match(line)
        if m:
            try:
                net = ipaddress.IPv4Network(m.group(1), strict=False)
                out.append((str(net[0]), str(net[-1])))
            except ValueError:
                pass
    return out


def _decode_blob(data: bytes) -> str:
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    elif data[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            name = max(z.namelist(), key=lambda n: z.getinfo(n).file_size)
            data = z.read(name)
    return data.decode("utf-8", "replace")


def load_ip_filter(source: str, cache_dir: str, max_age_days: int = 7) -> list[tuple[str, str]]:
    """source = local file or http(s) URL (cached, refreshed weekly). gz / zip are unpacked."""
    if not source:
        return []
    if source.lower().startswith(("http://", "https://")):
        cache = os.path.join(cache_dir, "ipfilter.cache")
        fresh = os.path.isfile(cache) and time.time() - os.path.getmtime(cache) < max_age_days * 86400
        if not fresh:
            req = urllib.request.Request(source, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=60) as r:
                data = r.read()
            with open(cache + ".tmp", "wb") as f:
                f.write(data)
            os.replace(cache + ".tmp", cache)
        with open(cache, "rb") as f:
            return parse_ip_filter(_decode_blob(f.read()))
    with open(source, "rb") as f:
        return parse_ip_filter(_decode_blob(f.read()))


# ── peer country (DB-IP "IP to Country Lite", CC BY 4.0) ────────
DBIP_URL = "https://download.db-ip.com/free/dbip-country-lite-{ym}.mmdb.gz"


class GeoIP:
    """Downloads the free DB-IP country database once a month (~7 MB) in the background."""
    def __init__(self, data_dir: str, lang: str = "ru"):
        self.path = os.path.join(data_dir, "dbip-country-lite.mmdb")
        self.lang = lang
        self.reader = None
        self.status = "off"           # off | loading | ready | error:<text>
        self._lock = threading.Lock()
        self._cache: dict[str, tuple[str, str]] = {}

    def start(self) -> None:
        if self.status in ("loading", "ready"):
            return
        self.status = "loading"
        threading.Thread(target=self._load, daemon=True).start()

    def _load(self) -> None:
        try:
            stale = not os.path.isfile(self.path) or time.time() - os.path.getmtime(self.path) > 32 * 86400
            if stale:
                self._download()
            import maxminddb
            with self._lock:
                self.reader = maxminddb.open_database(self.path)
            self.status = "ready"
        except Exception as e:
            if os.path.isfile(self.path):          # keep using the old database if the update failed
                try:
                    import maxminddb
                    self.reader = maxminddb.open_database(self.path)
                    self.status = "ready"
                    return
                except Exception:
                    pass
            self.status = f"error:{e}"

    def _download(self) -> None:
        now = time.gmtime()
        months = [(now.tm_year, now.tm_mon)]
        months.append((now.tm_year - 1, 12) if now.tm_mon == 1 else (now.tm_year, now.tm_mon - 1))
        last = None
        for y, m in months:                        # this month's file may not be published yet
            url = DBIP_URL.format(ym=f"{y:04d}-{m:02d}")
            try:
                req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(req, timeout=120) as r:
                    data = gzip.decompress(r.read())
                with open(self.path + ".tmp", "wb") as f:
                    f.write(data)
                os.replace(self.path + ".tmp", self.path)
                return
            except Exception as e:
                last = e
        raise last or RuntimeError("download failed")

    def lookup(self, ip: str) -> tuple[str, str]:
        """(ISO code, country name) or ("", "")."""
        if self.reader is None:
            return "", ""
        hit = self._cache.get(ip)
        if hit is not None:
            return hit
        try:
            with self._lock:
                rec = self.reader.get(ip) or {}
            c = rec.get("country") or {}
            names = c.get("names") or {}
            res = (c.get("iso_code", ""), names.get(self.lang) or names.get("en", ""))
        except Exception:
            res = ("", "")
        if len(self._cache) > 20000:
            self._cache.clear()
        self._cache[ip] = res
        return res
