"""Flight library — native flight management with upload, CRUD, and export."""

import asyncio
import hashlib
import json
import logging
import math
import os
import shutil
import tempfile
import time
import traceback
from collections import Counter, defaultdict
from datetime import datetime as _dt
from pathlib import Path
from uuid import UUID, uuid4

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import select, func, desc
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import defer
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.device import validate_device_api_key
from app.auth.jwt import get_current_user
from app.config import settings
from app.database import get_db, async_session
from app.models.aircraft import Aircraft
from app.models.battery import Battery, BatteryLog
from app.models.device_api_key import DeviceApiKey
from app.models.flight import Flight
from app.models.flight_details import FlightDetails, FlightSeries
from app.models.system_settings import SystemSetting
from app.models.user import User
from app.schemas.flight_details import (
    FlightDetailsResponse,
    FlightSeriesIndexEntry,
    FlightDetailsStatus,
    FlightSeriesResponse,
    details_unavailable_reason,
)
from app.schemas.flight import (
    FlightCreate, FlightDetailResponse, FlightResponse, FlightUpdate, FlightUploadResponse,
)
from app.services.flight_metrics import sanitize_odl_distance
from app.services.ntfy import send_alert
from app.services.telemetry_downsample import downsample, select_indices
from app.utils.timezone import iso_utc, local_date_compact

logger = logging.getLogger("doc.flights")

router = APIRouter(prefix="/api/flight-library", tags=["flight-library"])

# Heavy per-flight JSON columns that the list view never returns. Deferred in
# list_flights so a 500-row page doesn't materialize ~19k GPS points + a full
# telemetry time-series per row into >1 GB of Python objects (ADR-0019).
# Kept as a named constant so the regression test asserts against the same
# source of truth the endpoint uses.
LIST_DEFERRED_COLUMNS = (Flight.gps_track, Flight.telemetry, Flight.raw_metadata)


_DATE_FORMATS = (
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y-%m-%dT%H:%M:%SZ",
    "%Y-%m-%dT%H:%M:%S.%fZ",
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y-%m-%d",
    "%m/%d/%Y %H:%M:%S",
    "%m/%d/%Y",
    "%d/%m/%Y %H:%M:%S",
    "%d/%m/%Y",
)


def _parse_datetime(value: object) -> _dt | None:
    """Parse a datetime from string, numeric epoch, or datetime object.

    Handles ISO 8601 (OpenDroneLog's format), epoch timestamps, and common
    date strings.  Returns a naive UTC datetime suitable for DB storage.
    """
    if value is None or value == "":
        return None
    if isinstance(value, _dt):
        # L5 (ADR-0028): the flights.start_time column is naive (UTC). A
        # tz-aware datetime (e.g. a manual entry parsed from ISO-with-offset)
        # must be converted to naive UTC, not stored verbatim with its offset.
        if value.tzinfo is not None:
            from datetime import timezone
            return value.astimezone(timezone.utc).replace(tzinfo=None)
        return value
    # Numeric epoch (seconds since 1970)
    if isinstance(value, (int, float)):
        try:
            if value > 1e12:
                return _dt.utcfromtimestamp(value / 1000)
            return _dt.utcfromtimestamp(value)
        except (OSError, OverflowError, ValueError):
            return None
    if isinstance(value, str):
        cleaned = value.strip()
        if not cleaned:
            return None
        # Primary: use fromisoformat — handles all ISO 8601 variants that ODL produces
        # e.g. "2024-01-15T14:30:00", "2024-01-15T14:30:00Z", "2024-01-15T14:30:00+00:00"
        # L4 (ADR-0028): ISO/date parsing runs BEFORE the numeric-epoch branch.
        # The old order ran float() first, so a bare year like "2024" parsed as
        # epoch 2024 → 1970-01-01T00:33:44 instead of failing cleanly.
        try:
            # Python 3.11+ fromisoformat handles trailing Z natively; for 3.10 compat
            # we also replace trailing Z with +00:00
            iso_str = cleaned
            if iso_str.endswith("Z"):
                iso_str = iso_str[:-1] + "+00:00"
            dt = _dt.fromisoformat(iso_str)
            # Strip timezone info to store as naive UTC
            if dt.tzinfo is not None:
                from datetime import timezone
                dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
            return dt
        except (ValueError, TypeError):
            pass
        # Explicit format strings for non-ISO date formats.
        for fmt in _DATE_FORMATS:
            try:
                return _dt.strptime(cleaned, fmt)
            except ValueError:
                continue
        # Last resort: a *pure* numeric string that looks like a real epoch.
        # Guarded by magnitude so calendar-ish tokens ("2024") are NOT coerced
        # into a 1970 timestamp — seconds must be ≥ 1e8 (≈ 1973), millis > 1e12.
        try:
            num = float(cleaned)
            if num > 1e12:
                return _dt.utcfromtimestamp(num / 1000)
            if num >= 1e8:
                return _dt.utcfromtimestamp(num)
        except (ValueError, OSError, OverflowError):
            pass
        logger.warning("Could not parse date: %r", value)
    return None

from app.models.system_settings import SystemSetting

PARSER_URL = "http://flight-parser:8100"

# ── Original file storage ─────────────────────────────────────────────
_FLIGHT_LOGS_DIR = Path(settings.upload_dir) / "flight_logs"


def _get_stored_file_path(file_hash: str) -> Path | None:
    """Find a stored original file by its hash (any extension).

    Original logs are persisted as ``{file_hash}{ext}`` where ``ext`` is the
    upload's single-component suffix (or ``.bin``). A filesystem glob on the
    hash stem resolves the one matching file directly — the directory index
    does the lookup — instead of an O(N) ``iterdir()`` scan that listed every
    stored log to find one hash (audit P3-5c). ``glob('<hash>.*')`` covers all
    single-extension names; the bare ``<hash>`` (extensionless) form is checked
    separately so the result is identical to the old ``f.stem == file_hash``
    predicate for every name the store can produce.
    """
    if not _FLIGHT_LOGS_DIR.exists():
        return None
    for match in _FLIGHT_LOGS_DIR.glob(f"{file_hash}.*"):
        return match
    bare = _FLIGHT_LOGS_DIR / file_hash
    if bare.exists():
        return bare
    return None


def _stream_spool_to_temp(src, dest_path: str) -> tuple[str, int]:
    """Stream a spooled multipart upload to *dest_path* in 1 MiB chunks,
    hashing incrementally. Runs in a worker thread. Returns (sha256, size).

    The multipart spool threshold is 4 MB, so ``src`` (``UploadFile.file``)
    is a disk-backed temp file for any real flight log — the upload is never
    materialized as a single ``bytes`` object. Replaces the old
    ``await upload.read()`` whole-dump-in-RAM path that is the same OOM class
    that killed image uploads (missions.py v2.68.7) and backup uploads
    (backup.py ``_save_upload_to_disk``) — flight-log upload is the operator's
    primary field workflow (audit 2026-06-11, finding #9).
    """
    sha256 = hashlib.sha256()
    size = 0
    src.seek(0)
    with open(dest_path, "wb") as out:
        while chunk := src.read(1024 * 1024):
            sha256.update(chunk)
            out.write(chunk)
            size += len(chunk)
    return sha256.hexdigest(), size


def _store_original_from_path(file_hash: str, src_path: str, filename: str) -> None:
    """Copy an already-spooled flight log into the original-file store.

    Persists to the hash-named destination (``{file_hash}{ext}``) idempotently
    ("write only if absent") with fail-soft logging, copying from the temp
    spool file instead of holding the whole upload in a ``bytes`` object.
    """
    try:
        _FLIGHT_LOGS_DIR.mkdir(parents=True, exist_ok=True)
        ext = Path(filename).suffix.lower() if filename else ".bin"
        dest = _FLIGHT_LOGS_DIR / f"{file_hash}{ext}"
        if not dest.exists():
            shutil.copyfile(src_path, dest)
            logger.debug("Saved original flight log: %s", dest.name)
    except Exception as e:
        logger.warning("Failed to save original flight log %s: %s", file_hash, e)


class _SpooledUpload:
    """A flight-log upload streamed to a disk temp file with its hash known.

    Replaces the old ``content = await upload.read()`` whole-dump-in-RAM step.
    The original file is persisted to the hash-named store on construction
    (idempotent "write only if absent"). :meth:`parse`
    POSTs the temp file to the flight-parser service, streaming it (never a
    whole-file ``bytes`` in this process). :meth:`close` removes the temp file.

    Used as an async context manager so each caller's ordering — hash, then
    its own duplicate check, then (only if needed) parse — is preserved
    exactly, just as the three inline handlers did.
    """

    def __init__(self, tmp_path: str, file_hash: str, size_bytes: int, filename: str | None) -> None:
        self.tmp_path = tmp_path
        self.file_hash = file_hash
        self.size_bytes = size_bytes
        self.filename = filename

    async def parse(self, parser_headers: dict) -> tuple[int, dict | None]:
        """POST the spooled file to the parser. Returns ``(status_code, data)``;
        ``data`` is ``None`` on a non-200 status (caller appends the same
        ``"<name>: parser returned <code>"`` error). Raises ``httpx.ConnectError``
        and JSON-decode errors exactly as the inline code did."""
        async with httpx.AsyncClient(timeout=120) as client:
            with open(self.tmp_path, "rb") as parse_src:
                resp = await client.post(
                    f"{PARSER_URL}/parse",
                    files={"file": (self.filename or "upload.txt", parse_src)},
                    headers=parser_headers,
                )
        if resp.status_code != 200:
            return resp.status_code, None
        return resp.status_code, resp.json()

    def close(self) -> None:
        try:
            os.unlink(self.tmp_path)
        except OSError:
            pass

    async def __aenter__(self) -> "_SpooledUpload":
        return self

    async def __aexit__(self, *exc) -> None:
        self.close()


async def _spool_upload(upload: UploadFile) -> _SpooledUpload:
    """Stream a disk-spooled multipart upload to a temp file (incremental
    SHA-256, in a worker thread) and persist the original to the hash-named
    store — the OOM-safe replacement for ``await upload.read()`` (audit #9).

    The multipart spool threshold is 4 MB, so for any real flight log the
    upload is already a disk-backed temp file; this never materializes it as
    a single ``bytes`` object.
    """
    loop = asyncio.get_running_loop()
    fd, tmp_path = tempfile.mkstemp(prefix="flight_upload_")
    os.close(fd)
    try:
        file_hash, size_bytes = await loop.run_in_executor(
            None, _stream_spool_to_temp, upload.file, tmp_path
        )
        await loop.run_in_executor(
            None, _store_original_from_path, file_hash, tmp_path,
            upload.filename or "upload.bin",
        )
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    return _SpooledUpload(tmp_path, file_hash, size_bytes, upload.filename)


# Plausible DJI frame-cadence band (Hz). DJI airframes log telemetry between
# ~5 Hz (FPV) and ~15 Hz (Mavic 4 Pro). A resolved point_count/duration outside
# a generous band signals a mis-parsed duration (ADR-0027, e.g. the hard-coded
# /10 divisor that this release removes). Generous bounds → flag only the clearly
# wrong, never a legitimate edge case.
_INGEST_HZ_MIN = 0.2
# M2 (ADR-0028): DJI airframes log telemetry at ~5–15 Hz. The old 60 Hz ceiling
# never tripped on the real failure modes (a 15→30 Hz doubling, or a frames/10
# inflation). 25 Hz keeps generous margin above the fastest real cadence while
# actually catching a doubled/garbled rate.
_INGEST_HZ_MAX = 25.0


async def _check_ingest_anomalies(db: AsyncSession, flight: Flight) -> None:
    """Defense-in-depth ingest validation (ADR-0027) — *flag, never reject*.

    DJI logs are authoritative, so a suspicious flight is still imported; we
    surface the anomaly so an operator can review rather than silently trusting
    a possibly mis-parsed duration. Two checks:

    1. **Cadence sanity** — ``point_count / duration_secs`` must resolve to a
       plausible Hz. A wildly off rate is the fingerprint of a duration parsed
       with the wrong sample-rate assumption (the bug this release fixes).
    2. **Single-airframe interval overlap** — no two flights of the same
       physical airframe can be airborne at once. Overlapping
       ``[start, start+duration)`` intervals for one serial mean at least one
       flight's duration (or start) is wrong. Had this existed, the inflated
       Savannah Mavic durations would have been caught at upload.

    Best-effort: any failure here is logged and swallowed so it can never roll
    back or block a flight import.
    """
    try:
        dur = float(flight.duration_secs or 0.0)
        pts = int(flight.point_count or 0)
        # M2 (ADR-0028): prefer the parser's true frame_count over point_count
        # for the cadence sanity check — point_count excludes GPS-filtered
        # points, so a GPS-denied flight reads as falsely low Hz. frame_count is
        # the raw sample count and is the honest cadence numerator.
        raw_meta = getattr(flight, "raw_metadata", None)
        meta = raw_meta if isinstance(raw_meta, dict) else {}
        try:
            frame_count = int(meta.get("frame_count") or 0)
        except (TypeError, ValueError):
            frame_count = 0
        cadence_n = frame_count if frame_count > 0 else pts
        cadence_src = "frame_count" if frame_count > 0 else "point_count"
        if dur > 1.0 and cadence_n > 0:
            hz = cadence_n / dur
            if hz < _INGEST_HZ_MIN or hz > _INGEST_HZ_MAX:
                logger.warning(
                    "ingest-guard: flight %s (%s) implausible cadence %.2f Hz "
                    "(%s=%d / duration=%.1fs) — possible duration mis-parse",
                    flight.id, flight.name, hz, cadence_src, cadence_n, dur,
                )

        if not flight.drone_serial or not flight.start_time or dur <= 0:
            return

        from datetime import timedelta
        new_start = flight.start_time
        new_end = new_start + timedelta(seconds=dur)

        rows = await db.execute(
            select(Flight.id, Flight.name, Flight.start_time, Flight.duration_secs).where(
                Flight.drone_serial == flight.drone_serial,
                Flight.id != flight.id,
                Flight.start_time.isnot(None),
            )
        )
        overlaps = []
        for oid, oname, ostart, odur in rows.all():
            if ostart is None or not odur or odur <= 0:
                continue
            oend = ostart + timedelta(seconds=float(odur))
            # Half-open intervals overlap iff each starts before the other ends.
            if new_start < oend and ostart < new_end:
                overlaps.append((oid, oname, ostart, float(odur)))

        if not overlaps:
            return

        first = overlaps[0]
        logger.warning(
            "ingest-guard: flight %s (%s) serial=%s [%s +%.0fs] overlaps %d existing "
            "flight(s) for the same airframe — first: %s (%s +%.0fs). A single airframe "
            "cannot be airborne twice; a duration is likely mis-parsed.",
            flight.id, flight.name, flight.drone_serial, iso_utc(new_start), dur,
            len(overlaps), first[1], iso_utc(first[2]), first[3],
        )
        # Page operator (ADR-0036/0037 high). Dedup per airframe+local-day so a
        # bulk import of a contaminated set fires once, not once per flight.
        await send_alert(
            f"[DroneOps] Flight overlap on {flight.drone_serial}",
            (
                f"Airframe {flight.drone_serial}: flight '{flight.name}' "
                f"({iso_utc(new_start)}, {dur:.0f}s) overlaps {len(overlaps)} other "
                f"flight(s) of the same airframe — e.g. '{first[1]}'. A single airframe "
                f"cannot fly two overlapping intervals; review for a mis-parsed duration."
            ),
            priority=1,
            topic="droneops-flight-overlap",
            tags=("warning", "flight"),
            dedup_key=f"flight-overlap:{flight.drone_serial}:{local_date_compact(new_start)}",
            dedup_ttl_seconds=3600,
        )
    except Exception as exc:  # never let the guard break an import
        logger.warning("ingest-guard check failed for flight %s: %s",
                       getattr(flight, "id", "?"), exc)


def _parsed_is_viable(parsed: dict, parsed_start) -> bool:
    """Minimal-viability gate (ADR-0028 M6).

    A parse that yields no start_time, no points and no duration is junk —
    persisting it creates a ``start_time=NULL`` ghost row that pollutes the
    library and report aggregates. Require at least ONE real signal.
    """
    try:
        pts = int(parsed.get("point_count", 0) or 0)
    except (TypeError, ValueError):
        pts = 0
    try:
        dur = float(parsed.get("duration_secs", 0) or 0)
    except (TypeError, ValueError):
        dur = 0.0
    return parsed_start is not None or pts > 0 or dur > 0.0


# Bounded retries when a concurrent import grabs the same auto-generated name
# before our flush (ADR-0028 H3). Each retry regenerates the name (the rank +
# conflict-bump now sees the just-committed sibling) inside a fresh savepoint.
_AUTONAME_MAX_RETRIES = 5


async def _build_flight_from_parsed(
    db: AsyncSession, parsed: dict, file_hash: str, upload_filename: str | None,
    *, log_prefix: str = "Imported",
) -> Flight | None:
    """Construct, persist, and battery-track a new :class:`Flight` from a
    parser-emitted dict. Returns the persisted ``Flight``, or ``None`` when the
    flight was skipped (non-viable parse, or a duplicate ``source_file_hash``).

    The single builder used by ``/upload``, ``/device-upload`` and
    ``/reprocess``. It is now fully self-contained and NEVER leaves the session
    in a failed state nor propagates ``IntegrityError`` (ADR-0028 C2/H3/H4):

    * **M6 viability gate** — a parse with no start_time, points or duration is
      rejected (returns ``None``) instead of creating a junk row.
    * **H4 dedup** — a pre-check on ``source_file_hash`` plus the partial unique
      index (migration 0006) makes the dedup race-safe; a losing racer's
      ``IntegrityError`` is caught and reported as a skip (``None``).
    * **H3 autoname race** — the flush runs inside a SAVEPOINT; an autoname
      unique violation rolls back the savepoint, regenerates the name and
      retries (bounded), so two concurrent imports never wedge the batch.
    """
    ph = parsed.get("file_hash", file_hash)
    parsed_start = _parse_datetime(parsed.get("start_time"))

    # M6 — reject non-viable parses before doing any work.
    if not _parsed_is_viable(parsed, parsed_start):
        logger.warning(
            "%s: skipping non-viable parse (no start_time/points/duration) hash=%s file=%s",
            log_prefix, ph, parsed.get("original_filename", upload_filename),
        )
        return None

    # H4 — fast-path dedup; the unique index is the actual guarantee.
    dup = await db.execute(select(Flight.id).where(Flight.source_file_hash == ph))
    if dup.scalar_one_or_none() is not None:
        logger.info("%s: duplicate source_file_hash %s — skipped", log_prefix, ph)
        return None

    fleet_match = await _match_fleet_aircraft(
        db, parsed.get("drone_serial"), parsed.get("drone_model")
    )
    if fleet_match:
        logger.info("Auto-matched flight to fleet aircraft: %s (serial=%s)",
                    fleet_match.model_name, fleet_match.serial_number)

    flight: Flight | None = None
    for attempt in range(_AUTONAME_MAX_RETRIES):
        flight_name = await _generate_flight_name(
            db, parsed.get("drone_model"), fleet_match, parsed_start
        )
        candidate = Flight(
            name=flight_name,
            drone_model=fleet_match.model_name if fleet_match else parsed.get("drone_model"),
            drone_serial=parsed.get("drone_serial"),
            battery_serial=parsed.get("battery_serial"),
            start_time=parsed_start,
            duration_secs=parsed.get("duration_secs", 0),
            total_distance=parsed.get("total_distance", 0),
            max_altitude=parsed.get("max_altitude", 0),
            max_speed=parsed.get("max_speed", 0),
            home_lat=parsed.get("home_lat"),
            home_lon=parsed.get("home_lon"),
            point_count=parsed.get("point_count", 0),
            gps_track=parsed.get("gps_track"),
            telemetry=parsed.get("telemetry"),
            raw_metadata=parsed.get("raw_metadata"),
            source=parsed.get("source", "dji_txt"),
            source_file_hash=ph,
            original_filename=parsed.get("original_filename", upload_filename),
            aircraft_id=fleet_match.id if fleet_match else None,
        )
        try:
            async with db.begin_nested():
                db.add(candidate)
                await db.flush()
            flight = candidate
            break
        except IntegrityError as exc:
            msg = str(getattr(exc, "orig", exc))
            if "uq_flights_source_file_hash" in msg or "source_file_hash" in msg:
                # Lost a dedup race — another import committed this hash first.
                logger.info("%s: duplicate source_file_hash %s (race) — skipped",
                            log_prefix, ph)
                return None
            if "uq_flights_autoname" in msg and attempt < _AUTONAME_MAX_RETRIES - 1:
                logger.info("%s: autoname collision on '%s' (attempt %d) — regenerating",
                            log_prefix, flight_name, attempt + 1)
                continue
            # Unknown integrity error, or exhausted name retries — re-raise so the
            # caller's per-file savepoint records it without poisoning the batch.
            raise

    if flight is None:
        return None

    await db.refresh(flight)
    logger.info("%s new flight %s (%s) aircraft=%s", log_prefix, flight.id,
                flight.name, fleet_match.model_name if fleet_match else "unmatched")

    # Defense-in-depth ingest validation (ADR-0027/0028) — flags implausible
    # cadence and single-airframe time overlaps. Never rejects; never rolls back.
    await _check_ingest_anomalies(db, flight)

    # Auto-track battery — best-effort via savepoint so failures
    # don't rollback the flight import
    battery_data = parsed.get("battery_data")
    if battery_data and battery_data.get("serial"):
        try:
            async with db.begin_nested():
                await _track_battery(db, flight, battery_data)
        except Exception as bat_exc:
            logger.warning("Battery tracking failed for flight %s: %s", flight.id, bat_exc)

    return flight


import re as _re


_DJI_ALIASES: dict[str, str] = {
    # DJI uses short model codes in flight logs that differ from marketing names.
    # Map normalized short codes → normalized canonical names.
    "m30": "matrice30",
    "m30t": "matrice30t",
    "m300": "matrice300",
    "m300rtk": "matrice300rtk",
    "m350": "matrice350",
    "m350rtk": "matrice350rtk",
    "m4td": "matrice4td",
    "m4e": "matrice4e",
    "m4t": "matrice4t",
    "m3d": "mavic3d",           # Mavic 3 (D = standard downward cam)
    "m3e": "mavic3enterprise",
    "m3m": "mavic3multispectral",
    "m3t": "mavic3thermal",
    "m3pro": "mavic3pro",
    "mini4pro": "mini4pro",
    "mini3pro": "mini3pro",
    "mini5pro": "mini5pro",
}


def _normalize_model(name: str) -> str:
    """Normalize a drone model name for fuzzy comparison.

    Strips 'DJI', spaces, hyphens, underscores → lowercase.
    Removes redundant 'M' prefix after 'MATRICE' (e.g. 'Matrice M30T' → 'matrice30t').
    Then resolves known DJI short codes to canonical names.

    'DJI Mavic 3 Pro' → 'mavic3pro'
    'Mavic3Pro'       → 'mavic3pro'
    'DJI Matrice 30T' → 'matrice30t'
    'Matrice30'       → 'matrice30'
    'M30T'            → 'matrice30t'
    'M4TD'            → 'matrice4td'
    'Matrice M30T'    → 'matrice30t'
    """
    s = name.upper().replace("DJI", "").strip()
    s = _re.sub(r'[\s\-_]+', '', s)
    s = s.lower()
    # Fix "Matrice M30T" → "matrice30t" (redundant M after matrice)
    s = _re.sub(r'^matricem(\d)', r'matrice\1', s)
    # Resolve known DJI short codes
    return _DJI_ALIASES.get(s, s)


async def _match_fleet_aircraft(db: AsyncSession, drone_serial: str | None, drone_model: str | None) -> Aircraft | None:
    """Match a parsed flight to a fleet aircraft.

    Priority (strict — v2.63.14, ADR-0007):
      1. Exact serial number match (case-insensitive). If serial is present,
         this is the only acceptable identity — we never silently fall back
         to model matching when a serial is provided but doesn't match a
         fleet record (that's the bug ADR-0007 closes).
      2. Exact normalized model match — only when the fleet has exactly ONE
         aircraft of that model. If two or more share the model, attribution
         is ambiguous without a serial → return None.

    Prefix/substring fuzzy matching has been removed: it caused every
    incoming flight whose parsed model shared any prefix with a fleet
    record (e.g. "Mavic 3" → "DJI Mavic 3 Pro") to be attributed to that
    record, including batteries-by-flight in the UI.
    """
    # Normalize inputs at the top — whitespace-only strings (which some DJI
    # parsers emit when the field is present but blank) would otherwise pass
    # the truthy guard, hit the DB with a useless `"   "` query, and produce
    # a confusing "serial=    present but unmatched" log line. v2.63.15.
    drone_serial = (drone_serial or "").strip() or None
    drone_model = (drone_model or "").strip() or None

    # 1. Exact serial match — authoritative when serial is present.
    if drone_serial:
        result = await db.execute(
            select(Aircraft).where(
                Aircraft.serial_number.isnot(None),
                func.upper(Aircraft.serial_number) == drone_serial.upper(),
            )
        )
        match = result.scalar_one_or_none()
        if match:
            logger.info(
                "fleet-match: serial=%s → aircraft=%s (%s)",
                drone_serial, match.id, match.model_name,
            )
            return match
        # Serial present but no fleet record — do NOT fall back to model match.
        # Flight stays unattributed; user can attach manually from the UI.
        logger.info(
            "fleet-match: serial=%s present but unmatched in fleet; leaving unattributed",
            drone_serial,
        )
        return None

    # 2. No serial → exact normalized model match, but only if unambiguous.
    if drone_model:
        parsed_norm = _normalize_model(drone_model)
        if not parsed_norm:
            return None

        result = await db.execute(select(Aircraft))
        candidates = [ac for ac in result.scalars().all()
                      if _normalize_model(ac.model_name) == parsed_norm]

        if len(candidates) == 1:
            ac = candidates[0]
            logger.info(
                "fleet-match: model=%s (no serial) → aircraft=%s (%s) [unique]",
                drone_model, ac.id, ac.model_name,
            )
            return ac
        if len(candidates) > 1:
            logger.info(
                "fleet-match: model=%s (no serial) ambiguous — %d fleet aircraft share this model; leaving unattributed",
                drone_model, len(candidates),
            )
            return None
        logger.info(
            "fleet-match: model=%s (no serial) — no fleet aircraft with exact normalized match; leaving unattributed",
            drone_model,
        )

    return None


async def _generate_flight_name(db: AsyncSession, drone_model: str | None, fleet_aircraft: Aircraft | None, start_time: _dt | None) -> str:
    """Generate a standardized flight name: ``<Label>_YYYYMMDD_XXXX`` (ADR-0027).

    - ``<Label>`` — the fleet aircraft ``model_name`` if matched, else the
      parsed ``drone_model``, else ``Flight`` (spaces → hyphens).
    - ``YYYYMMDD`` — the flight's calendar date in the **operator's local**
      timezone, NOT the UTC date (ADR-0017). An evening Pacific flight whose
      stored UTC instant rolls past midnight is still named for the local day.
    - ``XXXX`` — the flight's **start-time rank within its own
      (label, operator-local day) group**, zero-padded to 4 digits.

    The pre-ADR-0027 implementation was broken three ways: it counted by
    ``created_at`` (ingest time) inside a window built from ``start_time``'s day
    — so when start-day ≠ ingest-day the count returned 0 and every flight
    collided on ``_0001``; it was a fleet-wide "created today" tally rather than
    per-aircraft and start-ordered; and it used a UTC day boundary for the
    window against a Pacific date token (off-by-one). The result was duplicate
    and garbage-ordered names.

    The sequence is the start-time rank: ``1 + (flights in the same group with an
    earlier start_time)``. A trailing **conflict-bump** loop plus the partial
    unique index ``uq_flights_autoname`` (migration 0004) guarantee the name is
    unique even under out-of-order imports or two airframes sharing a model
    label on the same day. The group key is the *name prefix* (label + local
    day) because that is exactly what must be unique on the wire.
    """
    # Pick the best drone identifier — fleet aircraft name takes priority
    aircraft_label = (fleet_aircraft.model_name if fleet_aircraft else None) or drone_model or "Flight"
    # Clean it up for a filename-friendly label
    aircraft_label = aircraft_label.replace(" ", "-").strip()

    flight_date = start_time if start_time else _dt.utcnow()
    date_str = local_date_compact(flight_date)
    prefix = f"{aircraft_label}_{date_str}_"

    # M7 (ADR-0028): the prefix contains literal underscores, which are
    # single-char wildcards in SQL LIKE. Unescaped, ``Mavic-3_20240115_%`` also
    # matches ``Mavic-3X20240115X0001`` → wrong rank counts and sequence gaps.
    # Escape the LIKE metacharacters and declare the escape char explicitly.
    esc_prefix = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    like_pat = f"{esc_prefix}%"

    # Start-time rank within the (label, operator-local day) group. The group is
    # every existing flight whose generated name shares this prefix; the rank is
    # how many of them started strictly before this flight. Flights with no
    # start_time (rare) sort last and are simply appended.
    if start_time is not None:
        rank_result = await db.execute(
            select(func.count(Flight.id)).where(
                Flight.name.like(like_pat, escape="\\"),
                Flight.start_time.isnot(None),
                Flight.start_time < start_time,
            )
        )
    else:
        rank_result = await db.execute(
            select(func.count(Flight.id)).where(Flight.name.like(like_pat, escape="\\"))
        )
    seq_num = (rank_result.scalar() or 0) + 1

    # Conflict-bump: advance the sequence past any name already taken in this
    # group so a collision can never persist (defence-in-depth alongside the DB
    # unique index). Bounded by the group size + 1 to avoid an unbounded loop.
    taken_result = await db.execute(
        select(Flight.name).where(Flight.name.like(like_pat, escape="\\"))
    )
    taken = {row[0] for row in taken_result.all()}
    candidate = f"{prefix}{seq_num:04d}"
    while candidate in taken:
        seq_num += 1
        candidate = f"{prefix}{seq_num:04d}"

    return candidate


async def _get_dji_api_key(db: AsyncSession) -> str | None:
    """Read the DJI API key from system settings (set in Settings > Flight Data)."""
    try:
        result = await db.execute(
            select(SystemSetting).where(SystemSetting.key == "dji_api_key")
        )
        row = result.scalar_one_or_none()
        if row and row.value and row.value.strip():
            return row.value.strip()
    except Exception as e:
        logger.warning("Could not read DJI API key from settings: %s", e)
    return None


# ── List flights ──────────────────────────────────────────────────────
@router.get("", response_model=list[FlightResponse])
async def list_flights(
    search: str = Query(None),
    drone: str = Query(None),
    source: str = Query(None),
    date_from: str = Query(None, description="ISO date string for start of range"),
    date_to: str = Query(None, description="ISO date string for end of range"),
    aircraft_id: str = Query(None, description="Filter by fleet aircraft UUID"),
    sort_by: str = Query("date", description="Sort column: date, name, drone, duration, distance, altitude, speed, source"),
    sort_dir: str = Query("desc", description="Sort direction: asc or desc"),
    limit: int = Query(500, le=2000),
    offset: int = Query(0),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    # Build sort expression
    sort_col_map = {
        "date": Flight.start_time,
        "name": Flight.name,
        "drone": Flight.drone_model,
        "duration": Flight.duration_secs,
        "distance": Flight.total_distance,
        "altitude": Flight.max_altitude,
        "speed": Flight.max_speed,
        "source": Flight.source,
    }
    sort_col = sort_col_map.get(sort_by, Flight.start_time)
    order = desc(sort_col) if sort_dir == "desc" else sort_col.asc()
    # CRITICAL (ADR-0019): defer the heavy per-flight JSON columns. The list
    # response (FlightResponse) never includes gps_track / telemetry /
    # raw_metadata, but SQLAlchemy loads ALL mapped columns by default — and
    # those columns can hold ~19k GPS points + a full telemetry time-series
    # per flight. Materializing them for ~500 rows decompressed the TOASTed
    # JSON into >1 GB of Python objects, OOM-killing the 1 GB uvicorn worker
    # mid-serialization. The picker's GET /api/flight-library then failed,
    # silently fell back to the (unreachable) ODL proxy, and uploaded flights
    # vanished from the mission picker. Deferring keeps the list lean; the
    # detail/telemetry/track routes load these columns on demand.
    query = (
        select(Flight)
        .options(*[defer(col) for col in LIST_DEFERRED_COLUMNS])
        .order_by(order, desc(Flight.created_at))
    )

    if search:
        q = f"%{search}%"
        query = query.where(
            Flight.name.ilike(q) | Flight.drone_model.ilike(q) |
            Flight.drone_serial.ilike(q) | Flight.notes.ilike(q)
        )
    if drone:
        query = query.where(Flight.drone_model.ilike(f"%{drone}%"))
    if source:
        query = query.where(Flight.source == source)
    if date_from:
        try:
            from datetime import datetime as _dt
            dt_from = _dt.fromisoformat(date_from.replace("Z", "+00:00"))
            query = query.where(Flight.start_time >= dt_from)
        except ValueError:
            pass
    if date_to:
        try:
            from datetime import datetime as _dt, timedelta as _td
            dt_to = _dt.fromisoformat(date_to.replace("Z", "+00:00"))
            # Include the entire day
            dt_to = dt_to.replace(hour=23, minute=59, second=59)
            query = query.where(Flight.start_time <= dt_to)
        except ValueError:
            pass
    if aircraft_id:
        try:
            from uuid import UUID as _UUID
            query = query.where(Flight.aircraft_id == _UUID(aircraft_id))
        except ValueError:
            pass

    query = query.offset(offset).limit(limit)
    result = await db.execute(query)
    return result.scalars().all()


# ── Flight stats summary ─────────────────────────────────────────────
# NOTE: must be registered before /{flight_id} to avoid route shadowing
@router.get("/stats/summary")
async def flight_stats(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(
            func.count(Flight.id),
            func.sum(Flight.duration_secs),
            func.sum(Flight.total_distance),
            func.max(Flight.max_altitude),
            func.max(Flight.max_speed),
            func.sum(Flight.point_count),
        )
    )
    row = result.one()
    total = row[0] or 0

    # Longest flight by duration
    longest = None
    longest_result = await db.execute(
        select(Flight).where(Flight.duration_secs > 0).order_by(desc(Flight.duration_secs)).limit(1)
    )
    longest_flight = longest_result.scalar_one_or_none()
    if longest_flight:
        longest = {
            "name": longest_flight.name,
            "duration_secs": longest_flight.duration_secs,
            "drone_model": longest_flight.drone_model,
        }

    # Farthest from home (max total_distance in a single flight)
    farthest = None
    farthest_result = await db.execute(
        select(Flight).where(Flight.total_distance > 0).order_by(desc(Flight.total_distance)).limit(1)
    )
    farthest_flight = farthest_result.scalar_one_or_none()
    if farthest_flight:
        farthest = {
            "name": farthest_flight.name,
            "total_distance": farthest_flight.total_distance,
            "drone_model": farthest_flight.drone_model,
        }

    # Recent flights (last 10 by creation date — most recently added to the system)
    recent_result = await db.execute(
        select(Flight).order_by(desc(Flight.created_at)).limit(10)
    )
    recent_flights = [
        {
            "id": str(f.id),
            "name": f.name,
            "start_time": iso_utc(f.start_time),
            "created_at": iso_utc(f.created_at),
            "duration_secs": f.duration_secs,
            "total_distance": f.total_distance,
            "max_altitude": f.max_altitude,
            "drone_model": f.drone_model,
            "drone_serial": f.drone_serial,
        }
        for f in recent_result.scalars().all()
    ]

    return {
        "total_flights": total,
        "total_duration": row[1] or 0,
        "total_distance": row[2] or 0,
        "max_altitude": row[3] or 0,
        "max_speed": row[4] or 0,
        "total_points": row[5] or 0,
        "avg_duration": (row[1] or 0) / total if total > 0 else 0,
        "avg_distance": (row[2] or 0) / total if total > 0 else 0,
        "longest_flight": longest,
        "farthest_flight": farthest,
        "recent_flights": recent_flights,
    }


# ── Geo helpers for telemetry-stats ──────────────────────────────────

# Approximate bounding boxes: (state, min_lat, max_lat, min_lon, max_lon)
_US_STATE_BOXES: list[tuple[str, float, float, float, float]] = [
    ("Alabama", 30.22, 35.01, -88.47, -84.89),
    ("Alaska", 51.21, 71.39, -179.15, -129.98),
    ("Arizona", 31.33, 37.00, -114.81, -109.04),
    ("Arkansas", 33.00, 36.50, -94.62, -89.64),
    ("California", 32.53, 42.01, -124.41, -114.13),
    ("Colorado", 36.99, 41.00, -109.06, -102.04),
    ("Connecticut", 40.95, 42.05, -73.73, -71.79),
    ("Delaware", 38.45, 39.84, -75.79, -75.05),
    ("Florida", 24.40, 31.00, -87.63, -80.03),
    ("Georgia", 30.36, 35.00, -85.61, -80.84),
    ("Hawaii", 18.91, 22.24, -160.25, -154.81),
    ("Idaho", 41.99, 49.00, -117.24, -111.04),
    ("Illinois", 36.97, 42.51, -91.51, -87.02),
    ("Indiana", 37.77, 41.76, -88.10, -84.78),
    ("Iowa", 40.37, 43.50, -96.64, -90.14),
    ("Kansas", 36.99, 40.00, -102.05, -94.59),
    ("Kentucky", 36.50, 39.15, -89.57, -81.96),
    ("Louisiana", 28.93, 33.02, -94.04, -88.82),
    ("Maine", 43.06, 47.46, -71.08, -66.95),
    ("Maryland", 37.91, 39.72, -79.49, -75.05),
    ("Massachusetts", 41.24, 42.89, -73.51, -69.93),
    ("Michigan", 41.70, 48.26, -90.42, -82.12),
    ("Minnesota", 43.50, 49.38, -97.24, -89.49),
    ("Mississippi", 30.17, 35.00, -91.66, -88.10),
    ("Missouri", 35.99, 40.61, -95.77, -89.10),
    ("Montana", 44.36, 49.00, -116.05, -104.04),
    ("Nebraska", 40.00, 43.00, -104.05, -95.31),
    ("Nevada", 35.00, 42.00, -120.01, -114.04),
    ("New Hampshire", 42.70, 45.31, -72.56, -70.70),
    ("New Jersey", 38.93, 41.36, -75.56, -73.89),
    ("New Mexico", 31.33, 37.00, -109.05, -103.00),
    ("New York", 40.50, 45.01, -79.76, -71.86),
    ("North Carolina", 33.84, 36.59, -84.32, -75.46),
    ("North Dakota", 45.94, 49.00, -104.05, -96.55),
    ("Ohio", 38.40, 41.98, -84.82, -80.52),
    ("Oklahoma", 33.62, 37.00, -103.00, -94.43),
    ("Oregon", 41.99, 46.29, -124.57, -116.46),
    ("Pennsylvania", 39.72, 42.27, -80.52, -74.69),
    ("Rhode Island", 41.15, 42.02, -71.86, -71.12),
    ("South Carolina", 32.03, 35.22, -83.35, -78.54),
    ("South Dakota", 42.48, 45.95, -104.06, -96.44),
    ("Tennessee", 34.98, 36.68, -90.31, -81.65),
    ("Texas", 25.84, 36.50, -106.65, -93.51),
    ("Utah", 36.99, 42.00, -114.05, -109.04),
    ("Vermont", 42.73, 45.02, -73.44, -71.46),
    ("Virginia", 36.54, 39.47, -83.68, -75.24),
    ("Washington", 45.54, 49.00, -124.85, -116.92),
    ("West Virginia", 37.20, 40.64, -82.64, -77.72),
    ("Wisconsin", 42.49, 47.08, -92.89, -86.25),
    ("Wyoming", 40.99, 45.01, -111.06, -104.05),
]


def _lat_lon_to_state(lat: float, lon: float) -> str | None:
    """Return a US state name for the given lat/lon using bounding boxes."""
    for name, min_lat, max_lat, min_lon, max_lon in _US_STATE_BOXES:
        if min_lat <= lat <= max_lat and min_lon <= lon <= max_lon:
            return name
    return None


def _lon_to_timezone(lat: float, lon: float) -> str | None:
    """Return a US timezone name from lat/lon using simple longitude ranges."""
    if lat < 25 and lon < -150:
        return "Hawaii"
    if lat > 50 and lon < -130:
        return "Alaska"
    if lon <= -115:
        return "Pacific"
    if lon <= -105:
        return "Mountain"
    if lon <= -85:
        return "Central"
    return "Eastern"


# ── Telemetry stats (aggregate) ──────────────────────────────────────
# NOTE: must be registered before /{flight_id} to avoid route shadowing
@router.get("/telemetry-stats")
async def telemetry_stats(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Return aggregate statistics computed from all flights in the database."""
    logger.info("Computing telemetry-stats for user %s", _user.username)

    # Single query — load only the columns we need
    result = await db.execute(
        select(
            Flight.duration_secs,
            Flight.total_distance,
            Flight.max_altitude,
            Flight.max_speed,
            Flight.drone_serial,
            Flight.drone_model,
            Flight.drone_name,
            Flight.home_lat,
            Flight.home_lon,
            Flight.start_time,
            Flight.battery_serial,
            Flight.source,
            Flight.point_count,
            Flight.name,
            Aircraft.model_name.label("aircraft_model_name"),
        ).outerjoin(Aircraft, Flight.aircraft_id == Aircraft.id)
    )
    rows = result.all()

    total_flights = len(rows)
    total_duration_secs = 0.0
    total_distance_m = 0.0
    overall_max_alt_m = 0.0
    overall_max_speed_ms = 0.0
    longest_flight_secs = 0.0
    farthest_flight_m = 0.0
    drone_serials: set[str] = set()
    flight_locations: list[dict] = []
    states: set[str] = set()
    time_zones: set[str] = set()
    earliest: _dt | None = None
    latest: _dt | None = None
    month_counter: Counter[str] = Counter()
    battery_cycles = 0
    drone_stats: dict[str, dict] = defaultdict(lambda: {"flights": 0, "duration": 0.0})
    source_counter: Counter[str] = Counter()
    flights_needing_reprocess = 0

    for row in rows:
        dur = row.duration_secs or 0
        dist = row.total_distance or 0
        alt = row.max_altitude or 0
        spd = row.max_speed or 0

        total_duration_secs += dur
        total_distance_m += dist
        if alt > overall_max_alt_m:
            overall_max_alt_m = alt
        if spd > overall_max_speed_ms:
            overall_max_speed_ms = spd
        if dur > longest_flight_secs:
            longest_flight_secs = dur
        if dist > farthest_flight_m:
            farthest_flight_m = dist

        if row.drone_serial:
            drone_serials.add(row.drone_serial)

        # Best display name: drone_name (nickname) > aircraft model > flight drone_model > serial > Unknown
        drone_display = (
            row.drone_name
            or row.aircraft_model_name
            or row.drone_model
            or row.drone_serial
            or "Unknown"
        )
        drone_stats[drone_display]["flights"] += 1
        drone_stats[drone_display]["duration"] += dur

        if row.home_lat is not None and row.home_lon is not None:
            flight_locations.append({
                "lat": row.home_lat,
                "lon": row.home_lon,
                "name": row.name or "",
                "date": iso_utc(row.start_time) or "",
                "drone": drone_display,
            })
            st = _lat_lon_to_state(row.home_lat, row.home_lon)
            if st:
                states.add(st)
            tz = _lon_to_timezone(row.home_lat, row.home_lon)
            if tz:
                time_zones.add(tz)

        if row.start_time:
            if earliest is None or row.start_time < earliest:
                earliest = row.start_time
            if latest is None or row.start_time > latest:
                latest = row.start_time
            month_key = row.start_time.strftime("%Y-%m")
            month_counter[month_key] += 1

        if row.battery_serial:
            battery_cycles += 1

        src = row.source or "unknown"
        source_counter[src] += 1

        if src == "dji_txt" and (row.point_count or 0) == 0:
            flights_needing_reprocess += 1

    # Derived values
    busiest_month = month_counter.most_common(1)[0][0] if month_counter else None
    avg_flight_mins = (total_duration_secs / total_flights / 60) if total_flights > 0 else 0.0

    # Build sorted flights_by_month
    flights_by_month = [
        {"month": m, "count": c}
        for m, c in sorted(month_counter.items())
    ]

    # Build drone_breakdown
    drone_breakdown = sorted(
        [
            {
                "drone": drone,
                "flights": info["flights"],
                "hours": round(info["duration"] / 3600, 2),
            }
            for drone, info in drone_stats.items()
        ],
        key=lambda x: x["flights"],
        reverse=True,
    )

    METERS_TO_MILES = 0.000621371
    METERS_TO_FEET = 3.28084
    MS_TO_MPH = 2.23694

    response = {
        "total_flights": total_flights,
        "total_flight_hours": round(total_duration_secs / 3600, 2),
        "total_distance_miles": round(total_distance_m * METERS_TO_MILES, 2),
        "max_altitude_ft": round(overall_max_alt_m * METERS_TO_FEET, 2),
        "max_speed_mph": round(overall_max_speed_ms * MS_TO_MPH, 2),
        "unique_drones": len(drone_serials),
        "flight_locations": flight_locations,
        "states_flown": sorted(states),
        "time_zones_flown": sorted(time_zones),
        "earliest_flight": earliest.isoformat() if earliest else None,
        "latest_flight": latest.isoformat() if latest else None,
        "busiest_month": busiest_month,
        "avg_flight_duration_mins": round(avg_flight_mins, 2),
        "total_battery_cycles": battery_cycles,
        "longest_flight_secs": longest_flight_secs,
        "farthest_flight_miles": round(farthest_flight_m * METERS_TO_MILES, 2),
        "drone_breakdown": drone_breakdown,
        "source_breakdown": dict(source_counter),
        "flights_by_month": flights_by_month,
        "flights_needing_reprocess": flights_needing_reprocess,
    }

    logger.info("telemetry-stats computed: %d flights, %.1f hours", total_flights, total_duration_secs / 3600)
    return response


# ── Parser health check ──────────────────────────────────────────────
@router.get("/parser/status")
async def parser_status(
    _user: User = Depends(get_current_user),
):
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{PARSER_URL}/health")
            return resp.json()
    except Exception as e:
        return {"status": "offline", "error": str(e)}


# ── Device health check (test API key + connectivity) ────────────────
@router.get("/device-health")
async def device_health(
    device: DeviceApiKey = Depends(validate_device_api_key),
    db: AsyncSession = Depends(get_db),
):
    """Lightweight connectivity test for DroneOpsSync.

    Returns 200 with device info if the API key is valid and the server is
    reachable.  DroneOpsSync can hit this endpoint on startup to verify the
    connection before attempting file uploads.

    ADR-0003 — when the device authenticated via the OLD key during a
    rotation grace window, include ``rotated_key`` (the new raw key, read
    from the Redis hint) and ``rotation_grace_until`` (ISO-8601 UTC) so
    the paired controller can self-update without operator interaction.
    The fields are omitted otherwise; clients that don't know about
    them keep parsing the response unchanged.
    """
    parser_ok = False
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{PARSER_URL}/health")
            parser_ok = resp.status_code == 200
    except Exception:
        pass

    response: dict = {
        "status": "connected",
        "device_label": device.label,
        "parser_available": parser_ok,
        "upload_endpoint": "/api/flight-library/device-upload",
        # ADR-0023 §2.3 — additive hint: a new client can confirm the async
        # ingest pair exists before using it. Old clients ignore the field
        # (Gson/forward-compat tolerates unknown keys). The route's existence
        # is the real contract; this is a convenience signal, not a gate.
        "async_upload_available": True,
    }

    # ADR-0003 hint emission. The auth dep tagged the row with
    # ``_authenticated_via_old_key`` when both the OLD key matched AND a
    # rotation is in flight. Read the new raw key out of the Redis
    # side-channel; degrade silently if Redis is unreachable (the device
    # will retry on its next preflight tick).
    if getattr(device, "_authenticated_via_old_key", False):
        from app.services.rotation_hint import get_rotation_hint

        new_raw_key = await get_rotation_hint(str(device.id))
        if new_raw_key and device.rotation_grace_until is not None:
            response["rotated_key"] = new_raw_key
            response["rotation_grace_until"] = (
                device.rotation_grace_until.isoformat() + "Z"
            )

    return response


# ── Device upload (field controllers via X-Device-Api-Key) ───────────
@router.post("/device-upload", response_model=FlightUploadResponse)
async def device_upload_flights(
    files: list[UploadFile] = File(...),
    db: AsyncSession = Depends(get_db),
    _device: DeviceApiKey = Depends(validate_device_api_key),
):
    """Upload flight logs from a field controller using a static device API key.

    DEPRECATED (ADR-0023 / P2-2): this route parses each file synchronously
    inside the request, holding the HTTP connection open for the full parse.
    Prefer the async pair — ``POST /api/flight-library/device-upload/async``
    (202 + ``{batch_id, files}``) then poll
    ``GET /api/flight-library/device-upload/status/{batch_id}``. Kept working
    UNCHANGED forever for old field APKs (no OTA channel; backwards compat is
    the gating requirement).

    Identical processing to /upload but authenticates via X-Device-Api-Key header
    instead of a user JWT, allowing automated sync from DroneOpsSync without
    requiring a human login session on the controller.

    Emits a single INFO audit log at the end of each call (ADR-0002 §2.4)
    covering device label, file count, total bytes, and outcome counts. The
    raw API key is never logged; the `device_label` + `device.id` are the
    correlation handles.
    """
    imported = []
    skipped = 0
    errors = []
    total_bytes = 0

    # Fetch DJI API key from settings to pass to the parser
    dji_key = await _get_dji_api_key(db)
    parser_headers = {}
    if dji_key:
        parser_headers["X-DJI-Api-Key"] = dji_key

    for upload in files:
        try:
            async with await _spool_upload(upload) as spooled:
                total_bytes += spooled.size_bytes
                file_hash = spooled.file_hash

                existing = await db.execute(
                    select(Flight).where(Flight.source_file_hash == file_hash)
                )
                if existing.scalar_one_or_none():
                    skipped += 1
                    continue

                status_code, data = await spooled.parse(parser_headers)
                if data is None:
                    errors.append(f"{upload.filename}: parser returned {status_code}")
                    continue

                if data.get("errors"):
                    errors.extend(data["errors"])

                for parsed in data.get("flights", []):
                    # ADR-0028 C2: per-flight SAVEPOINT (see /upload). The
                    # builder dedups + handles autoname races and returns None
                    # on skip; a failure here rolls back only this flight.
                    try:
                        async with db.begin_nested():
                            flight = await _build_flight_from_parsed(
                                db, parsed, file_hash, upload.filename
                            )
                    except Exception as fe:
                        errors.append(f"{upload.filename}: {fe}")
                        continue
                    if flight is not None:
                        imported.append(flight)
                    else:
                        skipped += 1

        except httpx.ConnectError:
            errors.append(f"{upload.filename}: flight-parser service unavailable")
        except Exception as e:
            errors.append(f"{upload.filename}: {str(e)}")

    logger.info(
        "device_upload",
        extra={
            "event": "device_upload",
            "device_label": _device.label,
            "device_id": str(_device.id),
            "file_count": len(files),
            "total_bytes": total_bytes,
            "imported": len(imported),
            "skipped": skipped,
            "error_count": len(errors),
        },
    )

    return FlightUploadResponse(
        imported=len(imported),
        skipped=skipped,
        errors=errors,
        flights=imported,
    )


# ── Async device upload (ADR-0023 / P2-2) — 202 + poll ───────────────
@router.post("/device-upload/async", status_code=202)
async def device_upload_flights_async(
    files: list[UploadFile] = File(...),
    db: AsyncSession = Depends(get_db),
    _device: DeviceApiKey = Depends(validate_device_api_key),
):
    """Asynchronous device upload — return immediately, parse off-request.

    ADR-0023 §2.1. Same auth (``X-Device-Api-Key``) and same OOM-safe
    ``_spool_upload`` streaming as the legacy route. The connection is held
    only for the byte-stream; the expensive parse runs in a Celery job
    (``parse_device_flight_task``). Per-file flow:

    1. Stream the file to disk via ``_spool_upload`` (persists original bytes +
       computes SHA-256 — unchanged path).
    2. **Pre-parse dedup short-circuit** (ADR-0023 §2.1 step 2): if the hash
       already exists, mark the file ``skipped`` with NO job enqueued — the
       common "already synced" case stays instant.
    3. Otherwise enqueue one parse job per file and record it ``queued``.

    Seeds a batch Redis record (``STATUS_QUEUED``) keyed by a server-minted
    ``batch_id`` and returns ``202 + {batch_id, files: [{name, state}]}``.
    Backwards-compatible: old APKs keep hitting the legacy synchronous route.
    """
    from app.tasks.celery_tasks import parse_device_flight_task
    from app.tasks.device_upload_jobs import STATUS_QUEUED, write_batch_state

    batch_id = str(uuid4())

    dji_key = await _get_dji_api_key(db)
    parser_headers: dict = {}
    if dji_key:
        parser_headers["X-DJI-Api-Key"] = dji_key

    per_file: list[dict] = []
    total_bytes = 0
    enqueued = 0

    for upload in files:
        try:
            # Spool to disk — original bytes are persisted to the SHARED hash
            # store (/data/uploads/flight_logs on the app_data volume) inside
            # _spool_upload. The worker reads the original from THAT store, not
            # from this /tmp spool: the worker runs in a separate container and
            # never sees our private /tmp (ADR-0023 cross-container temp-handoff
            # regression). So the /tmp spool is now redundant for the async path
            # and we close it on every branch instead of leaking it (the worker
            # could never unlink a file in a different container's /tmp).
            spooled = await _spool_upload(upload)
            total_bytes += spooled.size_bytes
            file_hash = spooled.file_hash

            existing = await db.execute(
                select(Flight).where(Flight.source_file_hash == file_hash)
            )
            if existing.scalar_one_or_none():
                # Pre-parse dedup short-circuit — no job, instant skip.
                spooled.close()
                per_file.append({"name": upload.filename, "state": "skipped"})
                continue

            # Hardening (ADR-0023 §6): the worker reads the original from the
            # shared hash store, so do NOT accept (no 202 'pending', no enqueued
            # job) any file whose original did not durably land there.
            # _store_original_from_path is fail-soft (logs + swallows), so verify
            # the post-condition explicitly here rather than trusting it didn't
            # throw — failing fast in-request beats deferring an ENOENT the
            # worker can never service.
            if _get_stored_file_path(file_hash) is None:
                spooled.close()
                logger.error(
                    "Async device upload: original not persisted to shared store "
                    "for %s (hash=%s) — refusing to enqueue an unreadable parse; "
                    "check the app_data volume / disk on the backend container",
                    upload.filename, file_hash,
                )
                per_file.append({"name": upload.filename, "state": "error"})
                continue

            # Enqueue first, then release the ephemeral /tmp spool (the worker
            # resolves the file from the shared hash store by file_hash). tmp_path
            # is still passed for signature stability + same-container fallback.
            parse_device_flight_task.delay(
                batch_id, spooled.tmp_path, upload.filename, file_hash, parser_headers
            )
            spooled.close()
            enqueued += 1
            per_file.append({"name": upload.filename, "state": "pending"})
        except Exception as e:
            # Spooling itself failed (disk, oversized, etc.) — record per-file
            # and continue; never fail the whole batch on one file.
            logger.warning("Async device upload spool failed for %s: %s", upload.filename, e)
            per_file.append({"name": upload.filename, "state": "error"})

    write_batch_state(
        batch_id, status=STATUS_QUEUED, phase="queued", progress=0, per_file=per_file,
    )

    logger.info(
        "device_upload_async",
        extra={
            "event": "device_upload_async",
            "device_label": _device.label,
            "device_id": str(_device.id),
            "batch_id": batch_id,
            "file_count": len(files),
            "total_bytes": total_bytes,
            "enqueued": enqueued,
        },
    )

    return {"batch_id": batch_id, "files": per_file}


@router.get("/device-upload/status/{batch_id}")
async def device_upload_status(
    batch_id: str,
    _device: DeviceApiKey = Depends(validate_device_api_key),
):
    """Poll an async device-upload batch (ADR-0023 §2.1 envelope).

    Two-tier read, mirroring ``get_backup_job`` (backup.py): the rich per-file
    overlay from Redis when present; otherwise the Celery ``AsyncResult``
    terminal fallback (a Redis-evicted but completed batch still reports a
    sensible terminal state).
    """
    from app.tasks.celery_tasks import celery_app
    from app.tasks.device_upload_jobs import (
        STATUS_COMPLETE,
        STATUS_FAILED,
        STATUS_QUEUED,
        STATUS_RUNNING,
        read_batch_state,
    )

    state = read_batch_state(batch_id)
    if state is not None:
        per_file = state.get("per_file") or []
        return {
            "batch_id": batch_id,
            "status": state.get("status"),
            "phase": state.get("phase"),
            "progress": state.get("progress"),
            "per_file": per_file,
        }

    # No Redis overlay — derive from the Celery result backend. The task id is
    # not the batch_id (one batch may fan out to several file tasks), so we can
    # only report a coarse terminal rollup here; the Redis overlay is the rich
    # source and is the common case while a client is actively polling.
    result = celery_app.AsyncResult(batch_id)
    if result.state == "SUCCESS":
        payload = result.result if isinstance(result.result, dict) else {}
        return {
            "batch_id": batch_id,
            "status": STATUS_COMPLETE,
            "phase": "done",
            "progress": 100,
            "per_file": payload.get("per_file", []),
        }
    if result.state == "FAILURE":
        return {
            "batch_id": batch_id,
            "status": STATUS_FAILED,
            "phase": "error",
            "progress": 100,
            "per_file": [],
        }
    if result.state == "PENDING":
        # Unknown id or not-yet-started — report queued (cannot distinguish).
        return {
            "batch_id": batch_id,
            "status": STATUS_QUEUED,
            "phase": "queued",
            "progress": 0,
            "per_file": [],
        }
    return {
        "batch_id": batch_id,
        "status": STATUS_RUNNING,
        "phase": result.state.lower(),
        "progress": 50,
        "per_file": [],
    }


# ── Backfill fleet attribution for existing flights ──────────────
@router.post("/backfill-aircraft")
async def backfill_aircraft_attribution(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Match all unlinked flights to fleet aircraft and normalize drone_model names.

    Phase 1: Match flights without aircraft_id to fleet by serial/model.
    Phase 2: Normalize drone_model on ALL flights with aircraft_id to canonical fleet name.
    """
    # ── Phase 1: Match unlinked flights ──
    result = await db.execute(
        select(Flight).where(Flight.aircraft_id.is_(None))
    )
    unlinked = result.scalars().all()

    matched = 0
    for flight in unlinked:
        fleet_match = await _match_fleet_aircraft(db, flight.drone_serial, flight.drone_model)
        if fleet_match:
            flight.aircraft_id = fleet_match.id
            flight.drone_model = fleet_match.model_name
            matched += 1
            logger.info("Backfill: linked flight %s (%s) → %s",
                        flight.id, flight.name, fleet_match.model_name)

    # ── Phase 2: Normalize drone_model on already-linked flights ──
    # Fix flights where aircraft_id is set but drone_model doesn't match the fleet name
    result2 = await db.execute(
        select(Flight).where(Flight.aircraft_id.isnot(None))
    )
    linked_flights = result2.scalars().all()

    # Build aircraft lookup
    ac_result = await db.execute(select(Aircraft))
    aircraft_map = {str(ac.id): ac for ac in ac_result.scalars().all()}

    normalized = 0
    for flight in linked_flights:
        ac = aircraft_map.get(str(flight.aircraft_id))
        if ac and flight.drone_model != ac.model_name:
            old_name = flight.drone_model
            flight.drone_model = ac.model_name
            normalized += 1
            logger.debug("Backfill: normalized drone_model '%s' → '%s' for flight %s",
                         old_name, ac.model_name, flight.id)

    changes = matched + normalized
    if changes > 0:
        await db.flush()

    logger.info("Backfill complete: %d/%d unlinked matched, %d names normalized",
                matched, len(unlinked), normalized)
    return {
        "total_unlinked": len(unlinked),
        "matched": matched,
        "still_unlinked": len(unlinked) - matched,
        "names_normalized": normalized,
    }


# ── Reprocess status ──────────────────────────────────────────────
@router.get("/reprocess/status")
async def reprocess_status(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Return counts of flights that could benefit from re-processing,
    including how many have original files stored on disk."""
    from sqlalchemy import or_

    total_result = await db.execute(
        select(func.count(Flight.id)).where(Flight.source == "dji_txt")
    )
    total_dji = total_result.scalar() or 0

    # DJI flights needing reprocess
    reprocess_q = await db.execute(
        select(Flight.source_file_hash).where(
            Flight.source == "dji_txt",
            or_(
                Flight.point_count == 0,
                Flight.point_count.is_(None),
                Flight.gps_track.is_(None),
            ),
        )
    )
    reprocess_hashes = [row[0] for row in reprocess_q.all() if row[0]]
    reprocessable = len(reprocess_hashes)

    # How many have original files on disk?
    stored_count = sum(1 for h in reprocess_hashes if _get_stored_file_path(h) is not None)

    logger.info("Reprocess status: %d/%d DJI flights need re-processing (%d have stored files)",
                reprocessable, total_dji, stored_count)
    return {
        "reprocessable": reprocessable,
        "total_dji": total_dji,
        "stored_on_disk": stored_count,
        "need_manual_upload": reprocessable - stored_count,
    }


# ── Flight-details coverage (ADR-0043 §4.3) ───────────────────────
# NOTE: literal path — must be registered before /{flight_id} or the UUID
# route swallows it, the same shadowing trap /stats/summary calls out.
@router.get("/details/status", response_model=FlightDetailsStatus)
async def flight_details_status(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Coverage counters for the extended-log sidecar.

    Every value is a COUNT / GROUP BY over indexed or small columns — no
    ``gps_track``, ``telemetry``, ``raw_metadata`` or ``flight_series.values``
    is ever loaded. Mirrors ``/reprocess/status``'s shape so the operator
    reads the two Settings cards the same way.
    """
    total = (await db.execute(select(func.count(Flight.id)))).scalar() or 0

    by_source_rows = (
        await db.execute(
            select(Flight.source, func.count(Flight.id)).group_by(Flight.source)
        )
    ).all()
    by_source = {row[0] or "unknown": row[1] for row in by_source_rows}

    with_details = (
        await db.execute(select(func.count(FlightDetails.flight_id)))
    ).scalar() or 0

    # Stored-original coverage is a filesystem question, so it is scoped to
    # the flights that could ever be re-parsed (DJI rows carrying a hash)
    # rather than stat()ing the whole library.
    hashes = [
        row[0]
        for row in (
            await db.execute(
                select(Flight.source_file_hash).where(
                    Flight.source == "dji_txt",
                    Flight.source_file_hash.is_not(None),
                )
            )
        ).all()
        if row[0]
    ]
    with_stored_file = sum(1 for h in hashes if _get_stored_file_path(h) is not None)

    parser_versions = {
        (row[0] or "unknown"): row[1]
        for row in (
            await db.execute(
                select(FlightDetails.parser_version, func.count())
                .group_by(FlightDetails.parser_version)
            )
        ).all()
    }
    crate_versions = {
        (row[0] or "unknown"): row[1]
        for row in (
            await db.execute(
                select(FlightDetails.crate_version, func.count())
                .group_by(FlightDetails.crate_version)
            )
        ).all()
    }
    restamped = (
        await db.execute(
            select(func.count(FlightDetails.flight_id)).where(
                FlightDetails.gps_timestamps_restamped_at.is_not(None)
            )
        )
    ).scalar() or 0
    model_repaired = (
        await db.execute(
            select(func.count(FlightDetails.flight_id)).where(
                FlightDetails.drone_model_previous.is_not(None)
            )
        )
    ).scalar() or 0

    return FlightDetailsStatus(
        total=total,
        by_source=by_source,
        with_details=with_details,
        without_details=max(total - with_details, 0),
        with_stored_file=with_stored_file,
        parser_versions=parser_versions,
        crate_versions=crate_versions,
        restamped=restamped,
        model_repaired=model_repaired,
    )


# ── Reprocess ALL from stored files ──────────────────────────────
@router.post("/reprocess/all")
async def reprocess_all_from_stored(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Re-process all flights that have original files stored on disk.

    Finds flights needing reprocess (missing GPS/telemetry data), reads
    their original file from /data/uploads/flight_logs/, re-parses through
    the flight-parser service with the current DJI API key, and updates
    the flight records in place.
    """
    from sqlalchemy import or_

    dji_key = await _get_dji_api_key(db)
    parser_headers = {}
    if dji_key:
        parser_headers["X-DJI-Api-Key"] = dji_key

    # Find all flights that need re-processing
    result = await db.execute(
        select(Flight).where(
            Flight.source == "dji_txt",
            or_(
                Flight.point_count == 0,
                Flight.point_count.is_(None),
                Flight.gps_track.is_(None),
            ),
        )
    )
    flights_to_reprocess = list(result.scalars().all())

    updated = 0
    skipped_no_file = 0
    errors = []

    logger.info("Reprocess all: found %d flights needing re-processing", len(flights_to_reprocess))

    for flight in flights_to_reprocess:
        file_path = _get_stored_file_path(flight.source_file_hash) if flight.source_file_hash else None
        if not file_path:
            skipped_no_file += 1
            continue

        try:
            async with httpx.AsyncClient(timeout=120) as client:
                # M5 (ADR-0028): stream the stored original from its file handle
                # instead of read_bytes() — a 20 MB DJI log per iteration across
                # hundreds of flights was an O(file) RAM spike (OOM regression).
                with file_path.open("rb") as fh:
                    resp = await client.post(
                        f"{PARSER_URL}/parse",
                        files={"file": (file_path.name, fh)},
                        headers=parser_headers,
                    )
                if resp.status_code != 200:
                    errors.append(f"{flight.name}: parser returned {resp.status_code}")
                    continue

                data = resp.json()
                if data.get("errors"):
                    errors.extend(data["errors"])

                parsed_flights = data.get("flights", [])
                if not parsed_flights:
                    errors.append(f"{flight.name}: parser returned no flights")
                    continue

                parsed = parsed_flights[0]
                # Per-flight SAVEPOINT (ADR-0028 C2) so one bad row can't poison
                # the rest of a large reprocess sweep.
                async with db.begin_nested():
                    flight.duration_secs = parsed.get("duration_secs", 0)
                    flight.total_distance = parsed.get("total_distance", 0)
                    flight.max_altitude = parsed.get("max_altitude", 0)
                    flight.max_speed = parsed.get("max_speed", 0)
                    flight.home_lat = parsed.get("home_lat")
                    flight.home_lon = parsed.get("home_lon")
                    flight.point_count = parsed.get("point_count", 0)
                    flight.gps_track = parsed.get("gps_track")
                    flight.telemetry = parsed.get("telemetry")
                    flight.raw_metadata = parsed.get("raw_metadata")
                    await db.flush()
                # Re-run the ingest anomaly guard on the freshly-stamped metrics.
                await _check_ingest_anomalies(db, flight)
                updated += 1
                logger.info("Reprocess all: updated flight %s (%s) — %d points",
                            flight.id, flight.name, parsed.get("point_count", 0))

        except httpx.ConnectError:
            errors.append(f"{flight.name}: flight-parser service unavailable")
            break  # No point continuing if parser is down
        except Exception as e:
            errors.append(f"{flight.name}: {str(e)}")

    logger.info("Reprocess all complete: %d updated, %d skipped (no file), %d errors",
                updated, skipped_no_file, len(errors))
    return {
        "updated": updated,
        "skipped_no_file": skipped_no_file,
        "errors": errors,
        "total_attempted": len(flights_to_reprocess),
    }


# ── Reprocess uploaded flight logs (manual re-upload) ─────────────
@router.post("/reprocess")
async def reprocess_flights(
    files: list[UploadFile] = File(...),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Re-upload flight logs to update existing flights matched by file hash.

    Unlike /upload which skips duplicates, this endpoint updates existing flight
    records with freshly parsed data (useful after adding/changing the DJI API key).
    """
    updated_count = 0
    imported_count = 0
    skipped = 0
    errors = []

    dji_key = await _get_dji_api_key(db)
    parser_headers = {}
    if dji_key:
        parser_headers["X-DJI-Api-Key"] = dji_key

    for upload in files:
        try:
            # Stream/overwrite original file off the disk spool (audit #9);
            # reprocess always parses, so there is no pre-parser dedup here.
            async with await _spool_upload(upload) as spooled:
                file_hash = spooled.file_hash

                status_code, data = await spooled.parse(parser_headers)
                if data is None:
                    errors.append(f"{upload.filename}: parser returned {status_code}")
                    continue

                if data.get("errors"):
                    errors.extend(data["errors"])

                for parsed in data.get("flights", []):
                    ph = parsed.get("file_hash", file_hash)
                    # ADR-0028 C2: per-flight SAVEPOINT so one failure can't
                    # poison the batch.
                    existing_flight = None
                    new_flight = None
                    try:
                        async with db.begin_nested():
                            existing_result = await db.execute(
                                select(Flight).where(Flight.source_file_hash == ph)
                            )
                            existing_flight = existing_result.scalar_one_or_none()

                            if existing_flight:
                                existing_flight.duration_secs = parsed.get("duration_secs", 0)
                                existing_flight.total_distance = parsed.get("total_distance", 0)
                                existing_flight.max_altitude = parsed.get("max_altitude", 0)
                                existing_flight.max_speed = parsed.get("max_speed", 0)
                                existing_flight.home_lat = parsed.get("home_lat")
                                existing_flight.home_lon = parsed.get("home_lon")
                                existing_flight.point_count = parsed.get("point_count", 0)
                                existing_flight.gps_track = parsed.get("gps_track")
                                existing_flight.telemetry = parsed.get("telemetry")
                                existing_flight.raw_metadata = parsed.get("raw_metadata")
                                await db.flush()
                            else:
                                # Same builder /upload and /device-upload use —
                                # fleet attribution, name, persist, battery-track.
                                new_flight = await _build_flight_from_parsed(
                                    db, parsed, file_hash, upload.filename,
                                    log_prefix="Reprocess: imported",
                                )
                    except Exception as fe:
                        errors.append(f"{upload.filename}: {fe}")
                        continue

                    if existing_flight is not None:
                        updated_count += 1
                        logger.info("Reprocess: updated flight %s (%s) — %d points",
                                    existing_flight.id, existing_flight.name,
                                    parsed.get("point_count", 0))
                    elif new_flight is not None:
                        imported_count += 1
                    else:
                        skipped += 1

        except httpx.ConnectError:
            errors.append(f"{upload.filename}: flight-parser service unavailable")
        except Exception as e:
            errors.append(f"{upload.filename}: {str(e)}")

    logger.info("Reprocess complete: %d updated, %d imported, %d skipped, %d errors",
                updated_count, imported_count, skipped, len(errors))
    return {"updated": updated_count, "imported": imported_count, "skipped": skipped, "errors": errors}


# ── Airspace — OpenSky proxy ─────────────────────────────────────────
_opensky_token_cache: dict = {"token": None, "expires_at": 0.0}


async def _get_opensky_token(client_id: str, client_secret: str) -> str | None:
    """Obtain (or reuse cached) OAuth2 token from OpenSky Network."""
    now = time.time()
    if _opensky_token_cache["token"] and now < _opensky_token_cache["expires_at"] - 30:
        return _opensky_token_cache["token"]

    token_url = (
        "https://auth.opensky-network.org/auth/realms/opensky-network"
        "/protocol/openid-connect/token"
    )
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            logger.info("OpenSky: requesting OAuth2 token for client_id=%s", client_id)
            resp = await client.post(
                token_url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": client_id,
                    "client_secret": client_secret,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            _opensky_token_cache["token"] = data["access_token"]
            _opensky_token_cache["expires_at"] = now + data.get("expires_in", 300)
            logger.info("OpenSky: OAuth2 token acquired, expires_in=%s", data.get("expires_in"))
            return _opensky_token_cache["token"]
    except Exception as exc:
        logger.warning("OpenSky: OAuth2 token request failed: %s", exc)
        _opensky_token_cache["token"] = None
        _opensky_token_cache["expires_at"] = 0.0
        return None


@router.get("/airspace/aircraft")
async def get_airspace_aircraft(
    lat: float = Query(..., description="Center latitude"),
    lon: float = Query(..., description="Center longitude"),
    radius_nm: float = Query(25, description="Radius in nautical miles"),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Proxy to OpenSky Network API — returns nearby aircraft positions."""
    authenticated = False

    # 1. Read OpenSky credentials from SystemSetting
    client_id = None
    client_secret = None
    try:
        row_id = await db.execute(
            select(SystemSetting.value).where(SystemSetting.key == "opensky_client_id")
        )
        row_secret = await db.execute(
            select(SystemSetting.value).where(SystemSetting.key == "opensky_client_secret")
        )
        client_id = row_id.scalar_one_or_none()
        client_secret = row_secret.scalar_one_or_none()
    except Exception as exc:
        logger.warning("OpenSky: failed to read credentials from DB: %s", exc)

    # 2. Get OAuth2 token if credentials exist
    token = None
    if client_id and client_secret:
        token = await _get_opensky_token(client_id, client_secret)
        if token:
            authenticated = True

    # 3. Convert radius to bounding box
    lat_delta = radius_nm / 60.0
    lon_delta = radius_nm / (60.0 * math.cos(math.radians(lat)))
    lamin = lat - lat_delta
    lamax = lat + lat_delta
    lomin = lon - lon_delta
    lomax = lon + lon_delta

    # 4. Call OpenSky REST API
    api_url = "https://opensky-network.org/api/states/all"
    params = {
        "lamin": round(lamin, 4),
        "lomin": round(lomin, 4),
        "lamax": round(lamax, 4),
        "lomax": round(lomax, 4),
    }
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            logger.info(
                "OpenSky: querying states/all lat=%.4f lon=%.4f radius=%.1fnm authenticated=%s",
                lat, lon, radius_nm, authenticated,
            )
            resp = await client.get(api_url, params=params, headers=headers)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as exc:
        logger.error("OpenSky: API returned HTTP %s: %s", exc.response.status_code, exc.response.text[:200])
        return {"aircraft": [], "timestamp": int(time.time()), "count": 0, "authenticated": authenticated,
                "error": f"OpenSky API error: HTTP {exc.response.status_code}"}
    except Exception as exc:
        logger.error("OpenSky: API request failed: %s", exc)
        return {"aircraft": [], "timestamp": int(time.time()), "count": 0, "authenticated": authenticated,
                "error": f"OpenSky API request failed: {exc}"}

    # 5. Transform response
    states = data.get("states") or []
    aircraft_list = []
    for s in states:
        if len(s) < 17:
            continue
        aircraft_list.append({
            "icao24": s[0],
            "callsign": (s[1] or "").strip(),
            "origin_country": s[2],
            "lat": s[6],
            "lon": s[5],
            "alt_ft": round(s[7] * 3.28084) if s[7] is not None else None,
            "heading": s[10],
            "speed_kts": round(s[9] * 1.94384) if s[9] is not None else None,
            "vertical_rate_fpm": round(s[11] * 196.85) if s[11] is not None else None,
            "on_ground": s[8],
            "squawk": s[14],
        })

    logger.info("OpenSky: returned %d aircraft", len(aircraft_list))
    return {
        "aircraft": aircraft_list,
        "timestamp": data.get("time", int(time.time())),
        "count": len(aircraft_list),
        "authenticated": authenticated,
    }


# ── Get flight detail ─────────────────────────────────────────────────
@router.get("/{flight_id}", response_model=FlightDetailResponse)
async def get_flight(
    flight_id: UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    result = await db.execute(select(Flight).where(Flight.id == flight_id))
    flight = result.scalar_one_or_none()
    if not flight:
        raise HTTPException(status_code=404, detail="Flight not found")
    return flight


# ── Get telemetry (downsampled) ───────────────────────────────────────
@router.get("/{flight_id}/telemetry")
async def get_telemetry(
    flight_id: UUID,
    max_points: int = Query(2000, le=10000),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    result = await db.execute(select(Flight).where(Flight.id == flight_id))
    flight = result.scalar_one_or_none()
    if not flight:
        raise HTTPException(status_code=404, detail="Flight not found")

    telemetry = flight.telemetry or {}
    if not telemetry:
        return {"message": "No telemetry data available", "data": {}}

    # ``downsample`` used to be a closure right here. It now lives in
    # app.services.telemetry_downsample and is shared with the flight-series
    # read path, so the two endpoints cannot drift into disagreeing about
    # which source index a returned point came from (ADR-0032's standing
    # finding: a second copy is how this defect class recurs).
    return {
        "timestamps": downsample(telemetry.get("timestamps", []), max_points),
        "altitude": downsample(telemetry.get("altitude", []), max_points),
        "speed": downsample(telemetry.get("speed", []), max_points),
        "battery_pct": downsample(telemetry.get("battery_pct", []), max_points),
        "battery_voltage": downsample(telemetry.get("battery_voltage", []), max_points),
        "battery_temp": downsample(telemetry.get("battery_temp", []), max_points),
        "satellites": downsample(telemetry.get("satellites", []), max_points),
        "signal_strength": downsample(telemetry.get("signal_strength", []), max_points),
        "distance_from_home": downsample(telemetry.get("distance_from_home", []), max_points),
    }


# ── Flight details (ADR-0043 §4.1/§4.2) ───────────────────────────────
# Columns of ``flight_details`` that are serialised into the ``details``
# payload. Derived from the mapper, minus the join key, so a column added to
# the model is exposed automatically and cannot be forgotten here.
_DETAILS_PAYLOAD_COLUMNS: tuple[str, ...] = tuple(
    c.key for c in sa_inspect(FlightDetails).mapper.column_attrs if c.key != "flight_id"
)

# Hard ceiling on how many series one request may name. Bounds the response
# and the detoast set; the page asks for 1-4.
_MAX_SERIES_PER_REQUEST = 24


def _serialize_details(row: FlightDetails) -> dict:
    """Flatten a details row to JSON-ready primitives."""
    out: dict = {}
    for key in _DETAILS_PAYLOAD_COLUMNS:
        value = getattr(row, key)
        out[key] = iso_utc(value) if isinstance(value, _dt) else value
    return out


@router.get("/{flight_id}/details", response_model=FlightDetailsResponse)
async def get_flight_details(
    flight_id: UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Extended log data for one flight, plus an index of available series.

    **Never 404s on missing details** (operator decision D3): the Flight
    Details link renders on every flight, so "no extended data" is a normal
    response carrying an ``unavailable_reason`` the UI turns into a specific
    message. A 404 means the FLIGHT does not exist.

    The flight row is read column-explicitly (``Flight.source`` only) so this
    route never detoasts ``gps_track`` / ``telemetry`` / ``raw_metadata``, and
    the series index selects every column EXCEPT ``values`` — the whole point
    of the split table (plan §1.5).
    """
    source = (
        await db.execute(select(Flight.source).where(Flight.id == flight_id))
    ).scalar_one_or_none()
    if source is None:
        raise HTTPException(status_code=404, detail="Flight not found")

    details_row = (
        await db.execute(
            select(FlightDetails).where(FlightDetails.flight_id == flight_id)
        )
    ).scalar_one_or_none()

    index_rows = (
        await db.execute(
            select(
                FlightSeries.source,
                FlightSeries.name,
                FlightSeries.unit,
                FlightSeries.sample_count,
                FlightSeries.precision_dp,
            )
            .where(FlightSeries.flight_id == flight_id)
            .order_by(FlightSeries.source, FlightSeries.name)
        )
    ).all()

    return FlightDetailsResponse(
        flight_id=flight_id,
        source=source,
        details=_serialize_details(details_row) if details_row is not None else None,
        series_index=[
            FlightSeriesIndexEntry(
                source=r[0], name=r[1], unit=r[2], sample_count=r[3], precision_dp=r[4]
            )
            for r in index_rows
        ],
        unavailable_reason=details_unavailable_reason(source, details_row is not None),
    )


@router.get("/{flight_id}/details/series", response_model=FlightSeriesResponse)
async def get_flight_details_series(
    flight_id: UUID,
    names: str = Query(..., description="Comma-separated series names"),
    source: str = Query("frame", description="Series group: frame | pilot | ofdm | camera"),
    max_points: int = Query(2000, ge=1, le=10000),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Fetch named series, downsampled at READ time (operator decision D2).

    Stored resolution is full — every recorded sample. Reduction happens only
    here, by index stride via the shared ``telemetry_downsample`` service, so
    a returned point is always a real recorded sample and never a synthesised
    average.

    ``t_offset_s`` for the requested ``source`` is always returned alongside.
    Series in one ``source`` group are index-aligned by construction, so a
    same-length series and its time base get the SAME stride indices and the
    caller never has to align anything. (A series whose length differs from
    the time base — which would mean a write-side bug — gets its own honest
    stride rather than being silently forced onto another series' indices.)
    """
    exists = (
        await db.execute(select(Flight.id).where(Flight.id == flight_id))
    ).scalar_one_or_none()
    if exists is None:
        raise HTTPException(status_code=404, detail="Flight not found")

    requested = [n.strip() for n in names.split(",") if n.strip()]
    if not requested:
        raise HTTPException(status_code=400, detail="names must list at least one series")
    if len(requested) > _MAX_SERIES_PER_REQUEST:
        raise HTTPException(
            status_code=400,
            detail=f"at most {_MAX_SERIES_PER_REQUEST} series per request",
        )

    # The time base is always fetched, even when not asked for — it is what
    # makes the returned values interpretable.
    wanted = set(requested) | {"t_offset_s"}
    rows = (
        await db.execute(
            select(
                FlightSeries.name,
                FlightSeries.unit,
                FlightSeries.values,
            ).where(
                FlightSeries.flight_id == flight_id,
                FlightSeries.source == source,
                FlightSeries.name.in_(wanted),
            )
        )
    ).all()

    stored = {r[0]: (r[1], r[2] or []) for r in rows}
    base_len = len(stored["t_offset_s"][1]) if "t_offset_s" in stored else 0
    if not base_len and stored:
        base_len = max(len(v) for _, v in stored.values())

    series: dict[str, list] = {}
    units: dict[str, str | None] = {}
    for name, (unit, values) in stored.items():
        idx = select_indices(len(values), max_points)
        series[name] = [values[i] for i in idx]
        units[name] = unit

    returned = len(series.get("t_offset_s", next(iter(series.values()), [])))

    return FlightSeriesResponse(
        flight_id=flight_id,
        source=source,
        max_points=max_points,
        sample_count=base_len,
        returned_points=returned,
        series=series,
        units=units,
        missing=sorted(n for n in requested if n not in stored),
    )


# ── Get GPS track ─────────────────────────────────────────────────────
@router.get("/{flight_id}/track")
async def get_track(
    flight_id: UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    result = await db.execute(select(Flight).where(Flight.id == flight_id))
    flight = result.scalar_one_or_none()
    if not flight:
        raise HTTPException(status_code=404, detail="Flight not found")
    return flight.gps_track or []


# ── Upload flight logs ────────────────────────────────────────────────
@router.post("/upload", response_model=FlightUploadResponse)
async def upload_flights(
    files: list[UploadFile] = File(...),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    imported = []
    skipped = 0
    errors = []

    # Fetch DJI API key from settings to pass to the parser
    dji_key = await _get_dji_api_key(db)
    parser_headers = {}
    if dji_key:
        parser_headers["X-DJI-Api-Key"] = dji_key

    # Send files to the parser service — streamed off the disk-spooled
    # multipart temp file, never the whole upload in RAM (audit #9).
    for upload in files:
        try:
            async with await _spool_upload(upload) as spooled:
                file_hash = spooled.file_hash

                # Check for duplicate
                existing = await db.execute(
                    select(Flight).where(Flight.source_file_hash == file_hash)
                )
                if existing.scalar_one_or_none():
                    skipped += 1
                    continue

                # Call parser service
                status_code, data = await spooled.parse(parser_headers)
                if data is None:
                    errors.append(f"{upload.filename}: parser returned {status_code}")
                    continue

                if data.get("errors"):
                    errors.extend(data["errors"])

                for parsed in data.get("flights", []):
                    # ADR-0028 C2: each flight is its own SAVEPOINT, so a single
                    # IntegrityError rolls back ONLY that flight — never the
                    # already-imported siblings in this batch (the old code let
                    # one failure poison the whole transaction, and get_db then
                    # skipped the end-commit, silently discarding the batch while
                    # returning 200 "imported"). The builder handles dedup +
                    # autoname races and returns None on a clean skip.
                    try:
                        async with db.begin_nested():
                            flight = await _build_flight_from_parsed(
                                db, parsed, file_hash, upload.filename
                            )
                    except Exception as fe:
                        errors.append(f"{upload.filename}: {fe}")
                        continue
                    if flight is not None:
                        imported.append(flight)
                    else:
                        skipped += 1

        except httpx.ConnectError:
            errors.append(f"{upload.filename}: flight-parser service unavailable")
        except Exception as e:
            errors.append(f"{upload.filename}: {str(e)}")

    return FlightUploadResponse(
        imported=len(imported),
        skipped=skipped,
        errors=errors,
        flights=imported,
    )


# ── Manual flight entry ───────────────────────────────────────────────
@router.post("/manual", response_model=FlightResponse, status_code=201)
async def create_manual_flight(
    data: FlightCreate,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    flight = Flight(
        name=data.name,
        drone_model=data.drone_model,
        drone_serial=data.drone_serial,
        battery_serial=data.battery_serial,
        start_time=_parse_datetime(data.start_time),  # L5: normalize tz-aware → naive UTC
        duration_secs=data.duration_secs,
        total_distance=data.total_distance,
        max_altitude=data.max_altitude,
        max_speed=data.max_speed,
        home_lat=data.home_lat,
        home_lon=data.home_lon,
        point_count=len(data.gps_track) if data.gps_track else 0,
        gps_track=data.gps_track,
        notes=data.notes,
        tags=data.tags,
        source="manual",
        aircraft_id=data.aircraft_id,
    )
    db.add(flight)
    await db.flush()
    await db.refresh(flight)
    return flight


# ── Update flight ─────────────────────────────────────────────────────
@router.put("/{flight_id}", response_model=FlightResponse)
async def update_flight(
    flight_id: UUID,
    data: FlightUpdate,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    result = await db.execute(select(Flight).where(Flight.id == flight_id))
    flight = result.scalar_one_or_none()
    if not flight:
        raise HTTPException(status_code=404, detail="Flight not found")

    for key, val in data.model_dump(exclude_unset=True).items():
        setattr(flight, key, val)

    await db.flush()
    await db.refresh(flight)
    return flight


# ── Delete flight ─────────────────────────────────────────────────────
# ── Purge all flights ────────────────────────────────────────────────
# NOTE: must be registered before /{flight_id} to avoid route shadowing
@router.delete("/purge/all", status_code=200)
async def purge_all_flights(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Delete ALL flights, battery logs, and batteries for a clean re-sync."""
    from sqlalchemy import delete as sql_delete

    # Delete all battery logs (not just flight-linked — full wipe for clean ODL re-sync)
    await db.execute(sql_delete(BatteryLog))
    # Delete all batteries
    bat_result = await db.execute(sql_delete(Battery))
    bat_count = bat_result.rowcount
    # Delete all flights
    result = await db.execute(sql_delete(Flight))
    count = result.rowcount
    await db.commit()
    return {"deleted": count, "batteries_deleted": bat_count}


@router.delete("/{flight_id}", status_code=204)
async def delete_flight(
    flight_id: UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    result = await db.execute(select(Flight).where(Flight.id == flight_id))
    flight = result.scalar_one_or_none()
    if not flight:
        raise HTTPException(status_code=404, detail="Flight not found")
    await db.delete(flight)


# ── Export flight ─────────────────────────────────────────────────────
@router.get("/{flight_id}/export/{fmt}")
async def export_flight(
    flight_id: UUID,
    fmt: str,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    result = await db.execute(select(Flight).where(Flight.id == flight_id))
    flight = result.scalar_one_or_none()
    if not flight:
        raise HTTPException(status_code=404, detail="Flight not found")

    track = flight.gps_track or []

    if fmt == "csv":
        return _export_csv(flight, track)
    elif fmt == "gpx":
        return _export_gpx(flight, track)
    elif fmt == "kml":
        return _export_kml(flight, track)
    else:
        raise HTTPException(status_code=400, detail=f"Unsupported format: {fmt}. Use csv, gpx, or kml.")


# ── Import from OpenDroneLog ──────────────────────────────────────────
@router.post("/import/opendronelog")
async def import_from_opendronelog(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Bulk import all flights from configured OpenDroneLog instance."""
    from app.services.opendronelog import opendronelog_client

    if not await opendronelog_client.is_configured(db):
        raise HTTPException(status_code=400, detail="OpenDroneLog URL not configured")

    imported = 0
    skipped = 0
    errors = []

    try:
        odl_flights = await opendronelog_client.list_flights(db)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to connect to OpenDroneLog: {e}")

    # Fetch custom equipment names from ODL (battery + drone nicknames)
    equipment = {"battery_names": {}, "aircraft_names": {}}
    try:
        equipment = await opendronelog_client.get_equipment_names(db)
        logger.info("ODL import: loaded %d battery names, %d aircraft names",
                     len(equipment.get("battery_names", {})), len(equipment.get("aircraft_names", {})))
    except Exception as exc:
        logger.warning("ODL import: could not fetch equipment_names: %s", exc)

    battery_names = equipment.get("battery_names", {})   # serial -> custom name
    aircraft_names = equipment.get("aircraft_names", {})  # serial -> custom name

    for odl in odl_flights:
        try:
            odl_id = str(odl.get("id", ""))
            odl_hash = f"odl_{odl_id}"

            # Dedup by ODL flight ID (stable across renames)
            existing = await db.execute(
                select(Flight).where(Flight.source_file_hash == odl_hash)
            )
            if existing.scalar_one_or_none():
                skipped += 1
                continue

            # Flight display name: displayName from ODL, else fileName
            name = odl.get("display_name") or odl.get("name") or f"ODL Flight {odl_id}"

            # Parse start_time (ODL sends ISO 8601)
            start_raw = odl.get("start_time")
            start_dt = _parse_datetime(start_raw)
            if not start_dt:
                logger.warning("ODL import: could not parse start_time '%s' for flight %s (%s)", start_raw, odl_id, name)

            # Drone: model is hardware (droneModel), custom name from equipment_names (priority)
            # then aircraftName (DJI app name embedded in log file) as fallback
            drone_model = odl.get("drone_model") or None
            drone_serial = odl.get("drone_serial") or None
            drone_name = None
            if drone_serial:
                drone_name = aircraft_names.get(drone_serial) or aircraft_names.get(drone_serial.upper()) or None
            if not drone_name:
                drone_name = odl.get("drone_name") or None  # aircraftName from log

            # Battery custom name from equipment_names
            bat_serial = odl.get("battery_serial") or None
            bat_name = ""
            if bat_serial:
                bat_name = battery_names.get(bat_serial) or battery_names.get(bat_serial.upper()) or ""

            # Fetch GPS track
            track = []
            try:
                track = await opendronelog_client.get_flight_track(odl_id, db)
            except Exception:
                pass

            def _float(val: object, default: float = 0.0) -> float:
                try:
                    return float(val) if val is not None else default
                except (ValueError, TypeError):
                    return default

            def _int(val: object, default: int = 0) -> int:
                try:
                    return int(val) if val is not None else default
                except (ValueError, TypeError):
                    return default

            # C1 (ADR-0028): ODL passes total_distance through verbatim, so a
            # corrupt/teleported value (e.g. 12,583,855 m in 554 s) lands in the
            # DB and poisons every mission report that sums it. Clamp/recompute
            # any physically-impossible distance and stash an audit note.
            _odl_dur = _float(odl.get("duration_secs"))
            _odl_spd = _float(odl.get("max_speed"))
            _odl_dist_raw = _float(odl.get("total_distance"))
            _odl_dist, _dist_note = sanitize_odl_distance(_odl_dist_raw, _odl_dur, _odl_spd, track)
            if _dist_note:
                logger.warning(
                    "ODL import: sanitized implausible distance for %s (%s): %.0f→%.1f m via %s",
                    odl_id, name, _odl_dist_raw, _odl_dist, _dist_note["method"],
                )

            flight = Flight(
                name=name,
                drone_model=drone_model,
                drone_name=drone_name,
                drone_serial=drone_serial,
                battery_serial=bat_serial,
                start_time=start_dt,
                duration_secs=_odl_dur,
                total_distance=_odl_dist,
                max_altitude=_float(odl.get("max_altitude")),
                max_speed=_odl_spd,
                home_lat=_float(odl.get("home_lat"), None) if odl.get("home_lat") else None,
                home_lon=_float(odl.get("home_lon"), None) if odl.get("home_lon") else None,
                point_count=_int(odl.get("point_count")) or len(track),
                gps_track=track if track else None,
                raw_metadata={"distance_sanitized": _dist_note} if _dist_note else None,
                notes=odl.get("notes") or None,
                tags=odl.get("tags") if odl.get("tags") else None,
                source="opendronelog_import",
                source_file_hash=odl_hash,
                original_filename=odl.get("file_name") or None,
            )
            db.add(flight)
            await db.flush()
            imported += 1

            # Auto-create battery record with custom name from equipment_names
            if bat_serial:
                await _track_battery_from_odl(db, flight, bat_serial, bat_name, drone_model)

        except Exception as e:
            errors.append(f"Flight {odl.get('id', '?')}: {str(e)}")

    return {
        "imported": imported,
        "skipped": skipped,
        "errors": errors,
        "total_in_opendronelog": len(odl_flights),
    }


# ── Streaming import from OpenDroneLog (with progress) ────────────────
@router.post("/import/opendronelog/stream")
async def import_from_opendronelog_stream(
    _user: User = Depends(get_current_user),
):
    """Stream import progress as Server-Sent Events."""
    from app.services.opendronelog import opendronelog_client

    async def event_stream():
        def sse(data: dict) -> str:
            return f"data: {json.dumps(data)}\n\n"

        async with async_session() as db:
            try:
                if not await opendronelog_client.is_configured(db):
                    yield sse({"type": "error", "message": "OpenDroneLog URL not configured"})
                    return

                logger.info("ODL import: fetching flight list...")
                try:
                    odl_flights = await opendronelog_client.list_flights(db)
                except Exception as e:
                    logger.error("ODL import: failed to list flights: %s", e)
                    yield sse({"type": "error", "message": f"Failed to connect to OpenDroneLog: {e}"})
                    return

                total = len(odl_flights)
                logger.info("ODL import: found %d flights to process", total)

                if total == 0:
                    yield sse({"type": "complete", "total": 0, "imported": 0, "skipped": 0, "errors": 0, "error_details": []})
                    return

                # Fetch custom equipment names once
                equipment = {"battery_names": {}, "aircraft_names": {}}
                try:
                    equipment = await opendronelog_client.get_equipment_names(db)
                    logger.info("ODL import: loaded %d battery names, %d aircraft names",
                                len(equipment.get("battery_names", {})), len(equipment.get("aircraft_names", {})))
                except Exception as exc:
                    logger.warning("ODL import: could not fetch equipment_names: %s", exc)

                battery_names = equipment.get("battery_names", {})
                aircraft_names = equipment.get("aircraft_names", {})

                imported = 0
                skipped = 0
                error_count = 0
                error_details = []

                for i, odl in enumerate(odl_flights):
                    odl_id = str(odl.get("id", ""))
                    odl_hash = f"odl_{odl_id}"
                    name = odl.get("display_name") or odl.get("name") or f"ODL Flight {odl_id}"

                    try:
                        # Dedup by ODL flight ID
                        existing = await db.execute(
                            select(Flight).where(Flight.source_file_hash == odl_hash)
                        )
                        if existing.scalar_one_or_none():
                            skipped += 1
                            logger.debug("ODL import: skipped duplicate '%s'", name)
                            yield sse({"type": "progress", "current": i + 1, "total": total, "imported": imported, "skipped": skipped, "errors": error_count, "flight_name": name})
                            continue

                        # Parse start_time (ISO 8601 from ODL)
                        start_raw = odl.get("start_time")
                        start_dt = _parse_datetime(start_raw)
                        if not start_dt:
                            logger.warning("ODL import: could not parse start_time '%s' for flight %s", start_raw, odl_id)

                        # Drone: model is hardware, custom name from equipment_names (priority)
                        # then aircraftName (DJI app name from log file) as fallback
                        drone_model = odl.get("drone_model") or None
                        drone_serial = odl.get("drone_serial") or None
                        drone_name = None
                        if drone_serial:
                            drone_name = aircraft_names.get(drone_serial) or aircraft_names.get(drone_serial.upper()) or None
                        if not drone_name:
                            drone_name = odl.get("drone_name") or None  # aircraftName from log

                        # Battery custom name from equipment_names
                        bat_serial = odl.get("battery_serial") or None
                        bat_name = ""
                        if bat_serial:
                            bat_name = battery_names.get(bat_serial) or battery_names.get(bat_serial.upper()) or ""

                        # Fetch GPS track
                        track = []
                        try:
                            track = await opendronelog_client.get_flight_track(odl_id, db)
                        except Exception as te:
                            logger.warning("ODL import: track fetch failed for %s: %s", odl_id, te)

                        def _float(val, default=0.0):
                            try:
                                return float(val) if val is not None else default
                            except (ValueError, TypeError):
                                return default

                        def _int(val, default=0):
                            try:
                                return int(val) if val is not None else default
                            except (ValueError, TypeError):
                                return default

                        # C1 (ADR-0028): clamp/recompute physically-impossible
                        # ODL distances (see import_from_opendronelog).
                        _odl_dur = _float(odl.get("duration_secs"))
                        _odl_spd = _float(odl.get("max_speed"))
                        _odl_dist_raw = _float(odl.get("total_distance"))
                        _odl_dist, _dist_note = sanitize_odl_distance(
                            _odl_dist_raw, _odl_dur, _odl_spd, track
                        )
                        if _dist_note:
                            logger.warning(
                                "ODL import: sanitized implausible distance for %s (%s): %.0f→%.1f m via %s",
                                odl_id, name, _odl_dist_raw, _odl_dist, _dist_note["method"],
                            )

                        flight = Flight(
                            name=name,
                            drone_model=drone_model,
                            drone_name=drone_name,
                            drone_serial=drone_serial,
                            battery_serial=bat_serial,
                            start_time=start_dt,
                            duration_secs=_odl_dur,
                            total_distance=_odl_dist,
                            max_altitude=_float(odl.get("max_altitude")),
                            max_speed=_odl_spd,
                            home_lat=_float(odl.get("home_lat"), None) if odl.get("home_lat") else None,
                            home_lon=_float(odl.get("home_lon"), None) if odl.get("home_lon") else None,
                            point_count=_int(odl.get("point_count")) or len(track),
                            gps_track=track if track else None,
                            raw_metadata={"distance_sanitized": _dist_note} if _dist_note else None,
                            notes=odl.get("notes") or None,
                            tags=odl.get("tags") if odl.get("tags") else None,
                            source="opendronelog_import",
                            source_file_hash=odl_hash,
                            original_filename=odl.get("file_name") or None,
                        )
                        db.add(flight)
                        await db.flush()
                        imported += 1
                        logger.info("ODL import: imported '%s' (%d/%d)", name, i + 1, total)

                        # Auto-create battery record with custom name from equipment_names
                        if bat_serial:
                            await _track_battery_from_odl(db, flight, bat_serial, bat_name, drone_model)

                    except Exception as e:
                        error_count += 1
                        err_msg = f"Flight {odl_id} ({name}): {type(e).__name__}: {e}"
                        error_details.append(err_msg)
                        logger.error("ODL import: error on flight %s: %s\n%s", odl_id, e, traceback.format_exc())

                    yield sse({"type": "progress", "current": i + 1, "total": total, "imported": imported, "skipped": skipped, "errors": error_count, "flight_name": name})

                # Commit all at the end
                await db.commit()
                logger.info("ODL import: complete — %d imported, %d skipped, %d errors out of %d", imported, skipped, error_count, total)

                yield sse({
                    "type": "complete",
                    "total": total,
                    "imported": imported,
                    "skipped": skipped,
                    "errors": error_count,
                    "error_details": error_details[:20],
                })

            except Exception as e:
                logger.error("ODL import: fatal error: %s\n%s", e, traceback.format_exc())
                await db.rollback()
                yield sse({"type": "error", "message": f"Import failed: {type(e).__name__}: {e}"})

    return StreamingResponse(event_stream(), media_type="text/event-stream")






# ── Helpers ───────────────────────────────────────────────────────────

async def _track_battery(db: AsyncSession, flight: Flight, battery_data: dict):
    """Auto-create/update battery record from parsed flight data."""
    serial = battery_data.get("serial")
    if not serial:
        return

    result = await db.execute(select(Battery).where(Battery.serial == serial))
    battery = result.scalar_one_or_none()

    if not battery:
        battery = Battery(serial=serial, model=flight.drone_model, cycle_count=0)
        db.add(battery)
        await db.flush()

    battery.cycle_count += 1
    battery.last_voltage = battery_data.get("end_voltage")

    log = BatteryLog(
        battery_id=battery.id,
        flight_id=flight.id,
        start_voltage=battery_data.get("start_voltage"),
        end_voltage=battery_data.get("end_voltage"),
        min_voltage=battery_data.get("min_voltage"),
        max_temp=battery_data.get("max_temp"),
        discharge_mah=battery_data.get("discharge_mah"),
        cycles_at_time=battery.cycle_count,
    )
    db.add(log)


async def _track_battery_from_odl(
    db: AsyncSession, flight: Flight, serial: str, custom_name: str, drone_model: str | None
):
    """Auto-create/update battery record from ODL flight data."""
    if not serial:
        return

    # Look up by actual hardware serial
    result = await db.execute(select(Battery).where(Battery.serial == serial))
    battery = result.scalar_one_or_none()

    # Migration: if battery was previously stored with custom_name as serial, find and fix it
    if not battery and custom_name:
        result2 = await db.execute(select(Battery).where(Battery.serial == custom_name))
        battery = result2.scalar_one_or_none()
        if battery:
            # Fix: move the custom name to the name field, restore actual serial
            battery.name = custom_name
            battery.serial = serial

    if not battery:
        battery = Battery(serial=serial, name=custom_name or None, model=drone_model, cycle_count=0)
        db.add(battery)
        await db.flush()
    else:
        # Always update name from ODL if provided (ODL is source of truth for display names)
        if custom_name:
            battery.name = custom_name
        # Update drone model if not set
        if drone_model and not battery.model:
            battery.model = drone_model

    battery.cycle_count += 1

    log = BatteryLog(
        battery_id=battery.id,
        flight_id=flight.id,
        cycles_at_time=battery.cycle_count,
    )
    db.add(log)


def _export_csv(flight: Flight, track: list) -> StreamingResponse:
    import csv
    import io

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["latitude", "longitude", "altitude_m", "speed_ms", "timestamp"])
    for pt in track:
        if isinstance(pt, dict):
            writer.writerow([pt.get("lat"), pt.get("lng"), pt.get("alt", 0),
                           pt.get("speed", ""), pt.get("timestamp", "")])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{flight.name}.csv"'},
    )


def _export_gpx(flight: Flight, track: list) -> StreamingResponse:
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<gpx version="1.1" creator="DroneOpsCommand">',
        f'  <trk><name>{flight.name}</name><trkseg>',
    ]
    for pt in track:
        if isinstance(pt, dict):
            lat, lon, alt = pt.get("lat", 0), pt.get("lng", 0), pt.get("alt", 0)
            ts = pt.get("timestamp", "")
            time_tag = f"<time>{ts}</time>" if ts else ""
            lines.append(f'    <trkpt lat="{lat}" lon="{lon}"><ele>{alt}</ele>{time_tag}</trkpt>')
    lines.append("  </trkseg></trk>")
    lines.append("</gpx>")

    content = "\n".join(lines)
    return StreamingResponse(
        iter([content]),
        media_type="application/gpx+xml",
        headers={"Content-Disposition": f'attachment; filename="{flight.name}.gpx"'},
    )


def _export_kml(flight: Flight, track: list) -> StreamingResponse:
    coords = []
    for pt in track:
        if isinstance(pt, dict):
            coords.append(f"{pt.get('lng', 0)},{pt.get('lat', 0)},{pt.get('alt', 0)}")

    content = f"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <name>{flight.name}</name>
    <Placemark>
      <name>{flight.name}</name>
      <Style><LineStyle><color>ffff6600</color><width>3</width></LineStyle></Style>
      <LineString>
        <altitudeMode>relativeToGround</altitudeMode>
        <coordinates>{" ".join(coords)}</coordinates>
      </LineString>
    </Placemark>
  </Document>
</kml>"""

    return StreamingResponse(
        iter([content]),
        media_type="application/vnd.google-earth.kml+xml",
        headers={"Content-Disposition": f'attachment; filename="{flight.name}.kml"'},
    )
