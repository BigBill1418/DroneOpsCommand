"""v2.66.0 Fix 8 — webhook deposit branch falls back to
`stripe_checkout_session_id` when no invoice has the matching
`deposit_checkout_session_id` (pre-v2.65.0 invoices).

Without this fallback, paid customer events for legacy invoices were
silently dropped on the deposit branch.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


class _ScalarOneOrNone:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class FakeQueueAsyncSession:
    """Returns canned execute() results in the order configured."""

    def __init__(self, results):
        self._results = list(results)
        self.committed = False

    async def execute(self, _stmt):
        if not self._results:
            raise AssertionError("queue exhausted")
        return _ScalarOneOrNone(self._results.pop(0))

    async def commit(self):
        self.committed = True

    async def flush(self):
        pass


def _mk_invoice(*, deposit_session=None, balance_session="cs_legacy_456"):
    inv = SimpleNamespace(
        id=uuid.uuid4(),
        mission_id=uuid.uuid4(),
        total=1000.0,
        deposit_required=True,
        deposit_paid=False,
        deposit_amount=500.0,
        deposit_paid_at=None,
        deposit_payment_method=None,
        deposit_payment_intent_id=None,
        deposit_checkout_session_id=deposit_session,
        paid_in_full=False,
        paid_at=None,
        payment_method=None,
        stripe_payment_intent_id=None,
        stripe_checkout_session_id=balance_session,
        line_items=[],
    )
    inv.balance_amount = 500.0
    return inv


@pytest.mark.asyncio
async def test_deposit_branch_falls_back_to_legacy_session_column():
    """Pre-v2.65.0 invoice: deposit_checkout_session_id is NULL but the
    customer paid via a session that landed on stripe_checkout_session_id.
    Webhook MUST find the invoice via the legacy fallback."""
    from app.routers.stripe_webhook import _handle_deposit_completed

    inv = _mk_invoice(deposit_session=None, balance_session="cs_legacy_999")

    # First execute → no row (deposit_checkout_session_id miss).
    # Second execute → invoice (legacy column hit).
    db = FakeQueueAsyncSession(results=[None, inv])

    with patch(
        "app.routers.stripe_webhook._resolve_payment_method",
        new=AsyncMock(return_value="stripe_card"),
    ), patch(
        "app.routers.stripe_webhook._send_deposit_notifications",
        new=AsyncMock(return_value=None),
    ) as send_mock:
        await _handle_deposit_completed(
            session_id="cs_legacy_999",
            payment_intent_id="pi_legacy_111",
            payment_method_types=["card"],
            db=db,
        )

    assert inv.deposit_paid is True
    assert inv.deposit_payment_intent_id == "pi_legacy_111"
    assert db.committed is True
    send_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_deposit_branch_no_invoice_anywhere_logs_and_returns():
    """Both lookups miss → no commit, no notifications, no raise."""
    from app.routers.stripe_webhook import _handle_deposit_completed

    db = FakeQueueAsyncSession(results=[None, None])

    with patch(
        "app.routers.stripe_webhook._send_deposit_notifications",
        new=AsyncMock(return_value=None),
    ) as send_mock:
        await _handle_deposit_completed(
            session_id="cs_orphan",
            payment_intent_id=None,
            payment_method_types=[],
            db=db,
        )

    send_mock.assert_not_called()
    assert db.committed is False


@pytest.mark.asyncio
async def test_deposit_branch_primary_match_does_not_fall_back():
    """Normal path: deposit_checkout_session_id matches on first try.
    The fallback SELECT must not run (queue would only have 1 result)."""
    from app.routers.stripe_webhook import _handle_deposit_completed

    inv = _mk_invoice(deposit_session="cs_dep_primary")
    db = FakeQueueAsyncSession(results=[inv])

    with patch(
        "app.routers.stripe_webhook._resolve_payment_method",
        new=AsyncMock(return_value="stripe_card"),
    ), patch(
        "app.routers.stripe_webhook._send_deposit_notifications",
        new=AsyncMock(return_value=None),
    ):
        await _handle_deposit_completed(
            session_id="cs_dep_primary",
            payment_intent_id="pi_dep_222",
            payment_method_types=["card"],
            db=db,
        )

    assert inv.deposit_paid is True
    # No "queue exhausted" — the fallback SELECT did not fire.
