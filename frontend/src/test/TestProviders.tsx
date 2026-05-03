/**
 * TestProviders — wraps a component under test in MantineProvider +
 * MemoryRouter for v2.67.0 Mission Hub contract tests.
 *
 * Used by the new MissionCreateModal + MissionDetail.hub test files.
 */
import type { ReactNode } from 'react';
import { MantineProvider } from '@mantine/core';
import { Notifications } from '@mantine/notifications';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

interface Props {
  children: ReactNode;
  initialEntries?: string[];
  /**
   * If provided, renders children behind a Route at this path so
   * `useParams()` works inside the children. The wildcard
   * `*` route mirrors the rest of the app's catch-all behaviour.
   */
  routePath?: string;
}

export default function TestProviders({
  children,
  initialEntries = ['/'],
  routePath,
}: Props) {
  const content = routePath ? (
    <Routes>
      <Route path={routePath} element={children as any} />
      <Route path="*" element={children as any} />
    </Routes>
  ) : (
    children
  );

  return (
    <MantineProvider>
      <Notifications />
      <MemoryRouter initialEntries={initialEntries}>{content}</MemoryRouter>
    </MantineProvider>
  );
}
