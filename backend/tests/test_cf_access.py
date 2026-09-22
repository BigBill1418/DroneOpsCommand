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
    # PyJWT is NOT a dependency here, so build the none-alg token by hand.
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


# ── kid selection (ADR-0047 Amendment 3) ─────────────────────────────────
#
# python-jose never reads `kid`: handing it a whole JWK Set routes into
# `jose.jws._sig_matches_keys`, a bare `for key in keys: if key.verify(...)`
# loop. So before the fix, a token whose header claimed an UNKNOWN kid still
# verified as long as ANY key in the set matched its signature. These tests
# pin the selection, and pin that it did not widen the fetch-cooldown window.


async def test_unknown_kid_denies_even_when_a_set_key_matches_the_signature(
    rsa_keypair, rsa_keypair_2
):
    """THE REGRESSION. A two-key JWKS (what Cloudflare serves mid-rotation).
    The token is signed by a key that IS in the set, so the signature check
    itself passes — but its header claims a kid in NEITHER entry. Must be
    refused on the kid, not waved through on the signature.

    Runs in the steady state production actually sits in: JWKS cached from
    an earlier request, well outside the 30s fetch cooldown. The unknown kid
    therefore gets its forced refetch, the refetch returns the same keys,
    and the retry denies on the kid itself."""
    priv, pub = rsa_keypair
    _, pub2 = rsa_keypair_2

    jwks = [_jwk(pub, "kid-1"), _jwk(pub2, "kid-2")]
    client = _FakeHttpClient({_jwks_url(): jwks})

    # Warm the cache the way a previous good request would have.
    assert (
        await cfa.verify_cf_access_assertion(
            _sign(priv, "kid-1"), config=_config(), http_client=client
        )
    ).ok is True
    cfa._last_fetch_attempt[TEAM_DOMAIN] -= cfa.JWKS_COOLDOWN_SECONDS + 1

    result = await cfa.verify_cf_access_assertion(
        _sign(priv, "kid-the-token-invented"), config=_config(), http_client=client
    )
    assert result.ok is False
    assert result.reason == "unknown_kid"
    # And the attacker-controlled kid is never echoed into the denial reason
    # (routers/auth.py logs that reason verbatim on the sso-exchange path).
    assert "kid-the-token-invented" not in result.reason


async def test_unknown_kid_on_a_cold_cache_still_denies(rsa_keypair, rsa_keypair_2):
    """Same forgery against a COLD cache. The forced refetch is itself
    throttled by the 30s cooldown the initial fill just started, so the
    denial surfaces with the cooldown reason rather than `unknown_kid`.
    Either way it fails closed — which is the property that matters."""
    priv, pub = rsa_keypair
    _, pub2 = rsa_keypair_2

    client = _FakeHttpClient(
        {_jwks_url(): [_jwk(pub, "kid-1"), _jwk(pub2, "kid-2")]}
    )
    result = await cfa.verify_cf_access_assertion(
        _sign(priv, "kid-the-token-invented"), config=_config(), http_client=client
    )
    assert result.ok is False
    assert "kid-the-token-invented" not in result.reason


async def test_matching_kid_in_a_multi_key_jwks_still_verifies(
    rsa_keypair, rsa_keypair_2
):
    """The other half of the acceptance bar: selection must pick the RIGHT
    key out of a multi-key set, not merely reject. Signed by key 2, which
    sits second in the published set."""
    _, pub = rsa_keypair
    priv2, pub2 = rsa_keypair_2

    token = _sign(priv2, "kid-2")
    client = _FakeHttpClient(
        {_jwks_url(): [_jwk(pub, "kid-1"), _jwk(pub2, "kid-2")]}
    )

    result = await cfa.verify_cf_access_assertion(
        token, config=_config(), http_client=client
    )
    assert result.ok is True
    assert result.email == EMAIL


async def test_token_with_no_kid_header_denies_without_spending_a_fetch(
    rsa_keypair,
):
    """A kid-less token can never be fixed by a refetch, so it must NOT
    force one — that would hand an unauthenticated caller a lever on the
    30s fetch cooldown. Exactly one fetch (the initial cache fill), never a
    second forced one."""
    priv, pub = rsa_keypair
    from jose import jwt as josejwt

    claims = {
        "email": EMAIL,
        "iss": f"https://{TEAM_DOMAIN}",
        "aud": AUD,
        "exp": int(time.time()) + 3600,
    }
    token = josejwt.encode(claims, priv, algorithm="RS256")  # no `kid` header
    assert "kid" not in josejwt.get_unverified_header(token)

    calls: list[str] = []
    client = _FakeHttpClient({_jwks_url(): [_jwk(pub, "kid-1")]}, calls=calls)

    result = await cfa.verify_cf_access_assertion(
        token, config=_config(), http_client=client
    )
    assert result.ok is False
    assert result.reason == "unknown_kid"
    assert len(calls) == 1  # no forced refetch was spent on it


async def test_unknown_kid_costs_no_more_fetches_than_a_bad_signature_did(
    rsa_keypair, rsa_keypair_2
):
    """The cooldown must not have been widened. An unknown-kid token and a
    bad-signature token must trigger the SAME number of fetch attempts —
    one initial + one forced refetch — so the fix cannot make the
    `jwks fetch cooldown active` window any easier to hold open than the
    pre-fix code already allowed."""
    priv, pub = rsa_keypair
    attacker_priv, _ = rsa_keypair_2

    # Bad signature, correct kid: the pre-existing refetch trigger.
    bad_sig_calls: list[str] = []
    c1 = _FakeHttpClient({_jwks_url(): [_jwk(pub, "kid-1")]}, calls=bad_sig_calls)
    r1 = await cfa.verify_cf_access_assertion(
        _sign(attacker_priv, "kid-1"), config=_config(), http_client=c1
    )
    assert r1.ok is False

    cfa._reset_cf_access_cache_for_test()

    # Unknown kid, valid signature: the newly-introduced refetch trigger.
    unknown_kid_calls: list[str] = []
    c2 = _FakeHttpClient({_jwks_url(): [_jwk(pub, "kid-1")]}, calls=unknown_kid_calls)
    r2 = await cfa.verify_cf_access_assertion(
        _sign(priv, "kid-nope"), config=_config(), http_client=c2
    )
    assert r2.ok is False

    # One fetch each: the forced refetch is itself throttled by the cooldown
    # the initial fill started. The unknown-kid path is not cheaper OR more
    # expensive than the bad-signature path that already existed.
    assert len(unknown_kid_calls) == len(bad_sig_calls) == 1

    # Same comparison again in the warm-cache state, where the forced
    # refetch does run: still one apiece, still identical.
    for calls_out, token_factory in (
        ([], lambda: _sign(attacker_priv, "kid-1")),
        ([], lambda: _sign(priv, "kid-nope")),
    ):
        cfa._reset_cf_access_cache_for_test()
        c = _FakeHttpClient({_jwks_url(): [_jwk(pub, "kid-1")]}, calls=calls_out)
        assert (
            await cfa.verify_cf_access_assertion(
                _sign(priv, "kid-1"), config=_config(), http_client=c
            )
        ).ok is True
        cfa._last_fetch_attempt[TEAM_DOMAIN] -= cfa.JWKS_COOLDOWN_SECONDS + 1
        r = await cfa.verify_cf_access_assertion(
            token_factory(), config=_config(), http_client=c
        )
        assert r.ok is False
        assert len(calls_out) == 2  # initial fill + exactly one forced refetch


async def test_rotation_to_a_new_kid_still_recovers_via_the_refetch(
    rsa_keypair, rsa_keypair_2
):
    """Rotation resilience, expressed purely in kid terms: the cache holds
    only kid-old, Cloudflare now serves only kid-new, and the new token
    carries kid-new. The forced refetch must still recover it — an unknown
    kid is retryable precisely so a rotation does not cause an outage."""
    old_priv, old_pub = rsa_keypair
    new_priv, new_pub = rsa_keypair_2

    calls: list[str] = []
    jwks_state = {_jwks_url(): [_jwk(old_pub, "kid-old")]}
    client = _FakeHttpClient(jwks_state, calls=calls)

    assert (
        await cfa.verify_cf_access_assertion(
            _sign(old_priv, "kid-old"), config=_config(), http_client=client
        )
    ).ok is True

    cfa._last_fetch_attempt[TEAM_DOMAIN] -= cfa.JWKS_COOLDOWN_SECONDS + 1
    jwks_state[_jwks_url()] = [_jwk(new_pub, "kid-new")]

    result = await cfa.verify_cf_access_assertion(
        _sign(new_priv, "kid-new"), config=_config(), http_client=client
    )
    assert result.ok is True
    assert len(calls) == 2  # exactly one forced refetch


async def test_unknown_kid_inside_the_cooldown_window_still_fails_closed(
    rsa_keypair, rsa_keypair_2
):
    """An unknown kid discovered while the fetch cooldown is active denies
    rather than serving the cached set — the fail-closed contract holds on
    the new path exactly as it does on the signature path."""
    priv, pub = rsa_keypair
    _, pub2 = rsa_keypair_2

    client = _FakeHttpClient({_jwks_url(): [_jwk(pub, "kid-1")]})
    assert (
        await cfa.verify_cf_access_assertion(
            _sign(priv, "kid-1"), config=_config(), http_client=client
        )
    ).ok is True

    # Still inside the cooldown from that first fetch.
    result = await cfa.verify_cf_access_assertion(
        _sign(priv, "kid-2"), config=_config(), http_client=client
    )
    assert result.ok is False
    assert "jwks fetch cooldown active" in result.reason


# ── Startup logging never raises / never leaks secrets ───────────────────


def test_startup_log_does_not_raise_when_unconfigured(monkeypatch, caplog):
    monkeypatch.setattr(cfa.settings, "cf_access_team_domain", "")
    monkeypatch.setattr(cfa.settings, "cf_access_aud", "")
    cfa.log_cf_access_startup_state()  # must not raise


def test_startup_log_does_not_raise_when_configured(monkeypatch):
    monkeypatch.setattr(cfa.settings, "cf_access_team_domain", TEAM_DOMAIN)
    monkeypatch.setattr(cfa.settings, "cf_access_aud", AUD)
    cfa.log_cf_access_startup_state()  # must not raise
