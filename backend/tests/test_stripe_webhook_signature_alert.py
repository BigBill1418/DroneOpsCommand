"""v2.66.0 Fix 2 — Stripe webhook signature failure fires an urgent
ntfy alert before raising 400.

These tests verify:
  - SignatureVerificationError → send_alert called with priority=2
    (urgent), topic='droneops-deposits', dedup_key set, and the 400
    is still raised.
  - ValueError (malformed payload) does NOT alert — that's just bad
    Stripe SDK input, not a misconfigured-secret signal.
  - Alert helper raising must NOT prevent the 400 from being raised
    (fail-soft on alert path).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import stripe
from fastapi import HTTPException


class _NoopDB:
    async def execute(self, _stmt):
        class _R:
            def scalars(self):
                class _S:
                    def all(self):
                        return []
                return _S()
        return _R()

    async def commit(self):
        pass

    async def flush(self):
        pass


def _mk_request(*, body=b"{}", sig_header="t=1,v1=invalid"):
    headers = {"stripe-signature": sig_header}

    async def _body():
        return body

    return SimpleNamespace(headers=headers, body=_body)


@pytest.mark.asyncio
async def test_signature_failure_fires_urgent_ntfy_alert():
    from app.routers import stripe_webhook

    request = _mk_request()
    db = _NoopDB()

    fake_settings = {
        "stripe_secret_key": "sk_test_x",
        "stripe_webhook_secret": "whsec_x",
        "stripe_publishable_key": "",
    }

    send_alert_mock = AsyncMock(return_value=True)

    with patch.object(
        stripe_webhook, "get_stripe_settings",
        new=AsyncMock(return_value=fake_settings),
    ), patch.object(
        stripe.Webhook, "construct_event",
        side_effect=stripe.SignatureVerificationError("bad sig", "t=1,v1=x"),
    ), patch(
        "app.services.ntfy.send_alert", new=send_alert_mock,
    ):
        with pytest.raises(HTTPException) as exc_info:
            await stripe_webhook.stripe_webhook(request, db)

    assert exc_info.value.status_code == 400
    send_alert_mock.assert_awaited_once()
    kwargs = send_alert_mock.await_args.kwargs
    assert kwargs["priority"] == 2  # urgent
    assert kwargs["topic"] == "droneops-deposits"
    assert kwargs["dedup_key"] == "stripe-webhook-sig-failed"
    assert kwargs["dedup_ttl_seconds"] == 300
    assert "STRIPE WEBHOOK SIGNATURE FAILED" in kwargs["title"]
    # Click URL routes to NOC's status page (ADR-0036).
    assert "noc-mastercontrol.barnardhq.com" in kwargs["click"]


@pytest.mark.asyncio
async def test_invalid_payload_does_not_fire_alert():
    """ValueError = malformed Stripe SDK input. Not a secret-rotation
    signal. Should still 400 but NOT page the operator."""
    from app.routers import stripe_webhook

    request = _mk_request(body=b"not-json")
    db = _NoopDB()

    fake_settings = {
        "stripe_secret_key": "sk_test_x",
        "stripe_webhook_secret": "whsec_x",
        "stripe_publishable_key": "",
    }

    send_alert_mock = AsyncMock(return_value=True)

    with patch.object(
        stripe_webhook, "get_stripe_settings",
        new=AsyncMock(return_value=fake_settings),
    ), patch.object(
        stripe.Webhook, "construct_event",
        side_effect=ValueError("malformed"),
    ), patch(
        "app.services.ntfy.send_alert", new=send_alert_mock,
    ):
        with pytest.raises(HTTPException) as exc_info:
            await stripe_webhook.stripe_webhook(request, db)

    assert exc_info.value.status_code == 400
    send_alert_mock.assert_not_called()


@pytest.mark.asyncio
async def test_signature_failure_alert_helper_failure_still_returns_400():
    """If the ntfy helper itself blows up, the 400 must STILL be raised
    so Stripe gets the failure response. Alerts are best-effort."""
    from app.routers import stripe_webhook

    request = _mk_request()
    db = _NoopDB()

    fake_settings = {
        "stripe_secret_key": "sk_test_x",
        "stripe_webhook_secret": "whsec_x",
        "stripe_publishable_key": "",
    }

    with patch.object(
        stripe_webhook, "get_stripe_settings",
        new=AsyncMock(return_value=fake_settings),
    ), patch.object(
        stripe.Webhook, "construct_event",
        side_effect=stripe.SignatureVerificationError("bad sig", "t=1,v1=x"),
    ), patch(
        "app.services.ntfy.send_alert",
        new=AsyncMock(side_effect=RuntimeError("ntfy down")),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await stripe_webhook.stripe_webhook(request, db)

    assert exc_info.value.status_code == 400
