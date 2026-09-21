"""``GET /api/auth/setup-status`` SSO flags (ADR-0047).

The frontend's silent-SSO probe (``useAuth``) needs to know, BEFORE it
decides whether to attempt an unauthenticated ``/auth/account`` probe or go
straight to the password form, whether Cloudflare Access verification is
even configured. This is the one public endpoint that already runs on
every app boot, so it carries the flags rather than a new route.
"""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

import tests.conftest  # noqa: F401 — env stubs

from app.auth import cf_access as cfa
from app.database import get_db


class _ScalarsAll:
    def __init__(self, users):
        self._users = users

    def scalars(self):
        return self

    def all(self):
        return self._users


class _FakeDb:
    def __init__(self, users):
        self._users = users

    async def execute(self, _stmt):
        return _ScalarsAll(self._users)


def _build_app(users):
    from app.routers.auth import router as auth_router

    app = FastAPI()
    app.include_router(auth_router)

    async def _get_db_override():
        yield _FakeDb(users)

    app.dependency_overrides[get_db] = _get_db_override

    from slowapi import Limiter, _rate_limit_exceeded_handler
    from slowapi.errors import RateLimitExceeded
    from app.utils.client_ip import get_trusted_client_ip

    app.state.limiter = Limiter(key_func=get_trusted_client_ip)
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    return app


async def _get(app, path="/api/auth/setup-status"):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.get(path)


@pytest.fixture(autouse=True)
def _cf_access_unconfigured(monkeypatch):
    monkeypatch.setattr(cfa.settings, "cf_access_team_domain", "")
    monkeypatch.setattr(cfa.settings, "cf_access_aud", "")
    monkeypatch.setattr(cfa.settings, "local_login_disabled", False)


async def test_sso_configured_false_by_default():
    resp = await _get(_build_app([SimpleNamespace(username="admin")]))
    body = resp.json()
    assert body["sso_configured"] is False
    assert body["local_login_disabled"] is False
    assert body["needs_setup"] is False  # one user already exists


async def test_sso_configured_true_once_both_env_vars_set(monkeypatch):
    monkeypatch.setattr(cfa.settings, "cf_access_team_domain", "team.cloudflareaccess.com")
    monkeypatch.setattr(cfa.settings, "cf_access_aud", "aud-tag")

    resp = await _get(_build_app([]))
    body = resp.json()
    assert body["sso_configured"] is True
    assert body["needs_setup"] is True  # no users yet — flag is independent


async def test_local_login_disabled_true_forces_needs_setup_false(monkeypatch):
    monkeypatch.setattr(cfa.settings, "local_login_disabled", True)

    resp = await _get(_build_app([]))  # zero users
    body = resp.json()
    assert body["needs_setup"] is False  # never offer a wizard that can't be used
    assert body["local_login_disabled"] is True
