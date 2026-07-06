"""ADR-0040 — automated download-link delivery unit coverage.

Hermetic tests over `app.services.download_link_delivery`:

- `_delivery_skip_reason` — every skip condition of the payment-triggered
  follow-up email (no URL, already sent, not billable, not paid, expired,
  no customer email) and the all-clear path.
- `download_link_payment_blocked` — the relocated ADR-0039 policy,
  including the report=None portal case (no report row → no override).

The endpoint wiring (Stripe webhook / manual mark-paid / mission-update
re-arm) is thin glue over these; the send itself is exercised against a
stubbed email layer in `test_deliver_download_link_if_due_*`.

To run:

    cd backend
    pytest tests/test_download_link_delivery.py -v
"""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

from app.services.download_link_delivery import (
    _delivery_skip_reason,
    download_link_payment_blocked,
)


def _mission(
    *,
    is_billable: bool = True,
    paid_in_full: bool = True,
    invoice: object = "auto",
    download_link_url: str | None = "https://drop.example.com/abc",
    download_link_expires_at: datetime | None = None,
    download_link_email_sent_at: datetime | None = None,
    customer_email: str | None = "client@example.com",
):
    if invoice == "auto":
        invoice = SimpleNamespace(
            invoice_number="INV-1", paid_in_full=paid_in_full, total=400.50
        )
    customer = (
        SimpleNamespace(name="Client", email=customer_email)
        if customer_email is not None
        else None
    )
    return SimpleNamespace(
        id="11111111-1111-1111-1111-111111111111",
        title="Test Mission",
        is_billable=is_billable,
        invoice=invoice,
        customer=customer,
        download_link_url=download_link_url,
        download_link_expires_at=download_link_expires_at,
        download_link_email_sent_at=download_link_email_sent_at,
    )


# ── _delivery_skip_reason ──────────────────────────────────────────────
# The invoice is passed EXPLICITLY (lazy="noload" identity-map trap — see
# the service docstring); these call it exactly as the service does.

def _skip(m):
    return _delivery_skip_reason(m, m.invoice)


def test_all_conditions_met_sends():
    assert _skip(_mission()) is None


def test_no_url_skips():
    assert _skip(_mission(download_link_url=None)) == "no-download-url"


def test_already_sent_skips():
    m = _mission(download_link_email_sent_at=datetime.utcnow())
    assert _skip(m) == "already-sent"


def test_not_billable_skips():
    assert _skip(_mission(is_billable=False)) == "not-billable"


def test_unpaid_skips():
    assert _skip(_mission(paid_in_full=False)) == "not-paid-in-full"


def test_no_invoice_skips():
    assert _skip(_mission(invoice=None)) == "not-paid-in-full"


def test_expired_link_skips():
    m = _mission(download_link_expires_at=datetime.utcnow() - timedelta(hours=1))
    assert _skip(m) == "link-expired"


def test_future_expiry_sends():
    m = _mission(download_link_expires_at=datetime.utcnow() + timedelta(days=7))
    assert _skip(m) is None


def test_no_customer_email_skips():
    assert _skip(_mission(customer_email=None)) == "no-customer-email"


def test_rearmed_after_url_change_sends():
    # The mission-update path nulls the stamp when the URL changes; a
    # re-armed mission with a paid invoice delivers again.
    m = _mission(download_link_email_sent_at=None)
    assert _skip(m) is None


# ── download_link_payment_blocked (relocated ADR-0039 policy) ──────────

def _report(*, override: bool = False):
    return SimpleNamespace(download_link_payment_override=override)


def test_blocked_unpaid_billable():
    assert download_link_payment_blocked(_mission(paid_in_full=False), _report()) is True


def test_released_when_paid():
    assert download_link_payment_blocked(_mission(paid_in_full=True), _report()) is False


def test_released_by_override():
    assert (
        download_link_payment_blocked(_mission(paid_in_full=False), _report(override=True))
        is False
    )


def test_portal_case_no_report_row_blocks_unpaid():
    # The portal passes report=None when no report row exists — the gate
    # must still enforce (no report means no override).
    assert download_link_payment_blocked(_mission(paid_in_full=False), None) is True


def test_portal_case_no_report_row_releases_paid():
    assert download_link_payment_blocked(_mission(paid_in_full=True), None) is False


def test_non_billable_never_blocked():
    assert download_link_payment_blocked(_mission(is_billable=False), None) is False
