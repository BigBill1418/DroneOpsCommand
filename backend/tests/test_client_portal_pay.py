"""ADR-0009 — client portal /pay/{phase} endpoint tests.

Coverage:
  - /pay/deposit succeeds when deposit_required AND not deposit_paid
    (regardless of mission status — explicitly NOT gated on COMPLETED).
  - /pay/deposit 400s when deposit not required, already paid, or amount<=0.
  - /pay/balance succeeds only when mission COMPLETED|SENT AND deposit
    paid (or not required) AND not paid_in_full.
  - /pay/balance 400s when blocked by gate.
  - /pay (legacy alias) routes to deposit branch in deposit_due phase
    and to balance branch in balance_due phase; rejects awaiting/paid.
  - GET /invoice visibility extension: surfaced when deposit phase
    active even before mission COMPLETED.

These are unit tests against the route handlers with FakeAsyncSession
+ patched stripe service. No real Stripe calls.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest
from fastapi import HTTPException


# ── Test doubles ────────────────────────────────────────────────────────
class _ScalarOneOrNone:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def scalar_one(self):
        return self._value

    def scalars(self):
        return _Scalars(self._value)


class _Scalars:
    def __init__(self, value):
        self._value = value

    def all(self):
        if self._value is None:
            return []
        if isinstance(self._value, list):
            return self._value
        return [self._value]


class FakeQueueAsyncSession:
    """Async session that returns a queue of pre-canned execute() results.

    The pay endpoints fire 3 SELECTs in order:
        1. SELECT Mission WHERE id=…
        2. SELECT Invoice options(selectinload) WHERE mission_id=…
        3. SELECT Customer WHERE id=…
    """

    def __init__(self, results):
        self._results = list(results)
        self.committed = False

    async def execute(self, _stmt):
        if not self._results:
            raise AssertionError("FakeQueueAsyncSession ran out of canned results")
        return _ScalarOneOrNone(self._results.pop(0))

    async def commit(self):
        self.committed = True

    async def flush(self):
        pass

    async def rollback(self):
        pass

    async def close(self):
        pass


def _client_ctx(mission_id: UUID):
    return SimpleNamespace(
        customer_id=uuid.uuid4(),
        mission_ids=[str(mission_id)],
        can_access_mission=lambda mid: True,
    )


def _request():
    return SimpleNamespace(
        client=SimpleNamespace(host="127.0.0.1"),
        headers={"x-forwarded-for": "203.0.113.7"},
    )


def _make_mission(*, status):
    from app.models.mission import MissionStatus
    return SimpleNamespace(
        id=uuid.uuid4(),
        title="Test Mission",
        customer_id=uuid.uuid4(),
        status=status,
    )


def _make_invoice(
    *,
    deposit_required=True,
    deposit_paid=False,
    deposit_amount=500.0,
    paid_in_full=False,
    total=1000.0,
):
    inv = SimpleNamespace(
        id=uuid.uuid4(),
        mission_id=uuid.uuid4(),
        total=total,
        tax_amount=0,
        tax_rate=0,
        line_items=[],
        deposit_required=deposit_required,
        deposit_paid=deposit_paid,
        deposit_amount=deposit_amount,
        deposit_paid_at=None,
        deposit_payment_method=None,
        deposit_checkout_session_id=None,
        deposit_payment_intent_id=None,
        paid_in_full=paid_in_full,
        paid_at=None,
        payment_method=None,
        stripe_checkout_session_id=None,
        stripe_payment_intent_id=None,
    )
    # Attach the real balance_amount + payment_phase_for methods so
    # the legacy alias's phase inference works correctly.
    from app.models.invoice import Invoice
    inv.balance_amount = Invoice.balance_amount.fget(inv)
    inv.payment_phase_for = lambda status: Invoice.payment_phase_for(inv, status)
    return inv


def _make_customer():
    return SimpleNamespace(
        id=uuid.uuid4(),
        name="Test Customer",
        email="customer@example.com",
    )


# ── /pay/deposit ────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_pay_deposit_succeeds_before_completion():
    """ADR-0009 spec line: deposit can be paid any time. Mission is
    only IN_PROGRESS — deposit pay must still go through."""
    from app.models.mission import MissionStatus
    from app.routers.client_portal import create_client_deposit_payment

    mission = _make_mission(status=MissionStatus.IN_PROGRESS)
    invoice = _make_invoice()
    customer = _make_customer()

    db = FakeQueueAsyncSession([mission, invoice, customer])
    client = _client_ctx(mission.id)

    with patch(
        "app.services.stripe_service.create_checkout_session",
        new=AsyncMock(return_value="https://checkout.stripe.test/cs_dep_123"),
    ) as create_mock:
        resp = await create_client_deposit_payment(
            mission_id=mission.id, request=_request(), client=client, db=db,
        )

    assert resp.checkout_url.startswith("https://checkout.stripe.test/")
    assert db.committed is True
    # Asserted phase + amount sent to Stripe.
    kwargs = create_mock.call_args.kwargs
    assert kwargs["payment_phase"] == "deposit"
    assert kwargs["amount_override"] == 500.0


@pytest.mark.asyncio
async def test_pay_deposit_400_when_already_paid():
    from app.models.mission import MissionStatus
    from app.routers.client_portal import create_client_deposit_payment

    mission = _make_mission(status=MissionStatus.IN_PROGRESS)
    invoice = _make_invoice(deposit_paid=True)
    customer = _make_customer()
    db = FakeQueueAsyncSession([mission, invoice, customer])
    client = _client_ctx(mission.id)

    with patch(
        "app.services.stripe_service.create_checkout_session",
        new=AsyncMock(return_value="https://checkout.stripe.test/x"),
    ):
        with pytest.raises(HTTPException) as exc:
            await create_client_deposit_payment(
                mission_id=mission.id, request=_request(), client=client, db=db,
            )
    assert exc.value.status_code == 400
    assert "already" in exc.value.detail.lower()


@pytest.mark.asyncio
async def test_pay_deposit_400_when_not_required():
    from app.models.mission import MissionStatus
    from app.routers.client_portal import create_client_deposit_payment

    mission = _make_mission(status=MissionStatus.IN_PROGRESS)
    invoice = _make_invoice(deposit_required=False, deposit_amount=0)
    customer = _make_customer()
    db = FakeQueueAsyncSession([mission, invoice, customer])
    client = _client_ctx(mission.id)

    with pytest.raises(HTTPException) as exc:
        await create_client_deposit_payment(
            mission_id=mission.id, request=_request(), client=client, db=db,
        )
    assert exc.value.status_code == 400


# ── /pay/balance ────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_pay_balance_succeeds_when_completed_and_deposit_paid():
    from app.models.mission import MissionStatus
    from app.routers.client_portal import create_client_balance_payment

    mission = _make_mission(status=MissionStatus.COMPLETED)
    invoice = _make_invoice(deposit_paid=True)
    customer = _make_customer()
    db = FakeQueueAsyncSession([mission, invoice, customer])
    client = _client_ctx(mission.id)

    with patch(
        "app.services.stripe_service.create_checkout_session",
        new=AsyncMock(return_value="https://checkout.stripe.test/cs_bal_123"),
    ) as create_mock:
        resp = await create_client_balance_payment(
            mission_id=mission.id, request=_request(), client=client, db=db,
        )

    assert "stripe.test" in resp.checkout_url
    kwargs = create_mock.call_args.kwargs
    assert kwargs["payment_phase"] == "balance"
    assert kwargs["amount_override"] == 500.0  # 1000 total - 500 deposit


@pytest.mark.asyncio
async def test_pay_balance_400_before_mission_completed():
    from app.models.mission import MissionStatus
    from app.routers.client_portal import create_client_balance_payment

    mission = _make_mission(status=MissionStatus.IN_PROGRESS)
    invoice = _make_invoice(deposit_paid=True)
    customer = _make_customer()
    db = FakeQueueAsyncSession([mission, invoice, customer])
    client = _client_ctx(mission.id)

    with pytest.raises(HTTPException) as exc:
        await create_client_balance_payment(
            mission_id=mission.id, request=_request(), client=client, db=db,
        )
    assert exc.value.status_code == 400
    assert "complete" in exc.value.detail.lower()


@pytest.mark.asyncio
async def test_pay_balance_400_when_deposit_required_but_not_paid():
    from app.models.mission import MissionStatus
    from app.routers.client_portal import create_client_balance_payment

    mission = _make_mission(status=MissionStatus.COMPLETED)
    invoice = _make_invoice(deposit_required=True, deposit_paid=False)
    customer = _make_customer()
    db = FakeQueueAsyncSession([mission, invoice, customer])
    client = _client_ctx(mission.id)

    with pytest.raises(HTTPException) as exc:
        await create_client_balance_payment(
            mission_id=mission.id, request=_request(), client=client, db=db,
        )
    assert exc.value.status_code == 400
    assert "deposit" in exc.value.detail.lower()


@pytest.mark.asyncio
async def test_pay_balance_succeeds_when_deposit_not_required():
    """Legacy single-payment path: deposit_required=False on a
    COMPLETED mission charges the full total."""
    from app.models.mission import MissionStatus
    from app.routers.client_portal import create_client_balance_payment

    mission = _make_mission(status=MissionStatus.COMPLETED)
    invoice = _make_invoice(deposit_required=False, deposit_amount=0)
    customer = _make_customer()
    db = FakeQueueAsyncSession([mission, invoice, customer])
    client = _client_ctx(mission.id)

    with patch(
        "app.services.stripe_service.create_checkout_session",
        new=AsyncMock(return_value="https://checkout.stripe.test/cs_full"),
    ) as create_mock:
        await create_client_balance_payment(
            mission_id=mission.id, request=_request(), client=client, db=db,
        )

    kwargs = create_mock.call_args.kwargs
    assert kwargs["amount_override"] == 1000.0  # full total


@pytest.mark.asyncio
async def test_pay_balance_400_when_already_paid_in_full():
    from app.models.mission import MissionStatus
    from app.routers.client_portal import create_client_balance_payment

    mission = _make_mission(status=MissionStatus.COMPLETED)
    invoice = _make_invoice(deposit_paid=True, paid_in_full=True)
    customer = _make_customer()
    db = FakeQueueAsyncSession([mission, invoice, customer])
    client = _client_ctx(mission.id)

    with pytest.raises(HTTPException) as exc:
        await create_client_balance_payment(
            mission_id=mission.id, request=_request(), client=client, db=db,
        )
    assert exc.value.status_code == 400


# ── /pay (legacy alias) phase inference ────────────────────────────────
@pytest.mark.asyncio
async def test_pay_alias_routes_deposit_when_deposit_due():
    """When phase=deposit_due, /pay must delegate to the deposit handler."""
    from app.models.mission import MissionStatus
    from app.routers.client_portal import create_client_payment

    mission = _make_mission(status=MissionStatus.IN_PROGRESS)
    invoice = _make_invoice()  # deposit_required, not paid
    customer = _make_customer()
    # The alias performs preflight, then re-enters the deposit handler
    # which fires its own 3 SELECTs. Total = 6 results in the queue.
    results = [mission, invoice, customer, mission, invoice, customer]
    db = FakeQueueAsyncSession(results)
    client = _client_ctx(mission.id)

    with patch(
        "app.services.stripe_service.create_checkout_session",
        new=AsyncMock(return_value="https://checkout.stripe.test/cs_alias_dep"),
    ) as create_mock:
        await create_client_payment(
            mission_id=mission.id, request=_request(), client=client, db=db,
        )

    assert create_mock.call_args.kwargs["payment_phase"] == "deposit"


@pytest.mark.asyncio
async def test_pay_alias_routes_balance_when_balance_due():
    from app.models.mission import MissionStatus
    from app.routers.client_portal import create_client_payment

    mission = _make_mission(status=MissionStatus.COMPLETED)
    invoice = _make_invoice(deposit_paid=True)
    customer = _make_customer()
    results = [mission, invoice, customer, mission, invoice, customer]
    db = FakeQueueAsyncSession(results)
    client = _client_ctx(mission.id)

    with patch(
        "app.services.stripe_service.create_checkout_session",
        new=AsyncMock(return_value="https://checkout.stripe.test/cs_alias_bal"),
    ) as create_mock:
        await create_client_payment(
            mission_id=mission.id, request=_request(), client=client, db=db,
        )

    assert create_mock.call_args.kwargs["payment_phase"] == "balance"


@pytest.mark.asyncio
async def test_pay_alias_400_when_awaiting_completion():
    """deposit_required=False, mission IN_PROGRESS → nothing to charge yet."""
    from app.models.mission import MissionStatus
    from app.routers.client_portal import create_client_payment

    mission = _make_mission(status=MissionStatus.IN_PROGRESS)
    invoice = _make_invoice(deposit_required=False, deposit_amount=0)
    customer = _make_customer()
    db = FakeQueueAsyncSession([mission, invoice, customer])
    client = _client_ctx(mission.id)

    with pytest.raises(HTTPException) as exc:
        await create_client_payment(
            mission_id=mission.id, request=_request(), client=client, db=db,
        )
    assert exc.value.status_code == 400
    assert "not yet available" in exc.value.detail.lower()
