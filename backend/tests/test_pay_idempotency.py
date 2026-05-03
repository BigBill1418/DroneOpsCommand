"""ADR-0011 v2.66.0 — pay/deposit + pay/balance idempotency.

`_reuse_existing_checkout_session` returns an existing Stripe session
URL when:
  - session_id is non-null
  - Stripe.Checkout.Session.retrieve returns payment_status != 'paid'
  - session.created is within the last 30 minutes

Otherwise returns None and the caller mints a fresh session.
"""

from __future__ import annotations

import time
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


class _NoopDB:
    async def execute(self, _stmt):
        class _R:
            def scalars(self):
                class _S:
                    def all(self):
                        return []
                return _S()
        return _R()


@pytest.mark.asyncio
async def test_reuse_returns_url_when_session_recent_and_unpaid():
    from app.routers.client_portal import _reuse_existing_checkout_session

    fake_session = SimpleNamespace(
        payment_status="unpaid",
        created=int(time.time()) - 60,  # 1 minute ago
        url="https://checkout.stripe.com/c/pay/cs_recent",
    )

    fake_settings = {"stripe_secret_key": "sk_test_x"}

    with patch(
        "app.services.stripe_service.get_stripe_settings",
        new=AsyncMock(return_value=fake_settings),
    ), patch("stripe.checkout.Session.retrieve", return_value=fake_session):
        url = await _reuse_existing_checkout_session(
            session_id="cs_recent",
            db=_NoopDB(),
            log_prefix="[TEST]",
        )

    assert url == "https://checkout.stripe.com/c/pay/cs_recent"


@pytest.mark.asyncio
async def test_reuse_returns_none_when_session_paid():
    """Even within the window, a paid session must not be reused —
    the customer would land on a 'thank you' page mid-flow."""
    from app.routers.client_portal import _reuse_existing_checkout_session

    fake_session = SimpleNamespace(
        payment_status="paid",
        created=int(time.time()) - 60,
        url="https://checkout.stripe.com/c/pay/cs_paid",
    )

    fake_settings = {"stripe_secret_key": "sk_test_x"}

    with patch(
        "app.services.stripe_service.get_stripe_settings",
        new=AsyncMock(return_value=fake_settings),
    ), patch("stripe.checkout.Session.retrieve", return_value=fake_session):
        url = await _reuse_existing_checkout_session(
            session_id="cs_paid",
            db=_NoopDB(),
            log_prefix="[TEST]",
        )

    assert url is None


@pytest.mark.asyncio
async def test_reuse_returns_none_when_session_too_old():
    """30-minute window: a 31-minute-old session must NOT be reused
    (line items may have been re-priced in between)."""
    from app.routers.client_portal import _reuse_existing_checkout_session

    fake_session = SimpleNamespace(
        payment_status="unpaid",
        created=int(time.time()) - int(timedelta(minutes=31).total_seconds()),
        url="https://checkout.stripe.com/c/pay/cs_old",
    )

    fake_settings = {"stripe_secret_key": "sk_test_x"}

    with patch(
        "app.services.stripe_service.get_stripe_settings",
        new=AsyncMock(return_value=fake_settings),
    ), patch("stripe.checkout.Session.retrieve", return_value=fake_session):
        url = await _reuse_existing_checkout_session(
            session_id="cs_old",
            db=_NoopDB(),
            log_prefix="[TEST]",
        )

    assert url is None


@pytest.mark.asyncio
async def test_reuse_returns_none_when_no_session_id():
    from app.routers.client_portal import _reuse_existing_checkout_session

    url = await _reuse_existing_checkout_session(
        session_id=None,
        db=_NoopDB(),
        log_prefix="[TEST]",
    )
    assert url is None


@pytest.mark.asyncio
async def test_reuse_returns_none_on_stripe_retrieve_failure():
    """Network failure → fall through and let the caller mint fresh."""
    from app.routers.client_portal import _reuse_existing_checkout_session

    fake_settings = {"stripe_secret_key": "sk_test_x"}

    with patch(
        "app.services.stripe_service.get_stripe_settings",
        new=AsyncMock(return_value=fake_settings),
    ), patch(
        "stripe.checkout.Session.retrieve",
        side_effect=Exception("network down"),
    ):
        url = await _reuse_existing_checkout_session(
            session_id="cs_unreachable",
            db=_NoopDB(),
            log_prefix="[TEST]",
        )
    assert url is None


@pytest.mark.asyncio
async def test_reuse_returns_none_when_no_stripe_secret_configured():
    """If Stripe isn't configured at all, the freshness probe can't
    run — caller's existing 'no Stripe configured' branch will catch
    this on the create_checkout_session path."""
    from app.routers.client_portal import _reuse_existing_checkout_session

    with patch(
        "app.services.stripe_service.get_stripe_settings",
        new=AsyncMock(return_value={"stripe_secret_key": ""}),
    ):
        url = await _reuse_existing_checkout_session(
            session_id="cs_anything",
            db=_NoopDB(),
            log_prefix="[TEST]",
        )
    assert url is None
