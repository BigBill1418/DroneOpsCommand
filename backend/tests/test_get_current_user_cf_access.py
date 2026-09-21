"""``get_current_user`` CF-Access wiring (ADR-0047, app/auth/jwt.py).

Unit-level (mocked DB + mocked verifier — the verifier's own crypto is
covered exhaustively by test_cf_access.py, and resolve_cf_access_user's
real-DB identity semantics by test_cf_access_identity_resolution.py). This
file's job is narrower and specific: prove the DECISION LOGIC in
get_current_user itself — which credential wins, what falls through to
what, and that the pre-ADR-0047 behavior is byte-identical when CF Access
is not configured (the "additive, not a replacement" contract).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

import tests.conftest  # noqa: F401 — env stubs

from app.auth import jwt as jwt_mod
from app.auth import cf_access as cfa


def _fake_user(username: str = "alice", is_active: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        username=username,
        hashed_password="$2b$12$dummyhash",
        is_active=is_active,
        created_at=datetime.utcnow(),
    )


def _fake_db_returning(user_or_none):
    db = MagicMock()
    scalar_result = MagicMock()
    scalar_result.scalar_one_or_none.return_value = user_or_none
    db.execute = AsyncMock(return_value=scalar_result)
    return db


def _fake_token(username: str = "alice") -> str:
    from app.config import settings as cfg
    from jose import jwt as josejwt

    payload = {
        "sub": username,
        "type": "access",
        "exp": datetime.utcnow() + timedelta(minutes=30),
    }
    return josejwt.encode(payload, cfg.jwt_secret_key, algorithm=cfg.jwt_algorithm)


def _fake_credentials(token: str | None):
    return SimpleNamespace(credentials=token) if token is not None else None


def _fake_request(headers: dict | None = None):
    return SimpleNamespace(headers=headers or {})


@pytest.fixture(autouse=True)
def _clear_cache():
    jwt_mod.invalidate_user_cache(None)
    yield
    jwt_mod.invalidate_user_cache(None)


@pytest.fixture(autouse=True)
def _cf_access_unconfigured(monkeypatch):
    """Default every test to CF Access OFF; individual tests opt in."""
    monkeypatch.setattr(cfa.settings, "cf_access_team_domain", "")
    monkeypatch.setattr(cfa.settings, "cf_access_aud", "")


def _configure_cf_access(monkeypatch):
    monkeypatch.setattr(cfa.settings, "cf_access_team_domain", "test-team.cloudflareaccess.com")
    monkeypatch.setattr(cfa.settings, "cf_access_aud", "test-aud")


# ── Byte-identical-when-unconfigured contract ────────────────────────────


async def test_unconfigured_ignores_cf_access_header_uses_bearer(monkeypatch):
    """Even if a Cf-Access-Jwt-Assertion header is somehow present, when
    CF Access is not configured it must be completely ignored and the
    request must resolve purely via the bearer token — no verifier call."""
    user = _fake_user("alice")
    db = _fake_db_returning(user)
    request = _fake_request({"cf-access-jwt-assertion": "some-token"})
    creds = _fake_credentials(_fake_token("alice"))

    verify_spy = AsyncMock(side_effect=AssertionError("must not be called"))
    monkeypatch.setattr(jwt_mod, "verify_cf_access_assertion", verify_spy)

    result = await jwt_mod.get_current_user(request=request, credentials=creds, db=db)
    assert result.username == "alice"
    verify_spy.assert_not_called()


async def test_unconfigured_no_bearer_no_header_401s(monkeypatch):
    request = _fake_request({})
    with pytest.raises(HTTPException) as ei:
        await jwt_mod.get_current_user(request=request, credentials=None, db=_fake_db_returning(None))
    assert ei.value.status_code == 401


# ── Configured: CF-Access branch ─────────────────────────────────────────


async def test_configured_no_header_falls_through_to_bearer(monkeypatch):
    _configure_cf_access(monkeypatch)
    user = _fake_user("alice")
    db = _fake_db_returning(user)
    request = _fake_request({})  # no assertion header at all
    creds = _fake_credentials(_fake_token("alice"))

    result = await jwt_mod.get_current_user(request=request, credentials=creds, db=db)
    assert result.username == "alice"


async def test_configured_invalid_assertion_falls_through_to_bearer_not_401(monkeypatch):
    """A CF-Access credential that fails verification must NOT immediately
    401 the request — the session token is a fully independent, still-
    valid credential until Step B (LOCAL_LOGIN_DISABLED)."""
    _configure_cf_access(monkeypatch)
    user = _fake_user("alice")
    db = _fake_db_returning(user)
    request = _fake_request({"cf-access-jwt-assertion": "bad-token"})
    creds = _fake_credentials(_fake_token("alice"))

    verify_mock = AsyncMock(return_value=cfa.CfAccessResult(ok=False, reason="verification_failed"))
    monkeypatch.setattr(jwt_mod, "verify_cf_access_assertion", verify_mock)

    result = await jwt_mod.get_current_user(request=request, credentials=creds, db=db)
    assert result.username == "alice"
    verify_mock.assert_awaited_once()


async def test_configured_invalid_assertion_and_no_bearer_401s(monkeypatch):
    _configure_cf_access(monkeypatch)
    request = _fake_request({"cf-access-jwt-assertion": "bad-token"})

    verify_mock = AsyncMock(return_value=cfa.CfAccessResult(ok=False, reason="verification_failed"))
    monkeypatch.setattr(jwt_mod, "verify_cf_access_assertion", verify_mock)

    with pytest.raises(HTTPException) as ei:
        await jwt_mod.get_current_user(request=request, credentials=None, db=_fake_db_returning(None))
    assert ei.value.status_code == 401


def _any():
    """Sentinel matcher — the DB arg identity isn't the point of this test."""
    from unittest.mock import ANY
    return ANY


async def test_configured_valid_assertion_authenticates_without_any_bearer(monkeypatch):
    """The core SSO path: a valid Access assertion and NO Authorization
    header at all must still authenticate — this is the shape of a real
    browser request once Access fronts the hostname."""
    _configure_cf_access(monkeypatch)
    request = _fake_request({"cf-access-jwt-assertion": "good-token"})
    resolved_user = _fake_user("cf-access:bill@barnardhq.com")

    verify_mock = AsyncMock(return_value=cfa.CfAccessResult(ok=True, email="bill@barnardhq.com"))
    monkeypatch.setattr(jwt_mod, "verify_cf_access_assertion", verify_mock)
    resolve_mock = AsyncMock(return_value=resolved_user)
    monkeypatch.setattr(jwt_mod, "resolve_cf_access_user", resolve_mock)

    result = await jwt_mod.get_current_user(request=request, credentials=None, db=MagicMock())
    assert result is resolved_user
    resolve_mock.assert_awaited_once_with(_any(), "bill@barnardhq.com")


async def test_configured_valid_assertion_takes_precedence_over_bearer(monkeypatch):
    """When BOTH credentials are present and both would independently
    succeed, the CF-Access branch wins (it is checked first) — the
    resolved identity comes from Access, not the bearer token's user."""
    _configure_cf_access(monkeypatch)
    request = _fake_request({"cf-access-jwt-assertion": "good-token"})
    creds = _fake_credentials(_fake_token("alice"))  # would also succeed
    resolved_user = _fake_user("cf-access:bill@barnardhq.com")

    verify_mock = AsyncMock(return_value=cfa.CfAccessResult(ok=True, email="bill@barnardhq.com"))
    monkeypatch.setattr(jwt_mod, "verify_cf_access_assertion", verify_mock)
    resolve_mock = AsyncMock(return_value=resolved_user)
    monkeypatch.setattr(jwt_mod, "resolve_cf_access_user", resolve_mock)

    db = _fake_db_returning(_fake_user("alice"))
    result = await jwt_mod.get_current_user(request=request, credentials=creds, db=db)
    assert result is resolved_user
    # The bearer path's own DB lookup must never have been reached.
    db.execute.assert_not_called()
