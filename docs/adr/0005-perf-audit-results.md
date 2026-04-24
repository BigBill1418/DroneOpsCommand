# ADR-0005 — Performance Audit Results (2026-04-24)

**Status:** in-progress (becomes `accepted` once FIX-1..4 deployed and AFTER measurements pass)
**Date:** 2026-04-24
**Companion:** ADR-0004 (BEFORE state) and `docs/plans/2026-04-24-perf-audit.md`

---

## Context

Aegis is executing the 4 ranked perf fixes from the 2026-04-24 audit.
Each fix is a separate commit with its own patch-version bump
(v2.63.7 → v2.63.10) plus a docs-final commit (v2.63.11).

This ADR captures the AFTER measurements for each fix. Each subsection
below is appended as the corresponding fix lands on `main` and BOS-HQ
finishes autopulling it.

The BEFORE numbers all live in ADR-0004 §"BEFORE measurements". The
shape of each subsection mirrors §6 of the plan so a future audit can
diff the same metric over time.

---

## FIX-1 — Weather endpoint: `asyncio.gather` + Redis cache

**Commit:** _filled in by aegis once pushed_
**Version:** v2.63.7
**Files changed:** `backend/app/routers/weather.py`,
`backend/app/services/cache.py` (new), `backend/tests/test_weather_cache.py` (new).

### BEFORE (from ADR-0004)
- /api/weather/current p95: **7.4 - 8.3 s** (verified 2026-04-24 23:01-23:02 production logs).
- 5 sequential awaits, no caching.

### AFTER
_pending — will be appended after BOS-HQ deploy._

```text
[ pending ]
```

### Acceptance
- ✅ if cold-cache p95 < 3.0 s
- ✅ if warm-cache p95 < 100 ms
- ❌ rollback otherwise (per plan §6 acceptance thresholds)

---

## FIX-2 — Async DB pool 5+10 → 20+20 + cached `get_current_user`

_pending — populated when v2.63.8 deploys._

---

## FIX-3 — Frontend code-split 17 main pages + Vite `manualChunks`

_pending — populated when v2.63.9 deploys._

---

## FIX-4 — Client-side `useApiCache` hook + apply to Dashboard/Flights/Settings

_pending — populated when v2.63.10 deploys._

---

## Final summary (filled at end of session)

_pending — populated by aegis when all four fixes verified._

| Hot path | BEFORE p95 | AFTER p95 | Δ |
|----------|------------|-----------|---|
| Dashboard cold first-paint | _pending_ | _pending_ | _pending_ |
| Dashboard warm repeat-visit | _pending_ | _pending_ | _pending_ |
| Settings page first-paint | _pending_ | _pending_ | _pending_ |
| Weather endpoint cold | 7.4-8.3 s | _pending_ | _pending_ |
| Weather endpoint warm | 7.4-8.3 s | _pending_ | _pending_ |
| Frontend main-bundle gz | _pending_ | _pending_ | _pending_ |
