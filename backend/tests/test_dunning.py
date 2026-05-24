from app.models.invoice import Invoice


def test_invoice_has_dunning_columns():
    inv = Invoice(mission_id=None)  # type: ignore[arg-type]
    # all three start unset and are assignable
    assert inv.billed_at is None
    assert inv.reminder_sent_at is None
    assert inv.final_notice_sent_at is None
