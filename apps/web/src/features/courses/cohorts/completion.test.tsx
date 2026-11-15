import { act } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { SessionUserBody } from '@/features/auth/session';
import { applyLanguage } from '@/i18n';
import {
  meAs,
  problem,
  stubApi,
  TEST_USER,
  type ApiStub,
  type Routes,
} from '@/test/api-stub';
import { click } from '@/test/dom';
import {
  buttonNamed,
  jsonResponse,
  renderApp,
  settle,
  settleUntil,
  type RenderedApp,
} from '@/test/render-app';
import type { CohortDetail } from './api';
import { completionAudience } from './completion/rules';
import {
  COMPLETION,
  ENDED_COMPLETION,
  EMPTY_COMPLETION,
  NO_CRITERIA_COMPLETION,
  OWN_COMPLETION,
  TWO_CHILDREN_COMPLETION,
} from './completion/fixtures';
import {
  COHORT_ID,
  FAMILY_COHORT,
  ROSTER_COHORT,
  TUTOR_COHORT,
} from './fixtures';

// #323 W6: course completion on the cohort page. Who gets the request
// (completionAudience; never Finance, a course.view-only role or before
// the detail), the roster (statuses as text, attendance, counts of the
// server's statuses, the disclosure and its detail), the family sections,
// 403/404 hidden without a retry or a report, a 500 with "Try again", the
// criteria and context lines, and decimals shown as the API's text.

const DETAIL = `GET /api/v1/cohorts/${COHORT_ID}`;
const SESSIONS = `GET /api/v1/cohorts/${COHORT_ID}/sessions`;
const COMPLETION_ROUTE = `GET /api/v1/cohorts/${COHORT_ID}/completion`;

const user = (
  roles: SessionUserBody['roles'],
  permissions: SessionUserBody['permissions'],
): SessionUserBody => ({ ...TEST_USER, roles, permissions });

const COORDINATOR = user(
  ['academic_coordinator'],
  ['course.view', 'schedule.view', 'schedule.assign', 'evaluation.manage'],
);
const FINANCE = user(['finance_staff'], ['course.view', 'schedule.view']);
/** An Administrator whose role lost evaluation.manage (a custom setup). */
const COURSE_VIEW_ONLY = user(['administrator'], ['course.view']);
const TUTOR = user(['tutor'], ['student.view', 'tutor.view']);
const STUDENT = user(['student'], ['student.view']);
const GUARDIAN = user(['guardian'], ['student.view', 'guardian.view']);
/** A Coordinator who is also a guardian: the roster wins. */
const COORDINATOR_GUARDIAN = user(
  ['academic_coordinator', 'guardian'],
  ['course.view', 'evaluation.manage', 'guardian.view'],
);

let app: RenderedApp | undefined;
let api: ApiStub;
const main = () => app!.container.querySelector('main')!;
const text = () => main().textContent ?? '';

const reports = () =>
  vi
    .mocked(console.error)
    .mock.calls.filter((call) => call[0] === '[classora]');

function completionSection(): HTMLElement | null {
  return (
    [...main().querySelectorAll('section')].find(
      (section) =>
        section.querySelector('h2')?.textContent === 'Course completion',
    ) ?? null
  );
}

async function open(
  as: SessionUserBody,
  detail: CohortDetail,
  routes: Routes = {},
) {
  api = stubApi({
    'GET /api/v1/auth/me': meAs(as),
    [DETAIL]: () => jsonResponse(200, detail),
    [SESSIONS]: () => jsonResponse(200, { items: [] }),
    'GET /api/v1/tutors': () =>
      jsonResponse(200, { items: [], total: 0, limit: 100, offset: 0 }),
    ...routes,
  });
  app = await renderApp(`/courses/cohorts/${COHORT_ID}`);
  await settleUntil(
    () => main().querySelector('h1')?.textContent === detail.name,
  );
}

async function openRoster(routes: Routes = {}, as = COORDINATOR) {
  await open(as, ROSTER_COHORT, {
    [COMPLETION_ROUTE]: () => jsonResponse(200, COMPLETION),
    ...routes,
  });
  await settleUntil(() => completionSection()?.querySelector('table'));
}

const rowOf = (name: string) =>
  [...completionSection()!.querySelectorAll('tbody tr')].find((row) =>
    row.querySelector('th')?.textContent?.includes(name),
  ) as HTMLTableRowElement;

const disclosure = (name: string) => buttonNamed(main(), `Details for ${name}`);

beforeEach(() => {
  applyLanguage('en');
  vi.spyOn(console, 'error').mockImplementation(() => {});
  vi.spyOn(console, 'warn').mockImplementation(() => {});
});

afterEach(() => {
  app?.unmount();
  app = undefined;
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  document.body.innerHTML = '';
});

describe('completionAudience (W0)', () => {
  it.each([
    ['evaluation.manage', true, false, ['academic_coordinator'], 'roster'],
    ['the cohort tutor', false, true, ['tutor'], 'roster'],
    ['a tutor with an empty roster', false, true, ['tutor'], 'roster'],
    ['a staff guardian', true, false, ['owner', 'guardian'], 'roster'],
    [
      'a tutor-guardian of the cohort',
      false,
      true,
      ['tutor', 'guardian'],
      'roster',
    ],
    ['a student', false, false, ['student'], 'family'],
    ['a guardian', false, false, ['guardian'], 'family'],
    [
      'a tutor not teaching it who is a guardian',
      false,
      false,
      ['tutor', 'guardian'],
      'family',
    ],
    ['Finance', false, false, ['finance_staff'], null],
    ['a tutor not teaching it', false, false, ['tutor'], null],
    ['no role', false, false, [], null],
  ] as const)(
    '%s → %s',
    (_name, canEvaluate, hasTutorRoster, roles, expected) => {
      expect(completionAudience({ canEvaluate, hasTutorRoster, roles })).toBe(
        expected,
      );
    },
  );
});

describe('the roster (W1, W2)', () => {
  it('a Coordinator sees the section after Students, one row per student, statuses as text', async () => {
    await openRoster();
    expect(api.calls(COMPLETION_ROUTE)).toHaveLength(1);
    const headings = [...main().querySelectorAll('h2')].map(
      (h) => h.textContent,
    );
    expect(headings.indexOf('Course completion')).toBe(
      headings.indexOf('Students') + 1,
    );
    const section = completionSection()!;
    expect(section.getAttribute('aria-labelledby')).toBe(
      section.querySelector('h2')!.id,
    );
    const region = section.querySelector<HTMLElement>('[role="region"]')!;
    expect(region.getAttribute('tabindex')).toBe('0');
    const caption = section.querySelector('caption')!;
    expect(region.getAttribute('aria-labelledby')).toBe(caption.id);
    expect(
      [...section.querySelectorAll('thead th')].map((th) => [
        th.textContent,
        th.getAttribute('scope'),
      ]),
    ).toEqual([
      ['Student', 'col'],
      ['Status', 'col'],
      ['Attendance', 'col'],
      ['Assessments', 'col'],
      ['Modules', 'col'],
    ]);
    // API order, never re-sorted.
    expect(
      [...section.querySelectorAll('tbody th button')].map((b) =>
        b.getAttribute('aria-label'),
      ),
    ).toEqual([
      'Details for Ana López',
      'Details for Bruno Díaz',
      'Details for Celia Domínguez',
      'Details for Darío Fernández de la Cruz y Montenegro Villaseñor',
    ]);
    expect(rowOf('Ana').textContent).toContain('In progress');
    expect(rowOf('Bruno').textContent).toContain('Completed');
    // The API's status, even where the counts would say otherwise.
    expect(rowOf('Celia').textContent).toContain('Not completed');
    expect(rowOf('Celia').textContent).toContain('2 of 2 required passed');
    expect(rowOf('Celia').textContent).toContain('2 of 2 required');
    // Every badge is bordered text with an icon.
    const badge = [...rowOf('Ana').querySelectorAll('span')].find(
      (span) => span.textContent === 'In progress',
    )!;
    expect(badge.className).toContain('border');
    expect(badge.querySelector('svg[aria-hidden="true"]')).not.toBeNull();
    expect(reports()).toEqual([]);
  });

  it('attendance: the counts, the percent as sent, awaiting closeout; none when nothing was held', async () => {
    await openRoster();
    const ana = rowOf('Ana').querySelectorAll('td')[1]!.textContent;
    expect(ana).toContain('2 of 3 (66.67 %)');
    expect(ana).toContain('1 awaiting closeout');
    const bruno = rowOf('Bruno').querySelectorAll('td')[1]!.textContent;
    expect(bruno).toContain('8 of 8 (100.00 %)');
    expect(bruno).not.toContain('awaiting');
    const dario = rowOf('Darío').querySelectorAll('td')[1]!.textContent;
    expect(dario).toBe('0 of 0');
  });

  it('counts only required items with the server status (optional ones never count)', async () => {
    await openRoster();
    // Ana: Parcial passed, Examen final not evaluated; the optional
    // Cuestionario (failed) is left out. Álgebra met, Introducción pending.
    const cells = rowOf('Ana').querySelectorAll('td');
    expect(cells[2]!.textContent).toBe('1 of 2 required passed');
    expect(cells[3]!.textContent).toBe('1 of 2 required');
  });

  it('the disclosure: aria-expanded, aria-controls the detail row, focus kept, the detail', async () => {
    await openRoster();
    const button = disclosure('Ana López');
    const detailRow = document.getElementById(
      button.getAttribute('aria-controls')!,
    )!;
    expect(detailRow.tagName).toBe('TR');
    expect(detailRow.hidden).toBe(true);
    expect(button.getAttribute('aria-expanded')).toBe('false');
    expect(button.textContent).toBe('Ana López');
    act(() => button.focus());
    await click(button);
    expect(button.getAttribute('aria-expanded')).toBe('true');
    expect(detailRow.hidden).toBe(false);
    expect(document.activeElement).toBe(button);
    expect(detailRow.querySelector('td')!.getAttribute('colspan')).toBe('5');
    const detail = detailRow.textContent;
    // Attendance.
    expect(detail).toContain('2 of 3 classes attended');
    expect(detail).toContain('1 absent');
    expect(detail).toContain('5 still to come');
    expect(detail).toContain('1 awaiting closeout');
    expect(detail).toContain('Minimum 80 %');
    // Assessments, in API order, each outcome as text.
    const items = [...detailRow.querySelectorAll('li')].map(
      (li) => li.textContent,
    );
    const parcial = items.find((li) => li?.startsWith('Parcial'))!;
    expect(parcial).toContain('Passed');
    expect(parcial).toContain('Required');
    expect(parcial).toContain('Minimum 60 %');
    expect(parcial).toContain('72.50 %');
    expect(parcial).toContain('Evaluated on Jan 20, 2031');
    expect(parcial).toContain('2 attempts');
    const final = items.find((li) => li?.startsWith('Examen final'))!;
    expect(final).toContain('Not evaluated');
    expect(final).toContain('Required');
    expect(final).not.toContain('attempt');
    const quiz = items.find((li) => li?.startsWith('Cuestionario'))!;
    expect(quiz).toContain('Failed');
    expect(quiz).not.toContain('Required');
    expect(quiz).toContain('1 attempt');
    expect(quiz).not.toContain('1 attempts');
    // Modules: the course-covered text.
    const intro = items.find((li) => li?.startsWith('Introducción'))!;
    expect(intro).toContain('Pending');
    expect(intro).toContain('Covered by the course');
    expect(intro).toContain(
      'This module has no required assessment; it counts as met when the course ends.',
    );
    const algebra = items.find((li) => li?.startsWith('Álgebra'))!;
    expect(algebra).toContain('Met');
    expect(algebra).not.toContain('Covered by the course');
    // h3 under the section's h2.
    expect(
      [...detailRow.querySelectorAll('h3')].map((h) => h.textContent),
    ).toEqual(['Attendance', 'Assessments', 'Required modules']);
    await click(button);
    expect(button.getAttribute('aria-expanded')).toBe('false');
    expect(detailRow.hidden).toBe(true);
    expect(document.activeElement).toBe(button);
  });

  it('the criteria and context lines while the cohort has not ended', async () => {
    await openRoster();
    const section = completionSection()!.textContent;
    expect(section).toContain('Minimum attendance 80 %');
    expect(section).toContain('Required modules: Introducción, Álgebra');
    expect(section).toContain(
      'Required assessments: Parcial (minimum 60 %), Examen final',
    );
    expect(section).not.toContain('Cuestionario');
    expect(section).toContain(
      'Results are final for each student once the course has ended',
    );
  });

  it('no context line once the cohort has ended', async () => {
    await openRoster({
      [COMPLETION_ROUTE]: () => jsonResponse(200, ENDED_COMPLETION),
    });
    expect(completionSection()!.textContent).not.toContain('Results are final');
  });

  it('no criteria: the line, "Not required" and "None required"', async () => {
    await openRoster({
      [COMPLETION_ROUTE]: () => jsonResponse(200, NO_CRITERIA_COMPLETION),
    });
    const section = completionSection()!;
    expect(section.textContent).toContain(
      'No completion criteria. Every enrolled student completes the course when it ends.',
    );
    const cells = rowOf('Ana').querySelectorAll('td');
    expect(cells[1]!.textContent).toContain('Not required');
    expect(cells[2]!.textContent).toBe('None required');
    expect(cells[3]!.textContent).toBe('None required');
  });

  it('an empty roster: the line, no table', async () => {
    await open(COORDINATOR, ROSTER_COHORT, {
      [COMPLETION_ROUTE]: () => jsonResponse(200, EMPTY_COMPLETION),
    });
    await settleUntil(() =>
      completionSection()?.textContent?.includes('No students are enrolled.'),
    );
    expect(completionSection()!.querySelector('table')).toBeNull();
  });

  it("the cohort's tutor (the detail has tutor_roster) gets the roster", async () => {
    await open(TUTOR, TUTOR_COHORT, {
      [COMPLETION_ROUTE]: () => jsonResponse(200, COMPLETION),
    });
    await settleUntil(() => completionSection()?.querySelector('table'));
    expect(api.calls(COMPLETION_ROUTE)).toHaveLength(1);
  });

  it('a tutor with an empty tutor_roster still asks', async () => {
    await open(
      TUTOR,
      { ...TUTOR_COHORT, tutor_roster: [] },
      { [COMPLETION_ROUTE]: () => jsonResponse(200, EMPTY_COMPLETION) },
    );
    await settleUntil(() => completionSection());
    expect(api.calls(COMPLETION_ROUTE)).toHaveLength(1);
  });

  it('a Coordinator who is also a guardian gets the roster', async () => {
    await openRoster({}, COORDINATOR_GUARDIAN);
    expect(text()).not.toContain('My progress');
  });

  it('loading: busy inside the section, with its heading', async () => {
    await open(COORDINATOR, ROSTER_COHORT);
    await settleUntil(() => completionSection());
    expect(
      completionSection()!.querySelector('[aria-busy="true"]'),
    ).not.toBeNull();
  });
});

describe('who never asks (W0, W4)', () => {
  it.each([
    ['Finance', FINANCE],
    ['a course.view-only role', COURSE_VIEW_ONLY],
  ])('%s: no request, no section', async (_name, as) => {
    await open(as, ROSTER_COHORT, {
      [COMPLETION_ROUTE]: () => jsonResponse(200, COMPLETION),
    });
    await settleUntil(() => text().includes('Students'));
    await settle();
    expect(api.calls(COMPLETION_ROUTE)).toHaveLength(0);
    expect(completionSection()).toBeNull();
  });

  it('nobody asks before the detail has loaded', async () => {
    api = stubApi({
      'GET /api/v1/auth/me': meAs(COORDINATOR),
      [SESSIONS]: () => jsonResponse(200, { items: [] }),
      [COMPLETION_ROUTE]: () => jsonResponse(200, COMPLETION),
    });
    app = await renderApp(`/courses/cohorts/${COHORT_ID}`);
    await settleUntil(() => api.calls(DETAIL).length === 1);
    await settle();
    expect(api.calls(COMPLETION_ROUTE)).toHaveLength(0);
  });
});

describe('the family (W3)', () => {
  it('a student: "My progress", the badge, the lines, the detail expanded, no table', async () => {
    await open(STUDENT, FAMILY_COHORT, {
      [COMPLETION_ROUTE]: () => jsonResponse(200, OWN_COMPLETION),
    });
    await settleUntil(() => text().includes('My progress'));
    const section = [...main().querySelectorAll('section')].find(
      (s) => s.querySelector('h2')?.textContent === 'My progress',
    )!;
    expect(section.getAttribute('aria-labelledby')).toBe(
      section.querySelector('h2')!.id,
    );
    expect(section.textContent).toContain('In progress');
    expect(section.textContent).toContain('Minimum attendance 80 %');
    expect(section.textContent).toContain('Results are final');
    expect(section.textContent).toContain('2 of 3 classes attended');
    expect(section.textContent).toContain('Covered by the course');
    expect(section.querySelector('table')).toBeNull();
    expect(section.querySelector('button[aria-expanded]')).toBeNull();
    expect(completionSection()).toBeNull();
    expect(api.calls(COMPLETION_ROUTE)).toHaveLength(1);
  });

  it('a guardian of two: one section per child, named', async () => {
    await open(GUARDIAN, FAMILY_COHORT, {
      [COMPLETION_ROUTE]: () => jsonResponse(200, TWO_CHILDREN_COMPLETION),
    });
    await settleUntil(() => text().includes("Bruno Díaz's progress"));
    expect(
      [...main().querySelectorAll('h2')]
        .map((h) => h.textContent)
        .filter((h) => h?.endsWith('progress')),
    ).toEqual(["Ana López's progress", "Bruno Díaz's progress"]);
    expect(text()).not.toContain('My progress');
    expect(main().querySelector('table caption')).toBeNull();
  });

  it('no row: nothing at all', async () => {
    await open(GUARDIAN, FAMILY_COHORT, {
      [COMPLETION_ROUTE]: () => jsonResponse(200, EMPTY_COMPLETION),
    });
    await settleUntil(() => api.calls(COMPLETION_ROUTE).length === 1);
    await settle();
    expect(text()).not.toMatch(/My progress|'s progress/);
    expect(text()).not.toContain('Course completion');
  });

  it('loading shows nothing', async () => {
    await open(STUDENT, FAMILY_COHORT);
    await settleUntil(() => api.calls(COMPLETION_ROUTE).length === 1);
    expect(text()).not.toContain('My progress');
    expect(main().querySelector('[aria-busy="true"]')).toBeNull();
  });
});

describe('access and errors (W4)', () => {
  it.each([
    ['a 403', 403, 'permission_denied', COORDINATOR, ROSTER_COHORT],
    ['a 404', 404, 'cohort_not_found', COORDINATOR, ROSTER_COHORT],
    ['a family 404', 404, 'cohort_not_found', GUARDIAN, FAMILY_COHORT],
  ] as const)(
    '%s: hidden, no error, no retry, nothing reported',
    async (_name, status, code, as, detail) => {
      await open(as, detail, {
        [COMPLETION_ROUTE]: () => problem(status, code),
      });
      await settleUntil(() => api.calls(COMPLETION_ROUTE).length === 1);
      await settle();
      await settle();
      expect(completionSection()).toBeNull();
      expect(main().querySelector('[role="alert"]')).toBeNull();
      expect(text()).not.toMatch(/My progress|'s progress/);
      expect(api.calls(COMPLETION_ROUTE)).toHaveLength(1);
      expect(reports()).toEqual([]);
      // The rest of the page is there.
      expect(text()).toContain('Syllabus');
    },
  );

  it('a 500: the error inside the section, then "Try again" refetches and renders', async () => {
    await open(COORDINATOR, ROSTER_COHORT, {
      // The first try and both retries (5xx retries, #28) fail.
      [COMPLETION_ROUTE]: [
        () => problem(500, 'internal_error'),
        () => problem(500, 'internal_error'),
        () => problem(500, 'internal_error'),
        () => jsonResponse(200, COMPLETION),
      ],
    });
    await settleUntil(
      () => completionSection()?.querySelector('[role="alert"]'),
      15_000,
    );
    expect(reports().length).toBeGreaterThan(0);
    // The page stays usable.
    expect(text()).toContain('Syllabus');
    await click(buttonNamed(completionSection()!, 'Try again'));
    await settleUntil(() => completionSection()?.querySelector('table'));
    expect(api.calls(COMPLETION_ROUTE)).toHaveLength(4);
  }, 20_000);
});
