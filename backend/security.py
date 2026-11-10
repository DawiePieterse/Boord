"""Who is allowed into the Admin app.

There is one farm admin - the person who runs the pack house - so there is no
account system. What used to be a username, a password and a 30-day token is
now the network path a request arrived on: the Admin app answers Tailscale,
and nothing else.

Not even the server's own console. Browsing http://localhost:8000/admin/ while
sitting at the machine is refused like anything else; the admin opens the
https://<machine>.<tailnet>.ts.net/ address whatever they are sitting at. That
is deliberate - a loopback exemption is an exemption anybody with a remote
desktop session to that laptop inherits, and AnyDesk is how this server is
usually reached.

TWO SIGNALS, because only one of them is certain:

  1. The peer address is a Tailscale one. `tailscale serve` proxies from the
     machine itself, so its connections land on loopback - but it sets
     X-Forwarded-For, and uvicorn runs with proxy_headers=True and trusts that
     header from loopback, so request.client is rewritten to the visitor's
     tailnet IP. Verified against uvicorn 0.32.1: a LAN client forging the
     same header is NOT rewritten, because its peer is not loopback.

  2. Failing that, the peer is loopback AND the Host names a .ts.net site.
     This is the belt to (1)'s braces. If a Tailscale version ever stops
     sending X-Forwarded-For, (1) silently stops matching and every request
     looks like it came from the console - which, without this, would lock the
     farm out of its own Admin app with no way in short of a rollback. A
     device on the farm wifi cannot reach this branch: it can forge a Host
     header trivially, but it cannot make its peer address loopback.

The Field and Pack House screens are NOT behind any of this. They are phones
and tablets in an orchard and on a receiving bay, they have never had
credentials, and uvicorn still binds 0.0.0.0 for them - see install.ps1.
Removing the login without this file would have handed those same devices
Settings, payments, exports and every worker's contact details.
"""
import ipaddress
import os
from typing import Optional, Union

from fastapi import HTTPException, Request, status

_IPAddress = Union[ipaddress.IPv4Address, ipaddress.IPv6Address]

# Tailscale's own address space - what signal (1) above looks for.
_TAILNET_RANGES = (
    ipaddress.ip_network("100.64.0.0/10"),        # CGNAT range - Tailscale IPv4
    ipaddress.ip_network("fd7a:115c:a1e0::/48"),  # Tailscale IPv6
)

# Every tailnet name ends here, so this is what signal (2) matches on. Checked
# only when the peer is already loopback; on its own a Host header proves
# nothing, since anyone can send any Host they like.
_TAILNET_HOST_SUFFIX = ".ts.net"

# Local development only, and the farm never sets it: backend/run_preview.py
# does, because a developer's Mac has no `tailscale serve` in front of it and
# would otherwise be unable to open the Admin app at all. start_server.bat -
# the only thing that launches a farm server - does not set it, so this cannot
# quietly become the way in on a real install.
DEV_LOOPBACK_ENV = "BOORD_ALLOW_LOOPBACK_ADMIN"

# What the server says when Admin is opened from anywhere else. It names the
# fix, because the person reading it is the one admin and the answer is always
# the same address.
ADMIN_ONLY_MESSAGE = (
    "The Admin app is only reachable over Tailscale - open the secure "
    "https://...ts.net/ address, not the farm wifi one and not localhost"
)


def _peer_address(request: Request) -> Optional[_IPAddress]:
    """The address this request came from, or None if there isn't a usable
    one. A missing or unparseable client counts as untrusted rather than as an
    error: an ASGI transport that does not set a peer would otherwise open the
    Admin app to everyone."""
    client = request.client
    if client is None or not client.host:
        return None
    try:
        return ipaddress.ip_address(client.host)
    except ValueError:
        return None


def _asks_for_a_tailnet_site(request: Request) -> bool:
    """Whether this request is addressed to a .ts.net name.

    Both headers are accepted because which one carries the original name is
    the proxy's choice: a reverse proxy may pass the client's Host through
    untouched, or rewrite it to the backend and move the original into
    X-Forwarded-Host. Only ever consulted for a request already known to have
    arrived on loopback."""
    for header in ("host", "x-forwarded-host"):
        value = request.headers.get(header, "")
        if not value:
            continue
        # Take the first entry (X-Forwarded-Host can be a list), drop any
        # :port, and unwrap an IPv6 literal's brackets.
        name = value.split(",")[0].strip().rsplit(":", 1)[0].strip("[]").lower()
        if name.endswith(_TAILNET_HOST_SUFFIX):
            return True
    return False


def is_tailnet_client(request: Request) -> bool:
    """True when this request arrived over Tailscale: it may load the Admin
    app's pages. What it may see once there is is_admin_client's question."""
    address = _peer_address(request)
    if address is None:
        return False
    if any(address in network for network in _TAILNET_RANGES):
        return True
    if address.is_loopback:
        if os.environ.get(DEV_LOOPBACK_ENV) == "1":
            return True
        return _asks_for_a_tailnet_site(request)
    return False


# Dashboard-only devices: one Tailscale IP per line (# starts a comment), in a
# file the farm keeps and no release touches. A device listed here opens the
# Admin app but gets the Dashboard tab and nothing else - a farm manager who
# should see the harvest, not the payroll.
#
# By IP rather than by Tailscale's identity headers on purpose: uvicorn also
# answers the tailnet directly on port 8000, where `tailscale serve` adds no
# headers, so "no header" would have to mean "full admin" and the direct port
# would be the way round. The peer address is the same on both paths.
#
# It lists the RESTRICTED devices, so it fails open: a tailnet device not in
# the file is the admin, as it always was. The other way round, one wrong line
# locks the office out of Admin. Under signal (2) above (no X-Forwarded-For)
# the peer is loopback, no line matches, and everyone is the admin again.
DASHBOARD_ONLY_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "dashboard_only.txt")

DASHBOARD_ONLY_MESSAGE = "This device has dashboard-only access to the Admin app"


def _dashboard_only_addresses() -> set:
    # ponytail: read on every admin request, no cache. A handful of lines and
    # one office's traffic; cache on the file's mtime if that ever changes.
    try:
        with open(DASHBOARD_ONLY_FILE, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        return set()
    found = set()
    for line in lines:
        try:
            found.add(ipaddress.ip_address(line.split("#")[0].strip()))
        except ValueError:
            pass  # blank, comment, or a typo: a line that restricts nobody
    return found


def is_dashboard_only(request: Request) -> bool:
    address = _peer_address(request)
    return address is not None and address in _dashboard_only_addresses()


# The admin-only calls the Dashboard tab makes; everything else it loads is
# open to every device. Exact pairs, not a prefix: a new dashboard endpoint
# stays closed to these devices until somebody adds it here on purpose.
_DASHBOARD_ONLY_ROUTES = {
    ("GET", "/api/setup/state"),       # the page's first call; says "hide the tabs"
    ("GET", "/api/dashboard/summary"),
}


def _dashboard_only_may(request: Request) -> bool:
    """Whether a dashboard-only device may make this admin-only request."""
    return (request.method, request.url.path) in _DASHBOARD_ONLY_ROUTES


def is_admin_client(request: Request) -> bool:
    """True when this request may see admin data. Usable as a dependency in
    its own right for endpoints that serve everybody but serve the admin more
    - see master_data.list_workers, where a dashboard-only device gets what a
    field tablet gets."""
    return is_tailnet_client(request) and not is_dashboard_only(request)


def require_admin_client(request: Request) -> None:
    """Admin-only endpoints depend on this. 403 rather than 401: there are no
    credentials to go and fetch, so "try again with a token" would be a lie -
    this request came in on the wrong network and always will."""
    if not is_tailnet_client(request):
        raise HTTPException(status.HTTP_403_FORBIDDEN, ADMIN_ONLY_MESSAGE)
    if is_dashboard_only(request) and not _dashboard_only_may(request):
        raise HTTPException(status.HTTP_403_FORBIDDEN, DASHBOARD_ONLY_MESSAGE)
