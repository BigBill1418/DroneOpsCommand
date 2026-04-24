import { lazy, Suspense } from 'react';
import { Routes, Route, Navigate } from 'react-router-dom';
import { Loader, Center } from '@mantine/core';
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
const MissionNew = lazy(() => import('./pages/MissionNew'));
const MissionDetail = lazy(() => import('./pages/MissionDetail'));
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

// Client portal — code-split for separate bundle (already lazy).
const ClientPortal = lazy(() => import('./pages/client/ClientPortal'));
const ClientLogin = lazy(() => import('./pages/client/ClientLogin'));
const ClientMissionDetail = lazy(() => import('./pages/client/ClientMissionDetail'));

// Shared Suspense fallback — same dark theme + cyan loader the auth
// loader already uses, so route transitions don't visually flash.
const PageFallback = (
  <Center h="100vh" style={{ background: '#050608' }}>
    <Loader color="cyan" size="lg" />
  </Center>
);

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
                  <Route path="/missions/new" element={<MissionNew />} />
                  <Route path="/missions/:id/edit" element={<MissionNew />} />
                  <Route path="/missions/:id" element={<MissionDetail />} />
                  <Route path="/customers" element={<Customers />} />
                  <Route path="/batteries" element={<Batteries />} />
                  <Route path="/maintenance" element={<Maintenance />} />
                  <Route path="/financials" element={<Financials />} />
                  <Route path="/telemetry" element={<Telemetry />} />
                  <Route path="/airspace" element={<Airspace />} />
                  <Route path="/upload-logs" element={<UploadLogs />} />
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
