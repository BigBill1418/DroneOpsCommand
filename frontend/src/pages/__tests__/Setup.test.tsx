/**
 * Setup wizard — ADR-0047 email fix only (Setup.tsx:170 per the brief).
 * The footer/logo/SSO-card treatment was explicitly scoped to the LOGIN
 * page only ("make the bottom of the login page just like CallSignLane") —
 * Setup's footer is intentionally left in its pre-existing shape here.
 */
import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

import Setup from '../Setup';
import TestProviders from '../../test/TestProviders';

describe('Setup', () => {
  it('shows the fixed operator email, exact capitalization, in both href and text', () => {
    render(
      <TestProviders>
        <Setup onSetupComplete={vi.fn()} />
      </TestProviders>,
    );

    const mailLink = screen.getByRole('link', { name: 'Bill@BarnardHQ.com' });
    expect(mailLink).toHaveAttribute('href', 'mailto:Bill@BarnardHQ.com');
    expect(screen.queryByText(/me@barnardHQ\.com/i)).not.toBeInTheDocument();
  });
});
