"""Redis-backed read-through cache for short-TTL external API calls.

Used by the weather endpoint to collapse 5 sequential aviation-API
calls (Open-Meteo + AviationWeather METAR/TFR/NOTAM + NWS) behind a
short-lived shared cache key. See ADR-0005 / docs/plans/2026-04-24-perf-audit.md.

Failure mode is **failure-open**: if Redis is unreachable on either GET
or SET, we log WARN and fall through to the live `build()` callback.
The endpoint stays responsive at the cost of a cache miss — never returns
500 because Redis blipped. Per ``feedback_prevent_failures.md`` and
``feedback_no_deferred_fixes.md``: this is the complete, shipped fix.

Logging contract (per ``CLAUDE.md`` §Logging):
- INFO  cache_hit  — every served-from-cache call (key + ttl_remaining).
- INFO  cache_miss — cache miss followed by a successful refill.
- WARN  cache_get_failed / cache_set_failed — Redis failure-open paths.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Awaitable, Callable

import redis.asyncio as aioredis

from app.config import settings

logger = logging.getLogger("doc.cache")

# Module-level singleton client. ``redis.asyncio`` clients are
# coroutine-safe and pool internally — one client per process is correct.
_client: aioredis.Redis | None = None


def _conn() -> aioredis.Redis:
    """Return the lazily-initialised module-level redis client."""
    global _client
    if _client is None:
        _client = aioredis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_timeout=2,
            socket_connect_timeout=2,
        )
    return _client


async def get_or_fetch(
    key: str,
    build: Callable[[], Awaitable[Any]],
    ttl_seconds: int,
) -> Any:
    """Return cached JSON for ``key`` or call ``build`` and cache its result.

    - On cache HIT: returns the parsed JSON immediately. Logs INFO with the
      remaining TTL so dashboards can see effective hit rate + freshness.
    - On cache MISS: awaits ``build()``, caches the JSON-encoded result with
      ``ttl_seconds`` expiry, and returns the freshly-built value.
    - On Redis failure (either GET or SET): logs WARN and **falls through**
      to a live ``build()`` call. Cache invisibility, never 500.

    The cached payload is JSON-serialised with ``default=str`` so datetime
    fields produced by the build callback survive the round-trip.
    """
    try:
        cached = await _conn().get(key)
        if cached is not None:
            try:
                ttl_remaining = await _conn().ttl(key)
            except Exception:
                ttl_remaining = -1
            logger.info(
                "cache_hit key=%s ttl_remaining=%ss",
                key,
                ttl_remaining,
            )
            return json.loads(cached)
    except Exception as exc:
        logger.warning(
            "cache_get_failed key=%s err=%s — falling through to live fetch",
            key,
            exc,
        )

    value = await build()

    try:
        await _conn().set(
            key,
            json.dumps(value, default=str),
            ex=ttl_seconds,
        )
        logger.info(
            "cache_miss key=%s refilled ttl=%ss",
            key,
            ttl_seconds,
        )
    except Exception as exc:
        logger.warning(
            "cache_set_failed key=%s err=%s — value returned uncached",
            key,
            exc,
        )

    return value


async def invalidate(key: str) -> None:
    """Drop a single cache key. Best-effort, swallows Redis failures."""
    try:
        await _conn().delete(key)
        logger.info("cache_invalidate key=%s", key)
    except Exception as exc:
        logger.warning("cache_invalidate_failed key=%s err=%s", key, exc)
