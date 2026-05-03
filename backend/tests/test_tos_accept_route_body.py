"""v2.66.2 P0 hotfix regression test — `POST /api/tos/accept` body parsing.

The 2026-05-03 production incident (customer 824d055a…f59f5459f442 hit
Submit on the TOS page six times, every attempt 422'd in <2ms) traced
to a FastAPI body-vs-query inference failure on this exact route. The
combination of:

  * `from __future__ import annotations` at the top of `app.routers.tos`
  * `payload: TosAcceptanceRequest` declared as the first positional
    parameter of `accept_terms`

caused FastAPI to see the parameter annotation as a forward-ref string
at decoration time (PEP 563), fail to recognise it as a Pydantic
``BaseModel`` subclass, and default to ``Query()`` instead of
``Body()``. Customers POSTing a complete JSON body therefore got back
``{"detail":[{"loc":["query","payload"],"msg":"Field required"}]}``
with a 422 in 2ms — the route handler itself never ran, no acceptance
row was ever created, no signed PDF, no email.

The hermetic unit tests in ``test_tos_customer_sync.py`` did NOT catch
this because they call ``tos_module.accept_terms(...)`` directly,
bypassing FastAPI's request-parsing pipeline entirely.

This test exercises the route through the full FastAPI ASGI stack via
``TestClient`` and POSTs the exact JSON shape the frontend sends, so
any future regression of the body-inference path will be caught here
instead of by paying customers.

The OpenAPI schema assertion is the canonical guard: if FastAPI is
correctly routing the model as a body, ``requestBody`` must be present
and there must be no ``parameters`` entry named ``payload``.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


# ── Fakes that match the live route's collaborators ──────────────────
class _ScalarOneOrNone:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeAsyncSession:
    """Minimal AsyncSession surrogate used by the route via Depends."""

    def __init__(self, results):
        self._results = list(results)
        self.added = []
        self.commits = 0

    async def execute(self, _stmt):
        if not self._results:
            return _ScalarOneOrNone(None)
        return _ScalarOneOrNone(self._results.pop(0))

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1

    async def flush(self):
        pass

    async def refresh(self, obj):
        if not getattr(obj, "id", None):
            obj.id = uuid.uuid4()
        if not getattr(obj, "accepted_at", None):
            obj.accepted_at = datetime.now(timezone.utc)


def _mk_template():
    return SimpleNamespace(bytes=b"%PDF-1.4 fake", version="rev3")


def _mk_record():
    return SimpleNamespace(
        audit_id="aud_test_route_body",
        template_sha256="t" * 64,
        signed_sha256="s" * 64,
        field_values={
            "client_name": "Casey Operator",
            "client_email": "casey@example.com",
            "client_company": "",
            "client_title": "",
            "client_ip": "203.0.113.1",
        },
    )


def _build_app() -> FastAPI:
    """Build a tiny FastAPI app that mounts only the TOS router.

    The full app pulls in Postgres / Redis / SMTP / Sentry, none of
    which we need (or want) here. Mounting just the router exercises
    FastAPI's body-vs-query inference for the route under test, which
    is the exact decoration path that failed in production.
    """
    from app.database import get_db
    from app.routers.tos import router as tos_router

    app = FastAPI()
    app.include_router(tos_router)

    fake_db = _FakeAsyncSession(results=[None])

    async def _get_db_override():
        yield fake_db

    app.dependency_overrides[get_db] = _get_db_override
    app.state._fake_db = fake_db  # so tests can introspect commits
    return app


# ── Canary: schema-level assertion ───────────────────────────────────
def test_tos_accept_route_payload_is_body_not_query():
    """Canary: FastAPI must classify ``payload`` as a request body.

    If this fails, ``payload`` has been auto-classified as a query
    parameter (the v2.66.1 production bug). Any future change that
    re-introduces ``from __future__ import annotations`` + an
    un-annotated Pydantic body model on this route will trip here.
    """
    app = _build_app()
    schema = app.openapi()
    op = schema["paths"]["/api/tos/accept"]["post"]

    # Body must exist
    assert "requestBody" in op, (
        "POST /api/tos/accept has no requestBody — FastAPI did not "
        "infer the Pydantic model as a body. v2.66.1 regression."
    )

    # No parameter named 'payload' (would mean it became a query/path arg)
    params = op.get("parameters", [])
    payload_params = [p for p in params if p.get("name") == "payload"]
    assert not payload_params, (
        f"`payload` should be a body, not {payload_params[0].get('in')!r} param. "
        "v2.66.1 regression."
    )


# ── Integration: live POST through the ASGI pipeline ─────────────────
def test_tos_accept_post_with_full_frontend_payload_returns_201(tmp_path):
    """POST the EXACT JSON shape the React form sends and assert 201.

    Pre-fix this assertion fails with 422 + ``loc=['query','payload']``.
    Post-fix it succeeds with 201 + a JSON envelope containing
    ``audit_id``.
    """
    app = _build_app()

    from app.routers import tos as tos_module

    record = _mk_record()
    signed_bytes = b"%PDF-1.4 signed"

    with patch.object(tos_module, "get_active_tos_template", return_value=_mk_template()), \
         patch.object(tos_module, "accept_tos", return_value=(signed_bytes, record)), \
         patch.object(tos_module, "signed_pdf_dir", return_value=tmp_path), \
         patch.object(tos_module, "send_signed_tos_to_both_parties",
                      new=AsyncMock(return_value=None)):
        client = TestClient(app)
        # This is the exact shape `frontend/src/api/tosApi.ts` sends
        # for the customer flow that failed in production at 16:09
        # PT on 2026-05-03 — both customer_id (UUID string) and
        # intake_token (URL-safe base64) populated.
        response = client.post(
            "/api/tos/accept",
            json={
                "full_name": "Casey Operator",
                "email": "casey@example.com",
                "company": "",
                "title": "",
                "confirm": True,
                "customer_id": str(uuid.uuid4()),
                "intake_token": "s6Yel7IFniIyxOI7oLW24YQ-9XWdMbplZcRRQBiEVQ8",
            },
        )

    assert response.status_code == 201, (
        f"Expected 201, got {response.status_code}. Body: {response.text}"
    )
    body = response.json()
    assert body["audit_id"] == record.audit_id
    assert body["template_sha256"] == record.template_sha256
    assert body["signed_sha256"] == record.signed_sha256


def test_tos_accept_post_without_optional_correlation_returns_201(tmp_path):
    """Cold-visitor flow: customer reaches /tos/accept with no token /
    customer_id query params. The frontend sends them as JSON ``null``.
    Must still 201 (don't make optionals required as a side-effect of
    the body-fix)."""
    app = _build_app()

    from app.routers import tos as tos_module

    record = _mk_record()
    with patch.object(tos_module, "get_active_tos_template", return_value=_mk_template()), \
         patch.object(tos_module, "accept_tos", return_value=(b"x", record)), \
         patch.object(tos_module, "signed_pdf_dir", return_value=tmp_path), \
         patch.object(tos_module, "send_signed_tos_to_both_parties",
                      new=AsyncMock(return_value=None)):
        client = TestClient(app)
        response = client.post(
            "/api/tos/accept",
            json={
                "full_name": "Cold Visitor",
                "email": "cold@example.com",
                "company": "",
                "title": "",
                "confirm": True,
                "customer_id": None,
                "intake_token": None,
            },
        )

    assert response.status_code == 201, (
        f"Expected 201, got {response.status_code}. Body: {response.text}"
    )


def test_tos_accept_post_unchecked_confirm_returns_422(tmp_path):
    """The ``@field_validator('confirm')`` must still 422 if confirm
    is False — ensure the body-fix didn't accidentally relax this."""
    app = _build_app()

    from app.routers import tos as tos_module

    with patch.object(tos_module, "get_active_tos_template", return_value=_mk_template()), \
         patch.object(tos_module, "accept_tos", return_value=(b"x", _mk_record())), \
         patch.object(tos_module, "signed_pdf_dir", return_value=tmp_path), \
         patch.object(tos_module, "send_signed_tos_to_both_parties",
                      new=AsyncMock(return_value=None)):
        client = TestClient(app)
        response = client.post(
            "/api/tos/accept",
            json={
                "full_name": "Casey Operator",
                "email": "casey@example.com",
                "company": "",
                "title": "",
                "confirm": False,  # ← rejected
                "customer_id": None,
                "intake_token": None,
            },
        )

    assert response.status_code == 422
    detail = response.json()["detail"]
    # Ensure the failure is on the `confirm` field (body-located), not
    # the whole payload — i.e. the body parser ran successfully and
    # then the field validator fired.
    locs = [tuple(item["loc"]) for item in detail]
    assert any("confirm" in loc for loc in locs), (
        f"Expected validation failure on `confirm`, got: {detail}"
    )
