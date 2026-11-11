"""libtorrent session wrapper for ArtCore Client.

Owns the session, keeps .torrent/resume data on disk so torrents survive a
restart, and exposes a small API the UI calls from its timer.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

import libtorrent as lt

from . import APP_NAME, VERSION, PEER_ID_CODE, VERSION_TUPLE

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
        self.messages: list[str] = []          # log lines for the UI
        self.finished_names: list[str] = []    # for tray notifications
        self._hosts: dict[str, str] = {}

        self.ses = lt.session(self._settings())
        self._load_state()

    # ── settings ───────────────────────────────────────────────
    def _settings(self) -> dict:
        c = self.cfg
        port = int(c.get("port", 51413))
        return {
            # Client identity: what the tracker sees in peer_id / User-Agent
            "peer_fingerprint": lt.generate_fingerprint(PEER_ID_CODE, *VERSION_TUPLE),
            "user_agent":       f"{APP_NAME}/{VERSION} libtorrent/{lt.__version__}",
            "listen_interfaces": f"0.0.0.0:{port},[::]:{port}",
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
                           | lt.alert_category.storage | lt.alert_category.tracker),
        }

    def apply_settings(self, cfg: dict) -> None:
        self.cfg = cfg
        self.ses.apply_settings(self._settings())

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
                self.messages.append(f"Не удалось восстановить {fn}: {e}")

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
    def add_torrent_file(self, path: str, save_path: str | None = None, paused: bool = False) -> str:
        ti = lt.torrent_info(path)
        atp = lt.add_torrent_params()
        atp.ti = ti
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

    def remove(self, key: str, delete_files: bool = False) -> None:
        h = self.handles.pop(key, None)
        if h is None:
            return
        self.ses.remove_torrent(h, lt.options_t.delete_files if delete_files else 0)
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
                self.finished_names.append(a.handle.status().name)
                a.handle.save_resume_data(lt.torrent_handle.save_info_dict)
            elif isinstance(a, lt.metadata_received_alert):
                a.handle.save_resume_data(lt.torrent_handle.save_info_dict)
            elif isinstance(a, (lt.tracker_error_alert, lt.torrent_error_alert,
                                lt.file_error_alert, lt.fastresume_rejected_alert)):
                self.messages.append(a.message())

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
                ratio=(ul / dl) if dl > 0 else (float("inf") if ul > 0 else 0.0),
                eta=eta, save_path=s.save_path,
                error=s.errc.message() if s.errc.value() else "",
                tracker=s.current_tracker,
                tracker_host=self._host(key, h),
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

    def peers(self, key: str) -> list[dict]:
        h = self.handles.get(key)
        if not h:
            return []
        res = []
        for p in h.get_peer_info():
            res.append({
                "ip": f"{p.ip[0]}:{p.ip[1]}",
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


def _mask_passkey(url: str) -> str:
    """Hide the passkey in announce URLs shown in the UI (screenshots!)."""
    import re
    return re.sub(r"(passkey=|/)([0-9a-fA-F]{32})(?=$|[/&?])", lambda m: m.group(1) + "•" * 8, url)


def create_torrent(src: str, announce: str, out_path: str, private: bool = True, comment: str = "") -> str:
    """Make a .torrent for uploading to the tracker."""
    fs = lt.file_storage()
    lt.add_files(fs, src)
    if fs.num_files() == 0:
        raise ValueError("Нет файлов для торрента")
    t = lt.create_torrent(fs)
    if announce:
        t.add_tracker(announce, 0)
    t.set_priv(private)
    t.set_creator(f"{APP_NAME} {VERSION}")
    if comment:
        t.set_comment(comment)
    lt.set_piece_hashes(t, os.path.dirname(os.path.abspath(src)))
    with open(out_path, "wb") as f:
        f.write(lt.bencode(t.generate()))
    return out_path
