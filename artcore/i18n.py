"""Tiny RU/EN string table."""
from __future__ import annotations

_lang = "ru"

STRINGS = {
    # toolbar / menu
    "add_torrent":     ("Добавить торрент",       "Add torrent"),
    "add_magnet":      ("Magnet-ссылка",          "Magnet link"),
    "create_torrent":  ("Создать торрент",        "Create torrent"),
    "start":           ("Старт",                  "Start"),
    "pause":           ("Пауза",                  "Pause"),
    "remove":          ("Удалить",                "Remove"),
    "settings":        ("Настройки",              "Settings"),
    "open_folder":     ("Открыть папку",          "Open folder"),
    "recheck":         ("Проверить файлы",        "Force recheck"),
    "reannounce":      ("Обновить трекер",        "Force reannounce"),
    "copy_magnet":     ("Копировать magnet",      "Copy magnet"),
    "show":            ("Показать",               "Show"),
    "quit":            ("Выход",                  "Quit"),
    # columns
    "col_name":        ("Название",   "Name"),
    "col_size":        ("Размер",     "Size"),
    "col_progress":    ("Прогресс",   "Progress"),
    "col_state":       ("Статус",     "Status"),
    "col_down":        ("Загрузка",   "Down"),
    "col_up":          ("Отдача",     "Up"),
    "col_seeds":       ("Сиды",       "Seeds"),
    "col_peers":       ("Пиры",       "Peers"),
    "col_ratio":       ("Рейтинг",    "Ratio"),
    "col_eta":         ("Осталось",   "ETA"),
    # states
    "st_checking":     ("Проверка",       "Checking"),
    "st_metadata":     ("Метаданные",     "Metadata"),
    "st_downloading":  ("Загрузка",       "Downloading"),
    "st_finished":     ("Завершён",       "Finished"),
    "st_seeding":      ("Раздача",        "Seeding"),
    "st_paused":       ("Пауза",          "Paused"),
    "st_queued":       ("В очереди",      "Queued"),
    "st_error":        ("Ошибка",         "Error"),
    "st_unknown":      ("—",              "—"),
    # tabs
    "tab_general":     ("Общее",      "General"),
    "tab_peers":       ("Пиры",       "Peers"),
    "tab_trackers":    ("Трекеры",    "Trackers"),
    "tab_log":         ("Журнал",     "Log"),
    "peer_ip":         ("IP",         "IP"),
    "peer_client":     ("Клиент",     "Client"),
    "tr_url":          ("URL",        "URL"),
    "tr_tier":         ("Уровень",    "Tier"),
    "tr_msg":          ("Сообщение",  "Message"),
    "gen_path":        ("Папка",      "Save path"),
    "gen_uploaded":    ("Отдано",     "Uploaded"),
    "gen_downloaded":  ("Скачано",    "Downloaded"),
    "gen_tracker":     ("Трекер",     "Tracker"),
    "gen_error":       ("Ошибка",     "Error"),
    # dialogs
    "magnet_prompt":   ("Вставьте magnet-ссылку:",           "Paste a magnet link:"),
    "remove_title":    ("Удалить торрент",                   "Remove torrent"),
    "remove_text":     ("Удалить выбранные торренты ({0})?", "Remove selected torrents ({0})?"),
    "remove_files":    ("Удалить и скачанные файлы",         "Also delete downloaded files"),
    "choose_folder":   ("Куда сохранить",                    "Save to"),
    "torrent_filter":  ("Торрент-файлы (*.torrent)",         "Torrent files (*.torrent)"),
    "add_error":       ("Не удалось добавить: {0}",          "Could not add: {0}"),
    "finished_note":   ("Скачано: {0}",                      "Download finished: {0}"),
    "status_speed":    ("↓ {0}/s   ↑ {1}/s",                 "↓ {0}/s   ↑ {1}/s"),
    "torrents_count":  ("Торрентов: {0}",                    "Torrents: {0}"),
    # settings dialog
    "set_save_path":   ("Папка загрузок",                    "Download folder"),
    "set_browse":      ("Обзор…",                            "Browse…"),
    "set_port":        ("Порт",                              "Port"),
    "set_dl_limit":    ("Лимит загрузки, КБ/с (0 — без)",    "Download limit, KiB/s (0 = none)"),
    "set_ul_limit":    ("Лимит отдачи, КБ/с (0 — без)",      "Upload limit, KiB/s (0 = none)"),
    "set_active_dl":   ("Активных загрузок",                 "Active downloads"),
    "set_active_seed": ("Активных раздач",                   "Active seeds"),
    "set_dht":         ("DHT / LSD (для публичных торрентов)", "DHT / LSD (public torrents only)"),
    "set_upnp":        ("UPnP / NAT-PMP",                    "UPnP / NAT-PMP"),
    "set_tray":        ("Сворачивать в трей при закрытии",   "Minimize to tray on close"),
    "set_lang":        ("Язык (после перезапуска)",          "Language (after restart)"),
    # create torrent
    "ct_title":        ("Создать торрент",                   "Create torrent"),
    "ct_source":       ("Файл или папка",                    "File or folder"),
    "ct_file":         ("Файл…",                             "File…"),
    "ct_folder":       ("Папка…",                            "Folder…"),
    "ct_announce":     ("Announce URL (ваш, с passkey)",     "Announce URL (yours, with passkey)"),
    "ct_comment":      ("Комментарий",                       "Comment"),
    "ct_private":      ("Приватный (обязательно для трекера)", "Private (required by the tracker)"),
    "ct_seed":         ("Сразу начать раздачу",              "Start seeding right away"),
    "ct_done":         ("Торрент создан:\n{0}\n\nЗагрузите его на трекер.", "Torrent created:\n{0}\n\nUpload it to the tracker."),
    "ct_working":      ("Хеширование…",                      "Hashing…"),
    # site-style UI
    "subtitle":        ("Торрент-клиент · ArtCore Gangsta",  "Torrent client · ArtCore Gangsta"),
    "meta_version":    ("Версия {0}",                        "Version {0}"),
    "meta_port":       ("Порт {0}",                          "Port {0}"),
    "meta_dht":        ("DHT: {0} узлов",                    "DHT: {0} nodes"),
    "meta_dht_off":    ("DHT выключен",                      "DHT off"),
    "kpi_down":        ("Загрузка",                          "Download"),
    "kpi_up":          ("Отдача",                            "Upload"),
    "kpi_active":      ("Активных из {0}",                   "Active of {0}"),
    "kpi_session":     ("Отдано за сессию",                  "Uploaded this session"),
    "f_all":           ("Все",                               "All"),
    "f_downloading":   ("Загружаются",                       "Downloading"),
    "f_seeding":       ("Раздаются",                         "Seeding"),
    "f_paused":        ("Остановлены",                       "Stopped"),
    "f_error":         ("Ошибки",                            "Errors"),
    "tracker_all":     ("Все трекеры",                       "All trackers"),
    "col_tracker":     ("Трекер",                            "Tracker"),
    "search_ph":       ("Поиск по названию…",                "Search by name…"),
    "theme_toggle":    ("Светлая / тёмная тема",             "Light / dark theme"),
    "empty_title":     ("Торрентов пока нет",                "No torrents yet"),
    "empty_text":      ("Перетащите .torrent-файл в окно или нажмите «Добавить торрент»",
                        "Drop a .torrent file here or click “Add torrent”"),
    "gen_size":        ("Размер",                            "Size"),
    "gen_ratio":       ("Рейтинг",                           "Ratio"),
    "set_grp_general": ("Общие",                             "General"),
    "set_grp_conn":    ("Соединение",                        "Connection"),
    "set_grp_windows": ("Windows",                           "Windows"),
    "set_theme":       ("Тема",                              "Theme"),
    "theme_system":    ("Как в Windows",                     "Same as Windows"),
    "theme_light":     ("Светлая",                           "Light"),
    "theme_dark":      ("Тёмная",                            "Dark"),
    "set_autostart":   ("Запускать вместе с Windows (свёрнутым в трей)", "Start with Windows (minimized to tray)"),
    "set_assoc":       ("Открывать .torrent и magnet этим клиентом", "Open .torrent and magnet with this client"),
    "set_assoc_btn":   ("Сделать клиентом по умолчанию",     "Make default client"),
    "assoc_done":      (".torrent-файлы и magnet-ссылки теперь открываются в ArtCore Client.",
                        ".torrent files and magnet links now open in ArtCore Client."),
    "assoc_default":   ("Уже клиент по умолчанию",           "Already the default client"),
    "assoc_portable":  ("Доступно в установленной версии (.exe)", "Available in the installed version (.exe)"),
}


def set_lang(lang: str) -> None:
    global _lang
    _lang = "en" if lang == "en" else "ru"


def t(key: str, *args) -> str:
    pair = STRINGS.get(key)
    s = (pair[0] if _lang == "ru" else pair[1]) if pair else key
    for i, a in enumerate(args):
        s = s.replace("{" + str(i) + "}", str(a))
    return s
