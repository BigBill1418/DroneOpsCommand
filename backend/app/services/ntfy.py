"""ntfy notification service — ADR-0036 transport for ADR-0002 §5 + ADR-0003 alerts.

Single outbound path for operator alerts from the DroneOps backend. The
companion (DJI RC Pro) cannot reach the alert transport itself — any
reachability loss is detected server-side and published from here.

Migration note (ADR-0036, 2026-04-25): this module replaces the prior
``app.services.pushover`` module with the same public API
(``send_alert``, ``send_alert_sync``) so callers never had to change
their parameter shapes. Only the transport changed:

- pushover.net → self-hosted ntfy at ``ntfy.barnardhq.com`` (BOS-HQ)
- Bearer auth (``NTFY_DRONEOPS_PUBLISHER_TOKEN``) instead of two
  Pushover keys.
- Publisher-side fallback to ``ntfy.sh/<droneops-fallback-topic>`` on
  primary failure, with ``[FALLBACK]`` prepended to the title.
- Click URL follows the ADR-0036 3-tier priority contract (record →
  product section → ``noc.barnardhq.com/status/droneops``).

The watchdog contract from ADR-0002 §5 + ADR-0003 is preserved
unchanged:

- Same Redis-backed dedup with caller-owned key + TTL (no spam during
  long outages).
- Same fail-open behaviour on Redis errors (rather double-send than
  silently drop).
- Same ``int`` priority parameter shape (mapped to ntfy named priority
  for the wire — ``default`` / ``high`` / ``urgent``).
- Same return contract (``True`` = sent or no-op, ``False`` = transient
  send failure the caller may retry).
- Same 5s timeout budget (5s primary + 5s fallback worst case).
- No raw publisher token ever appears in logs or error messages.
"""

from __future__ import annotations

import logging
from typing import Iterable, Optional

import httpx
import redis.asyncio as aioredis

from app.config import settings

logger = logging.getLogger("doc.ntfy")

# Per ADR-0036 §Architecture — ntfy lives on BOS-HQ behind the existing
# cloudflared tunnel pattern.
_PRIMARY_BASE = "https://ntfy.barnardhq.com"
_FALLBACK_BASE = "https://ntfy.sh"

# Default topic for any caller that does not override. ADR-0036 §Topic
# shape: ``<service>-<purpose>``. Existing call sites are watchdog
# alerts, so ``droneops-alerts`` is the right default.
_DEFAULT_TOPIC = "droneops-alerts"

# Pinned per-service fallback topic from
# ~/noc-master/data/ntfy-fallback-topics.yml. The obscured suffix is the
# whole point — this topic is on the public internet (anyone subscribed
# can read messages), so the topic name is the access control. Bill's
# phone subscribes; nobody else.
_FALLBACK_TOPIC = "barnardhq-fleet-droneops-81b49d71de0f3e9fcf166e57f3c9846b"

# Click URL fallback when no record-specific or product-section URL is
# in scope. ADR-0036 §Click URL priority tier 3.
_DEFAULT_CLICK = "https://noc.barnardhq.com/status/droneops"

# Title prefix per ADR-0036 §Notification standard.
_TITLE_PREFIX = "[DroneOpsCommand]"

# Redis dedup key prefix is preserved across the migration so any
# in-flight dedup entry from the old pushover module continues to apply
# during the cutover. Bill explicitly does not want a second alert for
# the same condition just because the transport was swapped.
_REDIS_KEY_PREFIX = "doc:pushover:dedup:"

# Timeout budget — best-effort, never blocks the caller longer than
# this. Matches the pushover module's 5s budget; the fallback retry
# adds another 5s only on primary failure.
_PRIMARY_TIMEOUT = 5.0
_FALLBACK_TIMEOUT = 5.0


def _publisher_token() -> str:
    """Return the publisher token, or empty string if unset.

    The token is read at call time (not import time) so a runtime
    config change picks up without a process restart. ``settings``
    itself is loaded once at import; the env-backed value is the
    single source of truth.
    """
    return settings.ntfy_droneops_publisher_token or ""


def _configured() -> bool:
    """ntfy is "configured" if a publisher token is present.

    Same fail-soft behaviour as the prior pushover module: an unset
    token is a successful no-op (callers do not need to know whether
    the operator has wired the env yet). Falls back to fallback path
    only if token is set; otherwise returns True without any send.
    """
    return bool(_publisher_token())


def _map_priority(priority: int) -> str:
    """Map the numeric Pushover-shaped priority to ntfy named priority.

    The prior module's `priority: int = 0` parameter shape is preserved
    so callers do not change. Mapping per ADR-0036 §Priority:

      <0  -> "low"      (Pushover -2/-1: quiet/no-vibrate)
      0   -> "default"  (Pushover 0: normal)
      1   -> "high"     (Pushover 1: high-priority bypass DND)
      >=2 -> "urgent"   (Pushover 2: emergency / sticky)
    """
    if priority < 0:
        return "low"
    if priority == 0:
        return "default"
    if priority == 1:
        return "high"
    return "urgent"


def _build_headers(
    *,
    title: str,
    priority: int,
    click: Optional[str],
    tags: Optional[Iterable[str]],
    publisher_token: Optional[str],
    fallback: bool = False,
) -> dict[str, str]:
    """Build the ntfy header bundle per ADR-0036 §Notification standard.

    ``fallback=True`` prepends ``[FALLBACK]`` to the title and omits
    the Authorization header (the public ntfy.sh fallback topic has no
    auth — its access control is the obscured topic name).
    """
    full_title = f"{_TITLE_PREFIX} {title}"[:250]
    if fallback:
        full_title = f"[FALLBACK] {full_title}"[:250]

    headers: dict[str, str] = {
        "Title": full_title,
        "Priority": _map_priority(priority),
        "Click": click or _DEFAULT_CLICK,
    }
    if tags:
        # Filter out empties so callers can pass `tags or []` safely.
        joined = ",".join(t for t in tags if t)
        if joined:
            headers["Tags"] = joined
    if not fallback and publisher_token:
        headers["Authorization"] = f"Bearer {publisher_token}"
    return headers


async def _try_primary_async(
    *,
    topic: str,
    body: str,
    headers: dict[str, str],
) -> bool:
    """POST to the self-hosted ntfy primary. Returns True on 2xx."""
    try:
        async with httpx.AsyncClient(timeout=_PRIMARY_TIMEOUT) as client:
            resp = await client.post(
                f"{_PRIMARY_BASE}/{topic}",
                content=body.encode("utf-8"),
                headers=headers,
            )
        if resp.status_code >= 400:
            logger.warning(
                "ntfy primary failed: status=%s body=%s",
                resp.status_code,
                resp.text[:200],
            )
            return False
        return True
    except Exception as exc:
        logger.warning("ntfy primary exception: %s", exc)
        return False


async def _try_fallback_async(
    *,
    body: str,
    headers: dict[str, str],
) -> bool:
    """POST to the public ntfy.sh fallback topic. Returns True on 2xx."""
    try:
        async with httpx.AsyncClient(timeout=_FALLBACK_TIMEOUT) as client:
            resp = await client.post(
                f"{_FALLBACK_BASE}/{_FALLBACK_TOPIC}",
                content=body.encode("utf-8"),
                headers=headers,
            )
        if resp.status_code >= 400:
            logger.warning(
                "ntfy fallback failed: status=%s body=%s",
                resp.status_code,
                resp.text[:200],
            )
            return False
        return True
    except Exception as exc:
        logger.warning("ntfy fallback exception: %s; alert dropped", exc)
        return False


def _try_primary_sync(
    *,
    topic: str,
    body: str,
    headers: dict[str, str],
) -> bool:
    try:
        with httpx.Client(timeout=_PRIMARY_TIMEOUT) as client:
            resp = client.post(
                f"{_PRIMARY_BASE}/{topic}",
                content=body.encode("utf-8"),
                headers=headers,
            )
        if resp.status_code >= 400:
            logger.warning(
                "ntfy primary failed: status=%s body=%s",
                resp.status_code,
                resp.text[:200],
            )
            return False
        return True
    except Exception as exc:
        logger.warning("ntfy primary exception: %s", exc)
        return False


def _try_fallback_sync(
    *,
    body: str,
    headers: dict[str, str],
) -> bool:
    try:
        with httpx.Client(timeout=_FALLBACK_TIMEOUT) as client:
            resp = client.post(
                f"{_FALLBACK_BASE}/{_FALLBACK_TOPIC}",
                content=body.encode("utf-8"),
                headers=headers,
            )
        if resp.status_code >= 400:
            logger.warning(
                "ntfy fallback failed: status=%s body=%s",
                resp.status_code,
                resp.text[:200],
            )
            return False
        return True
    except Exception as exc:
        logger.warning("ntfy fallback exception: %s; alert dropped", exc)
        return False


async def send_alert(
    title: str,
    message: str,
    *,
    dedup_key: Optional[str] = None,
    dedup_ttl_seconds: int = 3600,
    priority: int = 0,
    topic: Optional[str] = None,
    click: Optional[str] = None,
    tags: Optional[Iterable[str]] = None,
) -> bool:
    """Send an operator alert via ntfy.

    Public API preserved from the pre-ADR-0036 ``pushover.send_alert``
    so call sites in ``app.auth.device`` and
    ``app.routers.admin_device_rotation`` continue to work unchanged.

    Returns True if a request was sent (or skipped because ntfy is not
    configured, treated as a successful no-op). Returns False when both
    primary and fallback fail — the caller may retry via the normal
    watchdog cadence.

    ``dedup_key`` is a caller-owned identifier. If provided, we set a
    Redis SETNX with ``dedup_ttl_seconds`` TTL; subsequent calls within
    the TTL window are suppressed. Unset = every call delivers.

    Optional ADR-0036 fields:

    - ``topic`` — override the default ``droneops-alerts`` topic.
      Should follow ``<service>-<purpose>`` per the standard.
    - ``click`` — record-specific or product-section URL per the
      3-tier priority order. Defaults to NOC's
      ``/status/droneops`` page.
    - ``tags`` — visual indicators (``warning``, ``rotating_light``,
      etc.) plus context tags. Comma-joined into the ``Tags`` header.
    """
    if not _configured():
        logger.debug("ntfy skipped — not configured")
        return True

    if dedup_key:
        try:
            r = aioredis.from_url(settings.redis_url, socket_timeout=2)
            key = f"{_REDIS_KEY_PREFIX}{dedup_key}"
            acquired = await r.set(key, "1", ex=dedup_ttl_seconds, nx=True)
            await r.aclose()
            if not acquired:
                logger.info("ntfy suppressed by dedup key=%s", dedup_key)
                return True
        except Exception as exc:
            # Fail-open: never drop alerts because Redis is down. Same
            # behaviour the pushover module had — caller may receive a
            # duplicate, which is preferable to silent drop during the
            # exact failure modes the watchdog is meant to surface.
            logger.warning("ntfy dedup check failed (sending anyway): %s", exc)

    body = message[:1024]
    target_topic = topic or _DEFAULT_TOPIC
    token = _publisher_token()

    primary_headers = _build_headers(
        title=title,
        priority=priority,
        click=click,
        tags=tags,
        publisher_token=token,
        fallback=False,
    )
    if await _try_primary_async(
        topic=target_topic, body=body, headers=primary_headers
    ):
        logger.info("ntfy alert sent topic=%s title=%r", target_topic, title)
        return True

    fallback_headers = _build_headers(
        title=title,
        priority=priority,
        click=click,
        tags=tags,
        publisher_token=None,
        fallback=True,
    )
    if await _try_fallback_async(body=body, headers=fallback_headers):
        logger.info(
            "ntfy fallback alert sent topic=%s title=%r",
            _FALLBACK_TOPIC,
            title,
        )
        return True

    logger.warning("ntfy alert dropped (primary + fallback both failed)")
    return False


def send_alert_sync(
    title: str,
    message: str,
    *,
    dedup_key: Optional[str] = None,
    dedup_ttl_seconds: int = 3600,
    priority: int = 0,
    topic: Optional[str] = None,
    click: Optional[str] = None,
    tags: Optional[Iterable[str]] = None,
) -> bool:
    """Sync variant for Celery tasks / synchronous code paths.

    Public API preserved from the pre-ADR-0036
    ``pushover.send_alert_sync``. Uses sync httpx + sync redis. Same
    primary→fallback contract as the async variant.
    """
    if not _configured():
        logger.debug("ntfy skipped — not configured")
        return True

    if dedup_key:
        try:
            import redis  # local import — sync redis client is heavy

            r = redis.from_url(settings.redis_url, socket_timeout=2)
            key = f"{_REDIS_KEY_PREFIX}{dedup_key}"
            acquired = r.set(key, "1", ex=dedup_ttl_seconds, nx=True)
            if not acquired:
                logger.info("ntfy suppressed by dedup key=%s", dedup_key)
                return True
        except Exception as exc:
            logger.warning("ntfy dedup check failed (sending anyway): %s", exc)

    body = message[:1024]
    target_topic = topic or _DEFAULT_TOPIC
    token = _publisher_token()

    primary_headers = _build_headers(
        title=title,
        priority=priority,
        click=click,
        tags=tags,
        publisher_token=token,
        fallback=False,
    )
    if _try_primary_sync(topic=target_topic, body=body, headers=primary_headers):
        logger.info("ntfy alert sent topic=%s title=%r", target_topic, title)
        return True

    fallback_headers = _build_headers(
        title=title,
        priority=priority,
        click=click,
        tags=tags,
        publisher_token=None,
        fallback=True,
    )
    if _try_fallback_sync(body=body, headers=fallback_headers):
        logger.info(
            "ntfy fallback alert sent topic=%s title=%r",
            _FALLBACK_TOPIC,
            title,
        )
        return True

    logger.warning("ntfy alert dropped (primary + fallback both failed)")
    return False
