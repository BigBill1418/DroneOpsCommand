# Payment Reminders (Dunning) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automatically email a customer a gentle payment reminder 48h after an unpaid invoice is billed, and a firmer final notice at 7 days that also emails an overdue alert to `bill@barnardhq.com`.

**Architecture:** A daily Celery-beat sweep (`send_payment_reminders`) finds billed, not-paid-in-full invoices and, per invoice, computes which stage is due from `billed_at` + two sent-timestamp columns, sends the stage's email(s), and stamps the timestamp (idempotent). Core decision logic (`due_stage`, `process_invoice`) is pure and hermetically tested; the sweep is thin async orchestration over `async_session`.

**Tech Stack:** FastAPI, async SQLAlchemy (asyncpg), Celery + Redis beat, Jinja2 email templates, aiosmtplib. Spec: `docs/plans/2026-05-24-payment-reminders-dunning.md`.

---

## File structure

- **Modify** `backend/app/models/invoice.py` — add 3 nullable timestamp columns to `Invoice`.
- **Modify** `backend/app/main.py` — idempotent `ADD COLUMN` for the 3 columns (mirror the deposit-column block).
- **Create** `backend/app/services/dunning.py` — `Stage` constants, `amount_due()`, `due_stage()` (pure), `process_invoice()` (pure, injected sender), `DunningSender` (real email sender), `run_dunning_sweep()` (async orchestration).
- **Modify** `backend/app/services/email_service.py` — add `_send_html_email()` helper + `send_payment_reminder_email()`, `send_payment_final_notice_email()`, `send_operator_overdue_email()`.
- **Create** `backend/app/templates/payment_reminder_email.html`, `backend/app/templates/payment_final_notice_email.html`.
- **Modify** `backend/app/tasks/celery_tasks.py` — `@celery_app.task send_payment_reminders` + `beat_schedule` entry.
- **Modify** `backend/app/routers/client_portal.py` — set `invoice.billed_at` on first client-link send.
- **Create** `backend/tests/test_dunning.py` — pure-logic tests.

Constants (define in `dunning.py`): `REMINDER_AFTER = timedelta(hours=48)`, `FINAL_AFTER = timedelta(days=7)`, `OPERATOR_ALERT_EMAIL = "bill@barnardhq.com"`.

---

### Task 1: Add reminder-tracking columns to the Invoice model

**Files:**
- Modify: `backend/app/models/invoice.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_dunning.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_dunning.py`:

```python
from app.models.invoice import Invoice


def test_invoice_has_dunning_columns():
    inv = Invoice(mission_id=None)  # type: ignore[arg-type]
    # all three start unset and are assignable
    assert inv.billed_at is None
    assert inv.reminder_sent_at is None
    assert inv.final_notice_sent_at is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python3 -m pytest tests/test_dunning.py::test_invoice_has_dunning_columns -q -p no:cacheprovider`
Expected: FAIL — `AttributeError: 'Invoice' object has no attribute 'billed_at'`.

- [ ] **Step 3: Add the columns to the model**

In `backend/app/models/invoice.py`, in the `Invoice` class, immediately after the existing `deposit_payment_method` mapped column, add:

```python
    # Dunning / payment-reminder tracking (2026-05-24).
    billed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    reminder_sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    final_notice_sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
```

- [ ] **Step 4: Add idempotent ALTER in main.py**

In `backend/app/main.py`, in the startup column-add block where the deposit columns are added (the `("deposit_required", "ALTER TABLE invoices ADD COLUMN ...")` tuple list), append these tuples to that same list:

```python
                ("billed_at",            "ALTER TABLE invoices ADD COLUMN billed_at TIMESTAMP"),
                ("reminder_sent_at",     "ALTER TABLE invoices ADD COLUMN reminder_sent_at TIMESTAMP"),
                ("final_notice_sent_at", "ALTER TABLE invoices ADD COLUMN final_notice_sent_at TIMESTAMP"),
```

(They are nullable with no default, so the add is safe on a populated table. The existing loop already guards each with an `if name not in invoice_cols` existence check.)

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && python3 -m pytest tests/test_dunning.py::test_invoice_has_dunning_columns -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/models/invoice.py backend/app/main.py backend/tests/test_dunning.py
git commit -m "feat(invoices): add dunning timestamp columns (billed_at, reminder_sent_at, final_notice_sent_at)"
```

---

### Task 2: `due_stage` — pure stage-decision logic

**Files:**
- Create: `backend/app/services/dunning.py`
- Test: `backend/tests/test_dunning.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_dunning.py`:

```python
from datetime import datetime, timedelta
from types import SimpleNamespace
from app.services import dunning


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python3 -m pytest tests/test_dunning.py -q -p no:cacheprovider`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.dunning'`.

- [ ] **Step 3: Implement `due_stage`**

Create `backend/app/services/dunning.py`:

```python
"""Automated payment reminders (dunning).

Two customer touches per unpaid invoice:
  - +48h  -> gentle reminder email
  - +7d   -> firmer final-notice email + operator overdue email to bill@

Core decision logic (`due_stage`, `process_invoice`) is pure and hermetically
tested. `run_dunning_sweep` is the async orchestration run by the daily Celery
beat task. Idempotent: each stage stamps a timestamp and never re-sends.
Design spec: docs/plans/2026-05-24-payment-reminders-dunning.md
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

logger = logging.getLogger("doc.dunning")

REMINDER_AFTER = timedelta(hours=48)
FINAL_AFTER = timedelta(days=7)
OPERATOR_ALERT_EMAIL = "bill@barnardhq.com"

STAGE_REMINDER = "reminder"
STAGE_FINAL = "final"


def due_stage(invoice, now: datetime) -> str | None:
    """Which dunning stage (if any) is due for `invoice` at `now`.

    Returns STAGE_FINAL, STAGE_REMINDER, or None. Pure — no I/O.
    """
    if invoice.billed_at is None or invoice.paid_in_full:
        return None
    age = now - invoice.billed_at
    # 7-day final notice takes precedence (covers the "reached day 7 without a
    # prior reminder" case — skip the gentle one and go straight to final).
    if age >= FINAL_AFTER:
        return None if invoice.final_notice_sent_at else STAGE_FINAL
    if age >= REMINDER_AFTER:
        return None if invoice.reminder_sent_at else STAGE_REMINDER
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python3 -m pytest tests/test_dunning.py -q -p no:cacheprovider`
Expected: PASS (all due_stage tests + the column test).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/dunning.py backend/tests/test_dunning.py
git commit -m "feat(dunning): add pure due_stage decision logic"
```

---

### Task 3: `amount_due` + `process_invoice` (decide, stamp, call sender)

**Files:**
- Modify: `backend/app/services/dunning.py`
- Test: `backend/tests/test_dunning.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_dunning.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python3 -m pytest tests/test_dunning.py -q -p no:cacheprovider`
Expected: FAIL — `AttributeError: module 'app.services.dunning' has no attribute 'amount_due'`.

- [ ] **Step 3: Implement `amount_due` and `process_invoice`**

Append to `backend/app/services/dunning.py`:

```python
def amount_due(invoice) -> float:
    """The next amount the portal will charge — matches the Stripe charge."""
    if getattr(invoice, "deposit_required", False) and not getattr(invoice, "deposit_paid", False):
        return float(invoice.deposit_amount or 0)
    return float(invoice.balance_amount or 0)


def process_invoice(invoice, now: datetime, sender, *, now_fn=datetime.utcnow) -> str | None:
    """Send the due stage for `invoice` (via `sender`) and stamp it. Pure aside
    from the injected `sender`. Returns the stage sent, or None.

    `sender` must expose send_reminder(invoice, amount), send_final(invoice, amount),
    send_operator(invoice, amount). `now_fn` supplies the stamp value (injectable
    for tests)."""
    stage = due_stage(invoice, now)
    if stage is None:
        return None
    amt = amount_due(invoice)
    if stage == STAGE_REMINDER:
        sender.send_reminder(invoice, amt)
        invoice.reminder_sent_at = now_fn()
    elif stage == STAGE_FINAL:
        sender.send_final(invoice, amt)
        sender.send_operator(invoice, amt)
        invoice.final_notice_sent_at = now_fn()
    return stage
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python3 -m pytest tests/test_dunning.py -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/dunning.py backend/tests/test_dunning.py
git commit -m "feat(dunning): add amount_due + process_invoice (decide/stamp/send)"
```

---

### Task 4: Email send functions + templates

**Files:**
- Modify: `backend/app/services/email_service.py`
- Create: `backend/app/templates/payment_reminder_email.html`
- Create: `backend/app/templates/payment_final_notice_email.html`

- [ ] **Step 1: Create the reminder template**

Create `backend/app/templates/payment_reminder_email.html`:

```html
<!DOCTYPE html>
<html>
<body style="font-family: Arial, sans-serif; color: #1a1f2e; max-width: 600px; margin: 0 auto;">
  {% if company_logo_url %}<img src="{{ company_logo_url }}" alt="{{ company_name }}" style="max-height: 60px; margin: 16px 0;">{% endif %}
  <h2 style="color: #0e7490;">A friendly reminder — invoice {{ invoice_number }}</h2>
  <p>Hi {{ customer_name }},</p>
  <p>Just a gentle nudge that your invoice for <strong>{{ mission_title }}</strong> has an outstanding balance of
     <strong>${{ "%.2f"|format(amount_due) }}</strong>.</p>
  <p>You can pay securely here:</p>
  <p><a href="{{ pay_url }}" style="background:#0e7490;color:#fff;padding:12px 20px;border-radius:6px;text-decoration:none;display:inline-block;">Pay invoice {{ invoice_number }}</a></p>
  <p>If you've already paid, please disregard this message — thank you!</p>
  <p>— {{ company_name }}</p>
</body>
</html>
```

- [ ] **Step 2: Create the final-notice template**

Create `backend/app/templates/payment_final_notice_email.html`:

```html
<!DOCTYPE html>
<html>
<body style="font-family: Arial, sans-serif; color: #1a1f2e; max-width: 600px; margin: 0 auto;">
  {% if company_logo_url %}<img src="{{ company_logo_url }}" alt="{{ company_name }}" style="max-height: 60px; margin: 16px 0;">{% endif %}
  <h2 style="color: #b91c1c;">Final notice — invoice {{ invoice_number }} is past due</h2>
  <p>Hi {{ customer_name }},</p>
  <p>Our records show invoice <strong>{{ invoice_number }}</strong> for <strong>{{ mission_title }}</strong>
     remains unpaid, with an outstanding balance of <strong>${{ "%.2f"|format(amount_due) }}</strong>, now more than
     7 days past billing.</p>
  <p>Please settle the balance to avoid late charges. Per our Terms of Service (§6.4), invoices unpaid more than 30
     days past due accrue a late fee of 1.5% per month.</p>
  <p><a href="{{ pay_url }}" style="background:#b91c1c;color:#fff;padding:12px 20px;border-radius:6px;text-decoration:none;display:inline-block;">Pay invoice {{ invoice_number }} now</a></p>
  <p>If you've already paid or have questions, please reply to this email.</p>
  <p>— {{ company_name }}</p>
</body>
</html>
```

- [ ] **Step 3: Add the send helper + three send functions**

In `backend/app/services/email_service.py`, add (after the existing `_get_branding` / SMTP helpers; reuse the module's existing imports — `aiosmtplib`, `MIMEMultipart`, `MIMEText`, `jinja_env`, `get_smtp_settings`, `_get_branding`, `_parse_bool`, `settings`, `logger`):

```python
async def _send_html_email(to_email: str, subject: str, html_body: str, db=None) -> bool:
    """Shared HTML-email sender (SMTP from DB settings, env fallback)."""
    if db:
        smtp = await get_smtp_settings(db)
    else:
        smtp = {
            "smtp_host": settings.smtp_host, "smtp_port": str(settings.smtp_port),
            "smtp_user": settings.smtp_user, "smtp_password": settings.smtp_password,
            "smtp_from_email": settings.smtp_from_email, "smtp_from_name": settings.smtp_from_name,
            "smtp_use_tls": settings.smtp_use_tls,
        }
    if not smtp["smtp_host"]:
        raise ValueError("SMTP not configured. Set SMTP_HOST in settings.")

    msg = MIMEMultipart()
    msg["From"] = f"{smtp['smtp_from_name']} <{smtp['smtp_from_email']}>"
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(html_body, "html"))

    smtp_port = int(smtp["smtp_port"])
    tls_flag = smtp["smtp_use_tls"] if isinstance(smtp["smtp_use_tls"], bool) else _parse_bool(str(smtp["smtp_use_tls"]), True)
    tls_kwargs = {"use_tls": True} if smtp_port == 465 else {"start_tls": tls_flag}
    await aiosmtplib.send(
        msg, hostname=smtp["smtp_host"], port=smtp_port,
        username=smtp["smtp_user"] or None, password=smtp["smtp_password"] or None,
        **tls_kwargs,
    )
    logger.info("Email sent to %s (%s)", to_email, subject)
    return True


async def send_payment_reminder_email(*, to_email, customer_name, mission_title,
                                      invoice_number, amount_due, pay_url, db=None) -> bool:
    branding = await _get_branding(db)
    html = jinja_env.get_template("payment_reminder_email.html").render(
        customer_name=customer_name, mission_title=mission_title,
        invoice_number=invoice_number, amount_due=amount_due, pay_url=pay_url, **branding,
    )
    return await _send_html_email(to_email, f"Reminder: invoice {invoice_number}", html, db)


async def send_payment_final_notice_email(*, to_email, customer_name, mission_title,
                                          invoice_number, amount_due, pay_url, db=None) -> bool:
    branding = await _get_branding(db)
    html = jinja_env.get_template("payment_final_notice_email.html").render(
        customer_name=customer_name, mission_title=mission_title,
        invoice_number=invoice_number, amount_due=amount_due, pay_url=pay_url, **branding,
    )
    return await _send_html_email(to_email, f"Final notice: invoice {invoice_number} past due", html, db)


async def send_operator_overdue_email(*, invoice_number, amount_due, customer_name,
                                      customer_email, mission_url, db=None) -> bool:
    html = (
        f"<p>Invoice <strong>{invoice_number}</strong> is <strong>7+ days overdue</strong>.</p>"
        f"<p>Amount due: <strong>${amount_due:.2f}</strong><br>"
        f"Customer: {customer_name} ({customer_email or 'NO EMAIL ON FILE'})</p>"
        f"<p><a href=\"{mission_url}\">Open the mission</a></p>"
    )
    return await _send_html_email("bill@barnardhq.com", f"[OVERDUE] {invoice_number} — ${amount_due:.2f}", html, db)
```

- [ ] **Step 4: Verify the module imports cleanly**

Run: `cd backend && python3 -c "import app.services.email_service as e; assert hasattr(e, 'send_payment_reminder_email') and hasattr(e, 'send_operator_overdue_email')" && echo OK`
Expected: `OK`.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/email_service.py backend/app/templates/payment_reminder_email.html backend/app/templates/payment_final_notice_email.html
git commit -m "feat(email): payment reminder / final-notice / operator-overdue email senders + templates"
```

---

### Task 5: `DunningSender` + `run_dunning_sweep` (async orchestration)

**Files:**
- Modify: `backend/app/services/dunning.py`

- [ ] **Step 1: Implement the sender + sweep**

Append to `backend/app/services/dunning.py`:

```python
async def _build_pay_url(db, mission_id) -> str:
    """Customer magic pay-link (reuses the client-link helper). Falls back to the
    plain portal URL if no token can be minted (e.g. no customer)."""
    from app.config import settings
    from app.routers.client_portal import get_or_mint_active_client_link
    minted = await get_or_mint_active_client_link(db, mission_id)
    if minted:
        client_jwt = minted[0]
        return f"{settings.frontend_url.rstrip('/')}/client/{client_jwt}"
    return f"{settings.frontend_url.rstrip('/')}/client/missions/{mission_id}"


class DunningSender:
    """Real email sender. Each method runs its async email coroutine to completion.
    Constructed with the loaded mission/customer context for one invoice."""
    def __init__(self, db, loop, *, mission, customer, pay_url, mission_url):
        self._db, self._loop = db, loop
        self.mission, self.customer, self.pay_url, self.mission_url = mission, customer, pay_url, mission_url

    def send_reminder(self, invoice, amount):
        from app.services.email_service import send_payment_reminder_email
        self._loop.run_until_complete(send_payment_reminder_email(
            to_email=self.customer.email, customer_name=self.customer.name,
            mission_title=self.mission.title, invoice_number=invoice.invoice_number or "(no number)",
            amount_due=amount, pay_url=self.pay_url, db=self._db))

    def send_final(self, invoice, amount):
        from app.services.email_service import send_payment_final_notice_email
        self._loop.run_until_complete(send_payment_final_notice_email(
            to_email=self.customer.email, customer_name=self.customer.name,
            mission_title=self.mission.title, invoice_number=invoice.invoice_number or "(no number)",
            amount_due=amount, pay_url=self.pay_url, db=self._db))

    def send_operator(self, invoice, amount):
        from app.services.email_service import send_operator_overdue_email
        self._loop.run_until_complete(send_operator_overdue_email(
            invoice_number=invoice.invoice_number or "(no number)", amount_due=amount,
            customer_name=self.customer.name, customer_email=self.customer.email,
            mission_url=self.mission_url, db=self._db))


async def run_dunning_sweep(db, loop, *, now=None) -> dict:
    """Find billed, unpaid invoices and process the due dunning stage for each.
    `loop` is the running event loop used by DunningSender for the (async) email
    sends. Per-invoice errors are caught so one failure can't abort the sweep."""
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload
    from app.config import settings
    from app.models.invoice import Invoice
    from app.models.mission import Mission
    from app.models.customer import Customer

    now = now or datetime.utcnow()
    summary = {"reminders": 0, "finals": 0, "skipped_no_email": 0, "errors": 0}

    result = await db.execute(
        select(Invoice).where(Invoice.billed_at.is_not(None), Invoice.paid_in_full.is_(False))
        .options(selectinload(Invoice.line_items))
    )
    for invoice in result.scalars().all():
        try:
            stage = due_stage(invoice, now)
            if stage is None:
                continue
            mission = (await db.execute(select(Mission).where(Mission.id == invoice.mission_id))).scalar_one_or_none()
            if mission is None or getattr(mission.status, "value", mission.status) == "cancelled":
                continue
            customer = None
            if mission.customer_id:
                customer = (await db.execute(select(Customer).where(Customer.id == mission.customer_id))).scalar_one_or_none()
            if customer is None or not customer.email:
                # Can't email the customer; still alert the operator at the final stage.
                summary["skipped_no_email"] += 1
                if stage == STAGE_FINAL:
                    from app.services.email_service import send_operator_overdue_email
                    loop.run_until_complete(send_operator_overdue_email(
                        invoice_number=invoice.invoice_number or "(no number)",
                        amount_due=amount_due(invoice),
                        customer_name=(customer.name if customer else "(unknown)"),
                        customer_email=(customer.email if customer else None),
                        mission_url=f"{settings.frontend_url.rstrip('/')}/missions/{invoice.mission_id}", db=db))
                    invoice.final_notice_sent_at = now
                    await db.commit()
                continue
            pay_url = await _build_pay_url(db, invoice.mission_id)
            mission_url = f"{settings.frontend_url.rstrip('/')}/missions/{invoice.mission_id}"
            sender = DunningSender(db, loop, mission=mission, customer=customer, pay_url=pay_url, mission_url=mission_url)
            sent = process_invoice(invoice, now, sender, now_fn=lambda: now)
            await db.commit()
            if sent == STAGE_REMINDER:
                summary["reminders"] += 1
            elif sent == STAGE_FINAL:
                summary["finals"] += 1
        except Exception as exc:  # one bad invoice must not abort the sweep
            await db.rollback()
            summary["errors"] += 1
            logger.error("[DUNNING] invoice=%s failed: %s", getattr(invoice, "id", "?"), exc, exc_info=True)
    logger.info("[DUNNING] sweep complete: %s", summary)
    return summary
```

- [ ] **Step 2: Verify it imports**

Run: `cd backend && python3 -c "from app.services.dunning import run_dunning_sweep, DunningSender; print('OK')"`
Expected: `OK`.

- [ ] **Step 3: Run the full dunning test file (no regressions)**

Run: `cd backend && python3 -m pytest tests/test_dunning.py -q -p no:cacheprovider`
Expected: PASS (Task 2 + 3 tests still green).

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/dunning.py
git commit -m "feat(dunning): DunningSender + run_dunning_sweep async orchestration"
```

---

### Task 6: Celery task + daily beat schedule

**Files:**
- Modify: `backend/app/tasks/celery_tasks.py`

- [ ] **Step 1: Add the task**

In `backend/app/tasks/celery_tasks.py`, add a new task (mirroring `generate_report_task`'s event-loop pattern):

```python
@celery_app.task(name="send_payment_reminders")
def send_payment_reminders_task() -> dict:
    """Daily dunning sweep — see app/services/dunning.py."""
    from app.database import async_session
    from app.services.dunning import run_dunning_sweep

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        async def _run():
            async with async_session() as db:
                return await run_dunning_sweep(db, loop)
        return loop.run_until_complete(_run())
    finally:
        loop.close()
```

- [ ] **Step 2: Add the beat schedule entry**

In `backend/app/tasks/celery_tasks.py`, add to the `celery_app.conf.beat_schedule` dict:

```python
    # Payment reminders / dunning — daily at 16:00 UTC (~9am Pacific).
    "payment-reminders": {
        "task": "send_payment_reminders",
        "schedule": crontab(hour=16, minute=0),
    },
```

- [ ] **Step 3: Verify it imports**

Run: `cd backend && python3 -c "from app.tasks.celery_tasks import celery_app; assert 'payment-reminders' in celery_app.conf.beat_schedule; print('OK')"`
Expected: `OK`.

- [ ] **Step 4: Commit**

```bash
git add backend/app/tasks/celery_tasks.py
git commit -m "feat(dunning): daily Celery-beat task for payment reminders (16:00 UTC)"
```

---

### Task 7: Set `billed_at` on first client-link send

**Files:**
- Modify: `backend/app/routers/client_portal.py`

- [ ] **Step 1: Set billed_at when the portal link is first sent**

In `backend/app/routers/client_portal.py`, in the operator `POST /api/missions/{id}/client-link/send` endpoint (the one that logs `[CLIENT-LINK-SEND]` and emails the portal link), after the send succeeds and the mission's invoice is in scope, set the billed-at anchor once. Load the invoice and add:

```python
        # Anchor the dunning clock on the first time we send the customer the
        # invoice link (never overwrite — the 48h/7d reminders count from here).
        inv = (await db.execute(
            select(Invoice).where(Invoice.mission_id == mission_id)
        )).scalar_one_or_none()
        if inv is not None and inv.billed_at is None:
            from datetime import datetime as _dt
            inv.billed_at = _dt.utcnow()
            await db.commit()
```

(If `Invoice` / `select` aren't already imported in this module, add `from app.models.invoice import Invoice` and ensure `select` is imported from `sqlalchemy`.)

- [ ] **Step 2: Verify it imports**

Run: `cd backend && python3 -c "import app.routers.client_portal; print('OK')"`
Expected: `OK`.

- [ ] **Step 3: Run the full backend suite (no regressions)**

Run: `cd backend && python3 -m pytest -q -p no:cacheprovider`
Expected: all pass except the 2 known-pre-existing `test_health_stripe_db_lookup.py` failures.

- [ ] **Step 4: Commit**

```bash
git add backend/app/routers/client_portal.py
git commit -m "feat(dunning): set invoice.billed_at on first client-link send"
```

---

## Backfill note (one-time, at deploy)
Existing already-billed unpaid invoices (e.g. the Banks invoice) have `billed_at = NULL`, so they won't be dunned. If desired, backfill `billed_at` for currently-sent unpaid invoices from their mission "sent" time at deploy. Decide at execution time; default is "new invoices only" (safe — no surprise emails to past customers).

## Verification (post-implementation, before relying on it)
- `due_stage`/`process_invoice` unit tests green.
- Manually exercise the sweep against the live DB in a **rolled-back** transaction (construct an invoice with `billed_at` 49h ago, run `run_dunning_sweep` with a mocked/echo sender, confirm it selects it and would send the reminder) — do NOT send real emails to customers during verification; point `to_email` at a test inbox or stub `aiosmtplib.send`.
- Confirm the beat entry is registered in the running `beat` container.

---

## Self-review

**Spec coverage:** billed_at anchor (Task 7) ✓; 48h reminder (Tasks 2/3/4/5) ✓; 7d final + operator email to bill@ (Tasks 3/4/5) ✓; stop-on-paid (`due_stage`) ✓; no-email skip + operator-still-alerted (Task 5) ✓; idempotent stamps (Task 3) ✓; daily ~9am Pacific (Task 6) ✓; amount-due = portal next charge (Task 3) ✓; channel seam for SMS — `DunningSender` methods are the seam (add SMS there later) ✓; Phase-1 email only ✓.

**Placeholder scan:** none — every code step has full code; integration edits (main.py, client_portal.py) give the anchor + exact code.

**Type consistency:** `STAGE_REMINDER`/`STAGE_FINAL` constants used consistently; `due_stage` → `process_invoice` → `run_dunning_sweep` signatures match; `send_*` function kwargs match the `DunningSender` call sites; `amount_due` used in both process + sweep.
