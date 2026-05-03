"""v2.67.0 (Mission Hub redesign, spec §4) — soft duplicate detection.

When a ``POST /api/missions`` arrives with the same
``(customer_id, title, mission_date)`` triple as a non-deleted mission
created in the last 5 minutes, the route MUST:

  * log a ``[MISSION-POST-DUP]`` WARNING line (so the ADR-0013
    4xx-burst alert can graduate it later if recurrent), AND
  * STILL accept the POST and return 201 — operators legitimately want
    two missions for the same customer/title on the same day in some
    workflows. This is opt-in observability, not a hard reject.

Per ADR-0013, exercised through the full FastAPI ASGI stack.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date
from typing import Any
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.test_missions_post_rejects_id_in_body import (
    _FakeSession,
    _MissionStub,
    _no_op_async,
)


def _build_app(execute_results: list[Any]) -> tuple[FastAPI, _FakeSession]:
    from app.auth.jwt import get_current_user
    from app.database import get_db
    from app.routers.missions import router as missions_router

    app = FastAPI()
    app.include_router(missions_router)
    fake_db = _FakeSession(execute_results)

    async def _get_db_override():
        yield fake_db

    async def _user_override():
        from types import SimpleNamespace
        return SimpleNamespace(username="op@test.example.com", id=uuid.uuid4())

    app.dependency_overrides[get_db] = _get_db_override
    app.dependency_overrides[get_current_user] = _user_override
    return app, fake_db


def test_dup_within_5min_logs_warning_but_still_returns_201(caplog):
    """First two POSTs of the same triple within 5 minutes:
    1) first POST: COUNT=0 → 201, no dup log.
    2) second POST: COUNT=1 → 201 (operator override allowed) + WARN log.
    """
    customer_id = uuid.uuid4()
    same_title = "Smith Property — duplicate-check"
    same_date = "2026-05-03"

    # ── First POST: dup-check returns 0, mission created normally ──
    stub_a = _MissionStub(title=same_title, customer_id=customer_id)
    app1, _db1 = _build_app(execute_results=[0, stub_a])
    client1 = TestClient(app1)

    with caplog.at_level(logging.WARNING, logger="doc.missions"), \
         patch("app.routers.missions._send_portal_email_for_mission", new=_no_op_async):
        resp1 = client1.post("/api/missions", json={
            "title": same_title,
            "customer_id": str(customer_id),
            "mission_date": same_date,
            "mission_type": "other",
        })

    assert resp1.status_code == 201, resp1.text
    # No dup-warning on the first call (count was 0).
    dup_warns_after_first = [
        r for r in caplog.records
        if r.levelno == logging.WARNING and "[MISSION-POST-DUP]" in r.getMessage()
    ]
    assert dup_warns_after_first == [], (
        f"first POST must not log dup-warning, got: {[r.getMessage() for r in dup_warns_after_first]}"
    )

    # ── Second POST: dup-check returns 1, must STILL succeed + log WARN ──
    caplog.clear()
    stub_b = _MissionStub(title=same_title, customer_id=customer_id)
    app2, _db2 = _build_app(execute_results=[1, stub_b])
    client2 = TestClient(app2)

    with caplog.at_level(logging.WARNING, logger="doc.missions"), \
         patch("app.routers.missions._send_portal_email_for_mission", new=_no_op_async):
        resp2 = client2.post("/api/missions", json={
            "title": same_title,
            "customer_id": str(customer_id),
            "mission_date": same_date,
            "mission_type": "other",
        })

    assert resp2.status_code == 201, (
        "operator override: dup-warning must NOT reject the POST; "
        f"got {resp2.status_code} body={resp2.text}"
    )

    dup_warns = [
        r for r in caplog.records
        if r.levelno == logging.WARNING and "[MISSION-POST-DUP]" in r.getMessage()
    ]
    assert len(dup_warns) == 1, (
        f"expected exactly 1 [MISSION-POST-DUP] WARN, got {len(dup_warns)}: "
        f"{[r.getMessage() for r in dup_warns]}"
    )
    msg = dup_warns[0].getMessage()
    assert same_title in msg, msg
    assert str(customer_id) in msg, msg


def test_dup_check_skipped_when_no_customer(caplog):
    """No customer_id → dup-check is skipped (the unique-key for the
    soft check is the (customer_id, title, date) triple). The route
    must still 201; absence of warning is the assertion."""
    stub = _MissionStub(title="Anonymous mission")
    app, _db = _build_app(execute_results=[stub])  # only the eager re-query
    client = TestClient(app)

    with caplog.at_level(logging.WARNING, logger="doc.missions"), \
         patch("app.routers.missions._send_portal_email_for_mission", new=_no_op_async):
        resp = client.post("/api/missions", json={
            "title": "Anonymous mission",
            "mission_type": "other",
        })

    assert resp.status_code == 201, resp.text
    dup_warns = [
        r for r in caplog.records
        if "[MISSION-POST-DUP]" in r.getMessage()
    ]
    assert dup_warns == []
