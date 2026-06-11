import { lazy, Suspense } from 'react';
import { Group, Loader, Stack, Tabs, Title } from '@mantine/core';
import {
  IconDatabaseExport,
  IconDrone,
  IconKey,
  IconMail,
  IconPalette,
  IconPlane,
  IconRobot,
  IconSettings,
  IconTool,
  IconUser,
  IconUsers,
} from '@tabler/icons-react';
import { tabStyles } from '../components/settings/shared';

/**
 * Settings — thin tab shell (P2-5 refactor).
 *
 * The page was a 2715-line monolith that mounted 13 `useForm` instances and
 * fired ~19 parallel GETs in a single mount `useEffect`, with zero memoization.
 * Each tab is now its own lazy-loaded subtree under `pages/settings/`. With
 * `keepMounted={false}` Mantine unmounts inactive panels, so only the ACTIVE
 * tab's component mounts — and each tab fetches its own data on mount. The
 * initial page load now fires only the default (BRANDING) tab's single GET
 * instead of all 19.
 *
 * The tab list, values, labels, icons, and `defaultValue="branding"` are
 * preserved exactly so existing deep-links / muscle memory are unchanged.
 */

const BrandingTab = lazy(() => import('./settings/BrandingTab'));
const GeneralTab = lazy(() => import('./settings/GeneralTab'));
const AiTab = lazy(() => import('./settings/AiTab'));
const EmailTab = lazy(() => import('./settings/EmailTab'));
const FlightDataTab = lazy(() => import('./settings/FlightDataTab'));
const FleetTab = lazy(() => import('./settings/FleetTab'));
const DevicesTab = lazy(() => import('./settings/DevicesTab'));
const PilotsTab = lazy(() => import('./settings/PilotsTab'));
const MaintenanceTab = lazy(() => import('./settings/MaintenanceTab'));
const BackupsTab = lazy(() => import('./settings/BackupsTab'));
const AccountTab = lazy(() => import('./settings/AccountTab'));

function TabFallback() {
  return (
    <Group justify="center" py="xl">
      <Loader color="cyan" size="sm" />
    </Group>
  );
}

export default function Settings() {
  return (
    <Stack gap="md">
      <Title order={2} c="#e8edf2" style={{ letterSpacing: '2px' }}>SETTINGS</Title>

      {/* keepMounted={false}: only the active panel mounts, so only the active
          tab's component runs its fetch-on-mount — this is what drops the
          initial 19-GET burst to the single default tab's GET. */}
      <Tabs defaultValue="branding" styles={tabStyles} keepMounted={false}>
        <Tabs.List>
          <Tabs.Tab value="branding" leftSection={<IconPalette size={14} />}>
            BRANDING
          </Tabs.Tab>
          <Tabs.Tab value="general" leftSection={<IconSettings size={14} />}>
            GENERAL
          </Tabs.Tab>
          <Tabs.Tab value="ai" leftSection={<IconRobot size={14} />}>
            AI / REPORTS
          </Tabs.Tab>
          <Tabs.Tab value="email" leftSection={<IconMail size={14} />}>
            EMAIL & BILLING
          </Tabs.Tab>
          <Tabs.Tab value="flight" leftSection={<IconDrone size={14} />}>
            FLIGHT DATA
          </Tabs.Tab>
          <Tabs.Tab value="fleet" leftSection={<IconPlane size={14} />}>
            FLEET & RATES
          </Tabs.Tab>
          <Tabs.Tab value="devices" leftSection={<IconKey size={14} />}>
            DEVICE ACCESS
          </Tabs.Tab>
          <Tabs.Tab value="pilots" leftSection={<IconUsers size={14} />}>
            PILOTS
          </Tabs.Tab>
          <Tabs.Tab value="maintenance" leftSection={<IconTool size={14} />}>
            MAINTENANCE
          </Tabs.Tab>
          <Tabs.Tab value="backups" leftSection={<IconDatabaseExport size={14} />}>
            BACKUPS
          </Tabs.Tab>
          <Tabs.Tab value="account" leftSection={<IconUser size={14} />}>
            ACCOUNT
          </Tabs.Tab>
        </Tabs.List>

        <Tabs.Panel value="branding" pt="md">
          <Suspense fallback={<TabFallback />}><BrandingTab /></Suspense>
        </Tabs.Panel>
        <Tabs.Panel value="general" pt="md">
          <Suspense fallback={<TabFallback />}><GeneralTab /></Suspense>
        </Tabs.Panel>
        <Tabs.Panel value="ai" pt="md">
          <Suspense fallback={<TabFallback />}><AiTab /></Suspense>
        </Tabs.Panel>
        <Tabs.Panel value="email" pt="md">
          <Suspense fallback={<TabFallback />}><EmailTab /></Suspense>
        </Tabs.Panel>
        <Tabs.Panel value="flight" pt="md">
          <Suspense fallback={<TabFallback />}><FlightDataTab /></Suspense>
        </Tabs.Panel>
        <Tabs.Panel value="fleet" pt="md">
          <Suspense fallback={<TabFallback />}><FleetTab /></Suspense>
        </Tabs.Panel>
        <Tabs.Panel value="devices" pt="md">
          <Suspense fallback={<TabFallback />}><DevicesTab /></Suspense>
        </Tabs.Panel>
        <Tabs.Panel value="pilots" pt="md">
          <Suspense fallback={<TabFallback />}><PilotsTab /></Suspense>
        </Tabs.Panel>
        <Tabs.Panel value="maintenance" pt="md">
          <Suspense fallback={<TabFallback />}><MaintenanceTab /></Suspense>
        </Tabs.Panel>
        <Tabs.Panel value="backups" pt="md">
          <Suspense fallback={<TabFallback />}><BackupsTab /></Suspense>
        </Tabs.Panel>
        <Tabs.Panel value="account" pt="md">
          <Suspense fallback={<TabFallback />}><AccountTab /></Suspense>
        </Tabs.Panel>
      </Tabs>
    </Stack>
  );
}
