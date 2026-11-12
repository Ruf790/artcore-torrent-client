# SPDX-License-Identifier: LGPL-3.0-or-later
"""ArtCore Client — BitTorrent client for Windows."""

APP_NAME      = "ArtCore"
APP_TITLE     = "ArtCore Client"
VERSION_TUPLE = (1, 0, 41, 0)
VERSION       = "1.0.41"
# Azureus-style peer_id prefix: -Ag1000- — built from MAJOR.MINOR only, so 1.0.x bug-fix
# releases keep the same prefix; 1.1.0 becomes -Ag1100-.
# The tracker whitelist (allowed_clients) must contain this 8-char prefix.
PEER_ID_CODE  = "Ag"
