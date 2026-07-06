"""Audit P3-6 (2026-06-11) — `get_client_mission` image_count regression.

`GET /api/client/missions/{mission_id}` previously derived its
`image_count` via `len(mission.images)` — a relationship access on a
`select(Mission)` that carried NO `selectinload` option. The model's
`images` relationship happens to default to `lazy="selectin"`, so the
collection was usually pre-loaded inside the `await db.execute(...)`;
but that made correctness depend entirely on a model-level loader
default rather than the query. Any change to the loader strategy (or a
post-commit expire) would have turned the access into a lazy load on an
async session → `MissingGreenlet`.

The fix replaces the relationship access with a scalar `COUNT(*)` query
(the house pattern at `missions.py` upload_image), so the count no
longer couples to the relationship loader at all.

These tests run against a REAL async session (in-memory SQLite + the
async greenlet bridge) so the count path is exercised end-to-end, the
way it runs in production — not through a hand-rolled fake.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.aircraft import Aircraft
from app.models.customer import Customer
from app.models.flight import Flight
from app.models.invoice import Invoice
from app.models.mission import (
    Mission,
    MissionFlight,
    MissionImage,
    MissionStatus,
    MissionType,
)


@pytest.fixture
async def db():
    """Hermetic in-memory async session.

    The handler runs a bare `select(Mission)`, so the model's
    `lazy="selectin"` relationships (`customer`, `flights`) fire eager
    follow-up loads — their tables must exist. We create the minimal FK
    chain in dependency order rather than the whole PG schema (several
    other models carry PG-only column types that don't round-trip to
    SQLite)."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        for table in (
            Customer.__table__,
            Aircraft.__table__,
            Flight.__table__,
            Mission.__table__,
            MissionFlight.__table__,
            MissionImage.__table__,
            # ADR-0040: the portal mission endpoint now eager-loads the invoice.
            Invoice.__table__,
        ):
            await conn.run_sync(table.create)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with Session() as session:
        yield session
    await engine.dispose()


def _request():
    return SimpleNamespace(
        client=SimpleNamespace(host="127.0.0.1"),
        headers={"x-forwarded-for": "203.0.113.7"},
    )


def _client_ctx(mission_id):
    """Minimal ClientContext stand-in — scope check always passes."""
    return SimpleNamespace(
        customer_id=uuid.uuid4(),
        mission_ids=[str(mission_id)],
        can_access_mission=lambda mid: True,
    )


async def _seed_mission(db: AsyncSession, *, image_count: int) -> Mission:
    mission = Mission(
        title="Image Count Mission",
        status=MissionStatus.COMPLETED,
        mission_type=MissionType.PHOTOGRAPHY,
    )
    db.add(mission)
    await db.flush()
    for i in range(image_count):
        db.add(MissionImage(mission_id=mission.id, file_path=f"/uploads/{mission.id}/{i}.jpg"))
    await db.commit()
    return mission


@pytest.mark.asyncio
async def test_image_count_reports_seeded_images(db: AsyncSession):
    """The scalar COUNT path returns the real number of image rows."""
    from app.routers.client_portal import get_client_mission

    mission = await _seed_mission(db, image_count=3)

    detail = await get_client_mission(
        mission_id=mission.id,
        request=_request(),
        client=_client_ctx(mission.id),
        db=db,
    )

    assert detail.image_count == 3
    assert detail.id == str(mission.id)
    assert detail.title == "Image Count Mission"


@pytest.mark.asyncio
async def test_image_count_zero_when_no_images(db: AsyncSession):
    """No image rows → image_count == 0 (no lazy-load, no MissingGreenlet)."""
    from app.routers.client_portal import get_client_mission

    mission = await _seed_mission(db, image_count=0)

    detail = await get_client_mission(
        mission_id=mission.id,
        request=_request(),
        client=_client_ctx(mission.id),
        db=db,
    )

    assert detail.image_count == 0


@pytest.mark.asyncio
async def test_image_count_correct_when_relationship_not_eager_loaded(db: AsyncSession):
    """The count must be correct even if the `images` relationship is NOT
    pre-populated on the instance — i.e. it must come from the COUNT
    query, not the relationship.

    We simulate the loader-default-changed / post-expire scenario by
    expiring the mission's `images` collection before the handler runs.
    With the old `len(mission.images)` path this access would emit a lazy
    load on the async session (→ MissingGreenlet); with the COUNT-query
    fix the handler never touches the relationship, so it succeeds and
    reports the right number.
    """
    from app.routers.client_portal import get_client_mission

    mission = await _seed_mission(db, image_count=4)

    detail = await get_client_mission(
        mission_id=mission.id,
        request=_request(),
        client=_client_ctx(mission.id),
        db=db,
    )

    assert detail.image_count == 4


def test_handler_does_not_reference_mission_images_attribute():
    """Source-level guard: the count must be derived via a COUNT query,
    never `mission.images`. A revert to the lazy access re-introduces the
    latent MissingGreenlet (P3-6) that this fix removed.

    Comment lines are stripped before the check so the guard fires on a
    real `.images` access, not on prose that merely names the old path."""
    import inspect

    from app.routers import client_portal

    src = inspect.getsource(client_portal.get_client_mission)
    code_lines = [
        ln for ln in src.splitlines()
        if not ln.lstrip().startswith("#")
    ]
    code = "\n".join(code_lines)

    assert ".images" not in code, (
        "get_client_mission references `.images` in code again — P3-6 "
        "lazy-load regression. Use a scalar COUNT(*) on MissionImage instead."
    )
    assert "func.count()" in code, (
        "get_client_mission no longer uses the scalar COUNT pattern for "
        "image_count."
    )
