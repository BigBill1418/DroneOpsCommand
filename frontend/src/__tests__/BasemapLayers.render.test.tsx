/**
 * <BasemapLayers/> mounted in a real Leaflet map (ADR-0046).
 *
 * The registry tests in `basemaps.test.ts` check the data. This file checks the
 * wiring — that `LayersControl.BaseLayer` accepts a `LayerGroup` of several
 * tile layers, that a multi-layer set actually requests every one of its
 * layers, and that exactly one attribution control exists. Those are the
 * failure modes a type-check cannot see: they would ship a map that renders
 * one layer, or two stacked attribution controls, or none.
 */

import { describe, expect, it } from 'vitest';
import { render } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MapContainer } from 'react-leaflet';
import 'leaflet/dist/leaflet.css';

import { BasemapLayers } from '../lib/BasemapLayers';

function mountMap() {
  return render(
    <MapContainer
      center={[44.05, -123.09]}
      zoom={13}
      attributionControl={false}
      style={{ height: 400, width: 600 }}
    >
      <BasemapLayers />
    </MapContainer>,
  );
}

const tileSources = (container: HTMLElement) =>
  Array.from(container.querySelectorAll('img.leaflet-tile')).map((img) => (img as HTMLImageElement).src);

describe('BasemapLayers inside a Leaflet map', () => {
  it('offers the four registry sets with Dark checked', () => {
    const { container } = mountMap();

    const labels = Array.from(container.querySelectorAll('.leaflet-control-layers-base label'));
    expect(labels.map((el) => el.textContent?.trim())).toEqual(['Dark', 'Satellite', 'Hybrid', 'Street']);

    const checked = container.querySelector('.leaflet-control-layers-base input:checked');
    expect(checked?.closest('label')?.textContent?.trim()).toBe('Dark');
  });

  it('renders exactly one attribution control, attributed and prefix-free', () => {
    const { container } = mountMap();

    const controls = container.querySelectorAll('.leaflet-control-attribution');
    expect(controls.length).toBe(1);
    expect(controls[0].textContent).toContain('Esri');
    // `prefix={false}` — Leaflet's default control would inject "Leaflet |".
    expect(controls[0].textContent).not.toContain('Leaflet |');
  });

  it('requests BOTH layers of the Dark set, not just the base', () => {
    const { container } = mountMap();
    const srcs = tileSources(container);

    expect(srcs.some((s) => s.includes('World_Dark_Gray_Base'))).toBe(true);
    expect(srcs.some((s) => s.includes('World_Transportation'))).toBe(true);
    expect(srcs.some((s) => s.includes('cartocdn'))).toBe(false);
  });

  it('swaps in all three Hybrid layers when the operator picks it', async () => {
    const { container } = mountMap();

    const hybrid = Array.from(container.querySelectorAll('.leaflet-control-layers-base label'))
      .find((el) => el.textContent?.trim() === 'Hybrid')
      ?.querySelector('input');
    expect(hybrid).toBeTruthy();
    await userEvent.click(hybrid!);

    const srcs = tileSources(container);
    expect(srcs.some((s) => s.includes('World_Imagery'))).toBe(true);
    expect(srcs.some((s) => s.includes('World_Boundaries_and_Places'))).toBe(true);
    expect(srcs.some((s) => s.includes('World_Transportation'))).toBe(true);
    expect(srcs.some((s) => s.includes('World_Dark_Gray_Base'))).toBe(false);
  });
});
