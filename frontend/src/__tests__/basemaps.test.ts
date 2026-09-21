/**
 * Basemap registry + tile failover contract (ADR-0046).
 *
 * The registry assertions are not decoration: every one of them encodes a
 * mistake that was live in this repo before ADR-0046 — a watermarked CARTO
 * URL, a `{s}` subdomain the OSM Foundation no longer specifies, an `@2x`
 * variant Esri does not serve, a bare "Esri" attribution neither provider
 * accepts, and a `maxZoom` with no `maxNativeZoom` behind it.
 */

import { describe, expect, it } from 'vitest';

import {
  BASEMAP_ORDER,
  BASEMAP_SETS,
  DEFAULT_BASEMAP_SET,
  DETECT_RETINA,
  ESRI_DARK_BASE,
  OSM_STANDARD,
  TILE_ERROR_THRESHOLD,
  TILE_ERROR_WINDOW_MS,
  TileFailoverState,
  isRealTileError,
  tileUrl,
  type BasemapSetId,
} from '../lib/basemaps';

const ALL_SPECS = BASEMAP_ORDER.flatMap((id) => BASEMAP_SETS[id].layers);

function fakeTile(src: string | null) {
  return { getAttribute: () => src };
}

describe('basemap registry', () => {
  it('offers exactly the four sets, Dark first and checked by default', () => {
    expect(BASEMAP_ORDER).toEqual(['dark', 'satellite', 'hybrid', 'street']);
    expect(DEFAULT_BASEMAP_SET).toBe('dark');
  });

  it('never references a watermarked or key-gated provider', () => {
    for (const spec of ALL_SPECS) {
      expect(spec.url).not.toContain('cartocdn');
      expect(spec.url).not.toContain('fastly.net');
      expect(spec.url).not.toContain('key=');
      expect(spec.url).not.toContain('api_key');
    }
  });

  it('requests no tile variant the providers do not serve', () => {
    for (const spec of ALL_SPECS) {
      // Esri and OSM both serve 256px tiles only; `{r}`/@2x is a 404.
      expect(spec.url).not.toContain('{r}');
      expect(spec.url).not.toContain('@2x');
      // A `{s}` placeholder without a subdomain list expands to "undefined".
      if (spec.url.includes('{s}')) {
        expect(spec.subdomains, spec.url).toBeTruthy();
      }
    }
  });

  it('carries a real attribution on every layer', () => {
    for (const spec of ALL_SPECS) {
      // Bare "Esri" / "OSM" satisfies neither provider's terms.
      expect(spec.attribution.length).toBeGreaterThan(20);
      expect(spec.attribution).toMatch(/Esri|OpenStreetMap/);
    }
  });

  it('bounds every layer by a measured native zoom below its display zoom', () => {
    for (const spec of ALL_SPECS) {
      expect(spec.maxNativeZoom).toBeGreaterThan(0);
      expect(spec.maxZoom).toBeGreaterThanOrEqual(spec.maxNativeZoom);
    }
    // The measured ceilings that drive the whole Dark design.
    expect(ESRI_DARK_BASE.maxNativeZoom).toBe(16);
    expect(BASEMAP_SETS.dark.layers[1].maxNativeZoom).toBe(19);
  });

  it('keeps detectRetina off so no layer requests past its native ceiling', () => {
    // Leaflet applies detectRetina's zoomOffset AFTER clamping to
    // maxNativeZoom, so enabling it would request the blank filler tile.
    expect(DETECT_RETINA).toBe(false);
  });

  it('keeps OSM off the default path', () => {
    // The OSMF Tile Usage Policy tolerates a hand-picked layer, not a default.
    expect(BASEMAP_SETS[DEFAULT_BASEMAP_SET].layers).not.toContain(OSM_STANDARD);
    expect(BASEMAP_SETS.street.layers).toEqual([OSM_STANDARD]);
  });

  it('terminates every fallback chain', () => {
    for (const id of BASEMAP_ORDER) {
      const seen = new Set<BasemapSetId>();
      let cursor: BasemapSetId | null = id;
      while (cursor !== null) {
        expect(seen.has(cursor)).toBe(false); // no cycles
        seen.add(cursor);
        cursor = BASEMAP_SETS[cursor].fallback;
      }
    }
  });
});

describe('tileUrl', () => {
  it('expands Esri templates in {z}/{y}/{x} order', () => {
    expect(tileUrl(ESRI_DARK_BASE, 10, 163, 373)).toBe(
      'https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/10/373/163',
    );
  });

  it('expands OSM templates in {z}/{x}/{y} order', () => {
    expect(tileUrl(OSM_STANDARD, 10, 163, 373)).toBe('https://tile.openstreetmap.org/10/163/373.png');
  });

  it('expands {s} only when the spec declares subdomains', () => {
    const sharded = { ...OSM_STANDARD, url: 'https://{s}.example.test/{z}/{x}/{y}.png', subdomains: 'abc' };
    expect(tileUrl(sharded, 3, 1, 1)).toBe('https://c.example.test/3/1/1.png');
  });
});

describe('isRealTileError', () => {
  it('counts a tile that still holds its http(s) URL', () => {
    expect(isRealTileError(fakeTile('https://server.arcgisonline.com/x/1/2/3'))).toBe(true);
    expect(isRealTileError(fakeTile('http://tile.example.test/1/2/3.png'))).toBe(true);
  });

  it('ignores a tile Leaflet aborted mid-flight', () => {
    // `_abortLoading` swaps in Util.emptyImageUrl before discarding the tile.
    expect(isRealTileError(fakeTile('data:image/gif;base64,R0lGODlhAQABAAD/ACwAAAAAAQABAAACADs='))).toBe(false);
    expect(isRealTileError(fakeTile(''))).toBe(false);
    expect(isRealTileError(fakeTile(null))).toBe(false);
    expect(isRealTileError(undefined)).toBe(false);
  });
});

describe('TileFailoverState', () => {
  it('stays on the chosen set below the threshold', () => {
    const s = new TileFailoverState('dark');
    for (let i = 0; i < TILE_ERROR_THRESHOLD - 1; i++) {
      expect(s.recordError(1_000 + i)).toBeNull();
    }
    expect(s.activeSetId).toBe('dark');
  });

  it('swaps to the registry fallback on the Nth error inside the window', () => {
    const s = new TileFailoverState('dark');
    let swapped: string | null = null;
    for (let i = 0; i < TILE_ERROR_THRESHOLD; i++) {
      swapped = s.recordError(1_000 + i * 10);
    }
    expect(swapped).toBe('street');
    expect(s.activeSetId).toBe('street');
  });

  it('does not swap on errors spread beyond the window', () => {
    const s = new TileFailoverState('dark');
    // One error per window-length. Never two inside one window.
    for (let i = 0; i < TILE_ERROR_THRESHOLD * 3; i++) {
      expect(s.recordError(i * (TILE_ERROR_WINDOW_MS + 1))).toBeNull();
    }
    expect(s.activeSetId).toBe('dark');
  });

  it('evicts errors that aged out of the window', () => {
    const s = new TileFailoverState('dark', 3, 1_000);
    expect(s.recordError(0)).toBeNull();
    expect(s.recordError(100)).toBeNull();
    // The first two are now older than the window — this is a fresh streak.
    expect(s.recordError(2_000)).toBeNull();
    expect(s.recordError(2_100)).toBeNull();
    expect(s.recordError(2_200)).toBe('street');
  });

  it('walks a multi-step chain one swap at a time', () => {
    const s = new TileFailoverState('hybrid', 2, 1_000);
    expect(s.recordError(0)).toBeNull();
    expect(s.recordError(10)).toBe('satellite'); // hybrid → satellite
    expect(s.recordError(20)).toBeNull(); // counter reset by the swap
    expect(s.recordError(30)).toBe('street'); // satellite → street
    expect(s.activeSetId).toBe('street');
  });

  it('stops at the end of the chain instead of flapping', () => {
    const s = new TileFailoverState('street', 2, 1_000);
    expect(s.exhausted).toBe(true);
    for (let i = 0; i < 20; i++) {
      expect(s.recordError(i)).toBeNull();
    }
    expect(s.activeSetId).toBe('street');
  });

  it('returns to the originally chosen set on reset', () => {
    const s = new TileFailoverState('dark', 2, 1_000);
    s.recordError(0);
    s.recordError(10);
    expect(s.activeSetId).toBe('street');
    s.reset();
    expect(s.activeSetId).toBe('dark');
    // And the error budget is fresh, not one error from swapping again.
    expect(s.recordError(20)).toBeNull();
  });
});
