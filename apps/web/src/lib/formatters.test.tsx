import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import type { Session } from '@/features/auth/session';
import { applyLanguage } from '@/i18n';
import { TEST_USER } from '@/test/api-stub';
import { StoryAuth } from '@/test/StoryAuth';
import { useFormatters } from './formatters';

(
  globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }
).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;

function Sample() {
  const { formatInstant, formatLocalDate, formatMoney } = useFormatters();
  return (
    <p>
      {formatInstant('2026-10-18T05:30:00Z')} | {formatLocalDate('2026-03-01')}{' '}
      | {formatMoney('1250.00')}
    </p>
  );
}

beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe('useFormatters', () => {
  it('binds the active language and the center zone, and follows switches', () => {
    applyLanguage('es');
    act(() => root.render(<Sample />));
    expect(container.textContent).toBe(
      '17 oct 2026, 23:30 | 1 mar 2026 | $1,250.00',
    );

    act(() => applyLanguage('en'));
    expect(container.textContent).toBe(
      'Oct 17, 2026, 11:30 PM | Mar 1, 2026 | $1,250.00',
    );
  });
});

// #37 H2: the zone is the business setting carried by the session.
function signedInWithZone(zone: string): Session {
  return {
    status: 'signedIn',
    user: {
      id: TEST_USER.id,
      username: TEST_USER.username,
      display_name: TEST_USER.display_name,
      roles: TEST_USER.roles,
      permissions: TEST_USER.permissions,
      privacy_notice_required: false,
      preferences: TEST_USER.preferences,
      center_time_zone: zone,
    },
  };
}

function InstantOnly() {
  const { formatInstant } = useFormatters();
  return <p>{formatInstant('2026-10-18T05:30:00Z', 'time')}</p>;
}

describe('useFormatters and the center time zone', () => {
  it("shows instants in the session's center zone, not Mexico City", () => {
    applyLanguage('es');
    act(() =>
      root.render(
        <StoryAuth session={signedInWithZone('America/Cancun')}>
          <InstantOnly />
        </StoryAuth>,
      ),
    );

    // Cancún is UTC-5 all year: 00:30, not 23:30 (Mexico City, UTC-6).
    expect(container.textContent).toBe('00:30');
  });

  it('follows a zone change in the session', () => {
    applyLanguage('es');
    act(() =>
      root.render(
        <StoryAuth session={signedInWithZone('America/Mexico_City')}>
          <InstantOnly />
        </StoryAuth>,
      ),
    );
    expect(container.textContent).toBe('23:30');

    act(() =>
      root.render(
        <StoryAuth session={signedInWithZone('America/Cancun')}>
          <InstantOnly />
        </StoryAuth>,
      ),
    );
    expect(container.textContent).toBe('00:30');
  });

  it('uses the default zone when signed out', () => {
    applyLanguage('es');
    act(() =>
      root.render(
        <StoryAuth session={{ status: 'signedOut' }}>
          <InstantOnly />
        </StoryAuth>,
      ),
    );

    expect(container.textContent).toBe('23:30');
  });
});
