"""Client-IP resolution, ``TRUSTED_PROXIES`` and the rate-limit IP key (#27 C2,
C3, C5)."""

import ipaddress
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.core.client_ip import client_ip, ip_key, resolve_client_ip
from app.core.config import Settings
from tests.authz.csrf import TEST_APP_ORIGIN

PROXY = "10.0.0.2"
CADDY_NET = "10.0.0.0/24"


def nets(*values: str) -> list[Any]:
    return [ipaddress.ip_network(v) for v in values]


def ip(value: str) -> Any:
    return ipaddress.ip_address(value)


TRUSTED = nets(CADDY_NET, "fd00::/64")


@pytest.mark.parametrize(
    ("peer", "headers", "trusted", "expected"),
    [
        # Untrusted peer: X-Forwarded-For is ignored completely.
        ("203.0.113.7", ["198.51.100.1"], [], "203.0.113.7"),
        ("203.0.113.7", ["198.51.100.1"], TRUSTED, "203.0.113.7"),
        ("203.0.113.7", ["garbage"], TRUSTED, "203.0.113.7"),
        # Trusted peer with one entry.
        (PROXY, ["198.51.100.1"], TRUSTED, "198.51.100.1"),
        # Spoofed left-most entries never win: the right-most untrusted does.
        (PROXY, ["1.1.1.1, 198.51.100.1"], TRUSTED, "198.51.100.1"),
        # A chain of trusted hops is skipped from the right.
        (PROXY, ["198.51.100.1, 10.0.0.9, 10.0.0.8"], TRUSTED, "198.51.100.1"),
        # Several header lines are joined in order.
        (PROXY, ["1.1.1.1", "198.51.100.1, 10.0.0.9"], TRUSTED, "198.51.100.1"),
        (PROXY, ["198.51.100.1", "10.0.0.9"], TRUSTED, "198.51.100.1"),
        # Spaces and empty entries.
        (PROXY, [" 198.51.100.1 ,, "], TRUSTED, "198.51.100.1"),
        # IPv6 client behind a trusted proxy; IPv6 trusted proxy.
        (PROXY, ["2001:db8::5"], TRUSTED, "2001:db8::5"),
        ("fd00::1", ["198.51.100.1"], TRUSTED, "198.51.100.1"),
        # IPv4-mapped IPv6 peer and entries count as IPv4.
        ("::ffff:10.0.0.2", ["198.51.100.1"], TRUSTED, "198.51.100.1"),
        ("::ffff:203.0.113.7", ["198.51.100.1"], TRUSTED, "203.0.113.7"),
        (PROXY, ["::ffff:198.51.100.1"], TRUSTED, "198.51.100.1"),
        # CIDR membership: 10.0.1.2 is outside 10.0.0.0/24.
        ("10.0.1.2", ["198.51.100.1"], TRUSTED, "10.0.1.2"),
        (PROXY, ["198.51.100.1, 10.0.1.2"], TRUSTED, "10.0.1.2"),
    ],
)
def test_resolution(peer, headers, trusted, expected):
    assert resolve_client_ip(peer, headers, trusted) == ip(expected)


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        # Missing or empty header: the peer.
        ([], PROXY),
        ([""], PROXY),
        ([" , "], PROXY),
        # Every entry trusted: the left-most trusted address.
        (["10.0.0.7, 10.0.0.9"], "10.0.0.7"),
        # An untrusted entry that is not an IP: the trusted hop to its right.
        (["198.51.100.1, garbage, 10.0.0.9"], "10.0.0.9"),
        (["garbage"], PROXY),
        (["198.51.100.1:443"], PROXY),
        (["[2001:db8::5]"], PROXY),
        (["unknown, 10.0.0.9"], "10.0.0.9"),
    ],
)
def test_fallbacks_never_use_a_client_chosen_string(headers, expected):
    assert resolve_client_ip(PROXY, headers, TRUSTED) == ip(expected)


@pytest.mark.parametrize("peer", [None, "testclient", "/run/api.sock", ""])
def test_a_non_ip_peer_gives_none(peer):
    assert resolve_client_ip(peer, ["198.51.100.1"], nets("0.0.0.0/1")) is None


@pytest.mark.parametrize(
    ("address", "key"),
    [
        (None, "unknown"),
        ("203.0.113.7", "203.0.113.7"),
        ("2001:db8:1:2:3:4:5:6", "2001:db8:1:2::/64"),
        ("2001:db8:1:2:ffff::1", "2001:db8:1:2::/64"),
        ("2001:db8:1:3::1", "2001:db8:1:3::/64"),
    ],
)
def test_ip_keys(address, key):
    assert ip_key(ip(address) if address else None) == key


def settings(trusted: str = "") -> Settings:
    return Settings(
        app_env="local",
        app_origin=TEST_APP_ORIGIN,
        database_url="postgresql://u:p@127.0.0.1:1/db",  # type: ignore[arg-type]
        database_direct_url="postgresql://u:p@127.0.0.1:1/db",  # type: ignore[arg-type]
        trusted_proxies=trusted,  # type: ignore[arg-type]
    )


def echo_app(configured: Settings) -> FastAPI:
    app = FastAPI()

    @app.get("/ip")
    async def show(request: Request) -> dict[str, str]:
        return {"key": ip_key(client_ip(request, configured))}

    return app


def test_client_ip_reads_the_scope_peer_and_every_header_line():
    with TestClient(echo_app(settings()), client=("203.0.113.7", 5000)) as client:
        assert client.get("/ip", headers={"X-Forwarded-For": "1.1.1.1"}).json() == {
            "key": "203.0.113.7"
        }
    with TestClient(
        echo_app(settings("203.0.113.7")), client=("203.0.113.7", 5000)
    ) as client:
        response = client.get(
            "/ip",
            headers=[("X-Forwarded-For", "1.1.1.1"), ("X-Forwarded-For", "2.2.2.2")],
        )
        assert response.json() == {"key": "2.2.2.2"}


def test_the_test_client_shares_the_unknown_key():
    with TestClient(echo_app(settings())) as client:
        assert client.get("/ip").json() == {"key": "unknown"}
