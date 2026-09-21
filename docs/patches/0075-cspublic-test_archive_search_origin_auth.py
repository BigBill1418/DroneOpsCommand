"""Origin-side Worker->origin auth for archive search (ADR-0075, Wave 2B).

`cs-api.barnardhq.com` (this origin's own tunnel hostname) answers
`/api/archive/search` directly — the cloudflared ingress allowlist has to
admit it, because the Worker itself fetches through that hostname. Nothing
distinguished a request relayed by the Worker (past Turnstile, past the
per-IP limiter) from one that hit the origin straight from the internet.
Verified live from BOS-HQ, outside every Cloudflare bypass: `GET
/api/archive/search` on the origin hostname answered a real result with no
Turnstile challenge in the loop.

`_require_worker_bearer` mirrors `ingest.py`'s already-tested
`_require_receiver_bearer` exactly (settings-store token,
`hmac.compare_digest`, 503 when unconfigured so the operator's log points
at the missing settings row rather than a bearer mismatch) — these tests
mirror `test_ingest_transmission.py`'s own coverage of that function one
for one.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.api.archive import _require_worker_bearer
from tests.conftest import seed_settings


async def test_unconfigured_origin_token_returns_503(bound_db, db):
    with pytest.raises(HTTPException) as ei:
        await _require_worker_bearer("Bearer whatever")
    assert ei.value.status_code == 503  # points the operator at the settings row, not a 401


async def test_wrong_bearer_returns_401(bound_db, session_factory):
    await seed_settings(session_factory, {"search.worker_origin_token": "s3cret"})
    with pytest.raises(HTTPException) as ei:
        await _require_worker_bearer("Bearer wrong")
    assert ei.value.status_code == 401


async def test_missing_authorization_header_returns_401(bound_db, session_factory):
    await seed_settings(session_factory, {"search.worker_origin_token": "s3cret"})
    with pytest.raises(HTTPException) as ei:
        await _require_worker_bearer("")
    assert ei.value.status_code == 401


async def test_correct_bearer_is_accepted(bound_db, session_factory):
    await seed_settings(session_factory, {"search.worker_origin_token": "s3cret"})
    # No exception raised == accepted.
    await _require_worker_bearer("Bearer s3cret")


async def test_correct_secret_wrong_scheme_is_rejected(bound_db, session_factory):
    """`Basic s3cret` or a bare `s3cret` (no `Bearer ` prefix) must not
    accidentally match — the comparison is against the literal
    `f"Bearer {token}"` string, not just the token substring."""
    await seed_settings(session_factory, {"search.worker_origin_token": "s3cret"})
    with pytest.raises(HTTPException) as ei:
        await _require_worker_bearer("s3cret")
    assert ei.value.status_code == 401
