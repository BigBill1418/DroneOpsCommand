import { useEffect, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { Center, Loader, Stack, Text, Paper, Button } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { useClientAuth } from '../../hooks/useClientAuth';

/**
 * Route wrapper for /client/:token
 * Validates the token from URL, stores JWT, then renders the dashboard.
 */
export default function ClientPortal() {
  const { token } = useParams<{ token: string }>();
  const navigate = useNavigate();
  const auth = useClientAuth();
  const [initializing, setInitializing] = useState(true);

  useEffect(() => {
    if (!token) {
      setInitializing(false);
      return;
    }

    // If already authenticated with this token, skip re-validation
    if (auth.isAuthenticated && !auth.loading) {
      setInitializing(false);
      return;
    }

    auth.initFromToken(token).then((valid) => {
      if (!valid) {
        notifications.show({
          title: 'Access Denied',
          message: 'This link is invalid or has expired. Please contact your operator for a new one.',
          color: 'red',
        });
      }
      setInitializing(false);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  if (initializing || auth.loading) {
    return (
      <Center h="100vh" style={{ background: '#050608' }}>
        <Stack align="center" gap="md">
          <Loader color="cyan" size="lg" />
          <Text c="#5a6478" size="sm" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
            VALIDATING ACCESS...
          </Text>
        </Stack>
      </Center>
    );
  }

  if (!auth.isAuthenticated) {
    return (
      <Center h="100vh" style={{ background: '#050608' }}>
        <Paper
          p="xl"
          radius="md"
          style={{
            background: '#0e1117',
            border: '1px solid #1a1f2e',
            maxWidth: 480,
            width: '100%',
          }}
        >
          <Stack align="center" gap="md">
            <Text
              size="xl"
              fw={700}
              style={{ fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '3px' }}
              c="#e8edf2"
            >
              ACCESS DENIED
            </Text>
            <Text c="#5a6478" size="sm" ta="center">
              {auth.error || 'This link is invalid or has expired.'}
            </Text>
            <Text c="#5a6478" size="xs" ta="center">
              Please contact your operator for a new portal link.
            </Text>
            {auth.hasPassword && (
              <Button
                variant="outline"
                color="cyan"
                onClick={() => navigate('/client/login')}
                style={{ fontFamily: "'Share Tech Mono', monospace" }}
              >
                LOGIN WITH PASSWORD
              </Button>
            )}
          </Stack>
        </Paper>
      </Center>
    );
  }

  // Lazy-loaded dashboard will be rendered here via the route tree
  // For now, inline the dashboard component
  return <ClientDashboardInline auth={auth} />;
}

/* Inline dashboard — will be extracted to ClientDashboard.tsx via lazy loading in App.tsx */
import { Badge, Group, SimpleGrid, Title } from '@mantine/core';
import { IconDrone } from '@tabler/icons-react';
import clientApi from '../../api/clientPortalApi';

interface ClientMission {
  id: string;
  title: string;
  mission_type: string;
  mission_date: string | null;
  location_name: string | null;
  status: string;
}

function ClientDashboardInline({ auth }: { auth: ReturnType<typeof useClientAuth> }) {
  const [missions, setMissions] = useState<ClientMission[]>([]);
  const [loadingMissions, setLoadingMissions] = useState(true);

  useEffect(() => {
    clientApi
      .get('/missions')
      .then((resp) => setMissions(resp.data))
      .catch((err) => {
        console.error('[ClientPortal] Failed to load missions:', err);
        notifications.show({
          title: 'Error',
          message: 'Failed to load missions. Please try refreshing.',
          color: 'red',
        });
      })
      .finally(() => setLoadingMissions(false));
  }, []);

  const statusColor: Record<string, string> = {
    draft: 'yellow',
    completed: 'green',
    sent: 'cyan',
  };

  const typeLabel: Record<string, string> = {
    sar: 'Search & Rescue',
    videography: 'Videography',
    lost_pet: 'Lost Pet',
    inspection: 'Inspection',
    mapping: 'Mapping',
    photography: 'Photography',
    survey: 'Survey',
    security_investigations: 'Security',
    other: 'Other',
  };

  return (
    <div style={{ minHeight: '100vh', background: '#050608', padding: '24px' }}>
      <Stack gap="lg" style={{ maxWidth: 960, margin: '0 auto' }}>
        {/* Header */}
        <Group justify="space-between" align="center">
          <div>
            <Title
              order={2}
              style={{ fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '3px' }}
              c="#e8edf2"
            >
              CLIENT PORTAL
            </Title>
            <Text c="#5a6478" size="sm" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
              {auth.customerName ? `Welcome, ${auth.customerName}` : 'Welcome'}
            </Text>
          </div>
          <Button
            variant="subtle"
            color="gray"
            size="xs"
            onClick={auth.logout}
            style={{ fontFamily: "'Share Tech Mono', monospace" }}
          >
            SIGN OUT
          </Button>
        </Group>

        {/* Mission list */}
        <Title
          order={4}
          style={{ fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '2px' }}
          c="#e8edf2"
        >
          YOUR MISSIONS
        </Title>

        {loadingMissions ? (
          <Center py="xl">
            <Loader color="cyan" size="md" />
          </Center>
        ) : missions.length === 0 ? (
          <Paper
            p="xl"
            radius="md"
            style={{ background: '#0e1117', border: '1px solid #1a1f2e' }}
          >
            <Center>
              <Text c="#5a6478">No missions available yet.</Text>
            </Center>
          </Paper>
        ) : (
          <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
            {missions.map((m) => (
              <Paper
                key={m.id}
                p="md"
                radius="md"
                style={{
                  background: '#0e1117',
                  border: '1px solid #1a1f2e',
                  cursor: 'pointer',
                  transition: 'border-color 0.2s',
                }}
                onMouseEnter={(e) => {
                  (e.currentTarget as HTMLElement).style.borderColor = '#00d4ff44';
                }}
                onMouseLeave={(e) => {
                  (e.currentTarget as HTMLElement).style.borderColor = '#1a1f2e';
                }}
              >
                <Group justify="space-between" mb="xs">
                  <Group gap="xs">
                    <IconDrone size={18} color="#00d4ff" />
                    <Text
                      fw={600}
                      c="#e8edf2"
                      style={{ fontFamily: "'Rajdhani', sans-serif" }}
                    >
                      {m.title}
                    </Text>
                  </Group>
                  <Badge
                    color={statusColor[m.status] || 'gray'}
                    variant="light"
                    size="sm"
                    style={{ fontFamily: "'Share Tech Mono', monospace" }}
                  >
                    {m.status.toUpperCase()}
                  </Badge>
                </Group>

                <Stack gap={4}>
                  <Text size="xs" c="#5a6478" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
                    {typeLabel[m.mission_type] || m.mission_type}
                  </Text>
                  {m.mission_date && (
                    <Text size="xs" c="#5a6478" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
                      {m.mission_date}
                    </Text>
                  )}
                  {m.location_name && (
                    <Text size="xs" c="#5a6478" style={{ fontFamily: "'Share Tech Mono', monospace" }}>
                      {m.location_name}
                    </Text>
                  )}
                </Stack>
              </Paper>
            ))}
          </SimpleGrid>
        )}
      </Stack>
    </div>
  );
}
