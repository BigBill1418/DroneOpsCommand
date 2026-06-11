"""Audit P1-3 (2026-06-11) — Stripe SDK calls must not block the event loop.

The synchronous `stripe` SDK makes blocking HTTPS round-trips to
api.stripe.com. Every network-touching call site now routes through
`stripe_service._stripe_call`, which offloads to the default
thread-pool executor (same pattern as the missions.py/aircraft.py
image-resize offload). These tests prove:

  1. `_stripe_call` runs the SDK function in a worker thread (not the
     event-loop thread), passes args/kwargs through, and propagates
     exceptions unchanged — so callers keep their retry/error semantics.
  2. `create_checkout_session`, `_resolve_payment_method`, and the
     client-portal `_reuse_existing_checkout_session` idempotency path
     all still behave identically through the executor path.

`stripe.Webhook.construct_event` is CPU-only (HMAC check, no network)
and intentionally remains inline — not covered here.

All Stripe + DB calls are mocked. No real network, no Postgres.
"""

from __future__ import annotations

import threading
import time
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import stripe

from app.services.stripe_service import _stripe_call


_FAKE_STRIPE_CFG = {
    "stripe_secret_key": "sk_test_fake",
    "stripe_webhook_secret": "whsec_fake",
    "stripe_publishable_key": "pk_test_fake",
}


# ── _stripe_call helper ───────────────────────────────────────────────
async def test_stripe_call_runs_off_the_event_loop_thread():
    """The whole point of the fix: the SDK call must execute in a
    worker thread, leaving the event loop free."""
    loop_thread_id = threading.get_ident()
    seen = {}

    def fake_sdk_call(positional, *, keyword=None):
        seen["thread_id"] = threading.get_ident()
        seen["positional"] = positional
        seen["keyword"] = keyword
        return "sdk-result"

    result = await _stripe_call(fake_sdk_call, "pos-arg", keyword="kw-arg")

    assert result == "sdk-result"
    assert seen["positional"] == "pos-arg"
    assert seen["keyword"] == "kw-arg"
    assert seen["thread_id"] != loop_thread_id, (
        "_stripe_call executed on the event-loop thread — executor offload broken"
    )


async def test_stripe_call_propagates_exceptions_unchanged():
    """Error semantics must be identical to the inline call — callers
    catch stripe.StripeError / Exception exactly as before."""

    def boom():
        raise stripe.StripeError("simulated stripe outage")

    with pytest.raises(stripe.StripeError, match="simulated stripe outage"):
        await _stripe_call(boom)


# ── create_checkout_session through the executor path ─────────────────
def _make_invoice_and_customer():
    invoice = SimpleNamespace(
        id=uuid.uuid4(),
        mission_id=uuid.uuid4(),
        line_items=[],
        tax_amount=0,
        tax_rate=0,
        total=1000.0,
        deposit_checkout_session_id=None,
        stripe_checkout_session_id=None,
    )
    customer = SimpleNamespace(email="customer@example.com")
    return invoice, customer


async def test_create_checkout_session_still_works_via_executor():
    from app.services.stripe_service import create_checkout_session

    invoice, customer = _make_invoice_and_customer()
    loop_thread_id = threading.get_ident()
    seen = {}

    def fake_session_create(**kwargs):
        seen["thread_id"] = threading.get_ident()
        seen["kwargs"] = kwargs
        return SimpleNamespace(id="cs_test_offloaded_1", url="https://checkout.stripe.com/c/pay/cs_test_offloaded_1")

    with patch(
        "app.services.stripe_service.get_stripe_settings",
        new=AsyncMock(return_value=dict(_FAKE_STRIPE_CFG)),
    ), patch.object(stripe.checkout.Session, "create", staticmethod(fake_session_create)):
        url = await create_checkout_session(
            invoice,
            customer,
            success_url="https://example.com/ok",
            cancel_url="https://example.com/cancel",
            db=object(),  # get_stripe_settings is mocked — never touched
            payment_phase="deposit",
            amount_override=500.0,
        )

    assert url == "https://checkout.stripe.com/c/pay/cs_test_offloaded_1"
    # Session id stamped on the deposit column exactly as before.
    assert invoice.deposit_checkout_session_id == "cs_test_offloaded_1"
    # Call payload unchanged by the offload wrapper.
    assert seen["kwargs"]["mode"] == "payment"
    assert seen["kwargs"]["metadata"]["payment_phase"] == "deposit"
    assert seen["kwargs"]["customer_email"] == "customer@example.com"
    # And it ran off the event-loop thread.
    assert seen["thread_id"] != loop_thread_id


async def test_create_checkout_session_stripe_error_still_raises():
    """The try/except stripe.StripeError in create_checkout_session must
    behave identically with the executor in the middle."""
    from app.services.stripe_service import create_checkout_session

    invoice, customer = _make_invoice_and_customer()

    def fake_session_create(**kwargs):
        raise stripe.StripeError("card network down")

    with patch(
        "app.services.stripe_service.get_stripe_settings",
        new=AsyncMock(return_value=dict(_FAKE_STRIPE_CFG)),
    ), patch.object(stripe.checkout.Session, "create", staticmethod(fake_session_create)):
        with pytest.raises(stripe.StripeError, match="card network down"):
            await create_checkout_session(
                invoice,
                customer,
                success_url="https://example.com/ok",
                cancel_url="https://example.com/cancel",
                db=object(),
                payment_phase="balance",
                amount_override=500.0,
            )

    # Failure path must not stamp a session id.
    assert invoice.stripe_checkout_session_id is None


# ── webhook _resolve_payment_method through the executor path ─────────
async def test_resolve_payment_method_ach_via_executor():
    from app.routers.stripe_webhook import _resolve_payment_method

    loop_thread_id = threading.get_ident()
    seen = {}

    def fake_pi_retrieve(payment_intent_id):
        seen["pi_thread_id"] = threading.get_ident()
        return SimpleNamespace(payment_method="pm_ach_1")

    def fake_pm_retrieve(payment_method_id):
        seen["pm_thread_id"] = threading.get_ident()
        return SimpleNamespace(type="us_bank_account")

    with patch(
        "app.routers.stripe_webhook.get_stripe_settings",
        new=AsyncMock(return_value=dict(_FAKE_STRIPE_CFG)),
    ), patch.object(stripe.PaymentIntent, "retrieve", staticmethod(fake_pi_retrieve)), \
            patch.object(stripe.PaymentMethod, "retrieve", staticmethod(fake_pm_retrieve)):
        method = await _resolve_payment_method("pi_test_123", ["card", "us_bank_account"], db=object())

    assert method == "stripe_ach"
    assert seen["pi_thread_id"] != loop_thread_id
    assert seen["pm_thread_id"] != loop_thread_id


async def test_resolve_payment_method_fallback_on_stripe_failure():
    """Transient Stripe outage → fall back to session-level
    payment_method_types, exactly as before the offload."""
    from app.routers.stripe_webhook import _resolve_payment_method

    def fake_pi_retrieve(payment_intent_id):
        raise stripe.StripeError("api.stripe.com unreachable")

    with patch(
        "app.routers.stripe_webhook.get_stripe_settings",
        new=AsyncMock(return_value=dict(_FAKE_STRIPE_CFG)),
    ), patch.object(stripe.PaymentIntent, "retrieve", staticmethod(fake_pi_retrieve)):
        method = await _resolve_payment_method("pi_test_456", ["us_bank_account"], db=object())

    assert method == "stripe_ach"  # inferred from session-level types


async def test_resolve_payment_method_no_intent_short_circuits():
    """No payment_intent id → default card, zero Stripe calls (and so
    zero executor hops)."""
    from app.routers.stripe_webhook import _resolve_payment_method

    method = await _resolve_payment_method(None, ["card"], db=object())
    assert method == "stripe_card"


# ── client-portal reuse path through the executor path ────────────────
async def test_reuse_existing_checkout_session_via_executor():
    from app.routers.client_portal import _reuse_existing_checkout_session

    loop_thread_id = threading.get_ident()
    seen = {}

    def fake_session_retrieve(session_id):
        seen["thread_id"] = threading.get_ident()
        return SimpleNamespace(
            payment_status="unpaid",
            created=int(time.time()) - 60,  # fresh — inside 30-min window
            url="https://checkout.stripe.com/c/pay/cs_reuse_1",
            amount_total=50000,
        )

    with patch(
        "app.services.stripe_service.get_stripe_settings",
        new=AsyncMock(return_value=dict(_FAKE_STRIPE_CFG)),
    ), patch.object(stripe.checkout.Session, "retrieve", staticmethod(fake_session_retrieve)):
        url = await _reuse_existing_checkout_session(
            session_id="cs_reuse_1",
            db=object(),
            log_prefix="[TEST]",
            expected_amount_cents=50000,
        )

    assert url == "https://checkout.stripe.com/c/pay/cs_reuse_1"
    assert seen["thread_id"] != loop_thread_id


async def test_reuse_existing_checkout_session_failure_returns_none():
    """Network failure on the retrieve → None (caller mints a fresh
    session) — semantics preserved through the executor."""
    from app.routers.client_portal import _reuse_existing_checkout_session

    def fake_session_retrieve(session_id):
        raise stripe.StripeError("timeout talking to api.stripe.com")

    with patch(
        "app.services.stripe_service.get_stripe_settings",
        new=AsyncMock(return_value=dict(_FAKE_STRIPE_CFG)),
    ), patch.object(stripe.checkout.Session, "retrieve", staticmethod(fake_session_retrieve)):
        url = await _reuse_existing_checkout_session(
            session_id="cs_gone_1",
            db=object(),
            log_prefix="[TEST]",
        )

    assert url is None
