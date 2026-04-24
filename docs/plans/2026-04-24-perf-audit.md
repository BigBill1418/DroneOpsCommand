# Performance Audit & Repair Plan — DroneOpsCommand

**Date:** 2026-04-24
**Author:** Terry (audit) → Aegis (execute) → Code-Reviewer (verify)
**Repo HEAD at audit:** `e0295a1` (v2.63.6)
**Live host:** BOS-HQ (10.99.0.4)
**Audit scope:** READ-ONLY investigation — NO code touched. This document is the directive for the executor.

---

## 1. Executive Summary

**The system is healthy at the floor.** Backend p50 for an empty hot path is 2-7 ms intra-container; DB is 52 MB across all of 19 tables (4 flights, 0 missions, 0 customers); container memory is comfortable; load average on the host is 0.3-0.9; no error log noise.

**The lag Bill feels is shaped by four concrete patterns, in this order of impact:**

1. The Dashboard fires the **weather endpoint synchronously across 5 external aviation APIs** (Open-Meteo + AviationWeather METAR + AviationWeather TFRs + AviationWeather NOTAMs + NWS alerts), each with a 10-15s timeout, **with no caching**. Every render of `/api/weather/current` takes **7-8 seconds wall-clock** (verified in production logs `2026-04-24 23:01:42` and `23:02:12`). Dashboard auto-refreshes every 5 minutes regardless of viewer.
2. The **Settings page makes 34 separate `api.get()` calls** in a single `useEffect`, with no `Promise.all`, no React Query, no cache. Each authenticated call also costs one extra `SELECT * FROM users` (token validation). The shared async DB pool is **5 connections + 10 overflow = 15 max**. Settings load saturates the pool, queues, and produces the 0.5s clusters in the logs.
3. The **frontend is shipped as a single 1.9 MB / 251 KB CSS bundle** (the `index-*.js` chunk). Only `client/*` portal pages are code-split. Mantine + Tabler + Leaflet + react-pdf + @sentry/react + tiptap all share the operator's first paint. On a slow uplink or first-visit cold cache, this is the felt latency before the spinner ever resolves.
4. **Every page does fresh `api.get()` on every mount.** No client cache. Navigating `Dashboard → Flights → Dashboard` re-fetches all 6 dashboard endpoints from scratch every time. Combined with #2 this multiplies in the Settings ↔ Dashboard ↔ Flights triangle Bill uses most.

**Anti-summary:** the database is not the problem. There are no missing indexes that materially hurt today's data sizes; SQLAlchemy lazy-loading is already correctly configured (selectin/noload chosen per relation); the Postgres replication setup is healthy; the worker queue is idle; the host VPS has tons of headroom.

**Expected user-visible improvement after Plan A (fixes 1-4 only):**
- **Dashboard first paint p95: ~9.5 s → ~1.8 s** (cold network, cold cache).
- **Dashboard repeat-visit p95 (warm):** ~7.5 s → ~150 ms (Redis-cached weather + client cache hits).
- **Settings page first paint p95: ~3.2 s → ~600 ms** (parallelized + fewer auth round-trips).

These numbers are **estimates** built on top of the BEFORE measurements in §2; aegis must collect the AFTER measurements per §6 to validate.

---

## 2. Methodology — BEFORE Measurements (2026-04-24, ~23:00 UTC)

All measurements taken from BOS-HQ via `ssh bbarnard065@10.99.0.4`. Backend container is `droneops-backend-1`, DB is `droneops-standby-db` (now primary post-2026-04-20 migration).

### 2.1 Host
```
$ uptime
 23:02:58 up 3 days, 14:23,  1 user,  load average: 0.34, 0.85, 0.86
```
Host is not the bottleneck.

### 2.2 Container resources (representative sample)
```
NAME                       CPU %   MEM
droneops-backend-1         0.22%   874 MiB / 31 GiB
droneops-worker-1          0.52%   536 MiB / 31 GiB
droneops-standby-db        2.90%    76 MiB / 31 GiB
droneops-redis-1           0.35%    3.5 MiB / 31 GiB
droneops-frontend-1        0.00%    8.0 MiB / 31 GiB
```
No container is starved. Backend at 874 MiB is steady (worker reaper + init:true already in place per b140ee2 / 9ae3c95). 31 GiB ceiling means we have all the room we want to add Redis caching without contention.

### 2.3 Postgres state
```
$ docker exec droneops-standby-db psql -U droneops -d droneops -c "SELECT pg_size_pretty(pg_database_size('droneops'));"
 db_size : 52 MB

Top tables (n_live_tup, total size):
 flights               4 rows   43 MB    ← payload is gps_track JSON
 mission_flights       0 rows   928 KB
 customers             0 rows   160 KB
 battery_logs          4 rows   160 KB
 [all other tables]    0 rows   <64 KB
```
The 43 MB on `flights` is the GPS-track JSON column on 4 historical flights. That is fine. There is no row-volume issue to fix.

**Extensions installed:** `plpgsql` only. `pg_stat_statements` is **not loaded** — operator action needed (see §8 Open Questions, optional).

### 2.4 Index inventory
26 indexes across 16 tables — every PK + `*_key` UNIQUEs (auto-from-constraints). **Zero `index=True` on any model column** (verified `grep -nE "Index\(|index=True" backend/app/models/*.py`). Not a today problem at 52 MB; documented for future scale (§3 finding F-7).

### 2.5 SQLAlchemy pool
`backend/app/database.py`:
```python
engine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_recycle=1800,
    pool_size=5,        # ← bottleneck under fan-out
    max_overflow=10,    # ← total ceiling = 15
)
```

### 2.6 Endpoint latency from inside container (intra-container, real)
```
$ docker exec droneops-backend-1 curl -s -o /dev/null -w "%{time_total}\n" http://127.0.0.1:8000/api/health
0.002783
0.002192
0.001918
0.002746
0.001772
```
Backend floor is ~2 ms. The 200 ms I initially saw via SSH-shell `curl localhost:8000` was simply SSH RTT.

### 2.7 Real per-endpoint latency from `RES` log line (`backend/app/main.py:455-470` middleware)
Captured during a single Settings page load + dashboard render:
```
RES GET /api/auth/setup-status     0.03s
RES GET /api/auth/account          0.03s   (then 0.21s on repeat)
RES GET /api/branding              0.00s
RES GET /api/customers             0.05s   0.14s
RES GET /api/maintenance/due       0.07s   0.16s
RES GET /api/batteries             0.07s   0.15s
RES GET /api/maintenance/next-due  0.52s   0.15s
RES GET /api/missions              0.56s   0.15s
RES GET /api/flight-library/stats/summary  0.56s   0.56s
RES GET /api/aircraft              0.17s
RES GET /api/llm/status            0.18s
RES GET /api/settings/smtp         0.19s
RES GET /api/settings/opendronelog 0.19s
RES GET /api/settings/opensky      0.19s
RES GET /api/settings/device-keys  0.17s
RES GET /api/backup/history        0.09s
RES GET /api/auth/account          0.21s
RES GET /api/settings/llm          0.21s
RES GET /api/backup/schedule       0.15s
RES GET /api/pilots                0.15s
RES GET /api/flight-library/reprocess/status  0.22s
RES GET /api/settings/dji          0.22s
RES GET /api/intake/default-tos-status        0.21s
RES GET /api/settings/weather      0.21s
RES GET /api/settings/payment      0.22s
RES GET /api/maintenance/status    0.13s
RES GET /api/settings/branding     0.21s
RES GET /api/rate-templates        0.23s

RES GET /api/weather/current       8.31s   ← always slow
RES GET /api/weather/current       7.44s   ← always slow
```
The 0.5s clusters land in tight time bands (multiple endpoints completing in the same 0.05s window) — diagnostic of pool saturation, not per-endpoint slowness. Each individual handler is fast; what's slow is being held in the pool queue waiting for a connection while 20+ siblings finish.

### 2.8 Frontend bundle
```
$ docker exec droneops-frontend-1 ls -lh /usr/share/nginx/html/assets/
ClientLogin-DhApaQM9.js              1.9 K   ← split
ClientMissionDetail-Cbm0EDQj.js      6.6 K   ← split
ClientPortal-r-owDJQz.js             4.3 K   ← split
useClientAuth-DxE5nH_7.js            1.9 K   ← split
index-B-LWiUMN.css                  250.8 K
index-BudFVcae.js                    1.9 M   ← single eager chunk for all 17 main pages
pdf.worker.min-qwK7q_zL.mjs        1021.7 K  ← lazy by react-pdf
TOTAL                                  3.2 M
```
On an average residential uplink (~50 Mbps down, but TLS/CF tunnel + parse), 1.9 MB is ~300-500 ms over wire + Vite/React parse on a 4-year-old laptop. On a phone or slow Wi-Fi, much worse.

### 2.9 Frontend page-fetch concurrency density
```
$ grep -c "api.get(" pages/Settings.tsx        → 34
$ grep -c "api.get(" pages/MissionNew.tsx      → fewer but still serial
$ grep -c "api.get(" pages/Dashboard.tsx       → 6 in mount, no Promise.all
```
None of the pages use `Promise.all`. Every call is a separate axios round-trip. There is **no React Query / TanStack Query / SWR** in `frontend/package.json`.

### 2.10 SQLAlchemy relationship loading (already correct)
```
$ grep -n "lazy=" backend/app/models/*.py
mission.py: customer  selectin
mission.py: flights   selectin
mission.py: images    selectin
mission.py: report    noload   ← correct, on-demand
mission.py: invoice   noload   ← correct
flight.py:  aircraft  selectin
flight.py:  pilot     selectin
flight.py:  battery_logs noload ← correct
[etc.]
```
Eager-loading is set thoughtfully per relation. Not a finding.

### 2.11 Sync I/O in async path (clean)
`grep -rEn "requests\.|httpx\.Client" backend/app/`:
- All HTTP egress uses `httpx.AsyncClient` ✓
- One `httpx.Client(timeout=5)` in `services/pushover.py:137` — that's a synchronous wrapper called from a Celery task (worker), not from the FastAPI event loop. Not a finding.

### 2.12 Vite config
`frontend/vite.config.ts` has **no `build.rollupOptions.output.manualChunks`**, no `build.target`, no chunking strategy. Default Vite splits by route/dynamic-import only; since 17 of 17 main pages are eagerly imported in `App.tsx`, they all land in `index-*.js`.

---

## 3. Findings — ranked by user-impact × effort × certainty

Scale: **Impact** S/M/L (perceived UX); **Effort** S/M/L (engineering hours); **Risk** L/M/H (failover, replication, data, customer-facing).

### F-1 — Weather endpoint serializes 5 external API calls + zero caching  ★ HIGHEST IMPACT
- **Symptom:** Dashboard "FLIGHT CONDITIONS" panel takes 7-8s to populate, every load, every refresh, for every viewer.
- **Cite:** `backend/app/routers/weather.py:84-107`. Body of `get_weather_and_airspace`:
  ```python
  weather = await _fetch_weather(lat, lon)       # Open-Meteo,        timeout=10s
  metar   = await _fetch_metar(airport)          # AviationWeather,   timeout=10s
  tfrs    = await _fetch_tfrs(airport)           # AviationWeather,   timeout=15s
  notams  = await _fetch_notams(airport)         # AviationWeather,   timeout=15s
  alerts  = await _fetch_nws_alerts(lat, lon)    # NWS,               timeout=10s
  ```
  All five `await`s are sequential. Worst-case path is sum-of-timeouts (~60s). Real production observed = 7-8s on each call.
- **Root cause:** Sequential awaits should be `asyncio.gather`. No Redis cache layer despite Redis already being a dependency (`droneops-redis-1` running, used by Celery).
- **Expected gain:** First call: 7-8s → 1.5-2.5s (slowest single fetch). Subsequent calls within TTL: 7-8s → ~3-8 ms (Redis hit). Dashboard auto-refresh moves from 7-8s every 5 min to ~3 ms hits with one fresh fetch every ttl.
- **Effort:** S (≤90 min). Two changes: gather + Redis cache helper.
- **Risk:** L. No DB schema, no replication, no failover impact. Cache invalidation is time-based and aviation data is intentionally short-TTL.

### F-2 — Settings page = 34 sequential api.get + pool saturation  ★ HIGHEST IMPACT (concurrent loads)
- **Symptom:** Opening Settings stalls visibly; 0.5s clusters in backend logs as the `get_db` pool queues; tabbing in/out of Settings re-fires all 34 because there is no client cache.
- **Cite:** `frontend/src/pages/Settings.tsx:188-216` (the bulk-fan-out useEffect, 34 distinct `api.get` calls, no `Promise.all`); `backend/app/database.py:6-15` (`pool_size=5, max_overflow=10`); `backend/app/auth/jwt.py:130-148` (every authenticated request adds one `select(User)` to validate).
- **Root cause (multi-part):**
  1. No client-side cache. Every mount = 34 round-trips.
  2. No request fan-out batch. The frontend issues all 34 in parallel via JS but the browser+CF+nginx chain serializes some of them.
  3. The backend pool ceiling of 15 is tight when the Dashboard (~6) + AppShell (~2) + Settings (~34) overlap.
  4. Token validation does its own `SELECT user`. 34 reads of a 1-row `users` table = wasteful.
- **Expected gain:** Settings p95 first-paint ~3.2s → ~600ms (combined: parallelization + cache reuse on repeat-visit + fewer DB reads + bigger pool to handle the bursts even on a cold visit).
- **Effort:** M (4-6 hours). Three parts: bigger pool (1 line), in-flight LRU on `get_current_user` (≤30 lines), client-side cache layer for read-only endpoints (TanStack Query or hand-rolled — see §4).
- **Risk:** L. Pool size up to 20+10 is comfortably under Postgres `max_connections=100` default. Token cache is keyed by token+user-id with 60-second TTL; revocation latency goes from 0 to 60s, acceptable for self-hosted (call it out in DECISIONS).

### F-3 — Single 1.9 MB main bundle, all 17 main pages eagerly bundled  ★ HIGH IMPACT
- **Symptom:** Cold-cache first paint is gated on downloading + parsing the entire app, including Leaflet (used only on `/airspace` + `/flights/replay`), react-pdf (used only in client portal), tiptap (used only in mission editor), Mantine forms/dropzone/dates (heavy).
- **Cite:** `frontend/src/App.tsx:7-23` — eager imports of all 17 main pages; only `pages/client/*` are `lazy()`-loaded.
- **Root cause:** No code-splitting at the route boundary, no `manualChunks` in `vite.config.ts`.
- **Expected gain:** First-paint chunk shrinks from 1.9 MB → ~400-500 KB (Login + Dashboard + AppShell + Mantine core + axios). Heavy pages (Settings, MissionNew, FlightReplay, MissionDetail, Maintenance) load on demand. Total bytes downloaded across a session can stay similar but the perceived first-paint time drops a lot, and CF caching benefits more chunks.
- **Effort:** S-M (2-3 hours). Two parts: `lazy()` all 17 main pages + Suspense fallback (already used for client/*); add `manualChunks` to Vite config to split vendor bundles (mantine, leaflet, tabler-icons, react-pdf, tiptap, sentry).
- **Risk:** L. Pure build-time change. CI must rebuild + redeploy. Test: every route still navigates and renders. Bill's standing rule (`feedback_code_splitting.md`) explicitly demands this.

### F-4 — No client-side query cache; every navigation re-fetches  ★ HIGH IMPACT
- **Symptom:** Dashboard → Flights → back-to-Dashboard re-runs all 6 dashboard `api.get` plus the weather (7-8s) every time. Quick-tabbing UX feels permanently "loading".
- **Cite:** `frontend/src/pages/Dashboard.tsx:241-249` (raw `useEffect` + `api.get`); `frontend/package.json:dependencies` — no React Query / SWR; `frontend/src/api/client.ts` — plain axios with no caching layer.
- **Root cause:** State management is hand-rolled `useState` + `useEffect`; no stale-while-revalidate pattern.
- **Expected gain:** Repeat-visit page render 200-700ms → 5-15ms (cache-hit). Combined with F-1 (Redis weather cache), Dashboard repeat-visit p95 drops from ~7.5s to ~150ms.
- **Effort:** M (4-6 hours). Two acceptable approaches in §4 — recommend a focused custom `useApiCache` hook over adding TanStack Query (smaller diff, no new heavy dep). Apply to Dashboard, Flights list, Settings GETs only (POST/PUT/DELETE bypass).
- **Risk:** L-M. Stale data is the failure mode. Mitigation: 30s TTL for most endpoints, `invalidate(key)` after every successful POST/PUT/DELETE. Document the staleness window in DECISIONS.

### F-5 — `react-pdf` Document optimizeDeps default + heavy mainBundle leak  (deferred, dependent on F-3)
- **Symptom:** Bundle bloat amplifier — react-pdf's pdf.js worker is correctly split (`pdf.worker.min-qwK7q_zL.mjs`, 1 MB), but the `<Document>` import in client portal pulls 100+ KB into the *main* bundle for users who never view a PDF.
- **Cite:** `frontend/src/pages/client/ClientMissionDetail.tsx` (per inspection of split chunk; pdf-related code split into client portal chunks but bundled when first used).
- **Root cause:** Settled by F-3 + F-4 implementations. Don't address separately.
- **Effort:** N/A — folded into F-3.
- **Risk:** N/A.

### F-6 — Per-request `select(User)` in token validation  (folded into F-2)
- **Symptom:** Every authenticated endpoint = 1 extra DB roundtrip.
- **Cite:** `backend/app/auth/jwt.py:144-147`.
- **Root cause:** No caching of resolved user; valid because revocation/disable must take effect, but at 0 cost on cache miss.
- **Expected gain:** 34 db reads per Settings load → ~1. Saves ~30-50ms in the aggregate-fan-out path. Folded into F-2 (TTL=60s LRU keyed by token).
- **Effort:** S — already in F-2's scope.
- **Risk:** L. Documented latency: token revocation up to 60s before takeover. For self-hosted single-tenant this is fine. Add this caveat to DECISIONS.

### F-7 — No `index=True` on any model column  (deferred — not a today problem)
- **Symptom:** None today (52 MB DB, single-digit row counts in most tables). At 10k+ flights this would matter.
- **Cite:** `grep -nE "index=True" backend/app/models/*.py` returns zero hits.
- **Root cause:** Never added.
- **Expected gain:** **Zero today.** Documented as a pre-emptive recommendation for ROADMAP — would matter once `flights.start_time` queries scan ≥10k rows or `mission_flights.flight_id` joins large datasets. Per the audit guideline ("don't propose a fix you can't measure"), **NOT included in the fix list.** Documented in DECISIONS.md and ROADMAP.md as a watch-item.
- **Effort:** S (when it becomes needed).
- **Risk:** L when shipped (CONCURRENTLY — see ROADMAP).

### F-8 — Dashboard re-renders on every weather state-tick  (LOW IMPACT)
- **Symptom:** Subtle. Dashboard's main render function is large; weather refresh (every 5 min) re-renders the entire page incl. the static stats cards.
- **Cite:** `frontend/src/pages/Dashboard.tsx:215-1163` (single component, 1163 lines, no `React.memo`, no sub-component split).
- **Root cause:** Component-level architecture; not a hot path.
- **Expected gain:** Tiny (<5ms render savings). Not worth shipping in this audit.
- **Status:** **Drop**. Documented for ROADMAP only.

### F-9 — `pg_stat_statements` not enabled  (Operator-action — informational)
- **Symptom:** No way to see top-N expensive queries empirically.
- **Cite:** `SELECT extname FROM pg_extension` returned only `plpgsql`.
- **Root cause:** Never loaded.
- **Status:** **Operator decision** — call it out in §8 Open Questions. To enable: add `shared_preload_libraries = 'pg_stat_statements'` to `postgresql.conf` + `CREATE EXTENSION pg_stat_statements`. **Requires postmaster restart, which means brief replication pause.** Aegis MUST NOT enable this without explicit operator sign-off (Failover Guard).

---

## 4. The Fix List

Five changes, ordered for delivery. Aegis ships them in this order, in the same session, in five separate commits, each with a CHANGELOG entry, a version bump (4-file rule), and an immediate verification step. No follow-ups.

---

### FIX-1 — Parallelize + Redis-cache the weather endpoint
**Source of truth:** F-1.

**Files to touch:**
- `backend/app/routers/weather.py` (refactor `get_weather_and_airspace`)
- (no new files — Redis client already exists in `app/database.py`-adjacent or `app/services/`; if not, add `app/services/cache.py`)

**Specific change:**
1. In `get_weather_and_airspace`:
   ```python
   import asyncio
   from app.services.cache import get_or_fetch  # new helper

   @router.get("/current")
   async def get_weather_and_airspace(
       _user: User = Depends(get_current_user),
       db: AsyncSession = Depends(get_db),
   ):
       lat, lon, label, airport = await _load_weather_location(db)
       cache_key = f"weather:current:{lat:.4f}:{lon:.4f}:{airport}"

       async def _build():
           weather, metar, tfrs, notams, alerts = await asyncio.gather(
               _fetch_weather(lat, lon),
               _fetch_metar(airport),
               _fetch_tfrs(airport),
               _fetch_notams(airport),
               _fetch_nws_alerts(lat, lon),
               return_exceptions=False,  # individual handlers already swallow errors
           )
           return {
               "location": label, "airport": airport,
               "weather": weather, "metar": metar,
               "tfrs": tfrs, "notams": notams, "alerts": alerts,
               "fetched_at": datetime.utcnow().isoformat(),
           }

       return await get_or_fetch(cache_key, _build, ttl_seconds=300)
   ```
2. Add `app/services/cache.py` (≤40 lines):
   ```python
   """Redis-backed read-through cache for short-TTL external API calls.
   Failure-mode: on Redis unreachable, fall through to live fetch.
   """
   import json, logging
   from typing import Awaitable, Callable, Any
   import redis.asyncio as aioredis
   from app.config import settings

   logger = logging.getLogger("doc.cache")
   _client: aioredis.Redis | None = None

   def _conn() -> aioredis.Redis:
       global _client
       if _client is None:
           _client = aioredis.from_url(settings.redis_url, decode_responses=True)
       return _client

   async def get_or_fetch(key: str, build: Callable[[], Awaitable[Any]], ttl_seconds: int) -> Any:
       try:
           cached = await _conn().get(key)
           if cached:
               return json.loads(cached)
       except Exception as e:
           logger.warning("cache_get_failed key=%s err=%s — falling through", key, e)
       value = await build()
       try:
           await _conn().set(key, json.dumps(value, default=str), ex=ttl_seconds)
       except Exception as e:
           logger.warning("cache_set_failed key=%s err=%s — value returned uncached", key, e)
       return value
   ```
   Important: `failure-open` (Redis down → live fetch). Per `feedback_prevent_failures.md` no fragile state.

**Expected measurable gain:** /api/weather/current cold-cache p95 7-8s → 1.5-2.5s; warm-cache p95 7-8s → <30 ms. Dashboard first paint p95 ~9.5s → ~2.5s.

**Verification commands (run from BOS-HQ shell):**
```bash
# Cold path (clear cache first)
ssh bbarnard065@10.99.0.4 'sudo docker exec droneops-redis-1 redis-cli DEL "weather:current:*" 2>/dev/null; for i in 1 2 3; do sudo docker exec droneops-backend-1 curl -s -o /dev/null -w "%{time_total}\n" -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/api/weather/current; done'
# Expect: 1st call ~1.5-2.5s, 2nd & 3rd ~0.005-0.030s
```
Where `$TOKEN` = a valid access token (obtain via `/api/auth/login` curl).

**Rollback:** `git revert <commit>` — the cache helper is additive so reverting just brings back the old serial fetch. No DB / config / volume change.

**Failover guard:** ✓ no schema change, no replication impact, redis is shared but failure-open, lifespan unaffected.

---

### FIX-2 — Code-split the 17 main pages + Vite manualChunks
**Source of truth:** F-3.

**Files to touch:**
- `frontend/src/App.tsx` — change all 17 main `import` to `lazy()` + wrap in `<Suspense fallback={…}>`.
- `frontend/vite.config.ts` — add `build.rollupOptions.output.manualChunks` to split vendor bundles.

**Specific change (App.tsx):**
Move from:
```tsx
import Dashboard from './pages/Dashboard';
import Missions from './pages/Missions';
// ...all 17
```
To:
```tsx
const Dashboard = lazy(() => import('./pages/Dashboard'));
const Missions = lazy(() => import('./pages/Missions'));
// ...all 17, matching existing client portal pattern (already uses Suspense)
```
Wrap the `<Routes>` block in `<Suspense fallback={<AppLoaderFallback/>}>`.
Keep `Login` and `Setup` eager (they are pre-auth and tiny; bundling them avoids a flash).

**Specific change (vite.config.ts):**
```ts
export default defineConfig({
  // ...existing config...
  build: {
    target: 'es2022',
    rollupOptions: {
      output: {
        manualChunks: {
          'mantine-core': ['@mantine/core', '@mantine/hooks', '@mantine/notifications'],
          'mantine-rich': ['@mantine/dates', '@mantine/dropzone', '@mantine/form', '@mantine/tiptap'],
          'leaflet': ['leaflet', 'react-leaflet'],
          'tiptap': ['@tiptap/react', '@tiptap/starter-kit', '@tiptap/extension-highlight',
                     '@tiptap/extension-link', '@tiptap/extension-text-align', '@tiptap/extension-underline'],
          'pdf': ['react-pdf'],
          'icons': ['@tabler/icons-react'],
          'sentry': ['@sentry/react'],
        },
      },
    },
  },
});
```

**Expected measurable gain:** main `index-*.js` 1.9 MB → ~400-500 KB on first paint. Vendor chunks cached separately by CF and benefit from cross-version cache hits.

**Verification:**
```bash
# After deploy, on BOS-HQ:
ssh bbarnard065@10.99.0.4 'sudo docker exec droneops-frontend-1 ls -lh /usr/share/nginx/html/assets/'
# Expect: many chunks, each <500KB. The main index-*.js < 600KB.

# Page-load measurement (uses curl with HTTP/2 against the Cloudflare-fronted tunnel; add Chromium DevTools manual check too):
curl -s -o /dev/null -w "main.js download: %{time_total}s size: %{size_download} bytes\n" \
  https://droneops.barnardhq.com/assets/index-<HASH>.js
```

**Rollback:** `git revert <commit>` — revert and rebuild. No persistent state change.

**Failover guard:** ✓ frontend-only.

---

### FIX-3 — Async DB pool tuning + cached `get_current_user`
**Source of truth:** F-2 + F-6.

**Files to touch:**
- `backend/app/database.py` (raise pool sizes)
- `backend/app/auth/jwt.py` (add 60s LRU cache around the user lookup)

**Specific change (database.py):**
```python
engine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_recycle=1800,
    pool_size=20,        # was 5
    max_overflow=20,     # was 10
)
```
Postgres default `max_connections=100`; with worker (5) + beat (2) + flight-parser (5) + 40 from this engine = 52 < 100, headroom intact.

**Specific change (jwt.py):**
Wrap `get_current_user`'s body in a TTL cache. Token decode stays in the request path (cheap); only the DB-User lookup is cached, keyed on `(user_id, token_hash[:16])`:
```python
import time
from functools import lru_cache

# Module-level cache: { (user_id, token_prefix): (user_dict, expiry_unix) }
_user_cache: dict[tuple, tuple[dict, float]] = {}
_USER_CACHE_TTL = 60.0  # seconds

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> User:
    token = credentials.credentials
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
        username = payload.get("sub")
        token_type = payload.get("type")
        if username is None or token_type != "access":
            raise HTTPException(status_code=401, detail="Invalid token")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

    cache_key = (username, token[:16])
    now = time.time()
    cached = _user_cache.get(cache_key)
    if cached and cached[1] > now:
        # Re-hydrate a transient User instance from cached primitives — avoid stale ORM session
        u = User(**cached[0])
        return u

    result = await db.execute(select(User).where(User.username == username))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found")

    # Cache the safe-to-replay fields only
    _user_cache[cache_key] = (
        {"id": user.id, "username": user.username, "hashed_password": user.hashed_password,
         "is_active": user.is_active, "created_at": user.created_at},
        now + _USER_CACHE_TTL,
    )
    # Trim cache if it grows past 1000 entries (operator-only system, this won't happen, but cheap insurance)
    if len(_user_cache) > 1000:
        # Drop expired entries
        expired = [k for k, v in _user_cache.items() if v[1] < now]
        for k in expired: _user_cache.pop(k, None)
    return user
```
Document in DECISIONS: token revocation latency = up to 60s. Self-hosted single-operator deployment, acceptable.

**Expected measurable gain:** Settings page p95 ~3.2s → ~600ms. Aggregate request count to `users` table on a Settings load: 34 → 1.

**Verification:**
```bash
# On BOS-HQ, with valid token in $TOKEN, fire 30 parallel requests:
ssh bbarnard065@10.99.0.4 'for i in $(seq 1 30); do (sudo docker exec droneops-backend-1 curl -s -o /dev/null -w "%{time_total}\n" -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/api/customers &); done; wait'
# Expect: all 30 finish within 0.3-0.6s. BEFORE: ~1.5-3s due to pool queueing.

# Verify pool size is honored:
ssh bbarnard065@10.99.0.4 'sudo docker exec droneops-standby-db psql -U droneops -d droneops -c "SELECT count(*) FROM pg_stat_activity WHERE application_name LIKE '\''%asyncpg%'\'' OR datname = '\''droneops'\'';"'
# Expect: <= 40 connections, well under max_connections=100.
```

**Rollback:** `git revert <commit>` — pure code revert, no schema/state.

**Failover guard:** ✓ Pool sizing is per-process and survives container restart (it's in `database.py`). Replication unaffected (cache is in-process only). User cache contents do not survive container restart, so a deploy/restart force-revalidates every token.

---

### FIX-4 — Client-side query cache (custom `useApiCache` hook)
**Source of truth:** F-4.

**Approach decision:** I considered TanStack Query (`@tanstack/react-query`). Rejected for this fix because:
1. It adds ~14 KB gzipped to the main bundle, partially undoing F-3.
2. Bill's `feedback_no_deferred_fixes.md` and the small surface area (Dashboard + Flights + Settings) make a hand-rolled cache the right level.
3. Adopting TanStack Query repo-wide is a large refactor that doesn't fit in this audit's "small high-leverage" constraint.

**Files to touch:**
- `frontend/src/hooks/useApiCache.ts` (new, ~80 lines)
- `frontend/src/pages/Dashboard.tsx` — replace 6 useEffect+api.get with useApiCache
- `frontend/src/pages/Flights.tsx` — same for the 3 list-fetches
- `frontend/src/pages/Settings.tsx` — wrap the 34 fan-out reads (writes still call `api.post/.put/.delete` and explicitly invalidate keys)

**Specific change (`useApiCache.ts`):**
```ts
import { useEffect, useState, useRef } from 'react';
import api from '../api/client';

type CacheEntry<T> = { data: T; expiresAt: number };
const cache = new Map<string, CacheEntry<unknown>>();
const inflight = new Map<string, Promise<unknown>>();
const subscribers = new Map<string, Set<() => void>>();

export function invalidate(prefix: string) {
  for (const key of cache.keys()) {
    if (key.startsWith(prefix)) {
      cache.delete(key);
      subscribers.get(key)?.forEach(fn => fn());
    }
  }
}

export function useApiCache<T>(
  url: string | null,
  options: { ttlMs?: number; deps?: unknown[] } = {}
): { data: T | null; loading: boolean; error: unknown; refetch: () => void } {
  const ttl = options.ttlMs ?? 30_000;  // 30s default
  const [, force] = useState(0);
  const [error, setError] = useState<unknown>(null);
  const mountedRef = useRef(true);

  useEffect(() => () => { mountedRef.current = false; }, []);

  const key = url ?? '';
  const entry = cache.get(key) as CacheEntry<T> | undefined;
  const fresh = !!entry && entry.expiresAt > Date.now();

  // Subscribe so external invalidate() refreshes us
  useEffect(() => {
    if (!key) return;
    if (!subscribers.has(key)) subscribers.set(key, new Set());
    const fn = () => mountedRef.current && force(n => n + 1);
    subscribers.get(key)!.add(fn);
    return () => { subscribers.get(key)?.delete(fn); };
  }, [key]);

  const refetch = () => {
    if (!url) return;
    cache.delete(url);
    if (!inflight.has(url)) {
      const p = api.get<T>(url).then(r => {
        cache.set(url, { data: r.data, expiresAt: Date.now() + ttl });
        inflight.delete(url);
        subscribers.get(url)?.forEach(fn => fn());
        return r.data;
      }).catch(e => {
        inflight.delete(url);
        if (mountedRef.current) setError(e);
        throw e;
      });
      inflight.set(url, p);
    }
  };

  useEffect(() => {
    if (url && !fresh) refetch();
  }, [url, ...(options.deps ?? [])]);

  return {
    data: entry?.data ?? null,
    loading: !fresh && !!url,
    error,
    refetch,
  };
}
```
Plus an example of how the Dashboard call sites look:
```tsx
// Before (Dashboard.tsx:241-249, 6 useEffect + 6 useState pairs)
useEffect(() => { api.get('/missions').then(r => setMissions(r.data ?? [])); /* etc */ }, []);

// After
const { data: missions = [] }      = useApiCache<Mission[]>('/missions');
const { data: customers = [] }     = useApiCache<Customer[]>('/customers');
const { data: flightStats }        = useApiCache<FlightStats>('/flight-library/stats/summary');
const { data: maintenanceAlerts = [] } = useApiCache<MaintenanceAlert[]>('/maintenance/due');
const { data: nextServiceDue }     = useApiCache<NextServiceDue>('/maintenance/next-due');
const { data: batteries = [] }     = useApiCache<BatteryInfo[]>('/batteries');
```
Mutations explicitly invalidate. Example in Settings:
```tsx
await api.post('/aircraft', body);
invalidate('/aircraft'); invalidate('/missions'); // anywhere mission cards show aircraft names
```

**Expected measurable gain:** Repeat-visit Dashboard (after first load): 6 endpoints @ 0.05–0.5s each → 6 cache hits ~0ms. Combined with F-1 (weather Redis cache): full repeat-visit Dashboard 7.5s → ~150ms (the 150ms is mostly the new SPA chunk download from F-3 + paint).

**Verification:**
- Browser DevTools Network tab: open Dashboard, navigate to Flights, navigate back to Dashboard. Expect: only POST/PUT/DELETE calls fire on the second mount (within TTL). All GETs come from cache.
- React DevTools: confirm no extra renders.
- Mutation invalidation: in Settings, add an aircraft, navigate to Dashboard, confirm new aircraft count is visible (no stale data).

**Rollback:** Revert the commit; pages return to direct `api.get` (the hook is opt-in per call site).

**Failover guard:** ✓ Pure frontend, no DB/replication/server impact.

---

### FIX-5 — Documentation: ADR-0004 + CHANGELOG + ROADMAP + PROGRESS
**Source of truth:** Documentation Discipline (`/home/bbarnard065/.claude/CLAUDE.md` + repo `CLAUDE.md`).

**Files to touch:**
- `docs/adr/0004-perf-audit-baseline.md` — ADR with BEFORE measurements (created at audit time, see §2 above; aegis appends AFTER measurements once FIX-1..4 are deployed and Status flips from `proposed` to `accepted`).
- `CHANGELOG.md` — one entry per fix (4 patches), and one summary entry for the audit work.
- `ROADMAP.md` — append F-7 (indexes) + F-8 (Dashboard sub-component split) + F-9 (pg_stat_statements operator decision) under a new "Performance — deferred" section.
- `PROGRESS.md` — session entry for 2026-04-24 perf audit + each subsequent ship.

**Specific change:** see ADR-0004 file (parallel deliverable) + CHANGELOG entries phrased as in repo style ("perf: parallelize weather endpoint with asyncio.gather + redis cache — v2.63.7" etc).

**Verification:** All four files exist and are referenced from each other. `CHANGELOG.md` has the version-bumped entries. `git diff` review is the verification.

**Rollback:** N/A — docs only.

**Failover guard:** ✓ Docs only.

---

## 5. Anti-Goals — what aegis MUST NOT do

These are out of scope. Explicit because past audits have drifted into them.

1. **Do NOT introduce a new caching layer that requires invalidation logic beyond the explicit one in F-1 (weather, time-based) and F-4 (client cache, mutation-based).** Specifically: do not add a Postgres query cache, do not add a pg-bouncer middleware, do not add CDN edge cache rules.
2. **Do NOT rewrite the SQLAlchemy ORM layer.** Eager-loading is already correctly tuned (§2.10). Do not flip relations from `selectin` to `joined` or vice versa without a measurement showing it matters.
3. **Do NOT change deploy topology, container orchestration, or Postgres configuration.** Don't enable `pg_stat_statements` (Failover Guard, §F-9). Don't add new sidecars. Don't move the cache to a new container.
4. **Do NOT downgrade or upgrade major dependency versions** (no Mantine v8, no Vite v7, no React 19) as part of this audit. Stay on the current versions per `package.json`.
5. **Do NOT disable any existing protective middleware** — slowapi rate limiter, demo guard middleware, CORS, JSON logger middleware, OTel auto-instrumentation, Sentry init.
6. **Do NOT touch the failover engine, blue-green flow, replication settings, WireGuard, fencing, or quorum logic.** F-3 frontend, F-1 weather, F-2 jwt+pool, F-4 useApiCache hook — none of these touch any of those systems. Verify with `git diff --name-only` before commit.
7. **Do NOT add Redis-backed read caches to anything other than the weather endpoint without an invalidation strategy you can defend in 2 sentences in the ADR.** F-1 is the only Redis-cached endpoint in this fix list.
8. **Do NOT batch all four fixes into a single commit.** Five commits, five version bumps (v2.63.7 → v2.63.8 → v2.63.9 → v2.63.10 → v2.63.11 if docs-only), so any one fix can be reverted cleanly per the repo's "All commits go directly to main" workflow.
9. **Do NOT introduce stopgaps.** `feedback_no_deferred_fixes.md`. Each fix lands shippable; F-7/F-8/F-9 are explicitly *not in this plan* and only appear in ROADMAP.
10. **Do NOT add manual deploy steps.** NOC autopull deployer is on. `feedback_always_push.md` + `feedback_always_deploy.md` — commit, push, let it deploy. Verify deploy via container logs only.

---

## 6. Acceptance — AFTER measurements (aegis must collect)

Each fix's section above already specifies its verification command. Aegis must, after **all four shipped + deployed + Watchtower-confirmed-live**, collect a final summary block and append it to ADR-0004 under "AFTER measurements (post-fix)":

```bash
# Run on BOS-HQ. Use a fresh access token. The measurement compares against §2 of this plan.
TOKEN=$(curl -s -X POST -H "Content-Type: application/json" \
  -d '{"username":"USER","password":"PASS"}' \
  http://localhost:8000/api/auth/login | jq -r .access_token)

# 1. Weather endpoint — cold + warm
sudo docker exec droneops-redis-1 redis-cli --scan --pattern 'weather:*' | xargs -r sudo docker exec droneops-redis-1 redis-cli DEL
echo "=== weather cold ==="
for i in 1 2 3; do sudo docker exec droneops-backend-1 curl -s -o /dev/null -w "%{time_total}\n" -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/api/weather/current; done
echo "=== weather warm ==="
for i in 1 2 3; do sudo docker exec droneops-backend-1 curl -s -o /dev/null -w "%{time_total}\n" -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/api/weather/current; done

# 2. Settings burst (simulate fan-out)
echo "=== 30 parallel /api/customers ==="
for i in $(seq 1 30); do (sudo docker exec droneops-backend-1 curl -s -o /dev/null -w "%{time_total}\n" -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/api/customers &); done; wait

# 3. Frontend bundle
echo "=== bundle sizes ==="
sudo docker exec droneops-frontend-1 ls -lh /usr/share/nginx/html/assets/

# 4. DB pool sanity
echo "=== pg connection count ==="
sudo docker exec droneops-standby-db psql -U droneops -d droneops -c "SELECT state, count(*) FROM pg_stat_activity WHERE datname='droneops' GROUP BY state;"

# 5. RES log p95 sample (last 200 RES lines)
sudo docker logs droneops-backend-1 --tail 2000 2>&1 | grep "RES " | awk '{print $NF}' | sed 's/s//' | sort -n | tail -20
```

**Acceptance thresholds (must hit ALL three):**
- Weather warm-cache p50 < 50 ms (BEFORE: 7.4s).
- 30 parallel `/api/customers` p95 < 700 ms (BEFORE: estimated 1.5-3s).
- Frontend main `index-*.js` chunk < 700 KB uncompressed (BEFORE: 1.9 MB).

If any threshold fails, aegis MUST roll back the offending fix and reopen the relevant section in this plan with a "did not hit" entry.

---

## 7. Risk Register

| ID | Change | Risk | Likelihood | Impact | Mitigation |
|----|--------|------|------------|--------|------------|
| R-1 | F-1 redis cache | Stale weather served | M | L | 5-min TTL acceptable for METAR/NOTAM/TFR (FAA refresh cadence is hourly); failure-open if Redis down |
| R-2 | F-1 asyncio.gather | One slow API blocks all 5 (gather waits) | L | L | Each `_fetch_*` already wraps in try/except; per-call timeouts unchanged |
| R-3 | F-2 user cache | Token revocation lag ≤60s | L | L | Documented in ADR. Container restart flushes cache. Self-hosted, single operator. |
| R-4 | F-2 pool size 5→20 | Postgres connection exhaustion | L | L | Worker (5) + beat (2) + parser (5) + backend (40) = 52 < default 100. Headroom. |
| R-5 | F-3 manualChunks | Vendor chunk hash changes break a stale CF cache | L | L | CF caches by hash; build hashes change every deploy anyway. No-op. |
| R-6 | F-3 lazy load | Suspense fallback flicker on slow nav | L | L | Existing pattern in client portal works fine; same fallback used. Bill's `feedback_code_splitting.md` mandates this anyway. |
| R-7 | F-4 client cache | Stale read after concurrent edit on another tab | M | L | TTL 30s; mutations `invalidate()`; documented. For single-operator self-hosted use, conflict probability is negligible. |
| R-8 | F-4 hand-rolled hook | Bugs missed vs. battle-tested TanStack Query | M | L | Surface area is small (3 pages), tests follow in §6 verification, code is <80 lines |
| R-9 | All | Failover engine breakage | L | H | Verified §F-1 through §F-4 don't touch DB schema, replication settings, network, or quorum |
| R-10 | All | NOC autopull deploy fails | L | M | Standard deploy; Watchtower watches the image; rollback = `git revert` and let autopull re-deploy |

---

## 8. Open Questions

These are explicitly *not* blocking. Aegis can ship FIX-1 through FIX-5 without operator input. The questions below are documented so the operator can opt-in to additional work after ship.

1. **`pg_stat_statements`** — should the operator enable this for empirical query observability? Requires postmaster restart + brief replication pause. **Recommendation:** wait until DB grows past 1 GB or until a perf incident demands it. Until then, the `RES` middleware logs (which already log per-request timing) are sufficient.
2. **TanStack Query repo-wide adoption** — out of scope here. If Bill wants stronger client cache semantics across all 17 pages (deduplication, optimistic updates, query devtools), open a separate spec. The custom `useApiCache` shipped in F-4 is intentionally minimal.
3. **`weather/current` per-tenant cache key** — the current key uses lat/lon/airport. If managed multi-tenant ever shares a Redis (it doesn't today; managed instances each have their own redis-managed sidecar), the key would need a tenant prefix. Documented in ADR-0004.
4. **Index strategy at scale** — F-7 deferred. Bill should expect to revisit when total flight count exceeds 5k or any single tenant's DB exceeds 500 MB.

---

## 9. Implementation order for aegis (TL;DR)

1. **FIX-1** weather parallelize + Redis cache → commit + push → patch v2.63.7 → verify §6.1.
2. **FIX-3** pool + user cache → commit + push → patch v2.63.8 → verify §6.2.
3. **FIX-2** code-split + manualChunks → commit + push → patch v2.63.9 → verify §6.3.
4. **FIX-4** useApiCache hook + apply to Dashboard/Flights/Settings → commit + push → patch v2.63.10 → verify §6 end-to-end.
5. **FIX-5** ADR-0004 (append AFTER measurements) + CHANGELOG/ROADMAP/PROGRESS final pass → docs commit v2.63.11 (or fold into FIX-4's commit if all docs are co-authored — repo CLAUDE.md says docs ship with code).

Each commit ends with the standard repo session-link footer per `CLAUDE.md` Conventions.

---

**End of plan.** Aegis: report any deviation in PROGRESS.md before deploying.
