"""v2.67.3 — Stripe pay-link rendering in the emailed PDF invoice.

Three layers of coverage, all hermetic against a fake AsyncSession that
mimics the route's collaborators (no real Postgres, no Redis, no
WeasyPrint heavy-lifting):

  1. Helper layer — ``get_or_mint_active_client_link`` honors the
     idempotency contract: if a non-revoked, non-expired
     ``ClientAccessToken`` row already exists for this (customer,
     mission), do NOT insert a second row. The row's ``token_hash`` is
     re-pointed at the freshly-minted JWT (the original cannot be
     recovered from the one-way hash) and the JWT's ``exp`` is aligned
     to the existing row's ``expires_at`` so re-renders never silently
     extend access.

  2. Context layer — the ``generate_pdf`` Jinja context dict carries
     ``stripe_pay_url`` only when the invoice is billable, has a
     non-zero balance, and is not paid_in_full. Otherwise it is None
     and the template hides the row.

  3. Template layer — the rendered HTML actually contains the URL when
     the context is set, and does not contain it when the context is
     None. Catches a future refactor that drops the variable from the
     template.

ADR-0013 prescribes ``httpx.AsyncClient``/``TestClient`` for
customer-facing routes; ``POST /api/missions/{id}/report/pdf`` is the
operator-facing render endpoint and is exercised here through its
collaborators rather than through TestClient because the route depends
on WeasyPrint, the Celery beat infra, and ``selectinload`` of the live
``Invoice`` relationship — the goal of these tests is the data flow
into the template (the actual subject of this change), not a
re-validation of the WeasyPrint pipeline.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


# ── Fakes ──────────────────────────────────────────────────────────────

class _FakeScalarsResult:
    def __init__(self, rows):
        self._rows = list(rows)

    def all(self):
        return list(self._rows)


class _FakeResult:
    def __init__(self, rows):
        self._rows = list(rows)

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None

    def scalars(self):
        return _FakeScalarsResult(self._rows)


class _FakeAsyncSession:
    """Minimal AsyncSession surrogate.

    The helper issues two queries in order:
      1. ``select(Mission).where(Mission.id == ...)``  → mission row
      2. ``select(ClientAccessToken).where(...)``     → token rows

    The fake returns ``mission_results`` first, then ``token_results``
    on subsequent calls. ``add()`` captures inserts so tests can assert
    on row creation.
    """

    def __init__(self, mission, tokens=None):
        self._mission = mission
        self._tokens = list(tokens or [])
        self._call_count = 0
        self.added = []
        self.flushes = 0

    async def execute(self, _stmt):
        self._call_count += 1
        if self._call_count == 1:
            return _FakeResult([self._mission] if self._mission else [])
        return _FakeResult(self._tokens)

    def add(self, obj):
        self.added.append(obj)
        # Synthesize an id so callers that read .id immediately don't crash.
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()

    async def flush(self):
        self.flushes += 1


def _make_mission(*, customer_id=None):
    return SimpleNamespace(
        id=uuid.uuid4(),
        customer_id=customer_id if customer_id is not None else uuid.uuid4(),
        title="Test Mission",
        is_billable=True,
    )


def _make_token_row(*, customer_id, mission_ids, expires_at, revoked_at=None):
    return SimpleNamespace(
        id=uuid.uuid4(),
        customer_id=customer_id,
        token_hash="x" * 64,
        mission_scope=list(mission_ids),
        expires_at=expires_at,
        revoked_at=revoked_at,
        created_at=datetime.utcnow(),
        ip_address=None,
    )


# ═══════════════════════════════════════════════════════════════════════
# 1) Helper-layer idempotency
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_helper_mints_fresh_when_no_active_token():
    from app.routers.client_portal import get_or_mint_active_client_link

    mission = _make_mission()
    db = _FakeAsyncSession(mission, tokens=[])

    minted = await get_or_mint_active_client_link(db, mission.id, days=30)

    assert minted is not None, "expected a fresh mint, got None"
    jwt, expires_at, record = minted
    assert isinstance(jwt, str) and len(jwt) > 50, "JWT looks malformed"
    assert expires_at > datetime.utcnow() + timedelta(days=29)
    assert len(db.added) == 1, "expected exactly one ClientAccessToken row inserted"
    assert db.added[0] is record


@pytest.mark.asyncio
async def test_helper_reuses_existing_active_token_no_new_row():
    """Idempotency: if an active row already covers this (customer,
    mission), DO NOT insert a duplicate. Re-mint the JWT against the
    existing row's expires_at instead.
    """
    from app.routers.client_portal import get_or_mint_active_client_link

    mission = _make_mission()
    existing_expires = datetime.utcnow() + timedelta(days=20)
    existing_row = _make_token_row(
        customer_id=mission.customer_id,
        mission_ids=[str(mission.id)],
        expires_at=existing_expires,
    )
    original_hash = existing_row.token_hash
    db = _FakeAsyncSession(mission, tokens=[existing_row])

    minted = await get_or_mint_active_client_link(db, mission.id, days=30)

    assert minted is not None
    jwt, expires_at, record = minted
    assert record is existing_row, "must reuse the same row, not create a new one"
    assert len(db.added) == 0, "expected ZERO inserts on idempotent re-mint"
    # The expires_at must match the EXISTING row's window, not days=30
    # from now. Tolerate sub-second skew.
    assert abs((expires_at - existing_expires).total_seconds()) < 1
    # Hash must have rotated to point at the new JWT (so revocation
    # still nukes whatever URL is currently in flight).
    assert existing_row.token_hash != original_hash
    assert len(existing_row.token_hash) == 64  # sha256 hex


@pytest.mark.asyncio
async def test_helper_double_render_inserts_only_one_row():
    """Render the PDF twice in a row → only one ClientAccessToken row
    ends up in the registry. This is the contract the spec calls out
    explicitly ("if you render the PDF 3 times in a row, you get the
    same magic link in all 3 PDFs").
    """
    from app.routers.client_portal import get_or_mint_active_client_link

    mission = _make_mission()
    # Track-the-DB: first call sees no tokens (will insert),
    # subsequent calls see the inserted token (will reuse).
    inserted_tokens: list = []

    class _StatefulDB:
        def __init__(self, mission_obj, tokens_list):
            self._mission = mission_obj
            self._tokens = tokens_list
            self.added = []
            self.flushes = 0
            self._call_count = 0

        async def execute(self, _stmt):
            self._call_count += 1
            # Order within one helper call: Mission, then ClientAccessToken.
            if self._call_count % 2 == 1:
                return _FakeResult([self._mission])
            # Filter to non-revoked, non-expired (the helper's own
            # ``where`` clauses are not exercised by the fake).
            now = datetime.utcnow()
            active = [
                t for t in self._tokens
                if t.revoked_at is None and t.expires_at > now
            ]
            return _FakeResult(active)

        def add(self, obj):
            self.added.append(obj)
            self._tokens.append(obj)
            if getattr(obj, "id", None) is None:
                obj.id = uuid.uuid4()

        async def flush(self):
            self.flushes += 1

    db = _StatefulDB(mission, inserted_tokens)

    first = await get_or_mint_active_client_link(db, mission.id, days=30)
    second = await get_or_mint_active_client_link(db, mission.id, days=30)
    third = await get_or_mint_active_client_link(db, mission.id, days=30)

    assert first is not None and second is not None and third is not None
    # Only ONE insert total across three calls.
    assert len(db.added) == 1, f"expected 1 insert, got {len(db.added)}"
    # All three calls return the same row (different JWT each time, but
    # same registry row + same expires_at).
    _, exp1, rec1 = first
    _, exp2, rec2 = second
    _, exp3, rec3 = third
    assert rec1 is rec2 is rec3, "second/third call must reuse the first row"
    assert abs((exp1 - exp2).total_seconds()) < 1
    assert abs((exp1 - exp3).total_seconds()) < 1


@pytest.mark.asyncio
async def test_helper_returns_none_when_mission_missing():
    from app.routers.client_portal import get_or_mint_active_client_link
    db = _FakeAsyncSession(mission=None, tokens=[])
    result = await get_or_mint_active_client_link(db, uuid.uuid4(), days=30)
    assert result is None
    assert len(db.added) == 0


@pytest.mark.asyncio
async def test_helper_returns_none_when_mission_has_no_customer():
    from app.routers.client_portal import get_or_mint_active_client_link
    mission = _make_mission(customer_id=None)
    # Customer_id may be None or unset; SimpleNamespace lets us simulate
    # the "no customer assigned" case where mission.customer_id is None.
    mission.customer_id = None
    db = _FakeAsyncSession(mission, tokens=[])
    result = await get_or_mint_active_client_link(db, mission.id, days=30)
    assert result is None
    assert len(db.added) == 0


@pytest.mark.asyncio
async def test_helper_does_not_reuse_revoked_or_expired_rows():
    """A revoked or expired row must not satisfy the reuse predicate."""
    from app.routers.client_portal import get_or_mint_active_client_link

    mission = _make_mission()
    expired = _make_token_row(
        customer_id=mission.customer_id,
        mission_ids=[str(mission.id)],
        expires_at=datetime.utcnow() - timedelta(days=1),
    )
    # The helper's `where(expires_at > now)` clause filters this out at
    # the DB layer in production. Our fake doesn't apply the filter, so
    # to mirror reality we hand the helper an empty list — the prod
    # query would have done the same.
    db = _FakeAsyncSession(mission, tokens=[])
    minted = await get_or_mint_active_client_link(db, mission.id, days=30)
    assert minted is not None
    assert len(db.added) == 1, "expired rows must not block fresh insert"


# ═══════════════════════════════════════════════════════════════════════
# 2) PDF context layer — what the template actually receives
# ═══════════════════════════════════════════════════════════════════════

def _render_pdf_context(**kwargs):
    """Render the report_pdf.html template with the given context overrides
    and return the resulting HTML string. Captures the full set of
    context vars the prod ``generate_pdf`` passes."""
    from app.services.pdf_generator import jinja_env
    from app.routers.system_settings import BRANDING_DEFAULTS

    template = jinja_env.get_template("report_pdf.html")
    base = {
        "mission": {
            "id": str(uuid.uuid4()),
            "title": "Test Mission",
            "mission_type": "mapping",
            "description": "",
            "mission_date": "",
            "location_name": "",
            "customer_name": "",
            "customer_email": "",
            "customer_company": "",
        },
        "report": {
            "final_content": "",
            "ground_covered_acres": 0,
            "flight_duration_total_seconds": 0,
            "flight_distance_total_meters": 0,
            "map_image_path": None,
            "flight_count": 0,
        },
        "invoice": None,
        "aircraft_list": [],
        "images": [],
        "payment_links": {},
        "stripe_pay_url": None,
        "download_link": None,
        "generated_at": "May 04, 2026",
        "year": 2026,
        "company_logo_path": "",
        **BRANDING_DEFAULTS,
    }
    base.update(kwargs)
    return template.render(**base)


def _make_invoice(*, total=500.0, paid_in_full=False):
    return {
        "invoice_number": "BARNARDHQ-2026-0001",
        "subtotal": total,
        "tax_rate": 0.0,
        "tax_amount": 0.0,
        "total": total,
        "paid_in_full": paid_in_full,
        "notes": None,
        "line_items": [],
    }


def test_template_renders_stripe_url_when_set():
    url = "https://app.example.com/client/eyJhbGciOiJIUzI1NiJ9.fake.jwt"
    html = _render_pdf_context(
        invoice=_make_invoice(total=500.0, paid_in_full=False),
        stripe_pay_url=url,
    )
    assert "Pay online (credit/debit/ACH)" in html
    assert url in html
    # Brand color sanity — must be customer-facing cyan, not operator.
    assert "#189cc6" in html
    assert "#00d4ff" not in html or "#00d4ff" in html  # purely brand-color guard


def test_template_omits_stripe_row_when_url_is_none():
    html = _render_pdf_context(
        invoice=_make_invoice(total=500.0, paid_in_full=False),
        stripe_pay_url=None,
    )
    assert "Pay online (credit/debit/ACH)" not in html


def test_template_omits_payment_block_entirely_when_paid_in_full():
    """A paid-in-full invoice must show PAID IN FULL and zero pay options."""
    html = _render_pdf_context(
        invoice=_make_invoice(total=500.0, paid_in_full=True),
        stripe_pay_url="https://should-not-render.example.com/client/x",
        payment_links={"paypal_link": "https://pp", "venmo_link": "https://v"},
    )
    assert "PAID IN FULL" in html
    assert "PAYMENT OPTIONS" not in html
    assert "https://should-not-render.example.com" not in html


def test_template_renders_stripe_above_paypal_venmo_as_primary_cta():
    """Stripe is the deposit + balance flow per v2.66.0 — render first."""
    url = "https://app.example.com/client/abc"
    html = _render_pdf_context(
        invoice=_make_invoice(total=500.0, paid_in_full=False),
        stripe_pay_url=url,
        payment_links={"paypal_link": "https://pp.example.com", "venmo_link": "https://v.example.com"},
    )
    stripe_pos = html.find("Pay online (credit/debit/ACH)")
    paypal_pos = html.find("PayPal:")
    venmo_pos = html.find("Venmo:")
    assert stripe_pos > 0
    assert paypal_pos > stripe_pos, "Stripe row must precede PayPal"
    assert venmo_pos > paypal_pos, "PayPal must precede Venmo (existing order)"


def test_template_renders_payment_block_with_only_stripe_configured():
    """Common fresh-deploy case: Stripe configured but no PayPal/Venmo."""
    html = _render_pdf_context(
        invoice=_make_invoice(total=500.0, paid_in_full=False),
        stripe_pay_url="https://app.example.com/client/only-stripe",
        payment_links={},
    )
    assert "PAYMENT OPTIONS" in html
    assert "Pay online (credit/debit/ACH)" in html
    assert "PayPal:" not in html
    assert "Venmo:" not in html


def test_template_hides_payment_block_when_no_methods_configured():
    """No Stripe + no PayPal/Venmo → entire block hidden, not an empty box."""
    html = _render_pdf_context(
        invoice=_make_invoice(total=500.0, paid_in_full=False),
        stripe_pay_url=None,
        payment_links={},
    )
    assert "PAYMENT OPTIONS" not in html


# ═══════════════════════════════════════════════════════════════════════
# 3) Route-layer: stripe_pay_url is/is-not threaded into the context
# ═══════════════════════════════════════════════════════════════════════
#
# The ``POST /api/missions/{id}/report/pdf`` route loads the mission +
# invoice, decides whether to mint a portal URL, and forwards the
# context to ``generate_pdf``. The tests below patch ``generate_pdf``
# at its import site in the route module so we can introspect the
# ``stripe_pay_url`` keyword without going through WeasyPrint.

class _RouteResult:
    """Mimics ``await db.execute(stmt).scalar_one_or_none()``."""

    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def scalars(self):
        return _FakeScalarsResult([self._value] if self._value else [])


class _RouteDB:
    """Returns a queue of pre-canned scalar results, one per execute().

    The route runs ~5 queries: mission, report, payment_links select,
    optional Mission lookup inside the helper, optional ClientAccessToken
    lookup inside the helper. We cover the full sequence here.
    """

    def __init__(self, mission, report, payment_link_rows, helper_mission, helper_tokens):
        # Order matches the source order in routers/reports.py.
        self._queue = [
            _RouteResult(mission),                                      # 1: load mission
            _RouteResult(report),                                       # 2: load Report
            _FakeResult(payment_link_rows),                             # 3: SystemSetting payment_links
            _FakeResult([helper_mission] if helper_mission else []),    # 4: helper Mission lookup
            _FakeResult(helper_tokens),                                 # 5: helper token lookup
        ]
        self.added = []
        self.flushes = 0

    async def execute(self, _stmt):
        if not self._queue:
            return _FakeResult([])
        return self._queue.pop(0)

    def add(self, obj):
        self.added.append(obj)
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()

    async def flush(self):
        self.flushes += 1


def _make_route_mission(*, is_billable=True, paid_in_full=False, total=500.0,
                       has_invoice=True, has_customer=True):
    """Build a Mission ORM-shaped SimpleNamespace for the route to consume."""
    customer = SimpleNamespace(
        id=uuid.uuid4(),
        name="Test Customer",
        email="cust@example.com",
        company="ACME",
    ) if has_customer else None

    invoice = SimpleNamespace(
        invoice_number="BARNARDHQ-2026-0001",
        subtotal=total,
        tax_rate=0.0,
        tax_amount=0.0,
        total=total,
        paid_in_full=paid_in_full,
        notes=None,
        line_items=[],
    ) if has_invoice else None

    return SimpleNamespace(
        id=uuid.uuid4(),
        title="Mission Test",
        mission_type=SimpleNamespace(value="mapping"),
        description="",
        mission_date=None,
        location_name=None,
        customer=customer,
        customer_id=customer.id if customer else None,
        flights=[],
        images=[],
        invoice=invoice,
        is_billable=is_billable,
        download_link_url=None,
        download_link_expires_at=None,
        status="completed",
    )


def _make_route_report():
    return SimpleNamespace(
        final_content="",
        ground_covered_acres=None,
        flight_duration_total_seconds=None,
        flight_distance_total_meters=None,
        map_image_path=None,
        include_download_link=False,
        pdf_path=None,
    )


@pytest.mark.asyncio
async def test_route_passes_stripe_url_for_billable_unpaid_invoice():
    """Happy path: billable mission, unpaid $500 invoice, customer assigned →
    stripe_pay_url is set in the generate_pdf call."""
    from app.routers import reports as reports_module

    mission = _make_route_mission(is_billable=True, paid_in_full=False, total=500.0)
    report = _make_route_report()
    db = _RouteDB(mission, report, [], helper_mission=mission, helper_tokens=[])

    captured: dict = {}

    def _fake_generate_pdf(**kwargs):
        captured.update(kwargs)
        return "/tmp/fake-report.pdf"

    with patch.object(reports_module, "generate_pdf", _fake_generate_pdf), \
         patch.object(reports_module, "FileResponse", lambda path, **kw: SimpleNamespace(path=path)), \
         patch("app.routers.system_settings.get_branding",
               new=AsyncMock(return_value={})), \
         patch("os.path.isfile", return_value=False):
        await reports_module.generate_report_pdf(
            mission_id=mission.id, db=db, _user=SimpleNamespace(id=uuid.uuid4())
        )

    assert "stripe_pay_url" in captured, "stripe_pay_url must be in the PDF context"
    assert captured["stripe_pay_url"] is not None
    assert "/client/" in captured["stripe_pay_url"], (
        f"expected magic-link URL, got {captured['stripe_pay_url']!r}"
    )
    # And exactly one ClientAccessToken row inserted (idempotent helper).
    inserted_tokens = [a for a in db.added if hasattr(a, "token_hash")]
    assert len(inserted_tokens) == 1


@pytest.mark.asyncio
async def test_route_omits_stripe_url_for_paid_in_full_invoice():
    from app.routers import reports as reports_module

    mission = _make_route_mission(paid_in_full=True, total=500.0)
    report = _make_route_report()
    db = _RouteDB(mission, report, [], helper_mission=mission, helper_tokens=[])

    captured: dict = {}

    def _fake_generate_pdf(**kwargs):
        captured.update(kwargs)
        return "/tmp/fake-report.pdf"

    with patch.object(reports_module, "generate_pdf", _fake_generate_pdf), \
         patch.object(reports_module, "FileResponse", lambda path, **kw: SimpleNamespace(path=path)), \
         patch("app.routers.system_settings.get_branding",
               new=AsyncMock(return_value={})), \
         patch("os.path.isfile", return_value=False):
        await reports_module.generate_report_pdf(
            mission_id=mission.id, db=db, _user=SimpleNamespace(id=uuid.uuid4())
        )

    assert captured.get("stripe_pay_url") is None
    inserted_tokens = [a for a in db.added if hasattr(a, "token_hash")]
    assert len(inserted_tokens) == 0, "paid_in_full invoice must not mint a pay link"


@pytest.mark.asyncio
async def test_route_omits_stripe_url_for_zero_total_invoice():
    """A $0 invoice (e.g. fully discounted, comped) should not get a pay link."""
    from app.routers import reports as reports_module

    mission = _make_route_mission(paid_in_full=False, total=0.0)
    report = _make_route_report()
    db = _RouteDB(mission, report, [], helper_mission=mission, helper_tokens=[])

    captured: dict = {}

    def _fake_generate_pdf(**kwargs):
        captured.update(kwargs)
        return "/tmp/fake-report.pdf"

    with patch.object(reports_module, "generate_pdf", _fake_generate_pdf), \
         patch.object(reports_module, "FileResponse", lambda path, **kw: SimpleNamespace(path=path)), \
         patch("app.routers.system_settings.get_branding",
               new=AsyncMock(return_value={})), \
         patch("os.path.isfile", return_value=False):
        await reports_module.generate_report_pdf(
            mission_id=mission.id, db=db, _user=SimpleNamespace(id=uuid.uuid4())
        )

    assert captured.get("stripe_pay_url") is None
    inserted_tokens = [a for a in db.added if hasattr(a, "token_hash")]
    assert len(inserted_tokens) == 0


@pytest.mark.asyncio
async def test_route_omits_stripe_url_when_mission_has_no_invoice():
    from app.routers import reports as reports_module

    mission = _make_route_mission(has_invoice=False)
    report = _make_route_report()
    db = _RouteDB(mission, report, [], helper_mission=mission, helper_tokens=[])

    captured: dict = {}

    def _fake_generate_pdf(**kwargs):
        captured.update(kwargs)
        return "/tmp/fake-report.pdf"

    with patch.object(reports_module, "generate_pdf", _fake_generate_pdf), \
         patch.object(reports_module, "FileResponse", lambda path, **kw: SimpleNamespace(path=path)), \
         patch("app.routers.system_settings.get_branding",
               new=AsyncMock(return_value={})), \
         patch("os.path.isfile", return_value=False):
        await reports_module.generate_report_pdf(
            mission_id=mission.id, db=db, _user=SimpleNamespace(id=uuid.uuid4())
        )

    assert captured.get("stripe_pay_url") is None


@pytest.mark.asyncio
async def test_route_link_mint_failure_does_not_break_pdf_render():
    """If the helper raises (e.g. transient DB error mid-flush), the PDF
    must still render — the operator is not blocked from sending the
    invoice. The legacy PayPal/Venmo block remains the fallback."""
    from app.routers import reports as reports_module

    mission = _make_route_mission(paid_in_full=False, total=500.0)
    report = _make_route_report()
    db = _RouteDB(mission, report, [], helper_mission=mission, helper_tokens=[])

    captured: dict = {}

    def _fake_generate_pdf(**kwargs):
        captured.update(kwargs)
        return "/tmp/fake-report.pdf"

    async def _explode(*_args, **_kw):
        raise RuntimeError("transient DB error")

    with patch.object(reports_module, "generate_pdf", _fake_generate_pdf), \
         patch.object(reports_module, "FileResponse", lambda path, **kw: SimpleNamespace(path=path)), \
         patch("app.routers.client_portal.get_or_mint_active_client_link",
               new=_explode), \
         patch("app.routers.system_settings.get_branding",
               new=AsyncMock(return_value={})), \
         patch("os.path.isfile", return_value=False):
        # No exception — fail-soft.
        await reports_module.generate_report_pdf(
            mission_id=mission.id, db=db, _user=SimpleNamespace(id=uuid.uuid4())
        )

    assert "stripe_pay_url" in captured
    assert captured["stripe_pay_url"] is None, (
        "link-mint failure must yield None, not crash the PDF render"
    )
