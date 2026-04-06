import { lazy, Suspense } from 'react';
import { Routes, Route, Navigate } from 'react-router-dom';
import { Loader, Center } from '@mantine/core';
import { useAuth } from './hooks/useAuth';
import ErrorBoundary from './components/ErrorBoundary';
import AppLayout from './components/Layout/AppShell';
import Login from './pages/Login';
import Setup from './pages/Setup';
import Dashboard from './pages/Dashboard';
import Missions from './pages/Missions';
import MissionNew from './pages/MissionNew';
import MissionDetail from './pages/MissionDetail';
import Customers from './pages/Customers';
import Flights from './pages/Flights';
import Batteries from './pages/Batteries';
import Maintenance from './pages/Maintenance';
import Financials from './pages/Financials';
import Settings from './pages/Settings';
import CustomerIntake from './pages/CustomerIntake';
import UploadLogs from './pages/UploadLogs';
import Telemetry from './pages/Telemetry';
import Airspace from './pages/Airspace';
import FlightReplay from './pages/FlightReplay';

// Client portal — code-split for separate bundle
const ClientPortal = lazy(() => import('./pages/client/ClientPortal'));
const ClientLogin = lazy(() => import('./pages/client/ClientLogin'));

const ClientFallback = (
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
        <Route path="/intake/:token" element={<CustomerIntake />} />
        <Route path="/client/login" element={<Suspense fallback={ClientFallback}><ClientLogin /></Suspense>} />
        <Route path="/client/:token" element={<Suspense fallback={ClientFallback}><ClientPortal /></Suspense>} />

        {/* All other routes require authentication */}
        <Route path="*" element={
          needsSetup ? <Setup onSetupComplete={completeSetup} /> :
          !isAuthenticated ? <Login onLogin={login} /> : (
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
          )
        } />
      </Routes>
    </ErrorBoundary>
  );
}
