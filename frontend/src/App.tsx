import { Routes, Route, Navigate } from 'react-router-dom';
import { Loader, Center } from '@mantine/core';
import { useAuth } from './hooks/useAuth';
import AppLayout from './components/Layout/AppShell';
import Login from './pages/Login';
import Dashboard from './pages/Dashboard';
import Missions from './pages/Missions';
import MissionNew from './pages/MissionNew';
import MissionDetail from './pages/MissionDetail';
import Customers from './pages/Customers';
import Flights from './pages/Flights';
import Settings from './pages/Settings';

export default function App() {
  const { isAuthenticated, loading, login, logout } = useAuth();

  if (loading) {
    return (
      <Center h="100vh" style={{ background: '#050608' }}>
        <Loader color="cyan" size="lg" />
      </Center>
    );
  }

  if (!isAuthenticated) {
    return <Login onLogin={login} />;
  }

  return (
    <Routes>
      <Route element={<AppLayout onLogout={logout} />}>
        <Route path="/" element={<Dashboard />} />
        <Route path="/flights" element={<Flights />} />
        <Route path="/missions" element={<Missions />} />
        <Route path="/missions/new" element={<MissionNew />} />
        <Route path="/missions/:id/edit" element={<MissionNew />} />
        <Route path="/missions/:id" element={<MissionDetail />} />
        <Route path="/customers" element={<Customers />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="*" element={<Navigate to="/" />} />
      </Route>
    </Routes>
  );
}
