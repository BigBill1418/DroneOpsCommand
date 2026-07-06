"""ADR-0040 — end-to-end trigger coverage over the REAL endpoints.

Where `test_download_link_delivery.py` unit-tests the policy helpers,
this file drives the actual router functions against a real (sqlite)
session — the same house pattern as `test_report_live_aircraft_adr0038.py`
— with only the SMTP layer stubbed:

- `PUT /missions/{id}/invoice` marking paid_in_full  → delivery email fires
  once, `download_link_email_sent_at` stamped; repeat PUT → no re-send.
- `PUT /missions/{id}` changing download_link_url on a paid mission
  → stamp reset + re-delivery; setting it on an UNPAID mission → no email.
- `GET /api/client/missions/{id}` (portal) hides the link while unpaid
  and exposes it once paid.
- SMTP-unconfigured (send returns False) → NOT stamped, delivery stays
  armed for the next trigger.

To run:

    cd backend
    pytest tests/test_download_link_delivery_e2e.py -v
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.aircraft import Aircraft
from app.models.customer import Customer
from app.models.flight import Flight
from app.models.invoice import Invoice, LineItem
from app.models.mission import (
    Mission,
    MissionFlight,
    MissionImage,
    MissionStatus,
    MissionType,
)
from app.models.report import Report

# The reports table carries a Postgres JSONB column (ADR-0015); teach the
# sqlite dialect to render it as JSON so the hermetic fixture can create it.
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles


@compiles(JSONB, "sqlite")
def _render_jsonb_on_sqlite(type_, compiler, **kw):
    return "JSON"


# ── real-DB fixture (mirrors test_report_live_aircraft_adr0038.py) ────────
@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        for table in (
            Customer.__table__,
            Aircraft.__table__,
            Flight.__table__,
            Mission.__table__,
            MissionFlight.__table__,
            MissionImage.__table__,
            Invoice.__table__,
            LineItem.__table__,
            Report.__table__,
        ):
            await conn.run_sync(table.create)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with Session() as session:
        yield session
    await engine.dispose()


@pytest.fixture
def sent_emails(monkeypatch):
    """Stub the SMTP layer; capture every delivery-email call."""
    calls: list[dict] = []

    async def _fake_send(**kwargs):
        calls.append(kwargs)
        return True

    import app.services.email_service as email_service

    monkeypatch.setattr(email_service, "send_download_link_email", _fake_send)
    return calls


async def _seed(db, *, paid=False, url="https://drop.example.com/abc"):
    customer = Customer(id=uuid.uuid4(), name="Test Client", email="client@example.com")
    db.add(customer)
    mission = Mission(
        id=uuid.uuid4(),
        title="E2E Gate Mission",
        mission_type=MissionType.VIDEOGRAPHY,
        status=MissionStatus.SENT,
        mission_date=date(2026, 7, 1),
        is_billable=True,
        customer_id=customer.id,
        download_link_url=url,
    )
    db.add(mission)
    invoice = Invoice(
        id=uuid.uuid4(),
        mission_id=mission.id,
        invoice_number="TEST-0001",
        subtotal=400.50,
        tax_rate=0,
        tax_amount=0,
        total=400.50,
        paid_in_full=paid,
    )
    db.add(invoice)
    await db.flush()
    return mission, invoice, customer


def _fake_user():
    return SimpleNamespace(id=uuid.uuid4(), username="operator", email="op@example.com")


def _fake_request():
    return SimpleNamespace(headers={}, client=SimpleNamespace(host="127.0.0.1"))


def _client_ctx(customer, mission):
    from app.auth.client_auth import ClientContext

    return ClientContext(
        customer_id=customer.id, mission_ids=[str(mission.id)], customer=customer
    )


# ── Trigger: manual mark-paid via PUT /invoice ─────────────────────────

@pytest.mark.asyncio
async def test_manual_mark_paid_fires_delivery_once(db, sent_emails):
    from app.routers.invoices import update_invoice
    from app.schemas.invoice import InvoiceUpdate

    mission, invoice, _ = await _seed(db, paid=False)

    await update_invoice(mission.id, InvoiceUpdate(paid_in_full=True), db, _fake_user())

    assert len(sent_emails) == 1
    assert sent_emails[0]["to_email"] == "client@example.com"
    assert sent_emails[0]["download_url"] == "https://drop.example.com/abc"
    fresh = (await db.execute(select(Mission).where(Mission.id == mission.id))).scalar_one()
    assert fresh.download_link_email_sent_at is not None

    # Repeat PUT (still paid) — no transition, no re-send.
    await update_invoice(mission.id, InvoiceUpdate(paid_in_full=True), db, _fake_user())
    assert len(sent_emails) == 1


@pytest.mark.asyncio
async def test_mark_paid_without_url_sends_nothing(db, sent_emails):
    from app.routers.invoices import update_invoice
    from app.schemas.invoice import InvoiceUpdate

    mission, _, _ = await _seed(db, paid=False, url=None)
    await update_invoice(mission.id, InvoiceUpdate(paid_in_full=True), db, _fake_user())
    assert sent_emails == []
    fresh = (await db.execute(select(Mission).where(Mission.id == mission.id))).scalar_one()
    assert fresh.download_link_email_sent_at is None


# ── Trigger: download URL set/changed via PUT /mission ─────────────────

@pytest.mark.asyncio
async def test_url_set_on_paid_mission_fires_delivery(db, sent_emails):
    from app.routers.missions import update_mission
    from app.schemas.mission import MissionUpdate

    mission, _, _ = await _seed(db, paid=True, url=None)

    await update_mission(
        mission.id,
        MissionUpdate(download_link_url="https://drop.example.com/late"),
        db,
        _fake_user(),
    )
    assert len(sent_emails) == 1
    assert sent_emails[0]["download_url"] == "https://drop.example.com/late"


@pytest.mark.asyncio
async def test_url_change_rearms_and_redelivers(db, sent_emails):
    from app.routers.missions import update_mission
    from app.schemas.mission import MissionUpdate

    mission, _, _ = await _seed(db, paid=True)
    mission.download_link_email_sent_at = datetime.utcnow()  # already delivered once
    await db.flush()

    await update_mission(
        mission.id,
        MissionUpdate(download_link_url="https://drop.example.com/replacement"),
        db,
        _fake_user(),
    )
    assert len(sent_emails) == 1
    assert sent_emails[0]["download_url"] == "https://drop.example.com/replacement"


@pytest.mark.asyncio
async def test_url_set_on_unpaid_mission_holds(db, sent_emails):
    from app.routers.missions import update_mission
    from app.schemas.mission import MissionUpdate

    mission, _, _ = await _seed(db, paid=False, url=None)
    await update_mission(
        mission.id,
        MissionUpdate(download_link_url="https://drop.example.com/early"),
        db,
        _fake_user(),
    )
    assert sent_emails == []
    fresh = (await db.execute(select(Mission).where(Mission.id == mission.id))).scalar_one()
    assert fresh.download_link_email_sent_at is None  # armed, waiting for payment


@pytest.mark.asyncio
async def test_unrelated_mission_update_does_not_rearm(db, sent_emails):
    from app.routers.missions import update_mission
    from app.schemas.mission import MissionUpdate

    mission, _, _ = await _seed(db, paid=True)
    stamp = datetime.utcnow()
    mission.download_link_email_sent_at = stamp
    await db.flush()

    await update_mission(mission.id, MissionUpdate(title="Renamed"), db, _fake_user())
    assert sent_emails == []
    fresh = (await db.execute(select(Mission).where(Mission.id == mission.id))).scalar_one()
    assert fresh.download_link_email_sent_at == stamp


# ── Portal exposure ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_portal_hides_link_while_unpaid_shows_when_paid(db):
    from app.routers.client_portal import get_client_mission

    mission, invoice, customer = await _seed(db, paid=False)
    ctx = _client_ctx(customer, mission)

    detail = await get_client_mission(mission.id, _fake_request(), ctx, db)
    assert detail.download_url is None

    invoice.paid_in_full = True
    await db.flush()

    detail = await get_client_mission(mission.id, _fake_request(), ctx, db)
    assert detail.download_url == "https://drop.example.com/abc"


@pytest.mark.asyncio
async def test_portal_override_releases_unpaid(db):
    from app.routers.client_portal import get_client_mission

    mission, _, customer = await _seed(db, paid=False)
    db.add(
        Report(
            mission_id=mission.id,
            include_download_link=True,
            download_link_payment_override=True,
        )
    )
    await db.flush()

    detail = await get_client_mission(mission.id, _fake_request(), _client_ctx(customer, mission), db)
    assert detail.download_url == "https://drop.example.com/abc"


# ── SMTP-unconfigured must NOT stamp ───────────────────────────────────

@pytest.mark.asyncio
async def test_smtp_unconfigured_keeps_delivery_armed(db, monkeypatch):
    import app.services.email_service as email_service
    from app.services.download_link_delivery import deliver_download_link_if_due

    async def _send_returns_false(**kwargs):
        return False  # the graceful no-op path (SMTP not configured)

    monkeypatch.setattr(email_service, "send_download_link_email", _send_returns_false)

    mission, _, _ = await _seed(db, paid=True)
    status = await deliver_download_link_if_due(db, mission.id, trigger="test")
    assert status == "skipped:smtp-unconfigured"
    fresh = (await db.execute(select(Mission).where(Mission.id == mission.id))).scalar_one()
    assert fresh.download_link_email_sent_at is None
