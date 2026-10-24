import { act } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { SessionUserBody } from '@/features/auth/session';
import { PICKER_DEBOUNCE_MS } from '@/features/guardians/SearchPicker';
import { conflictsByDate } from '@/features/scheduling/session/rules';
import {
  EMPTY_RECURRENCE,
  type RecurrenceValues,
} from '@/features/scheduling/series/recurrence';
import { applyLanguage } from '@/i18n';
import {
  meAs,
  problem,
  stubApi,
  TEST_USER,
  type ApiStub,
  type Responder,
} from '@/test/api-stub';
import {
  click,
  fieldByLabel,
  openDialog,
  pressEscape,
  typeInto,
} from '@/test/dom';
import {
  buttonNamed,
  jsonResponse,
  renderApp,
  type RenderedApp,
  settle,
  settleUntil,
} from '@/test/render-app';
import {
  ACTIVE_ANA,
  ACTIVE_BRUNO,
  CAPACITY_CONFLICT,
  CLOSED_SUNDAY,
  CLOSED_SUNDAY_LATER,
  CREATE_ROOM_CONFLICT,
  CREATE_SOFT_CONFLICTS,
  CREATED_GROUP,
  ENDED_GROUP,
  ENROLL_OVERLAP_CONFLICT,
  ENROLL_ROOM_CONFLICT,
  ENROLLED,
  GROUP,
  GROUP_ID,
  LEAKY_STUDENT_GROUP,
  LIST_ITEMS,
  ROOM_ID,
  ROOM_LIMITED_GROUP,
  SESSIONS,
  STUDENT_CARLA,
  STUDENT_LIST_ITEMS,
  SUBJECT_ID,
  UNACCEPTED_GROUP_CONFLICT,
  WITHDRAWN_CARLA,
  WITHDRAWN_CARLA_EARLIER,
} from './fixtures';
import {
  createAccepts,
  createServerErrors,
  EMPTY_GROUP,
  enrollAccepts,
  filterParams,
  groupActions,
  groupCreateBody,
  groupCreateOverride,
  groupOverrideAction,
  groupView,
  hasChanges,
  hasFilters,
  listFilters,
  listItemEnded,
  rosterParts,
  sessionsWindow,
  shiftWindow,
  updateBody,
  validateGroupCreate,
  withdrawBody,
  type GroupCreateValues,
} from './rules';

// #234 G1: access (anyRole/anyOf is in access.test.ts), the nav for all
// seven roles, the view choice, the list's URL state, the create's client
// rules and body (never `rrule`), which create conflicts can be overridden
// (E3), the date grouping of the 409s, the action visibility, the edit's
// changed-fields PATCH, the blank withdrawal reason, the student's window
// with a foreign host zone (Pacific/Kiritimati, vite.config.ts) and a
// Tijuana DST date; then the pages: the list, the group page in both
// views (never the roster or next_occurrences for a student), the three
// dialogs and the create form.

const ZONE = 'America/Mexico_City';
const TIJUANA = 'America/Tijuana';
const LIST = 'GET /api/v1/groups';
const DETAIL = `GET /api/v1/groups/${GROUP_ID}`;
const SESSIONS_ROUTE = `GET /api/v1/groups/${GROUP_ID}/sessions`;
const ENROLL = `POST /api/v1/groups/${GROUP_ID}/enrollments`;
const WITHDRAW = `POST /api/v1/groups/${GROUP_ID}/enrollments/${ACTIVE_ANA.id}/withdraw`;
const PATCH = `PATCH /api/v1/groups/${GROUP_ID}`;
const CREATE = 'POST /api/v1/groups';

const user = (
  roles: SessionUserBody['roles'],
  permissions: SessionUserBody['permissions'],
): SessionUserBody => ({ ...TEST_USER, roles, permissions });

const OWNER = user(
  ['owner'],
  ['schedule.view', 'schedule.manage', 'schedule.override', 'student.view'],
);
const ADMIN = user(
  ['administrator'],
  ['schedule.view', 'schedule.manage', 'student.view'],
);
const COORDINATOR = user(['academic_coordinator'], ['schedule.view']);
const FINANCE = user(['finance_staff'], ['schedule.view']);
const TUTOR = user(['tutor'], []);
const GUARDIAN = user(['guardian'], []);
const STUDENT = user(['student'], ['student.view']);

let app: RenderedApp | undefined;
const main = () => app!.container.querySelector('main')!;

function page<T>(items: T[]): Responder {
  return () =>
    jsonResponse(200, { items, total: items.length, limit: 100, offset: 0 });
}

function stub(
  as: SessionUserBody = OWNER,
  routes: Record<string, Responder | Responder[]> = {},
): ApiStub {
  return stubApi({
    'GET /api/v1/auth/me': meAs(as),
    [LIST]: () => jsonResponse(200, { items: LIST_ITEMS, next_cursor: null }),
    [DETAIL]: () => jsonResponse(200, GROUP),
    [SESSIONS_ROUTE]: () => jsonResponse(200, { items: SESSIONS }),
    'GET /api/v1/subjects': page([
      {
        id: SUBJECT_ID,
        name: 'Álgebra',
        education_level_id: '00000000-0000-4000-8000-0000000004f1',
        education_level_name: 'Secundaria',
        is_active: true,
        version: 1,
        created_at: '2026-01-01T00:00:00Z',
        updated_at: '2026-01-01T00:00:00Z',
      },
    ]),
    'GET /api/v1/tutors': page([]),
    'GET /api/v1/classrooms': page([
      {
        id: ROOM_ID,
        name: 'Aula 1',
        capacity: 10,
        is_active: true,
        availability: [],
        version: 1,
        created_at: '2026-01-01T00:00:00Z',
        updated_at: '2026-01-01T00:00:00Z',
      },
    ]),
    'GET /api/v1/students': page([
      {
        id: ACTIVE_ANA.student.id,
        given_names: 'Ana',
        family_names: 'López',
        is_active: true,
        is_minor: true,
        school_grade: null,
        education_level: null,
      },
      {
        id: STUDENT_CARLA.id,
        given_names: 'Carla',
        family_names: 'Méndez',
        is_active: true,
        is_minor: true,
        school_grade: null,
        education_level: null,
      },
    ]),
    ...routes,
  });
}

function conflictResponse(conflicts: unknown[]): Response {
  return new Response(
    JSON.stringify({
      type: 'about:blank',
      title: 'Conflict',
      status: 409,
      code: 'scheduling_conflict',
      request_id: 'req-409',
      detail: 'Server detail that must never reach the UI',
      conflicts,
    }),
    { status: 409, headers: { 'Content-Type': 'application/problem+json' } },
  );
}

function invalid(errors: { path: unknown[]; code: string }[]): Response {
  return new Response(
    JSON.stringify({
      type: 'about:blank',
      title: 'Unprocessable Content',
      status: 422,
      code: 'validation_error',
      request_id: 'req-422',
      errors: errors.map((e) => ({ ...e, message: 'x' })),
    }),
    { status: 422, headers: { 'Content-Type': 'application/problem+json' } },
  );
}

/** The lazy routes need a few more turns to load and render. */
async function open(path: string): Promise<RenderedApp> {
  const rendered = await renderApp(path);
  await settleUntil(() => rendered.container.querySelector('main h1'));
  await settle();
  return rendered;
}

function topDialog(): HTMLElement {
  const all = document.querySelectorAll<HTMLElement>('[role="dialog"]');
  return all[all.length - 1]!;
}

function typeArea(root: ParentNode, label: string, value: string) {
  const found = [...root.querySelectorAll('label')].find(
    (l) => l.textContent === label,
  )!;
  const area = document.getElementById(found.htmlFor) as HTMLTextAreaElement;
  // eslint-disable-next-line @typescript-eslint/unbound-method
  const setter = Object.getOwnPropertyDescriptor(
    HTMLTextAreaElement.prototype,
    'value',
  )?.set;
  act(() => {
    setter?.call(area, value);
    area.dispatchEvent(new Event('input', { bubbles: true }));
  });
}

function choose(root: ParentNode, label: string, value: string) {
  const select = [...root.querySelectorAll('label')]
    .filter((l) => l.textContent === label)
    .map((l) => document.getElementById(l.htmlFor))[0] as HTMLSelectElement;
  act(() => {
    select.value = value;
    select.dispatchEvent(new Event('change', { bubbles: true }));
  });
}

async function search(root: ParentNode, label: string, text: string) {
  typeInto(fieldByLabel(root, label), text);
  await act(async () => {
    await new Promise((resolve) =>
      setTimeout(resolve, PICKER_DEBOUNCE_MS + 30),
    );
  });
  await settle();
}

const navLabels = () =>
  [...app!.container.querySelectorAll('header nav a')].map(
    (a) => a.textContent,
  );

beforeEach(() => {
  applyLanguage('en');
});

afterEach(() => {
  app?.unmount();
  app = undefined;
  vi.unstubAllGlobals();
  vi.useRealTimers();
  document.body.innerHTML = '';
});

// --- Pure rules ----------------------------------------------------------------

describe('the list URL state (B1)', () => {
  it('parses the filters with their defaults', () => {
    expect(listFilters(new URLSearchParams(''))).toEqual({
      q: '',
      subject: '',
      status: 'active',
    });
    expect(
      listFilters(
        new URLSearchParams(`q=  álg  &subject=${SUBJECT_ID}&status=ended`),
      ),
    ).toEqual({ q: 'álg', subject: SUBJECT_ID, status: 'ended' });
    expect(listFilters(new URLSearchParams('status=bogus')).status).toBe(
      'active',
    );
    expect(
      listFilters(new URLSearchParams(`q=${'x'.repeat(200)}`)).q,
    ).toHaveLength(120);
  });

  it('writes only the non-default filters, never a cursor', () => {
    expect(
      filterParams({ q: '', subject: '', status: 'active' }).toString(),
    ).toBe('');
    expect(
      filterParams({ q: 'a b', subject: 's1', status: 'all' }).toString(),
    ).toBe('q=a+b&subject=s1&status=all');
    expect(hasFilters({ q: '', subject: '', status: 'active' })).toBe(false);
    expect(hasFilters({ q: '', subject: '', status: 'ended' })).toBe(true);
  });

  it('shows "Ended" only when the filter can list ended groups', () => {
    const ended = { next_starts_at: null };
    expect(listItemEnded(ended, 'active')).toBe(false);
    expect(listItemEnded(ended, 'ended')).toBe(true);
    expect(listItemEnded(ended, 'all')).toBe(true);
    expect(
      listItemEnded({ next_starts_at: '2031-01-04T16:00:00Z' }, 'all'),
    ).toBe(false);
  });
});

describe('the view and the actions (A4, C7)', () => {
  it('is the staff view for schedule.view, a staff+student user included', () => {
    expect(groupView({ ...FINANCE, roles: ['finance_staff'] })).toBe('staff');
    expect(groupView({ ...OWNER, roles: ['owner', 'student'] })).toBe('staff');
    expect(groupView(STUDENT)).toBe('student');
    expect(groupView(null)).toBe('student');
  });

  it('shows the actions to schedule.manage only, and no enroll once ended', () => {
    expect(groupActions(GROUP, true)).toEqual({
      enroll: true,
      withdraw: true,
      edit: true,
    });
    expect(groupActions(GROUP, false)).toEqual({
      enroll: false,
      withdraw: false,
      edit: false,
    });
    expect(groupActions(ENDED_GROUP, true).enroll).toBe(false);
  });

  it('splits the roster: active as listed, withdrawn newest first', () => {
    const parts = rosterParts(GROUP.roster!);
    expect(parts.active).toEqual([ACTIVE_ANA, ACTIVE_BRUNO]);
    expect(parts.withdrawn).toEqual([WITHDRAWN_CARLA, WITHDRAWN_CARLA_EARLIER]);
  });
});

describe('the edit and withdraw bodies (D3, D4)', () => {
  it('sends only the changed fields; nothing changed disables the submit', () => {
    expect(hasChanges(GROUP, { name: ` ${GROUP.name} `, max: '6' })).toBe(
      false,
    );
    expect(updateBody(GROUP, { name: GROUP.name, max: '8' })).toEqual({
      version: GROUP.version,
      max_students: 8,
    });
    expect(updateBody(GROUP, { name: ' Álgebra B ', max: '6' })).toEqual({
      version: GROUP.version,
      name: 'Álgebra B',
    });
  });

  it('sends a blank withdrawal reason as no reason', () => {
    expect(withdrawBody(3, '   \n ')).toEqual({ version: 3 });
    expect(withdrawBody(3, '  Se mudó ')).toEqual({
      version: 3,
      reason: 'Se mudó',
    });
  });
});

describe("the student's sessions window (C4)", () => {
  it('runs 4 weeks from local midnight in the center zone, whatever the host zone', () => {
    // The test host zone is Pacific/Kiritimati (UTC+14).
    expect(sessionsWindow('2031-01-04', ZONE)).toEqual({
      from: '2031-01-04T06:00:00.000Z',
      to: '2031-02-01T06:00:00.000Z',
    });
    expect(shiftWindow('2031-01-04', 1)).toBe('2031-02-01');
    expect(shiftWindow('2031-01-04', -1)).toBe('2030-12-07');
  });

  it('crosses the Tijuana DST change at local midnight, within 45 days', () => {
    const range = sessionsWindow('2026-03-01', TIJUANA);
    expect(range).toEqual({
      from: '2026-03-01T08:00:00.000Z',
      to: '2026-03-29T07:00:00.000Z',
    });
    const days =
      (Date.parse(range.to) - Date.parse(range.from)) / (24 * 3600 * 1000);
    expect(days).toBeLessThanOrEqual(45);
  });
});

const VALUES: GroupCreateValues = {
  ...EMPTY_GROUP,
  name: '  Álgebra sábado ',
  subjectId: SUBJECT_ID,
  max: '2',
  startsOn: '2031-01-04',
  start: '10:45',
  end: '12:15',
  students: [
    { id: ACTIVE_ANA.student.id, name: 'Ana López' },
    { id: STUDENT_CARLA.id, name: 'Carla Méndez' },
  ],
};
const WEEKLY: RecurrenceValues = {
  ...EMPTY_RECURRENCE,
  weekdays: [6],
  ends: 'count',
  count: '4',
};

describe('the create (E2)', () => {
  it('builds the body from structured fields, never rrule text', () => {
    const body = groupCreateBody(VALUES, WEEKLY);
    expect(body).toEqual({
      name: 'Álgebra sábado',
      subject_id: SUBJECT_ID,
      max_students: 2,
      tutor_user_id: null,
      classroom_id: null,
      starts_on: '2031-01-04',
      start_time: '10:45',
      duration_minutes: 90,
      recurrence: { weekdays: [6], interval: 1, until: null, count: 4 },
      student_ids: [ACTIVE_ANA.student.id, STUDENT_CARLA.id],
    });
    expect(JSON.stringify(body)).not.toMatch(/rrule|FREQ=/i);
  });

  it('checks the required fields, the bounds, the times and the rule', () => {
    const empty = validateGroupCreate(EMPTY_GROUP, EMPTY_RECURRENCE);
    expect(Object.keys(empty.errors).sort()).toEqual(
      ['end', 'max', 'name', 'start', 'startsOn', 'subject'].sort(),
    );
    expect(empty.recurrence.weekdays).toBe(
      'scheduling.series.validation.weekdaysRequired',
    );
    const bad = validateGroupCreate(
      { ...VALUES, max: '501', end: '10:45' },
      { ...WEEKLY, ends: 'until', until: '2031-01-03' },
    );
    expect(bad.errors).toEqual({
      max: 'groups.validation.maxRange',
      end: 'scheduling.validation.endAfterStart',
    });
    expect(bad.recurrence.until).toBe(
      'scheduling.series.validation.untilBeforeStart',
    );
    expect(validateGroupCreate(VALUES, WEEKLY)).toEqual({
      errors: {},
      recurrence: {},
    });
  });

  it('puts a student 422 on its chip and group_name_taken on the name', () => {
    const found = createServerErrors({
      code: 'validation_error',
      fieldErrors: [
        {
          path: ['body', 'student_ids', 1],
          code: 'student_inactive',
          message: '',
        },
        { path: ['body', 'starts_on'], code: 'starts_on_in_past', message: '' },
        {
          path: ['body', 'recurrence', 'count'],
          code: 'count_out_of_range',
          message: '',
        },
      ],
    });
    expect(found.students).toEqual({ 1: 'errors:student_inactive' });
    expect(found.fields).toEqual({ startsOn: 'errors:starts_on_in_past' });
    expect(found.recurrence).toEqual({ count: 'errors:count_out_of_range' });
    expect(
      createServerErrors({ code: 'group_name_taken', fieldErrors: [] }).fields,
    ).toEqual({ name: 'errors:group_name_taken' });
    expect(
      createServerErrors({
        code: 'validation_error',
        fieldErrors: [
          {
            path: ['body', 'student_ids'],
            code: 'student_ids_duplicate',
            message: '',
          },
        ],
      }).fields,
    ).toEqual({ students: 'errors:student_ids_duplicate' });
  });
});

describe('overriding a group write (E3, D2)', () => {
  it('offers the create override only for accepted soft codes, to holders, without a hard one', () => {
    expect(
      groupOverrideAction(CREATE_SOFT_CONFLICTS, true, createAccepts),
    ).toBe('override');
    expect(
      groupOverrideAction(CREATE_SOFT_CONFLICTS, false, createAccepts),
    ).toBe('notAllowed');
    expect(
      groupOverrideAction(
        [...CREATE_SOFT_CONFLICTS, CREATE_ROOM_CONFLICT],
        true,
        createAccepts,
      ),
    ).toBe('fixHardFirst');
    expect(
      groupOverrideAction(
        [CAPACITY_CONFLICT, UNACCEPTED_GROUP_CONFLICT],
        true,
        createAccepts,
      ),
    ).toBe('notYet');
    expect(createAccepts(UNACCEPTED_GROUP_CONFLICT)).toBe(false);
    expect(createAccepts(CLOSED_SUNDAY)).toBe(true);
    expect(createAccepts(CREATE_ROOM_CONFLICT)).toBe(false);
    expect(enrollAccepts(CAPACITY_CONFLICT)).toBe(true);
    expect(enrollAccepts(ENROLL_ROOM_CONFLICT)).toBe(false);
  });

  it('sends codes for the group level and pairs for the occurrences', () => {
    expect(
      groupCreateOverride(CREATE_SOFT_CONFLICTS, '  Grupo grande '),
    ).toEqual({
      codes: ['group_capacity_exceeded'],
      pairs: [
        {
          occurrence_start: '2031-01-05T16:00:00Z',
          code: 'outside_operating_hours',
        },
        {
          occurrence_start: '2031-01-12T16:00:00Z',
          code: 'outside_operating_hours',
        },
      ],
      reason: 'Grupo grande',
    });
    expect(groupCreateOverride([CAPACITY_CONFLICT], 'x')).toEqual({
      codes: ['group_capacity_exceeded'],
      reason: 'x',
    });
    expect(groupCreateOverride([CLOSED_SUNDAY], 'x')).toEqual({
      pairs: [
        {
          occurrence_start: '2031-01-05T16:00:00Z',
          code: 'outside_operating_hours',
        },
      ],
      reason: 'x',
    });
  });

  it('groups the 409s: the whole group first, then each local date (both room shapes)', () => {
    expect(
      conflictsByDate([
        ENROLL_OVERLAP_CONFLICT,
        ENROLL_ROOM_CONFLICT,
        CAPACITY_CONFLICT,
      ]).map((g) => [g.date, g.conflicts.map((c) => c.code)]),
    ).toEqual([
      ['', ['group_capacity_exceeded']],
      ['2031-01-04', ['room_capacity_exceeded']],
      ['2031-01-11', ['student_overlap']],
    ]);
    expect(
      conflictsByDate([
        CLOSED_SUNDAY_LATER,
        CREATE_ROOM_CONFLICT,
        CAPACITY_CONFLICT,
        CLOSED_SUNDAY,
      ]).map((g) => g.date),
    ).toEqual(['', '2031-01-05', '2031-01-11', '2031-01-12']);
  });
});

// --- Pages ---------------------------------------------------------------------

describe('navigation and access (A2, A3)', () => {
  it.each([
    ['owner', OWNER, true],
    ['administrator', ADMIN, true],
    ['academic_coordinator', COORDINATOR, true],
    ['finance_staff', FINANCE, true],
    ['student', STUDENT, true],
    // #246 B1: tutors and guardians too.
    ['tutor', TUTOR, true],
    ['guardian', GUARDIAN, true],
  ] as const)('shows "Groups" to a %s: %s', async (_role, as, shown) => {
    stub(as);
    app = await renderApp('/');
    expect(navLabels().includes('Groups')).toBe(shown);
  });

  it.each([
    ['tutor', TUTOR, '/groups/new'],
    ['guardian', GUARDIAN, '/groups/new'],
    ['academic_coordinator', COORDINATOR, '/groups/new'],
    ['finance_staff', FINANCE, '/groups/new'],
    ['student', STUDENT, '/groups/new'],
  ] as const)(
    'gives a %s the Forbidden page on %s',
    async (_role, as, path) => {
      const api = stub(as);
      app = await open(path);
      // Wait on the page, not a fixed number of turns (flaked under load).
      await vi.waitFor(
        () =>
          expect(main().querySelector('h1')?.textContent).toBe(
            "You don't have access to this page",
          ),
        { timeout: 3000, interval: 20 },
      );
      expect(api.calls(LIST)).toHaveLength(0);
      expect(api.calls(DETAIL)).toHaveLength(0);
    },
  );
});

describe('/groups (B)', () => {
  it('lists the groups for staff with seats, "Ended" and "New group"', async () => {
    const api = stub(OWNER);
    app = await open('/groups?status=all');
    expect(main().querySelector('h1')?.textContent).toBe('Groups');
    const text = main().textContent ?? '';
    expect(text).toContain('Álgebra sábado');
    expect(text).toContain('Álgebra (Secundaria)');
    expect(text).toContain('2 / 6');
    expect(text).toContain('No tutor yet');
    expect(text).toContain('Ended');
    expect(main().querySelector('a[href="/groups/new"]')).not.toBeNull();
    const url = api.fetch.mock.calls
      .map(([request]) => new URL(request.url))
      .filter((u) => u.pathname === '/api/v1/groups')
      .at(-1)!;
    expect(url.searchParams.get('status')).toBe('all');
    expect(url.searchParams.get('limit')).toBe('25');
  });

  it('is read-only for Finance, and shows the student only their groups', async () => {
    stub(FINANCE);
    app = await open('/groups');
    expect(main().querySelector('a[href="/groups/new"]')).toBeNull();
    app.unmount();

    stub(STUDENT, {
      [LIST]: () =>
        jsonResponse(200, { items: STUDENT_LIST_ITEMS, next_cursor: null }),
    });
    app = await open('/groups');
    expect(main().querySelector('h1')?.textContent).toBe('My groups');
    const headers = [...main().querySelectorAll('th')].map(
      (th) => th.textContent,
    );
    expect(headers).not.toContain('Seats');
    expect(main().querySelectorAll('select')).toHaveLength(0);
    expect(main().querySelector('a[href="/groups/new"]')).toBeNull();
  });

  it('says a student has no group, and offers "Clear filters" to staff', async () => {
    stub(STUDENT, {
      [LIST]: () => jsonResponse(200, { items: [], next_cursor: null }),
    });
    app = await open('/groups');
    expect(main().textContent).toContain("You aren't enrolled in any group.");
    app.unmount();

    stub(OWNER, {
      [LIST]: () => jsonResponse(200, { items: [], next_cursor: null }),
    });
    app = await open('/groups?status=ended');
    expect(main().textContent).toContain('No groups match these filters.');
    await click(buttonNamed(main(), 'Clear filters'));
    expect(app.location()).toBe('/groups');
  });

  it('loads the next page by cursor; a filter change starts again', async () => {
    const api = stub(OWNER, {
      [LIST]: (request) => {
        const cursor = new URL(request.url).searchParams.get('cursor');
        return jsonResponse(
          200,
          cursor
            ? { items: [LIST_ITEMS[1]], next_cursor: null }
            : { items: [LIST_ITEMS[0]], next_cursor: 'c1' },
        );
      },
    });
    app = await open('/groups');
    await click(buttonNamed(main(), 'Load more'));
    expect(main().textContent).toContain('Física verano');
    expect(main().textContent).not.toContain('Load more');
    choose(main(), 'Status', 'ended');
    await settle();
    expect(app.location()).toBe('/groups?status=ended');
    const last = api.fetch.mock.calls
      .map(([request]) => new URL(request.url))
      .filter((u) => u.pathname === '/api/v1/groups')
      .at(-1)!;
    expect(last.searchParams.get('cursor')).toBeNull();
    expect(last.searchParams.get('status')).toBe('ended');
  });
});

describe('/groups/:id staff view (C)', () => {
  it('shows the header, capacity, next sessions, roster and actions', async () => {
    stub(OWNER);
    app = await open(`/groups/${GROUP_ID}`);
    const text = main().textContent ?? '';
    expect(main().querySelector('h1')?.textContent).toBe('Álgebra sábado');
    expect(text).toContain('Every week on Saturday');
    expect(text).toContain('10:00 AM–11:30 AM (America/Mexico_City)');
    expect(text).toContain('2 of 6 seats taken');
    expect(text).not.toContain('The room holds');
    expect(text).toContain('Confirmed');
    expect(
      main().querySelector(
        'a[href="/schedule/sessions/00000000-0000-4000-8000-000000000d01"]',
      )?.textContent,
    ).toBe('January 4, 2031 · 10:00 AM–11:30 AM');
    expect(text).toContain('Overridden');
    expect(text).toContain('Inactive');
    expect(text).toContain('Se mudó de ciudad.');
    expect(text).toContain('No reason given');
    expect(buttonNamed(main(), 'Enroll student')).toBeTruthy();
    expect(buttonNamed(main(), 'Edit')).toBeTruthy();
    expect(buttonNamed(main(), 'Withdraw Ana López')).toBeTruthy();
  });

  it('is read-only for a Coordinator, and has no enroll once ended', async () => {
    stub(COORDINATOR);
    app = await open(`/groups/${GROUP_ID}`);
    expect(main().textContent).toContain('Ana López');
    expect(main().querySelectorAll('button')).toHaveLength(0);
    app.unmount();

    stub(OWNER, { [DETAIL]: () => jsonResponse(200, ENDED_GROUP) });
    app = await open(`/groups/${GROUP_ID}`);
    expect(main().textContent).toContain('Ended');
    expect(main().textContent).toContain('No upcoming sessions');
    expect(() => buttonNamed(main(), 'Enroll student')).toThrow();
    expect(buttonNamed(main(), 'Edit')).toBeTruthy();
  });

  it('says what the room limits, and shows not-found for a 404', async () => {
    stub(OWNER, { [DETAIL]: () => jsonResponse(200, ROOM_LIMITED_GROUP) });
    app = await open(`/groups/${GROUP_ID}`);
    expect(main().textContent).toContain(
      'The room holds 4, so at most 4 can attend.',
    );
    app.unmount();

    stub(OWNER, { [DETAIL]: () => problem(404, 'group_not_found') });
    app = await open(`/groups/${GROUP_ID}`);
    expect(main().querySelector('h1')?.textContent).toBe('Page not found');
  });
});

describe('/groups/:id student view (C4)', () => {
  it('never renders the roster, the count or next_occurrences, even if sent', async () => {
    const api = stub(STUDENT, {
      [DETAIL]: () => jsonResponse(200, LEAKY_STUDENT_GROUP),
    });
    app = await open(`/groups/${GROUP_ID}`);
    const text = main().textContent ?? '';
    expect(text).toContain('My sessions');
    expect(text).not.toContain('Bruno Díaz');
    expect(text).not.toContain('seats taken');
    expect(text).not.toContain('Next sessions');
    expect(text).not.toContain('Offered');
    expect(main().querySelector('a[href^="/schedule/sessions/"]')).toBeNull();
    expect(main().querySelectorAll('button')).toHaveLength(2);
    expect(text).toContain('Tutor: Carmen Ruiz');
    expect(text).toContain('Classroom: Aula 1');
    expect(text).toContain('Completed');
    expect(api.calls(SESSIONS_ROUTE)).toHaveLength(1);
  });

  it('moves the 4-week window by local dates in the center zone', async () => {
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(new Date('2031-01-04T05:30:00Z')); // Jan 3 in Mexico City
    const api = stub(STUDENT, {
      [DETAIL]: () => jsonResponse(200, LEAKY_STUDENT_GROUP),
    });
    app = await open(`/groups/${GROUP_ID}`);
    const query = (index: number) => {
      const request = api.fetch.mock.calls
        .map(([r]) => new URL(r.url))
        .filter((u) => u.pathname.endsWith('/sessions'))
        .at(index)!;
      return [request.searchParams.get('from'), request.searchParams.get('to')];
    };
    expect(query(0)).toEqual([
      '2031-01-03T06:00:00.000Z',
      '2031-01-31T06:00:00.000Z',
    ]);
    await click(buttonNamed(main(), 'Next 4 weeks'));
    expect(query(1)).toEqual([
      '2031-01-31T06:00:00.000Z',
      '2031-02-28T06:00:00.000Z',
    ]);
    await click(buttonNamed(main(), 'Previous 4 weeks'));
    await click(buttonNamed(main(), 'Previous 4 weeks'));
    expect(query(-1)).toEqual([
      '2030-12-06T06:00:00.000Z',
      '2031-01-03T06:00:00.000Z',
    ]);
  });
});

describe('the dialogs (D)', () => {
  it('enrolls with an override after a soft-only 409', async () => {
    const api = stub(OWNER, {
      [ENROLL]: [
        () => conflictResponse([CAPACITY_CONFLICT]),
        () => jsonResponse(201, { ...ENROLLED, overridden: true }),
      ],
    });
    app = await open(`/groups/${GROUP_ID}`);
    const trigger = buttonNamed(main(), 'Enroll student');
    act(() => trigger.focus());
    await click(trigger);
    const dialog = openDialog()!;
    await search(dialog, 'Student', 'a');
    const ana = dialog.querySelector<HTMLInputElement>(
      `input[value="${ACTIVE_ANA.student.id}"]`,
    )!;
    expect(ana.disabled).toBe(true);
    expect(dialog.textContent).toContain('Ana López (already enrolled)');
    await click(
      dialog.querySelector<HTMLInputElement>(
        `input[value="${STUDENT_CARLA.id}"]`,
      )!,
    );
    await click(buttonNamed(dialog, 'Enroll'));
    const panel = dialog.querySelector('[role="alert"]')!;
    expect(panel.querySelector('h3')?.textContent).toBe('Whole group');
    expect(panel.textContent).toContain('Can be overriddenThe group is full.');
    await click(buttonNamed(dialog, 'Override and enroll'));
    typeArea(topDialog(), 'Reason', '  Hermana de Ana ');
    await click(buttonNamed(topDialog(), 'Override and enroll'));
    expect(api.calls(ENROLL).map((c) => c.body)).toEqual([
      { student_id: STUDENT_CARLA.id },
      {
        student_id: STUDENT_CARLA.id,
        override: {
          codes: ['group_capacity_exceeded'],
          reason: 'Hermana de Ana',
        },
      },
    ]);
    expect(openDialog()).toBeNull();
    expect(main().textContent).toContain(
      'Carla Méndez enrolled with an override.',
    );
    // QA D1: both dialogs closed; the focus is back on the trigger.
    expect(document.activeElement).toBe(trigger);
  });

  it('asks to fix a hard enroll conflict first, labelled "Not allowed"', async () => {
    stub(OWNER, {
      [ENROLL]: () =>
        conflictResponse([
          CAPACITY_CONFLICT,
          ENROLL_ROOM_CONFLICT,
          ENROLL_OVERLAP_CONFLICT,
        ]),
    });
    app = await open(`/groups/${GROUP_ID}`);
    await click(buttonNamed(main(), 'Enroll student'));
    const dialog = openDialog()!;
    await search(dialog, 'Student', 'c');
    await click(
      dialog.querySelector<HTMLInputElement>(
        `input[value="${STUDENT_CARLA.id}"]`,
      )!,
    );
    await click(buttonNamed(dialog, 'Enroll'));
    const panel = dialog.querySelector('[role="alert"]')!;
    expect([...panel.querySelectorAll('h3')].map((h) => h.textContent)).toEqual(
      ['Whole group', 'January 4, 2031', 'January 11, 2031'],
    );
    expect(panel.textContent).toContain('Not allowed');
    expect(dialog.textContent).toContain(
      'Fix the conflicts that aren’t allowed first.',
    );
    expect(() => buttonNamed(dialog, 'Override and enroll')).toThrow();
  });

  it('shows already_enrolled translated', async () => {
    stub(OWNER, { [ENROLL]: () => problem(409, 'already_enrolled') });
    app = await open(`/groups/${GROUP_ID}`);
    await click(buttonNamed(main(), 'Enroll student'));
    const dialog = openDialog()!;
    await search(dialog, 'Student', 'c');
    await click(
      dialog.querySelector<HTMLInputElement>(
        `input[value="${STUDENT_CARLA.id}"]`,
      )!,
    );
    await click(buttonNamed(dialog, 'Enroll'));
    expect(dialog.textContent).toContain('This student is already enrolled.');
  });

  it('withdraws with a blank reason as no reason', async () => {
    const api = stub(OWNER, {
      [WITHDRAW]: () =>
        jsonResponse(200, {
          ...ACTIVE_ANA,
          withdrawn_at: '2031-01-01T00:00:00Z',
          withdrawn_by: ACTIVE_ANA.enrolled_by,
          version: 2,
        }),
    });
    app = await open(`/groups/${GROUP_ID}`);
    await click(buttonNamed(main(), 'Withdraw Ana López'));
    const dialog = openDialog()!;
    expect(dialog.textContent).toContain(
      'Withdraw Ana López from Álgebra sábado? They are removed from future sessions; past sessions are kept.',
    );
    typeArea(dialog, 'Reason (optional)', '   ');
    await click(buttonNamed(dialog, 'Withdraw'));
    expect(api.calls(WITHDRAW).map((c) => c.body)).toEqual([
      { version: ACTIVE_ANA.version },
    ]);
    expect(openDialog()).toBeNull();
    expect(main().textContent).toContain('Ana López withdrawn.');
  });

  it('offers a reload after a withdrawal version conflict', async () => {
    stub(OWNER, { [WITHDRAW]: () => problem(409, 'version_conflict') });
    app = await open(`/groups/${GROUP_ID}`);
    await click(buttonNamed(main(), 'Withdraw Ana López'));
    await click(buttonNamed(openDialog()!, 'Withdraw'));
    expect(openDialog()!.textContent).toContain('Reload');
  });

  // QA D1: the opener is read when the dialog opens, so the search must
  // not take the focus first (`autoFocus`); keyboard users start on it.
  async function openEnrollFromKeyboard(): Promise<HTMLElement> {
    const trigger = buttonNamed(main(), 'Enroll student');
    act(() => trigger.focus());
    await click(trigger);
    const dialog = openDialog()!;
    expect(document.activeElement).toBe(fieldByLabel(dialog, 'Student'));
    return dialog;
  }

  it('puts the focus back on "Enroll student" after Escape and Cancel', async () => {
    stub(OWNER);
    app = await open(`/groups/${GROUP_ID}`);
    await openEnrollFromKeyboard();
    await pressEscape();
    expect(openDialog()).toBeNull();
    expect(document.activeElement).toBe(buttonNamed(main(), 'Enroll student'));

    const dialog = await openEnrollFromKeyboard();
    await search(dialog, 'Student', 'c');
    await click(buttonNamed(dialog, 'Cancel'));
    expect(openDialog()).toBeNull();
    expect(document.activeElement).toBe(buttonNamed(main(), 'Enroll student'));
  });

  it('puts the focus back on "Enroll student" after an enroll', async () => {
    stub(OWNER, { [ENROLL]: () => jsonResponse(201, ENROLLED) });
    app = await open(`/groups/${GROUP_ID}`);
    const dialog = await openEnrollFromKeyboard();
    await search(dialog, 'Student', 'c');
    await click(
      dialog.querySelector<HTMLInputElement>(
        `input[value="${STUDENT_CARLA.id}"]`,
      )!,
    );
    await click(buttonNamed(dialog, 'Enroll'));
    expect(openDialog()).toBeNull();
    expect(document.activeElement).toBe(buttonNamed(main(), 'Enroll student'));
  });

  it('moves the focus to the notice after a withdrawal, as the row moves', async () => {
    const withdrawn = {
      ...ACTIVE_ANA,
      withdrawn_at: '2031-01-01T00:00:00Z',
      withdrawn_by: ACTIVE_ANA.enrolled_by,
      version: 2,
    };
    // The refetch is held, so the row's button is still there when the
    // dialog closes (as in a browser), then the row moves.
    let release: () => void = () => {};
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    stub(OWNER, {
      [DETAIL]: [
        () => jsonResponse(200, GROUP),
        async () => {
          await held;
          return jsonResponse(200, {
            ...GROUP,
            roster: GROUP.roster!.map((item) =>
              item.id === ACTIVE_ANA.id ? withdrawn : item,
            ),
          });
        },
      ],
      [WITHDRAW]: () => jsonResponse(200, withdrawn),
    });
    app = await open(`/groups/${GROUP_ID}`);
    const trigger = buttonNamed(main(), 'Withdraw Ana López');
    act(() => trigger.focus());
    await click(trigger);
    await click(buttonNamed(openDialog()!, 'Withdraw'));
    expect(openDialog()).toBeNull();
    const notice = main().querySelector('[role="status"][tabindex="-1"]');
    expect(notice?.textContent).toBe('Ana López withdrawn.');
    expect(trigger.isConnected).toBe(true);
    expect(document.activeElement).toBe(notice);

    release();
    await settle();
    expect(() => buttonNamed(main(), 'Withdraw Ana López')).toThrow();
    expect(document.activeElement).toBe(notice);
  });

  it('edits only the changed maximum, and shows the below-enrolled error', async () => {
    const api = stub(OWNER, {
      [PATCH]: [
        () =>
          invalid([
            {
              path: ['body', 'max_students'],
              code: 'max_students_below_enrolled',
            },
          ]),
        () => jsonResponse(200, { ...GROUP, max_students: 3, version: 6 }),
      ],
    });
    app = await open(`/groups/${GROUP_ID}`);
    await click(buttonNamed(main(), 'Edit'));
    const dialog = openDialog()!;
    expect(buttonNamed(dialog, 'Save').disabled).toBe(true);
    typeInto(fieldByLabel(dialog, 'Maximum students'), '1');
    expect(buttonNamed(dialog, 'Save').disabled).toBe(false);
    await click(buttonNamed(dialog, 'Save'));
    expect(dialog.textContent).toContain(
      "2 students are enrolled: the maximum can't be lower.",
    );
    typeInto(fieldByLabel(dialog, 'Maximum students'), '3');
    await click(buttonNamed(dialog, 'Save'));
    expect(api.calls(PATCH).map((c) => c.body)).toEqual([
      { version: GROUP.version, max_students: 1 },
      { version: GROUP.version, max_students: 3 },
    ]);
    expect(openDialog()).toBeNull();
    expect(main().textContent).toContain('Group saved.');
  });
});

describe('/groups/new (E)', () => {
  async function fillForm() {
    typeInto(fieldByLabel(main(), 'Name'), 'Álgebra sábado');
    choose(main(), 'Subject', SUBJECT_ID);
    typeInto(fieldByLabel(main(), 'Maximum students'), '1');
    typeInto(fieldByLabel(main(), 'Starts on'), '2031-01-04');
    typeInto(fieldByLabel(main(), 'Start'), '10:00');
    typeInto(fieldByLabel(main(), 'End'), '11:00');
    await search(main(), 'Add a student', 'a');
    await click(
      main().querySelector<HTMLInputElement>(
        `input[value="${ACTIVE_ANA.student.id}"]`,
      )!,
    );
    await click(
      main().querySelector<HTMLInputElement>(
        `input[value="${STUDENT_CARLA.id}"]`,
      )!,
    );
  }

  it('creates with an override of the accepted soft codes, then opens the group', async () => {
    const api = stub(OWNER, {
      [CREATE]: [
        () => conflictResponse(CREATE_SOFT_CONFLICTS),
        () => jsonResponse(201, CREATED_GROUP),
      ],
    });
    app = await open('/groups/new');
    expect(main().textContent).toContain('Aula 1 (holds 10)');
    await fillForm();
    expect(main().textContent).toContain('Carla Méndez');
    // The start date's weekday is pre-checked (Saturday).
    expect(
      main().querySelector<HTMLInputElement>(
        'input[type="checkbox"][value="6"]',
      )!.checked,
    ).toBe(true);
    await click(buttonNamed(main(), 'Create group'));
    const panel = main().querySelector('[role="alert"]')!;
    expect([...panel.querySelectorAll('h3')].map((h) => h.textContent)).toEqual(
      ['Whole group', 'January 5, 2031', 'January 12, 2031'],
    );
    expect(document.activeElement).toBe(panel.querySelector('h2'));
    await click(buttonNamed(main(), 'Override and create'));
    typeArea(topDialog(), 'Reason', 'Grupo de repaso');
    await click(buttonNamed(topDialog(), 'Override and create'));
    const [first, second] = api
      .calls(CREATE)
      .map((c) => c.body as Record<string, unknown>);
    expect(first).not.toHaveProperty('override');
    expect(first).not.toHaveProperty('rrule');
    expect(first!.student_ids).toEqual([
      ACTIVE_ANA.student.id,
      STUDENT_CARLA.id,
    ]);
    expect(first!.duration_minutes).toBe(60);
    expect(second!.override).toEqual(
      groupCreateOverride(CREATE_SOFT_CONFLICTS, 'Grupo de repaso'),
    );
    await settleUntil(() => app!.location().startsWith('/groups/0'));
    expect(app.location()).toBe(`/groups/${GROUP_ID}`);
    expect(main().textContent).toContain('Group created: 2 upcoming sessions.');
  });

  it('labels a soft code the create does not accept, and keeps the form', async () => {
    stub(OWNER, {
      [CREATE]: () =>
        conflictResponse([CAPACITY_CONFLICT, UNACCEPTED_GROUP_CONFLICT]),
    });
    app = await open('/groups/new');
    await fillForm();
    await click(buttonNamed(main(), 'Create group'));
    expect(main().textContent).toContain("Can't be overridden for groups yet");
    expect(main().textContent).toContain(
      "Some of these conflicts can't be overridden for groups yet.",
    );
    expect(() => buttonNamed(main(), 'Override and create')).toThrow();
    expect(fieldByLabel(main(), 'Name').value).toBe('Álgebra sábado');
  });

  it('puts a student 422 on its chip and the name taken on the name', async () => {
    stub(OWNER, {
      [CREATE]: [
        () =>
          invalid([
            { path: ['body', 'student_ids', 1], code: 'student_inactive' },
          ]),
        () => problem(409, 'group_name_taken'),
      ],
    });
    app = await open('/groups/new');
    await fillForm();
    await click(buttonNamed(main(), 'Create group'));
    const chip = document.getElementById(
      buttonNamed(main(), 'Remove Carla Méndez').getAttribute(
        'aria-describedby',
      )!,
    );
    expect(chip?.textContent).toBe(
      'This student is inactive. Choose another one.',
    );
    expect(document.activeElement).toBe(
      buttonNamed(main(), 'Remove Carla Méndez'),
    );
    await click(buttonNamed(main(), 'Create group'));
    expect(main().textContent).toContain(
      'Another group already has this name.',
    );
    expect(document.activeElement).toBe(fieldByLabel(main(), 'Name'));
  });
});
