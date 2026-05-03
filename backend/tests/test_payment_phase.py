"""ADR-0009 — Tests for the `payment_phase` truth table (spec §3.2).

`compute_payment_phase` is a pure function and the single source of
truth; the SQLAlchemy Invoice.payment_phase_for shim wraps it. We
exercise BOTH so a future refactor that drops the shim still has
coverage.

These tests deliberately do NOT spin up a DB — they construct
SimpleNamespace stand-ins for the model. Same hermetic pattern the
existing rotation tests use (see backend/tests/conftest.py).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.models.invoice import (
    PAYMENT_PHASE_AWAITING_COMPLETION,
    PAYMENT_PHASE_BALANCE_DUE,
    PAYMENT_PHASE_DEPOSIT_DUE,
    PAYMENT_PHASE_PAID_IN_FULL,
    Invoice,
    compute_payment_phase,
)
from app.models.mission import MissionStatus

# Spec §3.2 truth table. Each row: (deposit_required, deposit_paid,
# mission_completed_or_sent, paid_in_full, expected_phase).
TRUTH_TABLE = [
    # paid_in_full short-circuits everything else.
    (True,  True,  True,  True,  PAYMENT_PHASE_PAID_IN_FULL),
    (False, False, False, True,  PAYMENT_PHASE_PAID_IN_FULL),
    (True,  False, False, True,  PAYMENT_PHASE_PAID_IN_FULL),

    # Deposit required, not yet paid → deposit_due regardless of mission status.
    (True,  False, False, False, PAYMENT_PHASE_DEPOSIT_DUE),
    (True,  False, True,  False, PAYMENT_PHASE_DEPOSIT_DUE),

    # Deposit required + paid → depends on mission status.
    (True,  True,  False, False, PAYMENT_PHASE_AWAITING_COMPLETION),
    (True,  True,  True,  False, PAYMENT_PHASE_BALANCE_DUE),

    # Deposit not required → mission status drives phase.
    (False, False, False, False, PAYMENT_PHASE_AWAITING_COMPLETION),
    (False, False, True,  False, PAYMENT_PHASE_BALANCE_DUE),
]


@pytest.mark.parametrize(
    "deposit_required,deposit_paid,mission_completed_or_sent,paid_in_full,expected",
    TRUTH_TABLE,
)
def test_compute_payment_phase_truth_table(
    deposit_required, deposit_paid, mission_completed_or_sent, paid_in_full, expected,
):
    assert compute_payment_phase(
        deposit_required=deposit_required,
        deposit_paid=deposit_paid,
        mission_completed_or_sent=mission_completed_or_sent,
        paid_in_full=paid_in_full,
    ) == expected


@pytest.mark.parametrize(
    "status,is_completed",
    [
        (MissionStatus.DRAFT, False),
        (MissionStatus.SCHEDULED, False),
        (MissionStatus.IN_PROGRESS, False),
        (MissionStatus.PROCESSING, False),
        (MissionStatus.REVIEW, False),
        (MissionStatus.DELIVERED, False),  # Not in INVOICE_VISIBLE_STATUSES per ADR-0008.
        (MissionStatus.COMPLETED, True),
        (MissionStatus.SENT, True),
    ],
)
def test_invoice_payment_phase_for_handles_all_mission_statuses(status, is_completed):
    """The Invoice.payment_phase_for shim must consider only COMPLETED+SENT
    as "delivered". DELIVERED is intentionally NOT a billing trigger
    (per ADR-0008's INVOICE_VISIBLE_STATUSES)."""
    inv = Invoice(
        mission_id=None,  # type: ignore[arg-type]  - bypassing FK for unit test
    )
    inv.deposit_required = True
    inv.deposit_paid = True
    inv.paid_in_full = False
    phase = inv.payment_phase_for(status)
    if is_completed:
        assert phase == PAYMENT_PHASE_BALANCE_DUE, status
    else:
        assert phase == PAYMENT_PHASE_AWAITING_COMPLETION, status


def test_payment_phase_for_legacy_invoice_no_deposit():
    """A pre-v2.65.0 invoice (deposit_required=False) on a COMPLETED
    mission must phase straight to balance_due so the existing
    /pay alias keeps working."""
    inv = Invoice(mission_id=None)  # type: ignore[arg-type]
    inv.deposit_required = False
    inv.deposit_paid = False
    inv.paid_in_full = False
    assert inv.payment_phase_for(MissionStatus.COMPLETED) == PAYMENT_PHASE_BALANCE_DUE
    assert inv.payment_phase_for(MissionStatus.IN_PROGRESS) == PAYMENT_PHASE_AWAITING_COMPLETION
