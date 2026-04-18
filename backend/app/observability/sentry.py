"""Sentry/GlitchTip bootstrap for DroneOpsCommand.

DSN is read from ``SENTRY_DSN``; if absent, :func:`init_sentry` is a
no-op so single-tenant self-hosted installs (and dev/test) do not need
the central observability plane to be reachable. In prod (HSH-HQ) and
demo (CHAD-HQ override) the DSN is injected into ``~/droneops/.env`` /
``~/droneops/.env.demo`` out-of-band — see ``docs/adr/0001-observability.md``.

The :func:`_before_send` hook runs on every captured event and every
transaction before the SDK transmits. It delegates to
:func:`app.observability.pii.sanitize_event` which redacts emails and
loose phone numbers from free-text. If the scrubber itself raises, the
event is DROPPED rather than risk leaking an unsanitized payload.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from app.observability.pii import sanitize_event

logger = logging.getLogger("doc.observability.sentry")

_DEFAULT_TRACES_SAMPLE_RATE = 0.05


def _before_send(event: dict[str, Any], hint: dict[str, Any] | None) -> dict[str, Any] | None:
    """Redact PII on every event. Drop the event if sanitation fails."""
    try:
        return sanitize_event(event)
    except Exception:  # noqa: BLE001 — fail closed on scrubber errors
        logger.exception("pii.sanitize_event raised — dropping event")
        return None


def init_sentry(service: str) -> bool:
    """Initialize the Sentry SDK for ``service``.

    Returns True if the SDK was initialized, False otherwise (missing
    DSN, import error, or disabled). Callers must not block on the
    return value — Sentry is best-effort observability and must never
    fail app startup.
    """
    dsn = os.environ.get("SENTRY_DSN", "").strip()
    if not dsn:
        logger.info("sentry.skipped: no SENTRY_DSN set")
        return False

    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.celery import CeleryIntegration
        from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration
        from sentry_sdk.integrations.logging import LoggingIntegration
    except ImportError as exc:
        logger.warning("sentry.sdk_not_installed: %s", exc)
        return False

    try:
        rate = float(os.environ.get("SENTRY_TRACES_SAMPLE_RATE", str(_DEFAULT_TRACES_SAMPLE_RATE)))
    except ValueError:
        rate = _DEFAULT_TRACES_SAMPLE_RATE

    release = os.environ.get("APP_VERSION") or os.environ.get("DRONEOPS_VERSION") or "dev"
    env_tag = os.environ.get("ENV", "production")

    try:
        sentry_sdk.init(
            dsn=dsn,
            release=f"droneops@{release}",
            environment=env_tag,
            traces_sample_rate=rate,
            send_default_pii=False,
            before_send=_before_send,
            before_send_transaction=_before_send,
            max_breadcrumbs=50,
            integrations=[
                FastApiIntegration(transaction_style="endpoint"),
                CeleryIntegration(monitor_beat_tasks=False),
                SqlalchemyIntegration(),
                LoggingIntegration(level=logging.INFO, event_level=logging.ERROR),
            ],
        )
    except Exception:  # noqa: BLE001 — never fail startup on observability
        logger.exception("sentry.init_failed — continuing without Sentry")
        return False

    # Stamp tags so GlitchTip can filter per-service and per-tenant on
    # the same project (locked decision: one project per product line,
    # tenant label disambiguates shared vs per-customer streams).
    sentry_sdk.set_tag("service", service)
    sentry_sdk.set_tag("tenant", os.environ.get("TENANT", "shared"))
    sentry_sdk.set_tag("env", env_tag)
    logger.info("sentry.initialized service=%s rate=%s release=%s env=%s",
                service, rate, release, env_tag)
    return True
