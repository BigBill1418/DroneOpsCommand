# ADR-0031 — OpenDroneLog max-altitude is a verified achieved peak: remove the "unverified peak" report caveat

- **Status:** Accepted
- **Date:** 2026-06-30
- **Closes:** the open data-quality item left by [ADR-0029](0029-mission-reports-are-client-deliverables-not-compliance-audits.md). ADR-0029 stripped all altitude-limit / Part-107 exceedance commentary but left a defensive "unverified peak" caveat on ODL altitudes (a residue of the [ADR-0028](0028-flight-data-integrity-outlier-gate-transaction-and-truthfulness.md) H1 truthfulness work). This ADR retires that caveat. **ADR-0029's prohibition stays fully in force** — no altitude-limit / 400 ft / Part-107 commentary is reintroduced.

## Context

When ADR-0029 removed altitude-exceedance language from the report engine, it
left one defensive flag in `backend/app/routers/reports.py`: ODL-imported flights
whose `max_altitude` sat at the ~500 m DJI device ceiling were tagged in report
summaries as `" — unverified (device-reported maximum, not a measured peak)"`
(the `unverified_peak` flag in `_resolve_flight_metrics` / `_build_flight_summaries`,
guarded by the `_ODL_DEVICE_MAX_LOW_M` / `_ODL_DEVICE_MAX_HIGH_M` constants, plus a
matching clause in the LLM system prompt). The hypothesis was that a value pinned at
the device maximum was a reported artifact rather than a measured altitude, so the
narrative should not present it as an achieved peak.

That hypothesis was never validated against the per-point telemetry. This ADR
records the validation, which **disproves it**.

## Verification (authoritative DB, `droneops-standby-db` on BOS-HQ)

For every `source='opendronelog_import'` flight that stores a per-point
`gps_track` array, stored `max_altitude` was compared to the actual track peak
`max((point->>'alt')::float)`:

| Metric | Result |
| --- | --- |
| ODL flights with per-point tracks | **570** |
| Stored `max_altitude` matches track peak within 1 m | **570 / 570 (100%)** |
| Flights where stored exceeds track peak | **0** |
| Max absolute stored-vs-track difference | **0.4 m** |
| Flights at the ~500 m "device max" band (495–505 m) | 13 |

Each device-max flight carries **hundreds of genuine GPS points at 499–500 m AGL**
(e.g. one shows 1,724 of 2,147 points ≥ 499 m) — the drone genuinely flew to its
configured DJI 500 m limit; the peak is real, not a reported artifact.

**Conclusion:** ODL `max_altitude` is an accurate achieved-peak AGL value. The
"unverified (device-reported maximum, not a measured peak)" caveat put a *false*
data-quality disclaimer into a client deliverable and is removed.

## Decision

1. Remove the `unverified_peak` flag computation, the dict key, and the summary
   annotation string from `backend/app/routers/reports.py`, along with the now-unused
   `_ODL_DEVICE_MAX_LOW_M` / `_ODL_DEVICE_MAX_HIGH_M` constants and their comment block.
2. Remove the corresponding clause from the LLM system prompt
   (`SYSTEM_PROMPT_TEMPLATE` in `backend/app/services/ollama.py`, which
   `backend/app/services/claude_llm.py` imports — single source of truth, both
   providers covered).
3. ODL altitude is now presented as a plain, trustworthy value like any other source.

**Explicitly out of scope / preserved:** the ADR-0029 altitude-limit prohibition —
the prompt's STRICT PROHIBITIONS block, the `report_audience.py` runtime guard
patterns (`altitude_over_400ft`, `altitude_exceeds_limit`, `altitude_limit_phrase`,
`ft_ceiling`, `part_107_altitude_limit`), and their tests — remain unchanged. This
change removes only the data-confidence caveat, not any compliance/limit logic.

## Consequences

- Client reports present ODL altitudes accurately, with no false disclaimer.
- Tests that asserted the caveat existed are flipped to assert it is gone
  (`test_resolve_derives_no_unverified_peak_flag`,
  `test_summary_odl_peak_presented_plainly` in
  `backend/tests/test_reports_metrics_adr0028.py`); the obsolete clean-text guard
  case for the caveat string is dropped from
  `backend/tests/services/test_report_audience_guard.py`. ADR-0029 prohibition tests
  continue to pass. Full backend suite: 504 passed, 3 skipped.

## Failover & Resilience Guard self-check

Pure report-engine logic change (Python in the API/worker image). No DB schema,
port, replication, blue-green, or failover-engine impact. Survives container
recreation (code-only). No customer-facing service is affected during a site
failover.
