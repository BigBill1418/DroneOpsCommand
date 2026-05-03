"""ADR-0009 — deposit pricing helper tests.

Verifies:
  - default 50% rounding (TOS §6.2)
  - explicit override accepted within bounds
  - sum-invariant: deposit + balance == total
  - validation: negative or over-total amounts raise HTTP 400
  - deposit_required=False forces deposit_amount=0

Pure-function coverage; no DB, no Stripe.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.routers.invoices import _resolve_deposit_amount
from app.models.invoice import Invoice


@pytest.mark.parametrize(
    "total,expected",
    [
        (0.0, 0.0),
        (1.0, 0.50),
        (100.0, 50.00),
        (1000.0, 500.00),
        (1234.56, 617.28),  # round half-up to 2dp
        (0.01, 0.01),       # floor case for tiny totals
    ],
)
def test_default_50pct_rounding(total, expected):
    assert _resolve_deposit_amount(
        deposit_required=True,
        deposit_amount=None,
        total=total,
    ) == expected


def test_deposit_not_required_forces_zero():
    # Even if the operator passes a value, deposit_required=False
    # MUST clamp to 0.
    assert _resolve_deposit_amount(
        deposit_required=False, deposit_amount=999.99, total=2000.0,
    ) == 0.0
    assert _resolve_deposit_amount(
        deposit_required=False, deposit_amount=None, total=2000.0,
    ) == 0.0


def test_explicit_override_within_bounds():
    # Operator may pick any value 0..total inclusive.
    assert _resolve_deposit_amount(
        deposit_required=True, deposit_amount=300, total=1000,
    ) == 300.00
    assert _resolve_deposit_amount(
        deposit_required=True, deposit_amount=1000, total=1000,
    ) == 1000.00


def test_explicit_zero_when_required_is_allowed_but_caught_by_db_check():
    """0 explicitly is structurally allowed by the helper but rejected
    by the CHECK constraint deposit_required_consistent at flush time.
    The helper itself returns 0; it's the DB layer's job to enforce
    the consistency rule. This test pins that contract so a future
    refactor doesn't accidentally start raising here too."""
    assert _resolve_deposit_amount(
        deposit_required=True, deposit_amount=0, total=100,
    ) == 0.0


def test_negative_amount_rejected():
    with pytest.raises(HTTPException) as exc:
        _resolve_deposit_amount(
            deposit_required=True, deposit_amount=-1, total=100,
        )
    assert exc.value.status_code == 400


def test_over_total_amount_rejected():
    with pytest.raises(HTTPException) as exc:
        _resolve_deposit_amount(
            deposit_required=True, deposit_amount=200, total=100,
        )
    assert exc.value.status_code == 400


@pytest.mark.parametrize("total", [0.0, 100.0, 1234.56, 99999.99])
def test_deposit_plus_balance_equals_total(total):
    """Sum invariant — guards against introducing rounding drift if
    the 50% formula ever changes shape."""
    deposit = _resolve_deposit_amount(
        deposit_required=True, deposit_amount=None, total=total,
    )
    inv = Invoice(mission_id=None)  # type: ignore[arg-type]
    inv.deposit_required = True
    inv.deposit_amount = deposit
    inv.total = total
    # round to 2dp on both sides — both numbers come from
    # round(., 2) already, so addition is exact at 2dp.
    assert round(deposit + inv.balance_amount, 2) == round(total, 2)


def test_balance_amount_when_deposit_not_required():
    """deposit_required=False means balance_amount == total — there is
    no deposit slice carved out."""
    inv = Invoice(mission_id=None)  # type: ignore[arg-type]
    inv.deposit_required = False
    inv.deposit_amount = 0
    inv.total = 500
    assert inv.balance_amount == 500.0


def test_balance_amount_never_negative():
    """Defensive — if a stale row somehow has deposit_amount > total
    after a recalc bug, balance_amount must still be >= 0 (we surface
    it directly to Stripe)."""
    inv = Invoice(mission_id=None)  # type: ignore[arg-type]
    inv.deposit_required = True
    inv.deposit_amount = 600
    inv.total = 500
    assert inv.balance_amount == 0.0
