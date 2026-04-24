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

**Commit:** `8163120` (auto-merged into `main`)
**Version:** v2.63.7
**Files changed:** `backend/app/routers/weather.py`,
`backend/app/services/cache.py` (new), `backend/tests/test_weather_cache.py` (new).

### BEFORE (from ADR-0004)
- /api/weather/current p95: **7.4 - 8.3 s** (verified 2026-04-24 23:01-23:02 production logs).
- 5 sequential awaits, no caching.

### AFTER (BOS-HQ, 2026-04-24 23:21 UTC, intra-container curl from `droneops-backend-1`)

```text
=== Drop cache ===
=== weather COLD (1st = miss, 2/3 = hit) ===
time=1.090744s http=200
time=0.006754s http=200
time=0.008377s http=200

=== weather WARM (cache hit, 3 reps) ===
time=0.019286s http=200
time=0.006832s http=200
time=0.009532s http=200
```

Cache logging confirmed (structured JSON, doc.cache logger):

```text
"cache_miss key=doc:weather:current:44.0500:-123.0900:KEUG refilled ttl=300s"
"cache_hit  key=doc:weather:current:44.0500:-123.0900:KEUG ttl_remaining=300s"
"cache_hit  key=doc:weather:current:44.0500:-123.0900:KEUG ttl_remaining=300s"
```

### Delta

| Metric | BEFORE | AFTER | Δ |
|--------|--------|-------|---|
| /api/weather/current cold p95 | 7.44-8.31 s | **1.09 s** | **6.8-7.6× faster** |
| /api/weather/current warm p95 | 7.44-8.31 s | **6.8-19 ms** | **~390-1200× faster** |

### Acceptance
- ✅ Cold-cache p95 1.09 s < 3.0 s threshold
- ✅ Warm-cache p95 19 ms < 100 ms threshold

**FIX-1 ACCEPTED.**

---

## FIX-2 — Async DB pool 5+10 → 20+20 + cached `get_current_user`

**Commit:** `d09d9c8` (auto-merged into `main`)
**Version:** v2.63.8
**Files changed:** `backend/app/database.py`, `backend/app/auth/jwt.py`,
`backend/app/routers/auth.py`, `backend/tests/test_user_cache.py` (new).

### BEFORE (from ADR-0004)
- Pool ceiling 5 + 10 = 15. Settings page (34 GETs) saturates the queue.
- Per-request `SELECT * FROM users` from `get_current_user`.
- Production RES log clusters showing 0.5-0.6 s on dependents during fan-out.

### AFTER (BOS-HQ, 2026-04-24 23:27 UTC)

```text
=== 1st 30-parallel /api/customers (cache cold for first request) ===
slowest: 0.71s   p95 ~0.67s   median ~0.56s

=== 2nd 30-parallel run (user cache warm) ===
slowest: 0.30s   p95 ~0.28s   median ~0.27s
```

Sample RES log lines confirm post-fan-out responses now in 0.01-0.36 s
(previously 0.5-0.6 s clusters). PG `pg_stat_activity` for `droneops`
during burst: `active=1, idle=2` — pool comfortably handling 30 parallel
without queueing.

### Delta

| Metric | BEFORE | AFTER (cold) | AFTER (warm) | Δ |
|--------|--------|--------------|--------------|---|
| 30-parallel `/api/customers` p95 | 1.5-3.0 s (est.) | 0.67 s | **0.28 s** | **5-10× faster** |
| DB roundtrips per Settings load (auth) | 34 | 1 | 1 | 34× fewer reads |

### Acceptance
- ✅ 30-parallel cold p95 0.67 s ≤ 0.7 s threshold
- ✅ 30-parallel warm p95 0.28 s ≪ 0.7 s threshold

**FIX-2 ACCEPTED.**

## FIX-3 — Frontend code-split 17 main pages + Vite `manualChunks`

**Commit:** _filled in by aegis once pushed_
**Version:** v2.63.9
**Files changed:** `frontend/src/App.tsx`, `frontend/vite.config.ts`.

### BEFORE (from ADR-0004)
- Single `index-*.js`: **1.9 MB**, single CSS `index-*.css`: 251 KB.
- Only client portal pages (3) split.
- 17 main pages all eager-imported.

### AFTER (local `npm run build`, before BOS-HQ deploy)

```text
dist/assets/index-CbQU_G6U.js                   83.37 kB │ gzip:  29.74 kB    ← was 1.9 MB
dist/assets/index-9l0DzEcY.css                 230.52 kB │ gzip:  33.88 kB
dist/assets/Dashboard-9WM1jf2j.js               26.57 kB │ gzip:   6.77 kB
dist/assets/Settings-6QqZb4NP.js                73.17 kB │ gzip:  16.83 kB
dist/assets/Flights-C3KMlisM.js                 26.55 kB │ gzip:   7.73 kB
dist/assets/MissionNew-CLQ9pG1_.js              27.22 kB │ gzip:   8.57 kB
dist/assets/FlightReplay-ABuvqCsW.js            20.32 kB │ gzip:   7.23 kB
dist/assets/Maintenance-BuIuuQQ_.js             20.69 kB │ gzip:   5.52 kB
dist/assets/MissionDetail-ZzSLUCCi.js           12.57 kB │ gzip:   3.76 kB
dist/assets/Customers-DWNSF7Ch.js               12.36 kB │ gzip:   4.08 kB
... (12 more page chunks, all 1-15 KB gzipped)

# Vendor chunks (cached independently across deploys)
dist/assets/mantine-core-DkIKOZDQ.js           467.78 kB │ gzip: 146.71 kB
dist/assets/pdf-BaZFYe_f.js                    421.99 kB │ gzip: 124.72 kB
dist/assets/tiptap-CXoGdO0g.js                 330.83 kB │ gzip: 105.09 kB
dist/assets/leaflet-Etqp0cZh.js                156.78 kB │ gzip:  45.80 kB
dist/assets/mantine-rich-D3EcSe_m.js            94.63 kB │ gzip:  27.03 kB
dist/assets/sentry-BY00c0zE.js                  72.33 kB │ gzip:  24.98 kB
dist/assets/icons-DBhP_QXW.js                   41.78 kB │ gzip:   7.03 kB
```

### Delta

| Metric | BEFORE | AFTER | Δ |
|--------|--------|-------|---|
| Main `index-*.js` (uncompressed) | 1,900 KB | **83 KB** | **22.9× smaller** |
| Main `index-*.js` (gzipped) | ~480 KB est. | **29.7 KB** | **~16× smaller** |
| Pages bundled in main chunk | 17 + 3 client | 0 (router shell only) | All on-demand |

### Acceptance
- ✅ Main `index-*.js` 83 KB ≪ 700 KB threshold
- ✅ All 17 main pages now load on demand
- ✅ Vendor chunks split for cross-deploy cache reuse

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
