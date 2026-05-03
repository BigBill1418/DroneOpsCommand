/**
 * Client portal — JWT validation wrapper + inline mission-list dashboard.
 *
 * Route: /client/:token
 *   1. Validates the JWT in the URL against /client/validate.
 *   2. On valid: renders the dashboard (mission list).
 *   3. On invalid: renders the access-denied panel with optional
 *      password-login fallback.
 *
 * Customer-facing — wrapped in <CustomerLayout> with the BarnardHQ
 * brand pass (v2.65.0 — TOS-PDF cyan #189cc6, Bebas Neue display,
 * Share Tech Mono mono).
 */
import { useEffect, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
  Badge,
  Button,
  Center,
  Group,
  Loader,
  Paper,
  SimpleGrid,
  Stack,
  Text,
  Title,
} from '@mantine/core';
import { IconDrone } from '@tabler/icons-react';
import { useClientAuth } from '../../hooks/useClientAuth';
import clientApi from '../../api/clientPortalApi';
import CustomerLayout from '../../components/CustomerLayout';
import { customerBrand, customerStyles } from '../../lib/customerTheme';
import { customerNotify } from '../../lib/customerNotify';

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
        customerNotify({
          title: 'Access Denied',
          message: 'This link is invalid or has expired. Please contact your operator for a new one.',
          kind: 'danger',
        });
      }
      setInitializing(false);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  // ── Loading shell ─────────────────────────────────────────
  if (initializing || auth.loading) {
    return (
      <CustomerLayout
        contextSlot={<span style={{ textTransform: 'uppercase' }}>Validating Access</span>}
      >
        <Center py="xl" style={{ minHeight: '40vh' }}>
          <Stack align="center" gap="md">
            <Loader color="cyan" size="lg" />
            <Text
              style={{
                color: customerBrand.textMuted,
                fontFamily: customerBrand.fontMono,
                fontSize: 13,
                letterSpacing: customerBrand.trackMid,
                textTransform: 'uppercase',
              }}
            >
              Validating Access...
            </Text>
          </Stack>
        </Center>
      </CustomerLayout>
    );
  }

  // ── Access denied ─────────────────────────────────────────
  if (!auth.isAuthenticated) {
    return (
      <CustomerLayout
        maxWidth={520}
        contextSlot={<span style={{ textTransform: 'uppercase' }}>Access Denied</span>}
      >
        <Paper p="xl" radius="md" style={customerStyles.card}>
          <Stack align="center" gap="md">
            <Title
              order={2}
              style={{
                ...customerStyles.display,
                color: customerBrand.danger,
                fontSize: 32,
                textAlign: 'center',
              }}
            >
              ACCESS DENIED
            </Title>
            <Text
              style={{
                color: customerBrand.textBody,
                fontFamily: customerBrand.fontBody,
                fontSize: 14,
                textAlign: 'center',
                lineHeight: 1.6,
              }}
            >
              {auth.error || 'This link is invalid or has expired.'}
            </Text>
            <Text
              style={{
                color: customerBrand.textMuted,
                fontFamily: customerBrand.fontMono,
                fontSize: 11,
                letterSpacing: customerBrand.trackTight,
                textAlign: 'center',
              }}
            >
              Please contact your operator for a new portal link.
            </Text>
            {auth.hasPassword && (
              <Button
                variant="outline"
                onClick={() => navigate('/client/login')}
                styles={{
                  root: {
                    fontFamily: customerBrand.fontDisplay,
                    letterSpacing: customerBrand.trackMid,
                    color: customerBrand.brandCyan,
                    borderColor: customerBrand.brandCyan,
                  },
                }}
              >
                LOGIN WITH PASSWORD
              </Button>
            )}
          </Stack>
        </Paper>
      </CustomerLayout>
    );
  }

  return <ClientDashboardInline auth={auth} />;
}

// ─────────────────────────────────────────────────────────────
//  Inline dashboard
// ─────────────────────────────────────────────────────────────

interface ClientMission {
  id: string;
  title: string;
  mission_type: string;
  mission_date: string | null;
  location_name: string | null;
  status: string;
}

const STATUS_COLOR: Record<string, string> = {
  draft: 'gray',
  scheduled: 'blue',
  in_progress: 'yellow',
  processing: 'orange',
  review: 'cyan',
  delivered: 'green',
  completed: 'green',
  sent: 'teal',
};

const TYPE_LABEL: Record<string, string> = {
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

function ClientDashboardInline({ auth }: { auth: ReturnType<typeof useClientAuth> }) {
  const [missions, setMissions] = useState<ClientMission[]>([]);
  const [loadingMissions, setLoadingMissions] = useState(true);

  useEffect(() => {
    clientApi
      .get('/missions')
      .then((resp) => setMissions(resp.data))
      .catch((err) => {
        console.error('[ClientPortal] Failed to load missions:', err);
        customerNotify({
          title: 'Error',
          message: 'Failed to load missions. Please try refreshing.',
          kind: 'danger',
        });
      })
      .finally(() => setLoadingMissions(false));
  }, []);

  return (
    <CustomerLayout
      contextSlot={
        <Group gap="md" wrap="nowrap" justify="flex-end">
          {auth.customerName && (
            <Text
              component="span"
              visibleFrom="sm"
              style={{
                color: customerBrand.brandCyan,
                fontFamily: customerBrand.fontMono,
                fontSize: 12,
                letterSpacing: customerBrand.trackTight,
              }}
            >
              {auth.customerName.toUpperCase()}
            </Text>
          )}
          <Button
            variant="subtle"
            size="xs"
            onClick={auth.logout}
            styles={{
              root: {
                color: customerBrand.textOnBrand,
                background: 'rgba(255,255,255,0.06)',
                fontFamily: customerBrand.fontMono,
                letterSpacing: customerBrand.trackTight,
                minHeight: 32,
              },
            }}
          >
            SIGN OUT
          </Button>
        </Group>
      }
    >
      <div>
        <Title
          order={1}
          style={{
            ...customerStyles.display,
            color: customerBrand.textPrimary,
            fontSize: 'clamp(28px, 5vw, 40px)',
            marginBottom: 4,
          }}
        >
          CLIENT PORTAL
        </Title>
        <Text
          style={{
            color: customerBrand.textMuted,
            fontFamily: customerBrand.fontMono,
            fontSize: 13,
            letterSpacing: customerBrand.trackTight,
          }}
        >
          {auth.customerName ? `Welcome, ${auth.customerName}` : 'Welcome'}
        </Text>
      </div>

      <Title
        order={3}
        style={{
          ...customerStyles.display,
          color: customerBrand.brandCyan,
          fontSize: 22,
          letterSpacing: customerBrand.trackMid,
          marginTop: 8,
        }}
      >
        YOUR MISSIONS
      </Title>

      {loadingMissions ? (
        <Center py="xl">
          <Loader color="cyan" size="md" />
        </Center>
      ) : missions.length === 0 ? (
        <Paper p="xl" radius="md" style={customerStyles.card}>
          <Center>
            <Text
              style={{
                color: customerBrand.textMuted,
                fontFamily: customerBrand.fontMono,
                fontSize: 13,
              }}
            >
              No missions available yet.
            </Text>
          </Center>
        </Paper>
      ) : (
        <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
          {missions.map((m) => (
            <MissionCard key={m.id} mission={m} />
          ))}
        </SimpleGrid>
      )}
    </CustomerLayout>
  );
}

function MissionCard({ mission }: { mission: ClientMission }) {
  return (
    <Paper
      p="md"
      radius="md"
      style={{
        ...customerStyles.card,
        cursor: 'pointer',
        transition: 'border-color 0.18s ease, transform 0.18s ease, box-shadow 0.18s ease',
      }}
      onMouseEnter={(e) => {
        const el = e.currentTarget as HTMLElement;
        el.style.borderColor = customerBrand.brandCyan;
        el.style.boxShadow = `0 0 0 3px ${customerBrand.brandCyanSoft}, ${customerBrand.shadowCard}`;
      }}
      onMouseLeave={(e) => {
        const el = e.currentTarget as HTMLElement;
        el.style.borderColor = customerBrand.border;
        el.style.boxShadow = customerBrand.shadowCard;
      }}
      onClick={() => (window.location.href = `/client/mission/${mission.id}`)}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          window.location.href = `/client/mission/${mission.id}`;
        }
      }}
      role="link"
      tabIndex={0}
      aria-label={`Open mission ${mission.title}`}
    >
      <Group justify="space-between" mb="xs" wrap="nowrap">
        <Group gap="xs" wrap="nowrap" style={{ minWidth: 0 }}>
          <IconDrone size={18} color={customerBrand.brandCyan} style={{ flexShrink: 0 }} />
          <Text
            fw={600}
            style={{
              color: customerBrand.textPrimary,
              fontFamily: customerBrand.fontDisplay,
              fontSize: 18,
              letterSpacing: customerBrand.trackTight,
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
          >
            {mission.title}
          </Text>
        </Group>
        <Badge
          color={STATUS_COLOR[mission.status] || 'gray'}
          variant="light"
          size="sm"
          style={{ fontFamily: customerBrand.fontMono, flexShrink: 0 }}
        >
          {mission.status.toUpperCase()}
        </Badge>
      </Group>

      <Stack gap={4}>
        <Text
          size="xs"
          style={{ color: customerBrand.textMuted, fontFamily: customerBrand.fontMono }}
        >
          {TYPE_LABEL[mission.mission_type] || mission.mission_type}
        </Text>
        {mission.mission_date && (
          <Text
            size="xs"
            style={{ color: customerBrand.textMuted, fontFamily: customerBrand.fontMono }}
          >
            {mission.mission_date}
          </Text>
        )}
        {mission.location_name && (
          <Text
            size="xs"
            style={{ color: customerBrand.textMuted, fontFamily: customerBrand.fontMono }}
          >
            {mission.location_name}
          </Text>
        )}
      </Stack>
    </Paper>
  );
}
