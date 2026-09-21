import { useState, useEffect, useCallback } from 'react';
import axios from 'axios';
import api from '../api/client';

export function useAuth() {
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [needsSetup, setNeedsSetup] = useState(false);
  const [loading, setLoading] = useState(true);
  // ADR-0047 — whether the backend has Cloudflare Access verification
  // wired up at all. Self-hosted/OSS installs and the public demo
  // instance always read false here (no Access, no probe, no behavior
  // change from pre-ADR-0047).
  const [ssoConfigured, setSsoConfigured] = useState(false);
  const [localLoginDisabled, setLocalLoginDisabled] = useState(false);

  useEffect(() => {
    const tryLocalToken = async () => {
      const token = localStorage.getItem('access_token');
      if (!token) return;
      try {
        await api.get('/auth/account');
        setIsAuthenticated(true);
      } catch {
        localStorage.removeItem('access_token');
        localStorage.removeItem('refresh_token');
      }
    };

    // ADR-0047 — silent SSO probe. Cloudflare Access injects the
    // Cf-Access-Jwt-Assertion header on every proxied request
    // automatically once its application exists for this hostname, so an
    // already-Access-authenticated browser can authenticate here with NO
    // Authorization header at all. Bare axios (not the shared `api`
    // client) deliberately bypasses its refresh/redirect-on-401
    // interceptor: a failed probe here is an ordinary "not SSO-
    // authenticated (yet)" outcome that must fall through to the normal
    // local-token check / login screen, never force-navigate the page.
    const trySso = async (): Promise<boolean> => {
      try {
        await axios.get('/api/auth/account');
        setIsAuthenticated(true);
        return true;
      } catch {
        return false;
      }
    };

    const init = async () => {
      try {
        const setupResp = await api.get('/auth/setup-status');
        const sso = !!setupResp.data.sso_configured;
        const localDisabled = !!setupResp.data.local_login_disabled;
        setSsoConfigured(sso);
        setLocalLoginDisabled(localDisabled);

        if (setupResp.data.needs_setup) {
          setNeedsSetup(true);
          setLoading(false);
          return;
        }

        if (sso && (await trySso())) {
          setLoading(false);
          return;
        }

        await tryLocalToken();
      } catch {
        // setup-status itself failed (e.g. DB unreachable) — fall back to
        // whatever local token we have rather than stranding the operator.
        await tryLocalToken();
      } finally {
        setLoading(false);
      }
    };
    init();
  }, []);

  const login = useCallback(async (username: string, password: string) => {
    const resp = await api.post('/auth/login', { username, password });
    localStorage.setItem('access_token', resp.data.access_token);
    localStorage.setItem('refresh_token', resp.data.refresh_token);
    setIsAuthenticated(true);
  }, []);

  const logout = useCallback(() => {
    localStorage.removeItem('access_token');
    localStorage.removeItem('refresh_token');
    setIsAuthenticated(false);
    // Note: this clears the LOCAL credential only. An SSO-authenticated
    // session has no local token to clear — Cloudflare Access owns that
    // session at the edge, so the next mount's silent probe will simply
    // re-authenticate. Ending the Access session itself is out of this
    // app's scope (it's Cloudflare's IdP session, not ours).
  }, []);

  const completeSetup = useCallback((accessToken: string, refreshToken: string) => {
    localStorage.setItem('access_token', accessToken);
    localStorage.setItem('refresh_token', refreshToken);
    setNeedsSetup(false);
    setIsAuthenticated(true);
  }, []);

  return {
    isAuthenticated,
    needsSetup,
    loading,
    ssoConfigured,
    localLoginDisabled,
    login,
    logout,
    completeSetup,
  };
}
