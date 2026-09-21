/**
 * Basemap registry — the single source of tile URLs for every map in the app.
 *
 * ADR-0046. Before this module, five call sites each hard-coded their own tile
 * URLs. On ~2026-08-28 CARTO began serving its keyless raster basemaps with an
 * "API KEY REQUIRED" watermark burned into the pixels — HTTP 200, correct
 * content-type, correct CORS headers, degraded image — and it went unnoticed
 * for 24 days because there was nothing to notice it with. Every provider swap
 * now happens here, once.
 *
 * Providers are keyless by deliberate choice (see ADR-0046): CARTO's and
 * Stadia's free tiers both exclude commercial use, and a key would have to live
 * somewhere. Esri's `server.arcgisonline.com` raster endpoints are keyless with
 * `Access-Control-Allow-Origin: *`; OpenStreetMap's standard tiles are keyless
 * under the OSMF Tile Usage Policy (human-driven viewing only — never
 * pre-fetched, never cached server-side, never the default layer).
 *
 * Three things about these URLs that are easy to get wrong:
 *
 * 1. **Esri's axis order is `{z}/{y}/{x}`**, not `{z}/{x}/{y}`. OSM's is
 *    `{z}/{x}/{y}`. Both forms appear below; neither is a typo.
 * 2. **No `{s}` subdomain.** Esri never had one; OSMF now specifies the bare
 *    `tile.openstreetmap.org` host and warns that other subdomains "may be
 *    slower or withdrawn without notice".
 * 3. **No `@2x` / `{r}` retina variant exists on any of them.** Requesting one
 *    is a 404. See the `detectRetina` note below.
 *
 * `maxNativeZoom` values are MEASURED, not advertised. Esri's service metadata
 * claims LOD 23 for every layer; in reality each service returns a single
 * byte-identical blank filler tile at every zoom past its real data. The values
 * here were measured over Eugene, OR on 2026-09-21 (ADR-0046 / the provider
 * evaluation report, §3.2). `maxZoom` is set higher so Leaflet upsamples the
 * deepest real tile instead of requesting filler.
 */

import type { ControlPosition } from 'leaflet';

// ── Types ──────────────────────────────────────────────────────────

/** One raster tile layer. Sets are an ordered stack of these. */
export interface TileSpec {
  /** Leaflet URL template. Mind the axis order — Esri is `{z}/{y}/{x}`. */
  url: string;
  /** Rendered verbatim in the attribution control. Required, always. */
  attribution: string;
  /** Deepest zoom with REAL data. Past this Leaflet upsamples. */
  maxNativeZoom: number;
  /** Deepest zoom the map may reach with this layer shown. */
  maxZoom: number;
  /** Only for providers that still shard by subdomain. None here do. */
  subdomains?: string;
  /** Overlay opacity, 0-1. Omitted = 1. */
  opacity?: number;
}

export type BasemapSetId = 'dark' | 'satellite' | 'hybrid' | 'street';

export interface BasemapSet {
  id: BasemapSetId;
  /** Label shown in the LayersControl. */
  label: string;
  /** Drawn bottom-to-top: index 0 is the base, the rest are overlays. */
  layers: TileSpec[];
  /**
   * Next set to try when this one's tiles stop loading. `null` ends the chain.
   *
   * This covers provider OUTAGE — 404/5xx/DNS/connection failures. It does NOT
   * and CANNOT cover silent pixel degradation, which is what CARTO actually
   * did: those tiles returned HTTP 200 and loaded perfectly. The control for
   * that is the backend tile-health probe (ADR-0046 §5), which looks at the
   * pixels. Do not mistake this fallback for that.
   */
  fallback: BasemapSetId | null;
}

// ── Attribution strings ────────────────────────────────────────────
// Verbatim from each MapServer's own `copyrightText` (`?f=pjson`), pulled
// 2026-09-21. Esri's terms and the OSMF Tile Usage Policy both require
// attribution; shortening these to "Esri" (as four call sites used to do)
// does not satisfy either.

const ESRI_LINK = '<a href="https://www.esri.com/">Esri</a>';
const OSM_LINK = '<a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>';

const ATTR_ESRI_CANVAS =
  `Tiles &copy; ${ESRI_LINK} &mdash; Esri, HERE, Garmin, &copy; ${OSM_LINK} contributors, and the GIS user community`;

const ATTR_ESRI_TRANSPORTATION =
  `Tiles &copy; ${ESRI_LINK} &mdash; Esri, HERE, Garmin, &copy; ${OSM_LINK} contributors`;

const ATTR_ESRI_IMAGERY =
  `Tiles &copy; ${ESRI_LINK} &mdash; Source: Esri, Vantor, Earthstar Geographics, and the GIS User Community`;

const ATTR_OSM = `&copy; ${OSM_LINK} contributors`;

// ── Tile specs ─────────────────────────────────────────────────────

const ESRI = 'https://server.arcgisonline.com/ArcGIS/rest/services';

/** Dark canvas. Real data to z16 — soft above that, which is why the
 *  transportation overlay below carries the detail at drone zoom. */
export const ESRI_DARK_BASE: TileSpec = {
  url: `${ESRI}/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}`,
  attribution: ATTR_ESRI_CANVAS,
  maxNativeZoom: 16,
  maxZoom: 20,
};

/** Roads + street names + highway shields on transparent PNG. Real data to
 *  z19, so it stays razor-sharp exactly where the dark canvas goes soft.
 *  This layer is what makes the z16 base ceiling acceptable. */
export const ESRI_TRANSPORTATION: TileSpec = {
  url: `${ESRI}/Reference/World_Transportation/MapServer/tile/{z}/{y}/{x}`,
  attribution: ATTR_ESRI_TRANSPORTATION,
  maxNativeZoom: 19,
  maxZoom: 20,
};

export const ESRI_IMAGERY: TileSpec = {
  url: `${ESRI}/World_Imagery/MapServer/tile/{z}/{y}/{x}`,
  attribution: ATTR_ESRI_IMAGERY,
  maxNativeZoom: 19,
  maxZoom: 20,
};

/** Place names (cities, parks, campuses) for the hybrid view. */
export const ESRI_BOUNDARIES_PLACES: TileSpec = {
  url: `${ESRI}/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}`,
  attribution: ATTR_ESRI_CANVAS,
  maxNativeZoom: 16,
  maxZoom: 20,
};

/**
 * OSM standard tiles. USER-SELECTABLE LAYER ONLY — never the default and
 * never machine-driven. The OSMF Tile Usage Policy forbids bulk or
 * pre-emptive fetching and can withdraw access without notice.
 */
export const OSM_STANDARD: TileSpec = {
  url: 'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
  attribution: ATTR_OSM,
  maxNativeZoom: 19,
  maxZoom: 20,
};

// ── Layer sets ─────────────────────────────────────────────────────

export const BASEMAP_SETS: Record<BasemapSetId, BasemapSet> = {
  dark: {
    id: 'dark',
    label: 'Dark',
    // World_Dark_Gray_Reference was evaluated as a third layer for city
    // labels and REJECTED: at z12-z13 over Eugene its place names are
    // overprinted by the transportation layer's road casings and shields
    // and become unreadable, and it is blank from z16 up. Transportation
    // alone carries every label that survives the composite.
    layers: [ESRI_DARK_BASE, ESRI_TRANSPORTATION],
    fallback: 'street',
  },
  satellite: {
    id: 'satellite',
    label: 'Satellite',
    layers: [ESRI_IMAGERY],
    fallback: 'street',
  },
  hybrid: {
    id: 'hybrid',
    label: 'Hybrid',
    layers: [
      ESRI_IMAGERY,
      ESRI_BOUNDARIES_PLACES,
      // Held back from full opacity so the imagery underneath stays
      // readable — on a satellite view the ground IS the information.
      { ...ESRI_TRANSPORTATION, opacity: 0.75 },
    ],
    fallback: 'satellite',
  },
  street: {
    id: 'street',
    label: 'Street',
    layers: [OSM_STANDARD],
    fallback: null,
  },
};

/** Order the sets appear in the LayersControl. */
export const BASEMAP_ORDER: BasemapSetId[] = ['dark', 'satellite', 'hybrid', 'street'];

export const DEFAULT_BASEMAP_SET: BasemapSetId = 'dark';

/**
 * `detectRetina` is deliberately OFF on every layer.
 *
 * None of these providers serves an `@2x` variant, so Leaflet's detectRetina
 * would fall back to its zoom+1/half-tile trick — and Leaflet applies that
 * `zoomOffset` AFTER clamping to `maxNativeZoom`, so at the ceiling it would
 * request one zoom PAST the deepest real tile and get the blank filler. On a
 * retina device the dark base would go blank at z16 instead of merely soft.
 * Upsampling a real tile beats a sharp empty one.
 */
export const DETECT_RETINA = false;

/** Expand a URL template. Handles both `{z}/{y}/{x}` and `{z}/{x}/{y}`. */
export function tileUrl(spec: TileSpec, z: number, x: number, y: number): string {
  let url = spec.url
    .replace('{z}', String(z))
    .replace('{x}', String(x))
    .replace('{y}', String(y));
  if (spec.subdomains && spec.subdomains.length > 0) {
    const subs = spec.subdomains;
    url = url.replace('{s}', subs[Math.abs(x + y) % subs.length]);
  }
  return url;
}

// ── Failover state machine ─────────────────────────────────────────

/**
 * Consecutive real tile-load failures inside `TILE_ERROR_WINDOW_MS` before a
 * set is abandoned for its fallback.
 *
 * Six is above the noise floor of a single flaky tile or a CDN hiccup (Leaflet
 * retries nothing, so one dropped tile is one error) and below the ~12-24
 * tiles a single viewport asks for, so a genuinely dead provider trips it on
 * the first screenful.
 */
export const TILE_ERROR_THRESHOLD = 6;

/** Sliding window. Long enough to span one viewport's worth of requests,
 *  short enough that errors minutes apart never accumulate into a swap. */
export const TILE_ERROR_WINDOW_MS = 10_000;

/**
 * Was this a real load failure, or Leaflet aborting an in-flight tile because
 * the user panned or zoomed away?
 *
 * Leaflet's `_abortLoading` rewrites the element's `src` to its inline
 * `data:image/gif;base64,...` placeholder before discarding the tile, so an
 * aborted tile no longer carries an http(s) URL. Counting those would make the
 * failover fire on nothing worse than a fast pan.
 */
export function isRealTileError(tile: { getAttribute(name: string): string | null } | undefined): boolean {
  const src = tile?.getAttribute('src') ?? '';
  return src.startsWith('http://') || src.startsWith('https://');
}

/**
 * Per-set tile failure tracker. Pure — no Leaflet, no DOM, no timers — so the
 * swap logic is unit-testable without a map.
 *
 * Swaps are one-way and terminal: once a set has walked its whole chain it
 * stops trying, so a provider that is down for both sets cannot make the map
 * flap between them for the rest of the session.
 */
export class TileFailoverState {
  private readonly origin: BasemapSetId;
  private active: BasemapSetId;
  private failures: number[] = [];

  constructor(
    origin: BasemapSetId,
    private readonly threshold: number = TILE_ERROR_THRESHOLD,
    private readonly windowMs: number = TILE_ERROR_WINDOW_MS,
  ) {
    this.origin = origin;
    this.active = origin;
  }

  /** The set whose layers should currently be rendered for `origin`. */
  get activeSetId(): BasemapSetId {
    return this.active;
  }

  /** True once the chain is exhausted — further errors are ignored. */
  get exhausted(): boolean {
    return BASEMAP_SETS[this.active].fallback === null;
  }

  /**
   * Record one real tile failure.
   *
   * Returns the new active set id if this error triggered a swap, else `null`.
   * `now` is injected so tests do not depend on the wall clock.
   */
  recordError(now: number): BasemapSetId | null {
    if (this.exhausted) return null;

    this.failures = this.failures.filter((t) => now - t < this.windowMs);
    this.failures.push(now);
    if (this.failures.length < this.threshold) return null;

    const next = BASEMAP_SETS[this.active].fallback;
    if (next === null) return null;

    this.active = next;
    this.failures = [];
    return next;
  }

  /** Back to the originally requested set (e.g. the operator re-picks it). */
  reset(): void {
    this.active = this.origin;
    this.failures = [];
  }
}

/** Layers to render for a set, after any failover swap. */
export function layersFor(setId: BasemapSetId): TileSpec[] {
  return BASEMAP_SETS[setId].layers;
}

/** Default LayersControl corner. Every map used `topright`. */
export const BASEMAP_CONTROL_POSITION: ControlPosition = 'topright';
