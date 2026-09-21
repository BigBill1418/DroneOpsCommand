"""Trusted-proxy-aware client IP resolution (Phase 7 hardening, ADR-0045).

Covers the defect at `main.py:587` (pre-fix): every rate limiter and the
login lockout keyed on `request.client.host`, which is nginx's own
container IP on every request — collapsing every caller into one shared
bucket. `app/utils/client_ip.get_trusted_client_ip` replaces that.

Two properties are load-bearing and each gets a positive AND a negative
case, per this repo's own testing discipline (an absence assertion with no
control that can fail is not a real guard):

  1. X-Forwarded-For is honoured ONLY when the direct TCP peer is a
     trusted proxy — an untrusted peer's spoofed header must be ignored
     (test_spoofed_header_from_untrusted_peer_is_ignored).
  2. When trusted, the RIGHTMOST non-trusted hop is used, never the
     leftmost — a client-injected leftmost value must not be trusted even
     when relayed through a genuine trusted proxy
     (test_trusted_peer_client_injected_leftmost_hop_is_not_trusted).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.utils import client_ip as client_ip_module
from app.utils.client_ip import get_trusted_client_ip


def _request(peer: str | None, headers: dict[str, str] | None = None):
    client = SimpleNamespace(host=peer) if peer is not None else None
    return SimpleNamespace(client=client, headers=headers or {})


@pytest.fixture(autouse=True)
def _clear_dns_cache():
    """Every test controls trust explicitly via settings/patching — a
    cached resolution from a previous test must never leak in."""
    client_ip_module._dns_cache.clear()
    yield
    client_ip_module._dns_cache.clear()


@pytest.fixture(autouse=True)
def _no_dynamic_proxy(request, monkeypatch):
    """Default: dynamic hostname resolution off (returns None), by
    monkeypatching `_resolve_proxy_ip` itself. Tests that exercise
    `_resolve_proxy_ip` directly (the real DNS-cache behaviour) opt out via
    `@pytest.mark.real_dns_resolution` — otherwise this fixture's patch
    IS what they'd be calling, never reaching `socket.gethostbyname`."""
    if request.node.get_closest_marker("real_dns_resolution"):
        yield
        return
    monkeypatch.setattr(client_ip_module, "_resolve_proxy_ip", lambda hostname: None)
    yield


# ── Untrusted peer: X-Forwarded-For must be ignored entirely ─────────────


def test_untrusted_peer_ignores_x_forwarded_for(monkeypatch):
    """The core anti-spoofing property. A caller hitting the app directly
    (not through the configured trusted proxy) gets its OWN peer address
    back, never whatever it put in X-Forwarded-For."""
    monkeypatch.setattr("app.config.settings.forwarded_allow_ips", "")
    req = _request(peer="203.0.113.50", headers={"x-forwarded-for": "10.0.0.1"})
    assert get_trusted_client_ip(req) == "203.0.113.50"


def test_spoofed_header_from_untrusted_peer_is_ignored(monkeypatch):
    """Direct proof requested by the task: a client that is NOT the
    trusted proxy cannot claim to be some other IP via X-Forwarded-For,
    even a plausible multi-hop chain."""
    monkeypatch.setattr("app.config.settings.forwarded_allow_ips", "203.0.113.9")
    attacker_peer = "198.51.100.7"  # NOT in forwarded_allow_ips
    req = _request(
        peer=attacker_peer,
        headers={"x-forwarded-for": "1.2.3.4, 5.6.7.8, 9.9.9.9"},
    )
    assert get_trusted_client_ip(req) == attacker_peer


# ── Trusted peer: rightmost untrusted hop wins ────────────────────────────


def test_trusted_peer_single_hop_is_used(monkeypatch):
    monkeypatch.setattr("app.config.settings.forwarded_allow_ips", "203.0.113.9")
    req = _request(peer="203.0.113.9", headers={"x-forwarded-for": "198.51.100.20"})
    assert get_trusted_client_ip(req) == "198.51.100.20"


def test_trusted_peer_client_injected_leftmost_hop_is_not_trusted(monkeypatch):
    """nginx's `X-Forwarded-For: $proxy_add_x_forwarded_for` APPENDS to
    whatever the client sent, so the client's own injected value is always
    LEFT of the proxy's real observation. Trusting the leftmost hop (the
    old per-router `_client_ip()` helpers' bug) lets any client pick its
    own logged/rate-limited identity. The rightmost non-trusted hop is the
    one nginx itself appended."""
    monkeypatch.setattr("app.config.settings.forwarded_allow_ips", "203.0.113.9")
    req = _request(
        peer="203.0.113.9",  # the trusted proxy
        headers={"x-forwarded-for": "9.9.9.9, 198.51.100.7"},  # client injected 9.9.9.9
    )
    assert get_trusted_client_ip(req) == "198.51.100.7"


def test_trusted_peer_all_hops_trusted_degrades_to_leftmost(monkeypatch):
    monkeypatch.setattr("app.config.settings.forwarded_allow_ips", "203.0.113.9,203.0.113.10")
    req = _request(
        peer="203.0.113.9",
        headers={"x-forwarded-for": "203.0.113.10, 203.0.113.9"},
    )
    assert get_trusted_client_ip(req) == "203.0.113.10"


# ── Loopback is always trusted (matches uvicorn's own default) ───────────


def test_loopback_peer_is_trusted_by_default(monkeypatch):
    monkeypatch.setattr("app.config.settings.forwarded_allow_ips", "")
    req = _request(peer="127.0.0.1", headers={"x-forwarded-for": "198.51.100.7"})
    assert get_trusted_client_ip(req) == "198.51.100.7"


# ── No X-Forwarded-For header at all ──────────────────────────────────────


def test_trusted_peer_no_header_returns_peer(monkeypatch):
    monkeypatch.setattr("app.config.settings.forwarded_allow_ips", "203.0.113.9")
    req = _request(peer="203.0.113.9", headers={})
    assert get_trusted_client_ip(req) == "203.0.113.9"


def test_no_client_on_request_falls_back_to_placeholder(monkeypatch):
    monkeypatch.setattr("app.config.settings.forwarded_allow_ips", "")
    req = _request(peer=None)
    assert get_trusted_client_ip(req) == "0.0.0.0"


# ── Dynamic (DNS-resolved) trust source ───────────────────────────────────


def test_dynamic_proxy_resolution_is_trusted(monkeypatch):
    """The default trust source (settings.trusted_proxy_hostname == 'frontend'
    resolved via Docker embedded DNS) — no FORWARDED_ALLOW_IPS needed."""
    monkeypatch.setattr("app.config.settings.forwarded_allow_ips", "")
    monkeypatch.setattr(client_ip_module, "_resolve_proxy_ip", lambda hostname: "172.19.0.5")
    req = _request(peer="172.19.0.5", headers={"x-forwarded-for": "198.51.100.7"})
    assert get_trusted_client_ip(req) == "198.51.100.7"


def test_dynamic_resolution_failure_fails_closed(monkeypatch):
    """If the trusted-proxy hostname cannot be resolved (e.g. frontend not
    up yet during a rolling restart), nothing is trusted — never fail open
    into trusting an arbitrary peer's X-Forwarded-For."""
    monkeypatch.setattr("app.config.settings.forwarded_allow_ips", "")
    monkeypatch.setattr(client_ip_module, "_resolve_proxy_ip", lambda hostname: None)
    req = _request(peer="172.19.0.5", headers={"x-forwarded-for": "198.51.100.7"})
    assert get_trusted_client_ip(req) == "172.19.0.5"


@pytest.mark.real_dns_resolution
def test_dns_resolution_is_cached_within_ttl():
    """Mirrors nginx's own `resolver 127.0.0.11 valid=10s` rationale
    (frontend/nginx.conf): re-resolve on a short TTL, not per-request."""
    calls = []

    def fake_gethostbyname(hostname):
        calls.append(hostname)
        return "172.19.0.5"

    with patch.object(client_ip_module.socket, "gethostbyname", side_effect=fake_gethostbyname):
        client_ip_module._resolve_proxy_ip("frontend")
        client_ip_module._resolve_proxy_ip("frontend")
    assert len(calls) == 1, "second call within TTL must hit the cache, not DNS"


def test_dns_resolution_unparseable_static_entry_is_skipped(monkeypatch, caplog):
    """A typo in FORWARDED_ALLOW_IPS must not crash request handling."""
    monkeypatch.setattr("app.config.settings.forwarded_allow_ips", "not-an-ip, 203.0.113.9")
    req = _request(peer="203.0.113.9", headers={"x-forwarded-for": "198.51.100.7"})
    assert get_trusted_client_ip(req) == "198.51.100.7"
