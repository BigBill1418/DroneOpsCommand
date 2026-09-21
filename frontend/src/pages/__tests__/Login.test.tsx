/**
 * Login screen — ADR-0047 (operator Cloudflare Access SSO, modernized
 * login screen). Covers:
 *
 *   1. Email fix — Bill@BarnardHQ.com, exact capitalization, both the
 *      mailto: href and the visible text; the old me@barnardHQ.com is gone.
 *   2. Footer matches CallSignLane's pattern: an anchor to
 *      https://www.barnardhq.com wrapping "A software solution by:" plus
 *      the real /barnardhq-logo.svg wordmark.
 *   3. SSO-aware rendering: ssoConfigured=false renders the traditional
 *      password-only form (self-hosted/OSS/demo shape, untouched);
 *      ssoConfigured=true shows the Access card ABOVE a still-present
 *      password form; ssoConfigured=true + localLoginDisabled=true hides
 *      the password form entirely.
 */
import { afterEach, beforeAll, afterAll, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { setupServer } from 'msw/node';
import { http, HttpResponse } from 'msw';

import Login from '../Login';
import TestProviders from '../../test/TestProviders';

const server = setupServer(
  http.get('*/api/branding', () => HttpResponse.json({ company_name: 'DroneOpsCommand' })),
  http.get('*/api/demo/status', () => HttpResponse.json({ demo_mode: false })),
);

beforeAll(() => server.listen({ onUnhandledRequest: 'bypass' }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function renderLogin(props: Partial<React.ComponentProps<typeof Login>> = {}) {
  const onLogin = vi.fn().mockResolvedValue(undefined);
  render(
    <TestProviders>
      <Login onLogin={onLogin} {...props} />
    </TestProviders>,
  );
  return { onLogin };
}

describe('Login', () => {
  it('shows the fixed operator email, exact capitalization, in both href and text', () => {
    renderLogin();
    const mailLink = screen.getByRole('link', { name: 'Bill@BarnardHQ.com' });
    expect(mailLink).toHaveAttribute('href', 'mailto:Bill@BarnardHQ.com');
    expect(screen.queryByText(/me@barnardHQ\.com/i)).not.toBeInTheDocument();
  });

  it('renders the CallSignLane-style footer: label + real logo, linking to barnardhq.com', () => {
    renderLogin();
    const credit = screen.getByTestId('barnardhq-credit');
    expect(credit).toHaveAttribute('href', 'https://www.barnardhq.com');
    expect(credit).toHaveAttribute('target', '_blank');
    expect(credit).toHaveAttribute('rel', expect.stringContaining('noopener'));
    expect(credit).toHaveTextContent('A software solution by:');

    const logo = screen.getByAltText('BarnardHQ');
    expect(logo.tagName).toBe('IMG');
    expect(logo).toHaveAttribute('src', '/barnardhq-logo.svg');
  });

  it('ssoConfigured=false renders the traditional password form with no Access card (self-hosted/OSS/demo shape)', () => {
    renderLogin({ ssoConfigured: false, localLoginDisabled: false });

    expect(screen.getByLabelText('Username', { exact: false })).toBeInTheDocument();
    expect(screen.getByLabelText('Password', { exact: false })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'LOGIN' })).toBeInTheDocument();
    expect(screen.queryByTestId('sso-card')).not.toBeInTheDocument();
  });

  it('ssoConfigured=true shows the Access card AND keeps the password form (Step A additive state)', () => {
    renderLogin({ ssoConfigured: true, localLoginDisabled: false });

    expect(screen.getByTestId('sso-card')).toBeInTheDocument();
    expect(screen.getByText(/BARNARDHQ ACCESS/i)).toBeInTheDocument();
    expect(screen.getByLabelText('Username', { exact: false })).toBeInTheDocument();
    expect(screen.getByLabelText('Password', { exact: false })).toBeInTheDocument();
  });

  it('ssoConfigured=true + localLoginDisabled=true hides the password form entirely (Step B state)', () => {
    renderLogin({ ssoConfigured: true, localLoginDisabled: true });

    expect(screen.getByTestId('sso-card')).toBeInTheDocument();
    expect(screen.queryByLabelText('Username', { exact: false })).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Password', { exact: false })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'LOGIN' })).not.toBeInTheDocument();
  });

  it('submitting the password form still calls onLogin with the typed credentials', async () => {
    const { onLogin } = renderLogin();
    const userEvent = (await import('@testing-library/user-event')).default.setup();

    await userEvent.type(screen.getByLabelText('Username', { exact: false }), 'operator');
    await userEvent.type(screen.getByLabelText('Password', { exact: false }), 'hunter2hunter2!');
    await userEvent.click(screen.getByRole('button', { name: 'LOGIN' }));

    expect(onLogin).toHaveBeenCalledWith('operator', 'hunter2hunter2!');
  });
});
