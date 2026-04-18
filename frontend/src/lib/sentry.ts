/**
 * Sentry/GlitchTip frontend bootstrap for DroneOpsCommand.
 *
 * Activates only when VITE_SENTRY_DSN is set at build time. In the
 * common case (no DSN) this is a pure no-op — no network, no module
 * side-effects beyond the import itself.
 *
 * Runs BEFORE ReactDOM.createRoot so the BrowserClient hooks React's
 * internal error boundaries and any initial chunk-load failures.
 *
 * Redaction: emails and URL query strings are the two most common leak
 * sites on this frontend. The `beforeSend` hook strips the querystring
 * from event.request.url (in case a flow redirected through a URL that
 * included a reset token or intake token) and lets the rest pass — the
 * backend PII scrubber in app/observability/pii.py catches anything
 * returned in API responses that makes it into the breadcrumb trail.
 */

import * as Sentry from "@sentry/react";

declare global {
  interface ImportMetaEnv {
    readonly VITE_SENTRY_DSN?: string;
    readonly VITE_APP_VERSION?: string;
    readonly VITE_SENTRY_ENVIRONMENT?: string;
  }
}

export function initFrontendSentry(): boolean {
  const dsn = import.meta.env?.VITE_SENTRY_DSN;
  if (!dsn) return false;

  const release = import.meta.env?.VITE_APP_VERSION ?? "dev";
  const environment = import.meta.env?.VITE_SENTRY_ENVIRONMENT ?? "production";

  try {
    Sentry.init({
      dsn,
      release: `droneops-frontend@${release}`,
      environment,
      // Lightweight — GlitchTip's throughput budget is modest.
      tracesSampleRate: 0.05,
      // Strip query strings in case they include bearer tokens or
      // single-use intake tokens on a redirect.
      beforeSend(event) {
        if (event.request?.url) {
          try {
            const u = new URL(event.request.url);
            u.search = "";
            event.request.url = u.toString();
          } catch {
            /* leave as-is */
          }
        }
        return event;
      },
    });
    return true;
  } catch {
    // Never let Sentry init fail the app boot.
    return false;
  }
}
