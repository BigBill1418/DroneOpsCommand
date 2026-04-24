"""Pushover notification service — ADR-0002 §5 silent-drift watchdog.

Single outbound path for operator alerts from the DroneOps backend. The
companion (DJI RC Pro) cannot reach Pushover itself — any reachability
loss must be detected server-side and pushed from here.

Design notes:

- Keys are handled as Redis-backed dedup entries so a long outage cannot
  spam Bill's phone. Each alert topic carries its own (category, subject)
  dedup key and a TTL.
- The network call uses a short timeout; Pushover outage never blocks a
  request path. Best-effort: a failure is logged at WARN and the
  worker/API continues.
- No raw device API keys are ever sent in the message body — only the
  human-readable label and the 8-char SHA-256 prefix.
"""

from __future__ import annotations

import logging
from typing import Optional

import httpx
import redis.asyncio as aioredis

from app.config import settings

logger = logging.getLogger("doc.pushover")

_PUSHOVER_URL = "https://api.pushover.net/1/messages.json"
_REDIS_KEY_PREFIX = "doc:pushover:dedup:"


def _configured() -> bool:
    return bool(settings.pushover_token and settings.pushover_user_key)


async def send_alert(
    title: str,
    message: str,
    *,
    dedup_key: Optional[str] = None,
    dedup_ttl_seconds: int = 3600,
    priority: int = 0,
) -> bool:
    """Send a Pushover notification.

    Returns True if a request was sent (or skipped because Pushover is not
    configured, which is treated as a successful no-op). Returns False on
    transient network/HTTP errors — the caller may retry via the normal
    watchdog cadence.

    `dedup_key` is a caller-owned identifier. If provided, we set a
    Redis SETNX with ``dedup_ttl_seconds`` TTL; subsequent calls within
    the TTL window are suppressed. Unset = every call delivers.
    """
    if not _configured():
        logger.debug("pushover skipped — not configured")
        return True

    if dedup_key:
        try:
            r = aioredis.from_url(settings.redis_url, socket_timeout=2)
            key = f"{_REDIS_KEY_PREFIX}{dedup_key}"
            acquired = await r.set(key, "1", ex=dedup_ttl_seconds, nx=True)
            await r.aclose()
            if not acquired:
                logger.info("pushover suppressed by dedup key=%s", dedup_key)
                return True
        except Exception as exc:
            # Never fail alerting because Redis is down — we'd rather
            # double-send than silently drop.
            logger.warning("pushover dedup check failed (sending anyway): %s", exc)

    payload = {
        "token": settings.pushover_token,
        "user": settings.pushover_user_key,
        "title": title[:250],
        "message": message[:1024],
        "priority": priority,
    }
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.post(_PUSHOVER_URL, data=payload)
        if resp.status_code >= 400:
            logger.warning(
                "pushover send failed: status=%s body=%s",
                resp.status_code,
                resp.text[:200],
            )
            return False
        logger.info("pushover alert sent title=%r", title)
        return True
    except Exception as exc:
        logger.warning("pushover send exception: %s", exc)
        return False


def send_alert_sync(
    title: str,
    message: str,
    *,
    dedup_key: Optional[str] = None,
    dedup_ttl_seconds: int = 3600,
    priority: int = 0,
) -> bool:
    """Sync variant for Celery tasks / synchronous code paths.

    Uses a short synchronous httpx client. Dedup via sync redis client.
    """
    if not _configured():
        logger.debug("pushover skipped — not configured")
        return True

    if dedup_key:
        try:
            import redis

            r = redis.from_url(settings.redis_url, socket_timeout=2)
            key = f"{_REDIS_KEY_PREFIX}{dedup_key}"
            acquired = r.set(key, "1", ex=dedup_ttl_seconds, nx=True)
            if not acquired:
                logger.info("pushover suppressed by dedup key=%s", dedup_key)
                return True
        except Exception as exc:
            logger.warning("pushover dedup check failed (sending anyway): %s", exc)

    payload = {
        "token": settings.pushover_token,
        "user": settings.pushover_user_key,
        "title": title[:250],
        "message": message[:1024],
        "priority": priority,
    }
    try:
        with httpx.Client(timeout=5) as client:
            resp = client.post(_PUSHOVER_URL, data=payload)
        if resp.status_code >= 400:
            logger.warning(
                "pushover send failed: status=%s body=%s",
                resp.status_code,
                resp.text[:200],
            )
            return False
        logger.info("pushover alert sent title=%r", title)
        return True
    except Exception as exc:
        logger.warning("pushover send exception: %s", exc)
        return False
