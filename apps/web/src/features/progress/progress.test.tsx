import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { SessionUserBody } from '@/features/auth/session';
import { problemWith } from '@/features/academics/testing';
import {
  ADULT_ID,
  CHILD_ID,
  FAMILY_STUDENTS,
  SELF_ONLY,
  SIBLING_ID,
} from '@/features/academics/history/fixtures';
import { JOSE } from '@/features/students/fixtures';
import { applyLanguage } from '@/i18n';
import {
  meAs,
  problem,
  stubApi,
  TEST_USER,
  type ApiStub,
  type Routes,
} from '@/test/api-stub';
import { click, labelled, setFieldValue } from '@/test/dom';
import {
  buttonNamed,
  jsonResponse,
  renderApp,
  settleUntil,
  type RenderedApp,
} from '@/test/render-app';
import {
  EMPTY_LOG,
  EMPTY_PROGRESS,
  LOG_FIRST_PAGE,
  LOG_ITEMS,
  LOG_PAGE,
  LOG_SECOND_PAGE,
  MANY_SUBJECTS,
  MATH,
  MUSIC,
  NOTES_PAGE,
  PROGRESS,
  SCRIPT,
} from './fixtures';

// #322 B–F, G1 (pages): the family page (the picker, consent, 404, the
// range presets and the subject filter in the URL and the requests, the
// tiles with values and with nulls, the Chart/Table toggle and its table,
// each text summary, the empty states, more than 4 subjects, the homework
// series toggle, the class log with "Load more", summaries, badges, plain
// text and the lazy notes), the staff page (the name, links, the note
// count only, "Session details", the E2 former tutor and both 404s, a
// family-only user redirected), the 422 refusals with "Reset filters"
// (focus on the heading after the refetch), and no console report for the
// answers shown inline. "Today" is 2026-10-26 in Mexico City.

const FAMILY = 'GET /api/v1/schedule/family-students';
const FOREIGN_ID = '00000000-0000-4000-8000-000000322f99';
const progressOf = (id: string) => `GET /api/v1/students/${id}/progress`;
const logOf = (id: string) => `GET /api/v1/students/${id}/session-log`;
const notesOf = (id: string) => `GET /api/v1/students/${id}/learning-notes`;
const studentOf = (id: string) => `GET /api/v1/students/${id}`;

const user = (
  roles: SessionUserBody['roles'],
  permissions: SessionUserBody['permissions'],
): SessionUserBody => ({ ...TEST_USER, roles, permissions });
const GUARDIAN = user(['guardian'], ['student.view', 'guardian.view']);
const STUDENT_USER = user(['student'], ['student.view']);
const COORDINATOR = user(
  ['academic_coordinator'],
  ['student.view', 'evaluation.manage', 'schedule.view'],
);
const TUTOR = user(['tutor'], ['student.view', 'tutor.view']);
const FINANCE = user(['finance_staff'], ['student.view', 'schedule.view']);

let app: RenderedApp | undefined;
let api: ApiStub;
const main = () => app!.container.querySelector('main')!;
const text = () => main().textContent ?? '';
const heading = () => main().querySelector('h1');
const linkTo = (href: string) =>
  [...main().querySelectorAll('a')].find(
    (anchor) => anchor.getAttribute('href') === href,
  );
const figure = (name: string) =>
  [...main().querySelectorAll('figure')].find(
    (element) => element.querySelector('h2')?.textContent === name,
  )!;
const tile = (title: string) =>
  [...main().querySelectorAll('article')].find(
    (element) => element.querySelector('h3')?.textContent === title,
  )!;
const logArticles = () =>
  [...main().querySelectorAll('section article')].filter((element) =>
    ['Classes', 'Clases'].includes(
      element.closest('section')?.querySelector('h2')?.textContent ?? '',
    ),
  );

/** The query params of every request to `route`, in order. */
function paramsOf(route: string): URLSearchParams[] {
  const [method, path] = route.split(' ');
  return api.fetch.mock.calls
    .map(([request]) => request)
    .filter(
      (request) =>
        request.method === method && new URL(request.url).pathname === path,
    )
    .map((request) => new URL(request.url).searchParams);
}

const reports = () =>
  vi
    .mocked(console.error)
    .mock.calls.filter((call) => call[0] === '[classora]');

function familyRoutes(extra: Routes = {}): Routes {
  return {
    [FAMILY]: () => jsonResponse(200, FAMILY_STUDENTS),
    [progressOf(CHILD_ID)]: () => jsonResponse(200, PROGRESS),
    [logOf(CHILD_ID)]: () => jsonResponse(200, LOG_PAGE),
    [progressOf(SIBLING_ID)]: () =>
      jsonResponse(200, { ...EMPTY_PROGRESS, student_id: SIBLING_ID }),
    [logOf(SIBLING_ID)]: () => jsonResponse(200, EMPTY_LOG),
    [notesOf(CHILD_ID)]: () => jsonResponse(200, NOTES_PAGE),
    ...extra,
  };
}

async function open(
  path: string,
  routes: Routes = {},
  as: SessionUserBody = GUARDIAN,
) {
  api = stubApi({ 'GET /api/v1/auth/me': meAs(as), ...routes });
  app = await renderApp(path);
  await settleUntil(() => heading());
}

async function openFamily(path = '/my-progress', extra: Routes = {}) {
  await open(path, familyRoutes(extra));
  await settleUntil(
    () =>
      main().querySelector('figure') ||
      text().includes("isn't available") ||
      text().includes("hasn't shared") ||
      main().querySelector('[role="alert"]'),
  );
}

function staffRoutes(extra: Routes = {}): Routes {
  return {
    [studentOf(CHILD_ID)]: () => jsonResponse(200, JOSE),
    [progressOf(CHILD_ID)]: () => jsonResponse(200, PROGRESS),
    [logOf(CHILD_ID)]: () => jsonResponse(200, LOG_PAGE),
    ...extra,
  };
}

async function openStaff(
  as: SessionUserBody = COORDINATOR,
  extra: Routes = {},
  path = `/students/${CHILD_ID}/progress`,
) {
  await open(path, staffRoutes(extra), as);
}

beforeEach(() => {
  applyLanguage('en');
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(new Date('2026-10-26T18:00:00Z'));
  vi.spyOn(console, 'error').mockImplementation(() => {});
  vi.spyOn(console, 'warn').mockImplementation(() => {});
});

afterEach(() => {
  app?.unmount();
  app = undefined;
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  document.body.innerHTML = '';
});

describe('the family page: student and access (A2, B4, D1)', () => {
  it('the h1, the picker defaulting to the first student, the family links', async () => {
    await openFamily();
    expect(heading()!.textContent).toBe("Lucía Pérez's progress");
    expect(document.title).toContain("Lucía Pérez's progress");
    expect(labelled<HTMLSelectElement>(main(), 'Whose progress').value).toBe(
      CHILD_ID,
    );
    for (const href of [
      `/my-history?student=${CHILD_ID}`,
      `/my-learning-plans?student=${CHILD_ID}`,
      `/my-homework?student=${CHILD_ID}`,
      `/my-progress-reports?student=${CHILD_ID}`,
    ]) {
      expect(linkTo(href), href).toBeDefined();
    }
  });

  it('?student= picks; the picker keeps the period and drops the subject', async () => {
    await openFamily(`/my-progress?student=${SIBLING_ID}`);
    await settleUntil(() => text().includes('No classes in this period.'));
    expect(heading()!.textContent).toBe("Andrés Pérez's progress");
    expect(paramsOf(progressOf(CHILD_ID))).toHaveLength(0);
    void app!.router.navigate(
      `/my-progress?student=${SIBLING_ID}&months=6&subject=${MATH.id}`,
    );
    await settleUntil(() =>
      paramsOf(progressOf(SIBLING_ID)).some(
        (params) => params.get('subject_id') === MATH.id,
      ),
    );
    setFieldValue(
      labelled<HTMLSelectElement>(main(), 'Whose progress'),
      CHILD_ID,
    );
    await settleUntil(
      () => app!.location() === `/my-progress?student=${CHILD_ID}&months=6`,
    );
    await settleUntil(() => paramsOf(progressOf(CHILD_ID)).length > 0);
    expect(paramsOf(progressOf(CHILD_ID))[0]!.get('from')).toBe('2026-05-01');
    expect(heading()!.textContent).toBe("Lucía Pérez's progress");
  });

  it('a foreign ?student= falls back to the first, with no request for it', async () => {
    await openFamily(`/my-progress?student=${FOREIGN_ID}`);
    expect(paramsOf(progressOf(FOREIGN_ID))).toHaveLength(0);
    expect(paramsOf(logOf(FOREIGN_ID))).toHaveLength(0);
    expect(paramsOf(progressOf(CHILD_ID))).toHaveLength(1);
    expect(heading()!.textContent).toBe("Lucía Pérez's progress");
  });

  it("an adult who hasn't shared: the consent message and no request", async () => {
    await openFamily(`/my-progress?student=${ADULT_ID}`);
    expect(text()).toContain("hasn't shared their academic records");
    expect(heading()!.textContent).toBe("Mateo Pérez's progress");
    expect(paramsOf(progressOf(ADULT_ID))).toHaveLength(0);
    expect(paramsOf(logOf(ADULT_ID))).toHaveLength(0);
    // The picker stays usable.
    expect(labelled<HTMLSelectElement>(main(), 'Whose progress')).toBeTruthy();
  });

  it('one entry (the student themselves): the h1 "My progress", no picker or whose line', async () => {
    const self = SELF_ONLY.items[0]!.student_id;
    await open(
      '/my-progress',
      {
        [FAMILY]: () => jsonResponse(200, SELF_ONLY),
        [progressOf(self)]: () => jsonResponse(200, PROGRESS),
        [logOf(self)]: () => jsonResponse(200, LOG_PAGE),
      },
      STUDENT_USER,
    );
    await settleUntil(() => main().querySelector('figure'));
    expect(heading()!.textContent).toBe('My progress');
    expect(document.title).toContain('My progress');
    expect(text()).not.toContain('Whose progress');
    expect(
      [...main().querySelectorAll('label')].map((label) => label.textContent),
    ).not.toContain('Whose progress');
  });

  it('no family students: the message', async () => {
    await open('/my-progress', {
      [FAMILY]: () => jsonResponse(200, { items: [] }),
    });
    await settleUntil(() =>
      text().includes("There's no student linked to your account."),
    );
    expect(heading()!.textContent).toBe('Progress');
  });

  it('a 403 consent: the consent message, not reported', async () => {
    await openFamily('/my-progress', {
      [progressOf(CHILD_ID)]: () => problem(403, 'student_consent_required'),
    });
    expect(text()).toContain("hasn't shared their academic records");
    expect(reports()).toEqual([]);
  });

  it('a 404: "This progress page isn\'t available." inline, not reported; the picker stays', async () => {
    await openFamily('/my-progress', {
      [progressOf(CHILD_ID)]: () => problem(404, 'not_found'),
      [logOf(CHILD_ID)]: () => problem(404, 'not_found'),
    });
    expect(text()).toContain("This progress page isn't available.");
    expect(labelled<HTMLSelectElement>(main(), 'Whose progress')).toBeTruthy();
    expect(reports()).toEqual([]);
  });

  it('an unexpected 500 is reported and offers "Try again"', async () => {
    await openFamily('/my-progress', {
      // The first try and both retries (5xx retries, #28) fail.
      [progressOf(CHILD_ID)]: [
        () => problem(500, 'internal_error'),
        () => problem(500, 'internal_error'),
        () => problem(500, 'internal_error'),
        () => jsonResponse(200, PROGRESS),
      ],
    });
    await settleUntil(() => main().querySelector('[role="alert"]'), 15_000);
    expect(reports().length).toBeGreaterThan(0);
    await click(buttonNamed(main(), 'Try again'));
    await settleUntil(() => main().querySelector('figure'));
  });
});

describe('the range presets and the subject filter (B1–B3)', () => {
  it('12 months by default: from the first of the month 11 months back to today, in the center zone', async () => {
    await openFamily();
    const [summary] = paramsOf(progressOf(CHILD_ID));
    expect(summary!.get('from')).toBe('2025-11-01');
    expect(summary!.get('to')).toBe('2026-10-26');
    expect(summary!.has('subject_id')).toBe(false);
    const [log] = paramsOf(logOf(CHILD_ID));
    expect(log!.get('from')).toBe('2025-11-01');
    expect(log!.get('to')).toBe('2026-10-26');
    expect(log!.get('limit')).toBe('20');
    const twelve = buttonNamed(main(), '12 months');
    expect(twelve.getAttribute('aria-pressed')).toBe('true');
    expect(
      twelve.closest('[role="group"]')!.getAttribute('aria-labelledby'),
    ).toBeTruthy();
    expect(text()).toContain('From Nov 1, 2025 to Oct 26, 2026');
    expect(text()).not.toContain('Clear filters');
  });

  it.each([
    ['3 months', '3', '2026-08-01'],
    ['6 months', '6', '2026-05-01'],
    ['24 months', '24', '2024-11-01'],
  ])(
    '%s → ?months=%s and from=%s on both reads',
    async (name, months, from) => {
      await openFamily();
      const button = buttonNamed(main(), name);
      await click(button);
      await settleUntil(() =>
        paramsOf(progressOf(CHILD_ID)).some((p) => p.get('from') === from),
      );
      expect(app!.location()).toBe(`/my-progress?months=${months}`);
      expect(
        paramsOf(logOf(CHILD_ID)).some((p) => p.get('from') === from),
      ).toBe(true);
      expect(button.getAttribute('aria-pressed')).toBe('true');
      // The controls stay mounted while the new range loads (the same
      // button, so keyboard focus isn't lost).
      expect(button.isConnected).toBe(true);
    },
  );

  it('a stored invalid ?months= falls back to 12', async () => {
    await openFamily('/my-progress?months=7');
    expect(paramsOf(progressOf(CHILD_ID))[0]!.get('from')).toBe('2025-11-01');
  });

  it('the subject filter offers AM1 subjects (a class-only one too) and goes to the URL and both reads', async () => {
    await openFamily();
    const select = labelled<HTMLSelectElement>(main(), 'Subject');
    expect([...select.options].map((option) => option.textContent)).toEqual([
      'All subjects',
      'Física',
      'Matemáticas',
      'Música',
    ]);
    setFieldValue(select, MUSIC.id);
    await settleUntil(
      () => app!.location() === `/my-progress?subject=${MUSIC.id}`,
    );
    await settleUntil(() =>
      paramsOf(logOf(CHILD_ID)).some((p) => p.get('subject_id') === MUSIC.id),
    );
    expect(
      paramsOf(progressOf(CHILD_ID)).some(
        (p) => p.get('subject_id') === MUSIC.id,
      ),
    ).toBe(true);
  });

  it('an unknown ?subject= (not an id) is ignored: no subject_id is sent', async () => {
    await openFamily('/my-progress?subject=maths');
    expect(paramsOf(progressOf(CHILD_ID))[0]!.has('subject_id')).toBe(false);
    expect(paramsOf(logOf(CHILD_ID))[0]!.has('subject_id')).toBe(false);
  });

  it('"Clear filters" goes back to the defaults and focuses the h1 after the refetch', async () => {
    await openFamily(`/my-progress?months=3&subject=${MATH.id}`);
    await click(buttonNamed(main(), 'Clear filters'));
    await settleUntil(() => app!.location() === '/my-progress');
    await settleUntil(() => document.activeElement === heading());
    expect(text()).not.toContain('Clear filters');
  });
});

describe('the 422 refusals (D1)', () => {
  const field = (code: string, at: string) => () =>
    problemWith(422, 'validation_error', {
      errors: [{ path: ['query', at], code }],
    });

  it.each([
    ['range_in_future', 'to', "The period can't end after today."],
    ['range_too_long', 'from', 'Choose a period of at most 24 months.'],
    ['range_invalid', 'from', 'The end of the range must be after its start.'],
    ['subject_unknown', 'subject_id', 'Choose an existing subject.'],
  ])(
    '%s: the mapped message and "Reset filters", not reported',
    async (code, at, message) => {
      await openFamily(`/my-progress?months=3&subject=${MATH.id}`, {
        [progressOf(CHILD_ID)]: [
          field(code, at),
          () => jsonResponse(200, PROGRESS),
        ],
      });
      await settleUntil(() => main().querySelector('[role="alert"]'));
      expect(main().querySelector('[role="alert"]')!.textContent).toContain(
        message,
      );
      expect(reports()).toEqual([]);
      await click(buttonNamed(main(), 'Reset filters'));
      await settleUntil(() => app!.location() === '/my-progress');
      await settleUntil(() => main().querySelector('figure'));
      await settleUntil(() => document.activeElement === heading());
    },
  );
});

describe('the tiles (C1)', () => {
  it('show the API text and integer counts', async () => {
    await openFamily();
    const attendance = tile('Attendance').textContent;
    expect(attendance).toContain('66.67 %');
    expect(attendance).toContain('6 of 8 classes attended');
    expect(attendance).toContain('1 not recorded');
    expect(tile('Evaluations').textContent).toContain('average 84.17 %');
    expect(tile('Evaluations').textContent).toContain('3 evaluations');
    expect(tile('Homework grades').textContent).toContain('average 75.00 %');
    expect(tile('Homework grades').textContent).toContain(
      '2 graded in this period',
    );
    const completion = tile('Homework completion').textContent;
    expect(completion).toContain('Homework due in this period');
    expect(completion).toContain('75.00 %');
    expect(completion).toContain('1 missing · 1 late · 1 pending');
    const objectives = tile('Learning objectives').textContent;
    expect(objectives).toContain('3 of 5 objectives complete');
    expect(objectives).toContain('1 overdue milestone');
  });

  it('every null percentage reads "No data yet"; the counts still show', async () => {
    await openFamily('/my-progress', {
      [progressOf(CHILD_ID)]: () => jsonResponse(200, EMPTY_PROGRESS),
    });
    for (const title of [
      'Attendance',
      'Evaluations',
      'Homework grades',
      'Homework completion',
    ]) {
      expect(tile(title).textContent, title).toContain('No data yet');
      expect(tile(title).textContent, title).not.toContain('%');
    }
    expect(tile('Attendance').textContent).toContain('0 of 0 classes attended');
    expect(tile('Attendance').textContent).not.toContain('not recorded');
    expect(tile('Evaluations').textContent).toContain('0 evaluations');
    expect(tile('Learning objectives').textContent).not.toContain('overdue');
  });
});

describe('the charts (C2, C3)', () => {
  it('attendance: the summary, Chart by default, the Table with every value as text', async () => {
    await openFamily();
    const attendance = figure('Attendance by month');
    expect(
      attendance.querySelector(
        `#${attendance.getAttribute('aria-describedby')!.replace(/:/g, '\\:')}`,
      )?.textContent,
    ).toBe(
      'From Aug 1, 2026 to Oct 26, 2026: 5 present, 1 late, 2 absent and 1 not recorded; attendance 66.67 %.',
    );
    const chartButton = buttonNamed(attendance, 'Chart');
    const tableButton = buttonNamed(attendance, 'Table');
    expect(chartButton.getAttribute('aria-pressed')).toBe('true');
    expect(tableButton.getAttribute('aria-pressed')).toBe('false');
    expect(attendance.querySelector('table')).toBeNull();
    expect(
      [...attendance.querySelectorAll('li')].map((li) => li.textContent),
    ).toEqual(['Present', 'Late', 'Absent', 'Not recorded']);
    await click(tableButton);
    expect(tableButton.getAttribute('aria-pressed')).toBe('true');
    const table = attendance.querySelector('table')!;
    expect(table.querySelector('caption')!.textContent).toBe(
      'Attendance by month',
    );
    expect(
      [...table.querySelectorAll('thead th')].map((th) => [
        th.textContent,
        th.getAttribute('scope'),
      ]),
    ).toEqual([
      ['Month', 'col'],
      ['Present', 'col'],
      ['Late', 'col'],
      ['Absent', 'col'],
      ['Not recorded', 'col'],
      ['Total', 'col'],
    ]);
    const rows = [...table.querySelectorAll('tbody tr')].map((row) =>
      [...row.children].map((cell) => cell.textContent),
    );
    expect(rows).toEqual([
      ['Aug 2026', '1', '0', '1', '0', '2'],
      ['Sep 2026', '2', '1', '0', '0', '3'],
      ['Oct 2026', '2', '0', '1', '1', '4'],
    ]);
    expect(table.querySelector('tbody th')!.getAttribute('scope')).toBe('row');
    const region = table.closest('[role="region"]')!;
    expect(region.getAttribute('tabindex')).toBe('0');
    expect(region.getAttribute('aria-labelledby')).toBe(
      table.querySelector('caption')!.id,
    );
  });

  it('grade trend: the summary and the table in API order, as the API text', async () => {
    await openFamily();
    const grades = figure('Grade trend');
    expect(grades.textContent).toContain(
      'Evaluation average 84.17 % over 3 evaluations; homework average 75.00 % over 2 grades.',
    );
    await click(buttonNamed(grades, 'Table'));
    const rows = [...grades.querySelectorAll('tbody tr')].map((row) =>
      [...row.children].map((cell) => cell.textContent),
    );
    expect(rows).toEqual([
      ['Aug 2026', 'Física', '87.50 %', '1', 'No data yet', '0'],
      ['Sep 2026', 'Matemáticas', '70.00 %', '1', '70.00 %', '1'],
      ['Oct 2026', 'Matemáticas', 'No data yet', '0', '80.00 %', '1'],
      ['Oct 2026', 'Física', '95.00 %', '1', 'No data yet', '0'],
    ]);
    expect(grades.querySelector('caption')!.textContent).toBe(
      'Average by month and subject',
    );
  });

  it('grade trend: "Show homework averages" adds the homework series (aria-pressed)', async () => {
    await openFamily();
    const grades = figure('Grade trend');
    const legend = () =>
      [...grades.querySelectorAll('ul[aria-label="Legend"] li')].map(
        (li) => li.textContent,
      );
    expect(legend()).toEqual([
      'Física: evaluations',
      'Matemáticas: evaluations',
    ]);
    const toggle = buttonNamed(grades, 'Show homework averages');
    expect(toggle.getAttribute('aria-pressed')).toBe('false');
    await click(toggle);
    expect(toggle.getAttribute('aria-pressed')).toBe('true');
    expect(legend()).toEqual([
      'Física: evaluations',
      'Física: homework',
      'Matemáticas: evaluations',
      'Matemáticas: homework',
    ]);
  });

  it('more than 4 subjects: the chart view asks for one; the table has everything', async () => {
    await openFamily('/my-progress', {
      [progressOf(CHILD_ID)]: () => jsonResponse(200, MANY_SUBJECTS),
    });
    const grades = figure('Grade trend');
    expect(grades.textContent).toContain(
      'Choose a subject to see its trend chart.',
    );
    expect(grades.textContent).not.toContain('Show homework averages');
    await click(buttonNamed(grades, 'Table'));
    expect(grades.querySelectorAll('tbody tr')).toHaveLength(5);
  });

  it('no data: text empty states, no toggle, no chart', async () => {
    await openFamily('/my-progress', {
      [progressOf(CHILD_ID)]: () => jsonResponse(200, EMPTY_PROGRESS),
      [logOf(CHILD_ID)]: () => jsonResponse(200, EMPTY_LOG),
    });
    const attendance = figure('Attendance by month');
    expect(attendance.textContent).toContain('No classes in this period.');
    expect(attendance.querySelector('button')).toBeNull();
    expect(attendance.textContent).toContain('attendance no data yet.');
    const grades = figure('Grade trend');
    expect(grades.textContent).toContain('No grades in this period.');
    expect(grades.textContent).toContain(
      'Evaluation average no data yet over 0 evaluations; homework average no data yet over 0 grades.',
    );
    expect(text()).toContain('No learning plans yet.');
    expect(text()).toContain('No classes in this period.');
  });

  it('the learning plans: summary, badges, meter text, overdue, target dates', async () => {
    await openFamily();
    expect(text()).toContain(
      '1 active and 1 completed plans; 3 of 5 objectives complete.',
    );
    const plans = [...main().querySelectorAll('section')].find(
      (section) =>
        section.querySelector('h2')?.textContent === 'Learning plans',
    )!;
    const items = [...plans.querySelectorAll('li')];
    expect(items[0]!.textContent).toContain('Fracciones y decimales');
    expect(items[0]!.textContent).toContain('Active');
    expect(items[0]!.textContent).toContain('2 of 4 milestones done · 50 %');
    expect(items[0]!.textContent).toContain('1 overdue');
    expect(items[0]!.textContent).toContain('Target date: Dec 15, 2026');
    expect(items[1]!.textContent).toContain('Completed');
    expect(items[1]!.textContent).toContain('No target date');
    expect(
      plans.querySelector('progress')!.getAttribute('aria-labelledby'),
    ).toBeTruthy();
  });
});

describe('the class log (C4, C5)', () => {
  it('items: date and time in the center zone, subject, kind, tutor, the badge', async () => {
    await openFamily();
    await settleUntil(() => logArticles().length === 4);
    const [first, second, third, fourth] = logArticles();
    expect(first!.querySelector('h3')!.textContent).toBe(
      'Oct 20, 2026, 4:00 PM',
    );
    expect(first!.textContent).toContain('Matemáticas');
    expect(first!.textContent).toContain('Individual');
    expect(first!.textContent).toContain('with Carmen Ruiz');
    expect(first!.textContent).toContain('Present');
    expect(second!.textContent).toContain('Group');
    expect(second!.textContent).toContain('Late');
    expect(third!.textContent).toContain('Course');
    expect(third!.textContent).toContain('Absent');
    expect(third!.textContent).not.toContain('with ');
    expect(fourth!.textContent).toContain('Not recorded');
  });

  it('the approved summary as a labelled list; null fields omitted; null summary', async () => {
    await openFamily();
    await settleUntil(() => logArticles().length === 4);
    const [first, second] = logArticles();
    const terms = [...first!.querySelectorAll('dt')].map(
      (dt) => dt.textContent,
    );
    expect(terms).toEqual(['Topics', 'Performance', 'Next steps']);
    expect(first!.querySelector('dd')!.textContent).toBe(
      'Fracciones equivalentes',
    );
    expect(second!.textContent).toContain('Summary not available yet.');
    expect(second!.querySelector('dl')).toBeNull();
  });

  it('a summary is plain text: a <script> shows literally', async () => {
    await openFamily();
    await settleUntil(() => logArticles().length === 4);
    const third = logArticles()[2]!;
    expect(third.querySelector('dd')!.textContent).toBe(SCRIPT);
    expect(third.querySelector('script')).toBeNull();
  });

  it('"Load more" follows next_cursor and focuses the first new item', async () => {
    await openFamily('/my-progress', {
      [logOf(CHILD_ID)]: (request) =>
        jsonResponse(
          200,
          new URL(request.url).searchParams.get('cursor') === 'cursor-2'
            ? LOG_SECOND_PAGE
            : LOG_FIRST_PAGE,
        ),
    });
    await settleUntil(() => logArticles().length === 2);
    await click(buttonNamed(main(), 'Load more'));
    await settleUntil(() => logArticles().length === 4);
    const cursors = paramsOf(logOf(CHILD_ID)).map((p) => p.get('cursor'));
    expect(cursors).toEqual([null, 'cursor-2']);
    await settleUntil(() => document.activeElement === logArticles()[2]);
    expect(text()).not.toContain('Load more');
  });

  it('families: "Notes from the tutor (n)" loads the notes only when first opened', async () => {
    await openFamily();
    await settleUntil(() => logArticles().length === 4);
    expect(paramsOf(notesOf(CHILD_ID))).toHaveLength(0);
    const toggle = buttonNamed(main(), 'Notes from the tutor (2)');
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
    expect(
      document.getElementById(toggle.getAttribute('aria-controls')!)!.hidden,
    ).toBe(true);
    await click(toggle);
    await settleUntil(() => text().includes('Muy buena participación.'));
    expect(toggle.getAttribute('aria-expanded')).toBe('true');
    const [request] = paramsOf(notesOf(CHILD_ID));
    expect(request!.get('class_session_id')).toBe(
      LOG_ITEMS[0]!.class_session_id,
    );
    // Plain text.
    expect(text()).toContain(SCRIPT);
    expect(main().querySelector('script')).toBeNull();
    // Closing and reopening uses the loaded notes.
    await click(toggle);
    await click(toggle);
    expect(paramsOf(notesOf(CHILD_ID))).toHaveLength(1);
  });

  it('families: a notes failure shows an inline error with "Try again"', async () => {
    await openFamily('/my-progress', {
      [notesOf(CHILD_ID)]: [
        () => problem(400, 'bad_request'),
        () => jsonResponse(200, NOTES_PAGE),
      ],
    });
    await settleUntil(() => logArticles().length === 4);
    await click(buttonNamed(main(), 'Notes from the tutor (2)'));
    await settleUntil(() => logArticles()[0]!.querySelector('[role="alert"]'));
    await click(buttonNamed(logArticles()[0]!, 'Try again'));
    await settleUntil(() => text().includes('Muy buena participación.'));
  });

  it('the log fails on its own: an inline error with "Try again"', async () => {
    await openFamily('/my-progress', {
      [logOf(CHILD_ID)]: [
        () => problem(400, 'bad_request'),
        () => jsonResponse(200, LOG_PAGE),
      ],
    });
    await settleUntil(() =>
      [...main().querySelectorAll('section')].some(
        (s) =>
          s.querySelector('h2')?.textContent === 'Classes' &&
          s.querySelector('[role="alert"]'),
      ),
    );
    expect(figure('Attendance by month')).toBeDefined();
    const classes = [...main().querySelectorAll('section')].find(
      (s) => s.querySelector('h2')?.textContent === 'Classes',
    )!;
    await click(buttonNamed(classes, 'Try again'));
    await settleUntil(() => logArticles().length === 4);
  });
});

describe('the staff and tutor page (A2, B6, C4, C5, D1, E2, F1)', () => {
  it('a Coordinator: the name, Back to student, links, the count only, Session details', async () => {
    await openStaff();
    await settleUntil(() => logArticles().length === 4);
    expect(heading()!.textContent).toBe('Progress: José Ñúñez Pérez');
    expect(linkTo(`/students/${CHILD_ID}`)?.textContent).toBe(
      'Back to student',
    );
    expect(linkTo(`/students/${CHILD_ID}/history`)).toBeDefined();
    expect(text()).toContain('2 published notes');
    expect(text()).not.toContain('Notes from the tutor');
    expect(paramsOf(notesOf(CHILD_ID))).toHaveLength(0);
    expect(
      linkTo(`/schedule/sessions/${LOG_ITEMS[0]!.class_session_id}`)
        ?.textContent,
    ).toBe('Session details');
  });

  it('a tutor: the tutor links, no "Session details" without schedule.view', async () => {
    await openStaff(TUTOR);
    await settleUntil(() => logArticles().length === 4);
    for (const href of [
      `/students/${CHILD_ID}/history`,
      `/students/${CHILD_ID}/learning-plans`,
      `/students/${CHILD_ID}/assignments`,
      `/students/${CHILD_ID}/progress-reports`,
    ]) {
      expect(linkTo(href), href).toBeDefined();
    }
    expect(text()).not.toContain('Session details');
  });

  it('E2: progress 404 with the log 200 → the former tutor view', async () => {
    await openStaff(TUTOR, {
      [studentOf(CHILD_ID)]: () => problem(404, 'not_found'),
      [progressOf(CHILD_ID)]: () => problem(404, 'not_found'),
    });
    await settleUntil(() =>
      text().includes('You can see the classes you taught'),
    );
    expect(heading()!.textContent).toBe('Progress');
    expect(main().querySelector('[role="status"]')!.textContent).toBe(
      "You can see the classes you taught this student. The full progress page is for the student's current tutors.",
    );
    await settleUntil(() => logArticles().length === 4);
    expect(buttonNamed(main(), '12 months')).toBeTruthy();
    expect(main().querySelector('figure')).toBeNull();
    expect(text()).not.toContain('Subject');
    expect(linkTo(`/students/${CHILD_ID}/history`)).toBeUndefined();
    expect(text()).not.toContain('Back to student');
    expect(text()).not.toContain('Notes from the tutor');
    expect(reports()).toEqual([]);
  });

  it('E2: both 404 → "This progress page isn\'t available."', async () => {
    await openStaff(TUTOR, {
      [studentOf(CHILD_ID)]: () => problem(404, 'not_found'),
      [progressOf(CHILD_ID)]: () => problem(404, 'not_found'),
      [logOf(CHILD_ID)]: () => problem(404, 'not_found'),
    });
    await settleUntil(() =>
      text().includes("This progress page isn't available."),
    );
    expect(text()).not.toContain('You can see the classes');
    expect(reports()).toEqual([]);
  });

  it.each([
    ['a guardian', GUARDIAN],
    ['a student', STUDENT_USER],
  ] as const)(
    '%s is sent to /my-progress?student=, keeping the period',
    async (_who, as) => {
      await open(`/students/${CHILD_ID}/progress?months=6`, familyRoutes(), as);
      await settleUntil(
        () => app!.location() === `/my-progress?months=6&student=${CHILD_ID}`,
      );
      expect(api.calls(studentOf(CHILD_ID))).toHaveLength(0);
    },
  );

  it('Finance: access-denied on both routes, no progress request', async () => {
    for (const path of [`/students/${CHILD_ID}/progress`, '/my-progress']) {
      await open(path, staffRoutes(), FINANCE);
      expect(heading()!.textContent).toBe("You don't have access to this page");
      expect(api.calls(progressOf(CHILD_ID))).toHaveLength(0);
      expect(api.calls(logOf(CHILD_ID))).toHaveLength(0);
      app!.unmount();
      app = undefined;
    }
  });

  it('a tutor-only user on /my-progress: access-denied, no request', async () => {
    await open('/my-progress', familyRoutes(), TUTOR);
    expect(heading()!.textContent).toBe("You don't have access to this page");
    expect(api.calls(FAMILY)).toHaveLength(0);
  });
});

describe('Spanish (F2)', () => {
  it('uses the catalog words', async () => {
    applyLanguage('es');
    await openFamily();
    expect(heading()!.textContent).toBe('Progreso de Lucía Pérez');
    expect(
      labelled<HTMLSelectElement>(main(), 'De quién es el progreso').value,
    ).toBe(CHILD_ID);
    expect(tile('Asistencia').textContent).toContain(
      '6 de 8 clases con asistencia',
    );
    await settleUntil(() => logArticles().length === 4);
    expect(logArticles()[1]!.textContent).toContain('Retardo');
    expect(logArticles()[3]!.textContent).toContain('Sin registrar');
    expect(buttonNamed(main(), '12 meses')).toBeTruthy();
  });

  it('one entry (the student themselves): "Mi progreso", no whose line', async () => {
    applyLanguage('es');
    const self = SELF_ONLY.items[0]!.student_id;
    await open(
      '/my-progress',
      {
        [FAMILY]: () => jsonResponse(200, SELF_ONLY),
        [progressOf(self)]: () => jsonResponse(200, PROGRESS),
        [logOf(self)]: () => jsonResponse(200, LOG_PAGE),
      },
      STUDENT_USER,
    );
    await settleUntil(() => main().querySelector('figure'));
    expect(heading()!.textContent).toBe('Mi progreso');
    expect(document.title).toContain('Mi progreso');
    expect(text()).not.toContain('Progreso de Mi progreso');
    expect(text()).not.toContain('De quién es el progreso');
  });
});
