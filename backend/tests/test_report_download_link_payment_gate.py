"""ADR-0039 — unpaid-invoice download-link gate unit coverage.

Hermetic tests (SimpleNamespace fakes, no DB) over the two router helpers
that gate the mission-footage download link:

- `_download_link_payment_blocked` — the policy decision
- `_build_download_link` — the single choke point both exposure paths
  (PDF render + report email) go through

Policy under test: clients do not receive the download link until the
mission's invoice is paid in full; a billable mission with NO invoice is
fail-closed; a $0 invoice has nothing to collect and passes; the per-report
`download_link_payment_override` is the deliberate operator release valve.

To run:

    cd backend
    pytest tests/test_report_download_link_payment_gate.py -v
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from app.routers.reports import _build_download_link, _download_link_payment_blocked


def _mission(
    *,
    is_billable: bool = True,
    invoice=None,
    download_link_url: str | None = "https://drop.example.com/abc",
    download_link_expires_at: datetime | None = None,
):
    return SimpleNamespace(
        id="11111111-1111-1111-1111-111111111111",
        is_billable=is_billable,
        invoice=invoice,
        download_link_url=download_link_url,
        download_link_expires_at=download_link_expires_at,
    )


def _invoice(*, paid_in_full: bool = False, total: float = 400.50):
    return SimpleNamespace(
        invoice_number="BARNARDHQ-2026-0005",
        paid_in_full=paid_in_full,
        total=total,
    )


def _report(*, include_download_link: bool = True, override: bool = False):
    return SimpleNamespace(
        include_download_link=include_download_link,
        download_link_payment_override=override,
        pdf_path=None,
    )


# ── _download_link_payment_blocked ─────────────────────────────────────

def test_unpaid_invoice_blocks():
    m = _mission(invoice=_invoice(paid_in_full=False))
    assert _download_link_payment_blocked(m, _report()) is True


def test_paid_in_full_allows():
    m = _mission(invoice=_invoice(paid_in_full=True))
    assert _download_link_payment_blocked(m, _report()) is False


def test_override_releases_unpaid():
    m = _mission(invoice=_invoice(paid_in_full=False))
    assert _download_link_payment_blocked(m, _report(override=True)) is False


def test_non_billable_mission_allows():
    m = _mission(is_billable=False, invoice=None)
    assert _download_link_payment_blocked(m, _report()) is False


def test_billable_without_invoice_fails_closed():
    m = _mission(is_billable=True, invoice=None)
    assert _download_link_payment_blocked(m, _report()) is True


def test_billable_without_invoice_override_releases():
    m = _mission(is_billable=True, invoice=None)
    assert _download_link_payment_blocked(m, _report(override=True)) is False


def test_zero_total_invoice_allows():
    m = _mission(invoice=_invoice(paid_in_full=False, total=0))
    assert _download_link_payment_blocked(m, _report()) is False


def test_none_total_invoice_allows():
    m = _mission(invoice=_invoice(paid_in_full=False, total=None))
    assert _download_link_payment_blocked(m, _report()) is False


def test_legacy_report_row_without_override_attr_blocks():
    # Rows validated before migration 0008 (or fakes without the column)
    # must default to gate-enforced, not AttributeError.
    m = _mission(invoice=_invoice(paid_in_full=False))
    legacy = SimpleNamespace(include_download_link=True, pdf_path=None)
    assert _download_link_payment_blocked(m, legacy) is True


# ── _build_download_link (the choke point) ─────────────────────────────

def test_build_returns_none_when_gated():
    m = _mission(invoice=_invoice(paid_in_full=False))
    assert _build_download_link(m, _report()) is None


def test_build_returns_link_when_paid():
    m = _mission(
        invoice=_invoice(paid_in_full=True),
        download_link_expires_at=datetime(2026, 8, 1, 17, 0),
    )
    link = _build_download_link(m, _report())
    assert link == {
        "url": "https://drop.example.com/abc",
        "expires_at": "August 01, 2026 at 05:00 PM",
    }


def test_build_returns_link_on_override():
    m = _mission(invoice=_invoice(paid_in_full=False))
    link = _build_download_link(m, _report(override=True))
    assert link is not None
    assert link["url"] == "https://drop.example.com/abc"
    assert link["expires_at"] == "N/A"


def test_build_none_when_not_included():
    m = _mission(invoice=_invoice(paid_in_full=True))
    assert _build_download_link(m, _report(include_download_link=False)) is None


def test_build_none_when_no_url():
    m = _mission(invoice=_invoice(paid_in_full=True), download_link_url=None)
    assert _build_download_link(m, _report()) is None
