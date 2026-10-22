import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, createElement } from 'react';
import { createRoot } from 'react-dom/client';
import { createMemoryRouter, RouterProvider } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { SessionUserBody } from '@/features/auth/session';
import { PICKER_DEBOUNCE_MS } from '@/features/guardians/SearchPicker';
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
import { SessionDialog } from '@/features/calendar/SessionDialog';
import { item } from '@/features/calendar/fixtures';
import { i18n } from '@/i18n';
import { ANA, CLASSROOMS, SUBJECTS, TUTORS } from '../create/fixtures';
import { EMPTY_SCHEDULE, type ScheduleValues } from '../create/rules';
import { conflictsByDate } from '../session/rules';
import { describeRecurrence } from './describeRecurrence';
import {
  CANCELLED_SERIES,
  ENDED_SERIES,
  LATER_SERIES_ID,
  LINEAGE_SERIES,
  OCCURRENCE_SESSION,
  OCCURRENCES,
  OVERRIDE_SERIES,
  SERIES_CANCELLED,
  SERIES_CONFLICTS,
  SERIES_CREATED,
  SERIES_DETAIL,
  SERIES_EDITED,
  SERIES_ID,
  SOFT_SERIES_CONFLICTS,
  SPLIT_SERIES,
} from './fixtures';
import {
  affectedOccurrences,
  conflictOverrideBody,
  conflictRowAction,
  overriddenCount,
  seriesOverride,
  seriesOverridePairs,
} from './overrides';
import {
  durationMinutes,
  EMPTY_RECURRENCE,
  recurrenceBody,
  recurrenceServerErrors,
  recurrenceValuesOf,
  validateRecurrence,
  weekdaysFor,
} from './recurrence';
import {
  cancelBody,
  canEditOccurrence,
  defaultFromDate,
  isSeriesOver,
  occurrenceBody,
  seriesActions,
  seriesCreateBody,
  seriesEditBody,
  seriesEditValues,
  summaryParts,
  validateCancel,
} from './rules';

// #54b E2: describeRecurrence in es and en; the A2 client rules, the
// duration and the request body (structured fields, never `rrule`); the
// instant conversion of one occurrence for a Tijuana DST date (the test
// host zone is Pacific/Kiritimati, vite.config.ts); the date grouping of a
// series 409; the C2 pre-fill (COUNT → On date); the action visibility by
// permission, row state and `ended`. Then the pages: the create form's
// "Repeats weekly" section (201, 409, 422), the series page (route guard,
// not found, header, lineage, rows) and the three dialogs.

const ZONE = 'America/Mexico_City';
const TIJUANA = 'America/Tijuana';
const CREATE = 'POST /api/v1/class-series';
const DETAIL = `GET /api/v1/class-series/${SERIES_ID}`;
const EDIT = `POST /api/v1/class-series/${SERIES_ID}/edits`;
const CANCEL = `POST /api/v1/class-series/${SERIES_ID}/cancellations`;
const SESSION = `GET /api/v1/class-sessions/${OCCURRENCES[0]!.id}`;
const PATCH = `PATCH /api/v1/class-sessions/${OCCURRENCES[0]!.id}`;
// Before every fixture row.
const NOW = Date.parse('2030-12-01T00:00:00Z');

const MANAGER: SessionUserBody = {
  ...TEST_USER,
  permissions: ['schedule.view', 'schedule.manage', 'student.view'],
};
const VIEWER: SessionUserBody = {
  ...TEST_USER,
  roles: ['finance_staff'],
  permissions: ['schedule.view'],
};

const VALUES: ScheduleValues = {
  ...EMPTY_SCHEDULE,
  student: ANA,
  subjectId: SUBJECTS[0]!.id,
  date: '2031-01-06',
  start: '10:00',
  end: '11:15',
};

function page<T>(items: T[]): Responder {
  return () =>
    jsonResponse(200, { items, total: items.length, limit: 100, offset: 0 });
}

function stub(
  user: SessionUserBody = MANAGER,
  routes: Record<string, Responder | Responder[]> = {},
): ApiStub {
  return stubApi({
    'GET /api/v1/auth/me': meAs(user),
    'GET /api/v1/subjects': page(
      SUBJECTS.map((s) => ({
        id: s.id,
        name: s.name,
        education_level_id: '00000000-0000-4000-8000-0000000004f1',
        education_level_name: s.levelName,
        is_active: true,
        version: 1,
        created_at: '2026-01-01T00:00:00Z',
        updated_at: '2026-01-01T00:00:00Z',
      })),
    ),
    'GET /api/v1/tutors': page(
      TUTORS.map((t) => ({
        user_id: t.id,
        username: t.id.slice(-4),
        display_name: t.name,
        state: 'active',
        email: null,
        phone: null,
      })),
    ),
    'GET /api/v1/classrooms': page(
      CLASSROOMS.map((c) => ({
        id: c.id,
        name: c.name,
        capacity: 10,
        is_active: true,
        availability: [],
        version: 1,
        created_at: '2026-01-01T00:00:00Z',
        updated_at: '2026-01-01T00:00:00Z',
      })),
    ),
    'GET /api/v1/students': page([
      {
        id: ANA.id,
        given_names: 'Ana',
        family_names: 'López',
        is_active: true,
        is_minor: true,
        school_grade: null,
        education_level: null,
      },
    ]),
    [DETAIL]: () => jsonResponse(200, SERIES_DETAIL),
    ...routes,
  });
}

function conflictResponse(conflicts: unknown[] = SERIES_CONFLICTS): Response {
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

let app: RenderedApp | undefined;
const main = () => app!.container.querySelector('main')!;

function choose(root: ParentNode, label: string, value: string) {
  const select = [...root.querySelectorAll('label')]
    .filter((l) => l.textContent === label)
    .map((l) => document.getElementById(l.htmlFor))[0] as HTMLSelectElement;
  act(() => {
    select.value = value;
    select.dispatchEvent(new Event('change', { bubbles: true }));
  });
}

function radioNamed(root: ParentNode, text: string): HTMLInputElement {
  const label = [...root.querySelectorAll('label')].find(
    (l) => l.textContent === text,
  );
  const input = label?.querySelector('input');
  if (!input) {
    throw new Error(`No choice "${text}"`);
  }
  return input;
}

function weekday(root: ParentNode, day: number): HTMLInputElement {
  return root.querySelector<HTMLInputElement>(
    `input[type="checkbox"][value="${day}"]`,
  )!;
}

async function fillSeriesForm() {
  typeInto(fieldByLabel(main(), 'Student'), 'Ana');
  await act(async () => {
    await new Promise((resolve) =>
      setTimeout(resolve, PICKER_DEBOUNCE_MS + 30),
    );
  });
  await settle();
  await click(
    main().querySelector<HTMLInputElement>(
      `input[type="radio"][value="${ANA.id}"]`,
    )!,
  );
  choose(main(), 'Subject', SUBJECTS[0]!.id);
  await click(main().querySelector<HTMLInputElement>('[role="switch"]')!);
  typeInto(fieldByLabel(main(), 'Starts on'), '2031-01-06');
  typeInto(fieldByLabel(main(), 'Start'), '10:00');
  typeInto(fieldByLabel(main(), 'End'), '11:00');
}

/** The lazy route needs a few more turns to load and render. */
async function renderSeries(): Promise<RenderedApp> {
  const rendered = await renderApp(`/schedule/series/${SERIES_ID}`);
  for (let i = 0; i < 40; i += 1) {
    if (rendered.container.querySelector('main h1')) {
      break;
    }
    await settle();
  }
  await settle();
  return rendered;
}

beforeEach(() => {
  applyLanguage('en');
  vi.spyOn(console, 'error').mockImplementation(() => {});
});

afterEach(() => {
  app?.unmount();
  app = undefined;
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('describeRecurrence (A4)', () => {
  it('describes the rule in English and Spanish, never from rrule', () => {
    const rule = {
      weekdays: [4, 1],
      interval: 2,
      until: '2026-12-18',
      count: null,
    };
    expect(describeRecurrence(rule, 'en')).toBe(
      'Every 2 weeks on Monday and Thursday, until Dec 18, 2026',
    );
    expect(describeRecurrence(rule, 'es')).toBe(
      'Cada 2 semanas, los días lunes y jueves, hasta el 18 dic 2026',
    );
    const weekly = { weekdays: [1, 3, 5], interval: 1, until: null, count: 6 };
    expect(describeRecurrence(weekly, 'en')).toBe(
      'Every week on Monday, Wednesday, and Friday, 6 sessions in all',
    );
    expect(describeRecurrence(weekly, 'es')).toBe(
      'Cada semana, los días lunes, miércoles y viernes, 6 sesiones en total',
    );
    const open = { weekdays: [7], interval: 1, until: null, count: null };
    expect(describeRecurrence(open, 'en')).toBe(
      'Every week on Sunday, with no end date',
    );
    expect(describeRecurrence({ ...open, count: 1 }, 'es')).toBe(
      'Cada semana, los días domingo, 1 sesión en total',
    );
  });
});

describe('client rules and the create body (A2)', () => {
  it('wants a weekday, the interval and count in bounds, the end after the start', () => {
    const rule = { ...EMPTY_RECURRENCE, weekdays: [1] };
    expect(validateRecurrence(rule, '2031-01-06')).toEqual({});
    expect(validateRecurrence(EMPTY_RECURRENCE, '2031-01-06')).toEqual({
      weekdays: 'scheduling.series.validation.weekdaysRequired',
    });
    expect(
      validateRecurrence({ ...rule, interval: 5 }, '2031-01-06').interval,
    ).toBe('scheduling.series.validation.intervalRange');
    expect(
      validateRecurrence(
        { ...rule, ends: 'until', until: '2031-01-05' },
        '2031-01-06',
      ),
    ).toEqual({ until: 'scheduling.series.validation.untilBeforeStart' });
    expect(
      validateRecurrence(
        { ...rule, ends: 'until', until: '2031-01-06' },
        '2031-01-06',
      ),
    ).toEqual({});
    expect(
      validateRecurrence({ ...rule, ends: 'until', until: '' }, '2031-01-06')
        .until,
    ).toBe('scheduling.series.validation.untilRequired');
    for (const count of ['0', '201', '2.5', '', 'x']) {
      expect(
        validateRecurrence({ ...rule, ends: 'count', count }, '2031-01-06')
          .count,
      ).toBe('scheduling.series.validation.countRange');
    }
    for (const count of ['1', '200']) {
      expect(
        validateRecurrence({ ...rule, ends: 'count', count }, '2031-01-06'),
      ).toEqual({});
    }
  });

  it('computes the duration from the start and end times', () => {
    expect(durationMinutes('10:00', '11:15')).toBe(75);
    expect(durationMinutes('09:30', '10:00')).toBe(30);
    expect(durationMinutes('', '10:00')).toBeNull();
  });

  it('pre-checks the start date weekday (ISO, any host zone)', () => {
    expect(weekdaysFor('2031-01-06')).toEqual([1]);
    expect(weekdaysFor('2031-01-12')).toEqual([7]);
    expect(weekdaysFor('')).toEqual([]);
  });

  it('sends structured fields only, never rrule text', () => {
    const body = seriesCreateBody(VALUES, {
      weekdays: [3, 1, 3],
      interval: 2,
      ends: 'count',
      count: ' 6 ',
      until: '2031-02-01',
    });
    expect(body).toEqual({
      kind: 'individual',
      student_id: ANA.id,
      subject_id: SUBJECTS[0]!.id,
      tutor_user_id: null,
      classroom_id: null,
      starts_on: '2031-01-06',
      start_time: '10:00',
      duration_minutes: 75,
      recurrence: { weekdays: [1, 3], interval: 2, until: null, count: 6 },
    });
    expect(JSON.stringify(body)).not.toMatch(/rrule|FREQ=/i);
    expect(
      recurrenceBody({
        ...EMPTY_RECURRENCE,
        weekdays: [5],
        ends: 'until',
        until: '2031-02-01',
        count: '3',
      }),
    ).toEqual({ weekdays: [5], interval: 1, until: '2031-02-01', count: null });
  });

  it('puts the 422 recurrence errors on their controls', () => {
    expect(
      recurrenceServerErrors([
        {
          path: ['body', 'recurrence', 'weekdays'],
          code: 'weekdays_empty',
          message: '',
        },
        {
          path: ['body', 'recurrence', 'until'],
          code: 'until_before_start',
          message: '',
        },
        { path: ['body', 'recurrence'], code: 'no_occurrences', message: '' },
        { path: ['body', 'recurrence', 'count'], code: 'odd', message: '' },
        {
          path: ['body', 'start_time'],
          code: 'crosses_local_date',
          message: '',
        },
      ]),
    ).toEqual({
      weekdays: 'errors:weekdays_empty',
      until: 'errors:until_before_start',
      ends: 'errors:no_occurrences',
      count: 'errors:validation_error',
    });
  });
});

describe('one occurrence in the center zone (C1)', () => {
  it('converts a Tijuana DST date with a foreign host zone', () => {
    expect(Intl.DateTimeFormat().resolvedOptions().timeZone).not.toBe(TIJUANA);
    // Before the change (PST, UTC-8) and after it (PDT, UTC-7).
    expect(
      occurrenceBody(
        { date: '2026-03-06', start: '16:00', end: '17:00', classroomId: '' },
        3,
        TIJUANA,
      ),
    ).toEqual({
      version: 3,
      starts_at: '2026-03-07T00:00:00.000Z',
      ends_at: '2026-03-07T01:00:00.000Z',
      classroom_id: null,
    });
    expect(
      occurrenceBody(
        { date: '2026-03-09', start: '16:00', end: '17:00', classroomId: 'r' },
        3,
        TIJUANA,
      ),
    ).toMatchObject({
      starts_at: '2026-03-09T23:00:00.000Z',
      classroom_id: 'r',
    });
  });
});

describe('the 409 grouping (A3)', () => {
  it('groups the conflicts by local date, in date order', () => {
    expect(
      conflictsByDate(SERIES_CONFLICTS).map((g) => [
        g.date,
        g.conflicts.map((c) => c.code),
      ]),
    ).toEqual([
      ['2031-01-06', ['room_overlap']],
      ['2031-01-15', ['tutor_unavailable', 'student_overlap']],
    ]);
  });
});

describe('the edit pre-fill and bodies (C2, C3)', () => {
  it('pre-fills a COUNT series as On date = its last occurrence', () => {
    const values = seriesEditValues(SERIES_DETAIL, '2031-01-13');
    expect(values).toEqual({
      mode: 'following',
      fromDate: '2031-01-13',
      start: '10:00',
      end: '11:00',
      classroomId: CLASSROOMS[0]!.id,
      recurrence: {
        weekdays: [1, 3],
        interval: 1,
        ends: 'until',
        until: '2031-01-22',
        count: '',
      },
    });
    expect(
      recurrenceValuesOf(
        { weekdays: [2], interval: 3, until: null, count: null },
        null,
      ),
    ).toEqual({
      weekdays: [2],
      interval: 3,
      ends: 'never',
      until: '',
      count: '',
    });
  });

  it('sends the full new rule with the version; all has no date', () => {
    const values = seriesEditValues(SERIES_DETAIL, '2031-01-13');
    expect(
      seriesEditBody({ ...values, start: '11:00', end: '12:30' }, 4),
    ).toEqual({
      mode: 'following',
      from_date: '2031-01-13',
      version: 4,
      start_time: '11:00',
      duration_minutes: 90,
      classroom_id: CLASSROOMS[0]!.id,
      recurrence: {
        weekdays: [1, 3],
        interval: 1,
        until: '2031-01-22',
        count: null,
      },
    });
    expect(
      seriesEditBody({ ...values, mode: 'all', classroomId: '' }, 4),
    ).toMatchObject({ mode: 'all', from_date: null, classroom_id: null });
    expect(
      cancelBody({ mode: 'all', fromDate: '2031-01-13', reason: '  Fin  ' }, 4),
    ).toEqual({ mode: 'all', from_date: null, reason: 'Fin', version: 4 });
    expect(
      validateCancel({ mode: 'following', fromDate: '', reason: ' ' }),
    ).toEqual({
      fromDate: 'scheduling.series.validation.fromDateRequired',
      reason: 'scheduling.series.validation.reasonRequired',
    });
    expect(
      validateCancel({ mode: 'all', fromDate: '', reason: 'x'.repeat(1001) }),
    ).toEqual({ reason: 'scheduling.series.validation.reasonTooLong' });
  });

  it('defaults the scope date to the next occurrence that holds a class', () => {
    expect(defaultFromDate(SERIES_DETAIL, ZONE, '2030-12-01', NOW)).toBe(
      '2031-01-06',
    );
    // After the first two: the skipped and cancelled ones are passed over.
    expect(
      defaultFromDate(
        SERIES_DETAIL,
        ZONE,
        '2030-12-01',
        Date.parse('2031-01-09T00:00:00Z'),
      ),
    ).toBe('2031-01-20');
    expect(defaultFromDate({ occurrences: [] }, ZONE, '2030-12-01', NOW)).toBe(
      '2030-12-01',
    );
  });

  it('lists only the non-zero summary counts', () => {
    expect(summaryParts(SERIES_EDITED.summary)).toEqual([
      { key: 'moved', count: 4 },
      { key: 'cancelled', count: 2 },
      { key: 'created', count: 6 },
      { key: 'exceptions_reset', count: 1 },
    ]);
  });
});

describe('action visibility (B3)', () => {
  it('offers row edits only before the start and in an editable state', () => {
    const [first, edited, skipped, cancelled, conflict] = OCCURRENCES;
    expect(canEditOccurrence(first!, NOW)).toBe(true);
    expect(canEditOccurrence(edited!, NOW)).toBe(true);
    expect(canEditOccurrence(conflict!, NOW)).toBe(true);
    expect(canEditOccurrence(skipped!, NOW)).toBe(false);
    expect(canEditOccurrence(cancelled!, NOW)).toBe(false);
    expect(canEditOccurrence({ ...first!, state: 'completed' }, NOW)).toBe(
      false,
    );
    // Started: its start is now or earlier.
    const start = Date.parse(first!.starts_at);
    expect(canEditOccurrence(first!, start)).toBe(false);
    expect(canEditOccurrence(first!, start - 60_000)).toBe(true);
  });

  it('hides every action without schedule.manage and once the series ended', () => {
    const open = seriesActions(SERIES_DETAIL, true, ZONE, NOW);
    expect([open.editSeries, open.cancel]).toEqual([true, true]);
    expect(open.editOccurrence(OCCURRENCES[0]!)).toBe(true);
    for (const actions of [
      seriesActions(SERIES_DETAIL, false, ZONE, NOW),
      seriesActions(ENDED_SERIES, true, ZONE, NOW),
      seriesActions(CANCELLED_SERIES, true, ZONE, NOW),
    ]) {
      expect([actions.editSeries, actions.cancel]).toEqual([false, false]);
      expect(actions.editOccurrence(OCCURRENCES[0]!)).toBe(false);
    }
  });

  it('treats a series cancelled before its next class as ended', () => {
    expect(isSeriesOver(SERIES_DETAIL, ZONE)).toBe(false);
    expect(isSeriesOver(ENDED_SERIES, ZONE)).toBe(true);
    expect(isSeriesOver(CANCELLED_SERIES, ZONE)).toBe(true);
    // Finite, with rows still to come beyond the materialized ones.
    expect(
      isSeriesOver(
        {
          ...SERIES_DETAIL,
          last_occurrence_on: '2031-06-30',
          occurrences: OCCURRENCES.map((o) => ({ ...o, state: 'cancelled' })),
        },
        ZONE,
      ),
    ).toBe(false);
  });
});

describe('the create form with "Repeats weekly" (A1-A3)', () => {
  it('keeps the one-off form until the switch is on', async () => {
    stub();
    app = await renderApp('/schedule/new');
    const toggle = main().querySelector<HTMLInputElement>('[role="switch"]')!;
    expect(toggle.checked).toBe(false);
    expect(fieldByLabel(main(), 'Date')).toBeTruthy();
    expect(main().textContent).not.toContain('Repeats on');

    typeInto(fieldByLabel(main(), 'Date'), '2031-01-08');
    await click(toggle);

    expect(fieldByLabel(main(), 'Starts on').value).toBe('2031-01-08');
    // Pre-checked with the start date's weekday (a Wednesday).
    expect(weekday(main(), 3).checked).toBe(true);
    expect(weekday(main(), 1).checked).toBe(false);
    const legend = [...main().querySelectorAll('fieldset > legend')].map(
      (l) => l.textContent,
    );
    expect(legend).toContain('Repeats on');
    expect(legend).toContain('Ends');
  });

  it('checks the rule on the client, then sends the series', async () => {
    const api = stub(MANAGER, {
      [CREATE]: () => jsonResponse(201, SERIES_CREATED),
    });
    app = await renderApp('/schedule/new');
    await fillSeriesForm();
    await click(weekday(main(), 1));
    await click(buttonNamed(main(), 'Schedule series'));
    expect(api.calls(CREATE)).toHaveLength(0);
    expect(main().textContent).toContain(
      'Choose at least one day of the week.',
    );

    await click(weekday(main(), 1));
    await click(weekday(main(), 3));
    choose(main(), 'Repeat', '2');
    await click(radioNamed(main(), 'On date'));
    typeInto(fieldByLabel(main(), 'End date'), '2031-03-31');
    await click(buttonNamed(main(), 'Schedule series'));

    expect(api.calls(CREATE)[0]!.body).toEqual({
      kind: 'individual',
      student_id: ANA.id,
      subject_id: SUBJECTS[0]!.id,
      tutor_user_id: null,
      classroom_id: null,
      starts_on: '2031-01-06',
      start_time: '10:00',
      duration_minutes: 60,
      recurrence: {
        weekdays: [1, 3],
        interval: 2,
        until: '2031-03-31',
        count: null,
      },
    });
    expect(main().textContent).toContain('Weekly series scheduled');
    expect(main().textContent).toContain(
      'Every 2 weeks on Monday and Wednesday, until Mar 31, 2031',
    );
    expect(main().textContent).toContain(
      '3 sessions created so far; later ones are added automatically. 1 of them is skipped for a center closure.',
    );
    const link = [...main().querySelectorAll('a')].find(
      (a) => a.textContent === 'Open the series',
    );
    expect(link?.getAttribute('href')).toBe(`/schedule/series/${SERIES_ID}`);
  });

  it('shows a 409 grouped by date; without schedule.override no override', async () => {
    stub(MANAGER, { [CREATE]: () => conflictResponse() });
    app = await renderApp('/schedule/new');
    await fillSeriesForm();
    await click(buttonNamed(main(), 'Schedule series'));

    const panel = main().querySelector('[role="alert"]')!;
    expect(panel.querySelector('h2')?.textContent).toBe(
      'The series can’t be scheduled: 3 conflicts',
    );
    expect(document.activeElement).toBe(panel.querySelector('h2'));
    expect([...panel.querySelectorAll('h3')].map((h) => h.textContent)).toEqual(
      ['January 6, 2031', 'January 15, 2031'],
    );
    const items = [...panel.querySelectorAll('li li')].map(
      (li) => li.textContent,
    );
    expect(items).toEqual([
      'Not allowedThe classroom is already in use at this time.',
      'Not allowedThe student already has a class at this time.',
      'Can be overriddenThe tutor is not available at this time (time off).',
    ]);
    expect(panel.textContent).not.toContain('Server detail');
    expect(main().textContent).not.toContain('Override and schedule');
    expect(main().textContent).toContain(
      'Only an Owner, or an Administrator with the override permission, can override these conflicts.',
    );
    expect(main().textContent).not.toContain('Suggested times');
    expect(fieldByLabel(main(), 'Start').value).toBe('10:00');
  });

  it('puts the 422 errors on the fields and the rule controls', async () => {
    stub(MANAGER, {
      [CREATE]: () =>
        invalid([
          { path: ['body', 'starts_on'], code: 'starts_on_in_past' },
          {
            path: ['body', 'recurrence', 'weekdays'],
            code: 'weekdays_duplicate',
          },
        ]),
    });
    app = await renderApp('/schedule/new');
    await fillSeriesForm();
    await click(buttonNamed(main(), 'Schedule series'));

    expect(fieldByLabel(main(), 'Starts on').getAttribute('aria-invalid')).toBe(
      'true',
    );
    expect(main().textContent).toContain(
      "The series can't start before today.",
    );
    expect(main().textContent).toContain(
      'Choose each day of the week only once.',
    );
    const group = weekday(main(), 1).closest('fieldset')!;
    const described = group.getAttribute('aria-describedby')!;
    expect(document.getElementById(described)?.textContent).toBe(
      'Choose each day of the week only once.',
    );
  });
});

describe('the series page (B1-B3)', () => {
  it('is behind schedule.view, and an unknown id is not found', async () => {
    stub({ ...TEST_USER, roles: ['tutor'], permissions: [] });
    app = await renderSeries();
    expect(main().textContent).not.toContain('Weekly series');
    app.unmount();

    stub(VIEWER, { [DETAIL]: () => problem(404, 'not_found') });
    app = await renderSeries();
    expect(main().querySelector('h1')?.textContent).toBe('Page not found');
  });

  it('shows the header, the rule and the rows, read-only without schedule.manage', async () => {
    stub(VIEWER);
    app = await renderSeries();
    const text = main().textContent;
    expect(main().querySelector('h1')?.textContent).toBe('Weekly series');
    expect(text).toContain('Ana López');
    expect(text).toContain('No tutor yet');
    expect(text).toContain('Aula 1');
    expect(text).toContain('10:00 AM–11:00 AM (America/Mexico_City)');
    expect(text).toContain(
      'Every week on Monday and Wednesday, 6 sessions in all',
    );
    const rows = [...main().querySelectorAll('ol > li')];
    expect(rows).toHaveLength(6);
    expect(rows[0]!.textContent).toContain(
      'January 6, 2031 · 10:00 AM–11:00 AM',
    );
    expect(rows[1]!.textContent).toContain('12:00 PM–1:00 PM');
    expect(rows[1]!.textContent).toContain('Edited');
    expect(rows[0]!.textContent).not.toContain('Edited');
    expect(rows[2]!.textContent).toContain('Skipped (closure)');
    // The state is text with an icon, never colour only.
    expect(rows[2]!.querySelector('svg[aria-hidden="true"]')).not.toBeNull();
    expect(main().querySelectorAll('button')).toHaveLength(0);
    expect(text).not.toContain('Ended');
  });

  it('shows the lineage links and Ended', async () => {
    stub(MANAGER, { [DETAIL]: () => jsonResponse(200, LINEAGE_SERIES) });
    app = await renderSeries();
    const links = [...main().querySelectorAll('a')].map((a) => [
      a.textContent,
      a.getAttribute('href'),
    ]);
    expect(links).toContainEqual([
      'Continues from an earlier series',
      '/schedule/series/00000000-0000-4000-8000-0000000007a0',
    ]);
    expect(links).toContainEqual([
      'Continued as a new series',
      `/schedule/series/${LATER_SERIES_ID}`,
    ]);
    app.unmount();

    stub(MANAGER, { [DETAIL]: () => jsonResponse(200, ENDED_SERIES) });
    app = await renderSeries();
    expect(main().textContent).toContain('Ended');
    expect(main().querySelectorAll('button')).toHaveLength(0);
  });

  it('offers the actions to schedule.manage on the editable rows only', async () => {
    stub(MANAGER);
    app = await renderSeries();
    const rows = [...main().querySelectorAll('ol > li')];
    const editable = rows.map((row) => row.querySelector('button') !== null);
    expect(editable).toEqual([true, true, false, false, true, true]);
    expect(rows[0]!.querySelector('button')!.getAttribute('aria-label')).toBe(
      'Edit this occurrence: January 6, 2031, 10:00 AM–11:00 AM',
    );
    expect(buttonNamed(main(), 'Edit series')).toBeTruthy();
    expect(buttonNamed(main(), 'Cancel future occurrences')).toBeTruthy();
  });
});

describe('the dialogs (C1-C4)', () => {
  async function openOccurrence(
    routes: Record<string, Responder | Responder[]>,
  ) {
    const api = stub(MANAGER, {
      [SESSION]: () => jsonResponse(200, OCCURRENCE_SESSION),
      ...routes,
    });
    app = await renderSeries();
    const trigger = main().querySelector<HTMLButtonElement>('ol > li button')!;
    trigger.focus();
    await click(trigger);
    return { api, trigger };
  }

  it('moves one occurrence with its version and instants, then refreshes', async () => {
    const { api } = await openOccurrence({
      [PATCH]: () => jsonResponse(200, { ...OCCURRENCE_SESSION, version: 3 }),
      [DETAIL]: [
        () => jsonResponse(200, SERIES_DETAIL),
        () =>
          jsonResponse(200, {
            ...SERIES_DETAIL,
            occurrences: [
              { ...OCCURRENCES[0]!, is_exception: true },
              ...OCCURRENCES.slice(1),
            ],
          }),
      ],
    });
    const dialog = openDialog()!;
    expect(dialog.querySelector('h2')?.textContent).toBe(
      'Edit this occurrence',
    );
    expect(fieldByLabel(dialog, 'Date').value).toBe('2031-01-06');
    expect(fieldByLabel(dialog, 'Start').value).toBe('10:00');
    typeInto(fieldByLabel(dialog, 'Start'), '12:00');
    typeInto(fieldByLabel(dialog, 'End'), '13:00');
    choose(dialog, 'Classroom', '');
    await click(buttonNamed(dialog, 'Save occurrence'));

    expect(api.calls(PATCH)[0]!.body).toEqual({
      version: 2,
      starts_at: '2031-01-06T18:00:00.000Z',
      ends_at: '2031-01-06T19:00:00.000Z',
      classroom_id: null,
    });
    expect(openDialog()).toBeNull();
    expect(main().textContent).toContain('The occurrence was moved.');
    const first = main().querySelector('ol > li')!;
    expect(first.textContent).toContain('Edited');
  });

  it('shows a conflict, a version conflict with a reload and a 409 message', async () => {
    const { api } = await openOccurrence({
      [PATCH]: [
        () => conflictResponse([SERIES_CONFLICTS[1]]),
        () => problem(409, 'version_conflict'),
        () => problem(409, 'session_not_editable'),
      ],
    });
    let dialog = openDialog()!;
    await click(buttonNamed(dialog, 'Save occurrence'));
    const panel = dialog.querySelector('[role="alert"]')!;
    expect(panel.textContent).toContain(
      'The occurrence can’t be moved: 1 conflict',
    );
    expect(panel.querySelectorAll('h3')).toHaveLength(0);

    await click(buttonNamed(dialog, 'Save occurrence'));
    expect(dialog.textContent).toContain(
      'This changed since you opened it. Reload it and try again.',
    );
    const before = api.calls(SESSION).length;
    await click(buttonNamed(dialog, 'Reload'));
    expect(api.calls(SESSION).length).toBeGreaterThan(before);

    dialog = openDialog()!;
    await click(buttonNamed(dialog, 'Save occurrence'));
    expect(openDialog()!.textContent).toContain(
      "This session can't be changed any more",
    );
  });

  it('refills the times, room and version from the session after a reload (QA D1)', async () => {
    const changed = {
      ...OCCURRENCE_SESSION,
      version: 5,
      starts_at: '2031-01-06T20:00:00Z',
      ends_at: '2031-01-06T21:00:00Z',
      classroom: null,
    };
    const { api } = await openOccurrence({
      [SESSION]: [
        () => jsonResponse(200, OCCURRENCE_SESSION),
        () => jsonResponse(200, changed),
      ],
      [PATCH]: [
        () => problem(409, 'version_conflict'),
        () => jsonResponse(200, { ...changed, version: 6 }),
      ],
    });
    let dialog = openDialog()!;
    expect(fieldByLabel(dialog, 'Start').value).toBe('10:00');
    await click(buttonNamed(dialog, 'Save occurrence'));
    await click(buttonNamed(dialog, 'Reload'));
    await settle();

    // Someone moved it to 14:00-15:00 without a room meanwhile.
    dialog = openDialog()!;
    expect(fieldByLabel(dialog, 'Date').value).toBe('2031-01-06');
    expect(fieldByLabel(dialog, 'Start').value).toBe('14:00');
    expect(fieldByLabel(dialog, 'End').value).toBe('15:00');
    const room = [...dialog.querySelectorAll('label')].find(
      (l) => l.textContent === 'Classroom',
    )!;
    expect(
      (document.getElementById(room.htmlFor) as HTMLSelectElement).value,
    ).toBe('');
    expect(dialog.textContent).not.toContain(
      'This changed since you opened it',
    );

    await click(buttonNamed(dialog, 'Save occurrence'));
    expect(api.calls(PATCH)[1]!.body).toEqual({
      version: 5,
      starts_at: '2031-01-06T20:00:00.000Z',
      ends_at: '2031-01-06T21:00:00.000Z',
      classroom_id: null,
    });
  });

  it('returns the focus to the trigger on Escape', async () => {
    const { trigger } = await openOccurrence({});
    expect(openDialog()).not.toBeNull();
    act(() => {
      (document.activeElement ?? document.body).dispatchEvent(
        new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }),
      );
    });
    await settle();
    expect(openDialog()).toBeNull();
    expect(document.activeElement).toBe(trigger);
  });

  it('edits the series from a date and opens the resulting series', async () => {
    const api = stub(MANAGER, {
      [EDIT]: () => jsonResponse(200, SERIES_EDITED),
      [`GET /api/v1/class-series/${LATER_SERIES_ID}`]: () =>
        jsonResponse(200, SPLIT_SERIES),
    });
    app = await renderSeries();
    await click(buttonNamed(main(), 'Edit series'));
    const dialog = openDialog()!;
    expect(dialog.textContent).toContain(
      'Past and completed classes are never changed. Edited occurrences in this range take the new times.',
    );
    expect(radioNamed(dialog, 'This and following occurrences').checked).toBe(
      true,
    );
    typeInto(fieldByLabel(dialog, 'From'), '2031-01-13');
    typeInto(fieldByLabel(dialog, 'Start'), '11:00');
    typeInto(fieldByLabel(dialog, 'End'), '12:00');
    // The COUNT series ends on its last occurrence.
    expect(radioNamed(dialog, 'On date').checked).toBe(true);
    expect(fieldByLabel(dialog, 'End date').value).toBe('2031-01-22');
    await click(buttonNamed(dialog, 'Save series'));

    expect(api.calls(EDIT)[0]!.body).toEqual({
      mode: 'following',
      from_date: '2031-01-13',
      version: 4,
      start_time: '11:00',
      duration_minutes: 60,
      classroom_id: CLASSROOMS[0]!.id,
      recurrence: {
        weekdays: [1, 3],
        interval: 1,
        until: '2031-01-22',
        count: null,
      },
    });
    await settle();
    expect(app.location()).toBe(`/schedule/series/${LATER_SERIES_ID}`);
    expect(main().textContent).toContain(
      'Series changed: 4 moved, 2 cancelled, 6 created, 1 edited occurrence reset.',
    );
    expect(main().textContent).toContain('Continues from an earlier series');
  });

  it('shows the edit errors as messages and on the fields', async () => {
    stub(MANAGER, {
      [EDIT]: [
        () => conflictResponse(),
        () => problem(409, 'series_offer_pending'),
        () =>
          invalid([
            { path: ['body', 'from_date'], code: 'from_date_in_past' },
            { path: ['body'], code: 'series_unchanged' },
          ]),
      ],
    });
    app = await renderSeries();
    await click(buttonNamed(main(), 'Edit series'));
    const dialog = openDialog()!;
    await click(buttonNamed(dialog, 'Save series'));
    expect(
      [...dialog.querySelectorAll('[role="alert"] h3')].map(
        (h) => h.textContent,
      ),
    ).toEqual(['January 6, 2031', 'January 15, 2031']);

    await click(buttonNamed(dialog, 'Save series'));
    expect(dialog.textContent).toContain(
      "The series' tutor offer is waiting for an answer.",
    );

    await click(buttonNamed(dialog, 'Save series'));
    expect(fieldByLabel(dialog, 'From').getAttribute('aria-invalid')).toBe(
      'true',
    );
    expect(dialog.textContent).toContain("The date can't be before today.");
    expect(dialog.textContent).toContain(
      "The new rule is the same as the series' and no occurrence was edited.",
    );
  });

  it('cancels the future occurrences with a reason and shows Ended', async () => {
    const api = stub(MANAGER, {
      [CANCEL]: () => jsonResponse(200, SERIES_CANCELLED),
      [DETAIL]: [
        () => jsonResponse(200, SERIES_DETAIL),
        () => jsonResponse(200, CANCELLED_SERIES),
      ],
    });
    app = await renderSeries();
    await click(buttonNamed(main(), 'Cancel future occurrences'));
    const dialog = openDialog()!;
    await click(radioNamed(dialog, 'All future occurrences'));
    await click(buttonNamed(dialog, 'Cancel occurrences'));
    expect(api.calls(CANCEL)).toHaveLength(0);
    expect(dialog.textContent).toContain('Give a reason.');

    const label = [...dialog.querySelectorAll('label')].find(
      (l) => l.textContent === 'Reason',
    )!;
    const area = document.getElementById(label.htmlFor) as HTMLTextAreaElement;
    // eslint-disable-next-line @typescript-eslint/unbound-method
    const setter = Object.getOwnPropertyDescriptor(
      HTMLTextAreaElement.prototype,
      'value',
    )?.set;
    act(() => {
      setter?.call(area, ' The family moved. ');
      area.dispatchEvent(new Event('input', { bubbles: true }));
    });
    expect(dialog.textContent).toContain('17 of 1000 characters');
    await click(buttonNamed(dialog, 'Cancel occurrences'));

    expect(api.calls(CANCEL)[0]!.body).toEqual({
      mode: 'all',
      from_date: null,
      reason: 'The family moved.',
      version: 4,
    });
    expect(openDialog()).toBeNull();
    expect(main().textContent).toContain('4 occurrences cancelled.');
    // The trigger is gone with the actions: the notice takes the focus.
    expect(document.activeElement?.textContent).toBe(
      '4 occurrences cancelled.',
    );
    expect(main().textContent).toContain('Ended');
    expect(main().querySelectorAll('button')).toHaveLength(0);
  });
});

describe('the calendar entry point (D2)', () => {
  it('links an occurrence of a series to its page from the event dialog', async () => {
    const fetchSeriesId = vi.fn(() => Promise.resolve(SERIES_ID));
    const event = item();
    const container = document.createElement('div');
    document.body.appendChild(container);
    const root = createRoot(container);
    const router = createMemoryRouter(
      [
        {
          path: '/',
          element: createElement(SessionDialog, {
            item: event,
            context: {
              t: i18n.getFixedT('en', 'common'),
              timeZone: ZONE,
              language: 'en',
            },
            onClose: () => {},
            onReturnFocus: () => {},
            fetchSeriesId,
          }),
        },
      ],
      { initialEntries: ['/'] },
    );
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    act(() =>
      root.render(
        createElement(
          QueryClientProvider,
          { client },
          createElement(RouterProvider, { router }),
        ),
      ),
    );
    await settle();

    expect(fetchSeriesId).toHaveBeenCalledWith(event.id, expect.anything());
    const link = [...openDialog()!.querySelectorAll('a')].find(
      (a) => a.textContent === 'Open series',
    );
    expect(link?.getAttribute('href')).toBe(`/schedule/series/${SERIES_ID}`);

    act(() => root.unmount());
    container.remove();
    client.clear();
  });
});

// --- #236: series overrides --------------------------------------------------

const OVERRIDER: SessionUserBody = {
  ...MANAGER,
  permissions: [...MANAGER.permissions, 'schedule.override'],
};
const OVERRIDE_ROUTE = `POST /api/v1/class-sessions/${OCCURRENCES[4]!.id}/conflict-overrides`;
const REASON = 'Domingos acordados con la familia';

/** The top-most dialog (the override dialog over "Edit series"). */
function topDialog(): HTMLElement {
  const all = document.querySelectorAll<HTMLElement>('[role="dialog"]');
  return all[all.length - 1]!;
}

function typeReason(value: string) {
  const dialog = topDialog();
  const label = [...dialog.querySelectorAll('label')].find(
    (l) => l.textContent === 'Reason',
  )!;
  const area = document.getElementById(label.htmlFor) as HTMLTextAreaElement;
  // eslint-disable-next-line @typescript-eslint/unbound-method
  const setter = Object.getOwnPropertyDescriptor(
    HTMLTextAreaElement.prototype,
    'value',
  )?.set;
  act(() => {
    setter?.call(area, value);
    area.dispatchEvent(new Event('input', { bubbles: true }));
  });
  return area;
}

function withOverrides<T extends { occurrences: unknown[] }>(
  series: T,
  overridden: number,
): T {
  return {
    ...series,
    occurrences: series.occurrences.map((o, index) => ({
      ...(o as object),
      overridden: index < overridden,
    })),
  };
}

describe('the series override rules (#236 H)', () => {
  it('pairs every soft conflict with its instant exactly as sent', () => {
    const offset = {
      ...SOFT_SERIES_CONFLICTS[0]!,
      details: {
        ...SOFT_SERIES_CONFLICTS[0]!.details,
        occurrence_start: '2031-01-12T10:00:00-06:00',
      },
    };
    const conflicts = [
      offset,
      ...SOFT_SERIES_CONFLICTS.slice(1),
      { ...SOFT_SERIES_CONFLICTS[1]! },
      ...SERIES_CONFLICTS,
    ];

    expect(seriesOverridePairs(conflicts)).toEqual([
      {
        occurrence_start: '2031-01-12T10:00:00-06:00',
        code: 'outside_operating_hours',
      },
      {
        occurrence_start: '2031-01-19T16:00:00Z',
        code: 'outside_operating_hours',
      },
      {
        occurrence_start: '2031-01-26T16:00:00Z',
        code: 'outside_operating_hours',
      },
      { occurrence_start: '2031-01-15T16:00:00Z', code: 'tutor_unavailable' },
    ]);
    expect(seriesOverride(SOFT_SERIES_CONFLICTS, `  ${REASON}  `)).toEqual({
      pairs: seriesOverridePairs(SOFT_SERIES_CONFLICTS),
      reason: REASON,
    });
    expect(affectedOccurrences(conflicts)).toBe(4);
    expect(overriddenCount(OVERRIDE_SERIES.occurrences)).toBe(1);
  });

  it('offers a conflict row override by permission, state, start and codes', () => {
    const row = OCCURRENCES[4]!;
    const holder = { canManage: true, canOverride: true };
    expect(conflictRowAction(row, holder, NOW)).toBe('override');
    expect(
      conflictRowAction(row, { canManage: true, canOverride: false }, NOW),
    ).toBeNull();
    expect(
      conflictRowAction(row, { canManage: false, canOverride: true }, NOW),
    ).toBeNull();
    expect(conflictRowAction(OCCURRENCES[0]!, holder, NOW)).toBeNull();
    expect(
      conflictRowAction(row, holder, Date.parse(row.starts_at)),
    ).toBeNull();
    for (const codes of [['tutor_overlap'], ['tutor_inactive'], []]) {
      expect(
        conflictRowAction({ ...row, conflict_codes: codes }, holder, NOW),
      ).toBe('fix');
    }
    expect(conflictOverrideBody({ ...row, version: 7 }, ` ${REASON} `)).toEqual(
      {
        version: 7,
        override: { codes: ['outside_operating_hours'], reason: REASON },
      },
    );
  });
});

describe('overriding a series create (#236 H1, H2)', () => {
  it('overrides every soft pair with a reason and says on how many', async () => {
    const api = stub(OVERRIDER, {
      [CREATE]: [
        () => conflictResponse(SOFT_SERIES_CONFLICTS),
        () => jsonResponse(201, withOverrides(SERIES_CREATED, 3)),
      ],
    });
    app = await renderApp('/schedule/new');
    await fillSeriesForm();
    await click(buttonNamed(main(), 'Schedule series'));
    const items = [...main().querySelectorAll('[role="alert"] li li')].map(
      (li) => li.textContent,
    );
    expect(items.every((text) => text?.startsWith('Can be overridden'))).toBe(
      true,
    );
    const action = buttonNamed(main(), 'Override and schedule');
    await click(action);

    const dialog = topDialog();
    expect(dialog.textContent).toContain(
      'The series is scheduled despite these rules on 3 occurrences.',
    );
    expect(
      [...dialog.querySelectorAll('h4')].map((h) => h.textContent),
    ).toEqual(['January 12, 2031', 'January 19, 2031', 'January 26, 2031']);
    await click(buttonNamed(dialog, 'Override and schedule'));
    expect(dialog.textContent).toContain('Give a reason for the override.');
    expect(api.calls(CREATE)).toHaveLength(1);

    typeReason(`  ${REASON} `);
    await click(buttonNamed(topDialog(), 'Override and schedule'));

    const body = api.calls(CREATE)[1]!.body as Record<string, unknown>;
    expect(body.override).toEqual({
      pairs: SOFT_SERIES_CONFLICTS.map((c) => ({
        occurrence_start: c.details.occurrence_start,
        code: 'outside_operating_hours',
      })),
      reason: REASON,
    });
    expect({ ...body, override: undefined }).toEqual({
      ...(api.calls(CREATE)[0]!.body as object),
      override: undefined,
    });
    expect(openDialog()).toBeNull();
    expect(main().textContent).toContain('Weekly series scheduled');
    expect(main().textContent).toContain(
      'Scheduled with overrides on 3 occurrences.',
    );
  });

  it('asks to fix a hard conflict first, and shows a new list after a 409', async () => {
    stub(OVERRIDER, {
      [CREATE]: [
        () => conflictResponse(SOFT_SERIES_CONFLICTS),
        () => conflictResponse(SERIES_CONFLICTS),
      ],
    });
    app = await renderApp('/schedule/new');
    await fillSeriesForm();
    await click(buttonNamed(main(), 'Schedule series'));
    await click(buttonNamed(main(), 'Override and schedule'));
    typeReason(REASON);
    await click(buttonNamed(topDialog(), 'Override and schedule'));
    await settle();

    expect(openDialog()).toBeNull();
    const panel = main().querySelector('[role="alert"]')!;
    expect(panel.querySelector('h2')?.textContent).toBe(
      'The series can’t be scheduled: 3 conflicts',
    );
    expect(document.activeElement).toBe(panel.querySelector('h2'));
    expect(main().textContent).toContain(
      'Fix the conflicts that aren’t allowed first.',
    );
    expect(main().textContent).not.toContain('Override and schedule');
  });

  it('keeps a 422 on the reason in the dialog and a 403 as an alert', async () => {
    stub(OVERRIDER, {
      [CREATE]: [
        () => conflictResponse(SOFT_SERIES_CONFLICTS),
        () =>
          invalid([
            { path: ['body', 'override', 'reason'], code: 'reason_too_long' },
          ]),
        () => problem(403, 'permission_denied'),
      ],
    });
    app = await renderApp('/schedule/new');
    await fillSeriesForm();
    await click(buttonNamed(main(), 'Schedule series'));
    await click(buttonNamed(main(), 'Override and schedule'));
    typeReason(REASON);
    await click(buttonNamed(topDialog(), 'Override and schedule'));
    expect(topDialog().textContent).toContain(
      'The reason can have at most 1000 characters.',
    );

    await click(buttonNamed(topDialog(), 'Override and schedule'));
    expect(topDialog().querySelector('[role="alert"]')).not.toBeNull();
    expect(openDialog()).not.toBeNull();
  });
});

describe('the override dialogs and the focus (#236 H4)', () => {
  it('returns the focus to the trigger on Escape', async () => {
    stub(OVERRIDER, {
      [CREATE]: () => conflictResponse(SOFT_SERIES_CONFLICTS),
      [DETAIL]: () => jsonResponse(200, OVERRIDE_SERIES),
    });
    app = await renderApp('/schedule/new');
    await fillSeriesForm();
    await click(buttonNamed(main(), 'Schedule series'));
    const action = buttonNamed(main(), 'Override and schedule');
    action.focus();
    await click(action);
    expect(openDialog()).not.toBeNull();
    await pressEscape();
    expect(openDialog()).toBeNull();
    expect(document.activeElement).toBe(action);

    app.unmount();
    app = await renderSeries();
    const row = main().querySelectorAll<HTMLElement>('ol > li')[4]!;
    const trigger = buttonNamed(row, 'Override and schedule');
    trigger.focus();
    await click(trigger);
    expect(openDialog()).not.toBeNull();
    await pressEscape();
    expect(openDialog()).toBeNull();
    expect(document.activeElement).toBe(trigger);
  });
});

describe('overriding a series edit and a conflict row (#236 H2, H3)', () => {
  it('resends the edit with every soft pair and says on how many', async () => {
    const edited = {
      ...SERIES_EDITED,
      series: withOverrides(SPLIT_SERIES, 2),
    };
    const api = stub(OVERRIDER, {
      [EDIT]: [
        () => conflictResponse(SOFT_SERIES_CONFLICTS),
        () => jsonResponse(200, edited),
      ],
      [`GET /api/v1/class-series/${LATER_SERIES_ID}`]: () =>
        jsonResponse(200, edited.series),
    });
    app = await renderSeries();
    await click(buttonNamed(main(), 'Edit series'));
    typeInto(fieldByLabel(openDialog()!, 'From'), '2031-01-13');
    await click(buttonNamed(openDialog()!, 'Save series'));
    await click(buttonNamed(openDialog()!, 'Override and schedule'));
    expect(document.querySelectorAll('[role="dialog"]')).toHaveLength(2);
    typeReason(REASON);
    await click(buttonNamed(topDialog(), 'Override and schedule'));
    await settle();

    const [first, second] = api.calls(EDIT);
    expect((second!.body as Record<string, unknown>).override).toEqual(
      seriesOverride(SOFT_SERIES_CONFLICTS, REASON),
    );
    expect({
      ...(second!.body as object),
      override: undefined,
    }).toEqual({ ...(first!.body as object), override: undefined });
    expect(app.location()).toBe(`/schedule/series/${LATER_SERIES_ID}`);
    expect(main().textContent).toContain(
      'Series changed: 4 moved, 2 cancelled, 6 created, 1 edited occurrence reset. Scheduled with overrides on 2 occurrences.',
    );
  });

  it('shows Overridden and the row action only to override holders', async () => {
    stub(MANAGER, { [DETAIL]: () => jsonResponse(200, OVERRIDE_SERIES) });
    app = await renderSeries();
    const rows = [...main().querySelectorAll('ol > li')];
    expect(rows[0]!.textContent).toContain('Overridden');
    expect(rows[4]!.textContent).toContain('Conflicts: Opening hours');
    expect(main().textContent).not.toContain('Override and schedule');
    expect(main().textContent).not.toContain('Fix this occurrence');
  });

  it('overrides a conflict row with its version and codes, then refreshes', async () => {
    const api = stub(OVERRIDER, {
      [DETAIL]: [
        () => jsonResponse(200, OVERRIDE_SERIES),
        () =>
          jsonResponse(200, {
            ...OVERRIDE_SERIES,
            occurrences: OVERRIDE_SERIES.occurrences.map((o, index) =>
              index === 4
                ? { ...o, state: 'unassigned', overridden: true, version: 2 }
                : o,
            ),
          }),
      ],
      [OVERRIDE_ROUTE]: [
        () => problem(409, 'version_conflict'),
        () => jsonResponse(200, { ...OCCURRENCE_SESSION, version: 2 }),
      ],
    });
    app = await renderSeries();
    const rows = [...main().querySelectorAll('ol > li')];
    expect(rows[6]!.textContent).toContain(
      'Fix this occurrence by editing it or the series, or reject it in the inbox.',
    );
    expect(
      [...main().querySelectorAll('button')].filter(
        (b) => b.textContent === 'Override and schedule',
      ),
    ).toHaveLength(1);
    await click(buttonNamed(rows[4] as HTMLElement, 'Override and schedule'));
    const dialog = topDialog();
    expect(dialog.textContent).toContain('Opening hours');
    typeReason(REASON);
    await click(buttonNamed(dialog, 'Override and schedule'));
    expect(dialog.textContent).toContain(
      'Someone else changed this record. Reload it and try again.',
    );
    expect(buttonNamed(dialog, 'Reload')).toBeTruthy();

    await click(buttonNamed(dialog, 'Override and schedule'));
    await settle();
    expect(api.calls(OVERRIDE_ROUTE)[1]!.body).toEqual({
      version: 1,
      override: { codes: ['outside_operating_hours'], reason: REASON },
    });
    expect(openDialog()).toBeNull();
    expect(main().textContent).toContain(
      'The occurrence was scheduled with an override.',
    );
    expect(main().querySelectorAll('ol > li')[4]!.textContent).toContain(
      'Overridden',
    );
  });
});
