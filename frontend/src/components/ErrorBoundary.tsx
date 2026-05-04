import { Component, type ReactNode } from 'react';
import { Box, Button, Center, Stack, Text, Title } from '@mantine/core';
import { IconAlertTriangle, IconRefresh } from '@tabler/icons-react';

interface Props {
  children: ReactNode;
}

interface State {
  hasError: boolean;
  errorMessage: string;
  errorStack: string;
  isStaleBundleError: boolean;
}

// v2.67.2 — match the error messages browsers + Vite emit when a
// dynamic-imported chunk's hash no longer exists on the server (i.e.,
// the user's tab loaded the previous index.html, then we deployed a
// new build, and now `import('./pages/Settings')` 404s on the old hash).
//
// Patterns observed in the wild:
//   Chrome: "Failed to fetch dynamically imported module: https://…/assets/Settings-XXX.js"
//   Vite:   "Loading chunk 12 failed."
//   Safari: "Importing a module script failed."
//   Firefox: "error loading dynamically imported module"
const STALE_BUNDLE_PATTERNS = [
  /Failed to fetch dynamically imported module/i,
  /Loading chunk [\w-]+ failed/i,
  /Importing a module script failed/i,
  /error loading dynamically imported module/i,
];

function isStaleBundle(message: string): boolean {
  return STALE_BUNDLE_PATTERNS.some((re) => re.test(message));
}

// One-shot reload guard. If we reload because of a stale bundle and
// the SAME error fires again (e.g., the deploy is genuinely broken,
// not just a stale tab), don't infinite-loop — show the fallback UI
// instead so the operator sees the actual error.
const RELOAD_FLAG = 'doc.errorBoundary.staleBundleReloadAt';
function hasRecentlyAutoReloaded(): boolean {
  try {
    const ts = sessionStorage.getItem(RELOAD_FLAG);
    if (!ts) return false;
    return Date.now() - Number(ts) < 60_000; // 60s window
  } catch {
    return false;
  }
}
function markAutoReloaded(): void {
  try { sessionStorage.setItem(RELOAD_FLAG, String(Date.now())); } catch { /* non-essential */ }
}

export default class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false, errorMessage: '', errorStack: '', isStaleBundleError: false };

  static getDerivedStateFromError(error: Error): State {
    const msg = error?.message || 'Unknown error';
    return {
      hasError: true,
      errorMessage: msg,
      errorStack: error?.stack || '',
      isStaleBundleError: isStaleBundle(msg),
    };
  }

  componentDidMount() {
    // Catch async dynamic-import rejections that don't reach React's
    // error boundary path (e.g., navigation triggers a lazy import,
    // promise rejects, Suspense fallback may render error in a way
    // that bypasses the boundary depending on React version + concurrent
    // mode).
    window.addEventListener('unhandledrejection', this.handleUnhandledRejection);
    window.addEventListener('error', this.handleWindowError);
  }

  componentWillUnmount() {
    window.removeEventListener('unhandledrejection', this.handleUnhandledRejection);
    window.removeEventListener('error', this.handleWindowError);
  }

  handleUnhandledRejection = (event: PromiseRejectionEvent) => {
    const reason = event.reason;
    const msg = (reason instanceof Error ? reason.message : String(reason)) || '';
    if (isStaleBundle(msg)) {
      this.triggerStaleBundleReload(msg);
    }
  };

  handleWindowError = (event: ErrorEvent) => {
    if (isStaleBundle(event.message || '')) {
      this.triggerStaleBundleReload(event.message);
    }
  };

  triggerStaleBundleReload = (msg: string) => {
    if (hasRecentlyAutoReloaded()) {
      // Already auto-reloaded once recently and still failing — fall
      // through to the manual fallback UI so the operator sees the
      // real error (this isn't a stale-tab issue; the deploy may be
      // genuinely broken).
      this.setState({ hasError: true, errorMessage: msg, errorStack: '', isStaleBundleError: true });
      return;
    }
    markAutoReloaded();
    console.warn('[ErrorBoundary] Stale bundle detected — auto-reloading to pick up new deploy.');
    // Small delay so the user sees what happened (browser console + flash)
    setTimeout(() => window.location.reload(), 250);
  };

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error('[ErrorBoundary] Error:', error?.message);
    console.error('[ErrorBoundary] Stack:', error?.stack);
    console.error('[ErrorBoundary] Component:', info.componentStack);
    // If a stale-bundle error reaches this path (synchronous in React's
    // boundary — uncommon for dynamic imports but possible), trigger
    // the same auto-reload.
    if (isStaleBundle(error?.message || '')) {
      this.triggerStaleBundleReload(error?.message || '');
    }
  }

  handleReset = () => {
    this.setState({ hasError: false, errorMessage: '', errorStack: '', isStaleBundleError: false });
  };

  handleHardReload = () => {
    markAutoReloaded();
    // Bust HTTP cache by appending a timestamp; force the browser to
    // re-fetch index.html (already no-cache server-side, but some
    // intermediaries don't honor that).
    window.location.href = `/?_=${Date.now()}`;
  };

  render() {
    if (this.state.hasError) {
      // v2.67.2 — stale-bundle UX: explain what happened in operator-friendly
      // language ("a new version was deployed; refreshing will pick it up")
      // and offer a one-click hard reload. Auto-reload already fired via
      // triggerStaleBundleReload; this UI shows when auto-reload was already
      // attempted recently OR the user wants to retry manually.
      const isStale = this.state.isStaleBundleError;
      return (
        <Box
          style={{
            minHeight: '100vh',
            background: 'linear-gradient(135deg, #050608 0%, #0e1117 50%, #050608 100%)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
          }}
        >
          <Center>
            <Stack align="center" gap="md">
              {isStale ? (
                <IconRefresh size={48} color="#00d4ff" />
              ) : (
                <IconAlertTriangle size={48} color="#ff6b6b" />
              )}
              <Title
                order={3}
                c="#e8edf2"
                style={{ fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '2px' }}
              >
                {isStale ? 'NEW VERSION AVAILABLE' : 'SOMETHING WENT WRONG'}
              </Title>
              <Text
                c="#5a6478"
                ta="center"
                maw={460}
                style={{ fontFamily: "'Share Tech Mono', monospace" }}
              >
                {isStale
                  ? 'A new version of D.O.C was deployed. Reload to pick it up — your work isn\'t lost.'
                  : 'An unexpected error occurred. Try refreshing the page or navigating back.'}
              </Text>
              {!isStale && this.state.errorMessage && (
                <Text
                  c="#ff6b6b"
                  ta="center"
                  maw={500}
                  size="xs"
                  style={{ fontFamily: "'Share Tech Mono', monospace", wordBreak: 'break-word' }}
                >
                  {this.state.errorMessage}
                </Text>
              )}
              <Stack gap="xs" align="center">
                <Button
                  color="cyan"
                  leftSection={isStale ? <IconRefresh size={16} /> : undefined}
                  onClick={this.handleHardReload}
                  styles={{ root: { fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '2px' } }}
                >
                  {isStale ? 'RELOAD NOW' : 'RETURN TO DASHBOARD'}
                </Button>
                {isStale && (
                  <Text c="#5a6478" size="xs" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
                    or press Cmd/Ctrl + Shift + R
                  </Text>
                )}
              </Stack>
            </Stack>
          </Center>
        </Box>
      );
    }

    return this.props.children;
  }
}
