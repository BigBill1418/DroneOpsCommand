"""ADR-0011 §2 v2.66.0 — sequential invoice numbering.

`_next_invoice_number(db)` issues a `BARNARDHQ-YYYY-NNNN` string
backed by an atomic UPSERT on `system_settings.value`. Year prefix
resets every Jan 1 by virtue of using a per-year counter key.

These tests exercise the formatting + the counter-key shape; they
don't require a real PG connection. The atomic SQL is exercised by
the integration smoke test (operator runs `pytest -k integration`
against a live DB).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


class _Row:
    def __init__(self, value):
        self._value = value

    def fetchone(self):
        return (str(self._value),)


class _SeqDB:
    """Async DB stub: pretends each INSERT … RETURNING returns the next
    integer value the test scenario expects."""

    def __init__(self, returning_values):
        self._values = list(returning_values)
        self.queries = []

    async def execute(self, sql, params=None):
        self.queries.append((str(sql), params or {}))
        if not self._values:
            raise AssertionError("ran out of canned values")
        return _Row(self._values.pop(0))


@pytest.mark.asyncio
async def test_next_invoice_number_format_4_digit_zero_padded():
    from app.routers.invoices import _next_invoice_number

    db = _SeqDB(returning_values=[1])
    number = await _next_invoice_number(db)

    # Format: BARNARDHQ-YYYY-NNNN
    parts = number.split("-")
    assert parts[0] == "BARNARDHQ"
    assert parts[1].isdigit() and len(parts[1]) == 4
    assert parts[2] == "0001"


@pytest.mark.asyncio
async def test_next_invoice_number_high_counter_pads_correctly():
    from app.routers.invoices import _next_invoice_number

    db = _SeqDB(returning_values=[42])
    number = await _next_invoice_number(db)
    assert number.endswith("-0042")

    db2 = _SeqDB(returning_values=[10000])
    number2 = await _next_invoice_number(db2)
    # 10000 exceeds 4 digits — should not crash; numbers >9999 widen.
    assert number2.endswith("-10000")


@pytest.mark.asyncio
async def test_next_invoice_number_is_atomic_upsert():
    """The atomic primitive must be ON CONFLICT DO UPDATE … RETURNING
    so concurrent calls can't collide on a number."""
    from app.routers.invoices import _next_invoice_number

    db = _SeqDB(returning_values=[1])
    await _next_invoice_number(db)
    sql, params = db.queries[0]
    assert "ON CONFLICT" in sql
    assert "RETURNING" in sql
    assert params["k"].startswith("invoice_number_counter_")


@pytest.mark.asyncio
async def test_next_invoice_number_uses_per_year_counter_key():
    """The counter key embeds the year so Jan 1 naturally resets."""
    from app.routers.invoices import _next_invoice_number

    db = _SeqDB(returning_values=[1])
    await _next_invoice_number(db)
    _, params = db.queries[0]
    # Key includes the current year — Jan 1 of next year creates a
    # brand new row.
    from datetime import datetime
    assert params["k"].endswith(str(datetime.utcnow().year))
