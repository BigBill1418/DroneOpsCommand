import { useCallback, useEffect } from 'react';
import {
  AppShell,
  Burger,
  Drawer,
  Group,
  NavLink,
  ScrollArea,
  Stack,
  Text,
  ActionIcon,
  Tooltip,
} from '@mantine/core';
import { useDisclosure, useMediaQuery } from '@mantine/hooks';
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
  IconCloudUpload,
  IconTimeline,
  IconRadar2,
} from '@tabler/icons-react';
import { useNavigate, useLocation, Outlet } from 'react-router-dom';
import { useBranding } from '../../hooks/useBranding';
import { useDemoMode } from '../../hooks/useDemoMode';

interface AppLayoutProps {
  onLogout: () => void;
}

const navItems = [
  { icon: IconDashboard, label: 'Dashboard', path: '/' },
  { icon: IconPlane, label: 'Flights', path: '/flights' },
  { icon: IconDrone, label: 'Missions', path: '/missions' },
  { icon: IconUsers, label: 'Customers', path: '/customers' },
  { icon: IconBattery3, label: 'Batteries', path: '/batteries' },
  { icon: IconTool, label: 'Maintenance', path: '/maintenance' },
  { icon: IconChartBar, label: 'Financials', path: '/financials' },
  { icon: IconTimeline, label: 'Telemetry', path: '/telemetry' },
  { icon: IconRadar2, label: 'Airspace', path: '/airspace' },
  { icon: IconCloudUpload, label: 'Upload Logs', path: '/upload-logs' },
  { icon: IconSettings, label: 'Settings', path: '/settings' },
];

/* ── Shared nav link list (used in both desktop sidebar and mobile drawer) ── */
function NavContent({
  onNav,
  onLogout,
  currentPath,
}: {
  onNav: (path: string) => void;
  onLogout: () => void;
  currentPath: string;
}) {
  return (
    <>
      <Stack gap={0} style={{ flex: 1 }}>
        {navItems.map((item) => (
          <NavLink
            key={item.path}
            label={item.label}
            leftSection={<item.icon size={18} />}
            active={
              currentPath === item.path ||
              (item.path !== '/' && currentPath.startsWith(item.path))
            }
            onClick={() => onNav(item.path)}
            styles={{
              root: {
                borderRadius: 6,
                marginBottom: 2,
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
      </Stack>

      {/* Footer: logo + logout + version */}
      <div style={{ borderTop: '1px solid #1a1f2e', padding: '8px' }}>
        <div style={{ display: 'flex', justifyContent: 'center', padding: '8px 0 4px' }}>
          <img
            src="/logo.svg"
            alt="BarnardHQ"
            style={{ width: 140, opacity: 0.35 }}
          />
        </div>
        <NavLink
          label="Logout"
          leftSection={<IconLogout size={18} />}
          onClick={onLogout}
          styles={{
            root: { borderRadius: 6, color: '#ff6b6b' },
            label: { fontFamily: "'Rajdhani', sans-serif", fontWeight: 600 },
          }}
        />
        <Group gap={8} mt="xs" px={4}>
          <Text
            size="xs"
            c="#5a6478"
            style={{ fontFamily: "'Share Tech Mono', monospace", fontSize: '15px' }}
          >
            v2.63.13
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
      </div>
    </>
  );
}

export default function AppLayout({ onLogout }: AppLayoutProps) {
  const [opened, { toggle, close }] = useDisclosure(false);
  const navigate = useNavigate();
  const location = useLocation();
  const branding = useBranding();
  const isDemo = useDemoMode();
  // True on phones in portrait AND landscape (landscape phones have width>768
  // but height<500, so Mantine treats them as desktop without this check)
  const isMobile = useMediaQuery('(max-width: 768px)');
  const isLandscapePhone = useMediaQuery(
    '(orientation: landscape) and (max-height: 500px)',
  );
  const useMobileDrawer = isMobile || isLandscapePhone;

  // Close drawer on every route change
  useEffect(() => {
    close();
  }, [location.pathname, close]);

  const handleNav = useCallback(
    (path: string) => {
      navigate(path);
      close();
    },
    [navigate, close],
  );

  const handleLogout = useCallback(() => {
    close();
    onLogout();
  }, [close, onLogout]);

  return (
    <AppShell
      header={{ height: 60 }}
      navbar={
        useMobileDrawer
          ? undefined /* no AppShell navbar on mobile — we use a Drawer instead */
          : { width: 220, breakpoint: 'sm', collapsed: { mobile: !opened } }
      }
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
            {/* Burger: always visible on mobile, hidden on desktop */}
            {useMobileDrawer && (
              <Burger
                opened={opened}
                onClick={toggle}
                size="sm"
                color="#e8edf2"
                aria-label="Toggle navigation"
              />
            )}
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
              onClick={() => handleNav('/')}
            >
              {branding.company_name.toUpperCase()}
            </Text>
            <Text
              size="xs"
              c="#5a6478"
              style={{
                fontFamily: "'Share Tech Mono', monospace",
                letterSpacing: '2px',
              }}
              visibleFrom="sm"
            >
              {branding.company_tagline.toUpperCase()}
            </Text>
          </Group>
          <Group>
            <Tooltip label="Logout">
              <ActionIcon
                variant="subtle"
                color="gray"
                size="lg"
                onClick={onLogout}
              >
                <IconLogout size={18} />
              </ActionIcon>
            </Tooltip>
          </Group>
        </Group>
      </AppShell.Header>

      {isDemo && (
        <div
          style={{
            background: 'linear-gradient(90deg, #ff6b1a, #ff4444)',
            padding: '6px 16px',
            textAlign: 'center',
            fontFamily: "'Share Tech Mono', monospace",
            fontSize: '12px',
            color: '#fff',
            letterSpacing: '1px',
            position: 'relative',
            zIndex: 100,
          }}
        >
          DEMO INSTANCE — Some actions are restricted.{' '}
          <a
            href="https://github.com/BigBill1418/DroneOpsCommand"
            target="_blank"
            rel="noopener noreferrer"
            style={{
              color: '#fff',
              textDecoration: 'underline',
              fontWeight: 700,
            }}
          >
            Deploy Your Own
          </a>
        </div>
      )}

      {/* ── Mobile: Drawer-based navigation ── */}
      {useMobileDrawer && (
        <Drawer
          opened={opened}
          onClose={close}
          size={280}
          position="left"
          withCloseButton={false}
          lockScroll
          overlayProps={{ opacity: 0.6, color: '#000' }}
          styles={{
            content: {
              background: '#0e1117',
              display: 'flex',
              flexDirection: 'column',
            },
            body: {
              padding: '8px',
              display: 'flex',
              flexDirection: 'column',
              height: '100%',
              flex: 1,
            },
            inner: {
              /* ensure drawer starts below the header */
              top: 60,
              height: 'calc(100% - 60px)',
            },
            overlay: {
              top: 60,
              height: 'calc(100% - 60px)',
            },
          }}
          transitionProps={{ transition: 'slide-right', duration: 200 }}
        >
          <NavContent
            onNav={handleNav}
            onLogout={handleLogout}
            currentPath={location.pathname}
          />
        </Drawer>
      )}

      {/* ── Desktop: permanent sidebar ── */}
      {!useMobileDrawer && (
        <AppShell.Navbar>
          <AppShell.Section
            grow
            component={ScrollArea}
            type="auto"
            offsetScrollbars
            p="xs"
          >
            <Stack gap={0}>
              {navItems.map((item) => (
                <NavLink
                  key={item.path}
                  label={item.label}
                  leftSection={<item.icon size={18} />}
                  active={
                    location.pathname === item.path ||
                    (item.path !== '/' &&
                      location.pathname.startsWith(item.path))
                  }
                  onClick={() => handleNav(item.path)}
                  styles={{
                    root: {
                      borderRadius: 6,
                      marginBottom: 2,
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
            </Stack>
          </AppShell.Section>

          <AppShell.Section p="xs" style={{ borderTop: '1px solid #1a1f2e' }}>
            <div style={{ display: 'flex', justifyContent: 'center', padding: '8px 0 4px' }}>
              <img
                src="/logo.svg"
                alt="BarnardHQ"
                style={{ width: 140, opacity: 0.35 }}
              />
            </div>
            <NavLink
              label="Logout"
              leftSection={<IconLogout size={18} />}
              onClick={handleLogout}
              styles={{
                root: { borderRadius: 6, color: '#ff6b6b' },
                label: {
                  fontFamily: "'Rajdhani', sans-serif",
                  fontWeight: 600,
                },
              }}
            />
            <Group gap={8} mt="xs" px={4}>
              <Text
                size="xs"
                c="#5a6478"
                style={{
                  fontFamily: "'Share Tech Mono', monospace",
                  fontSize: '15px',
                }}
              >
                v2.63.13
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
          </AppShell.Section>
        </AppShell.Navbar>
      )}

      <AppShell.Main>
        <Outlet />
      </AppShell.Main>
    </AppShell>
  );
}
