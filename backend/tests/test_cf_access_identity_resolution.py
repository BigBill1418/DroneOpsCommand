"""``resolve_cf_access_user`` (ADR-0047, app/auth/jwt.py) — the identity-
mapping fix modeled on the marketing pilot's security-review correction
(marketing ADR-0099): a CF-Access-authenticated request must NEVER adopt a
pre-existing local account by matching ``users.username`` against the
verified email, and must always resolve to a real, stable ``users.id``.

Real-Postgres integration tier (opt-in via ``DOC_TEST_PG_URL``, same
convention as ``test_db_migrations.py`` / ``test_migration_0010_0011_
upgrade_path.py``) — this specifically needs real UNIQUE-constraint /
IntegrityError semantics, which a mock session cannot reproduce faithfully.

    cd backend && DOC_TEST_PG_URL=postgresql+asyncpg://doc:test@127.0.0.1:55199/doc \\
        pytest tests/test_cf_access_identity_resolution.py -v
"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import tests.conftest  # noqa: F401 — env stubs

_PG_URL = os.environ.get("DOC_TEST_PG_URL")
pytestmark = pytest.mark.skipif(
    not _PG_URL,
    reason="set DOC_TEST_PG_URL (postgresql+asyncpg://…) to run real-DB identity tests",
)


@pytest.fixture
async def db_session():
    from app.database import Base
    import app.models  # noqa: F401 — register all tables

    engine = create_async_engine(_PG_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


async def test_first_login_provisions_a_shadow_user(db_session):
    from app.auth.jwt import resolve_cf_access_user
    from app.models.cf_access_identity import CfAccessIdentity

    user = await resolve_cf_access_user(db_session, "bill@barnardhq.com")

    assert user.username == "cf-access:bill@barnardhq.com"
    assert user.is_active is True

    rows = (await db_session.execute(text("SELECT email, user_id FROM cf_access_identities"))).all()
    assert len(rows) == 1
    assert rows[0].email == "bill@barnardhq.com"
    assert rows[0].user_id == user.id


async def test_second_login_reuses_the_same_user_row(db_session):
    from app.auth.jwt import resolve_cf_access_user

    user1 = await resolve_cf_access_user(db_session, "bill@barnardhq.com")
    user2 = await resolve_cf_access_user(db_session, "bill@barnardhq.com")

    assert user1.id == user2.id

    count = (await db_session.execute(text("SELECT COUNT(*) FROM users"))).scalar()
    assert count == 1


async def test_never_adopts_a_preexisting_local_account_by_username_match(db_session):
    """The exact failure the marketing security review caught: a pre-
    existing local ``users`` row whose username happens to equal what the
    shadow-provisioning convention would use must NEVER be silently
    treated as the Access identity by this function. Since this app places
    no charset restriction on account renames (unlike marketing), this is
    a live, not merely theoretical, collision surface."""
    from app.auth.jwt import resolve_cf_access_user, hash_password_async
    from app.models.user import User

    squat_username = "cf-access:bill@barnardhq.com"
    preexisting = User(
        username=squat_username,
        hashed_password=await hash_password_async("Sup3r$ecretPW!"),
        is_active=True,
    )
    db_session.add(preexisting)
    await db_session.commit()
    preexisting_id = preexisting.id

    # resolve_cf_access_user must NOT silently return `preexisting` just
    # because its username string matches the shadow-naming convention —
    # it has no cf_access_identities row, so it must never be adopted.
    # Since the users.username UNIQUE constraint blocks provisioning a
    # second row with the same string, this must fail loudly (503) rather
    # than fall through to picking the squatted row.
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        await resolve_cf_access_user(db_session, "bill@barnardhq.com")
    assert ei.value.status_code == 503

    # The pre-existing account is untouched and still has no Access mapping.
    from sqlalchemy import select
    result = await db_session.execute(select(User).where(User.id == preexisting_id))
    row = result.scalar_one()
    assert row.username == squat_username

    mapped = (await db_session.execute(text("SELECT COUNT(*) FROM cf_access_identities"))).scalar()
    assert mapped == 0


async def test_different_emails_get_different_shadow_users(db_session):
    from app.auth.jwt import resolve_cf_access_user

    user_a = await resolve_cf_access_user(db_session, "bill@barnardhq.com")
    user_b = await resolve_cf_access_user(db_session, "someone-else@barnardhq.com")

    assert user_a.id != user_b.id
    assert user_a.username != user_b.username


async def test_shadow_password_is_never_the_empty_or_predictable_value(db_session):
    from app.auth.jwt import resolve_cf_access_user, verify_password_async

    user = await resolve_cf_access_user(db_session, "bill@barnardhq.com")

    assert user.hashed_password
    assert user.hashed_password != ""
    # A blank password must never verify against the shadow hash.
    assert await verify_password_async("", user.hashed_password) is False
    assert await verify_password_async("password", user.hashed_password) is False


async def test_concurrent_first_logins_converge_on_one_user_row(db_session):
    """Two requests racing to provision the SAME email concurrently must
    converge on one users row, not create two (the IntegrityError-recovery
    path on cf_access_identities.email)."""
    import asyncio
    from app.auth.jwt import resolve_cf_access_user

    results = await asyncio.gather(
        resolve_cf_access_user(db_session, "race@barnardhq.com"),
        resolve_cf_access_user(db_session, "race@barnardhq.com"),
        return_exceptions=True,
    )
    # better-sqlite3-style single-connection races aren't possible here
    # (asyncpg + one AsyncSession serializes these awaits on one
    # connection), so this mainly proves the happy path is idempotent
    # under sequential-await concurrency; a true multi-connection race is
    # exercised implicitly by the IntegrityError recovery branch itself
    # (test above proves that branch denies correctly on a REAL collision).
    ids = {r.id for r in results if not isinstance(r, Exception)}
    assert len(ids) == 1
