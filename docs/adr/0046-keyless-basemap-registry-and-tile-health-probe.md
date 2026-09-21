# ADR-0046: Keyless basemap registry + tile-health probe

**Date:** 2026-09-21
**Status:** Accepted — code, tests and docs shipped in one commit against `main`
at v2.92.0; deploy is the operator's push (ADR-0018).
**Supersedes in practice:** the five hard-coded CARTO/Esri/OSM tile URLs that
existed in `frontend/` before this change.
**Research this is built on:** `docs/reports/2026-09-21-basemap-provider-eval.md`
(committed alongside — probe results, zoom ladders, terms analysis, sources).

## Context

Every map in this app defaulted to CARTO's keyless raster basemap,
`https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png`. Since roughly
**2026-08-28** those tiles come back with a diagonal **"API KEY REQUIRED"**
watermark burned into the pixels.

The architecturally important part is not that CARTO changed its terms. It is
**how the failure presented**: HTTP **200**, `content-type: image/png`,
`access-control-allow-origin: *`, `cache-control` intact, 256x256, 5,103 bytes —
every header healthy, the image itself degraded. Verified by fetching
`https://a.basemaps.cartocdn.com/dark_all/10/163/373.png` on 2026-09-21 and
looking at it. The legacy Fastly host
(`cartodb-basemaps-a.global.ssl.fastly.net`) returns a **byte-identical**
watermarked tile, so there is no back door.

Ranked against that failure, every control we might reach for:

| Control | Would it have caught this? |
|---|---|
| Backend tile proxy with fallback on HTTP error | **No.** 200 OK. |
| Leaflet `tileerror` → next provider | **No.** The image loaded fine. |
| Uptime/healthcheck on the tile host | **No.** Host up and fast. |
| Disk/Redis tile cache | **No — actively worse.** It caches the watermark for its TTL. |
| **Pixel-level fingerprint probe** | **Yes.** The only one. |
| A human opening the app | Yes — which is how we found it, **24 days later**. |

The secondary problem is that the URLs lived in five places
(`FlightMap.tsx`, `Telemetry.tsx`, `FlightReplay.tsx`, `Airspace.tsx`,
`FlightVideoExporter.tsx`), so "swap the provider" was a five-file hunt. Two
smaller defects surfaced while reading them: all four map pages used the
`{s}.tile.openstreetmap.org` subdomain form the OSM Foundation no longer
specifies, and four call sites passed bare `attribution="Esri"` / `"OSM"` while
`FlightMap` passed `attributionControl={false}` and rendered none at all —
neither provider's terms accept that.

## Decision

### 1. One registry — `frontend/src/lib/basemaps.ts`

All tile URLs, attributions, zoom ceilings and fallback chains live in one
module. `<BasemapLayers/>` (`frontend/src/lib/BasemapLayers.tsx`) renders it as
a `LayersControl` plus an attribution control, and all four map pages mount that
single component. No call site contains a tile URL. This is the change that
makes every other decision here cheap to reverse.

### 2. Keyless Esri raster + OSM, with measured zoom ceilings

Four sets, default **Dark**:

| Set | Layers | Native zoom |
|---|---|---|
| **Dark** (default) | `Canvas/World_Dark_Gray_Base` + `Reference/World_Transportation` | 16 / 19 |
| **Satellite** | `World_Imagery` | 19 |
| **Hybrid** | `World_Imagery` + `Reference/World_Boundaries_and_Places` + `Reference/World_Transportation` @ 0.75 opacity | 19 / 16 / 19 |
| **Street** | `tile.openstreetmap.org` (no `{s}`) | 19 |

Three things that are easy to get wrong and are pinned by tests:

- **Esri's axis order is `{z}/{y}/{x}`**; OSM's is `{z}/{x}/{y}`. A transposed
  template returns a real tile of somewhere else entirely — HTTP 200, wrong
  place.
- **`maxNativeZoom` is measured, not advertised.** Esri's service metadata
  claims LOD 23 for every layer. In reality each service returns one
  byte-identical blank filler tile at every zoom past its real data (report
  §3.2, measured over Eugene). `maxZoom` is set to 20 so Leaflet upsamples the
  deepest real tile rather than requesting filler.
- **The transportation overlay is what makes the z16 dark base acceptable.**
  Below z16 you get Esri's dark canvas as-is; above it the base upsamples soft
  while roads and street names stay crisp from the z19-deep transportation
  layer — which is the information a drone operator actually needs at that
  zoom.

**`detectRetina` is off.** None of these providers serves an `@2x` variant, so
Leaflet would fall back to its zoom+1/half-tile trick — and it applies that
`zoomOffset` *after* clamping to `maxNativeZoom`, so at the ceiling it would
request one zoom past the deepest real tile and get the blank filler. On a
retina device the dark base would go blank at z16 instead of merely soft.

**`Canvas/World_Dark_Gray_Reference` was evaluated and rejected.** Composited
over the dark base at z12 and z13 over Eugene and inspected: it contributes a
single faint city label which the transportation layer then overprints with road
casings and highway shields, rendering it unreadable. It is blank from z16 up.
Transportation alone carries every label that survives the composite.

**OSM is a user-selectable layer only, never the default.** The OSMF Tile Usage
Policy tolerates human-driven viewing and can withdraw access without notice; it
forbids pre-emptive or bulk fetching. A test asserts OSM is not in the default
set.

### 3. Attribution on every map

`<BasemapLayers/>` renders `<AttributionControl prefix={false}/>` and every
`MapContainer` now passes `attributionControl={false}` so exactly one control
exists. The strings are verbatim from each MapServer's own `copyrightText`
(`?f=pjson`, pulled 2026-09-21). A test fails any layer whose attribution is
shorter than 20 characters, which is what made `"Esri"` possible.

### 4. Client-side failover — and what it is *not*

`TileFailoverState` swaps a set for its registry fallback (Dark → Street) after
six real tile failures inside ten seconds, logs once, shows no modal. Errors on
tiles Leaflet aborted mid-pan are excluded by checking that the element still
holds an `http(s)` src — Leaflet's `_abortLoading` swaps in a `data:` URI before
discarding a tile, so a fast pan cannot trip the failover.

**This covers provider outage only.** It is structurally incapable of detecting
the CARTO failure, because those tiles loaded successfully. Both the code
comment and the console message say so, so the next engineer does not mistake it
for the control.

### 5. Report renderer keeps OSM, with a compliant User-Agent

`app/services/map_renderer.py` fetches `tile.openstreetmap.org` server-side for
report PNGs. The OSMF policy requires "a clear, unique User-Agent string" naming
the application and explicitly names sending a library's default UA as a thing
you must not do — and `staticmap` 0.5.7 defaults to `User-Agent: StaticMap`,
exactly that case. **Checked the installed version: it accepts a `headers` dict**
(`StaticMap(..., headers={...})`, staticmap.py:182), so the fix is one argument
and the renderer stays on OSM. Verified on the wire against a local HTTP server:
four tile requests, all carrying
`DroneOpsCommand/2.92.0 (+https://droneops.barnardhq.com; bill@barnardhq.com)`.
Report maps are a handful of tiles over a bounded area at low volume, which the
policy allows; what it forbids is pre-emptive or cached bulk fetching, and this
renderer does neither.

The UA and `APP_VERSION` live in `app/version.py`, a **new seventh version
location**. `tests/test_app_version_parity.py` parses `app/main.py` and fails if
the two drift, so a half-finished bump is a red test rather than a server
identifying itself as the wrong release. CLAUDE.md's bump list is updated.

### 6. Video exporter: z+1 at half scale

`FlightVideoExporter.tsx` drew CARTO `@2x` 512px tiles onto a canvas. Esri has
no retina variant, so density now comes from fetching one zoom deeper and
drawing each tile at half size; a layer clamped below the display zoom by its
native ceiling simply upsamples, using the same arithmetic. The exporter
composites the full Dark stack (base + transportation), so an exported flight
still looks like the app. `crossOrigin='anonymous'` is kept — every provider
sends `ACAO: *`, so the canvas stays untainted and `captureStream()` works.

A `MAX_TILES_PER_LAYER = 180` cap steps the requested zoom back down rather than
letting one export become many hundred requests at a provider we are a keyless
guest of.

### 7. Tile-health probe — the actual "never again"

`app/services/basemap_probe.py`, run weekly by Celery beat
(`probe_basemap_tiles`, Mondays 15:47 UTC ≈ 08:47 PT — inside waking hours,
clear of the ADR-0037 quiet window, offset from every other beat entry).

It fetches **one fixed tile per provider** (z10/x163/y373, Eugene — the tile the
provider evaluation fingerprinted by hand) and records status, byte size,
dimensions and **two 64-bit perceptual hashes**, then grades them against a
checked-in baseline captured today (`app/services/basemap_baseline.json`,
regenerable via `python -m app.services.capture_basemap_baseline`).

- **No new dependency.** `imagehash` pulls numpy + scipy + PyWavelets, none of
  which this backend has. Pillow is already pinned, so aHash and dHash are ~15
  lines against Pillow alone.
- **Two hashes** because they fail differently: a watermark moves the gradient
  hash hardest, a palette change moves the average hash hardest.
- **Transparent overlays are flattened onto black before hashing.** Three of the
  five layers are transparent PNGs, and `convert("L")` on RGBA discards alpha.
- **Blankness is checked before the hashes, and this is load-bearing.** A sparse
  label overlay hashes to mostly zeros, so when such a layer runs out of data and
  returns the provider's blank filler, the Hamming distance from baseline is
  *tiny* — measured at **3** against the live `esri_boundaries_places` baseline,
  well inside the threshold. A hash-only probe would call an empty tile healthy.
  The byte-size band catches it too (872 B vs a 2,618 B baseline), but the
  explicit blank verdict is what makes it unambiguous.

Detection was validated against the real defect class rather than asserted: a
watermark stamped into each of the five live tiles moves the hashes by **10–35
bits** against a threshold of 8, and the same clean tile against its own
baseline reads `ok` (the negative control, without which the test would pass on
a probe that calls everything changed).

Every run is written to `system_settings.basemap_probe_last_result` and emitted
as a structured log line. Three admin endpoints expose it:
`GET /api/admin/basemap/tile-health`, `POST .../run` (60s cooldown — a button
someone can hold down is exactly the automated repeated fetching the OSMF policy
exists to stop), `PUT .../ntfy`.

**No Prometheus gauge.** Grepped for `prometheus_client` and a `/metrics` route:
this backend exposes neither, so a gauge would have needed a new dependency and
a new scrape target. The structured log line carries the same fields
(`ok`, `layers_ok`, `layers_total`, `failing`) into Loki, where a Grafana panel
can be built on it without shipping anything.

**ntfy is wired but DEFAULT OFF** behind `basemap_probe_ntfy_enabled`. The
Hamming and byte-band thresholds are starting points, not measurements —
basemaps legitimately change when a provider refreshes data, and a probe whose
first data refresh pages falsely gets muted, which is worse than no probe.
Observe-only for at least two weeks (ROADMAP MP-2, earliest 2026-10-05). When
armed it publishes at `default` priority (ADR-0037: not customer-visible, not
actionable in five minutes) on the repo's existing **`droneops-alerts`** topic
rather than a new `droneops-basemap` one — a new ntfy topic is a black hole
until Bill subscribes on his phone, and reusing a subscribed topic is worth more
than topic-level tidiness for a signal this rare. Cooldown 24h, deduped on the
*set of failing layers* (keying it on anything run-unique would make the cooldown
a decoration). Click URL `https://droneops.barnardhq.com/telemetry` — tier 1, a
page that actually renders a map.

### 8. One registry, mirrored — and enforced

The probe needs the URL list at runtime inside the backend image, but
`docker-compose.yml` builds `frontend` and `backend` from separate contexts
(`./frontend`, `./backend`), so no single file can be present in both. The
frontend registry stays authoritative; `app/services/basemap_registry.py` is a
mirror, and `tests/test_basemap_registry_parity.py` parses the TypeScript out of
the checkout and fails on any divergence in either direction. Add a provider on
one side only and the suite goes red.

## Alternatives considered

**CARTO with a free API key — rejected.** Three independent reasons, any one
sufficient: (1) the free tier is documented as *non-commercial* ("personal
projects, research, teaching, non-profits") and DroneOpsCommand is a commercial
internal product; routing it through our own proxy would hide that from CARTO's
analytics, which is worse, not better; (2) CARTO says the raster basemaps are
"being retired" and they are "considering stopping data updates" — we would buy
a seat on a sinking product with no published EOL date; (3) it optimises for the
wrong risk, solving "hide the key" rather than "provider silently degrades the
pixels".

**Stadia Maps `alidade_smooth_dark` — rejected on terms, kept as the paid exit.**
Keyless requests return 401. Genuinely the best-looking dark raster of the lot,
512px retina-native, deep zoom — but the free plan excludes commercial use and
the commercial entry point is **Starter, $20/month**. If Bill wants CARTO-grade
dark aesthetics immediately, this is a one-line registry change. That it is
one line is the point of §1.

**MapTiler / Thunderforest — rejected**, same terms shape as Stadia with weaker
free plans.

**USGS `ImageryOnly` — rejected**, z16 ceiling over Eugene.

**OpenFreeMap — deferred.** Keyless, free, no quota, excellent — and a MapLibre
GL style, not raster tiles. Consuming it means replacing react-leaflet across
five map components and re-implementing every overlay. That is a mapping-library
migration, not a basemap swap. Also bus-factor 1.

**Backend tile proxy with a disk/Redis cache — rejected (YAGNI, and worse).**
It would not have caught this bug; a cache in front of OSM is a Tile Usage
Policy violation (pre-emptive fetching and tile archiving); it puts a new
stateful hop with its own disk-pressure and eviction failure modes in front of
every map view; and it routes all tile traffic through our egress to hide a key
we do not have. Revisit only if we ever go keyed.

## Consequences

**Good.** No watermark. Zero cost, no key, no account. A provider swap is now a
one-line edit in one file instead of a five-file hunt. Both providers are
properly attributed for the first time. The report renderer is OSM-policy
compliant for the first time. A silent pixel-level degradation is now found in
≤7 days instead of 24, by the only class of control that can see it. The
exporter's video is sharper at high zoom than the old `@2x` path at the same
display zoom.

**The accepted risk — Esri's keyless terms are unresolved, and material.** Esri
has pushed developers off `server.arcgisonline.com` toward a keyed basemap layer
service since 2022; their own blog told open-source developers to migrate
"before 2022-04-30", and community threads state that use in a non-Esri client
such as Leaflet is not covered without a subscription. The endpoints have kept
serving keylessly for four years past that date because they back Esri's own
products. The substantive text of Esri's Master Agreement could not be retrieved
(the terms page serves links to PDFs). **This is structurally the same bet as
CARTO** — a widely used free legacy raster endpoint the vendor wants to retire —
and it is taken with eyes open because the mitigations are real: swap cost ≈ one
line, detection ≤ 7 days, and two documented exits (Stadia at $20/month;
Protomaps on R2).

**Soft above z16 on the Dark base.** CARTO rendered dark to z20; Esri's dark
canvas stops at z16. The transportation overlay keeps roads and labels sharp to
z19, so the softness is in the backdrop, not the information — but it is a real
visual regression at drone zoom and the honest fix is Protomaps (ROADMAP MP-1).

**Zoom ceilings are location-specific.** Every ladder was measured over Eugene,
OR. `World_Imagery` reaches z19 there and z20–21 over some metros. If flights
move outside the Willamette Valley, re-measure before trusting `maxNativeZoom`
(ROADMAP MP-3).

**Probe thresholds are unvalidated** until two weeks of observe-only data exist
(ROADMAP MP-2). Until then the probe records and logs but never pages.

**A seventh version location** (`backend/app/version.py`). Guarded by a test;
CLAUDE.md updated.

## Failover & Resilience Guard (CLAUDE.md, mandatory)

1. **PostgreSQL streaming replication** — untouched. No port, `pg_hba` or
   connection-string change. The probe writes one `system_settings` row through
   the normal application session.
2. **Container recreation** — survives. The registry, the mirror and the
   baseline fixture are source files in the images; the probe's persisted result
   is a DB row; the ntfy gate is a DB row and defaults closed when absent.
3. **Blue-green swap** — unaffected. No init script, no volume, no migration.
   `system_settings` is an existing table; the two new keys are created on first
   write.
4. **Failover engine** — unaffected. No quorum, health-monitor or WireGuard
   surface is touched. The new beat entry runs on the single existing `beat`
   sidecar and is idempotent: a missed week is a missed week.
5. **Customer-facing impact during a site failover** — none. Tiles are fetched
   by the browser directly from the provider; if the backend is mid-failover the
   maps still render. The probe is admin-only and best-effort, and its
   persistence failure path is caught and logged rather than raised.
