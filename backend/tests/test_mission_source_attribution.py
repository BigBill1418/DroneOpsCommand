"""ADR-0016 — mission lead-source attribution.

Covers the three layers the feature touches without standing up a real
Postgres/Redis stack (same hermetic ASGI pattern as
``test_missions_post_rejects_id_in_body.py``):

  1. Schema validation/serialization — ``MissionCreate`` / ``MissionUpdate``
     accept the ``MissionSource`` vocabulary and reject anything else;
     ``MissionResponse`` round-trips a stored plain-string source.
  2. Router coercion — ``MissionSource`` is persisted as its plain value
     ("website"), not the member repr.
  3. Financials rollup — ``GET /api/financials/summary`` returns a
     ``revenue_by_source`` block that groups PAID + billed revenue by
     source, mapping NULL → "unknown".
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.models.mission import MissionSource, MissionStatus, MissionType
from app.schemas.mission import MissionCreate, MissionResponse, MissionUpdate


# ── Layer 1: schema validation + serialization ───────────────────────


def test_mission_create_accepts_known_source():
    m = MissionCreate(title="Web lead", source="website")
    assert m.source is MissionSource.WEBSITE


def test_mission_create_source_optional_defaults_none():
    m = MissionCreate(title="No source given")
    assert m.source is None
    assert m.source_ref is None


def test_mission_create_rejects_unknown_source():
    with pytest.raises(ValidationError):
        MissionCreate(title="Bad", source="carrier_pigeon")


def test_mission_update_accepts_all_sources():
    for src in MissionSource:
        m = MissionUpdate(source=src.value)
        assert m.source is src


def test_mission_response_roundtrips_plain_string_source():
    """The DB column is a plain VARCHAR; a row read back has source as a
    bare string. ``MissionResponse`` must coerce it to the enum."""
    now = datetime.utcnow()
    resp = MissionResponse.model_validate(
        {
            "id": uuid.uuid4(),
            "customer_id": None,
            "title": "Stored row",
            "mission_type": MissionType.LOST_PET,
            "description": None,
            "mission_date": date(2026, 5, 23),
            "location_name": None,
            "area_coordinates": None,
            "status": MissionStatus.SENT,
            "is_billable": True,
            "source": "website",  # plain string, as the column holds it
            "source_ref": "lead-4471",
            "created_at": now,
            "updated_at": now,
        }
    )
    assert resp.source is MissionSource.WEBSITE
    assert resp.source_ref == "lead-4471"
    # JSON serialization emits the plain value the frontend expects.
    assert resp.model_dump(mode="json")["source"] == "website"


def test_mission_response_null_source_serializes_null():
    now = datetime.utcnow()
    resp = MissionResponse.model_validate(
        {
            "id": uuid.uuid4(),
            "customer_id": None,
            "title": "Legacy row, no source",
            "mission_type": MissionType.OTHER,
            "description": None,
            "mission_date": None,
            "location_name": None,
            "area_coordinates": None,
            "status": MissionStatus.DRAFT,
            "is_billable": False,
            "source": None,
            "source_ref": None,
            "created_at": now,
            "updated_at": now,
        }
    )
    assert resp.source is None
    assert resp.model_dump(mode="json")["source"] is None


# ── Layer 2 + 3: financials route fakes ──────────────────────────────


class _ScalarResult:
    def __init__(self, values: list[Any]) -> None:
        self._values = values

    def scalars(self):
        return self

    def all(self):
        return self._values


class _LineItem:
    def __init__(self, total: float, category=None) -> None:
        self.total = total
        self.category = category


class _Invoice:
    def __init__(self, *, paid: bool, total: float, number: str) -> None:
        self.paid_in_full = paid
        self.total = total
        self.invoice_number = number
        self.line_items = [_LineItem(total)]


class _MissionRow:
    """Quacks like an eager-loaded billable Mission for financials_summary."""

    def __init__(self, *, title: str, source: str | None, paid: bool, total: float) -> None:
        self.id = uuid.uuid4()
        self.title = title
        self.source = source
        self.mission_type = MissionType.LOST_PET
        self.mission_date = date(2026, 5, 23)
        self.location_name = "Washington County, Oregon"
        self.customer = None
        self.flights = []
        self.invoice = _Invoice(
            paid=paid, total=total, number=f"INV-{title[:4]}"
        )


class _FinancialsSession:
    """Returns a fixed set of billable missions for the single select()."""

    def __init__(self, missions: list[_MissionRow]) -> None:
        self._missions = missions

    async def execute(self, _stmt):
        return _ScalarResult(self._missions)

    @property
    def is_active(self) -> bool:
        return True

    async def commit(self):  # pragma: no cover
        pass

    async def rollback(self):  # pragma: no cover
        pass

    async def close(self):  # pragma: no cover
        pass


def _build_financials_app(missions: list[_MissionRow]) -> TestClient:
    from app.auth.jwt import get_current_user
    from app.database import get_db
    from app.routers.financials import router as financials_router
    from types import SimpleNamespace

    app = FastAPI()
    app.include_router(financials_router)

    session = _FinancialsSession(missions)

    async def _get_db_override():
        yield session

    async def _user_override():
        return SimpleNamespace(username="op@test.example.com", id=uuid.uuid4())

    app.dependency_overrides[get_db] = _get_db_override
    app.dependency_overrides[get_current_user] = _user_override
    return TestClient(app)


def test_revenue_by_source_groups_paid_and_total():
    """Two website jobs (one paid, one unpaid), one referral (paid),
    one NULL-source (paid) → buckets reflect paid vs billed correctly."""
    client = _build_financials_app(
        [
            _MissionRow(title="Banks Missing Dog", source="website", paid=True, total=1216.36),
            _MissionRow(title="Web Inspection (unpaid)", source="website", paid=False, total=500.0),
            _MissionRow(title="Friend Referral", source="referral", paid=True, total=300.0),
            _MissionRow(title="Old Job", source=None, paid=True, total=200.0),
        ]
    )
    resp = client.get("/api/financials/summary")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert "revenue_by_source" in body
    by_source = {row["source"]: row for row in body["revenue_by_source"]}

    assert by_source["website"]["paid"] == 1216.36
    assert by_source["website"]["total"] == 1716.36
    assert by_source["website"]["missions"] == 2

    assert by_source["referral"]["paid"] == 300.0
    assert by_source["unknown"]["source"] == "unknown"
    assert by_source["unknown"]["paid"] == 200.0

    # Sorted by collected (paid) revenue descending — website leads.
    assert body["revenue_by_source"][0]["source"] == "website"


def test_revenue_by_source_empty_when_no_billable_missions():
    client = _build_financials_app([])
    resp = client.get("/api/financials/summary")
    assert resp.status_code == 200, resp.text
    assert resp.json()["revenue_by_source"] == []


def test_summary_mission_row_carries_source():
    client = _build_financials_app(
        [_MissionRow(title="Banks Missing Dog", source="website", paid=True, total=1216.36)]
    )
    resp = client.get("/api/financials/summary")
    body = resp.json()
    assert body["missions"][0]["source"] == "website"
