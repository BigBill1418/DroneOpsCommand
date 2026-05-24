from datetime import datetime, timedelta
from types import SimpleNamespace

from app.models.invoice import Invoice
from app.services import dunning


def test_invoice_has_dunning_columns():
    inv = Invoice(mission_id=None)  # type: ignore[arg-type]
    # all three start unset and are assignable
    assert inv.billed_at is None
    assert inv.reminder_sent_at is None
    assert inv.final_notice_sent_at is None


def _inv(**kw):
    base = dict(billed_at=datetime(2026, 1, 1, 0, 0, 0), reminder_sent_at=None,
               final_notice_sent_at=None, paid_in_full=False)
    base.update(kw)
    return SimpleNamespace(**base)


def test_due_stage_none_before_48h():
    inv = _inv()
    assert dunning.due_stage(inv, inv.billed_at + timedelta(hours=47)) is None


def test_due_stage_reminder_in_window():
    inv = _inv()
    assert dunning.due_stage(inv, inv.billed_at + timedelta(hours=48)) == dunning.STAGE_REMINDER


def test_due_stage_none_when_reminder_already_sent():
    inv = _inv(reminder_sent_at=datetime(2026, 1, 3))
    assert dunning.due_stage(inv, inv.billed_at + timedelta(hours=72)) is None


def test_due_stage_final_at_7d():
    inv = _inv()
    assert dunning.due_stage(inv, inv.billed_at + timedelta(days=7)) == dunning.STAGE_FINAL


def test_due_stage_final_even_if_no_prior_reminder():
    inv = _inv()  # reminder never sent, now past 7d -> skip straight to final
    assert dunning.due_stage(inv, inv.billed_at + timedelta(days=8)) == dunning.STAGE_FINAL


def test_due_stage_none_when_final_already_sent():
    inv = _inv(final_notice_sent_at=datetime(2026, 1, 9))
    assert dunning.due_stage(inv, inv.billed_at + timedelta(days=10)) is None


def test_due_stage_none_when_paid():
    inv = _inv(paid_in_full=True)
    assert dunning.due_stage(inv, inv.billed_at + timedelta(days=8)) is None


def test_due_stage_none_when_never_billed():
    inv = _inv(billed_at=None)
    assert dunning.due_stage(inv, datetime(2026, 2, 1)) is None
