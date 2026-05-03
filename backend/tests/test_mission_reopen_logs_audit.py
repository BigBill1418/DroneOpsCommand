"""v2.67.0 (Mission Hub redesign, spec §8.5) — Reopen audit log.

A SENT mission cannot be silently re-edited. The Hub's Reopen Mission
button hits ``PATCH /api/missions/{id}?reopen=true`` with
``{"status": "completed"}``. That call MUST emit a
``[MISSION-REOPEN]`` WARNING line containing:

  * mission_id
  * previous_status (i.e. ``sent``)
  * new_status (i.e. ``completed``)
  * the operator's username

so the (forthcoming) audit-trail viewer can show "who reopened what
and when". This is the soft-lock audit semantics from spec §8.5 — no
DB-level constraint, but a load-bearing log line.

Per ADR-0013, exercised through the full FastAPI ASGI stack.
"""

from __future__ import annotations

import logging
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


def _build_app(stub: _MissionStub, username: str) -> tuple[FastAPI, _FakeSession]:
    from app.auth.jwt import get_current_user
    from app.database import get_db
    from app.routers.missions import router as missions_router

    app = FastAPI()
    app.include_router(missions_router)
    fake_db = _FakeSession(execute_results=[stub, stub])

    async def _get_db_override():
        yield fake_db

    async def _user_override():
        from types import SimpleNamespace
        return SimpleNamespace(username=username, id=uuid.uuid4())

    app.dependency_overrides[get_db] = _get_db_override
    app.dependency_overrides[get_current_user] = _user_override
    return app, fake_db


def test_reopen_logs_mission_reopen_audit_with_user_and_previous_status(caplog):
    from app.models.mission import MissionStatus

    stub = _MissionStub(status=MissionStatus.SENT, title="Locked-then-reopened mission")
    app, _db = _build_app(stub, username="bill@barnardhq.com")
    client = TestClient(app)

    with caplog.at_level(logging.WARNING, logger="doc.missions"), \
         patch("app.routers.missions._send_portal_email_for_mission", new=_no_op_async):
        resp = client.patch(
            f"/api/missions/{stub.id}?reopen=true",
            json={"status": "completed"},
        )

    assert resp.status_code == 200, resp.text

    reopen_records = [
        r for r in caplog.records
        if r.levelno == logging.WARNING and "[MISSION-REOPEN]" in r.getMessage()
    ]
    assert len(reopen_records) == 1, (
        f"expected exactly 1 [MISSION-REOPEN] WARN, got {len(reopen_records)}: "
        f"{[r.getMessage() for r in reopen_records]}"
    )
    msg = reopen_records[0].getMessage()
    # Audit fields the future viewer will surface:
    assert str(stub.id) in msg, msg
    assert "previous_status=sent" in msg, msg
    assert "new_status=completed" in msg, msg
    assert "bill@barnardhq.com" in msg, msg


def test_normal_status_transition_logs_mission_status_not_reopen(caplog):
    """A non-reopen transition must use the ``[MISSION-STATUS]`` INFO
    log, not the audit-grade WARN. This keeps the audit channel quiet
    for ordinary lifecycle moves."""
    from app.models.mission import MissionStatus

    stub = _MissionStub(status=MissionStatus.IN_PROGRESS)
    app, _db = _build_app(stub, username="op@test.example.com")
    client = TestClient(app)

    with caplog.at_level(logging.INFO, logger="doc.missions"), \
         patch("app.routers.missions._send_portal_email_for_mission", new=_no_op_async):
        resp = client.patch(
            f"/api/missions/{stub.id}",
            json={"status": "completed"},
        )

    assert resp.status_code == 200, resp.text
    status_records = [
        r for r in caplog.records
        if "[MISSION-STATUS]" in r.getMessage()
    ]
    reopen_records = [
        r for r in caplog.records
        if "[MISSION-REOPEN]" in r.getMessage()
    ]
    assert len(status_records) == 1
    assert reopen_records == []
    assert "from=in_progress" in status_records[0].getMessage()
    assert "to=completed" in status_records[0].getMessage()
