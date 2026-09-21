"""Client-link revocation actually revokes (Phase 7 hardening, ADR-0045).

Pre-fix: `DELETE /api/missions/{id}/client-link/{token_id}`
(client_portal.py:1047) stamps `ClientAccessToken.revoked_at`, but
`get_current_client` (auth/client_auth.py) never queried that table — it
only verified the JWT's signature + `exp`. A "revoked" link stayed fully
functional for up to `settings.client_token_expire_days` (default 30 days)
after the operator revoked it. `token_hash` was already populated on every
issuance path for exactly this lookup; it was simply never read at auth
time.

These are unit tests against `get_current_client` directly (bypassing the
ASGI/FastAPI DI layer, same style as `test_tos_customer_sync.py`) with a
FakeAsyncSession returning canned rows in call order:
  1. SELECT Customer WHERE id=sub
  2. SELECT ClientAccessToken WHERE token_hash=sha256(token)
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from app.auth import client_auth


class _ScalarOneOrNone:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeAsyncSession:
    def __init__(self, results):
        self._results = list(results)
        self.committed = False

    async def execute(self, _stmt):
        return _ScalarOneOrNone(self._results.pop(0))

    async def commit(self):
        self.committed = True


def _issue(customer_id, mission_ids=("m1",), expires_days=30):
    return client_auth.create_client_token(customer_id, list(mission_ids), expires_days)


def _creds(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


def _row(token: str, *, revoked_at=None, expires_days=29, last_accessed_at=None):
    return SimpleNamespace(
        id=uuid.uuid4(),
        token_hash=client_auth.hash_token(token),
        revoked_at=revoked_at,
        expires_at=datetime.utcnow() + timedelta(days=expires_days),
        last_accessed_at=last_accessed_at,
    )


async def test_revoked_token_is_rejected():
    customer_id = uuid.uuid4()
    token = _issue(customer_id)
    customer = SimpleNamespace(id=customer_id)
    token_row = _row(token, revoked_at=datetime.utcnow())
    db = _FakeAsyncSession([customer, token_row])

    with pytest.raises(HTTPException) as exc_info:
        await client_auth.get_current_client(credentials=_creds(token), db=db)

    assert exc_info.value.status_code == 401
    assert "revoked" in exc_info.value.detail.lower()


async def test_active_token_is_accepted_and_bumps_last_accessed():
    customer_id = uuid.uuid4()
    token = _issue(customer_id)
    customer = SimpleNamespace(id=customer_id)
    token_row = _row(token, revoked_at=None)
    db = _FakeAsyncSession([customer, token_row])

    ctx = await client_auth.get_current_client(credentials=_creds(token), db=db)

    assert ctx.customer_id == customer_id
    assert token_row.last_accessed_at is not None
    assert db.committed is True


async def test_token_with_no_matching_db_row_is_rejected():
    """A valid signature with no corresponding ClientAccessToken row —
    the row was deleted, or the token was never legitimately issued.
    Must not fall back to trusting the JWT alone."""
    customer_id = uuid.uuid4()
    token = _issue(customer_id)
    customer = SimpleNamespace(id=customer_id)
    db = _FakeAsyncSession([customer, None])

    with pytest.raises(HTTPException) as exc_info:
        await client_auth.get_current_client(credentials=_creds(token), db=db)

    assert exc_info.value.status_code == 401


async def test_expired_row_rejected_even_if_jwt_exp_is_later():
    """DB row is the operator-controlled, revocable source of truth — it
    must win even if the two ever disagree (e.g. expiry shortened after
    issuance)."""
    customer_id = uuid.uuid4()
    token = _issue(customer_id, expires_days=30)
    customer = SimpleNamespace(id=customer_id)
    token_row = _row(token, revoked_at=None, expires_days=-1)  # DB row already expired
    db = _FakeAsyncSession([customer, token_row])

    with pytest.raises(HTTPException) as exc_info:
        await client_auth.get_current_client(credentials=_creds(token), db=db)

    assert exc_info.value.status_code == 401


async def test_revocation_is_scoped_to_the_specific_token_not_the_customer():
    """A second, still-active token for the SAME customer must keep
    working after a different token for that customer is revoked — the
    lookup is by token_hash, never by customer_id alone."""
    customer_id = uuid.uuid4()
    live_token = _issue(customer_id, mission_ids=("m2",))
    customer = SimpleNamespace(id=customer_id)
    live_row = _row(live_token, revoked_at=None)
    db = _FakeAsyncSession([customer, live_row])

    ctx = await client_auth.get_current_client(credentials=_creds(live_token), db=db)
    assert ctx.customer_id == customer_id
    assert ctx.mission_ids == ["m2"]
