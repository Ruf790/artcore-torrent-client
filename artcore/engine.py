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


def _key(h: lt.torrent_handle) -> str:
    ih = h.info_hashes()
    return str(ih.v1) if ih.has_v1() else str(ih.v2)


class Engine:
    def __init__(self, data_dir: str, cfg: dict):
        self.data_dir   = data_dir
        self.resume_dir = os.path.join(data_dir, "resume")
        os.makedirs(self.resume_dir, exist_ok=True)

        self.cfg      = cfg
        self.handles: dict[str, lt.torrent_handle] = {}
        self.messages: list[tuple[str, str]] = []   # (kind, text) log events for the UI
        self.started = time.time()
        self.bound_ip, self.bind_ok = "", True
        self.ip_filter_rules = 0
        self.wan_ip = ""
        self.portmap: dict[str, str] = {}     # "UPnP" / "NAT-PMP" → status text
        # key → {(tracker url, v1/v2, local endpoint): last error, "" = ok}
        self.tr_status: dict[str, dict[tuple, str]] = {}
        self.stats_path = os.path.join(data_dir, "stats.json")
        try:
            with open(self.stats_path, encoding="utf-8") as f:
                self.stats_saved = json.load(f)
        except (FileNotFoundError, ValueError):
            self.stats_saved = {"all_down": 0, "all_up": 0}
        self._stats_flushed = time.time()
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

        # bans: ip → reason; user's IP-filter ranges are kept so both can be combined
        self.bans_path = os.path.join(data_dir, "bans.json")
        try:
            with open(self.bans_path, encoding="utf-8") as f:
                self.bans: dict[str, str] = json.load(f)
        except (FileNotFoundError, ValueError):
            self.bans = {}
        self._user_ranges: list[tuple[str, str]] = []
        self._peer_seen: dict[tuple, tuple] = {}      # (key, ip) → (uploaded to it, its progress) at first sight
        self._limited: set[str] = set()                # torrents whose limits come from a tracker rule
        self._cross_check: set[str] = set()            # cross-seeded, waiting for the hash check
        self.cross_results: list[tuple[str, str, float]] = []   # (name, key, progress) for the UI

        self.ses = lt.session(self._settings())
        self._load_state()
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
            "user_agent":       f"{APP_NAME}/{VERSION} libtorrent/{lt.__version__}",
            "listen_interfaces": listen,
            "outgoing_interfaces": outgoing,
            "out_enc_policy": int(enc[0]), "in_enc_policy": int(enc[0]),
            "allowed_enc_level": int(enc[1]),
            "enable_dht":   bool(c.get("dht", True)),
            "enable_lsd":   bool(c.get("lsd", True)),
            "enable_upnp":  bool(c.get("upnp", True)),
            "enable_natpmp": bool(c.get("upnp", True)),
            "download_rate_limit": int(c.get("dl_limit", 0)) * 1024,
            "upload_rate_limit":   int(c.get("ul_limit", 0)) * 1024,
            "active_downloads": int(c.get("active_downloads", 5)),
            "active_seeds":     int(c.get("active_seeds", 20)),
            "active_limit":     int(c.get("active_downloads", 5)) + int(c.get("active_seeds", 20)),
            # Private-tracker friendly: announce honestly to every tracker tier
            "announce_to_all_trackers": True,
            "announce_to_all_tiers":    True,
            "alert_mask": (lt.alert_category.error | lt.alert_category.status
                           | lt.alert_category.storage | lt.alert_category.tracker
                           | lt.alert_category.port_mapping),
        }

    def apply_settings(self, cfg: dict) -> None:
        self.cfg = cfg
        self.ses.apply_settings(self._settings())

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
    def ban(self, ip: str, reason: str) -> None:
        if ip in self.bans:
            return
        self.bans[ip] = reason
        self._save_bans()
        self._apply_filter()
        self._log("network", f"ban {ip}: {reason}")

    def clear_bans(self) -> None:
        self.bans.clear()
        self._save_bans()
        self._apply_filter()

    def _save_bans(self) -> None:
        tmp = self.bans_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.bans, f, indent=1)
        os.replace(tmp, self.bans_path)

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
                    self.ban(ip, f"{bad} ({client or pid[:8].decode('latin-1', 'replace')})")
                    continue
                seen = self._peer_seen.get((key, ip))
                if seen is None:
                    self._peer_seen[(key, ip)] = (p.total_upload, p.progress)
                    continue
                sent = p.total_upload - seen[0]
                gained = (p.progress - seen[1]) * size
                if sent > max(FAKE_MIN_BYTES, size * 0.05) and gained < sent * 0.1:
                    self.ban(ip, f"fake progress: got {sent >> 20} MiB, progress +{max(gained, 0) / size:.1%} ({client})")
        if len(self._peer_seen) > 20000:
            self._peer_seen.clear()

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
            if not h.is_valid() or self.opts.get(key, {}).get("own_limits"):
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
                h = self.ses.add_torrent(atp)
                self.handles[_key(h)] = h
            except Exception as e:  # corrupt resume file — skip it, keep the rest
                self._log("error", f"{fn}: {e}")

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
        tmp = self._resume_path(key) + ".tmp"
        with open(tmp, "wb") as f:
            f.write(lt.write_resume_data_buf(a.params))
        os.replace(tmp, self._resume_path(key))

    # ── adding / removing ──────────────────────────────────────
    def add_torrent_file(self, path: str, save_path: str | None = None, paused: bool = False,
                         file_priorities: list[int] | None = None) -> str:
        ti = lt.torrent_info(path)
        atp = lt.add_torrent_params()
        atp.ti = ti
        if file_priorities is not None:
            atp.file_priorities = file_priorities
        return self._add(atp, save_path, paused)

    def add_magnet(self, uri: str, save_path: str | None = None, paused: bool = False) -> str:
        atp = lt.parse_magnet_uri(uri)
        return self._add(atp, save_path, paused)

    def _add(self, atp: lt.add_torrent_params, save_path: str | None, paused: bool) -> str:
        atp.save_path = save_path or self.cfg.get("save_path") or os.path.expanduser("~/Downloads")
        if paused:
            atp.flags |= lt.torrent_flags.paused
            atp.flags &= ~lt.torrent_flags.auto_managed
        h = self.ses.add_torrent(atp)
        key = _key(h)
        if key in self.handles:
            raise ValueError("Этот торрент уже добавлен")
        self.handles[key] = h
        h.save_resume_data(lt.torrent_handle.save_info_dict)
        return key

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
        h = self.handles.pop(key, None)
        if h is None:
            return
        self.ses.remove_torrent(h, lt.options_t.delete_files if delete_files else 0)
        if self.opts.pop(key, None) is not None:
            self._save_opts()
        try:
            os.remove(self._resume_path(key))
        except FileNotFoundError:
            pass

    # ── control ────────────────────────────────────────────────
    def pause(self, key: str) -> None:
        h = self.handles.get(key)
        if h:
            h.unset_flags(lt.torrent_flags.auto_managed)
            h.pause()
            h.save_resume_data(lt.torrent_handle.save_info_dict)

    def resume(self, key: str) -> None:
        h = self.handles.get(key)
        if h:
            h.set_flags(lt.torrent_flags.auto_managed)
            h.resume()
            h.save_resume_data(lt.torrent_handle.save_info_dict)

    def recheck(self, key: str) -> None:
        h = self.handles.get(key)
        if h:
            h.force_recheck()

    def reannounce(self, key: str) -> None:
        h = self.handles.get(key)
        if h:
            h.force_reannounce()

    # ── polling ────────────────────────────────────────────────
    def poll(self) -> None:
        """Process alerts. Call from the UI timer (~1 s)."""
        for a in self.ses.pop_alerts():
            if isinstance(a, lt.save_resume_data_alert):
                self._write_resume(a)
            elif isinstance(a, lt.torrent_finished_alert):
                st = a.handle.status()
                self.finished_names.append((st.name, _key(a.handle)))
                a.handle.save_resume_data(lt.torrent_handle.save_info_dict)
                self._move_when_done(a.handle, st)
            elif isinstance(a, lt.storage_moved_alert):
                self._log("torrent", a.message())
                a.handle.save_resume_data(lt.torrent_handle.save_info_dict)
            elif isinstance(a, lt.storage_moved_failed_alert):
                self._log("error", a.message())
            elif isinstance(a, lt.metadata_received_alert):
                a.handle.save_resume_data(lt.torrent_handle.save_info_dict)
            elif isinstance(a, lt.tracker_error_alert) and _is_skip_notice(a):
                pass   # libtorrent skipped a local endpoint (127.0.0.1, ::1, fe80::) that can't reach the tracker
            elif isinstance(a, lt.tracker_error_alert):
                self._tracker_failed(a)
                # a rejected v2 announce while v1 works (hybrid torrent) is just a tracker note
                self._log("error" if self.tracker_error(_key(a.handle)) else "tracker", a.message())
            elif isinstance(a, lt.tracker_reply_alert):
                self.tr_status.setdefault(_key(a.handle), {})[_alert_id(a)] = ""
                self._log("tracker", a.message())
            elif isinstance(a, (lt.torrent_error_alert, lt.listen_failed_alert,
                                lt.file_error_alert, lt.fastresume_rejected_alert)):
                self._log("error", a.message())
            elif isinstance(a, (lt.tracker_reply_alert, lt.tracker_warning_alert)):
                self._log("tracker", a.message())
            elif isinstance(a, lt.listen_succeeded_alert):
                self._log("network", a.message())
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
        if self._polls % 10 == 0 and self.cfg.get("ban_bad_peers", True):
            try:
                self._scan_peers()
            except Exception as e:      # never let peer bookkeeping break the alert loop
                self._log("error", f"peer scan: {e}")
        if time.time() - self._stats_flushed > 300:     # survive a crash: flush totals every 5 min
            self._flush_stats()

    def _tracker_failed(self, a) -> None:
        reason = ""
        try:
            reason = a.failure_reason() or ""        # text the tracker sent ("Invalid Torrent …")
        except Exception:
            pass
        if not reason:
            reason = a.error.message() if a.error.value() else a.message()
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
        """kind: system | network | torrent | tracker | error."""
        self.messages.append((kind, text))
        del self.messages[:-2000]

    # ── statistics ─────────────────────────────────────────────
    def _flush_stats(self) -> None:
        st = self.ses.status()
        data = {"all_down": self.stats_saved["all_down"] + st.total_payload_download,
                "all_up": self.stats_saved["all_up"] + st.total_payload_upload}
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
        }

    def rows(self) -> list[TorrentRow]:
        out = []
        for key, h in self.handles.items():
            if not h.is_valid():
                continue
            s = h.status()
            paused = bool(s.flags & lt.torrent_flags.paused)
            auto   = bool(s.flags & lt.torrent_flags.auto_managed)
            tr_err = ""
            if s.errc.value():
                state = "error"
            elif paused and auto:
                state = "queued"
            elif paused:
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
                key=key, name=s.name or key, size=s.total_wanted, progress=s.progress,
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
            rule_done=False, own_limits=own, dl_limit=int(p["dl_limit"]), ul_limit=int(p["ul_limit"]))
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
                self.pause(key)
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

    def move(self, key: str, path: str) -> None:
        """Move downloaded files to another folder (no re-download)."""
        h = self.handles.get(key)
        if h and path and os.path.normcase(os.path.abspath(path)) != os.path.normcase(os.path.abspath(h.status().save_path)):
            os.makedirs(path, exist_ok=True)
            h.move_storage(path)

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
                "path": fs.file_path(i),
                "size": size,
                "progress": (done[i] / size) if size else 1.0,
                "priority": prios[i] if i < len(prios) else PRIO_NORMAL,
            })
        return out

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
        res = []
        for p in h.get_peer_info():
            res.append({
                "ip": f"{p.ip[0]}:{p.ip[1]}",
                "host": p.ip[0],
                "client": p.client.decode("utf-8", "replace") if isinstance(p.client, bytes) else str(p.client),
                "progress": p.progress,
                "down": p.payload_down_speed,
                "up": p.payload_up_speed,
            })
        return res

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
        h = self.handles.get(key)
        if not h:
            return []
        out = []
        for t in h.trackers():
            msg, ok = "", False
            for ep in t.get("endpoints", []):
                for ih in ep.get("info_hashes", []):   # [v1, v2]
                    if ih.get("message"):
                        msg = msg or ih["message"]
                    elif ih.get("start_sent") and not ih.get("fails"):
                        ok = True                       # some announce (v1 or v2) is accepted
            if ok:
                msg = ""
            out.append({"url": t["url"], "tier": t["tier"], "message": msg})
        return out

    def totals(self) -> tuple[int, int]:
        st = self.ses.status()
        return st.payload_download_rate, st.payload_upload_rate

    def shutdown(self) -> None:
        self.ses.pause()
        self.save_all()
        try:
            self._flush_stats()
        except OSError:
            pass


# Leech-only clients (take, never give back) — matched by peer-id prefix or client name
BAD_PEER_IDS = (b"-XL", b"-SD", b"-XF", b"-QD", b"-BN", b"-DL", b"-HP", b"-TT")
BAD_NAMES = ("xunlei", "thunder", "qqdownload", "baidu", "dandelion", "offline download", "torrentstorm")
FAKE_MIN_BYTES = 64 << 20      # judge fake progress only after sending a peer this much


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


def create_torrent(src: str, announce: str, out_path: str, private: bool = True, comment: str = "",
                   piece_size: int = 0, fmt: str = "v1", web_seeds: list[str] | None = None,
                   source: str = "", progress=None) -> str:
    """Make a .torrent for uploading to a tracker.
    announce: one URL per line, each line its own tier. fmt: v1 | hybrid | v2.
    source: info['source'] tag (private trackers use it for cross-seeding).
    progress(done, total) is called while hashing."""
    fs = lt.file_storage()
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
