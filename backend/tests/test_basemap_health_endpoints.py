"""Admin tile-health endpoints (ADR-0046).

Hermetic: an in-memory SQLite `system_settings` table, the auth dep overridden,
and `run_probe` patched so no test ever touches a tile provider — hitting Esri
or OpenStreetMap from a test loop is both flaky and, for OSM, the automated
fetching its Tile Usage Policy exists to stop.
"""

from __future__ import annotations

import inspect
import json
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.auth.jwt import get_current_user
from app.database import get_db
from app.models.system_settings import SystemSetting
from app.routers import basemap_health
from app.services.basemap_probe import SETTING_LAST_RESULT, SETTING_NTFY_ENABLED


def _probe_result(ok: bool = True, checked_at: datetime | None = None) -> dict:
    stamp = (checked_at or datetime.now(timezone.utc)).isoformat()
    return {
        "checked_at": stamp,
        "probe_tile": {"z": 10, "x": 163, "y": 373},
        "baseline_captured_at": "2026-09-21T00:00:00+00:00",
        "ok": ok,
        "layers_ok": 5 if ok else 4,
        "layers_total": 5,
        "failing": [] if ok else ["esri_dark_base"],
        "layers": {"esri_dark_base": {"verdict": "ok" if ok else "changed", "reasons": []}},
    }


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SystemSetting.__table__.create)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with Session() as session:
        yield session
    await engine.dispose()


@pytest.fixture
def client(db: AsyncSession):
    app = FastAPI()
    app.include_router(basemap_health.router)

    async def _db_override():
        yield db

    async def _user_override():
        return SimpleNamespace(username="op@test.example.com", id=uuid.uuid4())

    app.dependency_overrides[get_db] = _db_override
    app.dependency_overrides[get_current_user] = _user_override
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")


# ── GET ────────────────────────────────────────────────────────────────


async def test_get_distinguishes_never_run_from_healthy(client, db):
    """`null` is not `ok: true`. A probe that has never run must not read as
    a green one."""
    resp = await client.get("/api/admin/basemap/tile-health")

    assert resp.status_code == 200
    body = resp.json()
    assert body["last_result"] is None
    assert body["ntfy_enabled"] is False
    # The shipped baseline is reported regardless, so a missing fixture shows.
    assert body["baseline_captured_at"]
    assert "esri_dark_base" in body["baseline_layers"]


async def test_get_returns_the_persisted_result(client, db):
    db.add(SystemSetting(key=SETTING_LAST_RESULT, value=json.dumps(_probe_result(ok=False))))
    await db.commit()

    body = (await client.get("/api/admin/basemap/tile-health")).json()

    assert body["last_result"]["ok"] is False
    assert body["last_result"]["failing"] == ["esri_dark_base"]


async def test_get_survives_a_corrupt_persisted_result(client, db):
    """A truncated row must not 500 the admin page it is displayed on."""
    db.add(SystemSetting(key=SETTING_LAST_RESULT, value="{not json"))
    await db.commit()

    resp = await client.get("/api/admin/basemap/tile-health")

    assert resp.status_code == 200
    assert resp.json()["last_result"] is None


async def test_get_requires_authentication():
    """Without the dependency override the route is closed."""
    app = FastAPI()
    app.include_router(basemap_health.router)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as anon:
        assert (await anon.get("/api/admin/basemap/tile-health")).status_code in (401, 403)


# ── POST /run ──────────────────────────────────────────────────────────


async def test_manual_run_executes_the_probe_and_persists_it(client, db, monkeypatch):
    calls = []
    monkeypatch.setattr(basemap_health, "run_probe", lambda: (calls.append(1), _probe_result())[1])

    resp = await client.post("/api/admin/basemap/tile-health/run")

    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert len(calls) == 1

    stored = (await client.get("/api/admin/basemap/tile-health")).json()["last_result"]
    assert stored["layers_ok"] == 5


async def test_manual_run_overwrites_rather_than_duplicating(client, db, monkeypatch):
    """`system_settings.key` is the primary key — a second insert would raise."""
    monkeypatch.setattr(basemap_health, "run_probe", lambda: _probe_result())
    assert (await client.post("/api/admin/basemap/tile-health/run")).status_code == 200

    old = datetime.now(timezone.utc) - timedelta(hours=1)
    monkeypatch.setattr(basemap_health, "run_probe", lambda: _probe_result(ok=False, checked_at=old))
    # Age the stored row past the cooldown so the second run is allowed.
    row = await db.get(SystemSetting, SETTING_LAST_RESULT)
    row.value = json.dumps(_probe_result(checked_at=old))
    await db.commit()

    resp = await client.post("/api/admin/basemap/tile-health/run")

    assert resp.status_code == 200
    assert resp.json()["ok"] is False


async def test_manual_run_is_cooldown_limited(client, db, monkeypatch):
    """Each run costs a request at every provider; the OSMF policy is explicit
    that repeated automated fetching is what gets an app blocked."""
    monkeypatch.setattr(basemap_health, "run_probe", lambda: _probe_result())
    assert (await client.post("/api/admin/basemap/tile-health/run")).status_code == 200

    second = await client.post("/api/admin/basemap/tile-health/run")

    assert second.status_code == 429
    assert "between manual runs" in second.json()["detail"]


async def test_manual_run_never_publishes_to_ntfy(client, db, monkeypatch):
    """A hand-run is a diagnostic. If it took the alert path it could burn the
    24h cooldown and mask the next scheduled run."""
    monkeypatch.setattr(basemap_health, "run_probe", lambda: _probe_result(ok=False))
    db.add(SystemSetting(key=SETTING_NTFY_ENABLED, value="true"))
    await db.commit()

    sent = []
    import app.services.ntfy as ntfy_mod
    monkeypatch.setattr(ntfy_mod, "send_alert_sync", lambda *a, **k: sent.append(a))

    assert (await client.post("/api/admin/basemap/tile-health/run")).status_code == 200
    assert sent == []
    # Belt and braces: the monkeypatch above only catches a late-bound call,
    # so also pin that the router holds no reference to the alert transport
    # at all. A top-level `from app.services.ntfy import ...` would slip past
    # the patch but not past this.
    source = inspect.getsource(basemap_health)
    assert "send_alert" not in source
    assert "app.services.ntfy" not in source


# ── PUT /ntfy ──────────────────────────────────────────────────────────


async def test_ntfy_gate_round_trips(client, db):
    assert (await client.get("/api/admin/basemap/tile-health")).json()["ntfy_enabled"] is False

    resp = await client.put("/api/admin/basemap/tile-health/ntfy", json={"enabled": True})
    assert resp.status_code == 200
    assert resp.json()["ntfy_enabled"] is True
    assert (await client.get("/api/admin/basemap/tile-health")).json()["ntfy_enabled"] is True

    await client.put("/api/admin/basemap/tile-health/ntfy", json={"enabled": False})
    assert (await client.get("/api/admin/basemap/tile-health")).json()["ntfy_enabled"] is False


async def test_ntfy_gate_rejects_a_missing_flag(client):
    assert (await client.put("/api/admin/basemap/tile-health/ntfy", json={})).status_code == 422
