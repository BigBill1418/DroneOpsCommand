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

**2026-09-21 correction** (same day as the original Phase 7 shipment): the
first version of this module only ever trusted ONE hostname. Live
verification against BOS-HQ (`docker logs droneops-frontend-1` +
`docker network inspect droneops_default`) found the real production chain
is TWO proxy hops (`cloudflared` in front of `frontend`/nginx), not one —
so the single-hostname default recognized nginx as the trusted peer but
then, walking X-Forwarded-For rightmost-first, stopped at `cloudflared`'s
own (constant) container IP, untrusted, and returned THAT as "the client"
for every internet request. This reproduced the exact one-shared-bucket
defect the module exists to close, just relocated one hop over — provably
NOT closed by the original test suite, because every test here modeled
only a single trusted hop.

The classes below add the two topologies this app actually runs behind,
modeled precisely enough to have caught the regression:

  * `TestTwoHopNginxCloudflaredChain` — the primary/demo topology
    (`docker-compose.yml`), now with BOTH hops in the trusted set.
  * `TestManagedCaddyDirectChain` — the managed-tenant topology
    (`droneops-managed/templates/Caddyfile.client`, outside this repo),
    where `caddy` — not `frontend` — is uvicorn's direct peer, and an
    unconfigured deployment must fail closed, not silently trust the wrong
    hop.
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


# ── Multi-hostname trust list — unit level ────────────────────────────────


def test_parse_trusted_hostnames_dedupes_and_drops_blanks():
    got = client_ip_module._parse_trusted_hostnames("frontend, cloudflared,,frontend ,")
    assert got == ["frontend", "cloudflared"]


def test_resolve_trusted_proxy_ips_resolves_each_hostname_independently(monkeypatch):
    """One hostname failing to resolve (e.g. `cloudflared` doesn't exist on
    a managed tenant's isolated network) must not block the others."""

    def fake_resolve(hostname):
        return {"frontend": "172.19.0.8", "cloudflared": "172.19.0.11"}.get(hostname)

    monkeypatch.setattr(client_ip_module, "_resolve_proxy_ip", fake_resolve)
    got = client_ip_module._resolve_trusted_proxy_ips("frontend,cloudflared,nonexistent")
    assert got == frozenset({"172.19.0.8", "172.19.0.11"})


def test_resolve_trusted_proxy_ips_all_unresolvable_is_empty_not_error(monkeypatch):
    monkeypatch.setattr(client_ip_module, "_resolve_proxy_ip", lambda hostname: None)
    assert client_ip_module._resolve_trusted_proxy_ips("frontend,cloudflared") == frozenset()


# ── Topology 1: primary/demo two-hop chain (cloudflared -> nginx -> uvicorn) ──
#
# Live-verified on BOS-HQ, 2026-09-21: droneops-frontend-1's own access log
# shows $remote_addr == droneops-cloudflared-1's container IP on every
# Cloudflare-tunnel-routed request, and nginx's
# `X-Forwarded-For $proxy_add_x_forwarded_for` appends that same IP onto
# whatever arrived — so the chain uvicorn receives is genuinely two hops
# deep. These tests model that exact shape rather than a single trusted hop.


class TestTwoHopNginxCloudflaredChain:
    NGINX_IP = "172.19.0.8"
    CLOUDFLARED_IP = "172.19.0.11"

    @pytest.fixture(autouse=True)
    def _trust_both_hops(self, monkeypatch):
        # Patches `_resolve_trusted_proxy_ips` directly (not `_resolve_proxy_ip`,
        # which the module-level `_no_dynamic_proxy` autouse fixture also
        # patches) so this class's trust set is deterministic regardless of
        # same-scope autouse fixture ordering.
        monkeypatch.setattr("app.config.settings.forwarded_allow_ips", "")
        monkeypatch.setattr(
            client_ip_module,
            "_resolve_trusted_proxy_ips",
            lambda hostnames_csv: frozenset({self.NGINX_IP, self.CLOUDFLARED_IP}),
        )

    def test_real_caller_resolved_through_both_trusted_hops(self):
        """The regression this correction closes: previously this returned
        the constant CLOUDFLARED_IP for every request, not the real caller."""
        req = _request(
            peer=self.NGINX_IP,
            headers={"x-forwarded-for": f"203.0.113.50, {self.CLOUDFLARED_IP}"},
        )
        assert get_trusted_client_ip(req) == "203.0.113.50"

    def test_spoofed_header_from_a_peer_outside_the_chain_is_ignored(self):
        """A caller that reaches uvicorn WITHOUT going through nginx (not
        possible over the real docker network, but the peer check must
        hold regardless) cannot claim to be anyone via X-Forwarded-For,
        even a chain shaped exactly like the legitimate one."""
        attacker_peer = "198.51.100.66"
        req = _request(
            peer=attacker_peer,
            headers={"x-forwarded-for": f"203.0.113.50, {self.CLOUDFLARED_IP}"},
        )
        assert get_trusted_client_ip(req) == attacker_peer

    def test_client_injected_leftmost_hop_through_both_trusted_proxies_is_not_trusted(self):
        """A client sends its own X-Forwarded-For; Cloudflare's edge appends
        the true connecting IP after it (never removing the client's own
        value), and nginx then appends cloudflared's own IP. The client's
        injected leftmost value must never win."""
        req = _request(
            peer=self.NGINX_IP,
            headers={"x-forwarded-for": f"9.9.9.9, 203.0.113.50, {self.CLOUDFLARED_IP}"},
        )
        assert get_trusted_client_ip(req) == "203.0.113.50"


# ── Topology 2: managed-tenant Caddy-direct chain ─────────────────────────
#
# droneops-managed/templates/Caddyfile.client (outside this repo, BOS-HQ)
# reverse-proxies /api/* straight from a tenant's own `caddy` sidecar to
# that tenant's `backend:8000` — bypassing that tenant's `frontend` (nginx)
# entirely. uvicorn's direct peer for a managed tenant is `caddy`, never
# `frontend`. A shared `droneops-managed-gateway` sits in front of each
# tenant's `caddy`, on a SEPARATE Docker network a tenant cannot resolve by
# hostname — so trusting that hop requires FORWARDED_ALLOW_IPS (a CIDR),
# not TRUSTED_PROXY_HOSTNAME.


class TestManagedCaddyDirectChain:
    CADDY_IP = "172.30.0.5"
    SHARED_GATEWAY_CIDR = "172.29.0.0/16"
    SHARED_GATEWAY_IP = "172.29.0.2"

    @pytest.fixture(autouse=True)
    def _managed_tenant_config(self, monkeypatch):
        # Per docs/managed-hosting.md: the required operator override
        # (TRUSTED_PROXY_HOSTNAME=caddy). Patches `_resolve_trusted_proxy_ips`
        # directly for the same determinism reason as the class above.
        monkeypatch.setattr("app.config.settings.trusted_proxy_hostname", "caddy")
        monkeypatch.setattr(
            client_ip_module,
            "_resolve_trusted_proxy_ips",
            lambda hostnames_csv: frozenset({self.CADDY_IP}) if hostnames_csv == "caddy" else frozenset(),
        )

    def test_caddy_configured_but_gateway_not_allowlisted_fails_closed_not_open(self, monkeypatch):
        """Operator set TRUSTED_PROXY_HOSTNAME=caddy but never set
        FORWARDED_ALLOW_IPS for the shared gateway's subnet. This is the
        documented, accepted gap (docs/managed-hosting.md) — it must
        degrade to the gateway's own (constant, per-tenant) IP, never
        silently trust an arbitrary client-supplied header."""
        monkeypatch.setattr("app.config.settings.forwarded_allow_ips", "")
        req = _request(
            peer=self.CADDY_IP,
            headers={"x-forwarded-for": f"203.0.113.50, {self.SHARED_GATEWAY_IP}"},
        )
        assert get_trusted_client_ip(req) == self.SHARED_GATEWAY_IP

    def test_gateway_subnet_allowlisted_resolves_real_caller(self, monkeypatch):
        """With FORWARDED_ALLOW_IPS covering the shared gateway's CIDR (the
        fully-configured, correct managed deployment), the real caller is
        resolved — not the gateway's IP, and not a client-injected hop."""
        monkeypatch.setattr("app.config.settings.forwarded_allow_ips", self.SHARED_GATEWAY_CIDR)
        req = _request(
            peer=self.CADDY_IP,
            headers={"x-forwarded-for": f"203.0.113.50, {self.SHARED_GATEWAY_IP}"},
        )
        assert get_trusted_client_ip(req) == "203.0.113.50"

    def test_spoofed_header_from_untrusted_peer_ignored_even_with_managed_config(self, monkeypatch):
        """A caller that is NOT this tenant's `caddy` cannot spoof its
        identity via X-Forwarded-For just because the deployment is a
        managed tenant with FORWARDED_ALLOW_IPS set."""
        monkeypatch.setattr("app.config.settings.forwarded_allow_ips", self.SHARED_GATEWAY_CIDR)
        attacker_peer = "198.51.100.9"
        req = _request(
            peer=attacker_peer,
            headers={"x-forwarded-for": f"203.0.113.50, {self.SHARED_GATEWAY_IP}"},
        )
        assert get_trusted_client_ip(req) == attacker_peer

    def test_client_injected_leftmost_hop_not_trusted_in_managed_topology(self, monkeypatch):
        monkeypatch.setattr("app.config.settings.forwarded_allow_ips", self.SHARED_GATEWAY_CIDR)
        req = _request(
            peer=self.CADDY_IP,
            headers={"x-forwarded-for": f"9.9.9.9, 203.0.113.50, {self.SHARED_GATEWAY_IP}"},
        )
        assert get_trusted_client_ip(req) == "203.0.113.50"

    def test_unconfigured_managed_tenant_default_hostname_never_matches_caddy(self, monkeypatch):
        """Sanity check for the documented hazard itself: the PRIMARY
        topology's default (`frontend,cloudflared`) must not accidentally
        resolve or match a managed tenant's `caddy` peer — proving the two
        topologies genuinely require different configuration, not just
        different test setup."""
        monkeypatch.setattr("app.config.settings.trusted_proxy_hostname", "frontend,cloudflared")
        monkeypatch.setattr("app.config.settings.forwarded_allow_ips", "")
        req = _request(peer=self.CADDY_IP, headers={"x-forwarded-for": "203.0.113.50"})
        assert get_trusted_client_ip(req) == self.CADDY_IP
