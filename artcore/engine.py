"""libtorrent session wrapper for ArtCore Client.

Owns the session, keeps .torrent/resume data on disk so torrents survive a
restart, and exposes a small API the UI calls from its timer.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass

import libtorrent as lt

from . import APP_NAME, VERSION, PEER_ID_CODE, VERSION_TUPLE

# libtorrent download priorities
PRIO_SKIP, PRIO_NORMAL, PRIO_HIGH = 0, 4, 7


def torrent_files(ti: lt.torrent_info) -> list[tuple[int, str, int]]:
    """(index, path, size) for the add dialog; pad files skipped."""
    fs = ti.files()
    return [(i, fs.file_path(i), fs.file_size(i)) for i in range(fs.num_files())
            if not fs.file_flags(i) & lt.file_storage.flag_pad_file]


STATE_NAMES = {
    lt.torrent_status.checking_files:        "checking",
    lt.torrent_status.downloading_metadata:  "metadata",
    lt.torrent_status.downloading:           "downloading",
    lt.torrent_status.finished:              "finished",
    lt.torrent_status.seeding:               "seeding",
    lt.torrent_status.checking_resume_data:  "checking",
}


@dataclass
class TorrentRow:
    key: str            # info-hash (hex), stable id for the UI
    name: str
    size: int
    progress: float     # 0..1
    state: str          # see STATE_NAMES + paused / queued / error
    down_rate: int
    up_rate: int
    seeds: int
    peers: int
    uploaded: int
    downloaded: int
    ratio: float
    eta: int            # seconds, -1 = unknown
    save_path: str
    error: str
    tracker: str
    tracker_host: str   # domain of the first tracker, "DHT" when there is none
    category: str = ""
    kind: str = "file"  # video | audio | … | folder | magnet — for the name icon
    tags: tuple = ()
    hnr: str = ""       # what is left of the tracker's seeding requirement, "" = done / no rule
    done: int = 0       # bytes of the wanted files already here
    sess_down: int = 0  # downloaded / uploaded since the client started
    sess_up: int = 0
    queue: int = -1     # place in the download queue (1-based), -1 = not queued (seeding / forced)
    forced: bool = False   # "Force start": running outside the queue
    order: int = 0      # place in the list ("#" column, Up / Down buttons), 1-based
    moving: int = -1       # "Move…" in progress: percent of the data already at the new place, -1 = not moving
    missing: str = ""      # the data folder / files are gone (drive unplugged, folder renamed): where they were


def _key(h: lt.torrent_handle) -> str:
    ih = h.info_hashes()
    return str(ih.v1) if ih.has_v1() else str(ih.v2)


class Engine:
    def __init__(self, data_dir: str, cfg: dict):
        self.data_dir   = data_dir
        self.resume_dir = os.path.join(data_dir, "resume")
        os.makedirs(self.resume_dir, exist_ok=True)

        self.cfg      = cfg
        if cfg.get("random_port"):                 # BitComet's "random port on start"
            import random
            cfg["port"] = random.randint(20000, 60000)
        self.handles: dict[str, lt.torrent_handle] = {}
        self.messages: list[tuple[str, str]] = []   # (kind, text) log events for the UI
        self.started = time.time()
        self.bound_ip, self.bind_ok = "", True
        self.ip_filter_rules = 0
        self.wan_ip = ""
        self.portmap: dict[str, str] = {}     # "UPnP" / "NAT-PMP" → status text
        # key → {(tracker url, v1/v2, local endpoint): last error, "" = ok}
        self.tr_status: dict[str, dict[tuple, str]] = {}
        self.tr_peers: dict[str, dict[tuple, int]] = {}     # same key → peers in the last tracker reply
        self.task_logs: dict[str, list[tuple[str, str, str]]] = {}   # key → [(time, kind, text)] for "Task logs"
        self.task_seq: dict[str, int] = {}     # key → entries ever written (the list itself is capped at 500)
        self._cur_key = ""                    # torrent of the alert being handled (for _log)
        self._peer_seen: dict[tuple, float] = {}   # (key, ip:port) → when we first saw the connection
        self.stats_path = os.path.join(data_dir, "stats.json")
        try:
            with open(self.stats_path, encoding="utf-8") as f:
                self.stats_saved = json.load(f)
        except (FileNotFoundError, ValueError):
            self.stats_saved = {"all_down": 0, "all_up": 0}
        self._stats_flushed = time.time()
        sv = self.stats_saved
        sv.setdefault("first_run", int(time.time()))
        sv["sessions"] = int(sv.get("sessions", 0)) + 1
        for k in ("uptime", "added", "completed", "wasted", "peak_down", "peak_up"):
            sv.setdefault(k, 0)
        self.days: dict[str, list[int]] = sv.setdefault("days", {})   # "YYYY-MM-DD" → [down, up]
        self._day_mark = (0, 0)              # session totals already put into self.days
        self._moves: dict[str, dict] = {}    # key → {"dest", "files": [(path, size)], "total", "pct", "at"}
        self.moved_names: list[tuple[str, str, str]] = []      # (name, key, "" | error) for the UI
        self.checked_names: list[tuple[str, str, float]] = []  # (name, key, progress) after a hash check
        self.missing_names: list[tuple[str, str, str]] = []    # (name, key, folder) data found missing
        self._export_pending: set[str] = set()   # .torrent copies to make after the next resume write
        # per-tracker totals: host → [uploaded, downloaded]; seen = last counted all_time values per torrent
        self.tr_totals: dict[str, list[int]] = self.stats_saved.get("trackers", {})
        self._tr_seen: dict[str, list[int]] = self.stats_saved.get("seen", {})
        self._low_space: set[str] = set()       # paused because the disk is (almost) full
        self.space_events: list[tuple[str, str, int]] = []   # ("paused" | "resumed", name, free bytes)
        self.finished_names: list[str] = []    # for tray notifications
        self.stopped_names: list[str] = []     # paused by a stop-seeding rule
        self._hosts: dict[str, str] = {}
        self._kinds: dict[str, str] = {}
        # per-torrent options libtorrent doesn't keep in resume data (stop-seeding rules)
        self.opts_path = os.path.join(data_dir, "torrent_opts.json")
        try:
            with open(self.opts_path, encoding="utf-8") as f:
                self.opts: dict[str, dict] = json.load(f)
        except (FileNotFoundError, ValueError):
            self.opts = {}

        # bans: ip → {reason, kind, client, time, expires}; user's IP-filter ranges are kept so both
        # can be combined. Old files stored ip → reason text: converted on load.
        self.bans_path = os.path.join(data_dir, "bans.json")
        try:
            with open(self.bans_path, encoding="utf-8") as f:
                raw = json.load(f)
        except (FileNotFoundError, ValueError):
            raw = {}
        self.bans: dict[str, dict] = {ip: _ban_record(v) for ip, v in raw.items()}
        self._user_ranges: list[tuple[str, str]] = []
        self._peer_prog: dict[tuple, tuple] = {}      # (key, ip) → (uploaded to it, its progress) at first sight
        self._miss_hits: dict[str, int] = {}          # missing-files scans in a row that found nothing
        self._limited: set[str] = set()                # torrents whose limits come from a tracker rule
        self._cross_check: set[str] = set()            # cross-seeded, waiting for the hash check
        self.cross_results: list[tuple[str, str, float]] = []   # (name, key, progress) for the UI

        # removed torrents (newest first) + a copy of their resume data in history/
        self.history_dir = os.path.join(data_dir, "history")
        self.history_path = os.path.join(data_dir, "history.json")
        try:
            with open(self.history_path, encoding="utf-8") as f:
                self.history: list[dict] = json.load(f)
        except (FileNotFoundError, ValueError):
            self.history = []
        self.disk: dict = {}             # live disk numbers (see _disk_stats)
        self._perf_seen: dict[str, float] = {}
        self.port_check: dict = {}       # last result of check_port(): {"port", "open", "ip", "error", "at"}

        self.ses = lt.session(self._settings())
        # the user's order of the list (Up / Down); libtorrent's download queue follows it
        self.order_path = os.path.join(data_dir, "order.json")
        try:
            with open(self.order_path, encoding="utf-8") as f:
                self.order: list[str] = [str(k) for k in json.load(f)]
        except (FileNotFoundError, ValueError, TypeError):
            self.order = []
        self._load_state()
        self._order_sync()
        self._low_space = {k for k, o in self.opts.items() if o.get("low_space") and k in self.handles}
        if self.bans:
            self._apply_filter()

    # ── settings ───────────────────────────────────────────────
    def _settings(self) -> dict:
        c = self.cfg
        port = int(c.get("port", 51413))
        enc = {"prefer": (lt.enc_policy.pe_enabled, lt.enc_level.pe_both),
               "require": (lt.enc_policy.pe_forced, lt.enc_level.pe_rc4),
               "disable": (lt.enc_policy.pe_disabled, lt.enc_level.pe_both)}[c.get("encryption", "prefer")]
        # Interface binding (VPN kill-switch): when the chosen adapter is gone, bind to loopback,
        # so nothing leaks through the normal connection.
        bind = c.get("bind_interface", "")
        if bind:
            from .netx import interface_ip
            ip = interface_ip(bind)
            self.bound_ip = ip or "127.0.0.1"
            self.bind_ok = ip is not None
            listen = f"{self.bound_ip}:{port}"
            outgoing = self.bound_ip
        else:
            self.bound_ip, self.bind_ok = "", True
            listen = f"0.0.0.0:{port},[::]:{port}"
            outgoing = ""
        return {
            # Client identity: what the tracker sees in peer_id / User-Agent
            "peer_fingerprint": lt.generate_fingerprint(PEER_ID_CODE, VERSION_TUPLE[0], VERSION_TUPLE[1], 0, 0),
            # short on purpose: TBDev / TSSE-style trackers keep the agent in CHAR(32) columns and
            # a longer one makes their announce.php fail ("ArtCore/1.0.24 libtorrent/2.1.1.0" = 33)
            "user_agent":       f"{APP_NAME}/{VERSION}",
            "listen_interfaces": listen,
            "outgoing_interfaces": outgoing,
            "out_enc_policy": int(enc[0]), "in_enc_policy": int(enc[0]),
            "allowed_enc_level": int(enc[1]),
            "enable_dht":   bool(c.get("dht", True)),
            "enable_lsd":   bool(c.get("lsd", True)),
            "enable_upnp":  bool(c.get("upnp", True)),
            "enable_natpmp": bool(c.get("upnp", True)),
            "enable_outgoing_utp": bool(c.get("utp", True)),
            "enable_incoming_utp": bool(c.get("utp", True)),
            **_proxy_settings(c),
            **_disk_settings(c),
            "download_rate_limit": int(c.get("dl_limit", 0)) * 1024,
            "upload_rate_limit":   int(c.get("ul_limit", 0)) * 1024,
            "active_downloads": int(c.get("active_downloads", 5)),
            "active_seeds":     int(c.get("active_seeds", 20)),
            "active_limit":     int(c.get("active_downloads", 5)) + int(c.get("active_seeds", 20)),
            # connection limits — weak routers drop out with hundreds of connections
            "connections_limit":   int(c.get("max_connections", 200)) or 65535,
            "unchoke_slots_limit": int(c.get("upload_slots", 8)) or -1,
            # Private-tracker friendly: announce honestly to every tracker tier
            "announce_to_all_trackers": True,
            "announce_to_all_tiers":    True,
            "alert_mask": (lt.alert_category.error | lt.alert_category.status
                           | lt.alert_category.storage | lt.alert_category.tracker
                           | lt.alert_category.port_mapping | lt.alert_category.performance_warning),
        }

    def apply_settings(self, cfg: dict) -> None:
        self.cfg = cfg
        self.ses.apply_settings(self._settings())
        for key, h in self.handles.items():
            self._apply_conn_limits(key, h)

    def _apply_conn_limits(self, key: str, h) -> None:
        """Per-torrent connections / upload slots: its own value, else the default from Settings
        (0 = no limit; libtorrent takes -1 for that)."""
        try:
            o = self.opts.get(key, {})
            conn = int(o.get("max_conn") or self.cfg.get("conn_per_torrent", 0) or 0)
            ups = int(o.get("max_up") or self.cfg.get("slots_per_torrent", 0) or 0)
            h.set_max_connections(conn if conn > 0 else -1)
            h.set_max_uploads(ups if ups > 0 else -1)
        except Exception:
            pass

    def check_binding(self) -> bool | None:
        """Re-resolve the bound interface (VPN reconnects change its IP, a drop removes it).
        Returns None if nothing changed, else the new "ok" state."""
        bind = self.cfg.get("bind_interface", "")
        if not bind:
            return None
        from .netx import interface_ip
        ip = interface_ip(bind) or "127.0.0.1"
        if ip == self.bound_ip:
            return None
        self.ses.apply_settings(self._settings())
        self._log("network" if self.bind_ok else "error",
                  f"{bind}: {ip}" if self.bind_ok else f"{bind}: interface down — traffic blocked (kill-switch)")
        return self.bind_ok

    def set_ip_filter(self, ranges: list[tuple[str, str]]) -> None:
        self._user_ranges = list(ranges)
        self.ip_filter_rules = len(ranges)
        self._apply_filter()

    def _apply_filter(self) -> None:
        f = lt.ip_filter()
        for a, b in self._user_ranges:
            f.add_rule(a, b, 1)
        for ip in self.bans:
            try:
                f.add_rule(ip, ip, 1)
            except Exception:
                pass
        self.ses.set_ip_filter(f)      # libtorrent drops connected peers that became blocked

    # ── bans ───────────────────────────────────────────────────
    def ban(self, ip: str, reason: str, kind: str = "manual", client: str = "") -> None:
        """kind: leech (client by peer-id / name, permanent) | fake (fake progress, expires after
        FAKE_BAN_HOURS — addresses get reused) | manual (from the Peers tab, permanent)."""
        if ip in self.bans:
            return
        now = int(time.time())
        self.bans[ip] = {"reason": reason, "kind": kind, "client": client, "time": now,
                         "expires": now + FAKE_BAN_HOURS * 3600 if kind == "fake" else 0}
        self._save_bans()
        self._apply_filter()
        self._log("network", f"ban {ip}: {reason}")

    def unban(self, ips) -> int:
        n = 0
        for ip in list(ips):
            if self.bans.pop(ip, None) is not None:
                n += 1
                self._log("network", f"unban {ip}")
        if n:
            self._save_bans()
            self._apply_filter()
        return n

    def expire_bans(self) -> int:
        now = time.time()
        old = [ip for ip, b in self.bans.items() if b.get("expires") and b["expires"] <= now]
        return self.unban(old)

    def clear_bans(self) -> None:
        self.bans.clear()
        self._save_bans()
        self._apply_filter()

    def _save_bans(self) -> None:
        tmp = self.bans_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.bans, f, indent=1)
        os.replace(tmp, self.bans_path)

    def _track_peers(self) -> None:
        """Remember when each connection was first seen ('Connected' column of the Peers tab)."""
        now, seen = time.time(), set()
        for key, h in self.handles.items():
            try:
                if not h.is_valid() or not h.status().num_peers:
                    continue
                for p in h.get_peer_info():
                    sk = (key, f"{p.ip[0]}:{p.ip[1]}")
                    seen.add(sk)
                    self._peer_seen.setdefault(sk, now)
            except Exception:
                continue
        for sk in [k for k in self._peer_seen if k not in seen]:
            del self._peer_seen[sk]

    def _scan_peers(self) -> None:
        """Ban leech-only clients by peer-id / name, and peers that keep taking data while their
        reported progress doesn't move (fake progress: they never share what they got)."""
        for key, h in self.handles.items():
            if not h.is_valid():
                continue
            s = h.status()
            if s.flags & lt.torrent_flags.paused or not s.has_metadata:
                continue
            size = max(s.total_wanted, 1)
            for p in h.get_peer_info():
                ip = p.ip[0]
                if ip in self.bans:
                    continue
                client = p.client.decode("utf-8", "replace") if isinstance(p.client, bytes) else str(p.client)
                pid = p.pid.to_bytes()[:8]
                bad = _bad_client(client, pid)
                if bad:
                    self.ban(ip, bad, "leech", client or pid[:8].decode("latin-1", "replace"))
                    continue
                seen = self._peer_prog.get((key, ip))
                if seen is None:
                    self._peer_prog[(key, ip)] = (p.total_upload, p.progress)
                    continue
                sent = p.total_upload - seen[0]
                gained = (p.progress - seen[1]) * size
                if sent > max(FAKE_MIN_BYTES, size * 0.05) and gained < sent * 0.1:
                    self.ban(ip, f"fake progress: got {sent >> 20} MiB, progress +{max(gained, 0) / size:.1%}",
                             "fake", client)
        if len(self._peer_prog) > 20000:
            self._peer_prog.clear()

    # ── per-tracker totals ─────────────────────────────────────
    def _count_trackers(self) -> None:
        """Add what each torrent moved since the last count to its tracker. A torrent seen for the
        first time brings its whole history (all_time_* is kept in resume data)."""
        for key, h in self.handles.items():
            if not h.is_valid():
                continue
            s = h.status()
            up, down = int(s.all_time_upload), int(s.all_time_download)
            last = self._tr_seen.get(key)
            du, dd = (up, down) if last is None else (up - last[0], down - last[1])
            self._tr_seen[key] = [up, down]
            if du <= 0 and dd <= 0:
                continue
            tot = self.tr_totals.setdefault(self._host(key, h), [0, 0])
            tot[0] += max(du, 0)
            tot[1] += max(dd, 0)

    def tracker_totals(self) -> list[tuple[str, int, int, int]]:
        """[(host, uploaded, downloaded, torrents now)] sorted by uploaded."""
        self._count_trackers()
        count: dict[str, int] = {}
        for key, h in self.handles.items():
            if h.is_valid():
                host = self._host(key, h)
                count[host] = count.get(host, 0) + 1
        hosts = set(self.tr_totals) | set(count)
        return sorted(((hst, *self.tr_totals.get(hst, [0, 0]), count.get(hst, 0)) for hst in hosts),
                      key=lambda x: -x[1])

    # ── low disk space ─────────────────────────────────────────
    def _check_space(self) -> None:
        """Pause downloads when the target disk has less than min_free_gb, resume when 1 GB more is free."""
        import shutil
        need = float(self.cfg.get("min_free_gb", 0) or 0) * (1 << 30)
        if not need and not self._low_space:
            return
        free_by_path: dict[str, int] = {}

        def free(path: str) -> int:
            if path not in free_by_path:
                try:
                    free_by_path[path] = shutil.disk_usage(path).free
                except OSError:
                    free_by_path[path] = 1 << 62
            return free_by_path[path]

        for key, h in self.handles.items():
            if not h.is_valid():
                continue
            s = h.status()
            if key in self._low_space:
                if not need or free(s.save_path) >= need + (1 << 30):
                    self._low_space.discard(key)
                    self.opts.get(key, {}).pop("low_space", None)
                    self._save_opts()
                    self.resume(key)
                    self.space_events.append(("resumed", s.name, free(s.save_path)))
                continue
            if need and s.state == lt.torrent_status.downloading and not (s.flags & lt.torrent_flags.paused) \
                    and free(s.save_path) < need:
                self.stop(key)
                self._low_space.add(key)
                self.opts.setdefault(key, {})["low_space"] = True    # survives a restart
                self._save_opts()
                self.space_events.append(("paused", s.name, free(s.save_path)))
                self._log("error", f"low disk space ({free(s.save_path) >> 20} MiB free): paused {s.name}")

    # ── files already on disk ──────────────────────────────────
    @staticmethod
    def existing_files(ti, save_path: str) -> tuple[int, int, int, bool]:
        """(files present, files total, bytes present, every file present with its full size)."""
        fs = ti.files()
        have = total = got = 0
        full = True
        for i in range(fs.num_files()):
            if fs.file_flags(i) & lt.file_storage.flag_pad_file:
                continue
            total += 1
            p = os.path.join(save_path, fs.file_path(i))
            try:
                size = os.path.getsize(p)
            except OSError:
                full = False
                continue
            if size > 0:
                have += 1
                got += min(size, fs.file_size(i))
            if size != fs.file_size(i):
                full = False
        return have, total, got, full and have == total

    # ── tracker rules: H&R + per-tracker speed ─────────────────
    def tracker_rule(self, host: str) -> dict:
        return self.cfg.get("tracker_rules", {}).get(host, {})

    def hnr_left(self, key: str, h=None, s=None) -> str:
        """Text like '12 ч / рейтинг 1.00' while the tracker's seeding requirement isn't met."""
        h = h or self.handles.get(key)
        if not h:
            return ""
        rule = self.tracker_rule(self._host(key, h))
        need_h, need_r = float(rule.get("min_hours", 0) or 0), float(rule.get("min_ratio", 0) or 0)
        if not need_h and not need_r:
            return ""
        s = s or h.status()
        if not s.has_metadata:
            return ""
        hours = s.seeding_duration.total_seconds() / 3600
        ratio = _ratio(s.all_time_upload, s.all_time_download, s.total_wanted_done)
        if (need_h and hours >= need_h) or (need_r and ratio >= need_r):
            return ""
        from .i18n import t
        parts = []
        if need_h:
            parts.append(t("hnr_hours", f"{need_h - hours:.1f}"))
        if need_r:
            parts.append(t("hnr_ratio", f"{need_r:.2f}"))
        return t("hnr_or").join(parts)

    def _apply_tracker_limits(self) -> None:
        """A tracker's limit is shared equally by its running torrents (libtorrent has no
        per-tracker limit). Torrents with their own limit (Properties) are left alone."""
        groups: dict[str, list] = {}
        for key, h in self.handles.items():
            if not h.is_valid():
                continue
            if self.opts.get(key, {}).get("soft_pause"):     # paused: keep transfers frozen
                if h.download_limit() != 1 or h.upload_limit() != 1:
                    self._freeze(h)
                continue
            if self.opts.get(key, {}).get("own_limits"):
                continue
            host = self._host(key, h)
            r = self.tracker_rule(host)
            if r.get("dl_limit") or r.get("ul_limit"):
                if not (h.flags() & lt.torrent_flags.paused):
                    groups.setdefault(host, []).append((key, h))
            elif key in self._limited:
                h.set_download_limit(-1)
                h.set_upload_limit(-1)
                self._limited.discard(key)
        for host, items in groups.items():
            r = self.tracker_rule(host)
            n = len(items)
            dl = int(r.get("dl_limit", 0)) * 1024 // n
            ul = int(r.get("ul_limit", 0)) * 1024 // n
            for key, h in items:
                h.set_download_limit(dl or -1)
                h.set_upload_limit(ul or -1)
                self._limited.add(key)

    # ── cross-seed ─────────────────────────────────────────────
    def find_cross(self, ti) -> dict | None:
        """Existing torrent whose files hold the same data (same sizes) as torrent_info `ti`:
        {"key", "name", "save_path", "renamed": {index: path}}. Names may differ between trackers."""
        fs = ti.files()
        new = [(i, fs.file_path(i), fs.file_size(i)) for i in range(fs.num_files())
               if not fs.file_flags(i) & lt.file_storage.flag_pad_file]
        if not new or sum(sz for _, _, sz in new) < 1 << 20:
            return None
        for key, h in self.handles.items():
            if not h.is_valid():
                continue
            s = h.status()
            if not s.has_metadata or s.total_wanted_done <= 0:
                continue
            efs = h.torrent_file().files()
            old = [(efs.file_path(i), efs.file_size(i)) for i in range(efs.num_files())
                   if not efs.file_flags(i) & lt.file_storage.flag_pad_file]
            renamed = _match_files(new, old)
            if renamed is not None:
                return {"key": key, "name": s.name, "save_path": s.save_path, "renamed": renamed}
        return None

    def add_cross(self, path: str, match: dict, paused: bool = False) -> str:
        """Add .torrent onto another torrent's data. upload_mode: nothing is downloaded (so
        mismatching pieces can't overwrite the other torrent's files) until the check passes."""
        atp = lt.add_torrent_params()
        atp.ti = lt.torrent_info(path)
        if match["renamed"]:
            atp.renamed_files = match["renamed"]
        atp.flags |= lt.torrent_flags.upload_mode
        key = self._add(atp, match["save_path"], paused)
        self._cross_check.add(key)
        return key

    def _check_cross(self) -> None:
        for key in list(self._cross_check):
            h = self.handles.get(key)
            if not h or not h.is_valid():
                self._cross_check.discard(key)
                continue
            s = h.status()
            if s.state in (lt.torrent_status.checking_files, lt.torrent_status.checking_resume_data):
                continue
            self._cross_check.discard(key)
            if s.progress >= 1.0:
                h.unset_flags(lt.torrent_flags.upload_mode)
            self.cross_results.append((s.name, key, s.progress))
            self._log("torrent" if s.progress >= 1.0 else "error",
                      f"cross-seed {s.name}: {s.progress:.1%}")

    # ── persistence ────────────────────────────────────────────
    def _resume_path(self, key: str) -> str:
        return os.path.join(self.resume_dir, key + ".fastresume")

    def _load_state(self) -> None:
        for fn in sorted(os.listdir(self.resume_dir)):
            if not fn.endswith(".fastresume"):
                continue
            path = os.path.join(self.resume_dir, fn)
            try:
                with open(path, "rb") as f:
                    atp = lt.read_resume_data(f.read())
                if atp.ti is not None and not (atp.comment or atp.created_by or atp.creation_date):
                    self._restore_meta(atp)
                key = fn[:-len(".fastresume")]
                if atp.ti is not None and any(atp.have_pieces) and not self.opts.get(key, {}).get("missing") \
                        and _data_missing(atp.save_path, atp.ti.files(), dict(atp.renamed_files),
                                          list(atp.file_priorities)):
                    # without this libtorrent would reject the resume data, find nothing and quietly
                    # download everything again (into a new empty folder) — stop it and say so instead
                    self.opts.setdefault(key, {})["missing"] = atp.save_path
                    hp = list(atp.have_pieces)
                    self.opts[key]["missing_pct"] = sum(hp) / len(hp) if hp else 0.0
                if self.opts.get(key, {}).get("missing"):
                    atp.flags |= lt.torrent_flags.paused
                    atp.flags &= ~lt.torrent_flags.auto_managed
                h = self.ses.add_torrent(atp)
                self.handles[_key(h)] = h
                self._apply_conn_limits(key, h)
                if self.opts.get(key, {}).get("missing"):
                    self.missing_names.append((atp.name or (atp.ti.name() if atp.ti else key), key,
                                               self.opts[key]["missing"]))
            except Exception as e:  # corrupt resume file — skip it, keep the rest
                self._log("error", f"{fn}: {e}")
        self._save_opts()

    def _restore_meta(self, atp) -> None:
        """Torrents saved by older versions lost comment / creator / creation date (and publisher).
        Take them back from the same torrent in qBittorrent's BT_backup, if it is there."""
        try:
            from .importers import qbittorrent_dirs
            ih = atp.ti.info_hashes()
            name = (str(ih.v1) if ih.has_v1() else str(ih.v2)) + ".torrent"
            for d in qbittorrent_dirs():
                src = os.path.join(d, name)
                if not os.path.isfile(src):
                    continue
                with open(src, "rb") as f:
                    raw = f.read()
                t = lt.torrent_info(raw)
                if _key_of(t.info_hashes()) != _key_of(ih):
                    continue
                atp.ti = t                     # same info dict, plus the root fields
                atp.comment, atp.created_by, atp.creation_date = t.comment(), t.creator(), t.creation_date()
                root = lt.bdecode(raw) or {}
                o = self.opts.setdefault(name[:-8] if ih.has_v1() else _key_of(ih), {})
                for k, ok in ((b"publisher", "publisher"), (b"publisher-url", "publisher_url")):
                    v = root.get(k)
                    if v and not o.get(ok):
                        o[ok] = v.decode("utf-8", "replace") if isinstance(v, bytes) else str(v)
                return
        except Exception:
            pass

    def save_all(self, wait: float = 5.0) -> None:
        """Request resume data for every torrent and write it (used on exit)."""
        pending = 0
        for h in self.handles.values():
            if h.is_valid():
                h.save_resume_data(lt.torrent_handle.save_info_dict)
                pending += 1
        import time
        deadline = time.time() + wait
        while pending > 0 and time.time() < deadline:
            self.ses.wait_for_alert(500)
            for a in self.ses.pop_alerts():
                if isinstance(a, (lt.save_resume_data_alert, lt.save_resume_data_failed_alert)):
                    pending -= 1
                    if isinstance(a, lt.save_resume_data_alert):
                        self._write_resume(a)

    def _write_resume(self, a: lt.save_resume_data_alert) -> None:
        key = _key(a.handle)
        p = a.params
        # libtorrent's resume data keeps only the info dict: comment, "created by" and the creation
        # date would be gone after a restart unless we put them into the resume params ourselves
        try:
            ti = a.handle.torrent_file()
            if ti is not None:
                if not p.comment and ti.comment():
                    p.comment = ti.comment()
                if not p.created_by and ti.creator():
                    p.created_by = ti.creator()
                if not p.creation_date and ti.creation_date():
                    p.creation_date = ti.creation_date()
        except Exception:
            pass
        tmp = self._resume_path(key) + ".tmp"
        with open(tmp, "wb") as f:
            f.write(lt.write_resume_data_buf(p))
        os.replace(tmp, self._resume_path(key))
        if key in self._export_pending:
            self._export_pending.discard(key)
            self._auto_export(key, "export_dir")

    # ── adding / removing ──────────────────────────────────────
    def add_torrent_file(self, path: str, save_path: str | None = None, paused: bool = False,
                         file_priorities: list[int] | None = None, seed_mode: bool = False,
                         renames: dict[int, str] | None = None, task: dict | None = None) -> str:
        ti = lt.torrent_info(path)
        for idx, new_path in (renames or {}).items():     # name edited in the add dialog
            ti.rename_file(idx, new_path)
        atp = lt.add_torrent_params()
        atp.ti = ti
        if seed_mode:      # files known to be complete: skip the full check, pieces are verified when sent
            atp.flags |= lt.torrent_flags.seed_mode
        if file_priorities is not None:
            atp.file_priorities = file_priorities
        key = self._add(atp, save_path, paused)
        if task:
            self.set_task_props(key, task)
        return key

    def add_magnet(self, uri: str, save_path: str | None = None, paused: bool = False) -> str:
        atp = lt.parse_magnet_uri(uri)
        return self._add(atp, save_path, paused)

    def _add(self, atp: lt.add_torrent_params, save_path: str | None, paused: bool) -> str:
        atp.save_path = save_path or self.cfg.get("save_path") or os.path.expanduser("~/Downloads")
        if self.cfg.get("preallocate"):     # reserve the whole size at once: no fragmentation, no "disk full" later
            atp.storage_mode = lt.storage_mode_t.storage_mode_allocate
        if paused:
            atp.flags |= lt.torrent_flags.paused
            atp.flags &= ~lt.torrent_flags.auto_managed
        h = self.ses.add_torrent(atp)
        key = _key(h)
        if key in self.handles:
            raise ValueError("Этот торрент уже добавлен")
        self.handles[key] = h
        self._apply_conn_limits(key, h)
        self.count_event("added")
        self._add_auto_trackers(h)
        if h.status().has_metadata:
            self._export_pending.add(key)     # once its resume data is written (v2 piece layers)
        h.save_resume_data(lt.torrent_handle.save_info_dict)
        return key

    def _add_auto_trackers(self, h: lt.torrent_handle) -> None:
        """Settings → Trackers: extra trackers for public torrents (never for private ones —
        a private tracker counts any other announce as cheating)."""
        if not self.cfg.get("auto_trackers_on"):
            return
        urls = [u.strip() for u in self.cfg.get("auto_trackers", "").splitlines() if u.strip()]
        try:
            ti = h.torrent_file()
            if not urls or ti is None or ti.priv():
                return
            have = {t["url"] for t in h.trackers()}
            tier = max([t["tier"] for t in h.trackers()] + [-1]) + 1
            for u in urls:
                if u not in have:
                    h.add_tracker({"url": u, "tier": tier})
        except Exception as e:
            self._log("error", f"auto trackers: {e}")

    def add_params(self, atp: lt.add_torrent_params) -> str:
        """Add prepared params (import from other clients); save_path is already set."""
        h = self.ses.add_torrent(atp)
        key = _key(h)
        if key in self.handles:
            raise ValueError("Этот торрент уже добавлен")
        self.handles[key] = h
        h.save_resume_data(lt.torrent_handle.save_info_dict)
        return key

    def remove(self, key: str, delete_files: bool = False) -> None:
        self.tr_status.pop(key, None)
        self._limited.discard(key)
        self._cross_check.discard(key)
        self._low_space.discard(key)
        self._moves.pop(key, None)
        self._count_trackers()           # bank its last traffic before it disappears
        self._tr_seen.pop(key, None)
        h = self.handles.pop(key, None)
        if h is None:
            return
        self._to_history(key, h, delete_files)
        self.ses.remove_torrent(h, lt.options_t.delete_files if delete_files else 0)
        if self.opts.pop(key, None) is not None:
            self._save_opts()
        try:
            os.remove(self._resume_path(key))
        except FileNotFoundError:
            pass

    # ── history of removed torrents ────────────────────────────
    HISTORY_MAX = 300

    def _to_history(self, key: str, h: lt.torrent_handle, deleted: bool) -> None:
        """Keep what is needed to bring a removed torrent back: its resume data and options."""
        try:
            s = h.status()
            buf = None
            try:
                with open(self._resume_path(key), "rb") as f:
                    buf = f.read()
            except OSError:
                pass
            if not buf:
                ti = h.torrent_file()
                atp = lt.add_torrent_params()
                if ti is not None:
                    atp.ti = lt.torrent_info(ti)
                else:
                    atp = lt.parse_magnet_uri(lt.make_magnet_uri(h))
                atp.save_path = s.save_path
                buf = lt.write_resume_data_buf(atp)
            os.makedirs(self.history_dir, exist_ok=True)
            with open(os.path.join(self.history_dir, key + ".fastresume"), "wb") as f:
                f.write(buf)
            o = {k: v for k, v in self.opts.get(key, {}).items() if k not in ("soft_pause", "low_space")}
            self.history = [e for e in self.history if e.get("key") != key]
            self.history.insert(0, {
                "key": key, "name": s.name or key, "size": s.total_wanted, "progress": s.progress,
                "save_path": s.save_path, "removed": int(time.time()), "deleted_files": bool(deleted),
                "tracker": self._host(key, h), "uploaded": max(s.all_time_upload, s.total_payload_upload),
                "downloaded": max(s.all_time_download, s.total_payload_download), "opts": o,
            })
            for e in self.history[self.HISTORY_MAX:]:
                self._drop_history_file(e.get("key", ""))
            del self.history[self.HISTORY_MAX:]
            self._save_history()
        except Exception as e:
            self._log("error", f"history: {e}")

    def _drop_history_file(self, key: str) -> None:
        try:
            os.remove(os.path.join(self.history_dir, key + ".fastresume"))
        except OSError:
            pass

    def _save_history(self) -> None:
        tmp = self.history_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.history, f, ensure_ascii=False)
        os.replace(tmp, self.history_path)

    def restore_from_history(self, key: str, paused: bool = False) -> str:
        """Add a removed torrent back. If its files were deleted it simply downloads again."""
        if key in self.handles:
            raise ValueError("Этот торрент уже добавлен")
        e = next((e for e in self.history if e.get("key") == key), None)
        if e is None:
            raise ValueError("Нет в истории")
        with open(os.path.join(self.history_dir, key + ".fastresume"), "rb") as f:
            atp = lt.read_resume_data(f.read())
        if e.get("deleted_files"):            # nothing is on disk any more: forget the pieces we had
            atp.have_pieces = []
            atp.verified_pieces = []
            atp.unfinished_pieces = {}
            atp.total_downloaded = atp.total_uploaded = 0
        atp.flags &= ~lt.torrent_flags.paused
        atp.flags |= lt.torrent_flags.auto_managed
        if paused:
            atp.flags |= lt.torrent_flags.paused
            atp.flags &= ~lt.torrent_flags.auto_managed
        if e.get("opts"):
            self.opts[key] = dict(e["opts"])
            self._save_opts()
        new = self.add_params(atp)
        self.forget_history([key])
        return new

    def forget_history(self, keys: list[str]) -> None:
        keys = set(keys)
        for k in keys:
            self._drop_history_file(k)
        self.history = [e for e in self.history if e.get("key") not in keys]
        self._save_history()

    def clear_history(self) -> None:
        self.forget_history([e.get("key", "") for e in self.history])

    # ── queue ──────────────────────────────────────────────────
    def _order_sync(self) -> bool:
        """Drop removed torrents from the order, append new ones (downloads in their queue order
        first time round). Returns True if it changed."""
        have = set(self.handles)
        new = [k for k in self.order if k in have]
        seen = set(new)
        if len(seen) != len(new):
            new = list(dict.fromkeys(new))
            seen = set(new)
        extra = [k for k in self.handles if k not in seen]
        if extra:
            def qpos(k):
                try:
                    q = self.handles[k].status().queue_position
                except Exception:
                    q = -1
                return q if q >= 0 else 1 << 30
            extra.sort(key=qpos)
            new += extra
        if new != self.order:
            self.order = new
            self._save_order()
            return True
        return False

    def _save_order(self) -> None:
        try:
            tmp = self.order_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.order, f)
            os.replace(tmp, self.order_path)
        except OSError:
            pass

    def queue_move(self, keys: list[str], where: str) -> None:
        """where: up | down | top | bottom. Moves the torrents in the list order (any state, like
        BitComet); several keep their order among themselves. Downloads waiting in libtorrent's queue
        are then put in the same order."""
        self._order_sync()
        sel = set(k for k in keys if k in self.handles)
        if not sel:
            return
        o = self.order
        if where == "top":
            o = [k for k in o if k in sel] + [k for k in o if k not in sel]
        elif where == "bottom":
            o = [k for k in o if k not in sel] + [k for k in o if k in sel]
        elif where == "up":
            o = list(o)
            for i in range(1, len(o)):
                if o[i] in sel and o[i - 1] not in sel:
                    o[i - 1], o[i] = o[i], o[i - 1]
        elif where == "down":
            o = list(o)
            for i in range(len(o) - 2, -1, -1):
                if o[i] in sel and o[i + 1] not in sel:
                    o[i], o[i + 1] = o[i + 1], o[i]
        if o == self.order:
            return
        self.order = o
        self._save_order()
        # libtorrent's download queue in the same order (seeds / finished have no place in it)
        for k in o:
            h = self.handles.get(k)
            try:
                if h is not None and h.status().queue_position >= 0:
                    h.queue_position_bottom()
            except Exception:
                pass

    def force_start(self, key: str) -> None:
        """Start now, ignoring the limits on active downloads / seeds."""
        h = self.handles.get(key)
        if h:
            self._unfreeze(key, h)
            h.unset_flags(lt.torrent_flags.auto_managed)
            h.resume()
            h.save_resume_data(lt.torrent_handle.save_info_dict)

    # ── port check ─────────────────────────────────────────────
    PORT_CHECKERS = ("https://ifconfig.co/port/{}", "https://ipconfig.io/port/{}")

    def check_port(self, timeout: float = 15.0) -> dict:
        """Ask an outside service to connect to our listen port (blocking — run it in a thread)."""
        import urllib.request
        port = self.ses.listen_port()
        res = {"port": port, "open": None, "ip": "", "error": "", "at": time.time()}
        for url in self.PORT_CHECKERS:
            try:
                req = urllib.request.Request(url.format(port), headers={
                    "Accept": "application/json", "User-Agent": "ArtCore-Client"})
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    data = json.loads(r.read().decode("utf-8", "replace"))
                res.update(open=bool(data.get("reachable")), ip=str(data.get("ip", "")), error="")
                break
            except Exception as e:
                res["error"] = str(getattr(e, "reason", None) or e)
        if res["ip"] and not self.wan_ip:
            self.wan_ip = res["ip"]
        self.port_check = res
        return res

    def port_state(self) -> str:
        """open | closed | unknown — incoming connections prove the port is open."""
        st = self.ses.status()
        if st.has_incoming_connections:
            return "open"
        pc = self.port_check
        if pc.get("port") == self.ses.listen_port() and pc.get("open") is not None:
            return "open" if pc["open"] else "closed"
        return "unknown"

    # ── peers ──────────────────────────────────────────────────
    def add_peer(self, key: str, text: str) -> int:
        """Connect to peers typed as ip:port / [ipv6]:port (one per line or comma separated)."""
        import ipaddress
        h = self.handles.get(key)
        if not h:
            return 0
        n = 0
        for part in text.replace(",", "\n").replace(";", "\n").split():
            part = part.strip()
            if part.startswith("["):
                host, _, port = part[1:].partition("]:")
            else:
                host, _, port = part.rpartition(":")
            try:
                ipaddress.ip_address(host)
                p = int(port)
                if not 0 < p < 65536:
                    raise ValueError
            except ValueError:
                continue
            h.connect_peer((host, p))
            n += 1
        return n

    # ── control ────────────────────────────────────────────────
    def stop(self, key: str) -> None:
        """Stop: disconnect every peer and tell the tracker 'stopped'."""
        h = self.handles.get(key)
        if h:
            self._unfreeze(key, h)
            self.tr_status.pop(key, None)         # start again from a clean tracker state
            self.tr_peers.pop(key, None)
            h.unset_flags(lt.torrent_flags.auto_managed)
            h.pause()
            h.save_resume_data(lt.torrent_handle.save_info_dict)

    def pause(self, key: str) -> None:
        """Pause (like µTorrent / BitComet): transfers freeze, but the torrent stays connected to
        its peers and announced, so 'Start' picks up instantly. A stopped torrent stays stopped."""
        h = self.handles.get(key)
        if not h or h.flags() & lt.torrent_flags.paused:
            return
        self.opts.setdefault(key, {})["soft_pause"] = True
        self._save_opts()
        self._freeze(h)

    @staticmethod
    def _freeze(h: lt.torrent_handle) -> None:
        h.set_download_limit(1)          # 1 B/s: libtorrent's 0 means unlimited
        h.set_upload_limit(1)

    def _unfreeze(self, key: str, h: lt.torrent_handle) -> None:
        o = self.opts.get(key, {})
        if not o.pop("soft_pause", None):
            return
        self._save_opts()
        own = o.get("own_limits")
        h.set_download_limit(int(o.get("dl_limit", 0)) * 1024 or -1 if own else -1)
        h.set_upload_limit(int(o.get("ul_limit", 0)) * 1024 or -1 if own else -1)
        self._limited.discard(key)          # a tracker rule re-applies its share on the next pass

    def is_soft_paused(self, key: str) -> bool:
        return bool(self.opts.get(key, {}).get("soft_pause"))

    def resume(self, key: str) -> bool:
        """False when the data is still missing — the torrent stays stopped (see locate / recheck)."""
        h = self.handles.get(key)
        if h and self.opts.get(key, {}).get("missing"):
            if self._is_missing(key, h):
                return False
            self.opts[key].pop("missing", None)          # the drive is back
            self.opts[key].pop("missing_pct", None)
            self._save_opts()
            self._log("torrent", f"{h.status().name}: files found again")
        if h:
            self._unfreeze(key, h)
            h.set_flags(lt.torrent_flags.auto_managed)
            h.resume()
            h.save_resume_data(lt.torrent_handle.save_info_dict)
        return True

    def recheck(self, key: str) -> None:
        """Hash check of the data on disk. A torrent with missing data is checked too — whatever is
        found counts, the rest will be downloaded again once it is started."""
        h = self.handles.get(key)
        if h:
            o = self.opts.setdefault(key, {})
            o.pop("missing", None)
            o.pop("missing_pct", None)
            o["checking"] = True
            self._save_opts()
            self._check_now(h)

    @staticmethod
    def _check_now(h) -> None:
        """libtorrent doesn't check a paused torrent until it is started (and shows 0 % meanwhile):
        a stopped torrent is started with stop_when_ready, so it checks and stops again by itself,
        without connecting to anyone."""
        paused = bool(h.flags() & lt.torrent_flags.paused)
        h.force_recheck()                 # first: a torrent resumed before it would start at once
        if paused:
            h.unset_flags(lt.torrent_flags.auto_managed)
            h.set_flags(lt.torrent_flags.stop_when_ready)
            h.resume()

    def locate(self, key: str, folder: str) -> None:
        """'Set location': the files are already in <folder> (moved by hand, drive letter changed) —
        point the torrent there without moving anything, check the data and go on."""
        h = self.handles.get(key)
        if not h or not folder:
            return
        o = self.opts.setdefault(key, {})
        if not (h.flags() & lt.torrent_flags.paused) or o.get("missing"):
            o["start_after_check"] = True
        o.pop("missing", None)
        o.pop("missing_pct", None)
        o["checking"] = True
        self._save_opts()
        h.move_storage(folder, getattr(lt.move_flags_t, "reset_save_path_unchecked", lt.move_flags_t.reset_save_path))
        self._check_now(h)

    def _is_missing(self, key: str, h) -> bool:
        s = h.status()
        if not s.has_metadata:
            return False
        try:
            prios = list(h.get_file_priorities())
        except Exception:
            prios = []
        return _data_missing(s.save_path, h.torrent_file().files(),
                             {int(k): v for k, v in self.opts.get(key, {}).get("renamed", {}).items()}, prios)

    def _scan_missing(self) -> None:
        """Every 30 s: a torrent that has data but whose files all vanished is stopped and marked,
        instead of failing with a disk error or starting over from zero."""
        for key, h in list(self.handles.items()):
            o = self.opts.get(key, {})
            if o.get("missing") or key in self._moves or not h.is_valid():
                continue
            s = h.status()
            if s.total_wanted_done <= 0 or s.state in (lt.torrent_status.checking_files,
                                                     lt.torrent_status.checking_resume_data) \
                    or getattr(s, "moving_storage", False):
                continue
            # a torrent still downloading may not have its files on disk yet — only a vanished
            # folder counts for it; a complete one counts when all its files are gone
            gone = not os.path.isdir(s.save_path) or (s.progress >= 1.0 and self._is_missing(key, h))
            if not gone:
                self._miss_hits.pop(key, None)
                continue
            self._miss_hits[key] = self._miss_hits.get(key, 0) + 1
            if self._miss_hits[key] < 2:          # seen twice in a row (≈1 min) — not a hiccup
                continue
            self._miss_hits.pop(key, None)
            self.opts.setdefault(key, {})["missing"] = s.save_path
            self._save_opts()
            h.unset_flags(lt.torrent_flags.auto_managed)
            h.pause()
            self.missing_names.append((s.name, key, s.save_path))
            self._log("error", f"{s.name}: files not found in {s.save_path}")

    def reannounce(self, key: str) -> None:
        h = self.handles.get(key)
        if h:
            h.force_reannounce()

    # ── polling ────────────────────────────────────────────────
    def poll(self) -> None:
        """Process alerts. Call from the UI timer (~1 s)."""
        for a in self.ses.pop_alerts():
            self._cur_key = ""
            if isinstance(a, lt.torrent_alert):
                try:
                    if a.handle.is_valid():
                        self._cur_key = _key(a.handle)
                except Exception:
                    pass
            if isinstance(a, lt.file_renamed_alert) and self._cur_key:
                # torrent_file() keeps the original names; remember the current ones ourselves
                try:
                    new = a.new_name() if callable(getattr(a, "new_name", None)) else str(a.new_name)
                    self.opts.setdefault(self._cur_key, {}).setdefault("renamed", {})[str(a.index)] = new
                    self._save_opts()
                except Exception:
                    pass
            if self._cur_key and isinstance(a, TASK_ONLY_ALERTS):
                self._task(self._cur_key, "tracker" if isinstance(a, lt.tracker_announce_alert)
                           else "error" if isinstance(a, lt.hash_failed_alert) else "torrent", a.message())
            if isinstance(a, lt.save_resume_data_alert):
                self._write_resume(a)
            elif isinstance(a, lt.torrent_finished_alert):
                st = a.handle.status()
                # libtorrent also posts this when an already complete torrent is loaded at start-up;
                # only a download that finished during this run gets the notification / done actions
                # really downloaded in this run — not just found complete on disk by a hash check
                # (that one gets "Recheck finished", not "Downloaded" and the when-done actions)
                if _finished_now(st, self.started) and (st.total_payload_download > 0 or st.all_time_download > 0):
                    self.finished_names.append((st.name, _key(a.handle)))
                    self.count_event("completed")
                    self._auto_export(_key(a.handle), "export_done_dir")
                    self._move_when_done(a.handle, st)
                a.handle.save_resume_data(lt.torrent_handle.save_info_dict)
            elif isinstance(a, lt.storage_moved_alert):
                self._log("torrent", a.message())
                mv = self._moves.pop(self._cur_key, None)
                if mv is not None:
                    self.moved_names.append((a.handle.status().name, self._cur_key, ""))
                    if mv.get("recheck"):
                        self.opts.setdefault(self._cur_key, {})["checking"] = True
                        self._save_opts()
                        self._check_now(a.handle)
                a.handle.save_resume_data(lt.torrent_handle.save_info_dict)
            elif isinstance(a, lt.storage_moved_failed_alert):
                self._log("error", a.message())
                if self._moves.pop(self._cur_key, None) is not None:
                    err = a.error.message() if a.error.value() else a.message()
                    self.moved_names.append((a.handle.status().name, self._cur_key, err))
            elif isinstance(a, lt.torrent_checked_alert):
                o = self.opts.get(self._cur_key, {})
                if o.pop("checking", None):          # a check the user asked for (not the start-up one)
                    st = a.handle.status()
                    self.checked_names.append((st.name, self._cur_key, st.progress))
                    if o.pop("start_after_check", None):
                        self.resume(self._cur_key)
                    self._save_opts()
            elif isinstance(a, lt.metadata_received_alert):
                a.handle.save_resume_data(lt.torrent_handle.save_info_dict)
                if self._cur_key in self.handles:          # a magnet got its metadata
                    self._export_pending.add(self._cur_key)
            elif isinstance(a, lt.tracker_error_alert) and _is_skip_notice(a):
                pass   # libtorrent skipped a local endpoint (127.0.0.1, ::1, fe80::) that can't reach the tracker
            elif isinstance(a, lt.tracker_error_alert):
                self._tracker_failed(a)
                # a rejected v2 announce while v1 works (hybrid torrent) is just a tracker note
                self._log("error" if self.tracker_error(_key(a.handle)) else "tracker", a.message())
            elif isinstance(a, lt.tracker_reply_alert):
                self.tr_status.setdefault(_key(a.handle), {})[_alert_id(a)] = ""
                self.tr_peers.setdefault(_key(a.handle), {})[_alert_id(a)] = int(getattr(a, "num_peers", -1))
                self._log("tracker", a.message())
            elif isinstance(a, (lt.torrent_error_alert, lt.listen_failed_alert,
                                lt.file_error_alert, lt.fastresume_rejected_alert)):
                self._log("error", a.message())
            elif isinstance(a, (lt.tracker_reply_alert, lt.tracker_warning_alert)):
                self._log("tracker", a.message())
            elif isinstance(a, lt.listen_succeeded_alert):
                self._log("network", a.message())
            elif isinstance(a, lt.session_stats_alert):
                self._disk_stats(a.values)
            elif isinstance(a, lt.performance_alert):
                self._perf_warning(a)
            elif isinstance(a, lt.external_ip_alert):
                self.wan_ip = str(a.external_address)
                self._log("network", a.message())
            elif isinstance(a, (lt.portmap_alert, lt.portmap_error_alert)):
                name = "UPnP" if a.map_transport == lt.portmap_transport.upnp else "NAT-PMP"
                if isinstance(a, lt.portmap_alert):
                    self.portmap[name] = f"ok:{a.external_port}"
                    self._log("network", a.message())
                else:
                    self.portmap[name] = "error:" + a.message()
                    self._log("error", a.message())
        self._apply_stop_rules()
        self._polls = getattr(self, "_polls", 0) + 1
        if self._cross_check:
            self._check_cross()
        if self._polls % 3 == 0:
            self._apply_tracker_limits()
        if self._polls % 15 == 0:
            self._count_trackers()
            self._check_space()
        if self._polls % 30 == 7:
            try:
                self._scan_missing()
            except Exception as e:
                self._log("error", f"missing-files scan: {e}")
        if self._polls % 10 == 0:
            self._count_days()
        if self._polls % 2 == 0:
            self.ses.post_session_stats()      # → session_stats_alert → self.disk
        if self._polls % 5 == 0:
            self._track_peers()
        if self._polls % 60 == 0 and self.bans:
            self.expire_bans()
        if self._polls % 10 == 0 and self.cfg.get("ban_bad_peers", True):
            try:
                self._scan_peers()
            except Exception as e:      # never let peer bookkeeping break the alert loop
                self._log("error", f"peer scan: {e}")
        if time.time() - self._stats_flushed > 300:     # survive a crash: flush totals every 5 min
            self._flush_stats()

    def _disk_stats(self, v: dict) -> None:
        """Live disk numbers for Settings → Disk & cache (rates from the counters' deltas)."""
        now = time.time()
        prev, at = getattr(self, "_disk_prev", None), getattr(self, "_disk_at", 0.0)
        cur = {k: int(v.get("disk." + k, 0)) for k in ("num_blocks_written", "num_blocks_read",
                                                       "num_blocks_hashed", "disk_write_time")}
        d = {"queued": int(v.get("disk.queued_write_bytes", 0)),
             "jobs": int(v.get("disk.queued_disk_jobs", 0)),
             "blocked": int(v.get("disk.blocked_disk_jobs", 0)),
             "files": int(v.get("disk.file_pool_size", 0)),
             "write": 0, "read": 0, "hash": 0}
        if prev and now > at:
            dt = now - at
            d["write"] = max(0, cur["num_blocks_written"] - prev["num_blocks_written"]) * 16384 / dt
            d["read"] = max(0, cur["num_blocks_read"] - prev["num_blocks_read"]) * 16384 / dt
            d["hash"] = max(0, cur["num_blocks_hashed"] - prev["num_blocks_hashed"]) * 16384 / dt
        self._disk_prev, self._disk_at = cur, now
        self.disk = d

    def _perf_warning(self, a) -> None:
        try:
            code = lt.performance_warning_t.values[int(a.warning_code)].name
        except Exception:
            code = str(a.warning_code)
        last = self._perf_seen.get(code, 0)
        if time.time() - last < 600:
            return
        self._perf_seen[code] = time.time()
        if code in _PERF_HINTS:
            from .i18n import t
            self._log("error", t("perf_" + code))
        else:
            self._log("system", a.message())

    def _tracker_failed(self, a) -> None:
        reason = ""
        try:
            reason = a.failure_reason() or ""        # text the tracker sent ("Invalid Torrent …")
        except Exception:
            pass
        if not reason:
            reason = a.error.message() if a.error.value() else a.message()
        code = int(getattr(a, "status_code", 0) or 0)
        if "bencod" in reason.lower():
            from .i18n import t
            reason = t("tr_bad_reply")
        if code >= 300:                              # libtorrent puts the HTTP status text in failure_reason
            reason = _http_reason(code, reason)
        self.tr_status.setdefault(_key(a.handle), {})[_alert_id(a)] = reason

    def tracker_error(self, key: str) -> str:
        """Error text when every tracker that answered has failed; "" while any one works.
        A hybrid torrent is announced twice (v1 and v2 info-hash) — trackers that only know the
        v1 hash reject the v2 announce, which must not count as an error while v1 works."""
        st = self.tr_status.get(key)
        if not st or any(v == "" for v in st.values()):
            return ""
        return next(iter(st.values()))

    def _log(self, kind: str, text: str) -> None:
        """kind: system | network | torrent | tracker | error.
        Goes to the global log and, for an alert about a torrent, to that torrent's task log."""
        self.messages.append((kind, text))
        del self.messages[:-2000]
        if self._cur_key:
            self._task(self._cur_key, kind, text)

    def _task(self, key: str, kind: str, text: str) -> None:
        log = self.task_logs.setdefault(key, [])
        log.append((time.strftime("%Y-%m-%d %H:%M:%S"), kind, text))
        del log[:-500]
        self.task_seq[key] = self.task_seq.get(key, 0) + 1

    def task_log(self, key: str) -> list[tuple[str, str, str]]:
        return self.task_logs.get(key, [])

    def task_log_seq(self, key: str) -> int:
        return self.task_seq.get(key, 0)

    # ── statistics ─────────────────────────────────────────────
    def _count_days(self) -> None:
        """Traffic per calendar day (local time) for the statistics window; 400 days are kept."""
        st = self.ses.status()
        d, u = st.total_payload_download, st.total_payload_upload
        dd, du = d - self._day_mark[0], u - self._day_mark[1]
        if dd > 0 or du > 0:
            day = self.days.setdefault(time.strftime("%Y-%m-%d"), [0, 0])
            day[0] += max(0, dd)
            day[1] += max(0, du)
            self._day_mark = (d, u)
            if len(self.days) > 400:
                for k in sorted(self.days)[:-400]:
                    del self.days[k]
        sv = self.stats_saved
        sv["peak_down"] = max(sv.get("peak_down", 0), st.payload_download_rate)
        sv["peak_up"] = max(sv.get("peak_up", 0), st.payload_upload_rate)

    def _session_wasted(self) -> int:
        st = self.ses.status()
        return int(getattr(st, "total_failed_bytes", 0)) + int(getattr(st, "total_redundant_bytes", 0))

    def count_event(self, what: str) -> None:
        """'added' / 'completed' — all-time counters for the statistics window."""
        self.stats_saved[what] = int(self.stats_saved.get(what, 0)) + 1

    def reset_stats(self) -> None:
        """Statistics → Reset: totals start from zero (from now on)."""
        st = self.ses.status()
        self.stats_saved = {"all_down": -st.total_payload_download, "all_up": -st.total_payload_upload,
                            "first_run": int(time.time()), "sessions": 1,
                            "uptime": -int(time.time() - self.started),
                            "added": 0, "completed": 0, "wasted": -self._session_wasted(),
                            "peak_down": 0, "peak_up": 0, "days": {}}
        self.days = self.stats_saved["days"]
        self._day_mark = (st.total_payload_download, st.total_payload_upload)
        self.tr_totals.clear()
        self._flush_stats()

    def _flush_stats(self) -> None:
        st = self.ses.status()
        self._count_trackers()
        self._count_days()
        sv = self.stats_saved
        data = dict(sv)
        data.update({"all_down": sv["all_down"] + st.total_payload_download,
                     "all_up": sv["all_up"] + st.total_payload_upload,
                     "uptime": int(sv.get("uptime", 0)) + int(time.time() - self.started),
                     "wasted": int(sv.get("wasted", 0)) + self._session_wasted(),
                     "trackers": self.tr_totals, "seen": self._tr_seen})
        tmp = self.stats_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, self.stats_path)
        self._stats_flushed = time.time()

    def stats(self) -> dict:
        st = self.ses.status()
        return {
            "uptime": int(time.time() - self.started),
            "port": self.ses.listen_port(),
            "incoming": bool(st.has_incoming_connections),
            "peers": st.num_peers,
            "dht_nodes": st.dht_nodes,
            "wan_ip": self.wan_ip,
            "portmap": dict(self.portmap),
            "down_rate": st.payload_download_rate, "up_rate": st.payload_upload_rate,
            "session_down": st.total_payload_download, "session_up": st.total_payload_upload,
            "all_down": self.stats_saved["all_down"] + st.total_payload_download,
            "all_up": self.stats_saved["all_up"] + st.total_payload_upload,
            "all_uptime": int(self.stats_saved.get("uptime", 0)) + int(time.time() - self.started),
            "sessions": int(self.stats_saved.get("sessions", 1)),
            "first_run": int(self.stats_saved.get("first_run", self.started)),
            "added": int(self.stats_saved.get("added", 0)),
            "completed": int(self.stats_saved.get("completed", 0)),
            "wasted": int(self.stats_saved.get("wasted", 0)) + self._session_wasted(),
            "session_wasted": self._session_wasted(),
            "peak_down": max(int(self.stats_saved.get("peak_down", 0)), st.payload_download_rate),
            "peak_up": max(int(self.stats_saved.get("peak_up", 0)), st.payload_upload_rate),
            "days": self.days,
        }

    def rows(self) -> list[TorrentRow]:
        self._order_sync()
        pos = {k: i + 1 for i, k in enumerate(self.order)}
        rows = self._rows()
        for r in rows:
            r.order = pos.get(r.key, len(pos) + 1)
        rows.sort(key=lambda r: r.order)
        return rows

    def _rows(self) -> list[TorrentRow]:
        out = []
        for key, h in self.handles.items():
            if not h.is_valid():
                continue
            s = h.status()
            paused = bool(s.flags & lt.torrent_flags.paused)
            auto   = bool(s.flags & lt.torrent_flags.auto_managed)
            tr_err = ""
            missing = self.opts.get(key, {}).get("missing", "")
            moving = -1
            if key in self._moves or getattr(s, "moving_storage", False):
                moving = self._move_pct(key)
            if moving >= 0:
                state = "moving"
            elif missing:
                state = "missing"
            elif s.errc.value():
                state = "error"
            elif paused and auto:
                state = "queued"
            elif paused:
                state = "stopped"
            elif self.opts.get(key, {}).get("soft_pause"):
                state = "paused"
            else:
                state = STATE_NAMES.get(s.state, "unknown")
                tr_err = self.tracker_error(key)
                if tr_err and state in ("downloading", "seeding", "finished"):
                    state = "tracker_error"

            left = s.total_wanted - s.total_wanted_done
            eta  = int(left / s.download_payload_rate) if s.download_payload_rate > 0 and left > 0 else -1
            # all_time_* is refreshed lazily; the session counters are live
            dl   = max(s.all_time_download, s.total_payload_download)
            ul   = max(s.all_time_upload, s.total_payload_upload)
            out.append(TorrentRow(
                key=key, name=s.name or key, size=s.total_wanted,
                progress=s.progress or (self.opts.get(key, {}).get("missing_pct", 0.0) if missing else 0.0),
                state=state, down_rate=s.download_payload_rate, up_rate=s.upload_payload_rate,
                seeds=s.num_seeds, peers=s.num_peers - s.num_seeds,
                uploaded=ul, downloaded=dl,
                ratio=_ratio(ul, dl, s.total_wanted_done),
                eta=eta, save_path=s.save_path,
                error=s.errc.message() if s.errc.value() else (t_prefix(tr_err) if tr_err else ""),
                tracker=s.current_tracker,
                tracker_host=self._host(key, h),
                category=self.opts.get(key, {}).get("category", ""),
                kind=self._kind(key, h, s),
                tags=tuple(self.opts.get(key, {}).get("tags", ())),
                hnr=self.hnr_left(key, h, s) if s.progress >= 1.0 else "",
                done=s.total_wanted_done, sess_down=s.total_payload_download, sess_up=s.total_payload_upload,
                queue=s.queue_position + 1 if s.queue_position >= 0 else -1,
                forced=not paused and not auto and not s.errc.value(),
                moving=moving, missing=missing,
            ))
        return out

    def _kind(self, key: str, h: lt.torrent_handle, s) -> str:
        cached = self._kinds.get(key)
        if cached:
            return cached
        if not s.has_metadata:
            return "magnet"
        from .filetypes import torrent_kind
        fs = h.torrent_file().files()
        files = [(fs.file_path(i), fs.file_size(i)) for i in range(fs.num_files())
                 if not fs.file_flags(i) & lt.file_storage.flag_pad_file]
        self._kinds[key] = kind = torrent_kind(files)
        return kind

    def _host(self, key: str, h: lt.torrent_handle) -> str:
        cached = self._hosts.get(key)
        if cached:
            return cached
        from urllib.parse import urlsplit
        trs = h.trackers()
        host = (urlsplit(trs[0]["url"]).hostname or "") if trs else ""
        if host.startswith("www."):
            host = host[4:]
        if host or h.status().has_metadata:   # magnet without metadata may get trackers later
            self._hosts[key] = host or "DHT"
        return host or "DHT"

    # ── per-torrent properties ─────────────────────────────────
    def _save_opts(self) -> None:
        tmp = self.opts_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.opts, f, indent=1)
        os.replace(tmp, self.opts_path)

    def props(self, key: str) -> dict:
        h = self.handles[key]
        o = self.opts.get(key, {})
        return {
            "dl_limit": int(o.get("dl_limit", 0)) if o.get("own_limits") else 0,   # KiB/s, 0 = unlimited
            "ul_limit": int(o.get("ul_limit", 0)) if o.get("own_limits") else 0,
            "super_seed": bool(h.flags() & lt.torrent_flags.super_seeding),
            "stop_mode": o.get("stop_mode", "global"),          # global | custom | never
            "stop_ratio": float(o.get("stop_ratio", 0.0)),       # 0 = off
            "stop_hours": int(o.get("stop_hours", 0)),           # 0 = off
            "max_conn": int(o.get("max_conn", 0)),               # 0 = the default from Settings
            "max_up": int(o.get("max_up", 0)),
        }

    def set_props(self, key: str, p: dict) -> None:
        h = self.handles.get(key)
        if not h:
            return
        h.set_download_limit(int(p["dl_limit"]) * 1024 or -1)
        h.set_upload_limit(int(p["ul_limit"]) * 1024 or -1)
        if p["super_seed"]:
            h.set_flags(lt.torrent_flags.super_seeding)
        else:
            h.unset_flags(lt.torrent_flags.super_seeding)
        # new rule → may stop again even if it already fired once; category / tags are kept
        own = bool(int(p["dl_limit"]) or int(p["ul_limit"]))
        self._limited.discard(key)
        self.opts.setdefault(key, {}).update(
            stop_mode=p["stop_mode"], stop_ratio=float(p["stop_ratio"]), stop_hours=int(p["stop_hours"]),
            rule_done=False, own_limits=own, dl_limit=int(p["dl_limit"]), ul_limit=int(p["ul_limit"]),
            max_conn=int(p.get("max_conn", 0)), max_up=int(p.get("max_up", 0)))
        self._save_opts()
        self._apply_conn_limits(key, h)
        h.save_resume_data(lt.torrent_handle.save_info_dict)

    # ── task properties (BitComet's Advanced / Publisher / Download order tabs) ──
    def task_props(self, key: str) -> dict:
        h = self.handles[key]
        o = self.opts.get(key, {})
        fl = h.flags()
        st = h.status()
        d = {"trackers": self.trackers_raw(key),
             "dht": not fl & lt.torrent_flags.disable_dht, "pex": not fl & lt.torrent_flags.disable_pex,
             "lsd": not fl & lt.torrent_flags.disable_lsd,
             "super_seed": bool(fl & lt.torrent_flags.super_seeding),
             "own_limits": bool(o.get("own_limits")),
             "dl_limit": int(o.get("dl_limit", 0)), "ul_limit": int(o.get("ul_limit", 0)),
             "web_seeds": sorted(h.url_seeds()),
             "publisher": o.get("publisher", ""), "publisher_url": o.get("publisher_url", ""),
             "private": False, "creator": "", "created": 0, "comment": "",
             "files": [], "order": o.get("order")}
        if st.has_metadata:
            ti = h.torrent_file()
            d.update(private=bool(ti.priv()), creator=ti.creator(), created=ti.creation_date(), comment=ti.comment())
            fs = ti.files()
            prog = h.file_progress()
            prios = h.get_file_priorities()
            d["files"] = [(i, self.file_path(key, fs, i), fs.file_size(i),
                           prog[i] / fs.file_size(i) if fs.file_size(i) else 1.0, prios[i])
                          for i in range(fs.num_files()) if not fs.file_flags(i) & lt.file_storage.flag_pad_file]
        return d

    def set_task_props(self, key: str, p: dict) -> None:
        h = self.handles.get(key)
        if not h:
            return
        o = self.opts.setdefault(key, {})
        if "trackers" in p and p["trackers"] != self.trackers_raw(key):
            self.set_trackers(key, p["trackers"])
        for name, flag in (("dht", lt.torrent_flags.disable_dht), ("pex", lt.torrent_flags.disable_pex),
                           ("lsd", lt.torrent_flags.disable_lsd)):
            if name in p:
                (h.unset_flags if p[name] else h.set_flags)(flag)
        if "super_seed" in p:
            (h.set_flags if p["super_seed"] else h.unset_flags)(lt.torrent_flags.super_seeding)
        if "own_limits" in p:
            own = bool(p["own_limits"])
            dl, ul = (int(p.get("dl_limit", 0)), int(p.get("ul_limit", 0))) if own else (0, 0)
            h.set_download_limit(dl * 1024 or -1)
            h.set_upload_limit(ul * 1024 or -1)
            self._limited.discard(key)
            o.update(own_limits=own and bool(dl or ul), dl_limit=dl, ul_limit=ul)
        if "web_seeds" in p:
            have, want = set(h.url_seeds()), {u.strip() for u in p["web_seeds"] if u.strip()}
            for u in have - want:
                h.remove_url_seed(u)
            for u in want - have:
                h.add_url_seed(u)
        for k in ("publisher", "publisher_url"):
            if p.get(k):
                o[k] = p[k]
        if "order" in p:
            prios = list(h.get_file_priorities())
            if p["order"]:
                h.prioritize_files(order_priorities(p["order"], prios))
                o["order"] = list(p["order"])
            elif o.pop("order", None) is not None:          # custom order switched off: back to normal
                h.prioritize_files([0 if x == 0 else 4 for x in prios])
        self._save_opts()
        h.save_resume_data(lt.torrent_handle.save_info_dict)

    def _stop_limits(self, key: str) -> tuple[float, int]:
        o = self.opts.get(key, {})
        mode = o.get("stop_mode", "global")
        if mode == "never":
            return 0.0, 0
        if mode == "custom":
            return float(o.get("stop_ratio", 0)), int(o.get("stop_hours", 0))
        return float(self.cfg.get("stop_ratio", 0)), int(self.cfg.get("stop_hours", 0))

    def _apply_stop_rules(self) -> None:
        """Pause seeding torrents that reached their ratio / seeding-time limit.
        Fires once per torrent: after a manual Start it keeps seeding."""
        for key, h in self.handles.items():
            if not h.is_valid() or self.opts.get(key, {}).get("rule_done"):
                continue
            ratio_lim, hours_lim = self._stop_limits(key)
            if not ratio_lim and not hours_lim:
                continue
            s = h.status()
            if s.state not in (lt.torrent_status.seeding, lt.torrent_status.finished) \
                    or s.flags & lt.torrent_flags.paused:
                continue
            ratio = _ratio(s.all_time_upload, s.all_time_download, s.total_wanted_done)
            hours = s.seeding_duration.total_seconds() / 3600
            hit = (ratio_lim and ratio >= ratio_lim) or (hours_lim and hours >= hours_lim)
            if hit and not self.hnr_left(key, h, s):    # never stop before the tracker's requirement
                self.stop(key)
                self.opts.setdefault(key, {})["rule_done"] = True
                self._save_opts()
                self.stopped_names.append((s.name, key))

    def pieces(self, key: str) -> list[bool]:
        h = self.handles.get(key)
        if not h:
            return []
        return list(h.status(lt.torrent_handle.query_pieces).pieces)

    def set_global_limits(self, dl_kib: int, ul_kib: int) -> None:
        """Used by the speed schedule; 0 = unlimited."""
        self.ses.apply_settings({"download_rate_limit": int(dl_kib) * 1024,
                                 "upload_rate_limit": int(ul_kib) * 1024})

    # ── categories / moving / trackers ─────────────────────────
    def set_category(self, key: str, name: str) -> None:
        if key not in self.handles:
            return
        o = self.opts.setdefault(key, {})
        if name:
            o["category"] = name
        else:
            o.pop("category", None)
        self._save_opts()

    def set_tags(self, key: str, tags: list[str]) -> None:
        if key not in self.handles:
            return
        o = self.opts.setdefault(key, {})
        tags = sorted({x.strip() for x in tags if x.strip()}, key=str.lower)
        if tags:
            o["tags"] = tags
        else:
            o.pop("tags", None)
        self._save_opts()

    def all_tags(self) -> list[str]:
        found = {x for k, o in self.opts.items() if k in self.handles for x in o.get("tags", ())}
        return sorted(found | set(self.cfg.get("tags", [])), key=str.lower)

    def category_path(self, name: str) -> str:
        for c in self.cfg.get("categories", []):
            if c.get("name") == name:
                return c.get("path", "")
        return ""

    def move(self, key: str, path: str, keep_existing: bool = False) -> bool:
        """Move downloaded files to another folder (no re-download). keep_existing: files that are
        already in the new folder are kept (and used) instead of being overwritten."""
        h = self.handles.get(key)
        if not h or not path or key in self._moves:
            return False
        s = h.status()
        if os.path.normcase(os.path.abspath(path)) == os.path.normcase(os.path.abspath(s.save_path)):
            return False
        os.makedirs(path, exist_ok=True)
        files = []
        if s.has_metadata:
            fs = h.torrent_file().files()
            files = [(self.file_path(key, fs, i), fs.file_size(i)) for i in range(fs.num_files())
                     if not fs.file_flags(i) & lt.file_storage.flag_pad_file]
        total = sum(sz for p, sz in files if os.path.exists(os.path.join(s.save_path, p)))
        self._moves[key] = {"dest": path, "files": files, "total": total, "pct": 0, "at": 0.0,
                            "recheck": keep_existing}     # kept files may differ — check them afterwards
        h.move_storage(path, lt.move_flags_t.dont_replace if keep_existing else lt.move_flags_t.always_replace_files)
        return True

    def _move_pct(self, key: str) -> int:
        """How much of the data is at the new place already (bytes there / bytes to move); refreshed
        every 2 s. Inside one drive the move is a rename and finishes at once."""
        m = self._moves.get(key)
        if m is None:
            return 0
        if time.time() - m["at"] >= 2 and m["total"] > 0:
            m["at"] = time.time()
            got = 0
            for p, sz in m["files"]:
                try:
                    got += min(sz, os.path.getsize(os.path.join(m["dest"], p)))
                except OSError:
                    pass
            m["pct"] = max(m["pct"], min(99, int(got * 100 / m["total"])))
        return m["pct"]

    def moving(self) -> list[tuple[str, str, int]]:
        """(key, destination, percent) of the moves in progress."""
        return [(k, m["dest"], m["pct"]) for k, m in self._moves.items()]

    def _move_when_done(self, h: lt.torrent_handle, st) -> None:
        """Settings → "Move completed downloads to": only torrents that were really downloaded
        (not ones seeded from your own disk), into <folder>/<category> when categorised."""
        target = self.cfg.get("move_done_dir", "")
        if not target or st.total_payload_download <= 0 and st.all_time_download <= 0:
            return
        cat = self.opts.get(_key(h), {}).get("category", "")
        if cat and self.cfg.get("move_done_by_category", True):
            target = os.path.join(target, cat)
        self.move(_key(h), target)

    # ── .torrent export ────────────────────────────────────────
    def _piece_layers(self, key: str, atp=None) -> bytes | None:
        """v2 / hybrid torrents need "piece layers" next to the info dict; libtorrent drops them from
        the loaded torrent, but they can be rebuilt from the merkle trees in the resume data
        (or from the add params right when the torrent is added)."""
        for src in ("atp", "resume"):
            try:
                if src == "atp":
                    if atp is None:
                        continue
                    p = atp
                else:
                    with open(self._resume_path(key), "rb") as f:
                        p = lt.read_resume_data(f.read())
                ent = lt.write_torrent_file(p)
                layers = ent.get(b"piece layers") if isinstance(ent, dict) else None
                if layers:
                    return lt.bencode(layers)
            except Exception:
                pass
        return None

    def torrent_bytes(self, key: str, atp=None) -> bytes | None:
        """The .torrent of a torrent in the client: the original info dict byte for byte (same
        info-hash), the current trackers (edited ones, with passkey), comment, creator, date,
        web seeds, publisher. None while a magnet has no metadata yet."""
        h = self.handles.get(key)
        if not h or not h.is_valid() or not h.status().has_metadata:
            return None
        ti = h.torrent_file()
        root: dict[bytes, bytes] = {b"info": bytes(ti.info_section())}     # raw, not re-encoded
        if ti.info_hashes().has_v2():
            layers = self._piece_layers(key, atp)
            if layers:
                root[b"piece layers"] = layers
            else:
                self._log("error", f"{ti.name()}: v2 piece layers not available yet, the .torrent is incomplete")
        tiers: dict[int, list[str]] = {}
        for tr in h.trackers():
            tiers.setdefault(int(tr.get("tier", 0)), []).append(tr["url"])
        urls = [u for _, us in sorted(tiers.items()) for u in us]
        if urls:
            root[b"announce"] = lt.bencode(urls[0].encode())
            if len(urls) > 1:
                root[b"announce-list"] = lt.bencode([[u.encode() for u in us] for _, us in sorted(tiers.items())])
        if ti.comment():
            root[b"comment"] = lt.bencode(ti.comment().encode())
        if ti.creator():
            root[b"created by"] = lt.bencode(ti.creator().encode())
        if ti.creation_date():
            root[b"creation date"] = lt.bencode(int(ti.creation_date()))
        seeds = [w["url"] for w in ti.web_seeds() if w.get("type", 0) == 0]
        if seeds:
            root[b"url-list"] = lt.bencode([u.encode() for u in seeds])
        o = self.opts.get(key, {})
        for k, ok in ((b"publisher", "publisher"), (b"publisher-url", "publisher_url")):
            if o.get(ok):
                root[k] = lt.bencode(str(o[ok]).encode())
        # bencoded dictionary: keys sorted as raw bytes, values already encoded
        return b"d" + b"".join(lt.bencode(k) + v for k, v in sorted(root.items())) + b"e"

    def export_torrent(self, key: str, folder: str, name: str | None = None, atp=None) -> str:
        """Write <folder>/<name>.torrent (a free name: "x (2).torrent" if it exists). Returns the path."""
        data = self.torrent_bytes(key, atp)
        if data is None:
            raise ValueError("no metadata yet")
        base = _safe_name(name or self.handles[key].status().name or key)
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, base + ".torrent")
        n = 2
        while os.path.exists(path):
            with open(path, "rb") as f:
                if f.read() == data:          # this very file is already there
                    return path
            path = os.path.join(folder, f"{base} ({n}).torrent")
            n += 1
        with open(path, "wb") as f:
            f.write(data)
        return path

    def _auto_export(self, key: str, cfg_key: str, atp=None) -> None:
        """Settings → Folders: copy the .torrent of every added / every finished torrent."""
        folder = self.cfg.get(cfg_key, "")
        if not folder:
            return
        try:
            self.export_torrent(key, folder, atp=atp)
        except Exception as e:
            self._log("error", f".torrent → {folder}: {e}")

    def trackers_raw(self, key: str) -> list[tuple[str, int]]:
        """Full tracker URLs (with passkey) for the editor."""
        h = self.handles.get(key)
        return [(t["url"], t["tier"]) for t in h.trackers()] if h else []

    def set_trackers(self, key: str, trackers: list[tuple[str, int]]) -> None:
        self.tr_status.pop(key, None)
        h = self.handles.get(key)
        if not h:
            return
        entries = []
        for u, tier in trackers:
            e = lt.announce_entry(u)
            e.tier = tier
            entries.append(e)
        h.replace_trackers(entries)
        self._hosts.pop(key, None)
        h.force_reannounce()
        h.save_resume_data(lt.torrent_handle.save_info_dict)

    def replace_in_trackers(self, keys: list[str], old: str, new: str) -> int:
        """Find & replace in tracker URLs (e.g. a changed passkey). Returns torrents changed."""
        changed = 0
        if not old:
            return 0
        for key in keys:
            trs = self.trackers_raw(key)
            new_trs = [(u.replace(old, new), tier) for u, tier in trs]
            if new_trs != trs:
                self.set_trackers(key, new_trs)
                changed += 1
        return changed

    def scan_watch_dir(self) -> list[str]:
        """Add .torrent files that appeared in the watch folder; renames them to .torrent.added."""
        folder = self.cfg.get("watch_dir", "")
        if not folder or not os.path.isdir(folder):
            return []
        added = []
        for fn in sorted(os.listdir(folder)):
            if not fn.lower().endswith(".torrent"):
                continue
            path = os.path.join(folder, fn)
            try:
                if time.time() - os.path.getmtime(path) < 2:     # still being written by the browser
                    continue
                cat = self.cfg.get("watch_category", "")
                match = self.find_cross(lt.torrent_info(path)) if self.cfg.get("cross_seed", True) else None
                if match:
                    key = self.add_cross(path, match)
                else:
                    key = self.add_torrent_file(path, save_path=self.category_path(cat) or None)
                if cat:
                    self.set_category(key, cat)
                added.append(fn)
                self._log("torrent", f"watch folder: {fn}")
            except Exception as e:
                self._log("error", f"watch folder: {fn}: {e}")
            try:
                os.replace(path, path + ".added")
            except OSError:
                pass
        return added

    # ── files ──────────────────────────────────────────────────
    def files(self, key: str) -> list[dict]:
        """Files of a torrent (pad files of v2/hybrid torrents skipped)."""
        h = self.handles.get(key)
        if not h or not h.status().has_metadata:
            return []
        fs = h.torrent_file().files()
        done = h.file_progress(lt.torrent_handle.piece_granularity)
        prios = h.get_file_priorities()
        out = []
        for i in range(fs.num_files()):
            if fs.file_flags(i) & lt.file_storage.flag_pad_file:
                continue
            size = fs.file_size(i)
            out.append({
                "index": i,
                "path": self.file_path(key, fs, i),
                "size": size,
                "progress": (done[i] / size) if size else 1.0,
                "priority": prios[i] if i < len(prios) else PRIO_NORMAL,
            })
        return out

    def search_files(self, match, limit: int = 2000) -> tuple[list[dict], bool]:
        """Files of every torrent whose path satisfies ``match(path_lower)``.

        Paths are cheap (metadata only); progress is asked from libtorrent only for torrents
        that actually have a hit. Returns (hits, truncated)."""
        out: list[dict] = []
        for key, h in list(self.handles.items()):
            try:
                st = h.status()
                if not st.has_metadata:
                    continue
                fs = h.torrent_file().files()
                hits = []
                for i in range(fs.num_files()):
                    if fs.file_flags(i) & lt.file_storage.flag_pad_file:
                        continue
                    path = self.file_path(key, fs, i)
                    if match(path.lower()):
                        hits.append((i, path, fs.file_size(i)))
                if not hits:
                    continue
                done = h.file_progress(lt.torrent_handle.piece_granularity)
                for i, path, size in hits:
                    out.append({"key": key, "torrent": st.name, "index": i, "path": path, "size": size,
                                "progress": (done[i] / size) if size else 1.0,
                                "abs": os.path.join(st.save_path, path)})
                    if len(out) >= limit:
                        return out, True
            except Exception:
                continue
        return out, False

    def file_path(self, key: str, fs, i: int) -> str:
        """Current path of a file — renamed ones included (torrent_file() has the original names)."""
        return self.opts.get(key, {}).get("renamed", {}).get(str(i)) or fs.file_path(i)

    def abs_file_path(self, key: str, i: int) -> str:
        h = self.handles.get(key)
        if not h or not h.status().has_metadata:
            return ""
        return os.path.join(h.status().save_path, self.file_path(key, h.torrent_file().files(), i))

    def current_info(self, key: str):
        """A copy of the torrent's metadata with the current (renamed) file paths."""
        h = self.handles.get(key)
        if not h or not h.status().has_metadata:
            return None
        ti = lt.torrent_info(h.torrent_file())
        for idx, path in self.opts.get(key, {}).get("renamed", {}).items():
            try:
                ti.rename_file(int(idx), path)
            except Exception:
                pass
        return ti

    def apply_files(self, key: str, f: dict) -> None:
        """Task properties, Basic tab: wanted files, renamed files, new folder."""
        h = self.handles.get(key)
        if not h:
            return
        wanted = f.get("wanted")
        if wanted is not None:
            prios = list(h.get_file_priorities())
            fs = h.torrent_file().files()
            new = []
            for i, pr in enumerate(prios):
                if fs.file_flags(i) & lt.file_storage.flag_pad_file:
                    new.append(pr)
                elif i in wanted:
                    new.append(pr if pr else 4)
                else:
                    new.append(0)
            if new != prios:
                h.prioritize_files(new)
        for idx, path in (f.get("renames") or {}).items():
            h.rename_file(idx, path)
        if f.get("save_path"):
            self.move(key, f["save_path"])
        h.save_resume_data(lt.torrent_handle.save_info_dict)

    def set_file_priority(self, key: str, indices: list[int], prio: int) -> None:
        h = self.handles.get(key)
        if not h:
            return
        prios = list(h.get_file_priorities())
        for i in indices:
            if 0 <= i < len(prios):
                prios[i] = prio
        h.prioritize_files(prios)
        h.save_resume_data(lt.torrent_handle.save_info_dict)

    def peers(self, key: str) -> list[dict]:
        h = self.handles.get(key)
        if not h:
            return []
        res, now, seen = [], time.time(), set()
        for p in h.get_peer_info():
            addr = f"{p.ip[0]}:{p.ip[1]}"
            sk = (key, addr)
            seen.add(sk)
            first = self._peer_seen.setdefault(sk, now)
            src = p.source
            source = ("tracker" if src & lt.peer_info.tracker else "dht" if src & lt.peer_info.dht
                      else "pex" if src & lt.peer_info.pex else "lsd" if src & lt.peer_info.lsd
                      else "resume" if src & lt.peer_info.resume_data else "incoming")
            fl = p.flags
            res.append({
                "ip": addr,
                "host": p.ip[0],
                "client": p.client.decode("utf-8", "replace") if isinstance(p.client, bytes) else str(p.client),
                "progress": p.progress,
                "down": p.payload_down_speed,
                "up": p.payload_up_speed,
                "downloaded": p.total_download,
                "uploaded": p.total_upload,
                "peer_rate": int(getattr(p, "remote_dl_rate", 0) or 0),   # how fast the peer itself downloads
                "source": source,
                "local": bool(fl & _OUTGOING),                     # we connected to them
                "utp": bool(fl & _UTP),
                "encrypted": bool(fl & (int(lt.peer_info.rc4_encrypted) | int(lt.peer_info.plaintext_encrypted))),
                "seed": bool(fl & int(lt.peer_info.seed)),
                "connected": int(now - first),
                "pieces": _buckets(p.pieces, 60),
            })
        for sk in [k for k in self._peer_seen if k[0] == key and k not in seen]:
            del self._peer_seen[sk]
        return res

    def peer_detail(self, key: str, addr: str) -> dict | None:
        """Everything libtorrent knows about one connection ("ip:port"), for the peer window.
        None once the peer has disconnected."""
        h = self.handles.get(key)
        if not h:
            return None
        for p in h.get_peer_info():
            if f"{p.ip[0]}:{p.ip[1]}" != addr:
                continue
            fl = int(p.flags)

            def has(name):
                v = getattr(lt.peer_info, name, None)
                return bool(v is not None and fl & int(v))

            def secs(v):
                try:
                    return float(v.total_seconds()) if hasattr(v, "total_seconds") else float(v)
                except Exception:
                    return -1.0
            src = int(p.source)
            sources = [n for n, f in (("tracker", lt.peer_info.tracker), ("dht", lt.peer_info.dht),
                                      ("pex", lt.peer_info.pex), ("lsd", lt.peer_info.lsd),
                                      ("resume", lt.peer_info.resume_data)) if src & int(f)] \
                or (["manual"] if fl & _OUTGOING else ["incoming"])     # we dialled it without a source
            try:
                le = p.local_endpoint() if callable(p.local_endpoint) else p.local_endpoint
                local = f"{le[0]}:{le[1]}"
            except Exception:
                local = ""
            pid = p.pid.to_bytes()
            ctype = int(getattr(p, "connection_type", 0) or 0)
            first = self._peer_seen.setdefault((key, addr), time.time())
            return {
                "addr": addr, "host": p.ip[0], "port": p.ip[1], "local_endpoint": local,
                "client": p.client.decode("utf-8", "replace") if isinstance(p.client, bytes) else str(p.client),
                "peer_id": pid[:8].decode("latin-1", "replace") if pid[:1] == b"-" else pid[:8].hex(),
                "sources": sources,
                "outgoing": bool(fl & _OUTGOING), "utp": bool(fl & _UTP), "i2p": has("i2p_socket"),
                "ssl": has("ssl_socket"), "holepunched": has("holepunched"),
                "enc": "rc4" if has("rc4_encrypted") else "header" if has("plaintext_encrypted") else "",
                # connection_type: standard_bittorrent=1, web_seed=2, http_seed=4
                "kind": "web" if ctype == int(lt.peer_info.web_seed) else
                        "http" if ctype == int(lt.peer_info.http_seed) else "bt",
                "connected": int(time.time() - first),
                "progress": float(p.progress), "num_pieces": int(p.num_pieces),
                "seed": has("seed"), "upload_only": has("upload_only"),
                "down": int(p.payload_down_speed), "up": int(p.payload_up_speed),
                "down_peak": int(p.download_rate_peak), "up_peak": int(p.upload_rate_peak),
                "downloaded": int(p.total_download), "uploaded": int(p.total_upload),
                "peer_rate": int(getattr(p, "remote_dl_rate", 0) or 0),
                "interesting": has("interesting"), "choked": has("choked"),
                "remote_interested": has("remote_interested"), "remote_choked": has("remote_choked"),
                "snubbed": has("snubbed"), "optimistic": has("optimistic_unchoke"),
                "on_parole": has("on_parole"), "endgame": has("endgame_mode"),
                "extensions": has("supports_extensions"),
                "dl_queue": int(p.download_queue_length), "ul_queue": int(p.upload_queue_length),
                "hashfails": int(p.num_hashfails), "failcount": int(p.failcount),
                "rtt": int(getattr(p, "rtt", 0) or 0),
                "last_active": secs(p.last_active), "last_request": secs(p.last_request),
            }
        return None

    def piece_map(self, key: str) -> dict:
        """Piece states for the Piece map tab and the Summary bars.
        state per piece: 0 empty · 1 downloading · 2 done · 3 skipped (files not wanted)."""
        h = self.handles.get(key)
        if not h:
            return {}
        s = h.status(lt.torrent_handle.query_pieces)
        have = list(s.pieces)
        if not have:
            return {}
        states = [2 if x else 0 for x in have]
        try:
            for i, pr in enumerate(h.get_piece_priorities()):
                if pr == 0 and not have[i]:
                    states[i] = 3
        except Exception:
            pass
        try:
            for q in h.get_download_queue():
                i = q["piece_index"] if isinstance(q, dict) else q.piece_index
                if 0 <= i < len(states) and states[i] != 2:
                    states[i] = 1
        except Exception:
            pass
        try:
            avail = list(h.piece_availability())
        except Exception:
            avail = []
        if len(avail) != len(states):
            avail = [0] * len(states)
        # a piece we have counts as one copy too (BitComet shows our own as available)
        avail = [a + (1 if have[i] else 0) for i, a in enumerate(avail)]
        ti = h.torrent_file()
        return {"states": states, "avail": avail, "piece_len": ti.piece_length() if ti else 0,
                "have": sum(have), "total": len(have)}

    def limits(self, key: str) -> tuple[int, int]:
        """Per-torrent (download, upload) limit in bytes/s, 0 = unlimited."""
        h = self.handles.get(key)
        if not h:
            return 0, 0
        return max(0, h.download_limit()), max(0, h.upload_limit())

    def info(self, key: str) -> dict:
        """Static metadata + timing for the General tab (BitComet-style summary)."""
        h = self.handles.get(key)
        if not h:
            return {}
        s = h.status()
        ih = h.info_hashes()
        d = {
            "v1": str(ih.v1) if ih.has_v1() else "",
            "v2": str(ih.v2) if ih.has_v2() else "",
            "added": s.added_time, "completed": s.completed_time,
            "active": int(s.active_duration.total_seconds()),
            "seeding": int(s.seeding_duration.total_seconds()),
            "pieces": 0, "piece_len": 0, "files": 0,
            "creator": "", "created": 0, "comment": "", "private": None,
        }
        if s.has_metadata:
            ti = h.torrent_file()
            fs = ti.files()
            d.update(pieces=ti.num_pieces(), piece_len=ti.piece_length(),
                     files=sum(1 for i in range(fs.num_files())
                               if not fs.file_flags(i) & lt.file_storage.flag_pad_file),
                     creator=ti.creator(), created=ti.creation_date(),
                     comment=ti.comment(), private=ti.priv())
        return d

    def trackers(self, key: str) -> list[dict]:
        """Trackers of a torrent for the Trackers tab (qBittorrent-style).
        Each tracker row sums up its announces; "children" are the single announces:
        one per local endpoint (network interface) x protocol (v1 / v2 info-hash).
        status: working | updating | not_working | tracker_error | not_contacted"""
        h = self.handles.get(key)
        if not h:
            return []
        ihs = h.info_hashes()
        has = (ihs.has_v1(), ihs.has_v2())
        now = time.time()
        if h.flags() & lt.torrent_flags.paused:       # stopped (qBittorrent: "Not contacted yet")
            return sorted(({"url": t["url"], "tier": t["tier"], "proto": "", "status": "not_contacted",
                            "message": "", "peers": -1, "seeds": -1, "leeches": -1, "downloaded": -1,
                            "next": 0, "min": 0, "children": []} for t in h.trackers()),
                          key=lambda r: r["tier"])
        errors = self.tr_status.get(key, {})
        peers = self.tr_peers.get(key, {})
        out = []
        for t in h.trackers():
            url = t["url"]
            children = []
            for ep in t.get("endpoints", []):
                addr = ep.get("local_address") or ("", 0)
                for i, ih in enumerate(ep.get("info_hashes", [])[:2]):
                    if not has[i]:
                        continue                      # a v1-only torrent has no v2 announce
                    ver = "V1" if i == 0 else "V2"
                    aid = (url, ver, str(tuple(addr)))
                    reason = errors.get(aid, "")
                    if ih.get("updating"):
                        st = "updating"
                    elif ih.get("fails"):
                        # the tracker answered with a refusal → tracker error; no answer (network) → not working
                        cat = (ih.get("last_error") or {}).get("category", "")
                        st = "tracker_error" if ih.get("message") or cat == "libtorrent" else "not_working"
                    elif ih.get("start_sent"):
                        st = "working"
                    else:
                        st = "not_contacted"
                    http = st == "tracker_error" and reason.startswith("HTTP ")
                    if http:
                        st = "not_working"            # the web server answered, the tracker script didn't
                    msg = reason if http else ih.get("message") or (reason if st in ("not_working", "tracker_error") else "")
                    if "bencod" in msg.lower():
                        from .i18n import t as _t
                        msg = _t("tr_bad_reply")
                    children.append({
                        "proto": "v1" if i == 0 else "v2",
                        "endpoint": f"{addr[0]}:{addr[1]}" if addr[0] else "",
                        "status": st, "message": msg,
                        "peers": peers.get(aid, -1) if st == "working" else -1,
                        "seeds": ih.get("scrape_complete", -1),
                        "leeches": ih.get("scrape_incomplete", -1),
                        "downloaded": ih.get("scrape_downloaded", -1),
                        "next": _left(ih.get("next_announce", 0), now),
                        "min": _left(ih.get("min_announce", 0), now),
                    })
            out.append(_sum_tracker(url, t["tier"], children))
        out.sort(key=lambda r: r["tier"])          # stable: keeps the order inside a tier
        return out

    def peer_sources(self, key: str) -> dict:
        """DHT / PeX / LSD rows of the Trackers tab: state and connected peers found through each."""
        h = self.handles.get(key)
        if not h:
            return {}
        try:
            ti = h.torrent_file()
            private = bool(ti and ti.priv())
        except Exception:
            private = False
        counts = {n: [0, 0, 0] for n in ("dht", "pex", "lsd")}     # peers, seeds, leeches
        flags = {"dht": lt.peer_info.dht, "pex": lt.peer_info.pex, "lsd": lt.peer_info.lsd}
        try:
            for p in h.get_peer_info():
                for n, f in flags.items():
                    if p.source & f:
                        c = counts[n]
                        c[0] += 1
                        c[1 if p.flags & lt.peer_info.seed else 2] += 1
        except Exception:
            pass
        enabled = {"dht": bool(self.cfg.get("dht", True)), "pex": True, "lsd": bool(self.cfg.get("lsd", True))}
        out = {}
        for n, (np, ns, nl) in counts.items():
            status = "private" if private else ("working" if enabled[n] else "disabled")
            out[n] = {"status": status, "peers": np, "seeds": ns, "leeches": nl}
        return out

    def totals(self) -> tuple[int, int]:
        st = self.ses.status()
        return st.payload_download_rate, st.payload_upload_rate

    def shutdown(self) -> None:
        self.ses.pause()                 # every torrent sends "stopped" to its trackers
        self.save_all()
        try:
            self._flush_stats()
        except OSError:
            pass
        self._wait_stopped_announces()

    def _wait_stopped_announces(self, wait: float = 3.0) -> None:
        """Give the trackers a moment to get our 'stopped' (a private tracker would otherwise list
        us as a seeder until its timeout) — until each torrent with a tracker got an answer."""
        waiting = set()
        for k, h in self.handles.items():
            try:
                if h.is_valid() and h.trackers():
                    waiting.add(k)
            except Exception:
                pass
        deadline = time.time() + wait
        while waiting and time.time() < deadline:
            self.ses.wait_for_alert(200)
            for a in self.ses.pop_alerts():
                if isinstance(a, (lt.tracker_reply_alert, lt.tracker_error_alert)):
                    try:
                        waiting.discard(_key(a.handle))
                    except Exception:
                        pass

    def close(self, timeout: float = 5.0) -> None:
        """Destroy the libtorrent session (it flushes the disk and closes sockets) without letting
        it hang the exit: done in a thread, and given up on after `timeout` seconds."""
        import threading
        ses, self.ses = self.ses, None
        self.handles = {}

        def destroy(s=[ses]):
            s.pop()                      # the last reference: the session destructor runs here
        t = threading.Thread(target=destroy, daemon=True)
        t.start()
        t.join(timeout)


# Leech-only clients (take, never give back) — matched by peer-id prefix or client name
BAD_PEER_IDS = (b"-XL", b"-SD", b"-XF", b"-QD", b"-BN", b"-DL", b"-HP", b"-TT")
BAD_NAMES = ("xunlei", "thunder", "qqdownload", "baidu", "dandelion", "offline download", "torrentstorm")
FAKE_MIN_BYTES = 64 << 20      # judge fake progress only after sending a peer this much
FAKE_BAN_HOURS = 24            # a fake-progress ban is lifted after this (dynamic IPs get reused)


def _ban_record(v) -> dict:
    """bans.json entry → record. Before 1.0.39 the value was just the reason text."""
    if isinstance(v, dict):
        return {"reason": str(v.get("reason", "")), "kind": v.get("kind", "manual"),
                "client": str(v.get("client", "")), "time": int(v.get("time", 0) or 0),
                "expires": int(v.get("expires", 0) or 0)}
    text = str(v)
    kind = "fake" if text.startswith("fake progress") else "leech" if text.startswith("leech") else "manual"
    client = ""
    if "(" in text and text.endswith(")"):            # "leech client (Xunlei 0.0.1.2)"
        text, client = text[:text.rindex("(")].strip(), text[text.rindex("(") + 1:-1]
    # old fake-progress bans had no date: give them a fresh day instead of keeping them forever
    exp = int(time.time()) + FAKE_BAN_HOURS * 3600 if kind == "fake" else 0
    return {"reason": text, "kind": kind, "client": client, "time": 0, "expires": exp}


def _safe_name(name: str) -> str:
    """A Windows-safe file name from a torrent name."""
    bad = '<>:"/\\|?*'
    out = "".join("_" if ch in bad or ord(ch) < 32 else ch for ch in name).strip().rstrip(". ")
    return (out or "torrent")[:150]


def _data_missing(save_path: str, fs, renamed: dict, prios: list) -> bool:
    """True when not one wanted file of the torrent is on disk (folder deleted / renamed, drive
    unplugged). A single missing file is not this — libtorrent reports that as a file error."""
    if not save_path or not os.path.isdir(save_path):
        return True
    checked = 0
    for i in range(fs.num_files()):
        if fs.file_flags(i) & lt.file_storage.flag_pad_file or (i < len(prios) and prios[i] == 0):
            continue
        checked += 1
        if os.path.exists(os.path.join(save_path, renamed.get(i) or fs.file_path(i))):
            return False
    return checked > 0


def _bad_client(client: str, pid: bytes) -> str:
    low = client.lower()
    for n in BAD_NAMES:
        if n in low:
            return "leech client"
    if pid.startswith(BAD_PEER_IDS):
        return "leech client"
    return ""


def _match_files(new: list[tuple[int, str, int]], old: list[tuple[str, int]]) -> dict | None:
    """Map every file of the new torrent to an existing file of the same size.
    Same relative path wins, then same file name, then the only file of that size.
    Returns {new index: existing path} for files whose path differs, None if anything is missing."""
    import posixpath
    def norm(p):
        return p.replace("\\", "/")
    by_size: dict[int, list[str]] = {}
    for path, size in old:
        by_size.setdefault(size, []).append(norm(path))
    used: set[str] = set()
    renamed: dict[int, str] = {}
    for idx, path, size in new:
        cands = [c for c in by_size.get(size, []) if c not in used]
        if not cands:
            return None
        path = norm(path)
        pick = next((c for c in cands if c == path), None) \
            or next((c for c in cands if posixpath.basename(c).lower() == posixpath.basename(path).lower()), None) \
            or (cands[0] if len(cands) == 1 else None)
        if pick is None:
            return None
        used.add(pick)
        if pick != path:
            renamed[idx] = pick
    return renamed




def _http_reason(code: int, text: str) -> str:
    """'Not Found' → 'HTTP 404 Not Found — the announce URL doesn't exist on the server (…)'.
    An HTTP error means the request never reached the tracker script, so there is no tracker text."""
    from .i18n import t
    hint = {401: "http_auth", 403: "http_forbidden", 404: "http_not_found", 410: "http_not_found",
            429: "http_too_many"}.get(code)
    if hint is None and code >= 500:
        hint = "http_down" if code in (502, 503, 504) else "http_server"
    text = text if text and not text[0].isdigit() else ""
    out = f"HTTP {code} {text}".rstrip()
    return f"{out} — {t(hint)}" if hint else out


_OUTGOING = int(getattr(lt.peer_info, "outgoing_connection", getattr(lt.peer_info, "local_connection", 0)))
_UTP = int(getattr(lt.peer_info, "utp_socket", 1 << 17))     # not exported by the bindings; bit 17 in libtorrent

TASK_ONLY_ALERTS = (lt.tracker_announce_alert, lt.state_changed_alert, lt.hash_failed_alert,
                    lt.torrent_paused_alert, lt.torrent_resumed_alert, lt.torrent_checked_alert,
                    lt.torrent_finished_alert, lt.metadata_received_alert, lt.add_torrent_alert,
                    lt.file_renamed_alert, lt.performance_alert)



def order_priorities(order: list[int], prios: list[int]) -> list[int]:
    """BitComet-style download order on top of libtorrent priorities: the first files get 7,
    the last 1 (libtorrent has 7 levels, so a long list shares levels in order). Skipped stay 0."""
    out = list(prios)
    wanted = [i for i in order if 0 <= i < len(out) and out[i] != 0]
    n = len(wanted)
    for pos, i in enumerate(wanted):
        out[i] = 7 - (pos * 7) // n
    return out

def _buckets(bits, n: int) -> list[float]:
    """A peer's piece bitfield squeezed to n fractions (0..1) for a small bar."""
    try:
        bits = list(bits)
    except TypeError:
        return []
    total = len(bits)
    if not total:
        return []
    n = min(n, total)
    out = []
    for b in range(n):
        a, z = b * total // n, max((b + 1) * total // n, b * total // n + 1)
        chunk = bits[a:z]
        out.append(sum(1 for x in chunk if x) / len(chunk))
    return out


# Settings → Advanced → Disk & cache. Presets fill these values in the dialog.
DISK_PRESETS = {
    "hdd": {"preallocate": True,  "disk_write_mode": "auto",   "disk_os_cache": "enable",
            "disk_queue_mb": 256, "aio_threads": 4,  "hashing_threads": 1, "checking_mem_mb": 32,
            "file_pool": 100, "send_buffer_kb": 1024},
    "ssd": {"preallocate": False, "disk_write_mode": "mmap",   "disk_os_cache": "enable",
            "disk_queue_mb": 128, "aio_threads": 16, "hashing_threads": max(1, min(4, os.cpu_count() or 1)),
            "checking_mem_mb": 64, "file_pool": 200, "send_buffer_kb": 1024},
    "lowram": {"preallocate": False, "disk_write_mode": "pwrite", "disk_os_cache": "enable",
               "disk_queue_mb": 32, "aio_threads": 4, "hashing_threads": 1, "checking_mem_mb": 8,
               "file_pool": 40, "send_buffer_kb": 500},
}

_LT_DEFAULTS = lt.default_settings()


def _disk_settings(c: dict) -> dict:
    """libtorrent disk / cache settings. Only keys this libtorrent build knows are sent
    (the Windows wheels lag behind the Linux ones)."""
    wm = getattr(lt, "mmap_write_mode_t", None)
    modes = {"pwrite": 0, "mmap": 1, "auto": 2}
    if wm is not None:
        modes = {"pwrite": int(wm.always_pwrite), "mmap": int(wm.always_mmap_write),
                 "auto": int(wm.auto_mmap_write)}
    io = lt.io_buffer_mode_t
    cache = {"enable": (int(io.enable_os_cache), int(io.enable_os_cache)),
             "disable": (int(io.disable_os_cache), int(io.disable_os_cache)),
             "write_through": (int(io.enable_os_cache), int(io.write_through))
             }.get(c.get("disk_os_cache", "enable"), (0, 0))
    out = {
        "disk_write_mode":      modes.get(c.get("disk_write_mode", "mmap"), modes["mmap"]),
        "disk_io_read_mode":    cache[0],
        "disk_io_write_mode":   cache[1],
        "max_queued_disk_bytes": max(1, int(c.get("disk_queue_mb", 100))) << 20,
        "aio_threads":          max(1, int(c.get("aio_threads", 10))),
        "hashing_threads":      max(1, int(c.get("hashing_threads", 1))),
        "checking_mem_usage":   max(1, int(c.get("checking_mem_mb", 4)) * 64),   # 16 KiB blocks
        "file_pool_size":       max(4, int(c.get("file_pool", 40))),
        "send_buffer_watermark": max(16, int(c.get("send_buffer_kb", 500))) * 1024,
    }
    return {k: v for k, v in out.items() if k in _LT_DEFAULTS}


# performance_alert kinds with an explanation in i18n ("perf_<name>"); at most once / 10 min each
_PERF_HINTS = ("outstanding_disk_buffer_limit_reached", "too_high_disk_queue_limit",
               "send_buffer_watermark_too_low", "too_few_file_descriptors")


def _proxy_settings(c: dict) -> dict:
    """libtorrent proxy settings from the config (Settings → Connection → Proxy)."""
    kind = c.get("proxy_type", "none")
    auth = bool(c.get("proxy_auth")) and bool(c.get("proxy_user"))
    pt = {"none": lt.proxy_type_t.none, "socks4": lt.proxy_type_t.socks4,
          "socks5": lt.proxy_type_t.socks5_pw if auth else lt.proxy_type_t.socks5,
          "http": lt.proxy_type_t.http_pw if auth else lt.proxy_type_t.http}.get(kind, lt.proxy_type_t.none)
    if kind == "none" or not c.get("proxy_host"):
        return {"proxy_type": int(lt.proxy_type_t.none)}
    return {"proxy_type": int(pt), "proxy_hostname": str(c.get("proxy_host", "")),
            "proxy_port": int(c.get("proxy_port", 1080)),
            "proxy_username": str(c.get("proxy_user", "")) if auth else "",
            "proxy_password": str(c.get("proxy_pass", "")) if auth else "",
            "proxy_peer_connections": bool(c.get("proxy_peers", True)),
            "proxy_tracker_connections": True,
            "proxy_hostnames": bool(c.get("proxy_hostnames", True))}


def _finished_now(st, started: float) -> bool:
    """True when the torrent completed during this run, not in an earlier one."""
    done = int(getattr(st, "completed_time", 0) or 0)
    return done > 0 and done >= started - 5


def _key_of(ih) -> str:
    return str(ih.v1) if ih.has_v1() else str(ih.v2)

def _left(t, now: float) -> int:
    """Seconds until a posix time from an announce entry, 0 when it's due / unknown."""
    try:
        t = float(t.timestamp()) if hasattr(t, "timestamp") else float(t)
    except (TypeError, ValueError):
        return 0
    return max(0, int(t - now)) if t > 0 else 0


_STATUS_RANK = {"working": 0, "updating": 1, "tracker_error": 2, "not_working": 3, "not_contacted": 4}


def _sum_tracker(url: str, tier: int, children: list[dict]) -> dict:
    """A tracker row from its announces: the best status wins, the numbers are the best known."""
    row = {"url": url, "tier": tier, "proto": "", "status": "not_contacted", "message": "",
           "peers": -1, "seeds": -1, "leeches": -1, "downloaded": -1, "next": 0, "min": 0,
           "children": children if len(children) > 1 else []}
    if not children:
        return row
    best = min(children, key=lambda c: _STATUS_RANK[c["status"]])
    row["status"] = best["status"]
    # an error shows only when no announce of this tracker is accepted
    row["message"] = "" if best["status"] == "working" else next((c["message"] for c in children if c["message"]), "")
    for k in ("peers", "seeds", "leeches", "downloaded"):
        row[k] = max(c[k] for c in children)
    nxt = [c["next"] for c in children if c["next"] > 0]
    row["next"] = min(nxt) if nxt else 0
    row["min"] = max(c["min"] for c in children)
    if len(children) == 1:
        row["proto"] = children[0]["proto"]
    return row

def _alert_id(a) -> tuple:
    """One tracker announce stream: URL × protocol version (v1/v2 hash) × local endpoint."""
    return (_alert_url(a), str(getattr(a, "version", "")), str(getattr(a, "local_endpoint", "")))


def _alert_url(a) -> str:
    try:
        return a.tracker_url()
    except Exception:
        return getattr(a, "url", "")


def t_prefix(msg: str) -> str:
    from .i18n import t
    return t("tr_error_prefix", msg)


def _is_skip_notice(a) -> bool:
    """'skipping tracker announce (unreachable)': not an error, the endpoint simply has no route."""
    msg = a.message()
    return "skipping tracker announce" in msg or "unreachable" in msg and "skipping" in msg


def _ratio(uploaded: int, downloaded: int, done: int) -> float:
    """Uploaded / max(downloaded, have): a torrent seeded from your own disk
    (downloaded = 0) still gets a real ratio instead of infinity."""
    base = max(downloaded, done)
    if base > 0:
        return uploaded / base
    return float("inf") if uploaded > 0 else 0.0


PIECE_SIZES = [0] + [1 << n for n in range(14, 26)]     # 0 = auto, 16 KiB … 32 MiB


# files nobody wants in a release: Windows / macOS folder litter, unfinished downloads
JUNK_NAMES = {"thumbs.db", "ehthumbs.db", "ehthumbs_vista.db", "desktop.ini", ".ds_store", "icon\r",
              ".localized", "folder.lock"}
JUNK_PREFIX = ("._", "~$", ".~lock.")
JUNK_EXT = (".!ut", ".!qb", ".bc!", ".crdownload", ".part", ".partial")
JUNK_DIRS = {"__macosx", ".spotlight-v100", ".trashes", ".fseventsd", "$recycle.bin",
             "system volume information", ".temporaryitems"}


def is_junk(name: str, is_dir: bool = False) -> bool:
    n = name.lower()
    if is_dir:
        return n in JUNK_DIRS
    return n in JUNK_NAMES or n.startswith(JUNK_PREFIX) or n.endswith(JUNK_EXT)


def _junk_pred(src: str):
    """add_files() filter: False for junk files and anything inside a junk folder."""
    root = os.path.abspath(src)

    def pred(path: str) -> bool:
        rel = os.path.relpath(os.path.abspath(path), os.path.dirname(root))
        parts = rel.replace("\\", "/").split("/")
        if any(is_junk(p, True) for p in parts[:-1]):
            return False
        return not is_junk(parts[-1], os.path.isdir(path))
    return pred


def recommended_piece(total: int) -> int:
    """About 1000–2000 pieces: small enough for a quick start of seeding, few enough for a small
    .torrent. 32 KiB … 16 MiB."""
    size = 32 << 10
    while size < (16 << 20) and total / size > 2000:
        size <<= 1
    return size


def analyze_source(src: str, exclude_junk: bool = True, fmt: str = "v1") -> dict:
    """What a new torrent of <src> would contain: files, size, junk, empty / zero-byte, and the piece
    size libtorrent's 'Auto' would take."""
    src = os.path.abspath(src)
    junk, zero, empty, files = [], [], [], 0
    total = 0
    base = os.path.dirname(src)
    if os.path.isfile(src):
        size = os.path.getsize(src)
        files, total = 1, size
        if size == 0:
            zero.append(os.path.basename(src))
    else:
        for root, dirs, names in os.walk(src):
            rel_root = os.path.relpath(root, base)
            bad_dir = any(is_junk(p, True) for p in rel_root.replace("\\", "/").split("/"))
            if not dirs and not names:
                empty.append(rel_root)
            for n in names:
                full = os.path.join(root, n)
                try:
                    size = os.path.getsize(full)
                except OSError:
                    continue
                rel = os.path.join(rel_root, n)
                if bad_dir or is_junk(n):
                    junk.append((rel, size))
                    if exclude_junk:
                        continue
                files += 1
                total += size
                if size == 0:
                    zero.append(rel)
    auto = 0
    try:
        fs = lt.file_storage()
        if exclude_junk and os.path.isdir(src):
            lt.add_files(fs, src, _junk_pred(src))
        else:
            lt.add_files(fs, src)
        if fs.num_files():
            flags = {"v1": lt.create_torrent.v1_only, "v2": lt.create_torrent.v2_only, "hybrid": 0}[fmt]
            auto = lt.create_torrent(fs, 0, flags=flags).piece_length()
    except Exception:
        pass
    return {"files": files, "total": total, "junk": junk, "zero": zero, "empty": empty,
            "auto_piece": auto, "recommended": recommended_piece(total)}


def create_torrent(src: str, announce: str, out_path: str, private: bool = True, comment: str = "",
                   piece_size: int = 0, fmt: str = "v1", web_seeds: list[str] | None = None,
                   source: str = "", progress=None, exclude_junk: bool = False) -> str:
    """Make a .torrent for uploading to a tracker.
    announce: one URL per line, each line its own tier. fmt: v1 | hybrid | v2.
    source: info['source'] tag (private trackers use it for cross-seeding).
    progress(done, total) is called while hashing."""
    fs = lt.file_storage()
    if exclude_junk and os.path.isdir(src):
        lt.add_files(fs, src, _junk_pred(src))
    else:
        lt.add_files(fs, src)
    if fs.num_files() == 0:
        raise ValueError("Нет файлов для торрента")
    flags = {"v1": lt.create_torrent.v1_only, "v2": lt.create_torrent.v2_only, "hybrid": 0}[fmt]
    t = lt.create_torrent(fs, int(piece_size), flags=flags)
    for tier, url in enumerate(u.strip() for u in announce.splitlines() if u.strip()):
        t.add_tracker(url, tier)
    for ws in web_seeds or []:
        if ws.strip():
            t.add_url_seed(ws.strip())
    t.set_priv(private)
    t.set_creator(f"{APP_NAME} {VERSION}")
    if comment:
        t.set_comment(comment)
    total = t.num_pieces()
    if progress:
        lt.set_piece_hashes(t, os.path.dirname(os.path.abspath(src)), lambda i: progress(i + 1, total))
    else:
        lt.set_piece_hashes(t, os.path.dirname(os.path.abspath(src)))
    d = t.generate()
    if source:
        d[b"info"][b"source"] = source.encode("utf-8")
    with open(out_path, "wb") as f:
        f.write(lt.bencode(d))
    return out_path
