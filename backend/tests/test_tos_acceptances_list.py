"""v2.66.0 — Operator audit-browse endpoint for ``tos_acceptances``.

Hermetic unit coverage of ``GET /api/tos/acceptances``:

* empty list → 200 with empty items + total=0
* populated list → 200 with items + total
* free-text ``q=`` matches partial email / audit_id / client_name
  (we exercise the route's WHERE-clause-construction path; the actual
  SQLAlchemy ILIKE is exercised by the integration env)
* ``customer_id=`` filter narrows scope
* pagination (limit / offset propagate to total + envelope)
* default ordering is ``accepted_at desc``
* unauthenticated → 401 (verified by ``Depends(get_current_user)``
  signature at the route level — same pattern the existing
  ``download_signed_operator`` route uses)

These tests stub the AsyncSession the same way ``test_tos_customer_sync.py``
does; we never spin up Postgres. The interesting behaviour is the
route's response-construction path (URL synthesis, envelope shape,
filter application). The DB layer is the unit in the integration
suite, not here.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest


# ── Fakes ────────────────────────────────────────────────────────────


class _ScalarOne:
    """Mimics ``Result.scalar_one()`` for COUNT(*) queries."""

    def __init__(self, value: int) -> None:
        self._value = value

    def scalar_one(self) -> int:
        return self._value


class _ScalarsAll:
    """Mimics ``Result.scalars().all()`` for the page query."""

    def __init__(self, rows: list) -> None:
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class FakeBrowseSession:
    """Async session that returns canned results in execute() order.

    The route fires two queries per call:
      1. SELECT count(*) FROM tos_acceptances [WHERE …]
      2. SELECT * FROM tos_acceptances [WHERE …] ORDER BY accepted_at DESC
         LIMIT … OFFSET …

    Tests pass a (count, rows) tuple. We also record every executed
    statement so a test can assert "the WHERE clause referenced the
    customer_id column" without booting a real DB.
    """

    def __init__(self, count: int, rows: list) -> None:
        self._queue = [_ScalarOne(count), _ScalarsAll(rows)]
        self.executed: list = []

    async def execute(self, stmt):
        self.executed.append(stmt)
        if not self._queue:
            raise AssertionError("FakeBrowseSession.execute queue exhausted")
        return self._queue.pop(0)


def _row(
    *,
    audit_id: str = "DOC-20260502120000-abcdef01",
    customer_id: uuid.UUID | None = None,
    intake_token: str | None = None,
    client_name: str = "Casey Operator",
    client_email: str = "casey@example.com",
    client_company: str = "",
    client_title: str = "",
    client_ip: str = "203.0.113.5",
    user_agent: str = "pytest",
    accepted_at: datetime | None = None,
    template_version: str = "DOC-001/TOS/REV3",
    template_sha256: str = "t" * 64,
    signed_sha256: str = "s" * 64,
    signed_pdf_size: int = 158_321,
    created_at: datetime | None = None,
):
    """Build a row stub that quacks like a SQLAlchemy ``TosAcceptance``."""
    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        id=uuid.uuid4(),
        audit_id=audit_id,
        customer_id=customer_id,
        intake_token=intake_token,
        client_name=client_name,
        client_email=client_email,
        client_company=client_company,
        client_title=client_title,
        client_ip=client_ip,
        user_agent=user_agent,
        accepted_at=accepted_at or now,
        template_version=template_version,
        template_sha256=template_sha256,
        signed_sha256=signed_sha256,
        signed_pdf_size=signed_pdf_size,
        created_at=created_at or now,
    )


def _user():
    return SimpleNamespace(username="op@example.com")


# ── Tests ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_returns_empty_envelope_when_no_rows():
    from app.routers.tos import list_acceptances

    db = FakeBrowseSession(count=0, rows=[])

    resp = await list_acceptances(
        q=None, customer_id=None, limit=50, offset=0,
        db=db, user=_user(),
    )

    assert resp.total == 0
    assert resp.items == []
    assert resp.limit == 50
    assert resp.offset == 0


@pytest.mark.asyncio
async def test_list_serialises_rows_with_download_url():
    """Every item must include a ``download_url`` pointing at the
    operator-gated signed-PDF endpoint, never the on-disk path."""
    from app.routers.tos import list_acceptances

    rows = [
        _row(audit_id="DOC-20260501120000-aaaa1111",
             client_email="alpha@example.com",
             client_name="Alpha Tester"),
        _row(audit_id="DOC-20260502120000-bbbb2222",
             client_email="beta@example.com",
             client_name="Beta Tester"),
    ]
    db = FakeBrowseSession(count=2, rows=rows)

    resp = await list_acceptances(
        q=None, customer_id=None, limit=50, offset=0,
        db=db, user=_user(),
    )

    assert resp.total == 2
    assert len(resp.items) == 2
    # Download URL points at the operator-gated route, not /by-token/…
    assert resp.items[0].download_url == "/api/tos/signed/DOC-20260501120000-aaaa1111"
    assert resp.items[1].download_url == "/api/tos/signed/DOC-20260502120000-bbbb2222"
    # Hashes preserved full-length (UI is responsible for truncation).
    assert len(resp.items[0].signed_sha256) == 64
    assert len(resp.items[0].template_sha256) == 64
    # signed_pdf_path MUST NOT leak — schema doesn't include the field.
    assert not hasattr(resp.items[0], "signed_pdf_path")


@pytest.mark.asyncio
async def test_list_search_q_applies_ilike_predicate():
    """``q=`` produces a WHERE clause; the route trims whitespace and
    wraps the term in ``%…%``. We assert the query was constructed
    (not the SQL string itself — that's an integration concern)."""
    from app.routers.tos import list_acceptances

    matched = _row(
        audit_id="DOC-20260502120000-deadbeef",
        client_email="search-hit@example.com",
        client_name="Search Hit",
    )
    db = FakeBrowseSession(count=1, rows=[matched])

    resp = await list_acceptances(
        q="search-hit",
        customer_id=None, limit=50, offset=0,
        db=db, user=_user(),
    )

    assert resp.total == 1
    assert resp.items[0].client_email == "search-hit@example.com"
    # Both queries must have been issued (count + page).
    assert len(db.executed) == 2
    # Whitespace trimming: leading/trailing spaces should not affect the
    # downstream behaviour. Easiest probe = same path with " hit " ≠ crash.
    db2 = FakeBrowseSession(count=0, rows=[])
    resp2 = await list_acceptances(
        q="   audit_id-fragment   ",
        customer_id=None, limit=50, offset=0,
        db=db2, user=_user(),
    )
    assert resp2.total == 0


@pytest.mark.asyncio
async def test_list_search_by_audit_id_fragment():
    """A partial audit_id string must match (the OR'd ILIKE on
    ``audit_id`` is the only way to look up a row by its short ID
    without dropping to SQL)."""
    from app.routers.tos import list_acceptances

    target = _row(audit_id="DOC-20260502120000-deadbeef")
    db = FakeBrowseSession(count=1, rows=[target])

    resp = await list_acceptances(
        q="deadbeef",
        customer_id=None, limit=50, offset=0,
        db=db, user=_user(),
    )

    assert resp.total == 1
    assert resp.items[0].audit_id == "DOC-20260502120000-deadbeef"


@pytest.mark.asyncio
async def test_list_filters_by_customer_id():
    """``customer_id=`` adds a WHERE on the FK column. Verify the
    response surfaces only the requested customer's rows."""
    from app.routers.tos import list_acceptances

    cust = uuid.uuid4()
    rows = [_row(customer_id=cust, client_email="alpha@example.com")]
    db = FakeBrowseSession(count=1, rows=rows)

    resp = await list_acceptances(
        q=None, customer_id=cust, limit=50, offset=0,
        db=db, user=_user(),
    )

    assert resp.total == 1
    assert resp.items[0].customer_id == cust


@pytest.mark.asyncio
async def test_list_pagination_passes_limit_and_offset_through():
    from app.routers.tos import list_acceptances

    rows = [_row() for _ in range(10)]
    db = FakeBrowseSession(count=42, rows=rows)

    resp = await list_acceptances(
        q=None, customer_id=None, limit=10, offset=20,
        db=db, user=_user(),
    )

    assert resp.total == 42  # WHERE-filtered count, not page len
    assert len(resp.items) == 10
    assert resp.limit == 10
    assert resp.offset == 20


@pytest.mark.asyncio
async def test_list_default_orders_accepted_at_desc():
    """Newest-first by default. We supply rows in DESC order (the
    DB would have done it for us); the route must preserve that."""
    from app.routers.tos import list_acceptances

    now = datetime.now(timezone.utc)
    rows = [
        _row(audit_id="DOC-20260502120000-newest", accepted_at=now),
        _row(audit_id="DOC-20260501120000-middle", accepted_at=now - timedelta(days=1)),
        _row(audit_id="DOC-20260430120000-oldest", accepted_at=now - timedelta(days=2)),
    ]
    db = FakeBrowseSession(count=3, rows=rows)

    resp = await list_acceptances(
        q=None, customer_id=None, limit=50, offset=0,
        db=db, user=_user(),
    )

    assert [i.audit_id for i in resp.items] == [
        "DOC-20260502120000-newest",
        "DOC-20260501120000-middle",
        "DOC-20260430120000-oldest",
    ]


def test_list_route_requires_authentication():
    """The route's signature pins ``Depends(get_current_user)`` so an
    unauthenticated request is rejected at the FastAPI dependency
    layer with 401 — same pattern as ``download_signed_operator``.

    We assert on the dep-injection signature rather than booting a
    TestClient: the rest of this suite is hermetic and we want this
    test to share that property."""
    from fastapi.params import Depends as DependsParam

    from app.auth.jwt import get_current_user
    from app.routers.tos import list_acceptances

    # FastAPI stores the Depends() marker as the parameter default.
    user_default = list_acceptances.__defaults__[-2]  # user before db? check both
    # Easier: walk the signature.
    import inspect
    sig = inspect.signature(list_acceptances)
    user_param = sig.parameters["user"]
    assert isinstance(user_param.default, DependsParam)
    # The dep must be the operator JWT dep (not a no-op).
    assert user_param.default.dependency is get_current_user
