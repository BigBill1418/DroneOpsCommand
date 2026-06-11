# ADR-0020 — Report generation OOM: simplify GPS tracks before buffering

- **Status:** Accepted
- **Date:** 2026-06-11
- **Supersedes / relates to:** ADR-0019 (flight-library list defers heavy JSON
  columns) — same failure class (heavy GPS data + 1 GiB worker cgroup), different
  code path.

## Context

Clicking **"Generate Report"** in DroneOpsCommand returned a Cloudflare **520**
origin-error page ("the origin web server returned an invalid or incomplete
response"). The frontend surfaced the CF HTML as a red "Generation Failed"
notification. Client deliverables were blocked.

`POST /api/missions/{id}/report/generate` (`backend/app/routers/reports.py`) runs
the GPS-geometry pipeline **synchronously, in-request**, before dispatching the
Celery job:

1. `extract_gps_tracks(flights)` — pulls each MissionFlight's `flight_data_cache`
   GPS track.
2. `calculate_area_acres(tracks)` — coverage acreage.
3. `render_static_map(flights)` — OSM-tile PNG for the PDF.

A real mission (`436af975…`) had **3 flights / 33,830 GPS points**.
`calculate_area_acres` built a single `MultiLineString` from the raw tracks and
called `.buffer(30)` on it. Buffering tens of thousands of near-collinear
vertices makes GEOS allocate **>900 MB**; on top of the live worker baseline
(~390 MB) that exceeded the **1 GiB** container cgroup and the kernel
**OOM-killed uvicorn mid-response**. Cloudflare received a truncated response →
520.

### Evidence (live, BOS-HQ, 2026-06-10/11 — times UTC)

Backend log, the request that failed:

```
00:10:52.006  Mission 436af975… loaded: 3 flights
00:10:52.021  Extracted 3 GPS tracks (33830 total points)
              ← no "Mission … geo: … acres" line ever printed
00:11:08      kernel: Memory cgroup out of memory: Killed process … (uvicorn) … anon-rss:1043MB
```

Bill retried at 00:12 → identical sequence → second OOM at 00:12:23.
`dmesg` showed four uvicorn OOM-kills in the window, each `anon-rss ≈ 1043 MB`,
`constraint=CONSTRAINT_MEMCG`, `oom_memcg=docker-…scope` (the backend container).
`docker inspect` reported `OOMKilled=false` — a cgroup-v2 quirk: the uvicorn
**worker** PID is killed and respawned by the master, so the *container* is not
flagged, but the in-flight request still dies.

Line-level reproduction in-container (isolated process, same 1 GiB cgroup):

```
after build 3 LineStrings   rss=135MB
after MultiLineString       rss=135MB
<SIGKILL inside .buffer(30)>  EXIT=137   (128 + SIGKILL)
```

Secondary risk found: `render_static_map` fed all 33,830 points to `staticmap`
and called `m.render()` with **no tile-fetch timeout** (`staticmap` default is
`None`). A slow/blocked `tile.openstreetmap.org` could hang the request past
Cloudflare's ~100 s edge window → **524**.

## Decision

Keep the in-request geometry (it feeds the report record and Celery payload) but
make it **bounded and cheap**:

1. **Simplify each track in projected space before buffering.** In
   `calculate_area_acres`, Douglas-Peucker-simplify each UTM `LineString` at
   `GEO_SIMPLIFY_TOLERANCE_M = 2.0` m, **buffer each line independently**, and
   `unary_union` the polygons. Every intermediate geometry stays small.
2. **Cap rasteriser vertices.** `render_static_map` decimates each track to
   `MAX_RENDER_VERTICES_PER_TRACK = 2000` (strided, endpoints preserved) — the
   raw points are vastly sub-pixel on an 800×600 PNG, so this is visually
   identical and bounds cost.
3. **Bound the tile fetch.** Pass `tile_request_timeout=10` to `StaticMap` so a
   slow OSM tile cannot hang the request; on failure the map is skipped and the
   report still generates (existing `try/except`).

### Why simplification is correct, not just cheap

Live convergence sweep on the offending mission (per-line buffer + union):

| tolerance | vertices | acres |
|-----------|----------|-------|
| 5.0 m     | 114      | 68.51 |
| 2.0 m     | 170      | 68.91 |
| 1.0 m     | 231      | 69.01 |
| 0.5 m     | 343      | 69.11 |

Spread **0.6 acres (<1%)** while collapsing 33,830 → ~170 vertices. Peak RSS for
the whole geo pipeline dropped from **>1 GiB (OOM)** to **136 MB**, in **0.05 s**.
2 m is well below GPS noise and the 30 m coverage buffer, so it does not affect
the reported figure materially.

## Alternatives considered

- **Raise `mem_limit` only.** A band-aid: a larger multi-flight mission would
  still OOM, and it wastes RAM on the shared BOS-HQ host. Rejected as a
  standalone fix. (Not needed — the real per-request footprint is now ~136 MB.)
- **Move the whole geo pipeline into the Celery worker.** Larger change; the
  endpoint persists `acres`/`map_path` on the Report row synchronously and the
  frontend polls for the LLM body separately. Deferred — the cheap, bounded
  in-request computation is sufficient and lowest-risk for an urgent unblock.
- **Drop the area calculation.** It is a customer-facing deliverable field.
  Rejected.

## Consequences

- "Generate Report" no longer OOM-kills the worker; the request returns a normal
  JSON `{task_id, status}` and CF sees a complete response. No more 520.
- Reported acreage changes by <1% vs the (previously uncomputable) raw buffer.
- The static map renders from a decimated path (visually identical) and can no
  longer hang on a slow tile server.
- Resilience guard (CLAUDE.md): runtime-only Python change; no DB schema, ports,
  replication, or blue-green impact; survives container recreation (code is in
  the image). No failover surface touched.

## Follow-up hardening (not required to unblock)

- Consider moving the geo pipeline fully into the Celery worker so the HTTP
  request returns instantly regardless of mission size.
- The new InfraWatch `droneops-backend-mem` Grafana rule (working-set vs the
  1 GiB limit) now covers this surface; the 80% ratio auto-rescales if
  `mem_limit` is ever changed.
