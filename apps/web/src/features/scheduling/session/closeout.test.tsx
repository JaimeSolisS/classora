import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { applyLanguage } from '@/i18n';
import type { SessionUserBody } from '@/features/auth/session';
import { STAFF_VIEW } from '@/features/tutor-portal/attendance/fixtures';
import type { SessionCloseoutView } from '@/features/tutor-portal/closeout/api';
import {
  FINANCE_SUBMITTED,
  NO_CLOSEOUT,
  STAFF_SUBMITTED,
  WAITING,
} from '@/features/tutor-portal/closeout/fixtures';
import { meAs, stubApi, TEST_USER, type ApiStub } from '@/test/api-stub';
import {
  jsonResponse,
  renderApp,
  settle,
  settleUntil,
  type RenderedApp,
} from '@/test/render-app';
import { CREATED } from '../create/fixtures';
import { CONFIRMED_SESSION } from './fixtures';

// #77 H6, G3: the staff session page's read-only Closeout section (the
// submitted closeout with the minutes difference as text, the summary or
// "Summary visible to academic reviewers", "No closeout recorded",
// "Waiting for the tutor's closeout"; nothing for a session that hasn't
// started).

const ID = CREATED.id;
const DETAIL = `GET /api/v1/class-sessions/${ID}`;
const CLOSEOUT = `GET /api/v1/class-sessions/${ID}/closeout`;

const STAFF: SessionUserBody = {
  ...TEST_USER,
  permissions: ['schedule.view', 'attendance.correct'],
};

const PAST = '2026-01-05T16:00:00Z';
const FUTURE = '2099-01-05T16:00:00Z';

let app: RenderedApp | undefined;
let api: ApiStub;
const main = () => app!.container.querySelector('main')!;

function section(): HTMLElement | undefined {
  return [...main().querySelectorAll('section')].find(
    (s) => s.querySelector('h2')?.textContent === 'Closeout',
  );
}

async function open(
  state: string,
  startsAt: string,
  closeout?: SessionCloseoutView,
): Promise<void> {
  api = stubApi({
    'GET /api/v1/auth/me': meAs(STAFF),
    [DETAIL]: () =>
      jsonResponse(200, {
        ...CONFIRMED_SESSION,
        state,
        starts_at: startsAt,
        ends_at: new Date(Date.parse(startsAt) + 3_600_000).toISOString(),
      }),
    [`GET /api/v1/class-sessions/${ID}/offers`]: () =>
      jsonResponse(200, { items: [] }),
    [`GET /api/v1/class-sessions/${ID}/substitutions`]: () =>
      jsonResponse(200, { items: [] }),
    [`GET /api/v1/class-sessions/${ID}/attendance`]: () =>
      jsonResponse(200, STAFF_VIEW),
    ...(closeout ? { [CLOSEOUT]: () => jsonResponse(200, closeout) } : {}),
  });
  app = await renderApp(`/schedule/sessions/${ID}`, undefined);
  await settleUntil(() => (main().textContent ?? '').includes('Attendance'));
  await settle();
}

beforeEach(() => {
  applyLanguage('en');
});

afterEach(() => {
  app?.unmount();
  app = undefined;
  vi.unstubAllGlobals();
});

describe('the closeout section (G3)', () => {
  it('shows a submitted closeout with the summary for reviewers', async () => {
    await open('completed', PAST, STAFF_SUBMITTED);
    await settleUntil(() => section()?.textContent?.includes('Submitted'));

    const content = section()!.textContent ?? '';
    expect(content).toContain('Submitted for review');
    expect(content).toContain('55 of 60 min');
    expect(content).toContain('5 min less than scheduled');
    expect(content).toContain('Carmen Ruiz');
    expect(content).toContain('Fracciones equivalentes');
    expect(content).toContain('Next steps');
    expect(section()!.querySelector('button')).toBeNull();
  });

  it('hides the summary for Finance', async () => {
    await open('completed', PAST, FINANCE_SUBMITTED);
    await settleUntil(() => section()?.textContent?.includes('Submitted'));

    const content = section()!.textContent ?? '';
    expect(content).toContain('55 of 60 min');
    expect(content).toContain('Summary visible to academic reviewers');
    expect(content).not.toContain('Fracciones');
  });

  it('says when no closeout was recorded', async () => {
    await open('completed', PAST, NO_CLOSEOUT);
    await settleUntil(() => section()?.textContent?.includes('No closeout'));
  });

  it("waits for the tutor's closeout on a started session", async () => {
    await open('confirmed', PAST, WAITING);
    await settleUntil(() =>
      section()?.textContent?.includes("Waiting for the tutor's closeout"),
    );
  });

  it('is not shown before the class starts', async () => {
    await open('confirmed', FUTURE);

    expect(section()).toBeUndefined();
    expect(api.calls(CLOSEOUT)).toHaveLength(0);
  });
});
