/**
 * useAuth — ADR-0047 silent-SSO-probe wiring.
 *
 * The pre-existing local-token behavior (no SSO configured — the shape
 * every self-hosted/OSS install and the public demo instance are in) must
 * be byte-identical to before this ADR. The new behavior only activates
 * when the backend reports `sso_configured: true` on `/auth/setup-status`.
 */
import axios from 'axios';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';

import api from '../../api/client';
import { useAuth } from '../useAuth';

function mockApiAdapter(handlers: Record<string, () => { status: number; data?: unknown }>) {
  api.defaults.adapter = async (config) => {
    const url = config.url ?? '';
    const handler = handlers[url];
    if (!handler) {
      throw { config, response: { status: 404, data: {} } };
    }
    const { status, data } = handler();
    if (status >= 400) {
      // eslint-disable-next-line no-throw-literal
      throw { config, response: { status, data: data ?? {} } };
    }
    return { data, status, statusText: 'OK', headers: {}, config } as never;
  };
}

describe('useAuth', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
  });

  it('needs_setup=true short-circuits to the setup wizard', async () => {
    mockApiAdapter({
      '/auth/setup-status': () => ({ status: 200, data: { needs_setup: true, sso_configured: false, local_login_disabled: false } }),
    });

    const { result } = renderHook(() => useAuth());
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.needsSetup).toBe(true);
    expect(result.current.isAuthenticated).toBe(false);
  });

  it('sso_configured=false with no local token shows the login screen (pre-ADR-0047 shape)', async () => {
    mockApiAdapter({
      '/auth/setup-status': () => ({ status: 200, data: { needs_setup: false, sso_configured: false, local_login_disabled: false } }),
    });
    const getSpy = vi.spyOn(axios, 'get');

    const { result } = renderHook(() => useAuth());
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.isAuthenticated).toBe(false);
    expect(result.current.ssoConfigured).toBe(false);
    // No SSO probe attempted at all when the backend isn't configured for it.
    expect(getSpy).not.toHaveBeenCalledWith('/api/auth/account');
  });

  it('sso_configured=false with a valid local token authenticates (unchanged behavior)', async () => {
    localStorage.setItem('access_token', 'LOCAL_TOKEN');
    localStorage.setItem('refresh_token', 'LOCAL_REFRESH');
    mockApiAdapter({
      '/auth/setup-status': () => ({ status: 200, data: { needs_setup: false, sso_configured: false, local_login_disabled: false } }),
      '/auth/account': () => ({ status: 200, data: { ok: true, user: { username: 'admin' } } }),
    });

    const { result } = renderHook(() => useAuth());
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.isAuthenticated).toBe(true);
  });

  it('sso_configured=true and the silent probe succeeds authenticates with NO local token', async () => {
    mockApiAdapter({
      '/auth/setup-status': () => ({ status: 200, data: { needs_setup: false, sso_configured: true, local_login_disabled: false } }),
    });
    const getSpy = vi.spyOn(axios, 'get').mockImplementation(async (url: string) => {
      if (url === '/api/auth/account') {
        return { data: { ok: true, user: { username: 'bill@barnardhq.com' } } } as never;
      }
      throw new Error(`unexpected bare axios GET ${url}`);
    });

    const { result } = renderHook(() => useAuth());
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.isAuthenticated).toBe(true);
    expect(result.current.ssoConfigured).toBe(true);
    expect(getSpy).toHaveBeenCalledWith('/api/auth/account');
    expect(localStorage.getItem('access_token')).toBeNull();
  });

  it('sso_configured=true, probe fails, no local token -> shows login with ssoConfigured=true', async () => {
    mockApiAdapter({
      '/auth/setup-status': () => ({ status: 200, data: { needs_setup: false, sso_configured: true, local_login_disabled: false } }),
    });
    vi.spyOn(axios, 'get').mockRejectedValue({ response: { status: 401 } });

    const { result } = renderHook(() => useAuth());
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.isAuthenticated).toBe(false);
    expect(result.current.ssoConfigured).toBe(true);
  });

  it('sso_configured=true, probe fails, but a valid local token falls through and authenticates', async () => {
    localStorage.setItem('access_token', 'LOCAL_TOKEN');
    localStorage.setItem('refresh_token', 'LOCAL_REFRESH');
    mockApiAdapter({
      '/auth/setup-status': () => ({ status: 200, data: { needs_setup: false, sso_configured: true, local_login_disabled: false } }),
      '/auth/account': () => ({ status: 200, data: { ok: true, user: { username: 'admin' } } }),
    });
    // Bare axios (the SSO probe) fails; the shared `api` client's
    // /auth/account (the local-token check) is a SEPARATE call path via
    // the adapter above and must still succeed.
    vi.spyOn(axios, 'get').mockRejectedValue({ response: { status: 401 } });

    const { result } = renderHook(() => useAuth());
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.isAuthenticated).toBe(true);
  });

  it('localLoginDisabled is surfaced from setup-status', async () => {
    mockApiAdapter({
      '/auth/setup-status': () => ({ status: 200, data: { needs_setup: false, sso_configured: true, local_login_disabled: true } }),
    });
    vi.spyOn(axios, 'get').mockRejectedValue({ response: { status: 401 } });

    const { result } = renderHook(() => useAuth());
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.localLoginDisabled).toBe(true);
  });
});
