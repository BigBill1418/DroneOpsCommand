# Basemap Provider Evaluation — replacing CARTO in DroneOpsCommand

**Date:** 2026-09-21
**Repo:** DroneOpsCommand (`/home/bbarnard065/droneops`), main @ `d30eb5b`, app v2.91.0
**Stack:** React + Leaflet 1.9.4 + react-leaflet 4.2.1; FastAPI backend
**Status:** Research only. No application code changed, nothing committed.
**Author:** Terry (research/architecture pass)

---

## 1. The problem, verified

Every map surface in the app defaults to CARTO's free raster basemap. As of today those tiles come
back **HTTP 200, `content-type: image/png`, `access-control-allow-origin: *`** — and with a
diagonal **"API KEY REQUIRED / carto.com/basemaps/apikey"** watermark burned into the pixels.

Verified 2026-09-21 by fetching `https://a.basemaps.cartocdn.com/dark_all/10/163/373.png`
(200, 5,103 bytes, PNG 256x256 4-bit colormap) and viewing it. The watermark is present.

**This is the important architectural fact:** the failure is *inside the image*. Status code,
content-type, CORS headers and cache headers are all healthy. No `tileerror` handler, no HTTP
retry, no status-code check anywhere in the frontend or in a backend proxy can detect it. Only
something that *looks at the pixels* can. That constraint drives the whole "never again" design
in §5.

### 1.1 Call sites that must change

| File | Line | Layer |
|---|---|---|
| `frontend/src/components/FlightMap/FlightMap.tsx` | 107 | Dark = CARTO `dark_all` |
| `frontend/src/pages/Telemetry.tsx` | 217 | Dark = CARTO `dark_all` |
| `frontend/src/pages/FlightReplay.tsx` | 348 | Dark = CARTO `dark_all` |
| `frontend/src/pages/Airspace.tsx` | 374 | Dark = CARTO `dark_all` |
| `frontend/src/components/FlightVideoExporter.tsx` | 90 | CARTO `dark_all` **@2x** onto a canvas for MP4 export |
| `backend/app/services/map_renderer.py` | 267 | `tile.openstreetmap.org` server-side, report PNGs |

Two secondary defects found while reading those sites:

- All four frontend sites use the **`{s}.tile.openstreetmap.org`** subdomain form. The OSM
  Tile Usage Policy now specifies only `https://tile.openstreetmap.org/{z}/{x}/{y}.png` and warns
  that "other subdomains or hostnames may be slower or withdrawn without notice." Drop the `{s}`.
- `FlightVideoExporter.tsx:90` depends on **`@2x` retina tiles**, which CARTO served and
  **Esri does not** (see §3.2). That call site needs a different strategy, not just a URL swap.

---

## 2. What CARTO changed, and whether a free key fixes it

**Primary sources:**
- <https://docs.carto.com/faqs/carto-basemaps>
- <https://carto.com/basemaps/apikey/>

**Findings (fetched 2026-09-21):**

1. **A key is now required** for the raster basemaps at `basemaps.cartocdn.com`. Anonymous
   requests are served watermarked. The change landed around **2026-08-28**.
2. **The raster basemaps are being retired.** CARTO's own words: raster "is the older of the two
   and is being retired," they are "considering stopping data updates to the raster basemaps," and
   new applications "should use the vector basemaps instead." **No end-of-life date is published.**
3. **The free key is 5,000,000 tile requests / calendar month**, counted across raster + vector.
4. **The free key is documented as "intended for non-commercial use"** — personal projects,
   research, teaching, non-profits. For a commercial project CARTO says they "may ask you to move
   onto a commercial agreement." DroneOpsCommand is a commercial internal product.
5. The key is a `?key=` query parameter; domain/IP restriction is optional, so it *can* be
   exposed in frontend code.

### Verdict on the CARTO-key-via-backend-proxy candidate: **reject.**

Three independent reasons, any one of which is sufficient:

- **Terms.** The free tier is explicitly aimed at non-commercial use. A commercial app on it is
  living on a "we may ask you to move" clause. Routing it through our own proxy does not change
  the terms; it just hides the violation from CARTO's own analytics, which is worse.
- **Product direction.** The raster service we would be keying into is the one CARTO is retiring
  and may stop updating. We would be spending an engineer-day to buy a seat on a sinking product
  and would have to redo this work when raster goes away.
- **It optimises for the wrong risk.** A proxy solves "hide the key"; it does not solve
  "provider silently degrades the pixels," which is the failure we actually just had.

---

## 3. Candidate evaluation

Every URL below was fetched with `curl` on 2026-09-21 from HSH-HQ and, where marked ✅ viewed,
the returned image was opened and visually inspected for watermarks.

### 3.1 Live probe results

| Candidate | HTTP | Bytes | Type | CORS `ACAO` | Watermark? |
|---|---|---|---|---|---|
| CARTO `dark_all` z10/163/373 | 200 | 5,103 | image/png | `*` | **YES — viewed ✅** |
| CARTO `rastertiles/voyager` | 200 | 10,721 | image/png | `*` | YES (operator-verified) |
| `cartodb-basemaps-a.global.ssl.fastly.net/dark_all` (legacy host) | 200 | 5,103 | image/png | `*` | **YES — byte-identical to above** |
| **Esri `Canvas/World_Dark_Gray_Base`** | 200 | 4,317 | image/jpeg | `*` | **NO — clean, viewed ✅** |
| **Esri `Canvas/World_Dark_Gray_Reference`** | 200 | 872 | image/png | `*` | NO |
| **Esri `World_Imagery`** | 200 | 13,327 | image/jpeg | `*` | NO |
| **Esri `Reference/World_Boundaries_and_Places`** | 200 | 2,618 | image/png | `*` | NO |
| **Esri `Reference/World_Transportation`** | 200 | 7,889 | image/png | `*` | **NO — clean, viewed ✅ at z18** |
| `tile.openstreetmap.org` | 200 | 24,540 | image/png | `*` | NO |
| `tile.openstreetmap.de` | 200 | 22,282 | image/png | — | NO |
| `basemap.nationalmap.gov` USGSImageryOnly | 200 | 19,478 | image/jpeg | — | NO |
| Esri `Canvas/World_Light_Gray_Base` | 200 | 3,979 | image/jpeg | `*` | NO |
| **Stadia `alidade_smooth_dark`** | **401** | 14,885 | image/png | — | key required |
| `tiles.openfreemap.org/styles/dark` | 200 | 20,959 | application/json | — | vector style JSON, not raster |

Note the legacy Fastly host `cartodb-basemaps-a.global.ssl.fastly.net` returns a **byte-identical**
watermarked tile. There is no back door.

### 3.2 The zoom-depth finding (this is the one that changes the design)

I walked a zoom ladder over Eugene, OR (44.0521, -123.0868) and hashed each tile. When a service
runs out of real data it returns the **same byte-identical filler tile at every deeper zoom**:

```
darkgray   z14:10852B/0b163ec8  z15:13127B/4261da5a  z16:9913B/2f91d374  z17:2521B/f27d9de7  z18:2521B/f27d9de7  z19:2521B/f27d9de7
darkref    z14: 2410B/cb5d2a19  z15: 2376B/21d58cd8  z16: 872B/9cba0016  z17: 875B/2e3f659f  z18: 875B/2e3f659f  z19: 875B/2e3f659f
bnp        z14: 4186B/7ca659a0  z15: 6571B/25e7b4b7  z16:4021B/fc0cf952  z17: 872B/9cba0016  z18: 872B/9cba0016  z19: 872B/9cba0016
transport  z14:24303B/f64095b0  z16:15695B/48b7238e  z17:6831B/e308df10  z18:4733B/966b4a09  z19:3459B/463989f1  z20:875B/2e3f659f
imagery    z14:22055B/ee35ee14  z16:19090B/d1516d8f  z17:16996B/c995a29b z18:14568B/587fd265 z19:11335B/627e19a7 z20:2521B/f27d9de7
usgsimg    z14:34883B/ee6f6c14  z16:31603B/0d9498b8  z17: 572B/a98eb50d  z18: 572B/a98eb50d  z19: 572B/a98eb50d
```

Real max native zoom, measured, over Eugene:

| Service | Real data to | Above that |
|---|---|---|
| Esri **World Dark Gray Base** | **z16** | blank 2,521 B filler |
| Esri World Dark Gray Reference | z16 | blank filler |
| Esri World Boundaries_and_Places | z16 | blank filler |
| Esri **World Transportation** | **z19** | blank at z20 |
| Esri **World Imagery** | **z19** | blank at z20 |
| USGS ImageryOnly | z16 | blank filler |

Consequences:

- Esri's dark canvas **cannot be a drop-in for CARTO at drone zoom.** CARTO `dark_all` renders to
  z20. Esri Dark Gray stops at z16. Set `maxNativeZoom: 16, maxZoom: 19` and Leaflet upsamples the
  z16 tile — functional, but soft above z16.
- **Mitigation that makes this acceptable:** pair the dark base with **World Transportation**,
  which has real data to **z19**. Roads and street names stay razor-sharp exactly where the base
  goes soft. Viewed at z18 over Eugene: white road casings and haloed street labels
  ("E 7th Ave") on transparent PNG — legible over a dark base.
- **USGS ImageryOnly is out** as a satellite option (z16 ceiling over Eugene).
- `?blankTile=false` makes Esri return **404** instead of the filler tile
  (verified: `.../World_Dark_Gray_Base/MapServer/tile/18/95755/41036?blankTile=false` → **404**).
  Useful for the health probe in §5; do **not** put it on the live layers — use `maxNativeZoom`
  so Leaflet never issues the request at all.
- **Esri serves no `@2x` retina variant.** `?blankTile=false` and any size hint still return a
  256x256 JPEG (verified: 4,317 B, 256x256). This breaks `FlightVideoExporter.tsx:90` as written.

### 3.3 Per-candidate assessment

#### Esri ArcGIS Online raster tiles (`server.arcgisonline.com`) — **recommended, with an eyes-open risk**

- **Keyless?** Yes, today. No key, no account, no Referer requirement, `ACAO: *`,
  `Cache-Control: max-age=86400`, served off CloudFront.
- **Cost:** $0.
- **Terms — the real risk.** Esri has been pushing developers off the legacy
  `server.arcgisonline.com` / `services.arcgisonline.com` endpoints since 2022, toward a keyed
  "basemap layer service" that requires a free ArcGIS Developer account (2M tiles/month). Esri
  community threads state that using these basemaps in a non-Esri client such as Leaflet or
  OpenLayers is not covered without a subscription; Esri's own blog told open-source developers to
  migrate "before April 30, 2022." The legacy endpoints have kept serving keylessly for four years
  past that date because they back Esri's own products, **but this is precisely the CARTO
  setup**: a widely-used free legacy raster endpoint the vendor wants to retire.
  I could not locate a clause in Esri's published Master Agreement that either grants or denies
  anonymous third-party use — the terms page links to PDFs whose substantive text I could not
  retrieve in this time-box. **Treat Esri keyless as a gray-zone dependency, not a guarantee.**
  This is the single largest unverified item in this report.
- **Max zoom:** service metadata advertises LOD 23 for every layer; **real data is as measured in
  §3.2**. The advertised number is a lie for planning purposes.
- **Retina:** none.
- **CORS:** `Access-Control-Allow-Origin: *` — confirmed with an `Origin:` header. Canvas export
  with `img.crossOrigin='anonymous'` will work and the canvas stays untainted.
- **Attribution:** taken verbatim from each MapServer's `copyrightText` (see §6).
- **Longevity:** medium. Four years past its own migration deadline. Could gate at any time.

#### OpenStreetMap standard tiles — **keep as the Street layer only; never the default**

- **Keyless?** Yes. `ACAO: *`, but `cache-control: no-cache`.
- **Policy (<https://operations.osmfoundation.org/policies/tiles/>):** OSMF's Tile Usage Policy
  prohibits "bulk downloading" — defined as "any pre-emptive fetching of tiles other than those a
  user is actively viewing," including pre-seeding, tile archives, and automated scans at z≥14.
  It requires "a clear, unique User-Agent string that names your app," a valid HTTP `Referer` from
  web pages, and attribution. It sets **no numeric threshold** and states "we may block access,
  without notice, if your usage degrades the service."
- **Implications for us, explicitly:**
  - A human-driven Street layer in a small internal app is fine.
  - **A backend tile proxy with a disk/Redis cache in front of OSM is a policy violation** —
    a cache is pre-emptive fetching and tile archiving. This is a hard argument against the
    proxy-with-cache architecture for OSM specifically.
  - `backend/app/services/map_renderer.py:267` fetches OSM **server-side** for report PNGs. Each
    report renders a bounded area at low volume, so it is not bulk downloading, but it must send a
    compliant, app-identifying User-Agent. It currently relies on `staticmap`'s default UA — the
    policy names using "a library default User-Agent" as a thing you must not do. **Fix this.**
- **Max zoom:** 19. **Retina:** none. **Longevity:** high for the service, but our right to use it
  is conditional on staying light.

#### Stadia Maps `alidade_smooth_dark` — **reject on terms**

- Keyless request returns **HTTP 401** with a PNG body. A key is mandatory.
- Free plan: 200,000 credits/month, no card. **Commercial use is not permitted on the free plan.**
  Stadia's own docs: usage is commercial "if it is in a product or service that generates revenue
  ... or if the organization using it is for-profit at all." Development/testing/demo are allowed.
- Commercial entry point is **Starter, $20/month** for 1M credits, 3 cents/1,000 overage.
- Genuinely the best-looking dark raster of the lot, 512px retina-native, deep zoom. If Bill will
  spend $20/month this is the highest-quality answer and the one that most closely reproduces the
  look he had. **Flagged as the paid upgrade path, not the recommendation.**
  Sources: <https://docs.stadiamaps.com/limits/>, <https://stadiamaps.com/pricing/>

#### MapTiler — **reject for this pass**

Key required; free tier is watermarked/limited and commercial use is a paid tier. Same shape as
Stadia but with a weaker free plan. No probe run — excluded on terms before cost of verification.

#### Thunderforest — **reject**

Key required; free tier is explicitly non-commercial and capped at 150k tiles/month. Same terms
failure as Stadia/CARTO. No probe run.

#### OpenFreeMap — **reject for today, note for later**

- `https://tiles.openfreemap.org/styles/dark` returns **200, 20,959 bytes, `application/json`** —
  a MapLibre GL style, not raster tiles. Confirmed it references a vector source
  (`openmaptiles` → `https://tiles.openfreemap.org/planet`) plus a Natural Earth raster underlay.
- **Keyless, free, no quota, no attribution beyond OSM.** Run by one maintainer (Zsolt Ero) on
  donated infrastructure — genuinely excellent, genuinely a bus-factor-1 service.
- **Migration cost is the blocker.** Consuming it means MapLibre GL JS, which means replacing
  `react-leaflet` across five map components, re-implementing every overlay (flight tracks,
  airspace polygons, markers, the replay scrubber) against a different API. This is multi-day,
  not one day. It is not a basemap swap; it is a mapping-library migration.

#### Protomaps PMTiles self-hosted on Cloudflare R2 — **the real long-term answer; roadmap it**

- The fleet already runs R2 and already has the ADR-0232 backup posture around it.
- A PMTiles archive is a single file served over HTTP **Range** requests — R2 supports this
  natively, no tile server, no per-tile origin cost.
- **Critically, this does not force MapLibre.** `protomaps-leaflet` renders Protomaps vector tiles
  to a canvas **inside Leaflet**, with a built-in dark theme. Verified on the npm registry today:
  `protomaps-leaflet@5.1.0`, BSD-3-Clause, published **2025-06-18** (15 months old — stable but
  not actively moving). `pmtiles@4.5.0`, BSD-3-Clause, published **2026-08-10** (actively
  maintained).
- **What it buys:** a dark basemap we own outright. Sharp at every zoom, retina-native by
  construction (vector), restyleable to match the app's dark theme exactly, and structurally
  incapable of being watermarked, rate-limited, or retired by a vendor.
- **Cost:** ~1-2 engineer-days plus a build/refresh pipeline for the extract. A Pacific-Northwest
  or CONUS extract is a manageable file; planet is ~120 GB and not warranted.
- **Verdict:** this is the answer to "never again," but it is not a one-engineer-day answer.
  Roadmap it; do §4 today.

### 3.4 Decision matrix

| | Keyless | $ | Commercial OK | Real max z | Retina | CORS | Longevity | Verdict |
|---|---|---|---|---|---|---|---|---|
| **CARTO raster (keyed)** | No | $0 free tier | **No — "non-commercial"** | 20 | @2x | `*` | **Low — being retired** | Reject |
| **Esri Dark Gray Canvas** | **Yes** | $0 | Gray zone | **16** | No | `*` | Medium | **Adopt (Dark base)** |
| **Esri World Transportation** | **Yes** | $0 | Gray zone | **19** | No | `*` | Medium | **Adopt (label overlay)** |
| **Esri World Imagery** | **Yes** | $0 | Gray zone | **19** | No | `*` | Medium | **Adopt (Satellite)** |
| **Esri Boundaries_and_Places** | **Yes** | $0 | Gray zone | 16 | No | `*` | Medium | **Adopt (place labels)** |
| **OSM standard** | **Yes** | $0 | Yes, if light | 19 | No | `*` | High (conditional) | **Adopt (Street only)** |
| Stadia alidade_smooth_dark | No (401) | $20/mo | Paid tier only | 20 | **512px** | `*` | High | Paid upgrade path |
| MapTiler | No | Paid | Paid tier only | 20+ | Yes | `*` | High | Reject |
| Thunderforest | No | Paid | Paid tier only | 22 | Yes | `*` | Medium | Reject |
| USGS ImageryOnly | **Yes** | $0 | Yes (US Gov) | **16** | No | ? | High | Reject — z16 ceiling |
| OpenFreeMap (vector) | **Yes** | $0 | Yes | 20 | Vector | `*` | **Bus factor 1** | Defer — library migration |
| **Protomaps on R2** | N/A (ours) | ~$0 R2 | **Yes — we own it** | any | Vector | ours | **Highest** | **Roadmap** |

---

## 4. Recommended layer set

Four entries in the `LayersControl`, default **Dark**. All keyless, all verified clean today.

**Dark (default)** — two stacked layers:
1. Base: Esri World Dark Gray Base, `maxNativeZoom: 16`, `maxZoom: 19`
2. Overlay (always on with Dark): Esri World Transportation, `maxNativeZoom: 19`, `maxZoom: 19`

The overlay is what makes this work. Below z16 you get Esri's dark canvas as-is. Above z16 the base
upsamples (soft, but it is a dark backdrop, not the information), while roads and street names
stay crisp from the z19-deep transportation layer — which is exactly the information a drone
operator needs at that zoom.

**Satellite** — Esri World Imagery, `maxNativeZoom: 19`, `maxZoom: 19`.

**Hybrid** — World Imagery + World Boundaries_and_Places (`maxNativeZoom: 16`) +
World Transportation (`maxNativeZoom: 19`). Place names at low zoom, streets all the way in.

**Street** — OSM standard, no `{s}` subdomain, `maxZoom: 19`.

### 4.1 The `FlightVideoExporter` change

`FlightVideoExporter.tsx:90` currently pulls CARTO `@2x`. Esri has no retina variant. Two options:

- **Preferred:** fetch tiles at **z+1** and draw each at 0.5 scale — standard 2x trick, gives a
  true 512-effective-px render from 256px tiles. Works up to the layer's native ceiling, so use
  **World Imagery** (native z19 → effective 2x render to z18) for the video basemap rather than
  the dark canvas (native z16 → effective 2x only to z15). A satellite backdrop is arguably the
  better look for an exported flight video anyway.
- **Fallback:** render 1x and accept a softer MP4.

Keep `img.crossOrigin = 'anonymous'`; Esri sends `ACAO: *`, so the canvas stays untainted and
`toDataURL`/`captureStream` keep working. Verified.

### 4.2 The backend report renderer

`backend/app/services/map_renderer.py:267` keeps working as-is, but **must** set an
app-identifying User-Agent to comply with the OSM policy (the policy names library-default UAs as
disallowed). Format per the policy's own example:

```
DroneOpsCommand/2.91.0 (+https://droneops.barnardhq.com; contact: Bill@BarnardHQ.com)
```

`staticmap`'s `StaticMap` does not obviously expose a headers parameter — **this needs a 10-minute
check against the installed version** before it is written up as done. If it does not, the choices
are (a) monkeypatch the session, or (b) point `url_template` at Esri World Imagery instead, which
has no such UA requirement. Do not assume; verify.

---

## 5. "Never again" architecture

### 5.1 What actually failed, and what would have caught it

The CARTO failure was a **200 with degraded pixels**. Ranked by whether each candidate control
would have caught it:

| Control | Would it have caught CARTO? |
|---|---|
| Backend tile proxy with fallback chain on HTTP error | **No.** 200 OK. |
| Leaflet `tileerror` → next provider | **No.** The image loaded fine. |
| Uptime/healthcheck on the tile host | **No.** Host was up and fast. |
| Disk/Redis tile cache | **No — actively worse.** It would have cached the watermark for its TTL. |
| **Pixel-level fingerprint probe** | **Yes.** The only one. |
| Someone opening the app and looking | Yes — which is how we found it, 24 days later. |

This is why the recommendation is deliberately lopsided: build the cheap registry, skip the
expensive proxy, and build the one probe that addresses the actual failure mode.

### 5.2 Build today (~1 engineer-day)

**A. Shared basemap registry — `frontend/src/lib/basemaps.ts` (~2h).**
One module exporting the layer definitions (`id`, `label`, `url`, `attribution`, `maxZoom`,
`maxNativeZoom`, `overlays[]`, `crossOrigin`) plus a `<BasemapLayers/>` component wrapping
`LayersControl`. All five frontend call sites import it; none contains a hard-coded tile URL ever
again. **This is the highest-leverage two hours in the whole plan** — it converts "a provider died"
from a five-file hunt into a one-line edit, and it is what makes the probe in §5.4 possible
(the probe reads the same registry, so it can never drift from what the app actually requests).

**B. Swap the four maps + the video exporter to the §4 layer set (~2h).**

**C. Backend UA fix + drop the `{s}` OSM subdomain (~30min).**

**D. Client-side fallback chain (~1h).** Each registry entry gets an optional
`fallbacks: string[]`. A small `tileerror` counter swaps `layer.setUrl(next)` after N consecutive
errors in one session. ~20 lines. **Document in the code comment that this covers provider outage
and 404/5xx only, and explicitly does NOT cover silent pixel degradation** — so the next engineer
does not mistake it for the CARTO control.

### 5.3 Do NOT build (YAGNI)

- **Backend tile proxy `/api/tiles/{layer}/{z}/{x}/{y}` with disk/Redis cache.** Rejected on four
  grounds: (1) it would not have caught this bug; (2) a cache in front of OSM is a Tile Usage
  Policy violation (pre-emptive fetching / tile archiving); (3) it puts a new stateful hop in the
  path of every map view in the app, with its own disk-pressure, eviction and cache-poisoning
  failure modes — for a fleet that has already lost a day to disk pressure on HSH-HQ; (4) it
  routes all tile traffic through our egress for a benefit (hiding a key) we do not need, because
  the recommended providers are keyless. Revisit only if we go keyed (Stadia/CARTO paid).
- **MapLibre / OpenFreeMap migration today.** Multi-day library migration, not a basemap swap.
- **Any keyed free tier.** CARTO and Stadia free tiers both exclude commercial use. Taking one is
  choosing to redo this work under worse conditions later.

### 5.4 Build next (~2h, separate change — the actual "never again")

**Tile-health probe.** A scheduled job on BOS-HQ alongside the other fleet textfile collectors:

1. Read the same layer list the frontend uses (export the registry as JSON at build time, or
   duplicate a small YAML — the point is one source of truth).
2. For each layer, fetch **3 fixed tiles** (one low-zoom, one mid, one at the layer's measured
   `maxNativeZoom`) and record: HTTP status, byte size, image dimensions, and a **perceptual hash**
   (dHash/aHash, 64-bit, via Pillow — no new heavyweight dependency).
3. Compare against a checked-in fingerprint file captured at adoption time. Alert conditions:
   pHash Hamming distance > 8 from baseline, **or** byte size outside a ±40% band, **or** a
   non-200, **or** the tile matching a known blank-filler hash (`f27d9de7`, `875 B/2e3f659f`,
   `872 B/9cba0016` for Esri — captured in §3.2).
4. Emit a Prometheus textfile metric `droneops_basemap_tile_ok{layer="...",z="..."} 0|1` plus
   `droneops_basemap_probe_timestamp_seconds`.
5. On a transition to 0, publish **one** ntfy message at **`default` priority** per ADR-0037 —
   this is not customer-visible, is not imminent, and is not actionable in five minutes. It fails
   ADR-0037 questions 1 and 2 for anything higher. Topic `droneops-basemap`, title
   `[DroneOps Command] Basemap tiles changed — <layer>`, click URL the DroneOps map page (tier-1)
   falling back to `https://noc-mastercontrol.barnardhq.com/status/droneops`.
   Cooldown: **24h** (this is a slow-moving condition, not an incident).
   **Note per the operator-subscription constraint: a new ntfy topic is a black hole until Bill
   subscribes on his phone (~30s). Either reuse an existing subscribed topic or hand him the topic
   name explicitly as part of shipping this.**
6. A staleness deadman on `droneops_basemap_probe_timestamp_seconds` (> 26h, `warn`) so a dead
   probe is not mistaken for a green probe. **Name the collector to match the existing textfile
   deadman cadence conventions, or it will be caught by the generic 1h textfile deadman.**

Weekly cadence is sufficient. CARTO's change sat undetected for 24 days; a 7-day worst case is a
24x improvement for two hours of work.

### 5.5 Roadmap (not now)

- **ADR + Protomaps PMTiles on R2** with `protomaps-leaflet`. The only option where the answer to
  "what if the provider changes the terms" is "we are the provider." Sized ~1-2 days.
- **Stadia Starter at $20/month** if Bill wants CARTO-grade dark aesthetics immediately and would
  rather pay than wait for the Protomaps build. This is a one-line registry change once §5.2A
  lands — which is the point of the registry.

---

## 6. Verbatim tile URL templates and attribution strings

### 6.1 URL templates (Leaflet `TileLayer` `url` prop)

```
# Dark base — Esri World Dark Gray Canvas. maxNativeZoom 16, maxZoom 19. No {s}. No retina.
https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}

# Dark/Hybrid street + road-label overlay — real data to z19. maxNativeZoom 19.
https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Transportation/MapServer/tile/{z}/{y}/{x}

# Satellite — Esri World Imagery. maxNativeZoom 19, maxZoom 19.
https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}

# Hybrid place-name labels — maxNativeZoom 16.
https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}

# Street — OSM standard. NOTE the {z}/{x}/{y} order, and NO {s} subdomain. maxZoom 19.
https://tile.openstreetmap.org/{z}/{x}/{y}.png
```

**The Esri axis order is `{z}/{y}/{x}`, not `{z}/{x}/{y}`.** The existing code already gets this
right for World Imagery; do not "fix" it.

### 6.2 Attribution strings — verbatim from each service's `copyrightText`

Pulled from `MapServer?f=pjson` on 2026-09-21.

```
World Dark Gray Base        : Esri, HERE, Garmin, (c) OpenStreetMap contributors, and the GIS user community
World Dark Gray Reference   : Esri, HERE, Garmin, (c) OpenStreetMap contributors, and the GIS user community
World Imagery               : Source: Esri, Vantor, Earthstar Geographics, and the GIS User Community
World Boundaries_and_Places : Esri, HERE, Garmin, (c) OpenStreetMap contributors, and the GIS user community
World Transportation        : Esri, HERE, Garmin, (c) OpenStreetMap contributors
```

As HTML for the Leaflet `attribution` prop:

```html
<!-- Dark (base + transportation overlay) -->
Tiles &copy; <a href="https://www.esri.com/">Esri</a> &mdash; Esri, HERE, Garmin, &copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors, and the GIS user community

<!-- Satellite -->
Tiles &copy; <a href="https://www.esri.com/">Esri</a> &mdash; Source: Esri, Vantor, Earthstar Geographics, and the GIS User Community

<!-- Hybrid (imagery + boundaries + transportation) -->
Tiles &copy; <a href="https://www.esri.com/">Esri</a> &mdash; Source: Esri, Vantor, Earthstar Geographics, HERE, Garmin, &copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors, and the GIS User Community

<!-- Street -->
&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors
```

The current code passes bare `attribution="Esri"` and `attribution="OSM"` at four call sites and
**no attribution at all** on the CARTO dark layer. Both Esri and OSMF require real attribution;
the registry should make it impossible to add a layer without one.

---

## 7. Risks I could not verify

1. **Esri's terms for keyless third-party use — UNRESOLVED and material.** I could not retrieve
   the substantive text of Esri's Master Agreement (the terms page serves links to PDFs, not
   content). Community and Esri blog sources say Leaflet/OpenLayers apps were supposed to migrate
   off `server.arcgisonline.com` to a keyed service by 2022-04-30. The endpoints still serve
   keylessly four years later. **We are recommending a gray-zone dependency.** The mitigations are
   the registry (swap cost ≈ zero), the probe (we find out in ≤7 days, not 24), and the documented
   Stadia-at-$20/month and Protomaps escape hatches. If Bill wants zero terms risk, the answer is
   Protomaps on R2 and it is a 1-2 day project, not a one-day one.
2. **CARTO raster end-of-life date — not published.** "Being retired," "considering stopping data
   updates," no date. Moot under this recommendation, but it means any CARTO path has an unknown
   clock on it.
3. **Esri zoom ceilings are location-specific.** All ladders in §3.2 were measured over Eugene, OR.
   World Imagery reaches z19 there; it reaches z20-21 over some metros and less over rural areas.
   If DroneOps flies outside the Willamette Valley, re-measure before hard-coding `maxNativeZoom`,
   or set it from a per-region probe.
4. **`staticmap` User-Agent control — unverified.** I did not confirm the installed `staticmap`
   version exposes a way to set request headers. Check before implementing §4.2.
5. **Stadia and MapTiler terms** were read from search-result summaries of their docs; the Stadia
   docs page returned **403** to direct fetch. The 401-requires-a-key finding is first-hand
   (curl); the free-tier commercial-use exclusion is second-hand and should be re-read on their
   pricing page before anyone spends money.
6. **`protomaps-leaflet` at 15 months since last publish.** Stable and BSD-3, but not moving.
   `pmtiles` itself is active (Aug 2026). If the Protomaps roadmap item is taken up, re-assess
   whether `protomaps-leaflet` or a MapLibre migration is the better host by then.
7. **Perceptual-hash thresholds are unvalidated.** The Hamming-distance-8 and ±40%-byte-band
   figures in §5.4 are starting points, not measured. Basemaps legitimately change when the
   provider refreshes data. Run the probe in observe-only mode for two weeks and tune from the
   observed variance before wiring the ntfy publish — otherwise the first data refresh pages as a
   false positive and the probe gets muted, which is worse than not having it.

---

## 8. Sources

- CARTO Basemaps FAQ — <https://docs.carto.com/faqs/carto-basemaps> (fetched 2026-09-21)
- CARTO API key request page — <https://carto.com/basemaps/apikey/> (fetched 2026-09-21)
- CARTO basemap styles repo — <https://github.com/cartodb/basemap-styles>
- OSMF Tile Usage Policy — <https://operations.osmfoundation.org/policies/tiles/> (fetched 2026-09-21)
- Esri Leaflet terms of use — <https://developers.arcgis.com/esri-leaflet/terms-of-use/>
- Esri blog, "Open source developers: Time to upgrade to the new ArcGIS basemap layer service!" — <https://www.esri.com/arcgis-blog/products/developers/developers/open-source-developers-time-to-upgrade-to-the-new-arcgis-basemap-layer-service>
- Esri Community, "Terms of Use for services.arcgisonline.com" — <https://community.esri.com/t5/arcgis-online-questions/terms-of-use-for-http-services-arcgisonline-com/td-p/601874>
- Esri MapServer metadata (`?f=pjson`) for World_Dark_Gray_Base, World_Dark_Gray_Reference, World_Imagery, World_Boundaries_and_Places, World_Transportation — queried directly 2026-09-21
- Stadia Maps service limits — <https://docs.stadiamaps.com/limits/>
- Stadia Maps pricing — <https://stadiamaps.com/pricing/>
- OpenFreeMap dark style JSON — <https://tiles.openfreemap.org/styles/dark> (fetched 2026-09-21)
- npm registry: `protomaps-leaflet@5.1.0` (2025-06-18), `pmtiles@4.5.0` (2026-08-10)
- Independent corroboration of the CARTO watermark across unrelated projects:
  <https://github.com/home-assistant/core/issues/180277>,
  <https://github.com/timmaurice/lovelace-blitzortung-lightning-card/issues/90>,
  <https://github.com/hhftechnology/traefik-log-dashboard/issues/229>
- Fleet policy referenced: ADR-0036 (ntfy notification standard), ADR-0037 (noise-reduction rubric)

---

## 9. Suggested follow-on documentation

Per fleet documentation discipline, the implementation change should carry:
- `docs/adr/NNNN-basemap-provider-migration.md` — the decision, the CARTO trigger, the Esri
  gray-zone risk accepted with eyes open, and the Protomaps exit.
- `CHANGELOG.md` — 2026-09-21 entry under Changed/Fixed.
- `ROADMAP.md` — Protomaps-on-R2 basemap self-hosting, and the tile-health probe.
