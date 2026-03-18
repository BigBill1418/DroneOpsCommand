import { useState } from 'react';
import {
  AppShell,
  Burger,
  Group,
  NavLink,
  Text,
  ActionIcon,
  Tooltip,
} from '@mantine/core';
import {
  IconBattery3,
  IconBrandGithub,
  IconChartBar,
  IconDashboard,
  IconDrone,
  IconTool,
  IconUsers,
  IconSettings,
  IconLogout,
  IconPlane,
} from '@tabler/icons-react';
import { useNavigate, useLocation, Outlet } from 'react-router-dom';
import { useBranding } from '../../hooks/useBranding';

interface AppLayoutProps {
  onLogout: () => void;
}

export default function AppLayout({ onLogout }: AppLayoutProps) {
  const [opened, setOpened] = useState(false);
  const navigate = useNavigate();
  const location = useLocation();
  const branding = useBranding();

  const navItems = [
    { icon: IconDashboard, label: 'Dashboard', path: '/' },
    { icon: IconPlane, label: 'Flights', path: '/flights' },
    { icon: IconDrone, label: 'Missions', path: '/missions' },
    { icon: IconUsers, label: 'Customers', path: '/customers' },
    { icon: IconBattery3, label: 'Batteries', path: '/batteries' },
    { icon: IconTool, label: 'Maintenance', path: '/maintenance' },
    { icon: IconChartBar, label: 'Financials', path: '/financials' },
    { icon: IconSettings, label: 'Settings', path: '/settings' },
  ];

  return (
    <AppShell
      header={{ height: 60 }}
      navbar={{ width: 220, breakpoint: 'sm', collapsed: { mobile: !opened } }}
      padding="md"
      styles={{
        main: { background: '#050608' },
        header: {
          background: 'linear-gradient(135deg, #050608 0%, #0e1117 100%)',
          borderBottom: '1px solid #1a1f2e',
        },
        navbar: {
          background: '#0e1117',
          borderRight: '1px solid #1a1f2e',
        },
      }}
    >
      <AppShell.Header>
        <Group h="100%" px="md" justify="space-between">
          <Group>
            <Burger opened={opened} onClick={() => setOpened(!opened)} hiddenFrom="sm" size="sm" color="#e8edf2" />
            <Text
              size="xl"
              fw={700}
              style={{
                fontFamily: "'Bebas Neue', sans-serif",
                letterSpacing: '3px',
                fontSize: '24px',
                cursor: 'pointer',
              }}
              c="#e8edf2"
              onClick={() => navigate('/')}
            >
              {branding.company_name.toUpperCase()}
            </Text>
            <Text
              size="xs"
              c="#5a6478"
              style={{ fontFamily: "'Share Tech Mono', monospace", letterSpacing: '2px' }}
              visibleFrom="sm"
            >
              {branding.company_tagline.toUpperCase()}
            </Text>
          </Group>
          <Group>
            <Tooltip label="Logout">
              <ActionIcon variant="subtle" color="gray" size="lg" onClick={onLogout}>
                <IconLogout size={18} />
              </ActionIcon>
            </Tooltip>
          </Group>
        </Group>
      </AppShell.Header>

      <AppShell.Navbar p="xs">
        {navItems.map((item) => (
          <NavLink
            key={item.path}
            label={item.label}
            leftSection={<item.icon size={18} />}
            active={location.pathname === item.path || (item.path !== '/' && location.pathname.startsWith(item.path))}
            onClick={() => {
              navigate(item.path);
              setOpened(false);
            }}
            styles={{
              root: {
                borderRadius: 6,
                marginBottom: 4,
                color: '#e8edf2',
                '&[dataActive]': {
                  backgroundColor: 'rgba(0, 212, 255, 0.1)',
                  color: '#00d4ff',
                },
              },
              label: {
                fontFamily: "'Rajdhani', sans-serif",
                fontWeight: 600,
                letterSpacing: '0.5px',
              },
            }}
          />
        ))}
        <Group
          gap={8}
          style={{
            position: 'absolute',
            bottom: 12,
            left: 16,
          }}
        >
          <Text
            size="xs"
            c="#5a6478"
            style={{
              fontFamily: "'Share Tech Mono', monospace",
              fontSize: '12px',
            }}
          >
            v2.9.0
          </Text>
          <Tooltip label="Star on GitHub" position="right">
            <ActionIcon
              variant="subtle"
              color="gray"
              size="xs"
              component="a"
              href="https://github.com/BigBill1418/DroneOpsCommand"
              target="_blank"
              rel="noopener noreferrer"
            >
              <IconBrandGithub size={14} />
            </ActionIcon>
          </Tooltip>
        </Group>
      </AppShell.Navbar>

      <AppShell.Main>
        <Outlet />
      </AppShell.Main>
    </AppShell>
  );
}
