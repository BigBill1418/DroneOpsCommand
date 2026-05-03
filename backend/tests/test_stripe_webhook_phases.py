"""ADR-0009 — webhook handler phase routing + idempotency.

Each branch (deposit | balance | absent/legacy) lands in the right
columns and fires the right notification helper. Idempotency: a
duplicate delivery returns early without re-firing notifications.

All Stripe + email + ntfy calls are mocked. No real network.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


# ── Test doubles (shared with test_client_portal_pay would be ideal,
#     but pytest collects from each file independently and there's no
#     single canonical fixtures module yet). ─────────────────────────
class _ScalarOneOrNone:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class FakeAsyncSession:
    def __init__(self, row):
        self._row = row
        self.committed = False

    async def execute(self, _stmt):
        return _ScalarOneOrNone(self._row)

    async def commit(self):
        self.committed = True

    async def flush(self):
        pass

    async def rollback(self):
        pass


def _make_invoice(
    *,
    deposit_required=True,
    deposit_paid=False,
    paid_in_full=False,
    deposit_amount=500.0,
    total=1000.0,
    deposit_session_id="cs_deposit_test_123",
    balance_session_id="cs_balance_test_456",
):
    inv = SimpleNamespace(
        id=uuid.uuid4(),
        mission_id=uuid.uuid4(),
        total=total,
        deposit_required=deposit_required,
        deposit_paid=deposit_paid,
        deposit_amount=deposit_amount,
        deposit_paid_at=None,
        deposit_payment_method=None,
        deposit_payment_intent_id=None,
        deposit_checkout_session_id=deposit_session_id,
        paid_in_full=paid_in_full,
        paid_at=None,
        payment_method=None,
        stripe_payment_intent_id=None,
        stripe_checkout_session_id=balance_session_id,
        line_items=[],
    )
    from app.models.invoice import Invoice
    inv.balance_amount = Invoice.balance_amount.fget(inv)
    return inv


# ── Deposit branch ─────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_webhook_deposit_branch_marks_deposit_paid():
    from app.routers.stripe_webhook import _handle_checkout_completed

    invoice = _make_invoice()
    db = FakeAsyncSession(invoice)

    session_event = {
        "id": invoice.deposit_checkout_session_id,
        "payment_intent": "pi_dep_123",
        "payment_method_types": ["card"],
        "metadata": {
            "invoice_id": str(invoice.id),
            "mission_id": str(invoice.mission_id),
            "payment_phase": "deposit",
        },
    }

    with patch(
        "app.routers.stripe_webhook._resolve_payment_method",
        new=AsyncMock(return_value="stripe_card"),
    ), patch(
        "app.routers.stripe_webhook._send_deposit_notifications",
        new=AsyncMock(return_value=None),
    ) as send_mock:
        await _handle_checkout_completed(session_event, db)

    assert invoice.deposit_paid is True
    assert invoice.deposit_payment_method == "stripe_card"
    assert invoice.deposit_payment_intent_id == "pi_dep_123"
    assert invoice.paid_in_full is False  # balance still owed
    assert db.committed is True
    send_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_webhook_deposit_idempotent_when_already_paid():
    """Duplicate delivery: deposit_paid already True → return early,
    notifications NOT fired again."""
    from app.routers.stripe_webhook import _handle_checkout_completed

    invoice = _make_invoice(deposit_paid=True)
    db = FakeAsyncSession(invoice)

    session_event = {
        "id": invoice.deposit_checkout_session_id,
        "payment_intent": "pi_dep_123",
        "payment_method_types": ["card"],
        "metadata": {"payment_phase": "deposit"},
    }

    with patch(
        "app.routers.stripe_webhook._resolve_payment_method",
        new=AsyncMock(return_value="stripe_card"),
    ), patch(
        "app.routers.stripe_webhook._send_deposit_notifications",
        new=AsyncMock(return_value=None),
    ) as send_mock:
        await _handle_checkout_completed(session_event, db)

    send_mock.assert_not_called()
    assert db.committed is False  # nothing to write


# ── Balance branch ────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_webhook_balance_branch_marks_paid_in_full():
    from app.routers.stripe_webhook import _handle_checkout_completed

    invoice = _make_invoice(deposit_paid=True)
    db = FakeAsyncSession(invoice)

    session_event = {
        "id": invoice.stripe_checkout_session_id,
        "payment_intent": "pi_bal_456",
        "payment_method_types": ["card"],
        "metadata": {"payment_phase": "balance"},
    }

    with patch(
        "app.routers.stripe_webhook._resolve_payment_method",
        new=AsyncMock(return_value="stripe_card"),
    ), patch(
        "app.routers.stripe_webhook._send_balance_notifications",
        new=AsyncMock(return_value=None),
    ) as send_mock:
        await _handle_checkout_completed(session_event, db)

    assert invoice.paid_in_full is True
    assert invoice.payment_method == "stripe_card"
    assert invoice.stripe_payment_intent_id == "pi_bal_456"
    assert db.committed is True
    send_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_webhook_balance_idempotent_when_already_paid():
    from app.routers.stripe_webhook import _handle_checkout_completed

    invoice = _make_invoice(deposit_paid=True, paid_in_full=True)
    db = FakeAsyncSession(invoice)
    session_event = {
        "id": invoice.stripe_checkout_session_id,
        "payment_intent": "pi_bal_456",
        "payment_method_types": ["card"],
        "metadata": {"payment_phase": "balance"},
    }
    with patch(
        "app.routers.stripe_webhook._resolve_payment_method",
        new=AsyncMock(return_value="stripe_card"),
    ), patch(
        "app.routers.stripe_webhook._send_balance_notifications",
        new=AsyncMock(return_value=None),
    ) as send_mock:
        await _handle_checkout_completed(session_event, db)
    send_mock.assert_not_called()
    assert db.committed is False


# ── Legacy / no metadata path ─────────────────────────────────────────
@pytest.mark.asyncio
async def test_webhook_no_metadata_falls_through_to_balance():
    """Pre-v2.65.0 sessions that completed mid-cutover. The handler
    must treat them exactly as the legacy single-payment path did."""
    from app.routers.stripe_webhook import _handle_checkout_completed

    invoice = _make_invoice(deposit_required=False, deposit_amount=0)
    db = FakeAsyncSession(invoice)

    session_event = {
        "id": invoice.stripe_checkout_session_id,
        "payment_intent": "pi_legacy_789",
        "payment_method_types": ["card"],
        # No "metadata" key at all — covers the pre-feature shape.
    }

    with patch(
        "app.routers.stripe_webhook._resolve_payment_method",
        new=AsyncMock(return_value="stripe_card"),
    ), patch(
        "app.routers.stripe_webhook._send_balance_notifications",
        new=AsyncMock(return_value=None),
    ) as send_mock:
        await _handle_checkout_completed(session_event, db)

    assert invoice.paid_in_full is True
    send_mock.assert_awaited_once()


# ── Lookup mismatch / no invoice ──────────────────────────────────────
@pytest.mark.asyncio
async def test_webhook_deposit_no_invoice_logs_and_returns():
    """Webhook arrives but no invoice has the matching deposit session id
    (e.g., test event from Stripe dashboard). Must NOT raise."""
    from app.routers.stripe_webhook import _handle_checkout_completed

    db = FakeAsyncSession(row=None)
    session_event = {
        "id": "cs_orphan_test",
        "payment_intent": None,
        "payment_method_types": [],
        "metadata": {"payment_phase": "deposit"},
    }
    # No notification helpers patched → if they fire, the import side
    # effect would be visible. We assert by getting a clean return.
    await _handle_checkout_completed(session_event, db)
    assert db.committed is False


@pytest.mark.asyncio
async def test_webhook_balance_no_invoice_logs_and_returns():
    from app.routers.stripe_webhook import _handle_checkout_completed

    db = FakeAsyncSession(row=None)
    session_event = {
        "id": "cs_orphan_balance",
        "payment_intent": None,
        "payment_method_types": [],
        "metadata": {"payment_phase": "balance"},
    }
    await _handle_checkout_completed(session_event, db)
    assert db.committed is False
