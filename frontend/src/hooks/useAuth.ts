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

    // ADR-0048 — mint a local bearer from the verified Access session.
    // trySso() only proves Cloudflare authenticated this browser; it leaves
    // the SPA holding exactly one credential, the Cf-Access-Jwt-Assertion
    // header, which the edge injects ONLY on paths its Access application
    // covers. The 8 operator-only endpoints under /api/intake/* and
    // /api/tos/* sit behind sibling Access apps with `bypass` policies, so
    // they never see that header and accept a local bearer only. Exchanging
    // here is what makes those routes work for an operator who has never
    // typed a password — and it is the whole reason the password can be
    // retired at all. Bare axios, like trySso(): a failure here is an
    // ordinary "no Access session" outcome that must fall through to the
    // local-token path, never trip the shared client's redirect-on-401.
    // Returns 404 until CF_ACCESS_TEAM_DOMAIN/CF_ACCESS_AUD are set, so
    // this is inert for self-hosted/OSS and the public demo instance.
    const trySsoExchange = async (): Promise<boolean> => {
      try {
        const resp = await axios.post('/api/auth/sso-exchange');
        if (!resp.data?.access_token || !resp.data?.refresh_token) return false;
        localStorage.setItem('access_token', resp.data.access_token);
        localStorage.setItem('refresh_token', resp.data.refresh_token);
        setIsAuthenticated(true);
        return true;
      } catch {
        return false;
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

        // Seek a local bearer by whichever route is available. The mint
        // (ADR-0048) is tried first when Access has verified this browser;
        // otherwise we fall back to validating whatever token is already
        // stored. Exactly one of these runs on the happy path: re-running
        // tryLocalToken() after a successful exchange would send the
        // freshly minted token through a validation whose failure branch
        // DELETES it — throwing away the credential we just acquired.
        let minted = false;
        if (sso && (await trySso())) {
          minted = await trySsoExchange();
        }
        if (!minted) {
          await tryLocalToken();
        }
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
