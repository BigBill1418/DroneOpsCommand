"""client_login's freshly-minted token must pass get_current_client (ADR-0045).

Found auditing the blast radius of the ADR-0045 revocation fix (Phase 7):
`POST /api/client/auth/login` (client_portal.py's `client_login`) mints a
JWT via `create_client_token(...)` that aggregates mission_ids across the
customer's existing active `ClientAccessToken` rows — but never recorded a
`ClientAccessToken` row for the NEW aggregated token itself. That was
invisible before because `get_current_client` never queried the table at
all. Once it does (so that revocation actually revokes — see
`test_client_portal_revocation.py`), every issuance path needs a matching
row or the token the customer was just handed 401s on its very first use.
`client_login` now creates that row, mirroring
`_get_or_create_client_link` / `_send_portal_email_for_mission`'s existing
shape exactly.

This is the regression test for that fix: a full round trip through
`client_login` THEN `get_current_client` with the token it returned.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.security import HTTPAuthorizationCredentials


class _ScalarOneOrNone:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def scalars(self):
        return _Scalars(self._value)


class _Scalars:
    def __init__(self, value):
        self._value = value

    def all(self):
        if self._value is None:
            return []
        return self._value if isinstance(self._value, list) else [self._value]


class _RoundTripSession:
    """FIFO-queue fake that also captures what `add()` records, so the
    SECOND request (get_current_client) can be handed back the row the
    FIRST request (client_login) just created — a real round trip through
    the DB layer's actual read-after-write contract, not an idealized
    fixture that pre-supplies the "right" answer independently."""

    def __init__(self, results):
        self._results = list(results)
        self.added: list = []

    async def execute(self, _stmt):
        return _ScalarOneOrNone(self._results.pop(0))

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        pass

    async def flush(self):
        pass


def _request():
    from starlette.requests import Request

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/client/auth/login",
        "headers": [],
        "client": ("203.0.113.5", 0),
        "query_string": b"",
        "server": ("testserver", 80),
        "scheme": "http",
        "root_path": "",
        "app": SimpleNamespace(state=SimpleNamespace()),
    }
    return Request(scope)


async def test_freshly_issued_login_token_is_accepted_by_get_current_client():
    from app.auth import client_auth
    from app.routers import client_portal

    customer_id = uuid.uuid4()
    mission_id = str(uuid.uuid4())
    customer = SimpleNamespace(
        id=customer_id,
        email="customer@example.com",
        portal_password_hash="$2b$dummy",
        name="Casey Customer",
    )
    existing_active_token = SimpleNamespace(
        mission_scope=[mission_id],
    )

    # client_login's query sequence: 1) Customer by email, 2) active
    # ClientAccessToken rows for the mission-scope aggregation.
    db = _RoundTripSession(results=[customer, [existing_active_token]])

    with patch.object(client_portal, "verify_password_async", new=AsyncMock(return_value=True)):
        login_resp = await client_portal.client_login(
            data=SimpleNamespace(email="customer@example.com", password="correct"),
            request=_request(),
            db=db,
        )

    assert login_resp.access_token
    assert len(db.added) == 1, "client_login must record a ClientAccessToken row for the token it just minted"
    created_row = db.added[0]
    assert created_row.mission_scope == [mission_id]
    assert created_row.revoked_at is None
    assert created_row.customer_id == customer_id

    # Now the round trip: hand that EXACT token to get_current_client, with
    # a session queued to answer ITS query sequence (Customer by id, then
    # ClientAccessToken by token_hash) using the customer + the row
    # client_login just created — not a hand-authored stand-in.
    from app.auth.client_auth import hash_token

    # Give the captured row the attributes get_current_client reads.
    created_row.id = uuid.uuid4()
    created_row.token_hash = hash_token(login_resp.access_token)
    created_row.revoked_at = None
    created_row.expires_at = datetime.utcnow() + timedelta(days=30)
    created_row.last_accessed_at = None

    auth_db = _RoundTripSession(results=[customer, created_row])
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=login_resp.access_token)

    ctx = await client_auth.get_current_client(credentials=creds, db=auth_db)
    assert ctx.customer_id == customer_id
    assert mission_id in ctx.mission_ids
