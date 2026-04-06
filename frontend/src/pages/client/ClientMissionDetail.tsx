import { useEffect, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
  Badge,
  Button,
  Card,
  Center,
  Divider,
  Group,
  Loader,
  Paper,
  Stack,
  Stepper,
  Text,
  Title,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import {
  IconArrowLeft,
  IconCalendar,
  IconCheck,
  IconDrone,
  IconFileText,
  IconMapPin,
  IconPackage,
  IconReceipt,
} from '@tabler/icons-react';
import { useClientAuth } from '../../hooks/useClientAuth';
import clientApi from '../../api/clientPortalApi';

interface ClientMissionData {
  id: string;
  title: string;
  mission_type: string;
  description: string | null;
  mission_date: string | null;
  location_name: string | null;
  status: string;
  client_notes: string | null;
  created_at: string;
  image_count: number;
}

const STATUS_PIPELINE = [
  { key: 'scheduled', label: 'Scheduled', icon: IconCalendar, color: '#3b82f6' },
  { key: 'in_progress', label: 'In Progress', icon: IconDrone, color: '#eab308' },
  { key: 'processing', label: 'Processing', icon: IconFileText, color: '#f97316' },
  { key: 'review', label: 'Review', icon: IconCheck, color: '#00d4ff' },
  { key: 'delivered', label: 'Delivered', icon: IconPackage, color: '#22c55e' },
];

const statusColor: Record<string, string> = {
  draft: 'gray',
  scheduled: 'blue',
  in_progress: 'yellow',
  processing: 'orange',
  review: 'cyan',
  delivered: 'green',
  completed: 'green',
  sent: 'teal',
};

const statusLabel: Record<string, string> = {
  draft: 'DRAFT',
  scheduled: 'SCHEDULED',
  in_progress: 'IN PROGRESS',
  processing: 'PROCESSING',
  review: 'READY FOR REVIEW',
  delivered: 'DELIVERED',
  completed: 'COMPLETED',
  sent: 'SENT',
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

function getStepperActive(status: string): number {
  const idx = STATUS_PIPELINE.findIndex((s) => s.key === status);
  if (idx >= 0) return idx;
  if (status === 'completed' || status === 'sent') return STATUS_PIPELINE.length;
  return -1;
}

const cardStyle = { background: '#0e1117', border: '1px solid #1a1f2e' };
const monoFont = { fontFamily: "'Share Tech Mono', monospace" };

export default function ClientMissionDetail() {
  const { missionId } = useParams<{ missionId: string }>();
  const navigate = useNavigate();
  const auth = useClientAuth();
  const [mission, setMission] = useState<ClientMissionData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!missionId) return;
    clientApi
      .get(`/missions/${missionId}`)
      .then((resp) => setMission(resp.data))
      .catch((err) => {
        console.error('[ClientMissionDetail] Failed to load:', err);
        if (err.response?.status === 403) {
          notifications.show({ title: 'Access Denied', message: 'You do not have access to this mission.', color: 'red' });
        } else {
          notifications.show({ title: 'Error', message: 'Failed to load mission details.', color: 'red' });
        }
        navigate(-1);
      })
      .finally(() => setLoading(false));
  }, [missionId, navigate]);

  if (auth.loading || loading) {
    return (
      <Center h="100vh" style={{ background: '#050608' }}>
        <Loader color="cyan" size="lg" />
      </Center>
    );
  }

  if (!auth.isAuthenticated) {
    navigate('/client/login');
    return null;
  }

  if (!mission) {
    return (
      <Center h="100vh" style={{ background: '#050608' }}>
        <Text c="#5a6478">Mission not found.</Text>
      </Center>
    );
  }

  const stepperActive = getStepperActive(mission.status);
  const showStepper = mission.status !== 'draft' && mission.status !== 'sent';

  return (
    <div style={{ minHeight: '100vh', background: '#050608', padding: '24px' }}>
      <Stack gap="lg" style={{ maxWidth: 720, margin: '0 auto' }}>
        <Button
          variant="subtle"
          color="gray"
          size="xs"
          leftSection={<IconArrowLeft size={14} />}
          onClick={() => navigate(-1)}
          style={monoFont}
        >
          BACK TO MISSIONS
        </Button>

        <Card padding="lg" radius="md" style={cardStyle}>
          <Group justify="space-between" align="flex-start" wrap="wrap">
            <div>
              <Title
                order={2}
                c="#e8edf2"
                style={{ fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '2px' }}
              >
                {mission.title.toUpperCase()}
              </Title>
              <Group gap="xs" mt={4}>
                <Badge color={statusColor[mission.status] || 'gray'} variant="light" size="lg" style={monoFont}>
                  {statusLabel[mission.status] || mission.status.toUpperCase()}
                </Badge>
                <Text c="#5a6478" size="sm" style={monoFont}>
                  {typeLabel[mission.mission_type] || mission.mission_type}
                </Text>
              </Group>
            </div>
          </Group>

          <Divider my="md" color="#1a1f2e" />

          <Group gap="xl" wrap="wrap">
            {mission.mission_date && (
              <Group gap={6}>
                <IconCalendar size={14} color="#00d4ff" />
                <Text size="sm" c="#e8edf2" style={monoFont}>{mission.mission_date}</Text>
              </Group>
            )}
            {mission.location_name && (
              <Group gap={6}>
                <IconMapPin size={14} color="#00d4ff" />
                <Text size="sm" c="#e8edf2" style={monoFont}>{mission.location_name}</Text>
              </Group>
            )}
          </Group>

          {mission.description && (
            <>
              <Divider my="md" color="#1a1f2e" />
              <Text size="sm" c="#c0c8d4" style={{ lineHeight: 1.7 }}>
                {mission.description}
              </Text>
            </>
          )}
        </Card>

        {showStepper && (
          <Card padding="lg" radius="md" style={cardStyle}>
            <Title
              order={4}
              c="#e8edf2"
              mb="md"
              style={{ fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '2px' }}
            >
              MISSION PROGRESS
            </Title>
            <Stepper
              active={stepperActive}
              color="cyan"
              size="sm"
              styles={{
                root: { padding: '0 4px' },
                step: { minWidth: 0 },
                stepIcon: { background: '#0e1117', borderColor: '#1a1f2e' },
                stepLabel: { color: '#e8edf2', fontFamily: "'Rajdhani', sans-serif", fontSize: '13px' },
                stepDescription: { color: '#5a6478', fontSize: '11px', fontFamily: "'Share Tech Mono', monospace" },
                separator: { borderColor: '#1a1f2e' },
              }}
            >
              {STATUS_PIPELINE.map((step) => (
                <Stepper.Step
                  key={step.key}
                  label={step.label}
                  icon={<step.icon size={16} />}
                  completedIcon={<IconCheck size={16} />}
                />
              ))}
              <Stepper.Completed>
                <Center py="sm">
                  <Badge color="green" variant="light" size="lg" style={monoFont}>
                    MISSION COMPLETE
                  </Badge>
                </Center>
              </Stepper.Completed>
            </Stepper>
          </Card>
        )}

        {mission.client_notes && (
          <Card padding="lg" radius="md" style={cardStyle}>
            <Title
              order={4}
              c="#e8edf2"
              mb="sm"
              style={{ fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '2px' }}
            >
              OPERATOR NOTES
            </Title>
            <Text size="sm" c="#c0c8d4" style={{ lineHeight: 1.7, whiteSpace: 'pre-wrap' }}>
              {mission.client_notes}
            </Text>
          </Card>
        )}

        <Card padding="lg" radius="md" style={cardStyle}>
          <Group gap="xs" mb="sm">
            <IconPackage size={18} color="#00d4ff" />
            <Title
              order={4}
              c="#e8edf2"
              style={{ fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '2px' }}
            >
              DELIVERABLES
            </Title>
          </Group>
          <Paper
            p="md"
            radius="sm"
            style={{ background: '#050608', border: '1px dashed #1a1f2e' }}
          >
            <Text c="#5a6478" size="sm" ta="center" style={monoFont}>
              Deliverables will be available here once your mission is complete.
            </Text>
          </Paper>
        </Card>

        <Card padding="lg" radius="md" style={cardStyle}>
          <Group gap="xs" mb="sm">
            <IconReceipt size={18} color="#00d4ff" />
            <Title
              order={4}
              c="#e8edf2"
              style={{ fontFamily: "'Bebas Neue', sans-serif", letterSpacing: '2px' }}
            >
              INVOICE
            </Title>
          </Group>
          <Paper
            p="md"
            radius="sm"
            style={{ background: '#050608', border: '1px dashed #1a1f2e' }}
          >
            <Text c="#5a6478" size="sm" ta="center" style={monoFont}>
              Invoice details will appear here when billing is finalized.
            </Text>
          </Paper>
        </Card>
      </Stack>
    </div>
  );
}
