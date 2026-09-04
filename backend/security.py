"""Who is allowed into the Admin app.

There is one farm admin - the person who runs the pack house - so there is no
account system any more. What used to be a username, a password and a 30-day
token is now the network path a request arrived on: the Admin app answers the
server's own console and the tailnet, and nothing else.

That substitution is only honest because of how the farm is actually wired.
MANUAL.md has the server published with

    tailscale serve --bg --https=443 http://localhost:8000

so every Tailscale visitor comes through a proxy running ON the server and
reaches uvicorn from loopback, while a phone on the farm wifi hitting
http://192.168.68.114:8000 directly arrives as 192.168.x.x. The two paths are
distinguishable at the socket, which is the whole basis of this module.

The Field and Pack House screens are deliberately NOT behind this. They are
phones and tablets in an orchard and on a receiving bay, they have never had
credentials, and uvicorn still binds 0.0.0.0 for them - see install.ps1.
Removing the login without this file would have handed those same devices
Settings, payments, exports and every worker's ID number and bank details.
"""
import ipaddress
from typing import Optional, Union

from fastapi import HTTPException, Request, status

_IPAddress = Union[ipaddress.IPv4Address, ipaddress.IPv6Address]

# Tailscale's own address space. Needed for two paths that both end up here
# with a tailnet address rather than a loopback one:
#
#  - a farm that reaches the server by its tailnet IP (`tailscale ip -4`,
#    MANUAL.md step 2) instead of through `tailscale serve`;
#  - `tailscale serve` itself, because uvicorn honours X-Forwarded-For from
#    127.0.0.1 by default and so rewrites request.client to the visitor's
#    tailnet IP. Dropping these ranges would lock the admin out of the very
#    address the manual tells her to use.
#
# A LAN machine cannot borrow one of these addresses: the server routes
# replies to them out of the Tailscale interface, so a 192.168.x.x host
# claiming a 100.64.x.x source never completes the TCP handshake. And a
# forged X-Forwarded-For from the LAN is ignored, because uvicorn only reads
# that header when the immediate peer is itself loopback.
_TAILNET_RANGES = (
    ipaddress.ip_network("100.64.0.0/10"),        # CGNAT range - Tailscale IPv4
    ipaddress.ip_network("fd7a:115c:a1e0::/48"),  # Tailscale IPv6
)

# What the server says when the Admin app is reached from the farm wifi. It
# names the fix, because the person reading it is the one admin and the answer
# is always the same: open the .ts.net address instead.
ADMIN_ONLY_MESSAGE = (
    "The Admin app is only reachable from the server itself or over Tailscale - "
    "open the secure https://...ts.net/ address instead of the farm wifi one"
)


def _peer_address(request: Request) -> Optional[_IPAddress]:
    """The address this request came from, or None if there isn't a usable
    one. A missing or unparseable client counts as untrusted rather than as
    an error: TestClient and ASGI transports that do not set a peer would
    otherwise open the Admin app to everyone."""
    client = request.client
    if client is None or not client.host:
        return None
    try:
        return ipaddress.ip_address(client.host)
    except ValueError:
        return None


def is_admin_client(request: Request) -> bool:
    """True when this request may see admin data. Usable as a dependency in
    its own right for endpoints that serve everybody but serve the admin
    more - see master_data.list_workers."""
    address = _peer_address(request)
    if address is None:
        return False
    if address.is_loopback:
        return True
    return any(address in network for network in _TAILNET_RANGES)


def require_admin_client(request: Request) -> None:
    """Admin-only endpoints depend on this. 403 rather than 401: there are no
    credentials to go and fetch, so "try again with a token" would be a lie -
    this request came in on the wrong network and always will."""
    if not is_admin_client(request):
        raise HTTPException(status.HTTP_403_FORBIDDEN, ADMIN_ONLY_MESSAGE)
