"""Which hosts a push subscription's endpoint may point at.

The server POSTs to the endpoint a browser hands it (a test push, the daily reminder), so an arbitrary https URL
would let a student aim the server at internal hosts. Only the push services the browsers use are accepted, by
host name (never an IP literal), https only, no credentials or odd ports.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

EXACT = frozenset({"fcm.googleapis.com", "updates.push.services.mozilla.com"})
SUFFIXES = (".push.apple.com", ".push.services.mozilla.com", ".notify.windows.com")


def allowed_endpoint(endpoint: str) -> bool:
    try:
        u = urlsplit(endpoint)
        host = (u.hostname or "").lower()
        port = u.port
    except ValueError:
        return False
    if u.scheme != "https" or u.username or u.password or port not in (None, 443) or not host:
        return False
    try:
        ipaddress.ip_address(host)
        return False  # an IP literal is never a push service
    except ValueError:
        pass
    return host in EXACT or host.endswith(SUFFIXES)
