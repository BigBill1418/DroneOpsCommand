/**
 * Vitest setup — v2.67.0 Mission Hub redesign.
 *
 * Adds @testing-library/jest-dom matchers and shims browser APIs that
 * jsdom doesn't ship by default but Mantine 7 + Tabler icons reach for.
 */
import '@testing-library/jest-dom/vitest';
import { afterEach, vi } from 'vitest';
import { cleanup } from '@testing-library/react';

afterEach(() => {
  cleanup();
});

// Mantine reads window.matchMedia in several places; jsdom omits it.
if (typeof window !== 'undefined' && !window.matchMedia) {
  window.matchMedia = (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  });
}

// Mantine's Modal uses ResizeObserver for portal sizing.
if (typeof window !== 'undefined' && !(window as unknown as { ResizeObserver?: unknown }).ResizeObserver) {
  class _RO {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  (window as unknown as { ResizeObserver: typeof _RO }).ResizeObserver = _RO;
}

// scrollIntoView is called by some Mantine focus handling.
if (typeof Element !== 'undefined' && !Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = vi.fn();
}
