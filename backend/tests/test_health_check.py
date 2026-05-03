"""v2.66.0 Fix 7 — `/api/health` actually probes DB + Redis + Stripe.

A failed probe must return 503 with degraded body so Watchtower /
NOC / docker compose health treat it as unhealthy.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class _OkDB:
    async def execute(self, _stmt):
        return None


class _BrokenDB:
    async def execute(self, _stmt):
        raise RuntimeError("connection refused")


def _reset_health_cache():
    from app.main import _HEALTH_CACHE
    _HEALTH_CACHE.update({
        "checked_at": 0.0,
        "stripe_status": None,
        "stripe_error": None,
    })


@pytest.mark.asyncio
async def test_health_returns_healthy_when_all_probes_pass():
    from app.main import health_check

    _reset_health_cache()

    fake_redis = MagicMock()
    fake_redis.ping = AsyncMock(return_value=True)
    fake_redis.aclose = AsyncMock(return_value=None)

    with patch("redis.asyncio.from_url", return_value=fake_redis), \
         patch("app.main._probe_stripe_cached",
               new=AsyncMock(return_value=("ok", None))):
        body = await health_check(db=_OkDB())

    # 200 path returns the dict directly (not a JSONResponse).
    assert isinstance(body, dict)
    assert body["status"] == "healthy"
    assert body["db"] == "ok"
    assert body["redis"] == "ok"
    assert body["stripe"] == "ok"


@pytest.mark.asyncio
async def test_health_returns_503_when_db_fails():
    from app.main import health_check
    from fastapi.responses import JSONResponse

    _reset_health_cache()

    fake_redis = MagicMock()
    fake_redis.ping = AsyncMock(return_value=True)
    fake_redis.aclose = AsyncMock(return_value=None)

    with patch("redis.asyncio.from_url", return_value=fake_redis), \
         patch("app.main._probe_stripe_cached",
               new=AsyncMock(return_value=("ok", None))):
        resp = await health_check(db=_BrokenDB())

    assert isinstance(resp, JSONResponse)
    assert resp.status_code == 503
    import json
    body = json.loads(resp.body.decode())
    assert body["status"] == "degraded"
    assert body["db"] == "error"
    assert body["db_error"] == "RuntimeError"


@pytest.mark.asyncio
async def test_health_returns_503_when_redis_fails():
    from app.main import health_check
    from fastapi.responses import JSONResponse

    _reset_health_cache()

    fake_redis = MagicMock()
    fake_redis.ping = AsyncMock(side_effect=ConnectionError("redis down"))
    fake_redis.aclose = AsyncMock(return_value=None)

    with patch("redis.asyncio.from_url", return_value=fake_redis), \
         patch("app.main._probe_stripe_cached",
               new=AsyncMock(return_value=("ok", None))):
        resp = await health_check(db=_OkDB())

    assert isinstance(resp, JSONResponse)
    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_health_returns_503_when_stripe_fails():
    from app.main import health_check
    from fastapi.responses import JSONResponse

    _reset_health_cache()

    fake_redis = MagicMock()
    fake_redis.ping = AsyncMock(return_value=True)
    fake_redis.aclose = AsyncMock(return_value=None)

    with patch("redis.asyncio.from_url", return_value=fake_redis), \
         patch("app.main._probe_stripe_cached",
               new=AsyncMock(return_value=("error", "AuthenticationError"))):
        resp = await health_check(db=_OkDB())

    assert isinstance(resp, JSONResponse)
    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_health_unconfigured_stripe_is_healthy():
    """Stripe is optional (self-hosted operators). 'unconfigured' must
    NOT degrade the overall status."""
    from app.main import health_check

    _reset_health_cache()

    fake_redis = MagicMock()
    fake_redis.ping = AsyncMock(return_value=True)
    fake_redis.aclose = AsyncMock(return_value=None)

    with patch("redis.asyncio.from_url", return_value=fake_redis), \
         patch("app.main._probe_stripe_cached",
               new=AsyncMock(return_value=("unconfigured", None))):
        body = await health_check(db=_OkDB())

    assert isinstance(body, dict)
    assert body["status"] == "healthy"
    assert body["stripe"] == "unconfigured"
