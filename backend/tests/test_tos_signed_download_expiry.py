"""GET /api/tos/signed/by-token/{token} is bounded + rate-limited (ADR-0045).

Pre-fix this route had neither: the intake_token doubled as a permanent,
unlimited-rate PII-download bearer credential (name, email, company,
address on the signed TOS PDF) with no operator revocation path — a token
that ever leaked (shared inbox, proxy log, forwarded email) stayed valid
forever. This bounds it to `settings.tos_signed_download_expire_days`
(default ~2 years from `accepted_at`) and adds the same rate limit every
other public route in this file already carries.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient


class _ScalarOneOrNone:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeAsyncSession:
    def __init__(self, row):
        self._row = row

    async def execute(self, _stmt):
        return _ScalarOneOrNone(self._row)


def _row(*, accepted_at, audit_id="aud_x", pdf_path):
    return SimpleNamespace(
        audit_id=audit_id,
        accepted_at=accepted_at,
        signed_pdf_path=str(pdf_path),
    )


def _request():
    from starlette.requests import Request

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/tos/signed/by-token/tok",
        "headers": [],
        "client": ("203.0.113.5", 0),
        "query_string": b"",
        "server": ("testserver", 80),
        "scheme": "http",
        "root_path": "",
        "app": SimpleNamespace(state=SimpleNamespace()),
    }
    return Request(scope)


async def test_fresh_acceptance_downloads_successfully(tmp_path):
    from app.routers import tos as tos_module

    pdf = tmp_path / "signed.pdf"
    pdf.write_bytes(b"%PDF-1.4 signed")
    row = _row(accepted_at=datetime.now(timezone.utc), pdf_path=pdf)
    db = _FakeAsyncSession(row)

    resp = await tos_module.download_signed_by_token("tok", _request(), db)
    assert resp.status_code == 200


async def test_download_past_configured_window_is_rejected(monkeypatch, tmp_path):
    from app.routers import tos as tos_module

    monkeypatch.setattr(tos_module.settings, "tos_signed_download_expire_days", 30)
    pdf = tmp_path / "signed.pdf"
    pdf.write_bytes(b"%PDF-1.4 signed")
    old = datetime.now(timezone.utc) - timedelta(days=31)
    row = _row(accepted_at=old, pdf_path=pdf)
    db = _FakeAsyncSession(row)

    with pytest.raises(HTTPException) as exc_info:
        await tos_module.download_signed_by_token("tok", _request(), db)
    assert exc_info.value.status_code == 410


async def test_download_just_inside_configured_window_succeeds(monkeypatch, tmp_path):
    from app.routers import tos as tos_module

    monkeypatch.setattr(tos_module.settings, "tos_signed_download_expire_days", 30)
    pdf = tmp_path / "signed.pdf"
    pdf.write_bytes(b"%PDF-1.4 signed")
    recent = datetime.now(timezone.utc) - timedelta(days=29)
    row = _row(accepted_at=recent, pdf_path=pdf)
    db = _FakeAsyncSession(row)

    resp = await tos_module.download_signed_by_token("tok", _request(), db)
    assert resp.status_code == 200


async def test_naive_accepted_at_is_treated_as_utc(monkeypatch, tmp_path):
    """`accepted_at` is stored tz-aware in this model, but the guard must
    not crash if a legacy/naive value is ever encountered."""
    from app.routers import tos as tos_module

    monkeypatch.setattr(tos_module.settings, "tos_signed_download_expire_days", 730)
    pdf = tmp_path / "signed.pdf"
    pdf.write_bytes(b"%PDF-1.4 signed")
    naive_recent = datetime.utcnow() - timedelta(days=1)
    row = _row(accepted_at=naive_recent, pdf_path=pdf)
    db = _FakeAsyncSession(row)

    resp = await tos_module.download_signed_by_token("tok", _request(), db)
    assert resp.status_code == 200


# ── Rate limit is wired at all (full ASGI) ────────────────────────────


def _build_app(db):
    from app.database import get_db
    from app.routers.tos import router as tos_router

    app = FastAPI()
    app.include_router(tos_router)

    async def _get_db_override():
        yield db

    app.dependency_overrides[get_db] = _get_db_override
    from slowapi import Limiter, _rate_limit_exceeded_handler
    from slowapi.errors import RateLimitExceeded
    from app.utils.client_ip import get_trusted_client_ip

    app.state.limiter = Limiter(key_func=get_trusted_client_ip)
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    return app


def test_download_route_enforces_a_rate_limit(tmp_path, monkeypatch):
    """Pre-fix this route had NO @limiter.limit decorator at all — every
    other public route in this file does. 11 requests in the same minute
    from the same untrusted (unrecognised) peer must trip the 10/minute
    limit at least once."""
    from app.routers import tos as tos_module

    monkeypatch.setattr("app.config.settings.forwarded_allow_ips", "")
    pdf = tmp_path / "signed.pdf"
    pdf.write_bytes(b"%PDF-1.4 signed")
    row = _row(accepted_at=datetime.now(timezone.utc), pdf_path=pdf)
    db = _FakeAsyncSession(row)
    app = _build_app(db)
    client = TestClient(app)

    statuses = [client.get("/api/tos/signed/by-token/tok").status_code for _ in range(11)]
    assert 429 in statuses, f"expected a 429 among {statuses} — rate limit not enforced"
