import { useState, useEffect, useCallback } from 'react';
import clientApi, { setClientToken, getClientToken, clearClientToken } from '../api/clientPortalApi';

export interface ClientAuthState {
  isAuthenticated: boolean;
  loading: boolean;
  customerName: string | null;
  customerEmail: string | null;
  customerId: string | null;
  missionIds: string[];
  expiresAt: string | null;
  hasPassword: boolean;
  error: string | null;
  /** Initialize from a JWT token (URL param or login response) */
  initFromToken: (token: string) => Promise<boolean>;
  /** Password-based login for repeat clients */
  loginWithPassword: (email: string, password: string) => Promise<boolean>;
  logout: () => void;
}

export function useClientAuth(): ClientAuthState {
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [loading, setLoading] = useState(true);
  const [customerName, setCustomerName] = useState<string | null>(null);
  const [customerEmail, setCustomerEmail] = useState<string | null>(null);
  const [customerId, setCustomerId] = useState<string | null>(null);
  const [missionIds, setMissionIds] = useState<string[]>([]);
  const [expiresAt, setExpiresAt] = useState<string | null>(null);
  const [hasPassword, setHasPassword] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const applyValidation = useCallback((data: {
    valid: boolean;
    customer_name?: string | null;
    customer_email?: string | null;
    customer_id?: string | null;
    mission_ids?: string[];
    expires_at?: string | null;
    has_password?: boolean;
  }) => {
    if (data.valid) {
      setIsAuthenticated(true);
      setCustomerName(data.customer_name ?? null);
      setCustomerEmail(data.customer_email ?? null);
      setCustomerId(data.customer_id ?? null);
      setMissionIds(data.mission_ids ?? []);
      setExpiresAt(data.expires_at ?? null);
      setHasPassword(data.has_password ?? false);
      setError(null);
      return true;
    }
    clearClientToken();
    setIsAuthenticated(false);
    return false;
  }, []);

  const initFromToken = useCallback(async (token: string): Promise<boolean> => {
    setLoading(true);
    setError(null);
    try {
      setClientToken(token);
      const resp = await clientApi.post('/auth/validate');
      const ok = applyValidation(resp.data);
      if (!ok) setError('Invalid or expired link');
      return ok;
    } catch (err: any) {
      console.error('[ClientAuth] Token validation failed:', err);
      clearClientToken();
      setIsAuthenticated(false);
      setError('Unable to validate access link');
      return false;
    } finally {
      setLoading(false);
    }
  }, [applyValidation]);

  const loginWithPassword = useCallback(async (email: string, password: string): Promise<boolean> => {
    setLoading(true);
    setError(null);
    try {
      const resp = await clientApi.post('/auth/login', { email, password });
      const { access_token, customer_name, mission_ids: mids, expires_at: exp } = resp.data;
      setClientToken(access_token);
      setIsAuthenticated(true);
      setCustomerName(customer_name);
      setMissionIds(mids ?? []);
      setExpiresAt(exp);
      setError(null);
      return true;
    } catch (err: any) {
      const msg = err.response?.data?.detail || 'Login failed';
      setError(msg);
      setIsAuthenticated(false);
      return false;
    } finally {
      setLoading(false);
    }
  }, []);

  const logout = useCallback(() => {
    clearClientToken();
    setIsAuthenticated(false);
    setCustomerName(null);
    setCustomerEmail(null);
    setCustomerId(null);
    setMissionIds([]);
    setExpiresAt(null);
    setError(null);
  }, []);

  // On mount: check if there's an existing token in localStorage
  useEffect(() => {
    const existing = getClientToken();
    if (existing) {
      initFromToken(existing).finally(() => setLoading(false));
    } else {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return {
    isAuthenticated,
    loading,
    customerName,
    customerEmail,
    customerId,
    missionIds,
    expiresAt,
    hasPassword,
    error,
    initFromToken,
    loginWithPassword,
    logout,
  };
}
