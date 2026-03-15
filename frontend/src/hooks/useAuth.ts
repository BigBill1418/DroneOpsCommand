import { useState, useEffect, useCallback } from 'react';
import api from '../api/client';

export function useAuth() {
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const token = localStorage.getItem('access_token');
    if (token) {
      // Verify token by calling health endpoint
      api.get('/health')
        .then(() => setIsAuthenticated(true))
        .catch(() => {
          localStorage.removeItem('access_token');
          localStorage.removeItem('refresh_token');
          setIsAuthenticated(false);
        })
        .finally(() => setLoading(false));
    } else {
      setLoading(false);
    }
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

  return { isAuthenticated, loading, login, logout };
}
