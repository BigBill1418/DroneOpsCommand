"""Persist the parser's ``details`` payload into the sidecar tables (ADR-0043).

Called from the import path inside the **existing best-effort savepoint
pattern** used for battery tracking. The contract is absolute: **a details
failure must never fail an import.** Extended log data is a nice-to-have;
the flight record is not. Every entry point here is written so the worst
outcome is a missing sidecar row and a WARN line.

The payload crosses a service boundary — it arrives as JSON over HTTP from
the Rust `flight-parser` container — so it is treated as untrusted input:
every value is coerced against the column it is destined for, integers are
range-checked against their SQL width, strings are truncated to their declared
length, and anything that will not coerce is dropped rather than written.
A parser that starts emitting a wrong-typed field degrades to NULL instead of
raising ``DataError`` mid-import.

Column mapping is derived from the model, not transcribed. A column added to
``FlightDetails`` is picked up automatically the moment the parser emits a key
with that name — which is what lets the Tier 1 record pass land its
``pack_*`` / ``pilot_*`` / ``aircraft_sn_full`` fields without touching this
module.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import Boolean, DateTime, Float, Integer, SmallInteger, String, delete
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.flight_details import FlightDetails, FlightSeries

logger = logging.getLogger("doc.flight_details")

# ── Bounds. These exist because the payload is remote input. ───────────
#: Widest observed log is ~13,870 frames; 200k leaves an order of magnitude of
#: headroom while still refusing something pathological.
MAX_SAMPLES_PER_SERIES = 200_000
#: Tier 0 emits 15 series, Tier 1 adds ~6. 64 is generous and finite.
MAX_SERIES_PER_FLIGHT = 64

_INT32_MIN, _INT32_MAX = -(2**31), 2**31 - 1
_INT16_MIN, _INT16_MAX = -(2**15), 2**15 - 1

#: Column name → (python type, SQL type) for everything writable on the row.
#: ``flight_id`` is set by the caller, never by the payload.
_DETAILS_COLUMNS = {
    c.name: c.type
    for c in FlightDetails.__table__.columns
    if c.name != "flight_id"
}

_SERIES_MAX_LEN = {
    "source": FlightSeries.__table__.c.source.type.length,
    "name": FlightSeries.__table__.c.name.type.length,
    "unit": FlightSeries.__table__.c.unit.type.length,
}


def _parse_dt(value: Any) -> datetime | None:
    """RFC3339 (what the parser emits) → naive UTC (what this repo stores).

    Storing an aware datetime in a naive column is the ADR-0017 class of bug;
    normalising here keeps every timestamp in the database on one convention.
    """
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str) and value:
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _coerce(value: Any, sql_type: Any, column: str) -> Any:
    """Coerce one payload value to its column's type, or ``None``.

    Returning ``None`` for anything questionable is deliberate: a NULL is
    honest about not knowing, whereas a coerced-anyway value would be a
    number the aircraft never reported.
    """
    if value is None:
        return None
    try:
        if isinstance(sql_type, DateTime):
            return _parse_dt(value)
        if isinstance(sql_type, Boolean):
            return bool(value)
        if isinstance(sql_type, SmallInteger):
            n = int(value)
            return n if _INT16_MIN <= n <= _INT16_MAX else None
        if isinstance(sql_type, Integer):
            n = int(value)
            return n if _INT32_MIN <= n <= _INT32_MAX else None
        if isinstance(sql_type, Float):
            f = float(value)
            # NaN / inf have no SQL representation and no meaning as a reading.
            return f if f == f and abs(f) != float("inf") else None
        if isinstance(sql_type, String):
            s = str(value)
            limit = getattr(sql_type, "length", None)
            return s[:limit] if limit else s
        if isinstance(sql_type, JSONB):
            return value if isinstance(value, (dict, list)) else None
    except (TypeError, ValueError, OverflowError):
        logger.debug("details: dropping uncoercible value for %s: %r", column, value)
        return None
    return value


def build_details_values(payload: dict) -> dict:
    """Payload → a dict of column values for ``flight_details``.

    Keys the model does not have are ignored (a newer parser talking to an
    older backend must not raise), and keys the model has but the payload
    omits are simply absent (they stay NULL).
    """
    values: dict[str, Any] = {}
    for column, sql_type in _DETAILS_COLUMNS.items():
        if column not in payload:
            continue
        values[column] = _coerce(payload[column], sql_type, column)

    values["schema_version"] = values.get("schema_version") or 1
    values["generated_at"] = datetime.utcnow()
    return values


def build_series_rows(flight_id: UUID, payload: dict) -> list[dict]:
    """Payload → ``flight_series`` row dicts, bounded and validated.

    ``sample_count`` is recomputed from the values actually stored rather than
    trusted from the payload — a count that disagrees with the array would
    make the read path's alignment guarantee a lie.
    """
    blocks = payload.get("series")
    if not isinstance(blocks, list):
        return []

    rows: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for block in blocks[:MAX_SERIES_PER_FLIGHT]:
        if not isinstance(block, dict):
            continue
        source = block.get("source")
        name = block.get("name")
        values = block.get("values")
        if not isinstance(source, str) or not isinstance(name, str):
            continue
        if not isinstance(values, list):
            continue
        source = source[: _SERIES_MAX_LEN["source"]]
        name = name[: _SERIES_MAX_LEN["name"]]
        # The table's PK is (flight_id, source, name); a duplicate in the
        # payload would raise on insert, so the first one wins.
        if (source, name) in seen:
            logger.warning(
                "details: duplicate series %s/%s in payload for flight %s — keeping the first",
                source, name, flight_id,
            )
            continue
        if len(values) > MAX_SAMPLES_PER_SERIES:
            logger.warning(
                "details: series %s/%s has %d samples (> %d) for flight %s — skipped",
                source, name, len(values), MAX_SAMPLES_PER_SERIES, flight_id,
            )
            continue
        seen.add((source, name))

        unit = block.get("unit")
        if isinstance(unit, str):
            unit = unit[: _SERIES_MAX_LEN["unit"]]
        else:
            unit = None
        precision = block.get("precision_dp")
        precision = _coerce(precision, SmallInteger(), "precision_dp")

        rows.append({
            "flight_id": flight_id,
            "source": source,
            "name": name,
            "unit": unit,
            "sample_count": len(values),
            "precision_dp": precision,
            "values": values,
        })
    return rows


async def persist_flight_details(
    db: AsyncSession, flight_id: UUID, payload: Any
) -> tuple[bool, int]:
    """Write one flight's details + series. Returns ``(wrote_details, n_series)``.

    **Replaces wholesale, never merges.** The series for a flight are deleted
    and re-inserted, so a re-parse that produces fewer series cannot leave
    stale rows from a previous parser version index-misaligned against the new
    time base.

    The caller is responsible for the savepoint. This function does not commit.
    """
    if not isinstance(payload, dict):
        return False, 0

    values = build_details_values(payload)
    existing = await db.get(FlightDetails, flight_id)
    if existing is None:
        db.add(FlightDetails(flight_id=flight_id, **values))
    else:
        for key, value in values.items():
            setattr(existing, key, value)

    await db.execute(delete(FlightSeries).where(FlightSeries.flight_id == flight_id))
    rows = build_series_rows(flight_id, payload)
    for row in rows:
        db.add(FlightSeries(**row))

    await db.flush()
    return True, len(rows)
