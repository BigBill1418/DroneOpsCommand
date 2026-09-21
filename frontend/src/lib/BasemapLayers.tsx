/**
 * <BasemapLayers/> — the one map-layer component every map in the app mounts.
 *
 * Renders the ADR-0046 registry as a Leaflet LayersControl (Dark / Satellite /
 * Hybrid / Street, Dark checked) plus the attribution control that Esri's and
 * the OSM Foundation's terms both require. Drop it inside a <MapContainer> and
 * the map has basemaps; there is nothing else to configure and no tile URL to
 * copy.
 *
 * The host <MapContainer> must pass `attributionControl={false}` — Leaflet
 * would otherwise add a second attribution control carrying the "Leaflet"
 * prefix alongside this one.
 *
 * Failover: each layer reports tile load failures into a per-set
 * `TileFailoverState`. Six real failures inside ten seconds and the set is
 * swapped for its registry fallback (Dark → Street), logged once, with no
 * modal and no interruption. That handles a provider going DOWN. It does not
 * handle a provider going BAD while still returning HTTP 200 — see the
 * tile-health probe in the backend (ADR-0046 §5), which is the control for the
 * failure that actually happened.
 */

import { useMemo, useRef, useState } from 'react';
import { AttributionControl, LayerGroup, LayersControl, TileLayer } from 'react-leaflet';
import type { ControlPosition, TileErrorEvent } from 'leaflet';

import {
  BASEMAP_CONTROL_POSITION,
  BASEMAP_ORDER,
  BASEMAP_SETS,
  DEFAULT_BASEMAP_SET,
  DETECT_RETINA,
  TileFailoverState,
  isRealTileError,
  type BasemapSetId,
} from './basemaps';

import './basemaps.css';

export interface BasemapLayersProps {
  /** LayersControl corner. Defaults to `topright`, as every map used. */
  position?: ControlPosition;
  /** Which set starts checked. Defaults to Dark. */
  defaultSet?: BasemapSetId;
}

export function BasemapLayers({
  position = BASEMAP_CONTROL_POSITION,
  defaultSet = DEFAULT_BASEMAP_SET,
}: BasemapLayersProps) {
  // Maps a registry set to whatever it has failed over to. Empty in the
  // overwhelming majority of sessions.
  const [failedOver, setFailedOver] = useState<Partial<Record<BasemapSetId, BasemapSetId>>>({});
  const trackers = useRef(new Map<BasemapSetId, TileFailoverState>());

  // One stable handler per set — rebuilding these every render would make
  // react-leaflet detach and re-attach the Leaflet listeners on each pass.
  const errorHandlers = useMemo(() => {
    const bySet = new Map<BasemapSetId, (event: TileErrorEvent) => void>();
    for (const setId of BASEMAP_ORDER) {
      bySet.set(setId, (event: TileErrorEvent) => {
        if (!isRealTileError(event.tile)) return;

        let tracker = trackers.current.get(setId);
        if (!tracker) {
          tracker = new TileFailoverState(setId);
          trackers.current.set(setId, tracker);
        }

        const swappedTo = tracker.recordError(Date.now());
        if (!swappedTo) return;

        // Once per swap, not once per failed tile.
        console.warn(
          `[basemaps] "${BASEMAP_SETS[setId].label}" tiles are failing to load; ` +
            `falling back to "${BASEMAP_SETS[swappedTo].label}". ` +
            'This detects provider outage only — a provider serving degraded ' +
            'tiles with HTTP 200 is covered by the backend tile-health probe (ADR-0046).',
        );
        setFailedOver((prev) => ({ ...prev, [setId]: swappedTo }));
      });
    }
    return bySet;
  }, []);

  return (
    <>
      <AttributionControl position="bottomright" prefix={false} />
      <LayersControl position={position}>
        {BASEMAP_ORDER.map((setId) => {
          const renderedSet = BASEMAP_SETS[failedOver[setId] ?? setId];
          return (
            <LayersControl.BaseLayer
              key={setId}
              name={BASEMAP_SETS[setId].label}
              checked={setId === defaultSet}
            >
              <LayerGroup>
                {renderedSet.layers.map((spec) => (
                  <TileLayer
                    key={spec.url}
                    url={spec.url}
                    attribution={spec.attribution}
                    maxNativeZoom={spec.maxNativeZoom}
                    maxZoom={spec.maxZoom}
                    opacity={spec.opacity ?? 1}
                    detectRetina={DETECT_RETINA}
                    {...(spec.subdomains ? { subdomains: spec.subdomains } : {})}
                    eventHandlers={{ tileerror: errorHandlers.get(setId) }}
                  />
                ))}
              </LayerGroup>
            </LayersControl.BaseLayer>
          );
        })}
      </LayersControl>
    </>
  );
}

export default BasemapLayers;
