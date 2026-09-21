"""Trusted-proxy-aware client IP resolution (Phase 7 hardening, 2026-09-21).

**The defect this closes:** every rate limiter and the login-lockout tracker
in this app keyed on ``request.client.host`` — the raw ASGI TCP peer. Because
nginx (the ``frontend`` container) always sits between the internet and
uvicorn, that peer is **nginx's own container address on every single
request**, never the real caller's. The practical effect: every "per-IP"
rate limit and the login lockout in ``auth.py`` were actually ONE GLOBAL
bucket shared by every visitor — a stranger's failed logins could lock the
operator out of their own instance, and one heavy customer could exhaust the
intake-form limit for every other customer.

The per-router ``_client_ip()`` helpers this module replaces (formerly in
``tos.py``, ``client_portal.py``, ``intake.py``) read the *first*
(leftmost) hop of ``X-Forwarded-For`` unconditionally — which is exactly
backwards and exploitable. nginx's ``proxy_set_header X-Forwarded-For
$proxy_add_x_forwarded_for;`` (frontend/nginx.conf) *appends* to whatever
the client sent, so a client can set its own ``X-Forwarded-For: 1.2.3.4``
and have it relayed as ``X-Forwarded-For: 1.2.3.4, <real address>`` — trusting
the leftmost value lets ANY caller pick their own logged/rate-limited
identity. This module trusts nothing the client can control: it decides
whether to honour ``X-Forwarded-For`` at all based on whether the *direct*
TCP peer is a known-trusted proxy, and when it does trust the header, it
takes the **rightmost hop that is not itself a trusted proxy** — the same
algorithm uvicorn's own ``ProxyHeadersMiddleware`` uses, and the only one
that is not defeated by a client-supplied prefix.

**Trust sources (either or both):**

1. **Dynamic** — the Docker Compose service name in ``TRUSTED_PROXY_HOSTNAME``
   (default ``"frontend"``, i.e. nginx) is resolved via Docker's embedded DNS
   at call time, cached for ``_RESOLVE_TTL_SECS``. This mirrors nginx's own
   ``resolver 127.0.0.11 valid=10s`` pattern in ``frontend/nginx.conf`` —
   chosen for the identical reason: a container recreate (redeploy) changes
   the peer's IP, and re-resolving on a short TTL means the app self-heals
   without an operator having to hardcode or update anything. No Docker
   Compose network topology change is required.
2. **Static** — ``FORWARDED_ALLOW_IPS`` (env var, comma-separated IPs/CIDRs)
   for operators running behind a different or additional proxy layer.
   Empty by default.

Loopback (``127.0.0.1``, ``::1``) is always trusted, matching uvicorn's own
default and covering bare-metal/healthcheck callers.

If neither source resolves the peer as trusted, ``X-Forwarded-For`` is
IGNORED ENTIRELY and the raw TCP peer is returned — fail CLOSED, never
fail open. This is the property the task's own constraint demands: "a wrong
trust chain lets a client spoof its source IP and defeat the limiter
entirely" — an unresolvable or misconfigured trust source must never
silently start trusting client-supplied headers.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
import time
from typing import Union

from fastapi import Request

from app.config import settings

logger = logging.getLogger("doc.client_ip")

_RESOLVE_TTL_SECS = 30.0

_AddressOrNetwork = Union[
    ipaddress.IPv4Address, ipaddress.IPv6Address, ipaddress.IPv4Network, ipaddress.IPv6Network
]

# {hostname: (resolved_at_monotonic, ip_or_None)}
_dns_cache: dict[str, tuple[float, str | None]] = {}


def _parse_trusted_literal(raw: str) -> list[_AddressOrNetwork]:
    """Parse a comma-separated IPs/CIDRs string. Unparseable entries are
    logged and skipped rather than raising — a typo in an env var must not
    crash the app at import time."""
    out: list[_AddressOrNetwork] = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            if "/" in item:
                out.append(ipaddress.ip_network(item, strict=False))
            else:
                out.append(ipaddress.ip_address(item))
        except ValueError:
            logger.warning("client_ip: ignoring unparseable FORWARDED_ALLOW_IPS entry %r", item)
    return out


_ALWAYS_TRUSTED = _parse_trusted_literal("127.0.0.1,::1")


def _resolve_proxy_ip(hostname: str) -> str | None:
    """`socket.gethostbyname` is a blocking syscall — acceptable here
    specifically because (a) it only runs at most once per
    `_RESOLVE_TTL_SECS` across every request, not per-request, and (b)
    Docker's embedded DNS resolver for a same-network container hostname
    is a local, sub-millisecond lookup, not a network round trip. Do not
    lower the TTL enough to make this a per-request cost."""
    if not hostname:
        return None
    now = time.monotonic()
    cached = _dns_cache.get(hostname)
    if cached is not None and (now - cached[0]) < _RESOLVE_TTL_SECS:
        return cached[1]
    try:
        ip = socket.gethostbyname(hostname)
    except OSError as exc:
        logger.warning("client_ip: could not resolve trusted proxy host %r: %s", hostname, exc)
        ip = None
    _dns_cache[hostname] = (now, ip)
    return ip


def _is_trusted(host: str | None, static_trusted: list[_AddressOrNetwork], dynamic_ip: str | None) -> bool:
    if not host:
        return False
    if dynamic_ip is not None and host == dynamic_ip:
        return True
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    for entry in _ALWAYS_TRUSTED:
        if isinstance(entry, (ipaddress.IPv4Address, ipaddress.IPv6Address)):
            if addr == entry:
                return True
        elif addr in entry:
            return True
    for entry in static_trusted:
        if isinstance(entry, (ipaddress.IPv4Address, ipaddress.IPv6Address)):
            if addr == entry:
                return True
        elif addr in entry:
            return True
    return False


def get_trusted_client_ip(request: Request) -> str:
    """Resolve the real client IP, honouring X-Forwarded-For ONLY from a
    trusted reverse proxy, and only ever the rightmost untrusted hop.

    This is the single source of truth for "what IP made this request" —
    used as the slowapi ``key_func`` for every rate limiter in the app and
    for every audit-log IP field. Do not reintroduce a local
    ``_client_ip()`` helper in a router; import this instead.
    """
    peer = request.client.host if request.client else None

    static_trusted = _parse_trusted_literal(settings.forwarded_allow_ips)
    dynamic_ip = _resolve_proxy_ip(settings.trusted_proxy_hostname)

    if not _is_trusted(peer, static_trusted, dynamic_ip):
        return peer or "0.0.0.0"

    fwd = request.headers.get("x-forwarded-for", "")
    if not fwd.strip():
        return peer or "0.0.0.0"

    chain = [h.strip() for h in fwd.split(",") if h.strip()]
    if not chain:
        return peer or "0.0.0.0"

    for host in reversed(chain):
        if not _is_trusted(host, static_trusted, dynamic_ip):
            return host

    # Every hop in the chain is itself a trusted proxy — degrade to the
    # leftmost entry, matching uvicorn's own ProxyHeadersMiddleware
    # behaviour for this edge case (see uvicorn/middleware/proxy_headers.py).
    return chain[0]
