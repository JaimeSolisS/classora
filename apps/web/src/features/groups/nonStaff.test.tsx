import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { SessionUserBody } from '@/features/auth/session';
import { applyLanguage, i18n } from '@/i18n';
import {
  meAs,
  stubApi,
  TEST_USER,
  type ApiStub,
  type Responder,
} from '@/test/api-stub';
import { jsonResponse, renderApp, type RenderedApp } from '@/test/render-app';
import {
  FAMILY_GROUP,
  FAMILY_SESSIONS,
  GROUP_ID,
  ONE_CHILD_SESSIONS,
  STUDENT_LIST_ITEMS,
  TUTOR_GROUP,
  TUTOR_SESSIONS,
} from './fixtures';
import {
  myScheduleHref,
  namesText,
  sessionStateText,
  showsStudentNames,
} from './nonStaff';

// #246 B1, B3, B4, B6 (groups): the anyOf guard per role, the read-only
// pages of tutors and guardians (no create, edit, enroll or withdraw), the
// tutor's "Students" (`tutor_roster`) and nothing for families, the
// multi-child labels, the "My schedule" wording of the states and the links
// to "My schedule".

const ZONE = 'America/Mexico_City';
const LIST = 'GET /api/v1/groups';
const DETAIL = `GET /api/v1/groups/${GROUP_ID}`;
const SESSIONS_ROUTE = `GET /api/v1/groups/${GROUP_ID}/sessions`;

const user = (
  roles: SessionUserBody['roles'],
  permissions: SessionUserBody['permissions'],
): SessionUserBody => ({ ...TEST_USER, roles, permissions });

const TUTOR = user(['tutor'], ['student.view', 'tutor.view']);
const GUARDIAN = user(['guardian'], ['student.view', 'guardian.view']);
const STUDENT = user(['student'], ['student.view']);
const FINANCE = user(['finance_staff'], ['schedule.view']);

let app: RenderedApp | undefined;
const main = () => app!.container.querySelector('main')!;

function stub(
  as: SessionUserBody,
  routes: Record<string, Responder | Responder[]> = {},
): ApiStub {
  return stubApi({
    'GET /api/v1/auth/me': meAs(as),
    [LIST]: () =>
      jsonResponse(200, { items: STUDENT_LIST_ITEMS, next_cursor: null }),
    [DETAIL]: () => jsonResponse(200, FAMILY_GROUP),
    [SESSIONS_ROUTE]: () => jsonResponse(200, { items: FAMILY_SESSIONS }),
    ...routes,
  });
}

/** Waits on the page's heading and its sessions, never a fixed delay. */
async function open(path: string, text?: string): Promise<RenderedApp> {
  const rendered = await renderApp(path);
  await vi.waitFor(
    () => {
      const heading = rendered.container.querySelector('main h1');
      expect(heading).not.toBeNull();
      if (text) {
        expect(rendered.container.querySelector('main')?.textContent).toContain(
          text,
        );
      }
    },
    { timeout: 3000, interval: 20 },
  );
  return rendered;
}

function navLabels(): string[] {
  return [...app!.container.querySelectorAll('header nav a')].map(
    (a) => a.textContent ?? '',
  );
}

beforeEach(() => {
  applyLanguage('en');
});

afterEach(() => {
  app?.unmount();
  app = undefined;
  vi.unstubAllGlobals();
  document.body.innerHTML = '';
});

// --- Pure rules ----------------------------------------------------------------

describe('the non-staff texts (B3, B4)', () => {
  it('links a session to its local day in My schedule, List view', () => {
    // 05:30 UTC is still the previous day in Mexico City.
    expect(myScheduleHref('2031-01-05T05:30:00Z', ZONE)).toBe(
      '/my-schedule?date=2031-01-04&view=list',
    );
    expect(myScheduleHref('2031-01-04T16:00:00Z', ZONE)).toBe(
      '/my-schedule?date=2031-01-04&view=list',
    );
  });

  it('uses the My schedule wording for families and tutors only', () => {
    const t = i18n.getFixedT('en', 'common');
    expect(sessionStateText('skipped_closure', t, false)).toBe(
      'Not held: center closed',
    );
    expect(sessionStateText('cancelled', t, false)).toBe('Cancelled');
    expect(sessionStateText('offered', t, false)).toBe('Awaiting your answer');
    expect(sessionStateText('offered', t, true)).toBe(
      t('scheduling.states.offered'),
    );
    expect(sessionStateText('conflict', t, false)).toBe(
      t('scheduling.states.conflict'),
    );
  });

  it('names the students only when the list has more than one', () => {
    expect(showsStudentNames(FAMILY_SESSIONS)).toBe(true);
    expect(showsStudentNames(ONE_CHILD_SESSIONS)).toBe(false);
    expect(showsStudentNames(TUTOR_SESSIONS)).toBe(false);
    expect(namesText(FAMILY_SESSIONS[1]!.students)).toBe(
      'Ana López, Carla Méndez',
    );
    expect(namesText(undefined)).toBe('');
  });
});

// --- Access (B1) ----------------------------------------------------------------

describe('the groups guard per role (B1)', () => {
  it.each([
    ['tutor', TUTOR],
    ['guardian', GUARDIAN],
    ['student', STUDENT],
  ] as const)('opens /groups and its detail to a %s', async (_role, as) => {
    const api = stub(as);
    app = await open('/groups');
    expect(navLabels()).toContain('Groups');
    expect(main().querySelector('h1')?.textContent).toBe('My groups');
    expect(main().querySelector('a[href="/groups/new"]')).toBeNull();
    expect(api.calls(LIST)).toHaveLength(1);
    app.unmount();
    app = await open(`/groups/${GROUP_ID}`, 'My sessions');
    expect(api.calls(DETAIL)).toHaveLength(1);
  });

  it.each([
    ['tutor', TUTOR],
    ['guardian', GUARDIAN],
  ] as const)('keeps /groups/new Forbidden for a %s', async (_role, as) => {
    const api = stub(as);
    app = await open('/groups/new');
    expect(main().querySelector('h1')?.textContent).toBe(
      "You don't have access to this page",
    );
    expect(api.calls(LIST)).toHaveLength(0);
  });
});

// --- The tutor's page (B1, B4) ----------------------------------------------------

describe("the group's tutor (B1)", () => {
  it('shows "Students" by name and the sessions with their students, read-only', async () => {
    stub(TUTOR, {
      [DETAIL]: () => jsonResponse(200, TUTOR_GROUP),
      [SESSIONS_ROUTE]: () => jsonResponse(200, { items: TUTOR_SESSIONS }),
    });
    app = await open(`/groups/${GROUP_ID}`, 'Awaiting your answer');

    const students = [...main().querySelectorAll('h2')].find(
      (h) => h.textContent === 'Students',
    );
    expect(students).toBeDefined();
    const list = main().querySelector(`ul[aria-labelledby="${students!.id}"]`);
    expect(
      [...list!.querySelectorAll('li')].map((li) => li.textContent),
    ).toEqual(['Ana López', 'Bruno Díaz']);
    const text = main().textContent ?? '';
    expect(text).toContain('Students: Ana López, Bruno Díaz');
    expect(text).not.toContain('seats taken');
    expect(text).not.toContain('Withdraw');
    // Only the window buttons: no enroll, edit, withdraw or offer control.
    expect(
      [...main().querySelectorAll('button')].map((b) => b.textContent),
    ).toEqual(['Previous 4 weeks', 'Next 4 weeks']);
    expect(main().querySelector('a[href^="/schedule/sessions/"]')).toBeNull();
    expect(
      main().querySelector('a[href="/my-schedule?date=2031-01-04&view=list"]'),
    ).not.toBeNull();
  });
});

// --- A guardian's page (B1, B3, B4) -------------------------------------------------

describe('a guardian (B1, B3, B4)', () => {
  it('labels whose session it is with two children, and never shows a roster', async () => {
    stub(GUARDIAN);
    app = await open(`/groups/${GROUP_ID}`, 'Not held: center closed');

    const items = [...main().querySelectorAll('li')];
    expect(items).toHaveLength(3);
    expect(items[0]!.textContent).toContain('For: Ana López');
    expect(items[1]!.textContent).toContain('For: Ana López, Carla Méndez');
    expect(items[1]!.textContent).toContain('Cancelled');
    expect(items[2]!.textContent).toContain('For: Carla Méndez');
    expect(items[2]!.textContent).toContain('Not held: center closed');
    expect(items[0]!.textContent).toContain('Tutor: Carmen Ruiz');
    const headings = [...main().querySelectorAll('h2')].map(
      (h) => h.textContent,
    );
    expect(headings).not.toContain('Students');
    expect(headings).not.toContain('Next sessions');
    const links = [...main().querySelectorAll('a')].map((a) =>
      a.getAttribute('href'),
    );
    expect(links).toEqual([
      '/my-schedule?date=2031-01-04&view=list',
      '/my-schedule?date=2031-01-11&view=list',
      '/my-schedule?date=2031-01-18&view=list',
    ]);
  });

  it('shows no "For" label with one child in the list', async () => {
    stub(GUARDIAN, {
      [SESSIONS_ROUTE]: () => jsonResponse(200, { items: ONE_CHILD_SESSIONS }),
    });
    app = await open(`/groups/${GROUP_ID}`, 'Cancelled');

    expect(main().textContent).not.toContain('For:');
  });

  it('keeps the staff view for Finance (schedule.view)', async () => {
    stub(FINANCE, {
      [DETAIL]: () => jsonResponse(200, { ...TUTOR_GROUP, roster: [] }),
    });
    app = await open(`/groups/${GROUP_ID}`, 'Next sessions');

    // The staff roster (its own "Students"), never the tutor's list.
    const text = main().textContent ?? '';
    expect(text).toContain('No students enrolled.');
    expect(text).not.toContain('No students enrolled yet.');
    expect(text).not.toContain('Bruno Díaz');
  });
});
