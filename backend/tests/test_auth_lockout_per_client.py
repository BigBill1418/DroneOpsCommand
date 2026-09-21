"""Login lockout is per-real-client, not a global nginx-IP bucket (ADR-0045).

Pre-fix: `auth.py`'s `_check_lockout`/`_record_failure` keyed on
`get_remote_address(request)`, which reads `request.client.host` — the
direct ASGI TCP peer. Because nginx always sits in front of uvicorn, that
peer is nginx's OWN container IP on every request, never the caller's. The
practical effect: a stranger repeatedly failing `/api/auth/login` and an
operator logging in with a typo shared ONE lockout bucket — a stranger's
failed attempts could lock the operator out of their own instance.

Starlette's `TestClient` fixes the simulated peer to the literal string
`"testclient"` for every request regardless of headers, which would make
every request in this file collapse onto one key no matter what is being
tested — so these tests build their own `httpx.Client` over
`httpx.ASGITransport(app=app, client=(ip, port))`, one instance per
simulated real client, to get genuinely distinct peers. (No
X-Forwarded-For/trusted-proxy layer is needed here — that mechanism is
already pinned exhaustively in `test_client_ip.py`; this file's job is
only to prove the *login route* actually uses distinct keys for distinct
callers.)
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI


class _ScalarOneOrNone:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeAsyncSession:
    def __init__(self, user):
        self._user = user

    async def execute(self, _stmt):
        return _ScalarOneOrNone(self._user)


def _build_app(user):
    from app.database import get_db
    from app.routers.auth import router as auth_router

    app = FastAPI()
    app.include_router(auth_router)

    async def _get_db_override():
        yield _FakeAsyncSession(user)

    app.dependency_overrides[get_db] = _get_db_override

    from slowapi import Limiter, _rate_limit_exceeded_handler
    from slowapi.errors import RateLimitExceeded
    from app.utils.client_ip import get_trusted_client_ip

    app.state.limiter = Limiter(key_func=get_trusted_client_ip)
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    return app


def _client_at(app, peer_ip: str) -> httpx.AsyncClient:
    """A dedicated httpx.AsyncClient whose simulated ASGI peer is `peer_ip`
    — i.e. a genuinely distinct "real client" as the app sees it.
    ASGITransport only implements the async transport interface, so this
    (and the two tests below) are async."""
    transport = httpx.ASGITransport(app=app, client=(peer_ip, 443))
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


def _make_user():
    return SimpleNamespace(username="operator", hashed_password="$2b$dummy", is_active=True)


async def test_strangers_failed_logins_do_not_lock_out_a_different_real_client():
    """The headline defect from the audit, reproduced end to end: a
    stranger hammering /login must not be able to lock out the operator,
    even though pre-fix both would collapse onto nginx's one IP."""
    from app.routers import auth as auth_module

    auth_module._failed_attempts.clear()
    auth_module._lockouts.clear()

    app = _build_app(_make_user())
    async with _client_at(app, "198.51.100.66") as stranger, _client_at(app, "192.0.2.44") as operator:
        with patch.object(auth_module, "verify_password_async", new=AsyncMock(return_value=False)):
            for _ in range(5):
                resp = await stranger.post("/api/auth/login", json={"username": "operator", "password": "wrong"})
                assert resp.status_code == 401

            locked_resp = await stranger.post("/api/auth/login", json={"username": "operator", "password": "wrong"})
            assert locked_resp.status_code == 429, "the stranger's 6th attempt must be locked out"

            operator_resp = await operator.post("/api/auth/login", json={"username": "operator", "password": "wrong"})
            assert operator_resp.status_code == 401, (
                f"operator should NOT be locked out by the stranger's failures, "
                f"got {operator_resp.status_code}"
            )


async def test_successful_login_from_one_client_does_not_clear_anothers_lockout():
    """The inverse check: a successful login from the real operator must
    not be usable by an attacker to reset ITS OWN lockout clock via a
    shared bucket, and must not itself get blocked by an unrelated
    client's lockout."""
    from app.routers import auth as auth_module

    auth_module._failed_attempts.clear()
    auth_module._lockouts.clear()

    app = _build_app(_make_user())
    async with _client_at(app, "198.51.100.77") as attacker, _client_at(app, "192.0.2.55") as operator:
        with patch.object(auth_module, "verify_password_async", new=AsyncMock(return_value=False)):
            for _ in range(5):
                await attacker.post("/api/auth/login", json={"username": "operator", "password": "wrong"})
            locked = await attacker.post("/api/auth/login", json={"username": "operator", "password": "wrong"})
            assert locked.status_code == 429

        with patch.object(auth_module, "verify_password_async", new=AsyncMock(return_value=True)):
            ok = await operator.post("/api/auth/login", json={"username": "operator", "password": "correct"})
            assert ok.status_code == 200, f"operator's own login must succeed, got {ok.status_code}: {ok.text}"

        with patch.object(auth_module, "verify_password_async", new=AsyncMock(return_value=False)):
            still_locked = await attacker.post("/api/auth/login", json={"username": "operator", "password": "wrong"})
            assert still_locked.status_code == 429, "attacker's lockout must still be in effect"
