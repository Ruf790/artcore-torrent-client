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
        # per-torrent options libtorrent doesn't keep in resume data (stop-seeding rules)
        self.opts_path = os.path.join(data_dir, "torrent_opts.json")
        try:
            with open(self.opts_path, encoding="utf-8") as f:
                self.opts: dict[str, dict] = json.load(f)
        except (FileNotFoundError, ValueError):
            self.opts = {}

        self.ses = lt.session(self._settings())
        self._load_state()

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
            "peer_fingerprint": lt.generate_fingerprint(PEER_ID_CODE, *VERSION_TUPLE),
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
        f = lt.ip_filter()
        for a, b in ranges:
            f.add_rule(a, b, 1)
        self.ses.set_ip_filter(f)
        self.ip_filter_rules = len(ranges)

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
                self.finished_names.append(st.name)
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
            elif isinstance(a, (lt.tracker_error_alert, lt.torrent_error_alert, lt.listen_failed_alert,
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
        if time.time() - self._stats_flushed > 300:     # survive a crash: flush totals every 5 min
            self._flush_stats()

    def _log(self, kind: str, text: str) -> None:
        """kind: system | network | torrent | tracker | error. Passkeys are masked."""
        self.messages.append((kind, _mask_passkey(text)))
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
            if s.errc.value():
                state = "error"
            elif paused and auto:
                state = "queued"
            elif paused:
                state = "paused"
            else:
                state = STATE_NAMES.get(s.state, "unknown")

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
                error=s.errc.message() if s.errc.value() else "",
                tracker=s.current_tracker,
                tracker_host=self._host(key, h),
                category=self.opts.get(key, {}).get("category", ""),
            ))
        return out

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
            "dl_limit": max(0, h.download_limit()) // 1024,     # KiB/s, 0 = unlimited
            "ul_limit": max(0, h.upload_limit()) // 1024,
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
        # new rule → may stop again even if it already fired once
        self.opts[key] = {"stop_mode": p["stop_mode"], "stop_ratio": float(p["stop_ratio"]),
                          "stop_hours": int(p["stop_hours"]), "rule_done": False}
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
            if hit:
                self.pause(key)
                self.opts.setdefault(key, {})["rule_done"] = True
                self._save_opts()
                self.stopped_names.append(s.name)

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

    def trackers(self, key: str) -> list[dict]:
        h = self.handles.get(key)
        if not h:
            return []
        out = []
        for t in h.trackers():
            msg = ""
            for ep in t.get("endpoints", []):
                for ih in ep.get("info_hashes", []):
                    if ih.get("message"):
                        msg = ih["message"]
            out.append({"url": _mask_passkey(t["url"]), "tier": t["tier"], "message": msg})
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


_PASSKEY_PARAM = None
_PASSKEY_PATH = None


def _mask_passkey(text: str) -> str:
    """Hide passkeys in announce URLs and tracker messages shown in the UI / log (screenshots!).
    Covers ?passkey= / torrent_pass= / authkey= / pk= / uk= / key= and 32-hex path segments."""
    import re
    global _PASSKEY_PARAM, _PASSKEY_PATH
    if _PASSKEY_PARAM is None:
        _PASSKEY_PARAM = re.compile(r"([?&;](?:passkey|torrent_pass|authkey|pk|uk|key)=)[A-Za-z0-9_-]{8,}", re.I)
        _PASSKEY_PATH = re.compile(r"(/)[0-9a-fA-F]{32}(?=[/?&\s\"']|$)")
    text = _PASSKEY_PARAM.sub(lambda m: m.group(1) + "•" * 8, text)
    return _PASSKEY_PATH.sub(lambda m: m.group(1) + "•" * 8, text)


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
