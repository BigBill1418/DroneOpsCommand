"""Google review CTA renders on every customer-facing surface.

Five surfaces share one `google_review_url` context variable:

  1. report_pdf.html       — PDF invoice footer block (under totals)
  2. email_body.html       — Report-delivery email (carries the PDF)
  3. payment_received_email.html — Stripe payment confirmation
  4. report_ready_email.html     — "Your report is ready" notification
  5. ClientMissionDetail.tsx (frontend) — post-payment success state

This test exercises the four Jinja templates: each must render the
review URL when set, and omit the CTA block entirely when the URL is
falsy (env unset). The frontend surface is gated client-side on
`invoice.google_review_url` truthiness — covered by typecheck, not
here.
"""
from app.routers.system_settings import BRANDING_DEFAULTS
from app.services.pdf_generator import jinja_env

REVIEW_URL = "https://g.page/r/test-review-cta/review"


def _invoice():
    return {
        "invoice_number": "TEST-0001",
        "subtotal": 100.0,
        "tax_rate": 0.0,
        "tax_amount": 0.0,
        "total": 100.0,
        "paid_in_full": True,
        "notes": None,
        "line_items": [],
    }


def _render(template_name, **extra):
    template = jinja_env.get_template(template_name)
    ctx = {
        **BRANDING_DEFAULTS,
        "google_review_url": REVIEW_URL,
        "customer_name": "Test Customer",
        "mission_title": "Test Mission",
        "company_logo_url": "",
        "portal_url": "https://portal.example.com/m/123",
        "download_link": None,
        "invoice_total": "100.00",
        "payment_method": "Credit/Debit Card",
        "paid_at": "May 18, 2026 at 10:00 AM",
        # PDF-only context
        "mission": {"id": "x", "title": "T", "mission_type": "m"},
        "report": {"final_content": ""},
        "invoice": _invoice(),
        "aircraft_list": [],
        "images": [],
        "payment_links": {},
        "stripe_pay_url": None,
        "generated_at": "May 18, 2026",
        "year": 2026,
        "company_logo_path": "",
    }
    ctx.update(extra)
    return template.render(**ctx)


def test_pdf_invoice_footer_contains_review_link():
    html = _render("report_pdf.html")
    assert REVIEW_URL in html
    assert "Leave us a Google review" in html


def test_pdf_invoice_footer_hidden_when_url_unset():
    html = _render("report_pdf.html", google_review_url="")
    assert REVIEW_URL not in html
    assert "Leave us a Google review" not in html


def test_report_delivery_email_contains_review_cta():
    html = _render("email_body.html")
    assert REVIEW_URL in html
    assert "LEAVE A GOOGLE REVIEW" in html


def test_report_delivery_email_hides_cta_when_url_unset():
    html = _render("email_body.html", google_review_url="")
    assert REVIEW_URL not in html
    assert "LEAVE A GOOGLE REVIEW" not in html


def test_payment_received_email_contains_review_cta():
    html = _render("payment_received_email.html")
    assert REVIEW_URL in html
    assert "LEAVE A GOOGLE REVIEW" in html


def test_payment_received_email_hides_cta_when_url_unset():
    html = _render("payment_received_email.html", google_review_url="")
    assert REVIEW_URL not in html


def test_report_ready_email_contains_review_cta():
    html = _render("report_ready_email.html")
    assert REVIEW_URL in html
    assert "LEAVE A GOOGLE REVIEW" in html


def test_report_ready_email_hides_cta_when_url_unset():
    html = _render("report_ready_email.html", google_review_url="")
    assert REVIEW_URL not in html
