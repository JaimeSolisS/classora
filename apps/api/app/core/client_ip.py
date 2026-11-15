"""The client's IP address, and ``X-Forwarded-For`` from trusted proxies only
(#27; ADR-0008 D15).

The client is the TCP peer of the connection (``request.scope["client"]``),
unless that peer is a trusted proxy (``Settings.trusted_proxies``, read from
``TRUSTED_PROXIES``, empty by default). Only then is ``X-Forwarded-For``
read, from the **right**: trusted entries are skipped, and the first
untrusted entry is the client (:func:`resolve_client_ip`).

Uvicorn runs with ``--no-proxy-headers`` (``infrastructure/docker/api.Dockerfile``),
so it never rewrites the peer from the header itself: this module is the only
place that reads ``X-Forwarded-For``.

Fallbacks never let a client-chosen string become a new key: a missing or
empty header, or a chain of trusted entries only, gives the left-most trusted
address seen; an untrusted entry that is not an IP address gives the trusted
hop to its right. A peer that is not an IP address (a Unix socket, Starlette's
``TestClient``, which reports ``"testclient"``) gives ``None``.

:func:`ip_key` turns the result into a rate-limit key: an IPv4 address as is,
an IPv6 address as its ``/64`` network (one IPv6 host usually controls a whole
``/64``), and ``None`` as :data:`UNKNOWN_IP_KEY`. The general API limit (#163)
reuses these functions.

Standard library only; imports nothing from ``app.modules`` (ADR-0002).
"""

import ipaddress
from collections.abc import Iterable, Sequence
from typing import Final

from fastapi import Request

from app.core.config import Settings

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network

FORWARDED_FOR_HEADER: Final = "x-forwarded-for"
UNKNOWN_IP_KEY: Final = "unknown"
"""The one key shared by every request whose client is not an IP address."""
IPV6_KEY_PREFIX: Final = 64


def parse_ip(value: str) -> IPAddress | None:
    """``value`` as an IP address (an IPv4-mapped IPv6 address as IPv4), or
    ``None`` when it is not exactly one address (ports, brackets, names)."""
    try:
        address = ipaddress.ip_address(value.strip())
    except ValueError:
        return None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped
    return address


def is_trusted(address: IPAddress, trusted: Sequence[IPNetwork]) -> bool:
    return any(
        address.version == network.version and address in network for network in trusted
    )


def resolve_client_ip(
    peer: str | None,
    forwarded_for: Iterable[str],
    trusted: Sequence[IPNetwork],
) -> IPAddress | None:
    """The client's address (ADR-0008 D15; see the module docstring).

    ``peer`` is the TCP peer's host, ``forwarded_for`` every
    ``X-Forwarded-For`` header line in order, ``trusted`` the trusted proxies.
    """
    if peer is None:
        return None
    peer_ip = parse_ip(peer)
    if peer_ip is None:
        return None
    if not is_trusted(peer_ip, trusted):
        return peer_ip
    entries = [entry.strip() for line in forwarded_for for entry in line.split(",")]
    # Walking right to left; ``hop`` is the left-most trusted address so far.
    hop = peer_ip
    for entry in reversed(entries):
        if not entry:
            continue
        address = parse_ip(entry)
        if address is None:
            return hop
        if not is_trusted(address, trusted):
            return address
        hop = address
    return hop


def client_ip(request: Request, settings: Settings) -> IPAddress | None:
    """:func:`resolve_client_ip` for ``request`` with the configured proxies."""
    client = request.scope.get("client")
    peer = client[0] if client else None
    return resolve_client_ip(
        peer,
        request.headers.getlist(FORWARDED_FOR_HEADER),
        settings.trusted_proxies,
    )


def ip_key(address: IPAddress | None) -> str:
    """The rate-limit key of ``address``: IPv4 as is, IPv6 by ``/64``."""
    if address is None:
        return UNKNOWN_IP_KEY
    if isinstance(address, ipaddress.IPv6Address):
        network = ipaddress.IPv6Network((address, IPV6_KEY_PREFIX), strict=False)
        return str(network)
    return str(address)
