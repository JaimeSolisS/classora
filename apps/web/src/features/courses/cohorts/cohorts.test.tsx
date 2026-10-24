import { act } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { SessionUserBody } from '@/features/auth/session';
import { describeRecurrence } from '@/features/scheduling/series/describeRecurrence';
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
  settle,
  type RenderedApp,
} from '@/test/render-app';
import { ALGEBRA, PHYSICS_LAB, summaryOf } from '../fixtures';
import {
  COHORT,
  COHORT_ID,
  COHORT_LEVEL_CONFLICT,
  CREATED_COHORT,
  ENDED_COHORT,
  HARD_CONFLICT,
  LIST_ITEMS,
  PENDING_COHORT,
  PHYSICS_ACTIVE,
  SERIES_ID,
  SESSIONS,
  SOFT_CONFLICTS,
  TEMPLATE_OPTIONS,
  TUTORED_COHORT,
} from './fixtures';
import {
  assignmentOf,
  canOfferSeries,
  cohortCreateBody,
  cohortCreateOverride,
  cohortRule,
  createAccepts,
  createServerErrors,
  EMPTY_COHORT,
  filterParams,
  hasChanges,
  hasFilters,
  listFilters,
  offerFromDate,
  sessionsWindow,
  shiftWindow,
  templateSubjects,
  updateBody,
  validateCohortCreate,
  withTemplate,
  type CohortCreateValues,
} from './rules';

// #243 E4: the create body (structured fields only: never rrule,
// duration_minutes, until or count), the template → subject filtering, the
// rule summary, the URL filters, the PATCH body and version_conflict, the
// sessions window and "Respond by" with a foreign host zone
// (Pacific/Kiritimati, vite.config.ts) and a Tijuana DST week, and the
// guards (a Coordinator sees no "New cohort" but "Offer to a tutor";
// Finance neither). Then the pages: the tab bar, the list, the form (422s
// on their fields, the 409 panel and override), the detail and its edit
// dialog (focus back on "Edit").

const ZONE = 'America/Mexico_City';
const TIJUANA = 'America/Tijuana';
const LIST = 'GET /api/v1/cohorts';
const CREATE = 'POST /api/v1/cohorts';
const DETAIL = `GET /api/v1/cohorts/${COHORT_ID}`;
const PATCH = `PATCH /api/v1/cohorts/${COHORT_ID}`;
const SESSIONS_ROUTE = `GET /api/v1/cohorts/${COHORT_ID}/sessions`;
const OFFER = `POST /api/v1/class-series/${SERIES_ID}/offers`;
const TEMPLATES = 'GET /api/v1/course-templates';

const user = (
  roles: SessionUserBody['roles'],
  permissions: SessionUserBody['permissions'],
): SessionUserBody => ({ ...TEST_USER, roles, permissions });

const OWNER = user(
  ['owner'],
  [
    'course.view',
    'course.manage',
    'schedule.view',
    'schedule.manage',
    'schedule.assign',
    'schedule.override',
  ],
);
const COORDINATOR = user(
  ['academic_coordinator'],
  ['course.view', 'schedule.view', 'schedule.assign'],
);
const FINANCE = user(['finance_staff'], ['course.view', 'schedule.view']);

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
    [DETAIL]: () => jsonResponse(200, COHORT),
    [SESSIONS_ROUTE]: () => jsonResponse(200, { items: SESSIONS }),
    [TEMPLATES]: (request) =>
      new URL(request.url).searchParams.get('status') === 'active'
        ? page(TEMPLATE_OPTIONS)(request, undefined)
        : page([summaryOf(ALGEBRA), PHYSICS_LAB])(request, undefined),
    'GET /api/v1/subjects': page([]),
    'GET /api/v1/tutors': page([
      {
        user_id: '01900000-0000-7000-8000-00000000e031',
        display_name: 'Carmen Ruiz',
        state: 'active',
      },
    ]),
    'GET /api/v1/classrooms': page([]),
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
  for (let i = 0; i < 40; i += 1) {
    if (rendered.container.querySelector('main h1')) {
      break;
    }
    await settle();
  }
  await settle();
  return rendered;
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

function selectByLabel(root: ParentNode, label: string): HTMLSelectElement {
  return [...root.querySelectorAll('label')]
    .filter((l) => l.textContent === label)
    .map((l) => document.getElementById(l.htmlFor))[0] as HTMLSelectElement;
}

function lastQuery(api: ApiStub, pathname: string): URLSearchParams {
  return api.fetch.mock.calls
    .map(([request]) => new URL(request.url))
    .filter((u) => u.pathname === pathname)
    .at(-1)!.searchParams;
}

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

const VALUES: CohortCreateValues = {
  ...EMPTY_COHORT,
  name: '  Álgebra enero ',
  templateId: ALGEBRA.id,
  subjectId: ALGEBRA.subjects[0]!.id,
  max: '12',
  startsOn: '2031-01-06',
  start: '10:00',
};
const WEEKLY: RecurrenceValues = {
  ...EMPTY_RECURRENCE,
  weekdays: [3, 1],
  interval: 2,
  // Left from another form: never sent.
  ends: 'count',
  count: '4',
  until: '2031-03-01',
};

describe('the create body (C4)', () => {
  it('sends the structured fields only: no rrule, duration, until or count', () => {
    const body = cohortCreateBody(VALUES, WEEKLY, ZONE);
    expect(body).toEqual({
      name: 'Álgebra enero',
      template_id: ALGEBRA.id,
      subject_id: ALGEBRA.subjects[0]!.id,
      max_students: 12,
      tutor_user_id: null,
      classroom_id: null,
      starts_on: '2031-01-06',
      start_time: '10:00',
      recurrence: { weekdays: [1, 3], interval: 2 },
    });
    expect(body).not.toHaveProperty('duration_minutes');
    expect(body.recurrence).not.toHaveProperty('count');
    expect(body.recurrence).not.toHaveProperty('until');
    expect(JSON.stringify(body)).not.toMatch(/rrule|FREQ=/i);
  });

  it('sends "Respond by" only with a tutor, in the center zone across the Tijuana DST change', () => {
    const withDeadline = {
      ...VALUES,
      respondByDate: '2026-03-08',
      respondByTime: '10:00',
    };
    expect(cohortCreateBody(withDeadline, WEEKLY, TIJUANA)).not.toHaveProperty(
      'respond_by',
    );
    const tutored = { ...withDeadline, tutorId: 't1' };
    // 2026-03-08 is the first day of PDT (UTC-7) in Tijuana; the host zone
    // is Pacific/Kiritimati (UTC+14).
    expect(cohortCreateBody(tutored, WEEKLY, TIJUANA).respond_by).toBe(
      '2026-03-08T17:00:00.000Z',
    );
    expect(
      cohortCreateBody(
        { ...tutored, respondByDate: '2026-03-07' },
        WEEKLY,
        TIJUANA,
      ).respond_by,
    ).toBe('2026-03-07T18:00:00.000Z');
    expect(
      cohortCreateBody(tutored, WEEKLY, ZONE, {
        pairs: [],
        reason: 'x',
      }).override,
    ).toEqual({ pairs: [], reason: 'x' });
  });

  it('checks the required fields, the bounds, the deadline and the weekdays', () => {
    const empty = validateCohortCreate(EMPTY_COHORT, EMPTY_RECURRENCE);
    expect(Object.keys(empty.errors).sort()).toEqual(
      ['max', 'name', 'start', 'startsOn', 'subject', 'template'].sort(),
    );
    expect(empty.recurrence).toEqual({
      weekdays: 'scheduling.series.validation.weekdaysRequired',
    });
    const bad = validateCohortCreate(
      { ...VALUES, max: '501', tutorId: 't1', respondByDate: '2031-01-01' },
      WEEKLY,
    );
    expect(bad.errors).toEqual({
      max: 'courses.cohorts.validation.maxRange',
      respondBy: 'scheduling.offer.validation.deadlineIncomplete',
    });
    // No "Ends": a stale until/count never blocks the cohort.
    expect(
      validateCohortCreate(VALUES, { ...WEEKLY, count: 'x', until: '' }),
    ).toEqual({ errors: {}, recurrence: {} });
  });

  it('puts the #65 codes on their fields and too_many_occurrences on the weekdays', () => {
    const found = createServerErrors({
      code: 'validation_error',
      fieldErrors: [
        {
          path: ['body', 'template_id'],
          code: 'template_inactive',
          message: '',
        },
        {
          path: ['body', 'subject_id'],
          code: 'subject_not_in_template',
          message: '',
        },
        { path: ['body', 'starts_on'], code: 'starts_on_in_past', message: '' },
        {
          path: ['body', 'respond_by'],
          code: 'respond_by_requires_tutor',
          message: '',
        },
        {
          path: ['body', 'recurrence'],
          code: 'too_many_occurrences',
          message: '',
        },
        {
          path: ['body', 'recurrence', 'interval'],
          code: 'interval_out_of_range',
          message: '',
        },
      ],
    });
    expect(found.fields).toEqual({
      template: 'errors:template_inactive',
      subject: 'errors:subject_not_in_template',
      startsOn: 'errors:starts_on_in_past',
      respondBy: 'errors:respond_by_requires_tutor',
    });
    expect(found.recurrence).toEqual({
      weekdays: 'errors:too_many_occurrences',
      interval: 'errors:interval_out_of_range',
    });
    expect(
      createServerErrors({ code: 'cohort_name_taken', fieldErrors: [] }).fields,
    ).toEqual({ name: 'errors:cohort_name_taken' });
  });

  it('overrides only soft conflicts of single occurrences, as pairs', () => {
    expect(SOFT_CONFLICTS.every(createAccepts)).toBe(true);
    expect(createAccepts(HARD_CONFLICT)).toBe(false);
    expect(createAccepts(COHORT_LEVEL_CONFLICT)).toBe(false);
    expect(cohortCreateOverride(SOFT_CONFLICTS, '  Curso de verano ')).toEqual({
      pairs: [
        {
          occurrence_start: '2031-01-06T16:00:00Z',
          code: 'outside_operating_hours',
        },
        {
          occurrence_start: '2031-01-08T16:00:00Z',
          code: 'outside_operating_hours',
        },
      ],
      reason: 'Curso de verano',
    });
  });
});

describe('the template and the rule summary (C1, C2)', () => {
  it("offers only the template's subjects, preselecting its only one", () => {
    expect(templateSubjects(PHYSICS_ACTIVE).map((s) => s.name)).toEqual([
      'Física',
      'Química',
    ]);
    const one = withTemplate(EMPTY_COHORT, TEMPLATE_OPTIONS[0]);
    expect(one).toMatchObject({
      templateId: ALGEBRA.id,
      subjectId: ALGEBRA.subjects[0]!.id,
    });
    // A subject the new template doesn't offer is cleared.
    expect(withTemplate(one, PHYSICS_ACTIVE).subjectId).toBe('');
    expect(withTemplate(one, undefined)).toMatchObject({
      templateId: '',
      subjectId: '',
    });
  });

  it("says the rule with the template's session count", () => {
    expect(describeRecurrence(cohortRule(WEEKLY, 10), 'en')).toBe(
      'Every 2 weeks on Monday and Wednesday, 10 sessions in all',
    );
    expect(
      describeRecurrence(cohortRule({ weekdays: [2], interval: 1 }, 1), 'es'),
    ).toBe('Cada semana, los días martes, 1 sesión en total');
  });
});

describe('the list URL state (B1)', () => {
  it('parses the filters with their defaults', () => {
    expect(listFilters(new URLSearchParams(''))).toEqual({
      q: '',
      template: '',
      subject: '',
      status: 'current',
    });
    expect(
      listFilters(
        new URLSearchParams('q=  álg &template=t1&subject=s1&status=ended'),
      ),
    ).toEqual({ q: 'álg', template: 't1', subject: 's1', status: 'ended' });
    expect(listFilters(new URLSearchParams('status=bogus')).status).toBe(
      'current',
    );
  });

  it('writes only the non-default filters, never a cursor', () => {
    expect(
      filterParams({
        q: '',
        template: '',
        subject: '',
        status: 'current',
      }).toString(),
    ).toBe('');
    expect(
      filterParams({
        q: 'a b',
        template: 't1',
        subject: '',
        status: 'all',
      }).toString(),
    ).toBe('q=a+b&template=t1&status=all');
    expect(
      hasFilters({ q: '', template: '', subject: '', status: 'upcoming' }),
    ).toBe(true);
  });
});

describe('the assignment, the offer and the window (D3, D4)', () => {
  it('shows the assignment and offers only an untutored series to schedule.assign', () => {
    expect(assignmentOf(COHORT.series)).toEqual({ kind: 'none' });
    expect(assignmentOf(PENDING_COHORT.series)).toEqual({
      kind: 'pending',
      tutor: 'Carmen Ruiz',
      respondBy: '2031-01-03T18:00:00Z',
    });
    expect(assignmentOf(TUTORED_COHORT.series).kind).toBe('accepted');
    expect(canOfferSeries(COHORT.series, true)).toBe(true);
    expect(canOfferSeries(COHORT.series, false)).toBe(false);
    expect(canOfferSeries(PENDING_COHORT.series, true)).toBe(false);
    expect(canOfferSeries(ENDED_COHORT.series, true)).toBe(false);
    expect(offerFromDate(COHORT.series!, '2030-12-20')).toBe('2031-01-06');
    expect(offerFromDate(COHORT.series!, '2031-01-20')).toBe('2031-01-20');
  });

  it('runs 4 weeks from local midnight, across the Tijuana DST change', () => {
    expect(sessionsWindow('2031-01-04', ZONE)).toEqual({
      from: '2031-01-04T06:00:00.000Z',
      to: '2031-02-01T06:00:00.000Z',
    });
    const range = sessionsWindow('2026-03-01', TIJUANA);
    expect(range).toEqual({
      from: '2026-03-01T08:00:00.000Z',
      to: '2026-03-29T07:00:00.000Z',
    });
    expect(
      (Date.parse(range.to) - Date.parse(range.from)) / 86_400_000,
    ).toBeLessThanOrEqual(45);
    expect(shiftWindow('2031-01-04', -1)).toBe('2030-12-07');
  });

  it('sends the version and only the changed fields', () => {
    expect(hasChanges(COHORT, { name: ` ${COHORT.name} `, max: '12' })).toBe(
      false,
    );
    expect(updateBody(COHORT, { name: COHORT.name, max: '15' })).toEqual({
      version: COHORT.version,
      max_students: 15,
    });
    expect(updateBody(COHORT, { name: ' Álgebra B ', max: '12' })).toEqual({
      version: COHORT.version,
      name: 'Álgebra B',
    });
  });
});

// --- Pages ---------------------------------------------------------------------

describe('access and the tab bar (A1, A2)', () => {
  it.each([
    ['academic_coordinator', COORDINATOR],
    ['finance_staff', FINANCE],
  ] as const)(
    'gives a %s the Forbidden page on /courses/cohorts/new',
    async (_role, as) => {
      const api = stub(as);
      app = await open('/courses/cohorts/new');
      expect(main().querySelector('h1')?.textContent).toBe(
        "You don't have access to this page",
      );
      expect(api.calls(TEMPLATES)).toHaveLength(0);
    },
  );

  it('marks the current section of the Courses area', async () => {
    stub(OWNER, { [TEMPLATES]: page([]) });
    app = await open('/courses');
    const nav = () =>
      main().querySelector('nav[aria-label="Courses sections"]')!;
    const current = () =>
      nav().querySelector('[aria-current="page"]')?.textContent;
    expect(current()).toBe('Templates');
    app.unmount();
    stub(OWNER);
    app = await open('/courses/cohorts');
    expect(current()).toBe('Cohorts');
    expect(nav().querySelector('a[href="/courses"]')).not.toBeNull();
  });
});

describe('/courses/cohorts (B)', () => {
  it('lists the cohorts with their status as text, and "New cohort" for schedule.manage', async () => {
    const api = stub(OWNER);
    app = await open('/courses/cohorts');
    expect(main().querySelector('h1')?.textContent).toBe('Cohorts');
    const rows = [...main().querySelectorAll('tbody tr')].map((row) =>
      [...row.querySelectorAll('td')].map((cell) => cell.textContent),
    );
    expect(rows[0]).toEqual([
      'Álgebra enero',
      ALGEBRA.name,
      'Matemáticas (Secundaria)',
      'No tutor yet',
      'Jan 6, 2031',
      'Feb 5, 2031',
      'Upcoming',
    ]);
    expect(rows[1]![3]).toBe('Carmen Ruiz: offer pending');
    expect(rows[2]![6]).toBe('Ended');
    expect(rows[3]![6]).toBe('In progress');
    expect(
      main().querySelector('a[href="/courses/cohorts/new"]'),
    ).not.toBeNull();
    const query = lastQuery(api, '/api/v1/cohorts');
    expect(query.get('status')).toBe('current');
    expect(query.get('limit')).toBe('25');
    // The template filter lists inactive templates too.
    expect(
      [...selectByLabel(main(), 'Template').options].map((o) => o.text),
    ).toEqual([
      'All templates',
      ALGEBRA.name,
      `${PHYSICS_LAB.name} (inactive)`,
    ]);
  });

  it('keeps the filters in the URL and loads the next page by cursor', async () => {
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
    app = await open('/courses/cohorts');
    await click(buttonNamed(main(), 'Load more'));
    expect(lastQuery(api, '/api/v1/cohorts').get('cursor')).toBe('c1');
    expect(main().textContent).toContain('Álgebra febrero');
    choose(main(), 'Template', ALGEBRA.id);
    await settle();
    choose(main(), 'Status', 'all');
    await settle();
    expect(app.location()).toBe(
      `/courses/cohorts?template=${ALGEBRA.id}&status=all`,
    );
    const query = lastQuery(api, '/api/v1/cohorts');
    expect(query.get('cursor')).toBeNull();
    expect(query.get('template_id')).toBe(ALGEBRA.id);
    expect(query.get('status')).toBe('all');
  });

  it('offers "Clear filters" when nothing matches; no "New cohort" for a Coordinator', async () => {
    stub(COORDINATOR, {
      [LIST]: () => jsonResponse(200, { items: [], next_cursor: null }),
    });
    app = await open('/courses/cohorts?status=ended');
    expect(main().textContent).toContain('No cohorts match these filters.');
    expect(main().querySelector('a[href="/courses/cohorts/new"]')).toBeNull();
    await click(buttonNamed(main(), 'Clear filters'));
    expect(app.location()).toBe('/courses/cohorts');
  });
});

describe('/courses/cohorts/new (C)', () => {
  async function fill() {
    typeInto(fieldByLabel(main(), 'Name'), 'Álgebra enero');
    choose(main(), 'Course template', PHYSICS_ACTIVE.id);
    await settle();
    expect(
      [...selectByLabel(main(), 'Subject').querySelectorAll('option')].map(
        (o) => o.textContent,
      ),
    ).toEqual(['Choose a subject', 'Física', 'Química']);
    choose(main(), 'Course template', ALGEBRA.id);
    await settle();
    // Its only subject is preselected; its sessions show read-only.
    expect(selectByLabel(main(), 'Subject').value).toBe(
      ALGEBRA.subjects[0]!.id,
    );
    expect(main().textContent).toContain('10 sessions of 90 min');
    typeInto(fieldByLabel(main(), 'Maximum students'), '12');
    typeInto(fieldByLabel(main(), 'Starts on'), '2031-01-06');
    typeInto(fieldByLabel(main(), 'Start'), '10:00');
  }

  it('creates from the structured fields, then opens the cohort with "Cohort created."', async () => {
    const api = stub(OWNER, {
      [CREATE]: () => jsonResponse(201, CREATED_COHORT),
    });
    app = await open('/courses/cohorts/new');
    // No "Ends" choice, and only active templates.
    expect(main().textContent).not.toContain('Ends');
    expect(
      [...selectByLabel(main(), 'Course template').options].map((o) => o.text),
    ).toEqual(['Choose a template', ALGEBRA.name, PHYSICS_LAB.name]);
    await fill();
    expect(main().textContent).toContain(
      'Every week on Monday, 10 sessions in all',
    );
    await click(buttonNamed(main(), 'Create cohort'));
    const [body] = api.calls(CREATE).map((c) => c.body);
    expect(body).toEqual({
      name: 'Álgebra enero',
      template_id: ALGEBRA.id,
      subject_id: ALGEBRA.subjects[0]!.id,
      max_students: 12,
      tutor_user_id: null,
      classroom_id: null,
      starts_on: '2031-01-06',
      start_time: '10:00',
      recurrence: { weekdays: [1], interval: 1 },
    });
    for (let i = 0; i < 20 && !app.location().endsWith(COHORT_ID); i += 1) {
      await settle();
    }
    expect(app.location()).toBe(`/courses/cohorts/${COHORT_ID}`);
    expect(main().querySelector('[role="status"]')?.textContent).toBe(
      'Cohort created.',
    );
  });

  it('puts the 422s on their fields and keeps what was typed', async () => {
    stub(OWNER, {
      [CREATE]: [
        () =>
          invalid([
            { path: ['body', 'template_id'], code: 'template_inactive' },
            { path: ['body', 'recurrence'], code: 'too_many_occurrences' },
          ]),
        () => problem(409, 'cohort_name_taken'),
      ],
    });
    app = await open('/courses/cohorts/new');
    await fill();
    await click(buttonNamed(main(), 'Create cohort'));
    const template = selectByLabel(main(), 'Course template');
    expect(
      document.getElementById(
        template.getAttribute('aria-describedby')!.split(' ').at(-1)!,
      )?.textContent,
    ).toBe('This course template is inactive. Choose an active one.');
    expect(document.activeElement).toBe(template);
    expect(main().textContent).toContain(
      "The schedule can't fit the course's sessions",
    );
    await click(buttonNamed(main(), 'Create cohort'));
    expect(main().textContent).toContain(
      'Another cohort already has this name.',
    );
    expect(document.activeElement).toBe(fieldByLabel(main(), 'Name'));
    expect(fieldByLabel(main(), 'Maximum students').value).toBe('12');
  });

  it('shows the date-grouped 409 and overrides the soft pairs', async () => {
    const api = stub(OWNER, {
      [CREATE]: [
        () => conflictResponse(SOFT_CONFLICTS),
        () => jsonResponse(201, CREATED_COHORT),
      ],
    });
    app = await open('/courses/cohorts/new');
    await fill();
    await click(buttonNamed(main(), 'Create cohort'));
    const panel = main().querySelector('[role="alert"]')!;
    expect([...panel.querySelectorAll('h3')].map((h) => h.textContent)).toEqual(
      ['January 6, 2031', 'January 8, 2031'],
    );
    await click(buttonNamed(main(), 'Override and create'));
    const dialog = openDialog()!;
    const reason = dialog.querySelector('textarea')!;
    // eslint-disable-next-line @typescript-eslint/unbound-method
    const setter = Object.getOwnPropertyDescriptor(
      HTMLTextAreaElement.prototype,
      'value',
    )?.set;
    act(() => {
      setter?.call(reason, 'Curso de verano');
      reason.dispatchEvent(new Event('input', { bubbles: true }));
    });
    await click(buttonNamed(dialog, 'Override and create'));
    const [first, second] = api
      .calls(CREATE)
      .map((c) => c.body as Record<string, unknown>);
    expect(first).not.toHaveProperty('override');
    expect(second!.override).toEqual(
      cohortCreateOverride(SOFT_CONFLICTS, 'Curso de verano'),
    );
  });

  it("says a hard or cohort-level conflict can't be overridden", async () => {
    stub(OWNER, {
      [CREATE]: () =>
        conflictResponse([SOFT_CONFLICTS[0], COHORT_LEVEL_CONFLICT]),
    });
    app = await open('/courses/cohorts/new');
    await fill();
    await click(buttonNamed(main(), 'Create cohort'));
    expect(main().textContent).toContain("Can't be overridden for cohorts yet");
    expect(() => buttonNamed(main(), 'Override and create')).toThrow();
    expect(fieldByLabel(main(), 'Name').value).toBe('Álgebra enero');
  });
});

describe('/courses/cohorts/:id (D)', () => {
  it('shows the details, the snapshot syllabus, the sessions and the actions', async () => {
    const api = stub(OWNER);
    app = await open(`/courses/cohorts/${COHORT_ID}`);
    const text = main().textContent ?? '';
    expect(main().querySelector('h1')?.textContent).toBe('Álgebra enero');
    expect(
      main().querySelector(`a[href="/courses/templates/${ALGEBRA.id}"]`)
        ?.textContent,
    ).toBe(`${ALGEBRA.name}, version 1`);
    expect(text).toContain('Matemáticas (Secundaria)');
    expect(text).toContain('Upcoming');
    expect(text).toContain(
      'Every week on Monday and Wednesday, 10 sessions in all',
    );
    expect(text).toContain('10:00 AM–11:30 AM (America/Mexico_City)');
    expect(text).toContain('No tutor yet');
    // The snapshot, never the live template.
    expect(text).toContain('Ecuaciones (copia congelada)');
    expect(text).toContain('Skipped (closure)');
    expect(text).toContain('Exception');
    expect(api.calls(SESSIONS_ROUTE)).toHaveLength(1);
    expect(buttonNamed(main(), 'Edit')).toBeTruthy();
    expect(buttonNamed(main(), 'Offer the course')).toBeTruthy();
    // The series only: no "This session only".
    expect(text).not.toContain('This session only');
  });

  it('gives a Coordinator "Offer to a tutor" but no "Edit"; Finance neither', async () => {
    stub(COORDINATOR);
    app = await open(`/courses/cohorts/${COHORT_ID}`);
    expect(main().textContent).toContain('Offer to a tutor');
    expect(() => buttonNamed(main(), 'Edit')).toThrow();
    app.unmount();

    stub(FINANCE);
    app = await open(`/courses/cohorts/${COHORT_ID}`);
    expect(main().textContent).not.toContain('Offer to a tutor');
    expect(() => buttonNamed(main(), 'Edit')).toThrow();
    expect(
      main().querySelector('a[href^="/schedule/sessions/"]'),
    ).not.toBeNull();
  });

  it('offers the series from its start date, with the series version', async () => {
    const api = stub(OWNER, {
      [OFFER]: () =>
        jsonResponse(201, { offer: { scope: 'series' }, warnings: [] }),
    });
    app = await open(`/courses/cohorts/${COHORT_ID}`);
    choose(main(), 'Tutor', '01900000-0000-7000-8000-00000000e031');
    await click(buttonNamed(main(), 'Offer the course'));
    expect(api.calls(OFFER).map((c) => c.body)).toEqual([
      {
        tutor_user_id: '01900000-0000-7000-8000-00000000e031',
        version: COHORT.series!.version,
        from_date: '2031-01-06',
      },
    ]);
    expect(main().querySelector('[role="status"]')?.textContent).toBe(
      'Offer sent. The tutor answers in My offers.',
    );
  });

  it('shows a pending offer without an action, and not-found for a 404', async () => {
    stub(OWNER, { [DETAIL]: () => jsonResponse(200, PENDING_COHORT) });
    app = await open(`/courses/cohorts/${COHORT_ID}`);
    expect(main().textContent).toContain(
      'Carmen Ruiz: offer pending, responds by',
    );
    expect(main().textContent).not.toContain('Offer to a tutor');
    app.unmount();

    stub(OWNER, { [DETAIL]: () => problem(404, 'cohort_not_found') });
    app = await open(`/courses/cohorts/${COHORT_ID}`);
    expect(main().querySelector('h1')?.textContent).toBe('Page not found');
  });

  it('edits the changed maximum; a version conflict offers a reload; focus returns to "Edit"', async () => {
    const api = stub(OWNER, {
      [PATCH]: [
        () => problem(409, 'version_conflict'),
        () => jsonResponse(200, { ...COHORT, max_students: 15, version: 4 }),
      ],
    });
    app = await open(`/courses/cohorts/${COHORT_ID}`);
    const trigger = buttonNamed(main(), 'Edit');
    act(() => trigger.focus());
    await click(trigger);
    let dialog = openDialog()!;
    expect(buttonNamed(dialog, 'Save').disabled).toBe(true);
    await pressEscape();
    expect(openDialog()).toBeNull();
    expect(document.activeElement).toBe(trigger);

    await click(trigger);
    dialog = openDialog()!;
    typeInto(fieldByLabel(dialog, 'Maximum students'), '15');
    await click(buttonNamed(dialog, 'Save'));
    expect(dialog.textContent).toContain(
      'This cohort changed. Reload to see the latest.',
    );
    expect(buttonNamed(dialog, 'Reload')).toBeTruthy();
    await click(buttonNamed(dialog, 'Save'));
    expect(api.calls(PATCH).map((c) => c.body)).toEqual([
      { version: COHORT.version, max_students: 15 },
      { version: COHORT.version, max_students: 15 },
    ]);
    expect(openDialog()).toBeNull();
    expect(main().textContent).toContain('Cohort saved.');
    expect(document.activeElement).toBe(buttonNamed(main(), 'Edit'));
  });
});
