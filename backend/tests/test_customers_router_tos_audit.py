"""v2.66.3 — Customers router populates latest_tos_audit pointer fields.

Hermetic tests around the helpers `_serialize_customer` and
`_latest_audits_by_customer` so the router contract stays locked
without requiring a real Postgres + asyncpg stack.

The DISTINCT-ON SQL itself is exercised by manual smoke against BOS-HQ
(operator validates after merge); these tests cover the in-process
serialization + dict-bucketing logic that turn the row stream into
the per-customer latest pointer the frontend reads.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest


def _now() -> datetime:
    return datetime(2026, 5, 3, 16, 31, tzinfo=timezone.utc)


def _mk_customer(*, name="Casey Operator", tos_signed=True):
    return SimpleNamespace(
        id=uuid.uuid4(),
        name=name,
        email="casey@example.com",
        phone=None,
        address=None,
        city=None,
        state=None,
        zip_code=None,
        company=None,
        notes=None,
        tos_signed=tos_signed,
        tos_signed_at=_now() if tos_signed else None,
        intake_completed_at=None,
        created_at=_now(),
        updated_at=_now(),
    )


def _mk_audit(*, customer_id, audit_id="DOC-AUDIT-001",
              template_version="DOC-001/TOS/REV3", accepted_at=None):
    return SimpleNamespace(
        customer_id=customer_id,
        audit_id=audit_id,
        signed_sha256="s" * 64,
        template_version=template_version,
        accepted_at=accepted_at or _now(),
    )


# ── _serialize_customer ───────────────────────────────────────────────

def test_serialize_customer_with_acroform_audit_includes_pointer():
    """AcroForm-flow customer: latest_tos_audit_* fields populated from
    the joined tos_acceptances row."""
    from app.routers.customers import _serialize_customer

    customer = _mk_customer()
    audit = _mk_audit(customer_id=customer.id)

    resp = _serialize_customer(customer, audit)

    assert resp.id == customer.id
    assert resp.tos_signed is True
    assert resp.latest_tos_audit_id == "DOC-AUDIT-001"
    assert resp.latest_tos_signed_sha == "s" * 64
    assert resp.latest_tos_template_version == "DOC-001/TOS/REV3"


def test_serialize_customer_legacy_no_audit_falls_back_to_legacy_columns():
    """Legacy canvas-signed customer: no audit row, so all three
    latest_* fields are null. The legacy ``tos_signed`` boolean +
    ``tos_signed_at`` continue to drive UI."""
    from app.routers.customers import _serialize_customer

    customer = _mk_customer()  # tos_signed=True, no audit
    resp = _serialize_customer(customer, None)

    assert resp.tos_signed is True
    assert resp.tos_signed_at is not None
    assert resp.latest_tos_audit_id is None
    assert resp.latest_tos_signed_sha is None
    assert resp.latest_tos_template_version is None


def test_serialize_customer_never_signed_all_tos_fields_null():
    """Cold customer who has not signed at all — every TOS-related
    field must be falsy/null."""
    from app.routers.customers import _serialize_customer

    customer = _mk_customer(tos_signed=False)
    resp = _serialize_customer(customer, None)

    assert resp.tos_signed is False
    assert resp.tos_signed_at is None
    assert resp.latest_tos_audit_id is None
    assert resp.latest_tos_signed_sha is None
    assert resp.latest_tos_template_version is None


# ── _latest_audits_by_customer ────────────────────────────────────────

class _FakeScalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return _FakeScalars(self._rows)


class _FakeDb:
    def __init__(self, rows):
        self._rows = rows
        self.executed = []

    async def execute(self, stmt):
        self.executed.append(stmt)
        return _FakeResult(self._rows)


@pytest.mark.asyncio
async def test_latest_audits_empty_customer_list_short_circuits():
    """Empty input list ⇒ no SQL emitted, returns {}."""
    from app.routers.customers import _latest_audits_by_customer

    db = _FakeDb(rows=[])
    out = await _latest_audits_by_customer(db, [])
    assert out == {}
    assert db.executed == []  # short-circuit


@pytest.mark.asyncio
async def test_latest_audits_buckets_one_row_per_customer():
    """Even if the SQL accidentally returns multiple rows per customer,
    the helper must bucket them so the dict has exactly one row per
    customer_id (the DISTINCT ON contract)."""
    from app.routers.customers import _latest_audits_by_customer

    cid_a, cid_b = uuid.uuid4(), uuid.uuid4()
    a_latest = _mk_audit(
        customer_id=cid_a, audit_id="A-LATEST", accepted_at=_now(),
    )
    b_latest = _mk_audit(
        customer_id=cid_b, audit_id="B-LATEST",
        accepted_at=_now() - timedelta(hours=1),
    )

    db = _FakeDb(rows=[a_latest, b_latest])
    out = await _latest_audits_by_customer(db, [cid_a, cid_b])

    assert set(out.keys()) == {cid_a, cid_b}
    assert out[cid_a].audit_id == "A-LATEST"
    assert out[cid_b].audit_id == "B-LATEST"


@pytest.mark.asyncio
async def test_latest_audits_skips_null_customer_id_rows():
    """Anonymous (cold-visit) acceptances have customer_id=NULL — they
    must NOT pollute the bucket dict. Defensive guard since the SELECT
    filters by IN (customer_ids) but a stale row could appear."""
    from app.routers.customers import _latest_audits_by_customer

    cid = uuid.uuid4()
    legit = _mk_audit(customer_id=cid)
    orphan = _mk_audit(customer_id=None, audit_id="ANON-AUDIT")

    db = _FakeDb(rows=[legit, orphan])
    out = await _latest_audits_by_customer(db, [cid])

    assert list(out.keys()) == [cid]
    assert out[cid].audit_id == legit.audit_id
