import asyncio
import logging
import os
import time
from datetime import datetime
from functools import partial
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.jwt import get_current_user
from app.config import settings
from app.database import get_db
from app.models.flight import Flight
from app.models.invoice import Invoice
from app.models.mission import Mission, MissionFlight
from app.models.report import Report
from app.models.user import User
from app.schemas.report import ReportGenerateRequest, ReportResponse, ReportUpdateRequest
from app.services.map_renderer import render_static_map
from app.services.mission_tracks import calculate_mission_area_acres, load_bounded_flight_tracks
from app.services.pdf_generator import generate_pdf

logger = logging.getLogger("doc.reports")

router = APIRouter(prefix="/api/missions", tags=["reports"])


async def _load_mission_with_flights(db: AsyncSession, mission_id: UUID) -> Mission:
    """Load a mission with flights and aircraft eagerly loaded."""
    result = await db.execute(
        select(Mission)
        .where(Mission.id == mission_id)
        .options(
            selectinload(Mission.flights).selectinload(MissionFlight.aircraft),
            selectinload(Mission.customer),
            selectinload(Mission.images),
        )
    )
    mission = result.scalar_one_or_none()
    if not mission:
        raise HTTPException(status_code=404, detail="Mission not found")
    return mission


# ADR-0026 RC-1 defence: even with the DB unique guard (migration 0003) and the
# idempotent attach path, the report engine NEVER trusts the row set to be
# duplicate-free. A stray duplicate (legacy data, a race that slips the guard)
# must not re-inflate a report, so every aggregate iterates over flights uniqued
# by their identity key.
def _flight_key(f: MissionFlight) -> tuple[str, str]:
    if f.flight_id is not None:
        return ("fid", str(f.flight_id))
    if f.opendronelog_flight_id:
        return ("odl", str(f.opendronelog_flight_id))
    return ("id", str(f.id))


def _dedup_flights(flights) -> list[MissionFlight]:
    """Return flights uniqued by identity key, preserving first-seen order."""
    seen: set[tuple[str, str]] = set()
    out: list[MissionFlight] = []
    for f in flights:
        key = _flight_key(f)
        if key in seen:
            continue
        seen.add(key)
        out.append(f)
    return out


# Metres → feet. DJI logs altitude in METRES natively (flight-parser dji.rs:50
# "meters AGL"; airdata.rs converts feet→metres on ingest). Flight.max_altitude
# and the scalar cache are therefore METRES (ADR-0026). The legacy report
# printed the raw metre value with a bare "ft" label — understating altitude by
# ~3.28× AND mislabeling the unit. Altitude is now formatted in code with an
# explicit, correct unit so the LLM never guesses or appends a unit.
METERS_TO_FEET = 3.28084

# Ghost / aborted-launch threshold. A real survey flight has meaningful airtime
# AND ground track; an aborted launch is a few seconds with ~zero travel and a
# 0 m max altitude that drags altitude ranges and averages (ADR-0026). A flight
# is a "ghost" only when BOTH are tiny, so a legitimate short hop is never
# excluded.
GHOST_MAX_DURATION_S = 30.0
GHOST_MAX_DISTANCE_M = 10.0

# ADR-0029 reverses ADR-0028 H1: mission reports are CLIENT DELIVERABLES, not
# compliance audits. The report engine must NEVER announce, flag, list, compute,
# or comment on whether any flight exceeded an altitude limit, the 400 ft AGL
# ceiling, or any Part-107 / regulatory limit. Whether an altitude was permissible
# is the certificated operator's determination, supplied (if at all) separately —
# it is not editorialized in a client-facing narrative. Altitude is presented as
# neutral capture data only. No 400 ft / Part-107 / exceedance logic lives here.

# ADR-0031 retires the former "unverified ODL peak" data-quality caveat. Ground-
# truth verification (570/570 OpenDroneLog flights with per-point gps_track: stored
# max_altitude matched the track peak within 1 m, max diff 0.4 m, zero stored>track;
# the ~500 m flights each carry hundreds of real GPS points at 499–500 m AGL). ODL
# max_altitude is an accurate achieved peak AGL value and is now presented plainly,
# like any other source. (This does NOT reintroduce any altitude-limit / 400 ft /
# Part-107 exceedance commentary — that prohibition, ADR-0029, stays intact.)


def _cache_num(cache: dict, *keys) -> float | None:
    """First numeric value among ``keys`` in ``cache``; None if none present."""
    for k in keys:
        v = cache.get(k)
        if isinstance(v, (int, float)):
            return v
    return None


def _cache_duration_secs(cache: dict) -> float:
    return _cache_num(cache, "duration_secs", "durationSecs", "duration_seconds", "flight_time_seconds") or 0.0


def _cache_distance_m(cache: dict) -> float:
    return _cache_num(cache, "total_distance", "totalDistance", "distance", "distance_meters") or 0.0


def _cache_max_altitude_m(cache: dict) -> float | None:
    return _cache_num(cache, "max_altitude", "maxAltitude", "max_height")


def _is_ghost(cache: dict) -> bool:
    return (
        _cache_duration_secs(cache) < GHOST_MAX_DURATION_S
        and _cache_distance_m(cache) < GHOST_MAX_DISTANCE_M
    )


def _format_altitude(alt_m: float | None) -> str:
    if alt_m is None:
        return "Unknown"
    return f"{alt_m:.1f} m AGL ({alt_m * METERS_TO_FEET:.0f} ft)"


async def _load_live_flight_metrics(db: AsyncSession, mission: Mission) -> dict:
    """Map ``Flight.id`` → live scalar metrics for every native flight on the
    mission (ADR-0028 H2).

    Reports/PDF/LLM previously read the attach-time ``flight_data_cache``
    snapshot. Drift is 0 today, but any future live correction (the C1 distance
    sanitize, a re-stamp) would silently desync every report. We now read the
    authoritative live ``Flight`` scalars — selecting only scalar COLUMNS so the
    heavy ``gps_track`` / ``telemetry`` / ``raw_metadata`` JSON is never loaded
    (ADR-0019). Legacy-ODL rows (``flight_id IS NULL``) keep using the cache.
    """
    ids = [mf.flight_id for mf in mission.flights if mf.flight_id is not None]
    if not ids:
        return {}
    rows = await db.execute(
        select(
            Flight.id, Flight.duration_secs, Flight.total_distance,
            Flight.max_altitude, Flight.max_speed, Flight.source, Flight.notes,
            # drone_model / drone_name resolve the aircraft LABEL when a flight
            # has no linked fleet aircraft (aircraft_id NULL) — so an attached
            # flight whose aircraft is unrecognized is still named by its parsed
            # model instead of being dropped/genericized to "Unknown" (Avata-2
            # defect, 2026-07). Both are scalar VARCHARs — the heavy JSON
            # (gps_track/telemetry/raw_metadata) is still never loaded (ADR-0019).
            Flight.drone_model, Flight.drone_name,
        ).where(Flight.id.in_(ids))
    )
    return {r.id: r for r in rows.all()}


def _resolve_flight_metrics(mf: MissionFlight, live: dict) -> dict:
    """Resolve one flight's metrics, preferring live ``Flight`` scalars (H2),
    falling back to the cache only for legacy-ODL rows. Also derives the M8
    ghost flag. NO altitude-limit / Part-107 / 400 ft exceedance flag is derived
    here (ADR-0029), and no ODL "unverified peak" caveat either (ADR-0031)."""
    if mf.flight_id is not None and mf.flight_id in live:
        r = live[mf.flight_id]
        dur = float(r.duration_secs or 0.0)
        dist = float(r.total_distance or 0.0)
        alt = r.max_altitude
        source = r.source
        notes = r.notes
    else:
        cache = mf.flight_data_cache or {}
        dur = _cache_duration_secs(cache)
        dist = _cache_distance_m(cache)
        alt = _cache_max_altitude_m(cache)
        source = cache.get("source") or "opendronelog_import"
        notes = cache.get("notes")
    is_ghost = dur < GHOST_MAX_DURATION_S and dist < GHOST_MAX_DISTANCE_M
    return {
        "duration_secs": dur, "distance_m": dist, "max_altitude_m": alt,
        "source": source, "notes": notes, "is_ghost": is_ghost,
    }


def _aircraft_label(mf: MissionFlight, live: dict) -> str:
    """Resolve the display label for a flight's aircraft, robust to an
    unrecognized / unlinked fleet aircraft.

    An attached flight whose ``aircraft_id`` is NULL — e.g. it carries a serial
    the fleet record lacks, so the strict serial-match path (ADR-0007) left it
    unattributed — must NEVER be dropped from, or genericized to "Unknown" in,
    the client report. The flight already knows its own model from the parsed
    ``Flight.drone_model`` (native rows) or the attach-time cache snapshot
    (legacy-ODL rows). Resolution order, most-authoritative first:

      1. Linked fleet aircraft's canonical ``model_name`` (e.g. "DJI Avata 2").
      2. Live ``Flight.drone_name`` (operator nickname), then ``drone_model``.
      3. Cache ``drone_name`` → ``drone_model`` → ``aircraft`` (legacy-ODL rows).
      4. "Unknown" — only when no model is available anywhere.

    Field defect this closes: the DJI Avata 2 on the 2026-07-02 "Springfield
    Drifters Promo" mission vanished from the report because the code read only
    ``mf.aircraft`` and fell straight to "Unknown".
    """
    if mf.aircraft is not None and mf.aircraft.model_name:
        return mf.aircraft.model_name
    if mf.flight_id is not None and mf.flight_id in live:
        r = live[mf.flight_id]
        label = (getattr(r, "drone_name", None) or "").strip() or (getattr(r, "drone_model", None) or "").strip()
        if label:
            return label
    cache = mf.flight_data_cache or {}
    for key in ("drone_name", "drone_model", "aircraft"):
        val = cache.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return "Unknown"


def _build_flight_summaries(mission: Mission, live: dict) -> list[dict]:
    """Per-flight summaries for the LLM — deduplicated, live-scalar metrics
    (H2), unit-correct altitude, and aborted launches flagged so they don't
    pollute altitude ranges (ADR-0026). Altitude is neutral capture data only:
    NO altitude-limit, 400 ft, regulatory-ceiling, or Part-107 exceedance flag
    is ever attached (ADR-0029, reversing ADR-0028 H1). ODL altitude is a verified
    achieved peak and carries no data-quality caveat (ADR-0031)."""
    summaries = []
    for f in _dedup_flights(mission.flights):
        m = _resolve_flight_metrics(f, live)
        aircraft = _aircraft_label(f, live)
        if m["is_ghost"]:
            summaries.append({
                "aircraft": aircraft,
                "max_altitude": "N/A — aborted launch",
                "aborted": True,
                "notes": (
                    f"Aborted launch ({m['duration_secs']:.0f}s, "
                    f"{m['distance_m']:.1f} m travelled) — exclude from altitude ranges."
                ),
            })
            continue
        alt_str = _format_altitude(m["max_altitude_m"])
        summary = {
            "aircraft": aircraft,
            "max_altitude": alt_str,
        }
        if m["notes"]:
            summary["notes"] = m["notes"]
        summaries.append(summary)
    return summaries


@router.get("/{mission_id}/report", response_model=ReportResponse)
async def get_report(
    mission_id: UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    result = await db.execute(select(Report).where(Report.mission_id == mission_id))
    report = result.scalar_one_or_none()
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    return report


@router.post("/{mission_id}/report/generate")
async def generate_report(
    mission_id: UUID,
    data: ReportGenerateRequest,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Kick off LLM report generation as a background task."""
    from app.tasks.celery_tasks import generate_report_task

    logger.info("Report generation requested for mission %s", mission_id)
    start = time.perf_counter()

    mission = await _load_mission_with_flights(db, mission_id)
    logger.info("Mission %s loaded: %d flights", mission_id, len(mission.flights))

    loop = asyncio.get_running_loop()

    # ADR-0026 RC-2: AREA is a MEASUREMENT and must run on FULL-RESOLUTION
    # geometry — not the 2000-vertex strided decimation used for the map. The
    # streaming measurement loader loads each flight's full-res track on demand,
    # one at a time, simplifying (shape-preserving DP) + buffering each before
    # the next is materialized, so the ADR-0025 OOM fix is preserved.
    try:
        acres = await calculate_mission_area_acres(db, mission)
        logger.info("Mission %s area (full-res): %.2f acres (%.2fs)",
                     mission_id, acres, time.perf_counter() - start)
    except Exception as exc:
        logger.error("Mission %s area computation failed: %s", mission_id, exc)
        acres = 0.0

    # MAP render uses the cheap DECIMATED path (sub-pixel detail is wasted on an
    # 800×600 PNG). ADR-0025: never holds more than one raw track at a time.
    flights = await load_bounded_flight_tracks(db, mission)

    # H2: resolve live Flight scalars once; summaries + totals both use them.
    live_metrics = await _load_live_flight_metrics(db, mission)
    flight_summaries = _build_flight_summaries(mission, live_metrics)

    map_path = None
    if flights:
        try:
            map_path = await loop.run_in_executor(None, render_static_map, flights)
            logger.info("Mission %s map rendered: %s (%.2fs)",
                         mission_id, map_path, time.perf_counter() - start)
        except Exception as exc:
            logger.error("Mission %s map render failed: %s", mission_id, exc)

    # Duration/distance totals — summed over flights uniqued by identity key so
    # a stray duplicate row can never re-inflate the totals (ADR-0026 RC-1),
    # from LIVE scalars (H2), with aborted launches EXCLUDED uniformly (ADR-0028
    # M8) so a ghost's near-zero values never enter the totals or flight count.
    total_duration = 0
    total_distance = 0
    real_flight_count = 0
    aborted_count = 0
    for f in _dedup_flights(mission.flights):
        m = _resolve_flight_metrics(f, live_metrics)
        if m["is_ghost"]:
            aborted_count += 1
            continue
        real_flight_count += 1
        total_duration += m["duration_secs"]
        total_distance += m["distance_m"]
    logger.info(
        "Mission %s report metrics: %d flights (%d aborted excluded)",
        mission_id, real_flight_count, aborted_count,
    )

    # Ensure a report record exists so the frontend can poll GET /report
    existing = await db.execute(select(Report).where(Report.mission_id == mission_id))
    report = existing.scalar_one_or_none()
    if report:
        report.user_narrative = data.user_narrative
        report.include_download_link = data.include_download_link
        report.ground_covered_acres = acres if acres > 0 else None
        report.flight_duration_total_seconds = total_duration if total_duration > 0 else None
        report.flight_distance_total_meters = total_distance if total_distance > 0 else None
        report.map_image_path = map_path
    else:
        report = Report(
            mission_id=mission_id,
            user_narrative=data.user_narrative,
            include_download_link=data.include_download_link,
            ground_covered_acres=acres if acres > 0 else None,
            flight_duration_total_seconds=total_duration if total_duration > 0 else None,
            flight_distance_total_meters=total_distance if total_distance > 0 else None,
            map_image_path=map_path,
        )
        db.add(report)
    await db.flush()

    # Load company name for LLM prompt
    from app.routers.system_settings import get_branding as _get_brand
    _brand = await _get_brand(db)

    # Dispatch to Celery worker
    task = generate_report_task.delay(
        mission_id=str(mission_id),
        user_narrative=data.user_narrative,
        mission_title=mission.title,
        mission_type=mission.mission_type.value,
        mission_date=str(mission.mission_date) if mission.mission_date else None,
        location=mission.location_name or "Not specified",
        flight_summaries=flight_summaries,
        ground_covered_acres=acres if acres > 0 else None,
        total_duration=total_duration,
        total_distance=total_distance,
        map_path=map_path,
        company_name=_brand.get("company_name", "DroneOps"),
    )

    elapsed = time.perf_counter() - start
    logger.info("Mission %s report task dispatched: %s (%.2fs)", mission_id, task.id, elapsed)

    return {"task_id": task.id, "status": "generating"}


@router.get("/{mission_id}/report/status/{task_id}")
async def get_generation_status(
    mission_id: UUID,
    task_id: str,
    _user: User = Depends(get_current_user),
):
    """Poll the status of a background report generation task."""
    from app.tasks.celery_tasks import celery_app

    result = celery_app.AsyncResult(task_id)

    if result.state == "PENDING":
        return {"status": "generating"}
    elif result.state == "SUCCESS":
        return {"status": "complete"}
    elif result.state == "FAILURE":
        logger.error("Report task %s failed: %s", task_id, result.result)
        return {"status": "failed", "detail": str(result.result)}
    else:
        return {"status": "generating"}


@router.put("/{mission_id}/report", response_model=ReportResponse)
async def update_report(
    mission_id: UUID,
    data: ReportUpdateRequest,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    result = await db.execute(select(Report).where(Report.mission_id == mission_id))
    report = result.scalar_one_or_none()

    if report:
        for key, value in data.model_dump(exclude_unset=True).items():
            setattr(report, key, value)
    else:
        # Create a new report record (draft save before generation)
        report = Report(mission_id=mission_id, **data.model_dump(exclude_unset=True))
        db.add(report)

    await db.flush()
    await db.refresh(report)
    return report


@router.post("/{mission_id}/report/pdf")
async def generate_report_pdf(
    mission_id: UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Generate PDF from the report."""
    logger.info("PDF generation requested for mission %s", mission_id)
    start = time.perf_counter()

    result = await db.execute(
        select(Mission)
        .where(Mission.id == mission_id)
        .options(
            selectinload(Mission.flights).selectinload(MissionFlight.aircraft),
            selectinload(Mission.images),
            selectinload(Mission.customer),
            selectinload(Mission.invoice).selectinload(Invoice.line_items),
        )
    )
    mission = result.scalar_one_or_none()
    if not mission:
        raise HTTPException(status_code=404, detail="Mission not found")

    report_result = await db.execute(select(Report).where(Report.mission_id == mission_id))
    report = report_result.scalar_one_or_none()
    if not report:
        raise HTTPException(status_code=404, detail="Report not generated yet")

    # Build data for PDF template
    mission_dict = {
        "id": str(mission.id),
        "title": mission.title,
        "mission_type": mission.mission_type.value,
        "description": mission.description,
        "mission_date": str(mission.mission_date) if mission.mission_date else "",
        "location_name": mission.location_name or "",
        "customer_name": mission.customer.name if mission.customer else "",
        "customer_email": mission.customer.email if mission.customer else "",
        "customer_company": mission.customer.company if mission.customer else "",
    }

    # M8 (ADR-0028): the PDF flight count must match the report totals — count
    # only real (non-aborted) flights, using live scalars (H2).
    pdf_live = await _load_live_flight_metrics(db, mission)
    real_flights = [
        f for f in _dedup_flights(mission.flights)
        if not _resolve_flight_metrics(f, pdf_live)["is_ghost"]
    ]
    report_dict = {
        "final_content": report.final_content or "",
        "ground_covered_acres": report.ground_covered_acres,
        "flight_duration_total_seconds": report.flight_duration_total_seconds,
        "flight_distance_total_meters": report.flight_distance_total_meters,
        "map_image_path": report.map_image_path,
        "flight_count": len(real_flights),
    }

    # Aircraft used
    aircraft_list = []
    seen_aircraft = set()
    bundled_aircraft_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "aircraft")
    for f in mission.flights:
        if f.aircraft and f.aircraft.id not in seen_aircraft:
            seen_aircraft.add(f.aircraft.id)
            # Resolve full image path for WeasyPrint
            img_path = None
            if f.aircraft.image_filename:
                upload_path = os.path.join(settings.upload_dir, f.aircraft.image_filename)
                bundled_path = os.path.join(bundled_aircraft_dir, f.aircraft.image_filename)
                if os.path.isfile(upload_path):
                    img_path = upload_path
                elif os.path.isfile(bundled_path):
                    img_path = bundled_path
            aircraft_list.append({
                "model_name": f.aircraft.model_name,
                "manufacturer": f.aircraft.manufacturer,
                "image_path": img_path,
                "specs": f.aircraft.specs,
            })
        elif not f.aircraft:
            # Attached flight with an unrecognized / unlinked fleet aircraft
            # (aircraft_id NULL). Still list it in "Aircraft used" by its parsed
            # model so the deliverable never silently omits an aircraft that
            # flew (Avata-2 defect, 2026-07). No fleet record → no bundled image
            # or specs; the template already renders those as absent.
            fl = getattr(f, "flight", None)
            label = ""
            if fl is not None:
                label = (getattr(fl, "drone_name", None) or "").strip() or (getattr(fl, "drone_model", None) or "").strip()
            if not label:
                cache = f.flight_data_cache or {}
                for key in ("drone_name", "drone_model", "aircraft"):
                    val = cache.get(key)
                    if isinstance(val, str) and val.strip():
                        label = val.strip()
                        break
            label_key = ("label", label.lower()) if label else None
            if label and label_key not in seen_aircraft:
                seen_aircraft.add(label_key)
                aircraft_list.append({
                    "model_name": label,
                    "manufacturer": "DJI" if label.upper().startswith("DJI") else "",
                    "image_path": None,
                    "specs": {},
                })

    # Invoice
    invoice_dict = None
    if mission.invoice:
        invoice_dict = {
            "invoice_number": mission.invoice.invoice_number,
            "subtotal": float(mission.invoice.subtotal),
            "tax_rate": float(mission.invoice.tax_rate),
            "tax_amount": float(mission.invoice.tax_amount),
            "total": float(mission.invoice.total),
            "paid_in_full": mission.invoice.paid_in_full,
            "notes": mission.invoice.notes,
            "line_items": [
                {
                    "description": li.description,
                    "category": li.category.value,
                    "quantity": float(li.quantity),
                    "unit_price": float(li.unit_price),
                    "total": float(li.total),
                }
                for li in mission.invoice.line_items
            ],
        }

    # Load payment links for invoice
    payment_links = {}
    stripe_pay_url: str | None = None
    if mission.is_billable and invoice_dict and not invoice_dict.get("paid_in_full"):
        from app.models.system_settings import SystemSetting
        pl_result = await db.execute(
            select(SystemSetting).where(SystemSetting.key.in_(["paypal_link", "venmo_link"]))
        )
        payment_links = {r.key: r.value for r in pl_result.scalars().all()}

        # Reuses ADR-0011 idempotent client-link logic — repeated PDF renders
        # do not mint duplicate magic links. Only mint a Stripe pay URL when
        # there is a non-zero balance to collect; a $0 invoice or a missing
        # customer leaves stripe_pay_url=None and the template falls through
        # to the legacy PayPal/Venmo block (or hides the section entirely).
        if float(invoice_dict.get("total") or 0) > 0:
            from app.routers.client_portal import (
                build_client_portal_url,
                get_or_mint_active_client_link,
            )

            try:
                minted = await get_or_mint_active_client_link(
                    db, mission_id, days=30
                )
                if minted is not None:
                    client_jwt, _expires_at, _record = minted
                    stripe_pay_url = build_client_portal_url(client_jwt)
                else:
                    logger.info(
                        "[PDF-PAY-LINK] Skipping Stripe pay URL for mission=%s — no customer assigned",
                        mission_id,
                    )
            except Exception as exc:  # noqa: BLE001 — fail-soft on link-mint errors
                logger.warning(
                    "[PDF-PAY-LINK] Failed to mint Stripe pay URL for mission=%s: %s — PDF will render without it",
                    mission_id, exc,
                )

    # Images
    image_list = [{"file_path": img.file_path, "caption": img.caption} for img in mission.images]

    # Download link
    download_link = None
    if report.include_download_link and mission.download_link_url:
        download_link = {
            "url": mission.download_link_url,
            "expires_at": mission.download_link_expires_at.strftime("%B %d, %Y at %I:%M %p")
            if mission.download_link_expires_at
            else "N/A",
        }

    # Load branding for PDF template
    from app.routers.system_settings import get_branding
    branding = await get_branding(db)

    # Run WeasyPrint in thread executor — it's CPU-intensive and blocks
    loop = asyncio.get_running_loop()
    try:
        pdf_path = await loop.run_in_executor(
            None,
            partial(
                generate_pdf,
                mission=mission_dict,
                report=report_dict,
                invoice=invoice_dict if mission.is_billable else None,
                aircraft_list=aircraft_list,
                image_paths=image_list,
                payment_links=payment_links,
                stripe_pay_url=stripe_pay_url,
                download_link=download_link,
                branding=branding,
            ),
        )
    except Exception as exc:
        logger.error("Mission %s PDF generation failed: %s", mission_id, exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"PDF generation failed: {exc}")

    report.pdf_path = pdf_path
    await db.flush()

    elapsed = time.perf_counter() - start
    logger.info("Mission %s PDF generated: %s (%.2fs)", mission_id, pdf_path, elapsed)

    return FileResponse(pdf_path, media_type="application/pdf", filename=f"report_{mission.title}.pdf")


@router.post("/{mission_id}/report/send")
async def send_report(
    mission_id: UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Send report PDF to customer via email."""
    from app.services.email_service import send_report_email

    mission = await _load_mission_with_flights(db, mission_id)
    if not mission.customer or not mission.customer.email:
        raise HTTPException(status_code=400, detail="Customer email not set")

    report_result = await db.execute(select(Report).where(Report.mission_id == mission_id))
    report = report_result.scalar_one_or_none()
    if not report or not report.pdf_path:
        raise HTTPException(status_code=400, detail="PDF not generated yet")

    # Build download link for email
    download_link = None
    if report.include_download_link and mission.download_link_url:
        download_link = {
            "url": mission.download_link_url,
            "expires_at": mission.download_link_expires_at.strftime("%B %d, %Y at %I:%M %p")
            if mission.download_link_expires_at
            else "N/A",
        }

    logger.info("Sending report for mission %s to %s", mission_id, mission.customer.email)
    try:
        await send_report_email(
            to_email=mission.customer.email,
            customer_name=mission.customer.name,
            mission_title=mission.title,
            pdf_path=report.pdf_path,
            db=db,
            download_link=download_link,
        )
    except Exception as exc:
        logger.error("Mission %s email send failed: %s", mission_id, exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Email delivery failed")

    report.sent_at = datetime.utcnow()
    mission.status = "sent"
    await db.flush()

    logger.info("Mission %s report sent successfully", mission_id)
    return {"message": "Report sent successfully"}
