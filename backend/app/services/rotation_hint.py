"""Redis side-channel for ADR-0003 rotation hints.

The DB stores only SHA-256 hashes of device API keys, which means the
backend cannot reconstruct a raw key once written. To deliver the new
raw key to the paired controller's device-health response during the
grace window, we stash the raw value in Redis under
``doc:rotation:hint:{device_id}`` with a TTL matching the grace window.

The hint is read ONLY when the request authenticated via the OLD key on
the device-health path. It is written ONLY at rotation time. It is
deleted when the Celery finalizer promotes the new key (best-effort —
the TTL handles the rest).

Redis is already a hard dependency of the backend (Celery broker), so
this introduces no new infrastructure. If Redis is unreachable at
rotation time we fail closed — see ADR-0003 §5.
"""

from __future__ import annotations

import logging
from typing import Optional

import redis.asyncio as aioredis
from redis.asyncio.client import Redis as AsyncRedis

from app.config import settings

logger = logging.getLogger("doc.rotation_hint")

_KEY_PREFIX = "doc:rotation:hint:"


class RotationHintBackendUnavailable(RuntimeError):
    """Raised when Redis cannot be reached for rotation-hint operations."""


def _key(device_id: str) -> str:
    return f"{_KEY_PREFIX}{device_id}"


async def _client() -> AsyncRedis:
    return aioredis.from_url(settings.redis_url, socket_timeout=2)


async def set_rotation_hint(
    *,
    device_id: str,
    raw_key: str,
    ttl_seconds: int,
) -> None:
    """Write the rotation hint with a TTL. Raises if Redis is unreachable."""
    try:
        r = await _client()
        try:
            await r.set(_key(device_id), raw_key, ex=ttl_seconds)
        finally:
            await r.aclose()
    except Exception as exc:
        raise RotationHintBackendUnavailable(str(exc)) from exc


async def get_rotation_hint(device_id: str) -> Optional[str]:
    """Read the rotation hint, or None if missing / Redis unreachable.

    Reads degrade gracefully — if Redis is down the device-health endpoint
    simply omits the ``rotated_key`` field; the controller retries on the
    next preflight tick. This matches the failure-mode contract documented
    in ADR-0003 §5.
    """
    try:
        r = await _client()
        try:
            raw = await r.get(_key(device_id))
        finally:
            await r.aclose()
    except Exception as exc:
        logger.warning(
            "rotation_hint_read_failed",
            extra={
                "event": "rotation_hint_read_failed",
                "device_id": device_id,
                "error": str(exc),
            },
        )
        return None

    if raw is None:
        return None
    return raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else str(raw)


async def delete_rotation_hint(device_id: str) -> None:
    """Best-effort delete; never raises. Used by the Celery finalizer."""
    try:
        r = await _client()
        try:
            await r.delete(_key(device_id))
        finally:
            await r.aclose()
    except Exception as exc:
        logger.warning(
            "rotation_hint_delete_failed",
            extra={
                "event": "rotation_hint_delete_failed",
                "device_id": device_id,
                "error": str(exc),
            },
        )


def delete_rotation_hint_sync(device_id: str) -> None:
    """Sync variant for the Celery finalizer task."""
    try:
        import redis  # local import keeps cold start unaffected

        r = redis.from_url(settings.redis_url, socket_timeout=2)
        r.delete(_key(device_id))
    except Exception as exc:
        logger.warning(
            "rotation_hint_delete_failed",
            extra={
                "event": "rotation_hint_delete_failed",
                "device_id": device_id,
                "error": str(exc),
            },
        )
