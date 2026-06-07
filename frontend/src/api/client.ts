import axios from 'axios';

const api = axios.create({
  baseURL: '/api',
  timeout: 30000, // 30s default timeout — prevents indefinite hangs
});

// Attach JWT token to requests
api.interceptors.request.use((config) => {
  const token = localStorage.getItem('access_token');
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// ── Single-flight token refresh (v2.68.2) ──────────────────────────────
// When the 30-min access token expires, the dashboard fires its parallel
// polls (missions, customers, batteries, weather, maintenance, …) which
// all 401 in the same tick. The old interceptor ran a *separate*
// /api/auth/refresh for each 401 — a 7-way refresh storm that blanks and
// reloads the entire dashboard at once (the "portal cycling" symptom) and,
// once the server starts rotating/invalidating refresh tokens, would race
// itself straight to /login. We coalesce all concurrent 401s onto ONE
// in-flight refresh: the first caller starts it, everyone else awaits the
// same promise, then each retries its original request with the new token.
let refreshPromise: Promise<string> | null = null;

function redirectToLogin(): void {
  localStorage.removeItem('access_token');
  localStorage.removeItem('refresh_token');
  window.location.href = '/login';
}

// Runs at most once at a time. Resolves to the fresh access token; rejects
// (and bounces to /login) if there is no usable refresh token or the
// refresh itself fails.
function refreshAccessToken(): Promise<string> {
  if (!refreshPromise) {
    refreshPromise = (async () => {
      const refreshToken = localStorage.getItem('refresh_token');
      if (!refreshToken) {
        redirectToLogin();
        throw new Error('No refresh token');
      }
      try {
        // Bare axios (not `api`) so this request can't recurse through
        // this same 401 interceptor.
        const resp = await axios.post('/api/auth/refresh', {
          refresh_token: refreshToken,
        });
        const { access_token, refresh_token } = resp.data;
        localStorage.setItem('access_token', access_token);
        localStorage.setItem('refresh_token', refresh_token);
        return access_token as string;
      } catch (err) {
        // Surface refresh failures — a past login-lockout bug (v2.38.x) was
        // hard to diagnose because the interceptor swallowed 401s silently.
        console.warn('[auth] token refresh failed — redirecting to /login', err);
        redirectToLogin();
        throw err;
      }
    })().finally(() => {
      // Allow the next genuine expiry to start a new refresh.
      refreshPromise = null;
    });
  }
  return refreshPromise;
}

// Handle 401 responses - try refresh token
api.interceptors.response.use(
  (response) => response,
  async (error) => {
    const originalRequest = error.config;

    // Don't intercept 401s from the login endpoint — let them propagate to the caller
    const isLoginRequest = originalRequest.url?.includes('/auth/login');
    if (error.response?.status === 401 && !originalRequest._retry && !isLoginRequest) {
      originalRequest._retry = true;
      try {
        const accessToken = await refreshAccessToken();
        originalRequest.headers.Authorization = `Bearer ${accessToken}`;
        return api(originalRequest);
      } catch {
        // refreshAccessToken already cleared tokens + redirected to /login.
        return Promise.reject(error);
      }
    }

    return Promise.reject(error);
  }
);

export default api;
