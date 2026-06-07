/**
 * Single-flight token refresh contract (v2.68.2).
 *
 * Regression guard for the "portal cycling" symptom: when the access token
 * expires, the dashboard's parallel polls all 401 in the same tick. The
 * interceptor must coalesce those onto ONE /api/auth/refresh — not one per
 * 401'd request — and then retry each original request with the new token.
 */
import axios from 'axios';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import api from '../client';

const NEW_ACCESS = 'NEW_ACCESS_TOKEN';
const NEW_REFRESH = 'NEW_REFRESH_TOKEN';

describe('api client single-flight refresh', () => {
  beforeEach(() => {
    localStorage.setItem('access_token', 'STALE_ACCESS');
    localStorage.setItem('refresh_token', 'VALID_REFRESH');
  });

  afterEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
  });

  it('coalesces concurrent 401s into exactly one refresh, then retries all', async () => {
    let refreshCalls = 0;

    // Mock the bare-axios refresh POST. Small delay so the concurrent
    // callers genuinely overlap on the in-flight promise.
    const postSpy = vi.spyOn(axios, 'post').mockImplementation(async (url: string) => {
      if (url === '/api/auth/refresh') {
        refreshCalls += 1;
        await new Promise((r) => setTimeout(r, 20));
        return { data: { access_token: NEW_ACCESS, refresh_token: NEW_REFRESH } } as never;
      }
      throw new Error(`unexpected POST ${url}`);
    });

    // Protected-endpoint adapter: 401 until the request carries the new
    // access token, then 200. Mirrors the real "stale token → refresh →
    // retry succeeds" path.
    const calls: Record<string, number> = {};
    api.defaults.adapter = async (config) => {
      const url = config.url ?? '';
      calls[url] = (calls[url] ?? 0) + 1;
      const auth = config.headers?.Authorization;
      if (auth === `Bearer ${NEW_ACCESS}`) {
        return { data: { url }, status: 200, statusText: 'OK', headers: {}, config } as never;
      }
      // eslint-disable-next-line no-throw-literal
      throw { config, response: { status: 401, data: {} } };
    };

    const paths = ['/missions', '/customers', '/batteries', '/weather/current', '/maintenance/due'];
    const results = await Promise.all(paths.map((p) => api.get(p)));

    // Exactly one refresh despite five simultaneous 401s.
    expect(refreshCalls).toBe(1);
    expect(postSpy).toHaveBeenCalledTimes(1);

    // Every original request was retried and ultimately succeeded.
    for (const p of paths) {
      expect(calls[p]).toBe(2); // initial 401 + retry 200
    }
    expect(results.map((r) => r.status)).toEqual(paths.map(() => 200));
    expect(localStorage.getItem('access_token')).toBe(NEW_ACCESS);
    expect(localStorage.getItem('refresh_token')).toBe(NEW_REFRESH);
  });

  it('allows a fresh refresh after the in-flight one settles', async () => {
    const postSpy = vi.spyOn(axios, 'post').mockImplementation(async (url: string) => {
      if (url === '/api/auth/refresh') {
        return { data: { access_token: NEW_ACCESS, refresh_token: NEW_REFRESH } } as never;
      }
      throw new Error(`unexpected POST ${url}`);
    });

    api.defaults.adapter = async (config) => {
      const auth = config.headers?.Authorization;
      if (auth === `Bearer ${NEW_ACCESS}`) {
        return { data: {}, status: 200, statusText: 'OK', headers: {}, config } as never;
      }
      // eslint-disable-next-line no-throw-literal
      throw { config, response: { status: 401, data: {} } };
    };

    await api.get('/missions');
    // Force the next call to 401 again (token "expired" once more).
    localStorage.setItem('access_token', 'STALE_AGAIN');
    await api.get('/customers');

    // Two separate expiry events ⇒ two refreshes (single-flight is per-event,
    // not a permanent latch).
    expect(postSpy).toHaveBeenCalledTimes(2);
  });
});
