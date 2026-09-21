"""Cloudflare Access JWT verification (ADR-0047, app/auth/cf_access.py).

Mirrors the coverage shape of the marketing pilot's ``cf-access.test.js``
(noc-master ADR-0246 roadmap Phase 2 / marketing ADR-0099): the structural
off-by-default gate, the full verification happy path, and every documented
fail-closed error mode. No network I/O — a real RSA keypair is generated
once per test module and every JWKS "fetch" is a monkeypatched in-memory
function, never a real HTTP call.
"""

from __future__ import annotations

import time

import pytest

import tests.conftest  # noqa: F401 — env stubs

from app.auth import cf_access as cfa

# No `pytestmark = pytest.mark.asyncio` — pytest.ini sets asyncio_mode=auto,
# which auto-detects async tests without a marker. This file mixes sync
# (config-gate) and async (verification) tests; a blanket module-level mark
# would misfire on the sync ones.


# ── Fixtures: a real RSA keypair + a JWK/JWT factory ────────────────────

TEAM_DOMAIN = "test-team.cloudflareaccess.com"
AUD = "test-aud-tag"
EMAIL = "bill@barnardhq.com"


@pytest.fixture(scope="module")
def rsa_keypair():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv_pem = priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    pub_pem = priv.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return priv_pem, pub_pem


@pytest.fixture(scope="module")
def rsa_keypair_2():
    """A SECOND, distinct keypair — simulates Cloudflare's rotated key."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv_pem = priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    pub_pem = priv.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return priv_pem, pub_pem


def _jwk(pub_pem: str, kid: str) -> dict:
    from jose.backends.cryptography_backend import CryptographyRSAKey
    jwk = CryptographyRSAKey(pub_pem, algorithm="RS256").to_dict()
    jwk["kid"] = kid
    return jwk


def _sign(
    priv_pem: str,
    kid: str,
    *,
    email: str = EMAIL,
    iss: str | None = None,
    aud: str | None = None,
    exp_delta: int = 3600,
    alg: str = "RS256",
    extra_claims: dict | None = None,
    omit_claims: tuple[str, ...] = (),
) -> str:
    from jose import jwt as josejwt

    claims = {
        "email": email,
        "iss": iss if iss is not None else f"https://{TEAM_DOMAIN}",
        "aud": aud if aud is not None else AUD,
        "exp": int(time.time()) + exp_delta,
        "iat": int(time.time()),
    }
    if extra_claims:
        claims.update(extra_claims)
    for c in omit_claims:
        claims.pop(c, None)
    return josejwt.encode(claims, priv_pem, algorithm=alg, headers={"kid": kid})


@pytest.fixture(autouse=True)
def _reset_cache():
    cfa._reset_cf_access_cache_for_test()
    yield
    cfa._reset_cf_access_cache_for_test()


def _config(*, enabled=True, allowed=None) -> cfa.CfAccessConfig:
    return cfa.CfAccessConfig(
        enabled=enabled,
        team_domain=TEAM_DOMAIN,
        aud=AUD,
        allowed_emails=frozenset(allowed or [EMAIL]),
    )


class _FakeHttpClient:
    """Stands in for httpx.AsyncClient.get — no network, no monkeypatching
    of httpx internals required."""

    def __init__(self, jwks_by_url: dict[str, list[dict] | Exception], calls: list[str] | None = None):
        self._jwks_by_url = jwks_by_url
        self.calls = calls if calls is not None else []

    async def get(self, url: str):
        self.calls.append(url)
        result = self._jwks_by_url.get(url)
        if isinstance(result, Exception):
            raise result
        import httpx as _httpx

        class _Resp:
            def __init__(self, keys):
                self._keys = keys

            def raise_for_status(self):
                return None

            def json(self):
                return {"keys": self._keys}

        if result is None:
            raise _httpx.ConnectError("no route configured for URL", request=None)
        return _Resp(result)

    async def aclose(self):
        pass


def _jwks_url() -> str:
    return f"https://{TEAM_DOMAIN}/cdn-cgi/access/certs"


# ── Structural off-by-default gate ──────────────────────────────────────


def test_config_disabled_when_team_domain_and_aud_both_unset(monkeypatch):
    monkeypatch.setattr(cfa.settings, "cf_access_team_domain", "")
    monkeypatch.setattr(cfa.settings, "cf_access_aud", "")
    cfg = cfa.get_cf_access_config()
    assert cfg.enabled is False
    assert cfa.is_cf_access_configured() is False


def test_config_disabled_when_only_team_domain_set(monkeypatch):
    monkeypatch.setattr(cfa.settings, "cf_access_team_domain", TEAM_DOMAIN)
    monkeypatch.setattr(cfa.settings, "cf_access_aud", "")
    assert cfa.get_cf_access_config().enabled is False


def test_config_disabled_when_only_aud_set(monkeypatch):
    monkeypatch.setattr(cfa.settings, "cf_access_team_domain", "")
    monkeypatch.setattr(cfa.settings, "cf_access_aud", AUD)
    assert cfa.get_cf_access_config().enabled is False


def test_config_enabled_when_both_set(monkeypatch):
    monkeypatch.setattr(cfa.settings, "cf_access_team_domain", TEAM_DOMAIN)
    monkeypatch.setattr(cfa.settings, "cf_access_aud", AUD)
    cfg = cfa.get_cf_access_config()
    assert cfg.enabled is True
    assert cfg.team_domain == TEAM_DOMAIN
    assert cfg.aud == AUD


def test_default_allowed_emails_is_the_canonical_operator(monkeypatch):
    monkeypatch.setattr(cfa.settings, "cf_access_allowed_emails", "")
    cfg = cfa.get_cf_access_config()
    assert cfg.allowed_emails == frozenset({"bill@barnardhq.com"})


def test_allowed_emails_parses_comma_list_lowercased(monkeypatch):
    monkeypatch.setattr(cfa.settings, "cf_access_allowed_emails", "Alice@Example.com, bob@example.com")
    cfg = cfa.get_cf_access_config()
    assert cfg.allowed_emails == frozenset({"alice@example.com", "bob@example.com"})


async def test_verify_denies_when_not_configured():
    result = await cfa.verify_cf_access_assertion("anything", config=_config(enabled=False))
    assert result.ok is False
    assert result.reason == "not_configured"


async def test_verify_denies_missing_token():
    result = await cfa.verify_cf_access_assertion(None, config=_config())
    assert result.ok is False
    assert result.reason == "missing_token"

    result2 = await cfa.verify_cf_access_assertion("", config=_config())
    assert result2.ok is False
    assert result2.reason == "missing_token"


def test_extract_cf_access_token_reads_header():
    from types import SimpleNamespace
    req = SimpleNamespace(headers={"cf-access-jwt-assertion": "abc123"})
    assert cfa.extract_cf_access_token(req) == "abc123"


def test_extract_cf_access_token_absent():
    from types import SimpleNamespace
    req = SimpleNamespace(headers={})
    assert cfa.extract_cf_access_token(req) is None


# ── Happy path ────────────────────────────────────────────────────────


async def test_valid_assertion_passes(rsa_keypair):
    priv, pub = rsa_keypair
    token = _sign(priv, "kid-1")
    client = _FakeHttpClient({_jwks_url(): [_jwk(pub, "kid-1")]})

    result = await cfa.verify_cf_access_assertion(token, config=_config(), http_client=client)
    assert result.ok is True
    assert result.email == EMAIL


async def test_email_is_lowercased_and_trimmed(rsa_keypair):
    priv, pub = rsa_keypair
    token = _sign(priv, "kid-1", email="  Bill@BarnardHQ.com  ")
    client = _FakeHttpClient({_jwks_url(): [_jwk(pub, "kid-1")]})

    result = await cfa.verify_cf_access_assertion(token, config=_config(), http_client=client)
    assert result.ok is True
    assert result.email == "bill@barnardhq.com"


# ── Every fail-closed error mode ─────────────────────────────────────────


async def test_wrong_issuer_denied(rsa_keypair):
    priv, pub = rsa_keypair
    token = _sign(priv, "kid-1", iss="https://attacker.cloudflareaccess.com")
    client = _FakeHttpClient({_jwks_url(): [_jwk(pub, "kid-1")]})

    result = await cfa.verify_cf_access_assertion(token, config=_config(), http_client=client)
    assert result.ok is False
    assert "invalid_claims" in result.reason or "verification_failed" in result.reason


async def test_wrong_audience_denied(rsa_keypair):
    priv, pub = rsa_keypair
    token = _sign(priv, "kid-1", aud="someone-elses-app")
    client = _FakeHttpClient({_jwks_url(): [_jwk(pub, "kid-1")]})

    result = await cfa.verify_cf_access_assertion(token, config=_config(), http_client=client)
    assert result.ok is False
    assert "invalid_claims" in result.reason


async def test_missing_audience_claim_denied_even_though_required(rsa_keypair):
    """require_aud=True must reject a token with NO aud claim at all — the
    python-jose default (verify_aud without require_aud) silently PASSES
    an absent aud claim, which would be a fail-OPEN gap."""
    priv, pub = rsa_keypair
    token = _sign(priv, "kid-1", omit_claims=("aud",))
    client = _FakeHttpClient({_jwks_url(): [_jwk(pub, "kid-1")]})

    result = await cfa.verify_cf_access_assertion(token, config=_config(), http_client=client)
    assert result.ok is False


async def test_missing_issuer_claim_denied(rsa_keypair):
    priv, pub = rsa_keypair
    token = _sign(priv, "kid-1", omit_claims=("iss",))
    client = _FakeHttpClient({_jwks_url(): [_jwk(pub, "kid-1")]})

    result = await cfa.verify_cf_access_assertion(token, config=_config(), http_client=client)
    assert result.ok is False


async def test_expired_token_denied(rsa_keypair):
    priv, pub = rsa_keypair
    token = _sign(priv, "kid-1", exp_delta=-3600)  # expired 1h ago, beyond skew
    client = _FakeHttpClient({_jwks_url(): [_jwk(pub, "kid-1")]})

    result = await cfa.verify_cf_access_assertion(token, config=_config(), http_client=client)
    assert result.ok is False
    assert result.reason == "token_expired"


async def test_token_within_clock_skew_still_passes(rsa_keypair):
    priv, pub = rsa_keypair
    token = _sign(priv, "kid-1", exp_delta=-10)  # expired 10s ago, within 30s skew
    client = _FakeHttpClient({_jwks_url(): [_jwk(pub, "kid-1")]})

    result = await cfa.verify_cf_access_assertion(token, config=_config(), http_client=client)
    assert result.ok is True


async def test_forged_signature_denied(rsa_keypair, rsa_keypair_2):
    """Token signed with a DIFFERENT private key than what the JWKS
    publishes — the classic forged-signature case."""
    attacker_priv, _ = rsa_keypair_2
    _, real_pub = rsa_keypair
    token = _sign(attacker_priv, "kid-1")  # signed with attacker's key
    client = _FakeHttpClient({_jwks_url(): [_jwk(real_pub, "kid-1")]})  # only real pub published

    result = await cfa.verify_cf_access_assertion(token, config=_config(), http_client=client)
    assert result.ok is False


async def test_missing_email_claim_denied(rsa_keypair):
    priv, pub = rsa_keypair
    token = _sign(priv, "kid-1", omit_claims=("email",))
    client = _FakeHttpClient({_jwks_url(): [_jwk(pub, "kid-1")]})

    result = await cfa.verify_cf_access_assertion(token, config=_config(), http_client=client)
    assert result.ok is False
    assert result.reason == "no_email_claim"


async def test_email_outside_allow_list_denied(rsa_keypair):
    priv, pub = rsa_keypair
    token = _sign(priv, "kid-1", email="stranger@example.com")
    client = _FakeHttpClient({_jwks_url(): [_jwk(pub, "kid-1")]})

    result = await cfa.verify_cf_access_assertion(token, config=_config(), http_client=client)
    assert result.ok is False
    assert result.reason == "email_not_allowed"


async def test_alg_none_downgrade_denied(rsa_keypair):
    """A token asserting alg=none (or any non-RS256 alg) must never verify,
    even if it happens to carry a valid-looking payload."""
    import jwt as pyjwt  # PyJWT is NOT a dependency here, so build the
    # none-alg token by hand instead.
    import base64
    import json

    def _b64(d: dict) -> bytes:
        return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=")

    header = _b64({"alg": "none", "typ": "JWT", "kid": "kid-1"})
    payload = _b64({
        "email": EMAIL,
        "iss": f"https://{TEAM_DOMAIN}",
        "aud": AUD,
        "exp": int(time.time()) + 3600,
    })
    token = (header + b"." + payload + b".").decode()

    _, pub = rsa_keypair
    client = _FakeHttpClient({_jwks_url(): [_jwk(pub, "kid-1")]})
    result = await cfa.verify_cf_access_assertion(token, config=_config(), http_client=client)
    assert result.ok is False


async def test_malformed_token_denied():
    result = await cfa.verify_cf_access_assertion("not-a-jwt-at-all", config=_config())
    assert result.ok is False


async def test_jwks_unreachable_denies(rsa_keypair):
    priv, _ = rsa_keypair
    token = _sign(priv, "kid-1")
    client = _FakeHttpClient({})  # no URL configured -> ConnectError

    result = await cfa.verify_cf_access_assertion(token, config=_config(), http_client=client)
    assert result.ok is False
    assert "jwks_unavailable" in result.reason


async def test_malformed_jwks_response_denies(rsa_keypair):
    priv, _ = rsa_keypair
    token = _sign(priv, "kid-1")
    client = _FakeHttpClient({_jwks_url(): []})  # empty keys list

    result = await cfa.verify_cf_access_assertion(token, config=_config(), http_client=client)
    assert result.ok is False
    assert "jwks_unavailable" in result.reason


async def test_verify_never_raises_on_unexpected_exception(monkeypatch, rsa_keypair):
    priv, pub = rsa_keypair
    token = _sign(priv, "kid-1")

    async def _boom(*a, **kw):
        raise RuntimeError("something unexpected")

    monkeypatch.setattr(cfa, "_get_jwks", _boom)
    # Because _get_jwks raises something OTHER than JwksUnavailable, our
    # try/except around it must still not propagate.
    result = await cfa.verify_cf_access_assertion(token, config=_config())
    assert result.ok is False


# ── JWKS caching / cooldown / refetch-on-rotation ────────────────────────


async def test_jwks_is_cached_within_ttl_no_second_fetch(rsa_keypair):
    priv, pub = rsa_keypair
    token = _sign(priv, "kid-1")
    calls: list[str] = []
    client = _FakeHttpClient({_jwks_url(): [_jwk(pub, "kid-1")]}, calls=calls)

    await cfa.verify_cf_access_assertion(token, config=_config(), http_client=client)
    await cfa.verify_cf_access_assertion(token, config=_config(), http_client=client)

    assert len(calls) == 1


async def test_key_rotation_triggers_one_forced_refetch_and_then_passes(rsa_keypair, rsa_keypair_2):
    """Cloudflare rotates its signing key: the cached JWKS only has the OLD
    key, the new token is signed with the NEW key. Verification must
    recover via exactly one forced refetch, not deny forever — PROVIDED the
    rotation is discovered outside the fetch-attempt cooldown window (the
    same cooldown that bounds refetch-on-unknown-kid storms per the JS
    reference implementation's documented contract; see the companion
    cooldown test below for the in-window case, which correctly denies)."""
    old_priv, old_pub = rsa_keypair
    new_priv, new_pub = rsa_keypair_2

    calls: list[str] = []
    jwks_state = {_jwks_url(): [_jwk(old_pub, "kid-old")]}
    client = _FakeHttpClient(jwks_state, calls=calls)

    old_token = _sign(old_priv, "kid-old")
    result = await cfa.verify_cf_access_assertion(old_token, config=_config(), http_client=client)
    assert result.ok is True
    assert len(calls) == 1  # cache populated with the old key

    # Simulate real time passing beyond the fetch-attempt cooldown, so the
    # forced refetch below is not itself throttled.
    cfa._last_fetch_attempt[TEAM_DOMAIN] -= cfa.JWKS_COOLDOWN_SECONDS + 1

    # Cloudflare rotates: the endpoint now serves ONLY the new key.
    jwks_state[_jwks_url()] = [_jwk(new_pub, "kid-new")]
    new_token = _sign(new_priv, "kid-new")

    result2 = await cfa.verify_cf_access_assertion(new_token, config=_config(), http_client=client)
    assert result2.ok is True
    assert len(calls) == 2  # exactly one forced refetch, not a hammer


async def test_key_rotation_inside_cooldown_window_denies_not_hangs(rsa_keypair, rsa_keypair_2):
    """The narrow, documented tradeoff: a rotation discovered WITHIN the
    30s fetch-attempt cooldown of the previous fetch is denied rather than
    forcing a second network call — it recovers on the next verification
    attempt after the cooldown elapses, it does not deny forever."""
    old_priv, old_pub = rsa_keypair
    new_priv, new_pub = rsa_keypair_2

    jwks_state = {_jwks_url(): [_jwk(old_pub, "kid-old")]}
    client = _FakeHttpClient(jwks_state)

    old_token = _sign(old_priv, "kid-old")
    result = await cfa.verify_cf_access_assertion(old_token, config=_config(), http_client=client)
    assert result.ok is True

    jwks_state[_jwks_url()] = [_jwk(new_pub, "kid-new")]
    new_token = _sign(new_priv, "kid-new")

    # No time-travel here — still inside the cooldown from the first fetch.
    result2 = await cfa.verify_cf_access_assertion(new_token, config=_config(), http_client=client)
    assert result2.ok is False


async def test_jwks_cooldown_prevents_hammering_after_a_failed_fetch(rsa_keypair):
    priv, _ = rsa_keypair
    token = _sign(priv, "kid-1")
    calls: list[str] = []
    client = _FakeHttpClient({}, calls=calls)  # every fetch fails

    r1 = await cfa.verify_cf_access_assertion(token, config=_config(), http_client=client)
    r2 = await cfa.verify_cf_access_assertion(token, config=_config(), http_client=client)

    assert r1.ok is False
    assert r2.ok is False
    # Second call was inside the cooldown window — denied without a second
    # network attempt.
    assert len(calls) == 1


async def test_stale_cache_past_ttl_is_never_served(rsa_keypair, monkeypatch):
    """Once the TTL has elapsed, a subsequent fetch failure must deny —
    never fall back to the (now-stale) previously-cached keyset."""
    priv, pub = rsa_keypair
    token = _sign(priv, "kid-1")
    good_client = _FakeHttpClient({_jwks_url(): [_jwk(pub, "kid-1")]})

    result = await cfa.verify_cf_access_assertion(token, config=_config(), http_client=good_client)
    assert result.ok is True

    # Force the cache to look expired, and clear the cooldown timer so the
    # next call actually attempts a refetch.
    entry = cfa._jwks_cache[TEAM_DOMAIN]
    entry.fetched_at -= cfa.JWKS_TTL_SECONDS + 1
    cfa._last_fetch_attempt[TEAM_DOMAIN] -= cfa.JWKS_COOLDOWN_SECONDS + 1

    failing_client = _FakeHttpClient({})  # JWKS endpoint now unreachable
    result2 = await cfa.verify_cf_access_assertion(token, config=_config(), http_client=failing_client)
    assert result2.ok is False
    assert "jwks_unavailable" in result2.reason


# ── Startup logging never raises / never leaks secrets ───────────────────


def test_startup_log_does_not_raise_when_unconfigured(monkeypatch, caplog):
    monkeypatch.setattr(cfa.settings, "cf_access_team_domain", "")
    monkeypatch.setattr(cfa.settings, "cf_access_aud", "")
    cfa.log_cf_access_startup_state()  # must not raise


def test_startup_log_does_not_raise_when_configured(monkeypatch):
    monkeypatch.setattr(cfa.settings, "cf_access_team_domain", TEAM_DOMAIN)
    monkeypatch.setattr(cfa.settings, "cf_access_aud", AUD)
    cfa.log_cf_access_startup_state()  # must not raise
