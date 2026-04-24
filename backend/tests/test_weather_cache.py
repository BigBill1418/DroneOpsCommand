"""Tests for the Redis-backed cache helper used by the weather router.

Covers:
- cache HIT returns the stored payload without calling ``build()``.
- cache MISS calls ``build()``, stores the JSON payload, returns it.
- Redis GET failure ⇒ failure-open, calls ``build()``, returns its value.
- Redis SET failure ⇒ failure-open, returns the freshly-built value.
- ``invalidate()`` is a best-effort no-op on Redis failure.

Tests use ``fakeredis`` to simulate Redis behaviour deterministically
without needing a live server. The cache module is patched at the
``_conn`` boundary so each test can inject its own client (or a
crashing one).
"""

from __future__ import annotations

import asyncio
import json

import pytest
import fakeredis.aioredis as fake

# Force conftest's env stubs to apply before importing the module.
import tests.conftest  # noqa: F401

from app.services import cache as cache_mod


@pytest.fixture(autouse=True)
def _reset_module_state(monkeypatch):
    """Each test gets a fresh fakeredis-backed singleton."""
    fake_client = fake.FakeRedis(decode_responses=True)
    monkeypatch.setattr(cache_mod, "_client", fake_client)
    monkeypatch.setattr(cache_mod, "_conn", lambda: fake_client)
    yield
    # fakeredis cleans up automatically, no aclose needed.


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


@pytest.mark.asyncio
async def test_cache_miss_calls_build_and_stores():
    calls = {"n": 0}

    async def build():
        calls["n"] += 1
        return {"airport": "KEUG", "temp_f": 62}

    out = await cache_mod.get_or_fetch("doc:weather:current:test", build, ttl_seconds=300)
    assert out == {"airport": "KEUG", "temp_f": 62}
    assert calls["n"] == 1

    raw = await cache_mod._conn().get("doc:weather:current:test")
    assert json.loads(raw) == {"airport": "KEUG", "temp_f": 62}


@pytest.mark.asyncio
async def test_cache_hit_skips_build():
    await cache_mod._conn().set(
        "doc:weather:current:hit",
        json.dumps({"cached": True}),
        ex=120,
    )
    calls = {"n": 0}

    async def build():
        calls["n"] += 1
        return {"cached": False}

    out = await cache_mod.get_or_fetch("doc:weather:current:hit", build, ttl_seconds=300)
    assert out == {"cached": True}
    assert calls["n"] == 0  # build() never invoked on hit


@pytest.mark.asyncio
async def test_redis_get_failure_falls_through(monkeypatch):
    class CrashingClient:
        async def get(self, *a, **kw):
            raise RuntimeError("redis unreachable")

        async def set(self, *a, **kw):
            return True

        async def ttl(self, *a, **kw):
            return -1

    crashing = CrashingClient()
    monkeypatch.setattr(cache_mod, "_conn", lambda: crashing)

    async def build():
        return {"live": True}

    out = await cache_mod.get_or_fetch("doc:weather:current:dead", build, ttl_seconds=300)
    assert out == {"live": True}  # served live despite Redis failure


@pytest.mark.asyncio
async def test_redis_set_failure_still_returns_built_value(monkeypatch):
    class HalfDeadClient:
        async def get(self, *a, **kw):
            return None  # cache miss

        async def set(self, *a, **kw):
            raise RuntimeError("redis full")

        async def ttl(self, *a, **kw):
            return -1

    monkeypatch.setattr(cache_mod, "_conn", lambda: HalfDeadClient())

    async def build():
        return {"survived_set_failure": True}

    out = await cache_mod.get_or_fetch("doc:weather:current:half", build, ttl_seconds=300)
    assert out == {"survived_set_failure": True}


@pytest.mark.asyncio
async def test_invalidate_removes_key():
    await cache_mod._conn().set("doc:weather:current:bye", json.dumps({"a": 1}), ex=60)
    await cache_mod.invalidate("doc:weather:current:bye")
    assert await cache_mod._conn().get("doc:weather:current:bye") is None


@pytest.mark.asyncio
async def test_invalidate_failure_is_swallowed(monkeypatch):
    class BoomClient:
        async def delete(self, *a, **kw):
            raise RuntimeError("nope")

    monkeypatch.setattr(cache_mod, "_conn", lambda: BoomClient())
    # Should not raise.
    await cache_mod.invalidate("doc:weather:current:never")
