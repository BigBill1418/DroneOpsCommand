import { useState, useEffect, useCallback } from 'react';
import api from '../api/client';

export function useAuth() {
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [loading, setLoading] = useState(true);
  const [passwordCompliant, setPasswordCompliant] = useState(true);

  useEffect(() => {
    const token = localStorage.getItem('access_token');
    if (token) {
      // Verify token and check password compliance
      api.get('/auth/account')
        .then((r) => {
          setIsAuthenticated(true);
          setPasswordCompliant(r.data.password_compliant ?? true);
        })
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
    setPasswordCompliant(resp.data.password_compliant ?? true);
  }, []);

  const logout = useCallback(() => {
    localStorage.removeItem('access_token');
    localStorage.removeItem('refresh_token');
    setIsAuthenticated(false);
    setPasswordCompliant(true);
  }, []);

  const markPasswordCompliant = useCallback(() => {
    setPasswordCompliant(true);
  }, []);

  return { isAuthenticated, loading, login, logout, passwordCompliant, markPasswordCompliant };
}
