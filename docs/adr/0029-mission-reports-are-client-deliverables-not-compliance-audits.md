# ADR-0029 — Mission reports are client deliverables, not compliance audits: remove altitude-limit / Part-107 exceedance commentary

- **Status:** Accepted
- **Date:** 2026-06-29
- **Supersedes:** the **H1** portion of [ADR-0028](0028-flight-data-integrity-outlier-gate-transaction-and-truthfulness.md) ("Part-107 altitude truthfulness"). ADR-0028 H2 (live-scalar reporting), M8 (ghost/aborted-launch handling), C1/C2/H3/H4 are unaffected and remain in force.

## Context

ADR-0028 H1 added per-flight altitude-limit logic to the mission-report
engine: any flight whose recorded `max_altitude` exceeded 121.92 m (400 ft AGL)
was flagged, the LLM system prompt was instructed to "state the altitude and
the fact of exceedance," and the report counted/listed the over-limit flights.

In production this produced the following language in a **client-facing**
after-action report for the Savannah Bananas mission
(`e5f3aedf-c3a2-46d2-9438-33a3cb8f3f8f`, v2.76.0):

> "A number of flights operated above 400 ft AGL — specifically Flights
> 2, 4, 5, 6, 7, 8, 9, 11, 15, 19, 20, 21, 22, 23, and 26 — and as such
> exceeded the standard Part 107 400 ft AGL altitude limit."

This is damaging and wrong to put in a client deliverable:

1. **A mission report is a client deliverable, not a compliance audit.** The
   client commissioned aerial work; they did not commission — and should never
   receive — an unsolicited regulatory self-assessment of the operator's flying.
2. **Whether an altitude was permissible is the certificated operator's
   determination.** `max_altitude` is launch-relative AGL and says nothing about
   the ground reference, any LAANC/waiver authorization, structure proximity
   (§107.51(b) allows flight within 400 ft of a structure above its limit), or
   the operator's actual authorization. The report engine cannot know any of
   this, so any "exceeded the limit" claim it emits is, at best, unfounded and,
   at worst, a false admission of a violation against the operator.
3. ADR-0028 H1 conflated *data truthfulness* (a good goal — present accurate
   altitude values, correct units) with *compliance editorializing* (out of
   scope, harmful). The former stays; the latter is removed.

## Decision

**The mission-report narrative must NEVER announce, flag, list, compute, or
comment on whether any flight exceeded an altitude limit, the 400 ft AGL
ceiling, or any Part-107 / regulatory limit.** The only permitted compliance
framing is the existing positive statement that operations were "conducted in
accordance with FAA Part 107 procedures." Altitude is presented purely as
neutral capture data (a value/range with correct units) with no reference to any
limit, ceiling, threshold, or Part-107.

Concretely:

- **Report builder (`backend/app/routers/reports.py`).** Removed the
  `PART_107_CEILING_M` constant, the `over_400ft` per-flight flag, the
  `over_400ft` summary field, the `over_400ft_count` tally + its log line, and
  the `" — exceeds the 400 ft AGL Part 107 limit"` annotation. The `ceiling-
  limited` annotation (which referenced a "device ceiling") was replaced by a
  neutral data-confidence note. The **only** retained altitude caveat is a
  data-quality flag (`unverified_peak`) for OpenDroneLog rows pinned at the
  device-reported ~500 m maximum, rendered as
  `" — unverified (device-reported maximum, not a measured peak)"`. This makes
  no reference to any limit, regulatory ceiling, threshold, or Part-107 — it
  exists only so an ODL artifact is not presented as an achieved altitude.

- **LLM system prompt (`backend/app/services/ollama.py`, shared by both the
  Ollama and Claude paths via `claude_llm.py`).** The clause that told the model
  to "state the altitude and the fact of exceedance" was replaced with an
  explicit prohibition: the model must NOT mention, compare against, or flag any
  altitude limit / the 400 ft ceiling / Part-107 altitude rules; must NOT state
  or imply any flight exceeded an altitude; must NOT list/rank flights by
  altitude; and must treat altitude as neutral operational statistics. The
  single permitted positive framing ("in accordance with FAA Part 107
  procedures") is explicitly preserved.

- **Runtime guard (`backend/app/services/report_audience.py`).** The existing
  post-generation audience-leak detector (already wired into
  `celery_tasks._apply_audience_findings`, setting `Report.has_audience_leak`
  for the editorial gate) was extended with deterministic altitude-limit rules.
  If the model ever re-emits exceedance/limit/Part-107-ceiling language, the
  report is flagged before delivery. The rules are tuned to fire on
  exceedance/limit framing only — they do not match neutral altitude data
  ("190.8 m AGL (626 ft)") nor the positive Part-107 framing. No schema change:
  the existing `has_audience_leak` boolean is reused.

## Consequences

- Client reports no longer editorialize about altitude limits. The operator
  (Bill, the certificated Part-107 PIC) makes any compliance determination
  out of band; the deliverable stays a deliverable.
- Defense in depth: prompt prohibition (primary) + deterministic detector
  (secondary, fail-flag at the editorial gate). A regression now fails a test
  AND flags at runtime.
- Accurate units and the v2.74.0 altitude *display* fix are retained — this ADR
  removes limit/exceedance *commentary*, not altitude data.

## Failover & Resilience Guard

No impact. This is a code/prompt-only change in the report-generation path. No
schema migration (the `has_audience_leak` column predates this change and is
reused), no port bindings, no connection strings, no replication / blue-green /
failover-engine surface touched. Survives container recreation (baked into the
image); no init-script or volume dependency.
