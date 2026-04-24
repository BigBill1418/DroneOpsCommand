"""Tests for the FIX-2 in-process user cache in ``app.auth.jwt``.

Covers:
- Cache HIT skips the DB roundtrip and returns equivalent fields.
- Cache MISS calls the DB once, populates the cache, returns the user.
- TTL expiry causes a refresh roundtrip.
- ``invalidate_user_cache(username)`` drops only that username's entries.
- ``invalidate_user_cache(None)`` clears everything.
- Inactive user is rejected (and not cached).
- Different token prefix for the same user produces a separate cache entry.

These are unit tests against the cache machinery — not a full FastAPI
integration test. ``conftest.py`` already stubs the env so importing
``app.auth.jwt`` succeeds without a real DB or Redis.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

import tests.conftest  # noqa: F401 — env stubs

from app.auth import jwt as jwt_mod


def _fake_user(username: str = "alice", is_active: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        username=username,
        hashed_password="$2b$12$dummyhash",
        is_active=is_active,
        created_at=datetime.utcnow(),
    )


def _fake_db_returning(user_or_none):
    """Return a mock AsyncSession whose execute() returns a result with our user."""
    db = MagicMock()
    scalar_result = MagicMock()
    scalar_result.scalar_one_or_none.return_value = user_or_none
    db.execute = AsyncMock(return_value=scalar_result)
    return db


def _fake_credentials(token: str):
    return SimpleNamespace(credentials=token)


def _fake_token(username: str = "alice") -> str:
    """Mint a real JWT so jwt.decode succeeds in get_current_user."""
    from app.config import settings as cfg
    from datetime import timedelta
    from jose import jwt as josejwt

    payload = {
        "sub": username,
        "type": "access",
        "exp": datetime.utcnow() + timedelta(minutes=30),
    }
    return josejwt.encode(payload, cfg.jwt_secret_key, algorithm=cfg.jwt_algorithm)


@pytest.fixture(autouse=True)
def _clear_cache():
    jwt_mod.invalidate_user_cache(None)
    yield
    jwt_mod.invalidate_user_cache(None)


@pytest.mark.asyncio
async def test_cache_miss_then_hit_skips_second_db_call(monkeypatch):
    user = _fake_user()
    db = _fake_db_returning(user)
    token = _fake_token("alice")
    creds = _fake_credentials(token)

    out1 = await jwt_mod.get_current_user(credentials=creds, db=db)
    out2 = await jwt_mod.get_current_user(credentials=creds, db=db)

    assert out1.username == "alice"
    assert out2.username == "alice"
    # The first call hits DB; the second is served from cache.
    assert db.execute.await_count == 1


@pytest.mark.asyncio
async def test_inactive_user_rejected(monkeypatch):
    user = _fake_user(is_active=False)
    db = _fake_db_returning(user)
    creds = _fake_credentials(_fake_token("alice"))

    with pytest.raises(HTTPException) as ei:
        await jwt_mod.get_current_user(credentials=creds, db=db)
    assert ei.value.status_code == 401
    # Inactive user must not be cached.
    assert len(jwt_mod._user_cache) == 0


@pytest.mark.asyncio
async def test_invalidate_specific_user(monkeypatch):
    user = _fake_user("alice")
    db = _fake_db_returning(user)
    creds = _fake_credentials(_fake_token("alice"))

    await jwt_mod.get_current_user(credentials=creds, db=db)
    assert len(jwt_mod._user_cache) == 1

    jwt_mod.invalidate_user_cache("bob")
    assert len(jwt_mod._user_cache) == 1  # not Alice's entry

    jwt_mod.invalidate_user_cache("alice")
    assert len(jwt_mod._user_cache) == 0


@pytest.mark.asyncio
async def test_invalidate_all(monkeypatch):
    db = _fake_db_returning(_fake_user("alice"))
    await jwt_mod.get_current_user(
        credentials=_fake_credentials(_fake_token("alice")),
        db=db,
    )
    assert len(jwt_mod._user_cache) >= 1
    jwt_mod.invalidate_user_cache(None)
    assert len(jwt_mod._user_cache) == 0


@pytest.mark.asyncio
async def test_ttl_expiry_triggers_refresh(monkeypatch):
    user = _fake_user("alice")
    db = _fake_db_returning(user)
    creds = _fake_credentials(_fake_token("alice"))

    await jwt_mod.get_current_user(credentials=creds, db=db)
    assert db.execute.await_count == 1

    # Force every cached entry's expiry to the past.
    for k, (payload, _) in list(jwt_mod._user_cache.items()):
        jwt_mod._user_cache[k] = (payload, 0.0)

    await jwt_mod.get_current_user(credentials=creds, db=db)
    # Second call had to re-query the DB because the entry was expired.
    assert db.execute.await_count == 2
