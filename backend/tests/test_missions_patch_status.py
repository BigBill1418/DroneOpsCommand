"""v2.67.0 (Mission Hub redesign) — PATCH /api/missions/{id} status transitions.

The Mission Hub header (Mark COMPLETED, Mark SENT, Reopen Mission)
talks to this endpoint. Per spec §8.5 lockdown semantics:

  * Any valid ``MissionStatus`` enum value is accepted.
  * Invalid status strings → 422 (Pydantic validation).
  * SENT mission cannot be reverted to anything other than COMPLETED,
    and only via the explicit ``?reopen=true`` flow (logs
    ``[MISSION-REOPEN]`` audit line — covered in
    ``test_mission_reopen_logs_audit.py``).

Per ADR-0013, exercised through the full FastAPI ASGI stack.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.test_missions_post_rejects_id_in_body import (
    _FakeSession,
    _MissionStub,
    _no_op_async,
)


def _build_app(initial_mission: _MissionStub) -> tuple[FastAPI, _FakeSession]:
    """Build an app pre-loaded with one Mission stub.

    PATCH /api/missions/{id} executes:
      1. SELECT Mission WHERE id = … (used to verify existence + read previous status)
      2. (status update applied in-memory on the stub)
      3. flush
      4. SELECT Mission WHERE id = … with eager loads (returned to client)
    """
    from app.auth.jwt import get_current_user
    from app.database import get_db
    from app.routers.missions import router as missions_router

    app = FastAPI()
    app.include_router(missions_router)

    # Two execute() calls per PATCH → return the same stub from both.
    fake_db = _FakeSession(execute_results=[initial_mission, initial_mission])

    async def _get_db_override():
        yield fake_db

    async def _user_override():
        from types import SimpleNamespace
        return SimpleNamespace(username="op@test.example.com", id=uuid.uuid4())

    app.dependency_overrides[get_db] = _get_db_override
    app.dependency_overrides[get_current_user] = _user_override
    return app, fake_db


def test_patch_valid_status_returns_200_and_updates_row():
    from app.models.mission import MissionStatus

    stub = _MissionStub(status=MissionStatus.IN_PROGRESS, title="Mark complete me")
    app, _db = _build_app(stub)
    client = TestClient(app)

    with patch("app.routers.missions._send_portal_email_for_mission", new=_no_op_async):
        resp = client.patch(
            f"/api/missions/{stub.id}",
            json={"status": "completed"},
        )

    assert resp.status_code == 200, f"got {resp.status_code} body={resp.text}"
    body = resp.json()
    assert body["status"] == "completed"
    # In-memory mutation persisted on the stub before the eager re-query.
    assert stub.status == MissionStatus.COMPLETED


def test_patch_invalid_status_returns_422():
    from app.models.mission import MissionStatus

    stub = _MissionStub(status=MissionStatus.DRAFT)
    app, _db = _build_app(stub)
    client = TestClient(app)

    resp = client.patch(
        f"/api/missions/{stub.id}",
        json={"status": "absolutely-not-a-status"},
    )

    assert resp.status_code == 422, resp.text


def test_patch_missing_status_returns_422():
    from app.models.mission import MissionStatus

    stub = _MissionStub(status=MissionStatus.DRAFT)
    app, _db = _build_app(stub)
    client = TestClient(app)

    resp = client.patch(f"/api/missions/{stub.id}", json={})
    assert resp.status_code == 422, resp.text


def test_patch_sent_to_anything_other_than_completed_rejected():
    """Spec §8.5: SENT is the lock-down state. Reverting SENT → DRAFT (or
    any non-COMPLETED state) must be rejected with 400.
    """
    from app.models.mission import MissionStatus

    stub = _MissionStub(status=MissionStatus.SENT)
    app, _db = _build_app(stub)
    client = TestClient(app)

    resp = client.patch(
        f"/api/missions/{stub.id}",
        json={"status": "draft"},
    )

    assert resp.status_code == 400, resp.text
    assert "Reopen" in resp.json()["detail"], resp.json()


def test_patch_sent_to_completed_without_reopen_query_rejected():
    """Even SENT → COMPLETED requires ?reopen=true — the operator must
    pass through the explicit Reopen flow so the audit log fires.
    """
    from app.models.mission import MissionStatus

    stub = _MissionStub(status=MissionStatus.SENT)
    app, _db = _build_app(stub)
    client = TestClient(app)

    resp = client.patch(
        f"/api/missions/{stub.id}",
        json={"status": "completed"},
    )

    assert resp.status_code == 400, resp.text


def test_patch_sent_to_completed_with_reopen_query_succeeds():
    from app.models.mission import MissionStatus

    stub = _MissionStub(status=MissionStatus.SENT)
    app, _db = _build_app(stub)
    client = TestClient(app)

    with patch("app.routers.missions._send_portal_email_for_mission", new=_no_op_async):
        resp = client.patch(
            f"/api/missions/{stub.id}?reopen=true",
            json={"status": "completed"},
        )

    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "completed"
    assert stub.status == MissionStatus.COMPLETED


def test_patch_returns_404_for_unknown_mission():
    """Mission not found → 404 (eager re-query never runs)."""
    from app.auth.jwt import get_current_user
    from app.database import get_db
    from app.routers.missions import router as missions_router

    app = FastAPI()
    app.include_router(missions_router)

    fake_db = _FakeSession(execute_results=[None])  # SELECT returns None

    async def _get_db_override():
        yield fake_db

    async def _user_override():
        from types import SimpleNamespace
        return SimpleNamespace(username="op@test.example.com", id=uuid.uuid4())

    app.dependency_overrides[get_db] = _get_db_override
    app.dependency_overrides[get_current_user] = _user_override

    client = TestClient(app)
    resp = client.patch(f"/api/missions/{uuid.uuid4()}", json={"status": "completed"})
    assert resp.status_code == 404
