"""Cloudflare Access JWT verification — DroneOpsCommand OPERATOR SSO.

noc-master ADR-0246 decision 2 (fleet auth-posture standard): an app may
only have its local login removed once it verifies the Access JWT itself.
Access is an edge control — droneops.barnardhq.com's origin stays reachable
from the WireGuard mesh (BOS-HQ) — so trusting the
``Cf-Access-Jwt-Assertion`` header without verifying its signature would
make the origin unauthenticated to every container on the fleet. This
module is the verification; ``app/auth/jwt.py`` wires it into
``get_current_user`` as an additional accepted credential.

SCOPE: operator routes ONLY. The customer-facing client portal
(``app/auth/client_auth.py``), intake, and TOS-acceptance paths never
import this module and stay app-local permanently (ADR-0246 decision 5) —
those users are not in BarnardHQ's IdP and never will be.

PATTERN: ported from the marketing pilot (``~/marketing/api/cf-access.js``,
noc-master ADR-0246 roadmap Phase 2 reference implementation, marketing
ADR-0099) — same shape, same fail-closed contract, adapted from Node's
``jose`` (which ships a caching ``createRemoteJWKSet`` helper) to Python's
``python-jose`` (which does not — the TTL/cooldown cache below is our own,
matching the same parameters). The asymmetric-crypto verification itself is
never hand-rolled: ``jose.jwt.decode`` does the RS256 signature check, and
we pass an explicit ``algorithms=["RS256"]`` allow-list so a token
asserting ``alg: none`` or ``alg: HS256`` (the classic algorithm-confusion
downgrade) is rejected before any key is ever tried
(``jose.jws._verify_signature`` checks ``alg in algorithms`` first).

KEY SELECTION IS OURS TO DO (ADR-0047 Amendment 3): unlike Node's ``jose``,
``python-jose`` never reads the ``kid`` header — the string does not appear
anywhere in its verify path. Handing it a whole JWK Set routes into
``jose.jws._sig_matches_keys``, a bare ``for key in keys: if key.verify(...)``
loop, so a token carrying an UNKNOWN ``kid`` still verifies as long as some
key in the set matches its signature. ``_select_key`` below therefore picks
the single key by ``kid`` before verifying and denies when no entry matches.
This is defence in depth, not an authentication boundary: ``aud`` is already
pinned to this Access application, ``iss`` to the team domain, and an email
allow-list applies, so forging still requires a token Cloudflare signed for
this team with the right audience and an allow-listed email.

FAIL-CLOSED CONTRACT (explicit requirement, same as the marketing pilot):
every error path denies. Unlike TitanForge's stale-if-error JWKS cache
(which serves a stale-but-once-valid keyset on a refresh failure to avoid
locking out the operator UX), this verifier does NOT fall back to a stale
keyset once its TTL has expired — an unreachable JWKS endpoint denies, full
stop. A cached, still-fresh (within TTL) keyset IS reused across calls
(so we are not round-tripping to Cloudflare on every request), and a
verification failure against a fresh-but-possibly-rotated cache triggers
exactly one throttled forced refetch-and-retry (mirrors ``jose``'s
refetch-on-unknown-kid behaviour) — but if THAT refetch fails, or the
retry still fails, the request is denied. Never silently allowed.

OFF BY DEFAULT, STRUCTURALLY: verification requires BOTH
``cf_access_team_domain`` and ``cf_access_aud`` to be non-empty
(``app/config.py``). Self-hosted/OSS installs and the public demo instance
have no Cloudflare Access at all and never set these — ``is_cf_access_
configured()`` reads False, the CF-Access branch of ``get_current_user``
never runs, and ``httpx`` never makes a network call.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

import httpx
from jose import jwt
from jose.exceptions import ExpiredSignatureError, JWTClaimsError, JWTError

from app.config import settings

logger = logging.getLogger("doc.auth.cf_access")

# Header Cloudflare Access injects with the signed identity assertion on
# every request it proxies, once an Access application exists for the
# hostname — regardless of whether the browser also carries the
# `CF_Authorization` cookie. Starlette/FastAPI request headers are
# case-insensitive, so a single lower-case lookup key is sufficient.
CF_ACCESS_HEADER = "cf-access-jwt-assertion"

# Clock skew tolerance — matches the marketing pilot / TitanForge / EyesOn.
CLOCK_SKEW_SECONDS = 30

# JWKS in-memory TTL — how long a fetched key set is reused before its next
# scheduled refresh. Matches the marketing pilot's `jose` config exactly.
JWKS_TTL_SECONDS = 60 * 60  # 1 hour

# Minimum spacing between JWKS fetch ATTEMPTS (successful or not) — bounds
# refetch storms when many concurrent requests all see an expired/missing
# cache, or when Cloudflare has genuinely rotated a key and every
# in-flight request independently discovers its cached keyset no longer
# matches.
JWKS_COOLDOWN_SECONDS = 30

# Default operator allow-list when CF_ACCESS_ALLOWED_EMAILS is unset — the
# canonical operator identity used by every other Access app on the fleet
# (`allow email=bill@barnardhq.com`, noc-master ADR-0055).
DEFAULT_ALLOWED_EMAILS = ("bill@barnardhq.com",)

_JWKS_FETCH_TIMEOUT = httpx.Timeout(connect=5.0, read=5.0, write=5.0, pool=5.0)


@dataclass(frozen=True)
class CfAccessConfig:
    enabled: bool
    team_domain: str
    aud: str
    allowed_emails: frozenset[str]


@dataclass(frozen=True)
class CfAccessResult:
    ok: bool
    email: str = ""
    reason: str = ""


def get_cf_access_config() -> CfAccessConfig:
    """Read + validate config from ``app.config.settings``. Never raises."""
    team_domain = (settings.cf_access_team_domain or "").strip()
    aud = (settings.cf_access_aud or "").strip()
    allowed_raw = (settings.cf_access_allowed_emails or "").strip()
    allowed = (
        frozenset(e.strip().lower() for e in allowed_raw.split(",") if e.strip())
        if allowed_raw
        else frozenset(DEFAULT_ALLOWED_EMAILS)
    )
    return CfAccessConfig(
        enabled=bool(team_domain and aud),
        team_domain=team_domain,
        aud=aud,
        allowed_emails=allowed,
    )


def is_cf_access_configured() -> bool:
    """True only once the droneops.barnardhq.com Access app is wired in."""
    return get_cf_access_config().enabled


def extract_cf_access_token(request) -> str | None:
    """Pull the Access assertion out of a Starlette/FastAPI ``Request``."""
    value = request.headers.get(CF_ACCESS_HEADER)
    return value if value else None


# ── JWKS cache — module-level, one entry per team domain (in practice: one).
# Deliberately process-global (not per-request) so the TTL/cooldown actually
# bounds outbound calls to Cloudflare across concurrent requests.
@dataclass
class _JwksCacheEntry:
    keys: list[dict] = field(default_factory=list)
    fetched_at: float = 0.0  # monotonic seconds; 0.0 = never fetched


_jwks_cache: dict[str, _JwksCacheEntry] = {}
_last_fetch_attempt: dict[str, float] = {}
_fetch_locks: dict[str, asyncio.Lock] = {}


class JwksUnavailable(Exception):
    """Raised when a JWKS fetch is required and fails. Always denies."""


class MissingKid(JWTError):
    """Token header carries no ``kid``. A refetch can never make an absent
    ``kid`` match, so this denies immediately without spending a JWKS fetch
    attempt (and the cooldown that follows one)."""


class UnknownKid(JWTError):
    """Token's ``kid`` is absent from the JWKS we currently hold. This MAY
    be a Cloudflare key rotation we are lagging behind, so it is retryable:
    a plain ``JWTError`` subclass, it lands in the same
    refetch-once-and-retry path a signature failure already takes."""


def _get_lock(team_domain: str) -> asyncio.Lock:
    lock = _fetch_locks.get(team_domain)
    if lock is None:
        lock = asyncio.Lock()
        _fetch_locks[team_domain] = lock
    return lock


async def _fetch_jwks(team_domain: str, *, http_client: httpx.AsyncClient | None) -> list[dict]:
    url = f"https://{team_domain}/cdn-cgi/access/certs"
    owns_client = http_client is None
    client = http_client or httpx.AsyncClient(timeout=_JWKS_FETCH_TIMEOUT)
    try:
        resp = await client.get(url)
        resp.raise_for_status()
        data = resp.json()
    finally:
        if owns_client:
            await client.aclose()

    keys = data.get("keys") if isinstance(data, dict) else None
    if not isinstance(keys, list) or not keys:
        raise JwksUnavailable(f"empty or malformed JWKS response from {url}")
    return keys


async def _get_jwks(
    team_domain: str,
    *,
    force: bool = False,
    http_client: httpx.AsyncClient | None = None,
) -> list[dict]:
    """Return a (possibly cached) JWKS key list. Raises ``JwksUnavailable``
    on any fetch failure — including when a fresh fetch is required but the
    cooldown blocks it. Never returns a keyset past its TTL."""
    now = time.monotonic()
    entry = _jwks_cache.get(team_domain)
    if not force and entry is not None and (now - entry.fetched_at) < JWKS_TTL_SECONDS:
        return entry.keys

    last_attempt = _last_fetch_attempt.get(team_domain, 0.0)
    if (now - last_attempt) < JWKS_COOLDOWN_SECONDS:
        # Another caller already tried very recently (and presumably
        # failed, or we would have a fresh cache entry above). Do not
        # hammer Cloudflare — deny rather than serve anything stale.
        raise JwksUnavailable(
            f"jwks fetch cooldown active for {team_domain} — no fresh keys available"
        )

    async with _get_lock(team_domain):
        # Re-check after acquiring the lock: a concurrent caller may have
        # already refreshed the cache while we waited.
        now = time.monotonic()
        entry = _jwks_cache.get(team_domain)
        if not force and entry is not None and (now - entry.fetched_at) < JWKS_TTL_SECONDS:
            return entry.keys

        _last_fetch_attempt[team_domain] = time.monotonic()
        try:
            keys = await _fetch_jwks(team_domain, http_client=http_client)
        except JwksUnavailable:
            raise
        except Exception as exc:  # httpx errors, JSON decode errors, etc.
            raise JwksUnavailable(f"jwks_fetch_failed: {exc}") from exc

        _jwks_cache[team_domain] = _JwksCacheEntry(keys=keys, fetched_at=time.monotonic())
        return keys


def _reset_cf_access_cache_for_test() -> None:
    """Test-only: drop every cached JWKS + cooldown timer."""
    _jwks_cache.clear()
    _last_fetch_attempt.clear()
    _fetch_locks.clear()


def _select_key(token: str, keys: list[dict]) -> dict:
    """Return the ONE JWKS entry whose ``kid`` matches the token header.

    RFC 7515 s4.1.4: ``kid`` is a hint for *which* key to try, never a
    credential in itself — the RS256 signature check in ``_decode`` below is
    still what actually proves the token. Selecting on it is defence in
    depth against key confusion. Without it, handing ``python-jose`` a whole
    JWK Set (``{"keys": [...]}``) routes into ``jose.jws._sig_matches_keys``,
    which loops ``for key in keys: if key.verify(...): return True`` and so
    accepts a signature from ANY key in the set no matter which ``kid`` the
    token claims — ``kid`` appears nowhere in that library's verify path.

    The token's ``kid`` is attacker-controlled input, so it is used only as
    a dict lookup and never interpolated into a log line or a denial reason.
    """
    kid = jwt.get_unverified_header(token).get("kid")
    if not kid:
        raise MissingKid("token header carries no kid")
    for key in keys:
        if key.get("kid") == kid:
            return key
    raise UnknownKid("token kid is not present in the current JWKS")


def _decode(token: str, keys: list[dict], cfg: CfAccessConfig) -> dict:
    """Raises jose.exceptions.JWTError (or a subclass) on any failure."""
    return jwt.decode(
        token,
        _select_key(token, keys),
        algorithms=["RS256"],
        audience=cfg.aud,
        issuer=f"https://{cfg.team_domain}",
        options={
            "require_aud": True,
            "require_iss": True,
            "require_exp": True,
            "leeway": CLOCK_SKEW_SECONDS,
        },
    )


async def verify_cf_access_assertion(
    token: str | None,
    *,
    config: CfAccessConfig | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> CfAccessResult:
    """Verify a Cloudflare Access assertion. Returns ``CfAccessResult(ok=False,
    reason=...)`` for EVERY failure mode — not configured, no token,
    malformed token, bad signature, expired, wrong iss/aud, unreachable
    JWKS, missing ``email`` claim, or an email outside the allow list.
    Never raises: every internal error is caught and denied.
    """
    cfg = config or get_cf_access_config()
    if not cfg.enabled:
        return CfAccessResult(ok=False, reason="not_configured")
    if not token:
        return CfAccessResult(ok=False, reason="missing_token")

    try:
        keys = await _get_jwks(cfg.team_domain, http_client=http_client)
    except JwksUnavailable as exc:
        logger.warning("[CF-ACCESS] JWKS unavailable — denying: %s", exc)
        return CfAccessResult(ok=False, reason=f"jwks_unavailable: {exc}")
    except Exception as exc:  # defensive — never let this raise into a 500
        logger.warning("[CF-ACCESS] unexpected error fetching JWKS — denying: %s", exc)
        return CfAccessResult(ok=False, reason=f"jwks_unavailable: {exc}")

    payload: dict | None = None
    try:
        payload = _decode(token, keys, cfg)
    except ExpiredSignatureError:
        return CfAccessResult(ok=False, reason="token_expired")
    except JWTClaimsError as exc:
        return CfAccessResult(ok=False, reason=f"invalid_claims: {exc}")
    except MissingKid:
        # No `kid` at all. Cloudflare always sets one, and no refetch can
        # make an absent kid match — deny now rather than burn a forced
        # fetch attempt (and its 30s cooldown) on a request that cannot
        # succeed. Strictly NARROWER than the pre-fix behaviour, where such
        # a token failed the signature check and did force a refetch.
        return CfAccessResult(ok=False, reason="unknown_kid")
    except JWTError:
        # A genuinely bad signature, OR an unknown `kid`, OR Cloudflare
        # rotated its signing key since our cache was populated — the last
        # two are the SAME event seen from different angles, since a rotated
        # key arrives bearing a kid we have never seen. One throttled forced
        # refetch-and-retry (mirrors `jose`'s refetch-on-unknown-kid) —
        # if the refetch itself is unavailable or the retry still fails,
        # deny. Never falls back to treating this as a pass.
        try:
            keys = await _get_jwks(cfg.team_domain, force=True, http_client=http_client)
        except JwksUnavailable as exc:
            return CfAccessResult(ok=False, reason=f"verification_failed: {exc}")
        try:
            payload = _decode(token, keys, cfg)
        except ExpiredSignatureError:
            return CfAccessResult(ok=False, reason="token_expired")
        except JWTClaimsError as exc:
            return CfAccessResult(ok=False, reason=f"invalid_claims: {exc}")
        except (UnknownKid, MissingKid):
            # Refetched, and the kid is STILL absent from the live JWKS.
            # This is not a rotation we were lagging behind. Deny.
            return CfAccessResult(ok=False, reason="unknown_kid")
        except JWTError as exc:
            return CfAccessResult(ok=False, reason=f"verification_failed: {exc}")
    except Exception as exc:  # defensive — never let this raise into a 500
        return CfAccessResult(ok=False, reason=f"verification_failed: {exc}")

    email = str((payload or {}).get("email", "")).strip().lower()
    if not email:
        return CfAccessResult(ok=False, reason="no_email_claim")
    if email not in cfg.allowed_emails:
        return CfAccessResult(ok=False, reason="email_not_allowed")

    return CfAccessResult(ok=True, email=email)


def log_cf_access_startup_state() -> None:
    """One-line, secret-free startup log so an operator can confirm the
    Access config landed (or didn't) without grepping env vars. Called once
    from ``app/main.py``'s startup body. Never logs a token or AUD value."""
    cfg = get_cf_access_config()
    if cfg.enabled:
        logger.info(
            "[CF-ACCESS] SSO verification ENABLED — team=%s allowlist=%d email(s). "
            "Local password login remains active unless LOCAL_LOGIN_DISABLED=true.",
            cfg.team_domain,
            len(cfg.allowed_emails),
        )
    else:
        logger.info(
            "[CF-ACCESS] SSO verification not configured "
            "(CF_ACCESS_TEAM_DOMAIN/CF_ACCESS_AUD unset) — local login only."
        )
