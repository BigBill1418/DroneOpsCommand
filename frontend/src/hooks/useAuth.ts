import { useState, useEffect, useCallback } from 'react';
import api from '../api/client';

export function useAuth() {
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [needsSetup, setNeedsSetup] = useState(false);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const init = async () => {
      try {
        const setupResp = await api.get('/auth/setup-status');
        if (setupResp.data.needs_setup) {
          setNeedsSetup(true);
          setLoading(false);
          return;
        }
        const token = localStorage.getItem('access_token');
        if (token) {
          try {
            await api.get('/auth/account');
            setIsAuthenticated(true);
          } catch {
            localStorage.removeItem('access_token');
            localStorage.removeItem('refresh_token');
            setIsAuthenticated(false);
          }
        }
      } catch {
        const token = localStorage.getItem('access_token');
        if (token) {
          try {
            await api.get('/auth/account');
            setIsAuthenticated(true);
          } catch {
            localStorage.removeItem('access_token');
            localStorage.removeItem('refresh_token');
          }
        }
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
  }, []);

  const completeSetup = useCallback((accessToken: string, refreshToken: string) => {
    localStorage.setItem('access_token', accessToken);
    localStorage.setItem('refresh_token', refreshToken);
    setNeedsSetup(false);
    setIsAuthenticated(true);
  }, []);

  return { isAuthenticated, needsSetup, loading, login, logout, completeSetup };
}
