/**
 * The sidebar version must come from package.json (vite-defined
 * __APP_VERSION__), not a hand-typed literal. v2.97.0 shipped showing
 * "v2.96.0" because AppShell carried a fourth, untracked version marker.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const src = readFileSync(resolve(__dirname, '../AppShell.tsx'), 'utf8');

describe('AppShell version label', () => {
  it('has no hard-coded semver literal', () => {
    expect(src).not.toMatch(/\bv\d+\.\d+\.\d+\b/);
  });
  it('renders __APP_VERSION__', () => {
    expect(src).toContain('__APP_VERSION__');
  });
});
