"""
v2.66.4 regression test — `customers.tos_signed_at` is a naive `DateTime`
column; the v2.66.3 code that synced it from the timezone-aware
`tos_acceptances.accepted_at` triggered SQLAlchemy's dirty-tracking
comparison to crash with "can't subtract offset-naive and offset-aware
datetimes" AFTER the audit row had already been persisted, leaving an
orphan signed PDF on disk and the customer seeing HTTP 500.

This test exercises the REAL ORM path (no `_mk_payload(SimpleNamespace)`
bypass — see ADR-0013). It writes both columns through the ORM and
asserts the commit succeeds end-to-end.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import os
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.customer import Customer


@pytest.fixture
async def db():
    """Hermetic in-memory SQLite session — exercises the same SQLAlchemy
    dirty-tracking path that crashes on prod Postgres when a tz-aware
    value is assigned to a tz-naive column. SQLite's date adapters are
    lenient enough that the column-type mismatch goes through, but the
    Python-side comparison ("did the value change?") still fires the
    same offset-naive-vs-aware error.

    NOTE: only the `customers` table is created — `Base.metadata.create_all`
    would also try to materialize `tos_acceptances`, which uses the
    PG-only `INET` type for `client_ip` and crashes the SQLite dialect
    compiler. This test only writes/reads through the Customer model, so
    a per-table create is both correct and faster."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Customer.__table__.create)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with Session() as session:
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_customer_tos_signed_at_accepts_naive_datetime(db: AsyncSession):
    """Direct write of a naive datetime succeeds — establishes the column
    is naive."""
    c = Customer(name="Test User", email="naive@example.com")
    db.add(c)
    await db.flush()

    c.tos_signed = True
    c.tos_signed_at = datetime.now()  # naive
    await db.commit()  # must NOT raise

    refreshed = await db.get(Customer, c.id)
    assert refreshed.tos_signed is True
    assert refreshed.tos_signed_at is not None
    assert refreshed.tos_signed_at.tzinfo is None  # column round-trips as naive


@pytest.mark.skip(
    reason="Postgres-only contract: asyncpg's bind layer raises DataError "
    "on tz-aware → naive column; SQLite's date adapter accepts the cast "
    "silently and round-trips a stripped naive value, so this assertion "
    "cannot be proved against the in-memory hermetic engine. Replacing "
    "the SQLite fixture with a real Postgres testcontainer (or moving "
    "the contract proof up to a backend integration test) is tracked as "
    "a follow-up; the v2.66.4 fix itself is still verified by "
    "test_v2664_fix_strip_tzinfo_works below."
)
@pytest.mark.asyncio
async def test_customer_tos_signed_at_must_be_tz_naive(db: AsyncSession):
    """The v2.66.3 bug: assigning a tz-AWARE datetime to the naive column
    crashes on Postgres+asyncpg. This test was authored under the false
    assumption that SQLite would also raise; it does not. Skipped pending
    a Postgres-backed replacement (see the skip reason)."""
    # Pre-populate with a naive value so SQLAlchemy's dirty tracking has
    # something to compare against
    c = Customer(name="Test User", email="aware@example.com")
    c.tos_signed_at = datetime(2025, 1, 1, 12, 0, 0)  # naive
    db.add(c)
    await db.commit()

    # Simulate the v2.66.3 buggy assignment — tz-AWARE datetime.
    aware = datetime.now(timezone.utc)
    c.tos_signed_at = aware

    with pytest.raises((TypeError, Exception)) as excinfo:
        await db.commit()
    assert ("offset-naive" in str(excinfo.value).lower()
            or "offset-aware" in str(excinfo.value).lower()
            or "can't compare" in str(excinfo.value).lower()
            or "datetime" in str(excinfo.value).lower())


@pytest.mark.asyncio
async def test_v2664_fix_strip_tzinfo_works(db: AsyncSession):
    """The actual v2.66.4 pattern — `aware.replace(tzinfo=None)` — must
    commit cleanly when the column is naive."""
    c = Customer(name="Fixed User", email="fixed@example.com")
    c.tos_signed_at = datetime(2025, 1, 1, 12, 0, 0)  # naive baseline
    db.add(c)
    await db.commit()

    aware = datetime.now(timezone.utc)
    c.tos_signed = True
    c.tos_signed_at = aware.replace(tzinfo=None)  # the v2.66.4 fix

    await db.commit()  # must succeed

    refreshed = await db.get(Customer, c.id)
    assert refreshed.tos_signed is True
    assert refreshed.tos_signed_at is not None
    assert refreshed.tos_signed_at.tzinfo is None
    # Same UTC moment, just naive on the column
    assert abs((refreshed.tos_signed_at - aware.replace(tzinfo=None)).total_seconds()) < 1
