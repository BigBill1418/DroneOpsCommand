"""v2.67.1 — PATCH /api/missions/{id} accepts all 8 MissionStatus values.

This pins the contract that the v2.67.0 mixed-case enum bug cannot
recur. v2.67.0 worked for DRAFT/COMPLETED/SENT only because those
labels existed in both cases in the legacy PG enum; PATCH to
scheduled/in_progress/processing/review/delivered hit the SQLAlchemy
enum-NAME-not-VALUE serializer and 500'd at the DB.

v2.67.1's fix (`values_callable=lambda enum: [e.value for e in enum]`
on the Mission.status column mapping) makes SA write the lowercase
VALUES that match the PG enum labels for all 8 states. This test
exercises every state through the real HTTP route to prove that.

Per ADR-0013: real httpx + TestClient, no SimpleNamespace bypass.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.models.mission import MissionStatus

# Reuse Agent A's hermetic test infra
from tests.test_missions_post_rejects_id_in_body import (
    _FakeSession,
    _MissionStub,
)
from tests.test_missions_patch_status import _build_app


# Every MissionStatus value as a lowercase string.
ALL_STATUS_VALUES = sorted(s.value for s in MissionStatus)


@pytest.mark.parametrize("target_status", ALL_STATUS_VALUES)
def test_patch_status_accepts_every_enum_value(target_status: str) -> None:
    """v2.67.1 contract: PATCH succeeds for every MissionStatus value.

    Pre-fix this would 500 for scheduled/in_progress/processing/review/
    delivered because SA wrote the enum NAME (uppercase) and the legacy
    PG enum only had lowercase labels for those.
    """
    initial = _MissionStub(status=MissionStatus.DRAFT)
    app, session = _build_app(initial)
    client = TestClient(app)

    # Reopen-allowed transitions (SENT → COMPLETED) need the query param;
    # everything else uses the plain PATCH.
    needs_reopen = (
        initial.status == MissionStatus.SENT
        and target_status != MissionStatus.SENT.value
    )
    url = (
        f"/api/missions/{initial.id}"
        + ("?reopen=true" if needs_reopen else "")
    )

    resp = client.patch(url, json={"status": target_status})

    # Every enum value is acceptable from DRAFT (no lockdown applies).
    assert resp.status_code == 200, (
        f"PATCH to status={target_status!r} returned {resp.status_code} "
        f"with body {resp.text!r}; expected 200. v2.67.1 fix may have "
        f"regressed."
    )
    body = resp.json()
    assert body["status"] == target_status, (
        f"Round-tripped status {body['status']!r} != requested "
        f"{target_status!r}"
    )


def test_patch_status_invalid_value_returns_422() -> None:
    """Negative case: a status string that isn't in the enum returns 422."""
    initial = _MissionStub(status=MissionStatus.DRAFT)
    app, _ = _build_app(initial)
    client = TestClient(app)

    resp = client.patch(
        f"/api/missions/{initial.id}",
        json={"status": "TOTALLY_BOGUS_STATUS"},
    )
    assert resp.status_code == 422, (
        f"Expected 422 for bogus status, got {resp.status_code} body={resp.text!r}"
    )


def test_patch_status_uppercase_value_returns_422() -> None:
    """v2.67.1 invariant: SA + the API both speak lowercase values now.

    An UPPERCASE input like 'IN_PROGRESS' is no longer a valid enum
    value (the Python enum's values are all lowercase). API rejects
    with 422 — proves the case-mismatch fail-fast contract.
    """
    initial = _MissionStub(status=MissionStatus.DRAFT)
    app, _ = _build_app(initial)
    client = TestClient(app)

    resp = client.patch(
        f"/api/missions/{initial.id}",
        json={"status": "IN_PROGRESS"},
    )
    assert resp.status_code == 422, (
        f"Expected 422 for uppercase 'IN_PROGRESS', got {resp.status_code} "
        f"body={resp.text!r}. The API must reject case-mismatched values "
        f"explicitly, not 500 at the DB later."
    )
