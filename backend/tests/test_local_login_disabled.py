"""ADR-0047 Step B — LOCAL_LOGIN_DISABLED kill switch.

Default (false) must be a complete no-op — this is the state every self-
hosted/OSS install and the public demo instance are permanently in, so a
regression here would silently break local auth for every non-BarnardHQ
deployment of this software. When true, four credential-MINTING routes
(setup/login/account-PUT/refresh) are gated; get_current_user's bearer-
token VERIFICATION logic and GET /account (read-only identity, and the
route the frontend's silent SSO probe calls) are deliberately left
reachable — gating those would be exactly how an operator locks themselves
out even via a working Access session.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import FastAPI

import tests.conftest  # noqa: F401 — env stubs

from app.config import settings
from app.database import get_db


class _ScalarOneOrNone:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def scalars(self):
        return self

    def all(self):
        return [] if self._value is None else [self._value]


class _FakeDb:
    def __init__(self, user=None):
        self._user = user

    async def execute(self, _stmt):
        return _ScalarOneOrNone(self._user)

    def add(self, _obj):
        pass

    async def commit(self):
        pass


def _fake_user(username: str = "admin") -> SimpleNamespace:
    from app.auth.jwt import hash_password
    return SimpleNamespace(
        id="00000000-0000-0000-0000-000000000001",
        username=username,
        hashed_password=hash_password("CorrectHorse1!"),
        is_active=True,
        created_at=datetime.utcnow(),
    )


def _build_app(user=None):
    from app.routers.auth import router as auth_router

    app = FastAPI()
    app.include_router(auth_router)

    async def _get_db_override():
        yield _FakeDb(user)

    app.dependency_overrides[get_db] = _get_db_override

    from slowapi import Limiter, _rate_limit_exceeded_handler
    from slowapi.errors import RateLimitExceeded
    from app.utils.client_ip import get_trusted_client_ip

    app.state.limiter = Limiter(key_func=get_trusted_client_ip)
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    return app


async def _request(app, method, path, **kwargs):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.request(method, path, **kwargs)


def _bearer_token(username: str = "admin") -> str:
    from app.config import settings as cfg
    from jose import jwt as josejwt

    payload = {"sub": username, "type": "access", "exp": datetime.utcnow() + timedelta(minutes=30)}
    return josejwt.encode(payload, cfg.jwt_secret_key, algorithm=cfg.jwt_algorithm)


def _refresh_token(username: str = "admin") -> str:
    from app.config import settings as cfg
    from jose import jwt as josejwt

    payload = {"sub": username, "type": "refresh", "exp": datetime.utcnow() + timedelta(days=30)}
    return josejwt.encode(payload, cfg.jwt_secret_key, algorithm=cfg.jwt_algorithm)


@pytest.fixture(autouse=True)
def _reset_flag(monkeypatch):
    monkeypatch.setattr(settings, "local_login_disabled", False)
    yield


# ── Default (false) is a complete no-op ──────────────────────────────────


async def test_login_works_when_flag_is_false_default_state():
    user = _fake_user("admin")
    resp = await _request(
        _build_app(user), "POST", "/api/auth/login",
        json={"username": "admin", "password": "CorrectHorse1!"},
    )
    assert resp.status_code == 200
    assert "access_token" in resp.json()


async def test_setup_works_when_flag_is_false_default_state():
    resp = await _request(
        _build_app(None), "POST", "/api/auth/setup",
        json={"username": "newadmin", "password": "CorrectHorse1!"},
    )
    assert resp.status_code == 200


async def test_refresh_works_when_flag_is_false_default_state():
    resp = await _request(
        _build_app(_fake_user("admin")), "POST", "/api/auth/refresh",
        json={"refresh_token": _refresh_token("admin")},
    )
    assert resp.status_code == 200


# ── Gated when true ───────────────────────────────────────────────────


async def test_login_403s_when_disabled(monkeypatch):
    monkeypatch.setattr(settings, "local_login_disabled", True)
    resp = await _request(
        _build_app(_fake_user("admin")), "POST", "/api/auth/login",
        json={"username": "admin", "password": "CorrectHorse1!"},
    )
    assert resp.status_code == 403


async def test_setup_403s_when_disabled(monkeypatch):
    monkeypatch.setattr(settings, "local_login_disabled", True)
    resp = await _request(
        _build_app(None), "POST", "/api/auth/setup",
        json={"username": "newadmin", "password": "CorrectHorse1!"},
    )
    assert resp.status_code == 403


async def test_refresh_403s_when_disabled(monkeypatch):
    monkeypatch.setattr(settings, "local_login_disabled", True)
    resp = await _request(
        _build_app(_fake_user("admin")), "POST", "/api/auth/refresh",
        json={"refresh_token": _refresh_token("admin")},
    )
    assert resp.status_code == 403


async def test_account_put_403s_when_disabled(monkeypatch):
    user = _fake_user("admin")
    app = _build_app(user)

    from app.auth.jwt import get_current_user
    app.dependency_overrides[get_current_user] = lambda: user

    monkeypatch.setattr(settings, "local_login_disabled", True)
    resp = await _request(
        app, "PUT", "/api/auth/account",
        json={"current_password": "CorrectHorse1!", "new_password": "SomethingNew1!"},
    )
    assert resp.status_code == 403


# ── Deliberately NOT gated — GET /account is the SSO probe target ───────


async def test_get_account_still_works_when_disabled(monkeypatch):
    """GET /account must stay reachable even when local login is disabled
    — it is read-only identity info AND the exact endpoint the frontend's
    silent SSO probe calls. Gating it would lock an operator out even
    while genuinely authenticated via a working Access session."""
    user = _fake_user("admin")
    app = _build_app(user)

    from app.auth.jwt import get_current_user
    app.dependency_overrides[get_current_user] = lambda: user

    monkeypatch.setattr(settings, "local_login_disabled", True)
    resp = await _request(app, "GET", "/api/auth/account")
    assert resp.status_code == 200
    assert resp.json()["username"] == "admin"


async def test_setup_status_reports_the_flag(monkeypatch):
    monkeypatch.setattr(settings, "local_login_disabled", True)
    resp = await _request(_build_app(None), "GET", "/api/auth/setup-status")
    body = resp.json()
    assert body["local_login_disabled"] is True
    assert body["needs_setup"] is False
