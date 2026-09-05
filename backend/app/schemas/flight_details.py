"""Response schemas for the Flight Details read surface (ADR-0043 §4).

``GET /{flight_id}/details`` **never 404s on a missing details row.** Per
operator decision D3 the Flight Details link renders on every flight
regardless of source, so "this flight has no extended data" is a normal
response with a machine-readable ``unavailable_reason`` — not an error. A 404
is reserved for a flight that genuinely does not exist.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel

#: Why a flight has no extended data. ``None`` means details are present.
#:
#: * ``source_unsupported``   — litchi / airdata / manual: the format simply
#:   does not carry this data, and no amount of re-processing will change it.
#: * ``not_backfilled``       — a DJI flight whose row has not been written
#:   yet. Actionable: re-process it.
#: * ``odl_import_no_original`` — an OpenDroneLog-era row that never went
#:   through the Rust parser and has no original file to re-read.
UnavailableReason = Literal[
    "source_unsupported",
    "not_backfilled",
    "odl_import_no_original",
]


class FlightSeriesIndexEntry(BaseModel):
    """One available series, WITHOUT its values.

    The index exists so the page can render per-section availability in a
    single round trip. Including ``values`` here would defeat the entire
    reason the series live in their own table (plan §1.5).
    """

    source: str
    name: str
    unit: str | None = None
    sample_count: int
    precision_dp: int | None = None

    model_config = {"from_attributes": True}


class FlightDetailsResponse(BaseModel):
    flight_id: UUID
    source: str
    details: dict | None = None
    series_index: list[FlightSeriesIndexEntry] = []
    unavailable_reason: UnavailableReason | None = None


class FlightSeriesResponse(BaseModel):
    """Requested series plus the time base they are aligned to.

    ``t_offset_s`` for the same ``source`` is always returned alongside at the
    SAME sample indices, so the caller never has to align anything itself.
    ``max_points`` is the downsample target actually applied.
    """

    flight_id: UUID
    source: str
    max_points: int
    sample_count: int
    returned_points: int
    series: dict[str, list]
    units: dict[str, str | None] = {}
    missing: list[str] = []


class FlightDetailsStatus(BaseModel):
    """Coverage counters for the Settings → Flight Data card.

    Every field is a ``COUNT(*)`` / ``GROUP BY``; no JSON column is ever
    loaded. Mirrors ``/reprocess/status``'s shape so the operator reads the
    two cards the same way.
    """

    total: int
    by_source: dict[str, int]
    with_details: int
    without_details: int
    with_stored_file: int
    parser_versions: dict[str, int]
    crate_versions: dict[str, int]
    restamped: int
    model_repaired: int


#: Sources that structurally cannot carry extended log data. A Litchi or
#: Airdata CSV is a flat table of samples; a manual flight has no log at all.
#: Re-processing one of these can never produce a details row, so the UI must
#: say "not available for this source" rather than offering a re-process.
DETAILS_UNSUPPORTED_SOURCES: frozenset[str] = frozenset(
    {"litchi_csv", "airdata_csv", "manual"}
)


def details_unavailable_reason(
    source: str | None, has_details_row: bool
) -> UnavailableReason | None:
    """Resolve why a flight shows no extended data — or ``None`` if it does.

    Pure and total: every source value maps to exactly one outcome, including
    sources this build has never heard of (they fall to
    ``source_unsupported``, the honest answer for "we cannot produce this").

    Note the ordering: a present details row wins over every source rule. An
    ``opendronelog_import`` flight that has been re-imported from a recovered
    original therefore reports ``None`` and renders its data, which is what
    makes that recovery visible in the UI at all.
    """
    if has_details_row:
        return None
    if source == "opendronelog_import":
        return "odl_import_no_original"
    if source == "dji_txt":
        return "not_backfilled"
    return "source_unsupported"
