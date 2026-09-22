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

ADR-0048 adds SERVICE_ACCOUNT_USERNAMES on top: a comma-separated allow-list
that lets named machine callers keep using login + refresh while the switch
is on, because they reach this API over WireGuard and cannot hold a
Cloudflare Access cookie. Blank by default, so every assertion above still
describes the shipped default. The allow-list is exempt from the Step B 403
and from nothing else, which is what most of the ADR-0048 tests below pin
down: wrong password is still 401, setup/account-PUT are still 403, the
subject for refresh still comes from the signed token, and a name that is
not on the list gets the same 403 whether or not it exists.
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
    monkeypatch.setattr(settings, "service_account_usernames", "")
    # Two separate pieces of 429-producing state are module-level in
    # app/routers/auth.py and therefore shared by every test in the process:
    # the login lockout's dicts, and the slowapi limiter's in-memory storage
    # (the @limiter.limit decorator binds that module-level Limiter at import
    # time — giving each test app its own app.state.limiter does NOT isolate
    # it; measured, not assumed). Several tests below deliberately fail a
    # password, and the module as a whole exceeds 10 logins/minute, so
    # without this reset the failure lands on whichever test happens to run
    # once the shared budget runs out.
    from app.routers import auth as auth_mod
    def _reset_429_state():
        auth_mod._failed_attempts.clear()
        auth_mod._lockouts.clear()
        auth_mod.limiter.reset()
    _reset_429_state()
    yield
    _reset_429_state()


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


# ── ADR-0048 — service-account allow-list ────────────────────────────────
#
# Every test here runs with local_login_disabled=True. That is the whole
# point: with the switch off the allow-list is unreachable code, and the
# "no-op" block at the top of this file already pins that state.

SERVICE = "marketing-bridge"
SERVICE_2 = "droneopsmap-bridge"
BOTH = f"{SERVICE},{SERVICE_2}"


@pytest.fixture
def _disabled_with_allowlist(monkeypatch):
    """LOCAL_LOGIN_DISABLED=true, both real machine callers allow-listed."""
    monkeypatch.setattr(settings, "local_login_disabled", True)
    monkeypatch.setattr(settings, "service_account_usernames", BOTH)


# ── The allow-list parse itself ─────────────────────────────────────────


def test_allowlist_defaults_to_empty():
    """The shipped default. An empty set exempts nobody, so a fresh install
    and the public demo instance behave exactly as they did pre-ADR-0048."""
    assert settings.service_account_allowlist == frozenset()


def test_allowlist_strips_whitespace_and_drops_blanks(monkeypatch):
    monkeypatch.setattr(
        settings, "service_account_usernames", f"  {SERVICE} ,\t{SERVICE_2}  ,, ,"
    )
    assert settings.service_account_allowlist == frozenset({SERVICE, SERVICE_2})


def test_allowlist_parse_preserves_case(monkeypatch):
    """Stripping must not also normalise case. Folding entries here while
    comparing a raw username would silently un-allow-list a mixed-case
    account — `users.username` is byte-exact, so the operator's entry has to
    survive verbatim."""
    monkeypatch.setattr(settings, "service_account_usernames", " Marketing-Bridge ")
    assert settings.service_account_allowlist == frozenset({"Marketing-Bridge"})


def test_allowlist_of_separators_only_is_empty_not_a_wildcard(monkeypatch):
    """Fail closed: a malformed value must block, never open."""
    monkeypatch.setattr(settings, "service_account_usernames", " , ,,\t,")
    assert settings.service_account_allowlist == frozenset()


# ── Login: allow-listed accounts get through ────────────────────────────


async def test_allowlisted_service_account_can_login_when_disabled(_disabled_with_allowlist):
    resp = await _request(
        _build_app(_fake_user(SERVICE)), "POST", "/api/auth/login",
        json={"username": SERVICE, "password": "CorrectHorse1!"},
    )
    assert resp.status_code == 200, resp.text
    assert "access_token" in resp.json()


async def test_second_allowlisted_account_also_gets_through(_disabled_with_allowlist):
    """Both entries of a two-name list are live — not just the first."""
    resp = await _request(
        _build_app(_fake_user(SERVICE_2)), "POST", "/api/auth/login",
        json={"username": SERVICE_2, "password": "CorrectHorse1!"},
    )
    assert resp.status_code == 200, resp.text


async def test_whitespace_padded_allowlist_entry_still_logs_in(monkeypatch):
    monkeypatch.setattr(settings, "local_login_disabled", True)
    monkeypatch.setattr(settings, "service_account_usernames", f"  {SERVICE} , {SERVICE_2} ")
    resp = await _request(
        _build_app(_fake_user(SERVICE)), "POST", "/api/auth/login",
        json={"username": SERVICE, "password": "CorrectHorse1!"},
    )
    assert resp.status_code == 200, resp.text


# ── Login: the exemption is from the 403 and from NOTHING else ──────────


async def test_allowlisted_account_with_wrong_password_is_401_not_200_or_403(
    _disabled_with_allowlist,
):
    """The single most important assertion in this file. An allow-list entry
    buys a service account the right to PRESENT a password, never the right
    to skip checking it. 200 here would mean the allow-list authenticates;
    403 would mean the guard never let the password check run at all."""
    resp = await _request(
        _build_app(_fake_user(SERVICE)), "POST", "/api/auth/login",
        json={"username": SERVICE, "password": "WrongHorse9!"},
    )
    assert resp.status_code == 401, resp.text


async def test_allowlisted_but_deactivated_account_is_401(_disabled_with_allowlist):
    """is_active is still enforced — deactivating a machine account is still
    how an operator revokes it."""
    user = _fake_user(SERVICE)
    user.is_active = False
    resp = await _request(
        _build_app(user), "POST", "/api/auth/login",
        json={"username": SERVICE, "password": "CorrectHorse1!"},
    )
    assert resp.status_code == 401, resp.text


async def test_allowlisted_account_still_hits_the_ip_lockout(_disabled_with_allowlist):
    """Five bad passwords still lock the IP out — the allow-list does not
    buy an attacker an unthrottled password-guessing oracle against the one
    account whose name they now know is exempt."""
    app = _build_app(_fake_user(SERVICE))
    for _ in range(5):
        resp = await _request(
            app, "POST", "/api/auth/login",
            json={"username": SERVICE, "password": "WrongHorse9!"},
        )
        assert resp.status_code == 401, resp.text
    locked = await _request(
        app, "POST", "/api/auth/login",
        json={"username": SERVICE, "password": "CorrectHorse1!"},
    )
    assert locked.status_code == 429, locked.text
    # Two layers answer 429 on this route. Name the one under test: the
    # lockout raises an HTTPException with a `detail`, slowapi returns
    # {"error": "Rate limit exceeded: ..."}. Without this the assertion
    # above would pass on six requests that never reached the lockout.
    assert "Too many failed attempts" in locked.json()["detail"]


# ── Login: everyone else still gets 403 ─────────────────────────────────


async def test_non_allowlisted_user_still_403s_on_login(_disabled_with_allowlist):
    resp = await _request(
        _build_app(_fake_user("admin")), "POST", "/api/auth/login",
        json={"username": "admin", "password": "CorrectHorse1!"},
    )
    assert resp.status_code == 403, resp.text


async def test_non_allowlisted_name_403s_identically_whether_or_not_it_exists(
    _disabled_with_allowlist,
):
    """No user-enumeration regression: the guard runs before the row is read,
    so a present account and an absent one are indistinguishable."""
    present = await _request(
        _build_app(_fake_user("admin")), "POST", "/api/auth/login",
        json={"username": "admin", "password": "CorrectHorse1!"},
    )
    absent = await _request(
        _build_app(None), "POST", "/api/auth/login",
        json={"username": "no-such-user", "password": "CorrectHorse1!"},
    )
    assert present.status_code == absent.status_code == 403
    assert present.json() == absent.json()


async def test_allowlist_match_is_case_sensitive(_disabled_with_allowlist):
    """`User.username` is a Postgres varchar compared byte-exact, so a
    case-folding allow-list would wave a name past the gate that the lookup
    could never match. Belt: the fake DB returns its seeded user for ANY
    query, so a case-insensitive implementation would answer 200 here."""
    resp = await _request(
        _build_app(_fake_user(SERVICE)), "POST", "/api/auth/login",
        json={"username": SERVICE.upper(), "password": "CorrectHorse1!"},
    )
    assert resp.status_code == 403, resp.text


async def test_empty_allowlist_reproduces_pre_adr0048_login_behaviour(monkeypatch):
    """The shipped default, stated as a behavioural contract: with the
    allow-list blank, the name that WOULD be exempt is refused like any
    other — byte-identical to the ADR-0047 response."""
    monkeypatch.setattr(settings, "local_login_disabled", True)
    monkeypatch.setattr(settings, "service_account_usernames", "")
    resp = await _request(
        _build_app(_fake_user(SERVICE)), "POST", "/api/auth/login",
        json={"username": SERVICE, "password": "CorrectHorse1!"},
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"] == "Local login is disabled on this instance — sign in via SSO"


# ── Refresh: DocClient's token rotation must survive Step B ─────────────


async def test_allowlisted_service_account_can_refresh_when_disabled(_disabled_with_allowlist):
    resp = await _request(
        _build_app(_fake_user(SERVICE)), "POST", "/api/auth/refresh",
        json={"refresh_token": _refresh_token(SERVICE)},
    )
    assert resp.status_code == 200, resp.text
    assert "access_token" in resp.json()


async def test_non_allowlisted_user_still_403s_on_refresh(_disabled_with_allowlist):
    resp = await _request(
        _build_app(_fake_user("admin")), "POST", "/api/auth/refresh",
        json={"refresh_token": _refresh_token("admin")},
    )
    assert resp.status_code == 403, resp.text


async def test_empty_allowlist_reproduces_pre_adr0048_refresh_behaviour(monkeypatch):
    monkeypatch.setattr(settings, "local_login_disabled", True)
    monkeypatch.setattr(settings, "service_account_usernames", "")
    resp = await _request(
        _build_app(_fake_user(SERVICE)), "POST", "/api/auth/refresh",
        json={"refresh_token": _refresh_token(SERVICE)},
    )
    assert resp.status_code == 403, resp.text


async def test_refresh_subject_comes_from_the_token_not_the_request_body(
    _disabled_with_allowlist,
):
    """A client-supplied username must not be able to claim the exemption.
    The body carries an allow-listed name; the SIGNED token says `admin`."""
    resp = await _request(
        _build_app(_fake_user("admin")), "POST", "/api/auth/refresh",
        json={"refresh_token": _refresh_token("admin"), "username": SERVICE, "sub": SERVICE},
    )
    assert resp.status_code == 403, resp.text


async def test_refresh_with_a_foreign_signed_token_is_not_exempt(_disabled_with_allowlist):
    """A token minted with a different secret cannot buy the exemption: it
    never decodes, so no subject is ever established."""
    from jose import jwt as josejwt

    forged = josejwt.encode(
        {"sub": SERVICE, "type": "refresh", "exp": datetime.utcnow() + timedelta(days=30)},
        "not-the-real-signing-key",
        algorithm=settings.jwt_algorithm,
    )
    resp = await _request(
        _build_app(_fake_user(SERVICE)), "POST", "/api/auth/refresh",
        json={"refresh_token": forged},
    )
    assert resp.status_code == 401, resp.text


async def test_refresh_rejects_an_access_token_presented_as_a_refresh_token(
    _disabled_with_allowlist,
):
    """The `type` claim check still runs ahead of the allow-list."""
    resp = await _request(
        _build_app(_fake_user(SERVICE)), "POST", "/api/auth/refresh",
        json={"refresh_token": _bearer_token(SERVICE)},
    )
    assert resp.status_code == 401, resp.text


async def test_undecodable_refresh_token_is_401_when_disabled(_disabled_with_allowlist):
    """The one deliberate ADR-0048 behaviour change, pinned so it stays
    deliberate: the ADR-0047 guard answered 403 to a garbage token because it
    ran before the decode. It now needs a subject, so garbage answers 401.
    Nothing about any user is revealed either way."""
    resp = await _request(
        _build_app(_fake_user(SERVICE)), "POST", "/api/auth/refresh",
        json={"refresh_token": "not-a-jwt"},
    )
    assert resp.status_code == 401, resp.text


async def test_allowlisted_refresh_for_a_deactivated_account_is_401(_disabled_with_allowlist):
    user = _fake_user(SERVICE)
    user.is_active = False
    resp = await _request(
        _build_app(user), "POST", "/api/auth/refresh",
        json={"refresh_token": _refresh_token(SERVICE)},
    )
    assert resp.status_code == 401, resp.text


# ── The allow-list buys a LOGIN, never a credential ─────────────────────


async def test_setup_still_403s_for_an_allowlisted_name(_disabled_with_allowlist):
    """POST /setup MINTS the first credential — hard blocked, no exemption."""
    resp = await _request(
        _build_app(None), "POST", "/api/auth/setup",
        json={"username": SERVICE, "password": "CorrectHorse1!"},
    )
    assert resp.status_code == 403, resp.text


async def test_account_put_still_403s_for_an_allowlisted_service_account(
    _disabled_with_allowlist,
):
    """PUT /account MUTATES a credential. An allow-listed service account
    holding a perfectly valid bearer token is still refused — the allow-list
    must not become a path to rotating a password out from under an operator."""
    user = _fake_user(SERVICE)
    app = _build_app(user)

    from app.auth.jwt import get_current_user
    app.dependency_overrides[get_current_user] = lambda: user

    resp = await _request(
        app, "PUT", "/api/auth/account",
        json={"current_password": "CorrectHorse1!", "new_password": "SomethingNew1!"},
    )
    assert resp.status_code == 403, resp.text


async def test_setup_status_does_not_leak_the_allowlist(_disabled_with_allowlist):
    """The public status endpoint may say local login is off; it may not
    hand an unauthenticated caller the names that are still exempt."""
    resp = await _request(_build_app(None), "GET", "/api/auth/setup-status")
    body = resp.json()
    assert body["local_login_disabled"] is True
    assert SERVICE not in resp.text and SERVICE_2 not in resp.text


# ── With the switch OFF the allow-list changes nothing ──────────────────


async def test_allowlist_is_inert_while_local_login_is_enabled(monkeypatch):
    """Populating the list on a normal install must not alter anything: the
    guard returns before it is ever consulted."""
    monkeypatch.setattr(settings, "service_account_usernames", BOTH)
    ok = await _request(
        _build_app(_fake_user("admin")), "POST", "/api/auth/login",
        json={"username": "admin", "password": "CorrectHorse1!"},
    )
    assert ok.status_code == 200, ok.text
    bad = await _request(
        _build_app(_fake_user(SERVICE)), "POST", "/api/auth/login",
        json={"username": SERVICE, "password": "WrongHorse9!"},
    )
    assert bad.status_code == 401, bad.text
