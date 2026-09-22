"""ADR-0048 — POST /api/auth/sso-exchange: Access assertion -> local bearer.

ADR-0047 Amendment 1 established that `/api/intake/*` and `/api/tos/*` sit
behind Cloudflare Access apps with **bypass** policies, so the edge injects no
`Cf-Access-Jwt-Assertion` there and the 8 operator-only endpoints under those
prefixes accept only a local bearer. Once `LOCAL_LOGIN_DISABLED=true` there was
no way left to obtain one — reordering the SPA's `useAuth.init()` can preserve
an existing bearer but cannot mint one. This endpoint is that mint.

The verification is NOT mocked. A real RSA keypair is generated per module and
every assertion is really signed and really verified by `app/auth/cf_access.py`
— only the JWKS *transport* is stubbed, so the signature, `alg` allow-list,
`aud`, `iss` and `exp` checks all genuinely run. A stub of
`verify_cf_access_assertion` itself would make every denial test below
incapable of failing, which is the whole point of them.
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

import tests.conftest  # noqa: F401 — env stubs

from app.auth import cf_access as cfa
from app.config import settings
from app.database import get_db

TEAM_DOMAIN = "test-team.cloudflareaccess.com"
AUD = "test-aud-tag"
EMAIL = "bill@barnardhq.com"
SHADOW_USERNAME = f"cf-access:{EMAIL}"
KID = "kid-1"


# ── A real keypair + a real signer ──────────────────────────────────────


@pytest.fixture(scope="module")
def rsa_keypair():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    def _pem_pair():
        priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        return (
            priv.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            ).decode(),
            priv.public_key().public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo,
            ).decode(),
        )

    # (cloudflare's real key, an attacker's key Cloudflare never published)
    return _pem_pair(), _pem_pair()


def _jwk(pub_pem: str, kid: str) -> dict:
    from jose.backends.cryptography_backend import CryptographyRSAKey

    jwk = CryptographyRSAKey(pub_pem, algorithm="RS256").to_dict()
    jwk["kid"] = kid
    return jwk


def _sign(
    priv_pem: str,
    *,
    email: str = EMAIL,
    iss: str | None = None,
    aud: str | None = None,
    exp_delta: int = 3600,
    alg: str = "RS256",
    kid: str = KID,
) -> str:
    from jose import jwt as josejwt

    claims = {
        "email": email,
        "iss": iss if iss is not None else f"https://{TEAM_DOMAIN}",
        "aud": aud if aud is not None else AUD,
        "exp": int(time.time()) + exp_delta,
        "iat": int(time.time()),
    }
    return josejwt.encode(claims, priv_pem, algorithm=alg, headers={"kid": kid})


def _json(obj) -> bytes:
    import json

    return json.dumps(obj, separators=(",", ":"), sort_keys=True).encode()


def _b64u(raw: bytes) -> str:
    import base64

    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _attack_claims() -> dict:
    return {
        "email": EMAIL,
        "iss": f"https://{TEAM_DOMAIN}",
        "aud": AUD,
        "exp": int(time.time()) + 3600,
    }


def _raw_jwt(header: dict, claims: dict, signature: bytes) -> str:
    """Assemble a JWT byte-for-byte, bypassing any library that would refuse
    to build it. Needed for the `alg: none` / algorithm-confusion forgeries."""
    return f"{_b64u(_json(header))}.{_b64u(_json(claims))}.{_b64u(signature)}"


# ── Test app + a fake DB that speaks the resolver's dialect ─────────────


class _Result:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeDb:
    """Returns `shadow_user` for the resolver's mapping-table lookup. With
    `shadow_user=None` it exercises the provisioning path instead."""

    def __init__(self, shadow_user=None):
        self._shadow_user = shadow_user
        self.added = []
        self.commits = 0

    async def execute(self, _stmt):
        return _Result(self._shadow_user)

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = uuid.uuid4()

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        pass


def _shadow_user(username: str = SHADOW_USERNAME) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        username=username,
        hashed_password="$2b$12$notAUsablePasswordHashAtAll000000000000000000000000000",
        is_active=True,
        created_at=datetime.utcnow(),
    )


def _build_app(db=None, extra_routers=()):
    from app.routers.auth import router as auth_router

    app = FastAPI()
    app.include_router(auth_router)
    for r in extra_routers:
        app.include_router(r)

    db = db if db is not None else _FakeDb(_shadow_user())

    async def _get_db_override():
        yield db

    app.dependency_overrides[get_db] = _get_db_override

    from slowapi import Limiter, _rate_limit_exceeded_handler
    from slowapi.errors import RateLimitExceeded
    from app.utils.client_ip import get_trusted_client_ip

    app.state.limiter = Limiter(key_func=get_trusted_client_ip)
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    return app


async def _post(app, path, *, assertion: str | None = None, **kwargs):
    headers = dict(kwargs.pop("headers", {}))
    if assertion is not None:
        headers[cfa.CF_ACCESS_HEADER] = assertion
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.post(path, headers=headers, **kwargs)


@pytest.fixture(autouse=True)
def _access_configured(monkeypatch, rsa_keypair):
    """Access wired up and the real JWKS transport replaced by an in-memory
    one holding ONLY Cloudflare's public key. Nothing else is stubbed."""
    (_priv, pub), (_priv2, _pub2) = rsa_keypair
    monkeypatch.setattr(settings, "cf_access_team_domain", TEAM_DOMAIN)
    monkeypatch.setattr(settings, "cf_access_aud", AUD)
    monkeypatch.setattr(settings, "cf_access_allowed_emails", EMAIL)
    monkeypatch.setattr(settings, "local_login_disabled", False)
    monkeypatch.setattr(settings, "service_account_usernames", "")

    async def _fake_get_jwks(team_domain, *, force=False, http_client=None):
        return [_jwk(pub, KID)]

    monkeypatch.setattr(cfa, "_get_jwks", _fake_get_jwks)
    cfa._reset_cf_access_cache_for_test()

    from app.routers import auth as auth_mod
    auth_mod.limiter.reset()
    yield
    cfa._reset_cf_access_cache_for_test()
    auth_mod.limiter.reset()


@pytest.fixture
def good_assertion(rsa_keypair):
    (priv, _pub), _attacker = rsa_keypair
    return _sign(priv)


# ── Happy path ──────────────────────────────────────────────────────────


async def test_valid_assertion_mints_a_bearer_pair(good_assertion):
    resp = await _post(_build_app(), "/api/auth/sso-exchange", assertion=good_assertion)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"] and body["refresh_token"]


async def test_minted_token_shape_matches_a_password_login(good_assertion):
    """The SPA path downstream of this must not change: same keys, same
    claims, same `type` markers as POST /api/auth/login produces."""
    from jose import jwt as josejwt

    resp = await _post(_build_app(), "/api/auth/sso-exchange", assertion=good_assertion)
    body = resp.json()
    assert set(body) == {"access_token", "refresh_token", "token_type"}

    access = josejwt.decode(
        body["access_token"], settings.jwt_secret_key, algorithms=[settings.jwt_algorithm]
    )
    refresh = josejwt.decode(
        body["refresh_token"], settings.jwt_secret_key, algorithms=[settings.jwt_algorithm]
    )
    assert access["sub"] == refresh["sub"] == SHADOW_USERNAME
    assert access["type"] == "access"
    assert refresh["type"] == "refresh"
    assert refresh["exp"] > access["exp"]


async def test_minted_token_is_accepted_by_get_current_user(good_assertion):
    """The credential is real, not merely well-formed."""
    from app.auth.jwt import get_current_user, _user_cache

    _user_cache.clear()
    user = _shadow_user()
    db = _FakeDb(user)
    resp = await _post(_build_app(db), "/api/auth/sso-exchange", assertion=good_assertion)
    token = resp.json()["access_token"]

    from fastapi.security import HTTPAuthorizationCredentials

    resolved = await get_current_user(
        request=SimpleNamespace(headers={}),
        credentials=HTTPAuthorizationCredentials(scheme="Bearer", credentials=token),
        db=db,
    )
    assert resolved.username == SHADOW_USERNAME
    _user_cache.clear()


async def test_exchange_provisions_the_shadow_user_when_absent(good_assertion):
    """Reuses `resolve_cf_access_user`, so a first-time operator is
    provisioned through the same mapping table `get_current_user` uses —
    no second identity mechanism."""
    from app.models.cf_access_identity import CfAccessIdentity

    db = _FakeDb(None)
    resp = await _post(_build_app(db), "/api/auth/sso-exchange", assertion=good_assertion)
    assert resp.status_code == 200, resp.text
    assert any(isinstance(o, CfAccessIdentity) for o in db.added), db.added
    assert db.commits == 1


# ── The whole point: it works when local login is retired ───────────────


async def test_exchange_works_when_local_login_is_disabled(monkeypatch, good_assertion):
    """If this ever regresses, turning on Step B re-breaks all 8 bypassed
    operator endpoints — the 2026-09-21 outage, repeated."""
    monkeypatch.setattr(settings, "local_login_disabled", True)
    resp = await _post(_build_app(), "/api/auth/sso-exchange", assertion=good_assertion)
    assert resp.status_code == 200, resp.text
    assert resp.json()["access_token"]


async def test_exchange_is_reachable_while_login_and_refresh_are_403(
    monkeypatch, good_assertion
):
    """Pins the asymmetry: the two credential-MINTING local routes are shut,
    this one is open, in one and the same configuration."""
    monkeypatch.setattr(settings, "local_login_disabled", True)
    app = _build_app()

    login = await _post(
        app, "/api/auth/login", json={"username": "admin", "password": "CorrectHorse1!"}
    )
    assert login.status_code == 403, login.text

    exchange = await _post(app, "/api/auth/sso-exchange", assertion=good_assertion)
    assert exchange.status_code == 200, exchange.text


# ── A forged header mints nothing ───────────────────────────────────────


async def test_assertion_signed_by_a_foreign_key_mints_nothing(rsa_keypair):
    """The mesh-attacker case: the origin is reachable at 10.99.0.4:8000, so
    any container can SET this header. It cannot sign it."""
    _cloudflare, (attacker_priv, _attacker_pub) = rsa_keypair
    forged = _sign(attacker_priv)
    resp = await _post(_build_app(), "/api/auth/sso-exchange", assertion=forged)
    assert resp.status_code == 401, resp.text
    assert "access_token" not in resp.text


async def test_unsigned_alg_none_assertion_mints_nothing():
    """`alg: none` — the classic downgrade. Refused by the explicit RS256
    allow-list before any key is tried.

    Hand-assembled on purpose: `jose.jwt.encode` REFUSES to produce an
    `alg: none` token, so building this through the library would quietly
    test nothing. An attacker is under no such constraint."""
    unsigned = _raw_jwt({"alg": "none", "typ": "JWT", "kid": KID}, _attack_claims(), b"")
    resp = await _post(_build_app(), "/api/auth/sso-exchange", assertion=unsigned)
    assert resp.status_code == 401, resp.text
    assert "access_token" not in resp.text


async def test_hs256_algorithm_confusion_mints_nothing(rsa_keypair):
    """Signing HS256 with the RSA PUBLIC key as the shared secret — the other
    half of the confusion attack, and the one that succeeds against a verifier
    that takes `alg` from the token instead of from an allow-list.

    Also hand-assembled: python-jose refuses to use an asymmetric key as an
    HMAC secret, so this too has to be forged the way an attacker would."""
    import hashlib
    import hmac

    (_priv, pub), _attacker = rsa_keypair
    header = {"alg": "HS256", "typ": "JWT", "kid": KID}
    signing_input = f"{_b64u(_json(header))}.{_b64u(_json(_attack_claims()))}".encode()
    sig = hmac.new(pub.encode(), signing_input, hashlib.sha256).digest()
    confused = _raw_jwt(header, _attack_claims(), sig)

    resp = await _post(_build_app(), "/api/auth/sso-exchange", assertion=confused)
    assert resp.status_code == 401, resp.text
    assert "access_token" not in resp.text


async def test_wrong_audience_mints_nothing(rsa_keypair):
    """A correctly-signed assertion for a DIFFERENT Access app on the same
    team domain must not work here."""
    (priv, _pub), _attacker = rsa_keypair
    resp = await _post(
        _build_app(), "/api/auth/sso-exchange", assertion=_sign(priv, aud="some-other-app-aud")
    )
    assert resp.status_code == 401, resp.text


async def test_wrong_issuer_mints_nothing(rsa_keypair):
    (priv, _pub), _attacker = rsa_keypair
    resp = await _post(
        _build_app(),
        "/api/auth/sso-exchange",
        assertion=_sign(priv, iss="https://someone-elses-team.cloudflareaccess.com"),
    )
    assert resp.status_code == 401, resp.text


async def test_expired_assertion_mints_nothing(rsa_keypair):
    (priv, _pub), _attacker = rsa_keypair
    resp = await _post(
        _build_app(), "/api/auth/sso-exchange", assertion=_sign(priv, exp_delta=-3600)
    )
    assert resp.status_code == 401, resp.text


async def test_missing_assertion_mints_nothing():
    resp = await _post(_build_app(), "/api/auth/sso-exchange")
    assert resp.status_code == 401, resp.text


async def test_garbage_assertion_mints_nothing():
    resp = await _post(_build_app(), "/api/auth/sso-exchange", assertion="not-a-jwt-at-all")
    assert resp.status_code == 401, resp.text


async def test_email_outside_the_allowlist_mints_nothing(monkeypatch, rsa_keypair):
    """A perfectly valid Cloudflare assertion for somebody else's identity.
    CF_ACCESS_ALLOWED_EMAILS is defence in depth independent of the Access
    policy, and this route must honour it."""
    (priv, _pub), _attacker = rsa_keypair
    resp = await _post(
        _build_app(), "/api/auth/sso-exchange", assertion=_sign(priv, email="intruder@example.com")
    )
    assert resp.status_code == 401, resp.text
    assert "access_token" not in resp.text


async def test_denial_does_not_disclose_which_check_failed(rsa_keypair):
    """`email_not_allowed` vs `token_expired` would hand an attacker an
    allow-list oracle. Same body either way; the reason goes to the log."""
    (priv, _pub), _attacker = rsa_keypair
    not_allowed = await _post(
        _build_app(), "/api/auth/sso-exchange", assertion=_sign(priv, email="intruder@example.com")
    )
    expired = await _post(
        _build_app(), "/api/auth/sso-exchange", assertion=_sign(priv, exp_delta=-3600)
    )
    assert not_allowed.status_code == expired.status_code == 401
    assert not_allowed.json() == expired.json()
    for token in ("email_not_allowed", "token_expired", "intruder@example.com"):
        assert token not in not_allowed.text and token not in expired.text


# ── Inert on an install with no Cloudflare Access ───────────────────────


async def test_unconfigured_instance_returns_404_and_never_a_token(
    monkeypatch, good_assertion
):
    """Self-hosted/OSS and the public demo instance. Even presented with an
    assertion this route must look like it is not there."""
    monkeypatch.setattr(settings, "cf_access_team_domain", "")
    monkeypatch.setattr(settings, "cf_access_aud", "")
    resp = await _post(_build_app(), "/api/auth/sso-exchange", assertion=good_assertion)
    assert resp.status_code == 404, resp.text
    assert "access_token" not in resp.text


async def test_half_configured_instance_returns_404(monkeypatch, good_assertion):
    """One of the two vars set is still 'not configured' — the same
    structural gate `is_cf_access_configured()` applies everywhere else."""
    monkeypatch.setattr(settings, "cf_access_aud", "")
    resp = await _post(_build_app(), "/api/auth/sso-exchange", assertion=good_assertion)
    assert resp.status_code == 404, resp.text


# ── The endpoint's reason for existing: a bypassed-prefix route ─────────


async def test_minted_bearer_is_accepted_by_a_bypassed_prefix_endpoint(good_assertion):
    """`POST /api/intake/initiate` is endpoint #1 of the 8 enumerated in
    ADR-0047 Amendment 1 — it sits behind a bypass policy, so it never sees
    an Access assertion and accepts only a local bearer. The paired
    no-credential request proves the bearer is what changed the outcome."""
    from app.auth.jwt import _user_cache
    from app.routers.intake import router as intake_router

    _user_cache.clear()
    db = _FakeDb(_shadow_user())
    app = _build_app(db, extra_routers=(intake_router,))

    minted = (await _post(app, "/api/auth/sso-exchange", assertion=good_assertion)).json()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        without = await client.post("/api/intake/initiate", json={})
        with_bearer = await client.post(
            "/api/intake/initiate",
            json={},
            headers={"Authorization": f"Bearer {minted['access_token']}"},
        )

    assert without.status_code == 401, without.text
    assert with_bearer.status_code == 200, with_bearer.text
    assert with_bearer.json()["intake_token"]
    _user_cache.clear()
