import axios from 'axios';

const CLIENT_TOKEN_KEY = 'client_access_token';

const clientApi = axios.create({
  baseURL: '/api/client',
  timeout: 30000,
});

// Attach client JWT from localStorage
clientApi.interceptors.request.use((config) => {
  const token = localStorage.getItem(CLIENT_TOKEN_KEY);
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// On 401, clear stale token — no refresh flow for client tokens
clientApi.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      localStorage.removeItem(CLIENT_TOKEN_KEY);
      console.error('[ClientPortal] Token rejected — cleared from storage');
    }
    return Promise.reject(error);
  },
);

export function setClientToken(token: string): void {
  localStorage.setItem(CLIENT_TOKEN_KEY, token);
}

export function getClientToken(): string | null {
  return localStorage.getItem(CLIENT_TOKEN_KEY);
}

export function clearClientToken(): void {
  localStorage.removeItem(CLIENT_TOKEN_KEY);
}

export default clientApi;
