import { lazy, Suspense, useEffect } from 'react';
import { Routes, Route, Navigate, useNavigate, useParams } from 'react-router-dom';
import { Loader, Center } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { useAuth } from './hooks/useAuth';
import ErrorBoundary from './components/ErrorBoundary';
import AppLayout from './components/Layout/AppShell';

// Login + Setup stay eager — they are pre-auth, tiny, and avoid a flash
// for the first paint. Everything else is lazy so the operator's first
// authenticated screen ships as a small chunk.
import Login from './pages/Login';
import Setup from './pages/Setup';

// FIX-3 (v2.63.9): code-split all 17 main authenticated pages so the
// initial bundle is just Login/Setup + the first authenticated route's
// chunk. The 1.9 MB single index-*.js chunk is replaced by a small
// router shell + per-page on-demand chunks. Mantine, Leaflet, tiptap,
// react-pdf, sentry, and tabler-icons are pulled into vendor chunks
// via vite.config.ts manualChunks for cross-page cache reuse.
const Dashboard = lazy(() => import('./pages/Dashboard'));
const Missions = lazy(() => import('./pages/Missions'));
// v2.67.0 Mission Hub redesign (ADR-0014):
// - MissionDetail.tsx is the read-only Hub with 5 facet cards.
// - Per-facet editors are isolated routes; none shares the
//   POST /api/missions code path so the duplicate-mission bug
//   (2026-05-03 18:46/18:49 UTC) is physically impossible.
// - The legacy 5-step wizard (formerly MissionNew.tsx) is kept
//   on disk as MissionWizardLegacy.tsx and mounted at the hidden
//   /missions/:id/edit-legacy soak fallback. Deletion criteria in
//   ADR-0014 §Consequences.
const MissionDetail = lazy(() => import('./pages/MissionDetail'));
const MissionDetailsEdit = lazy(() => import('./pages/MissionDetailsEdit'));
const MissionFlightsEdit = lazy(() => import('./pages/MissionFlightsEdit'));
const MissionImagesEdit = lazy(() => import('./pages/MissionImagesEdit'));
const MissionReportEdit = lazy(() => import('./pages/MissionReportEdit'));
// v2.66.0 Fix #4 — standalone invoice editor for an existing mission.
const MissionInvoiceEdit = lazy(() => import('./pages/MissionInvoiceEdit'));
const MissionWizardLegacy = lazy(() => import('./pages/MissionWizardLegacy'));
const Customers = lazy(() => import('./pages/Customers'));
const Flights = lazy(() => import('./pages/Flights'));
const Batteries = lazy(() => import('./pages/Batteries'));
const Maintenance = lazy(() => import('./pages/Maintenance'));
const Financials = lazy(() => import('./pages/Financials'));
const Settings = lazy(() => import('./pages/Settings'));
const CustomerIntake = lazy(() => import('./pages/CustomerIntake'));
const UploadLogs = lazy(() => import('./pages/UploadLogs'));
const Telemetry = lazy(() => import('./pages/Telemetry'));
const Airspace = lazy(() => import('./pages/Airspace'));
const FlightReplay = lazy(() => import('./pages/FlightReplay'));
const TosAcceptancesAdmin = lazy(() => import('./pages/TosAcceptancesAdmin'));

// Client portal — code-split for separate bundle (already lazy).
const ClientPortal = lazy(() => import('./pages/client/ClientPortal'));
const ClientLogin = lazy(() => import('./pages/client/ClientLogin'));
const ClientMissionDetail = lazy(() => import('./pages/client/ClientMissionDetail'));

// TOS-acceptance page (ADR-0010) — public, no auth.
const TosAcceptance = lazy(() => import('./pages/TosAcceptance'));

// Shared Suspense fallback — same dark theme + cyan loader the auth
// loader already uses, so route transitions don't visually flash.
const PageFallback = (
  <Center h="100vh" style={{ background: '#050608' }}>
    <Loader color="cyan" size="lg" />
  </Center>
);

// v2.67.0 Mission Hub redesign — `/missions/new` no longer exists as
// a standalone page; the Missions list opens MissionCreateModal inline.
// Stale bookmarks degrade gracefully: redirect to /missions and show a
// notification telling the operator to use the New Mission button.
function MissionsNewLegacyRedirect() {
  const navigate = useNavigate();
  useEffect(() => {
    notifications.show({
      title: 'Use the New Mission button',
      message: 'The standalone create page was replaced by the inline modal in v2.67.0.',
      color: 'cyan',
    });
    navigate('/missions', { replace: true });
  }, [navigate]);
  return null;
}

// v2.67.0 — `/missions/:id/edit` (the old wizard URL) becomes a
// soft-redirect to the Hub at `/missions/:id`. Existing operator
// bookmarks land somewhere sensible without exposing the duplicate-
// mission bug class. Per spec §3 + ADR-0014.
function MissionEditLegacyRedirect() {
  const { id } = useParams<{ id: string }>();
  return <Navigate to={`/missions/${id ?? ''}`} replace />;
}

export default function App() {
  const { isAuthenticated, needsSetup, loading, login, logout, completeSetup } = useAuth();

  if (loading) {
    return (
      <Center h="100vh" style={{ background: '#050608' }}>
        <Loader color="cyan" size="lg" />
      </Center>
    );
  }

  return (
    <ErrorBoundary>
      <Routes>
        {/* Public routes — no auth required */}
        <Route path="/tos/accept" element={<Suspense fallback={PageFallback}><TosAcceptance /></Suspense>} />
        <Route path="/intake/:token" element={<Suspense fallback={PageFallback}><CustomerIntake /></Suspense>} />
        <Route path="/client/mission/:missionId" element={<Suspense fallback={PageFallback}><ClientMissionDetail /></Suspense>} />
        <Route path="/client/login" element={<Suspense fallback={PageFallback}><ClientLogin /></Suspense>} />
        <Route path="/client/:token" element={<Suspense fallback={PageFallback}><ClientPortal /></Suspense>} />

        {/* All other routes require authentication */}
        <Route path="*" element={
          needsSetup ? <Setup onSetupComplete={completeSetup} /> :
          !isAuthenticated ? <Login onLogin={login} /> : (
            <Suspense fallback={PageFallback}>
              <Routes>
                <Route element={<AppLayout onLogout={logout} />}>
                  <Route path="/" element={<Dashboard />} />
                  <Route path="/flights" element={<Flights />} />
                  <Route path="/flights/:id/replay" element={<FlightReplay />} />
                  <Route path="/missions" element={<Missions />} />
                  {/* v2.67.0 Mission Hub redesign (ADR-0014) — per spec §3.
                      Order matters: more-specific routes (`/edit-legacy`,
                      `/edit`, `/details/edit`, etc.) live BEFORE the
                      generic `/:id` so they take precedence in matching. */}
                  <Route path="/missions/new" element={<MissionsNewLegacyRedirect />} />
                  <Route path="/missions/:id/edit" element={<MissionEditLegacyRedirect />} />
                  <Route path="/missions/:id/edit-legacy" element={<MissionWizardLegacy />} />
                  <Route path="/missions/:id/details/edit" element={<MissionDetailsEdit />} />
                  <Route path="/missions/:id/flights/edit" element={<MissionFlightsEdit />} />
                  <Route path="/missions/:id/images/edit" element={<MissionImagesEdit />} />
                  <Route path="/missions/:id/report/edit" element={<MissionReportEdit />} />
                  <Route path="/missions/:id/invoice/edit" element={<MissionInvoiceEdit />} />
                  <Route path="/missions/:id" element={<MissionDetail />} />
                  <Route path="/customers" element={<Customers />} />
                  <Route path="/batteries" element={<Batteries />} />
                  <Route path="/maintenance" element={<Maintenance />} />
                  <Route path="/financials" element={<Financials />} />
                  <Route path="/telemetry" element={<Telemetry />} />
                  <Route path="/airspace" element={<Airspace />} />
                  <Route path="/upload-logs" element={<UploadLogs />} />
                  <Route path="/tos-acceptances" element={<TosAcceptancesAdmin />} />
                  <Route path="/settings" element={<Settings />} />
                  <Route path="*" element={<Navigate to="/" />} />
                </Route>
              </Routes>
            </Suspense>
          )
        } />
      </Routes>
    </ErrorBoundary>
  );
}
