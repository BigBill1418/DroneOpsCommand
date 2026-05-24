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


class _FakeSender:
    def __init__(self):
        self.reminders = []
        self.finals = []
        self.operator = []

    def send_reminder(self, invoice, amount):
        self.reminders.append((invoice, amount))

    def send_final(self, invoice, amount):
        self.finals.append((invoice, amount))

    def send_operator(self, invoice, amount):
        self.operator.append((invoice, amount))


def _payinv(**kw):
    base = dict(billed_at=datetime(2026, 1, 1), reminder_sent_at=None,
                final_notice_sent_at=None, paid_in_full=False,
                deposit_required=False, deposit_paid=False,
                deposit_amount=0.0, balance_amount=100.0)
    base.update(kw)
    return SimpleNamespace(**base)


def test_amount_due_no_deposit_uses_balance():
    assert dunning.amount_due(_payinv(balance_amount=844.30)) == 844.30


def test_amount_due_deposit_unpaid_uses_deposit():
    inv = _payinv(deposit_required=True, deposit_paid=False, deposit_amount=400.0, balance_amount=400.0)
    assert dunning.amount_due(inv) == 400.0


def test_process_reminder_sends_and_stamps():
    inv = _payinv()
    s = _FakeSender()
    sent = dunning.process_invoice(inv, inv.billed_at + timedelta(hours=49), s, now_fn=lambda: datetime(2026, 1, 3))
    assert sent == dunning.STAGE_REMINDER
    assert len(s.reminders) == 1 and s.finals == [] and s.operator == []
    assert inv.reminder_sent_at == datetime(2026, 1, 3)


def test_process_final_sends_customer_and_operator_and_stamps():
    inv = _payinv()
    s = _FakeSender()
    sent = dunning.process_invoice(inv, inv.billed_at + timedelta(days=8), s, now_fn=lambda: datetime(2026, 1, 9))
    assert sent == dunning.STAGE_FINAL
    assert len(s.finals) == 1 and len(s.operator) == 1
    assert inv.final_notice_sent_at == datetime(2026, 1, 9)


def test_process_nothing_due_returns_none():
    inv = _payinv()
    s = _FakeSender()
    assert dunning.process_invoice(inv, inv.billed_at + timedelta(hours=1), s) is None
    assert s.reminders == [] and s.finals == []
