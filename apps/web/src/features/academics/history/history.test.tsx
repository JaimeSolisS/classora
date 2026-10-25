import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { JOSE, JOSE_LIMITED } from '@/features/students/fixtures';
import { applyLanguage } from '@/i18n';
import { meAs, stubApi, type ApiStub, type Routes } from '@/test/api-stub';
import { alerts, click, labelled, setFieldValue } from '@/test/dom';
import {
  buttonNamed,
  jsonResponse,
  renderApp,
  settleUntil,
  type RenderedApp,
} from '@/test/render-app';
import { DRAFT, FINAL } from '../evaluations/fixtures';
import {
  COORDINATOR,
  FINANCE,
  GUARDIAN,
  problemWith,
  STUDENT,
  TUTOR,
  TUTOR_COORDINATOR,
} from '../testing';
import {
  ADULT_ID,
  CHILD_ID,
  EMPTY,
  FAMILY_STUDENTS,
  GROUPED,
  PHYSICS_ONLY,
  SELF_ONLY,
  SIBLING_ID,
  STUDENT_ID,
  TRUNCATED,
  UNGROUPED,
  WORKING_ITEMS,
} from './fixtures';
import { parseHistoryControls } from './urlState';

// #308 E, A1-A3, D2, G1 (history): the grouped view (period h2 with its
// dates, "No period" where the API puts it, subject h3 with the count and
// the average, entries by entry_ids), the chronological view, the controls
// in the URL, the range check, every state, "Details" for staff and tutors
// only, an unknown entry kind skipped, the family picker, the Evaluations
// tab (rows, the status options per role, "Load more"), "New evaluation",
// the student page link per role, and the route guard of /my-history.

const PHYSICS_ID = '00000000-0000-4000-8000-000000308a01';
const STUDENT_ROUTE = `GET /api/v1/students/${STUDENT_ID}`;
const historyRoute = (id: string) =>
  `GET /api/v1/students/${id}/academic-history`;
const WORKING = `GET /api/v1/students/${STUDENT_ID}/evaluations`;
const FAMILY = 'GET /api/v1/schedule/family-students';
const PAGE = `/students/${STUDENT_ID}/history`;

let app: RenderedApp | undefined;
let api: ApiStub;
let historyUrls: URL[] = [];
let workingUrls: URL[] = [];
const main = () => app!.container.querySelector('main')!;
const text = () => main().textContent ?? '';

function stub(routes: Routes = {}, as = TUTOR): ApiStub {
  historyUrls = [];
  workingUrls = [];
  const history =
    (body = GROUPED) =>
    (request: Request) => {
      historyUrls.push(new URL(request.url));
      return jsonResponse(200, body);
    };
  api = stubApi({
    'GET /api/v1/auth/me': meAs(as),
    [STUDENT_ROUTE]: () => jsonResponse(200, JOSE_LIMITED),
    [historyRoute(STUDENT_ID)]: history(),
    [historyRoute(CHILD_ID)]: history(),
    [historyRoute(SIBLING_ID)]: history(EMPTY),
    [WORKING]: (request) => {
      workingUrls.push(new URL(request.url));
      return jsonResponse(200, { items: WORKING_ITEMS, next_cursor: null });
    },
    [FAMILY]: () => jsonResponse(200, FAMILY_STUDENTS),
    ...routes,
  });
  return api;
}

async function open(path: string, ready: () => unknown): Promise<void> {
  app = await renderApp(path);
  await settleUntil(ready);
}

const ready = () =>
  main().querySelector('article') ||
  main().querySelector('[role="status"]') ||
  alerts(main()).length > 0;

beforeEach(() => {
  applyLanguage('en');
});

afterEach(() => {
  app?.unmount();
  app = undefined;
  vi.unstubAllGlobals();
  document.body.innerHTML = '';
});

describe('access (A1, A3)', () => {
  it('a tutor-only user gets access-denied on /my-history and no request', async () => {
    stub();
    await open('/my-history', () => main().querySelector('h1'));
    expect(text()).toMatch(/permission/i);
    expect(api.calls(FAMILY)).toHaveLength(0);
    expect(
      api.requests.filter((r) => r.path.includes('academic-history')),
    ).toHaveLength(0);
  });

  it.each([
    ['a Coordinator', COORDINATOR, true],
    ['a tutor', TUTOR, true],
    ['Finance', FINANCE, false],
    ['a guardian', GUARDIAN, false],
    ['a student', STUDENT, false],
  ] as const)(
    'the student page links the history for %s: %s',
    async (_who, as, shown) => {
      stub({ [STUDENT_ROUTE]: () => jsonResponse(200, JOSE) }, as);
      await open(`/students/${STUDENT_ID}`, () =>
        main().querySelector('h1')?.textContent?.includes('José'),
      );
      const link = main().querySelector(`a[href="${PAGE}"]`);
      expect(Boolean(link)).toBe(shown);
      if (link) {
        expect(link.textContent).toBe('Academic history');
      }
    },
  );
});

describe('families on the staff URL (#308 QA D2)', () => {
  it.each([
    ['a guardian', GUARDIAN],
    ['a student', STUDENT],
  ] as const)(
    '%s goes to /my-history for that student, without tabs or Details',
    async (_who, as) => {
      stub({}, as);
      await open(PAGE, () => app!.location().startsWith('/my-history'));
      expect(app!.location()).toBe(`/my-history?student=${STUDENT_ID}`);
      await settleUntil(() => main().querySelector('h1'));
      expect(main().querySelector('[role="tablist"]')).toBeNull();
      expect(main().querySelector('a[href^="/evaluations/"]')).toBeNull();
      expect(api.calls(STUDENT_ROUTE)).toHaveLength(0);
      expect(api.calls(WORKING)).toHaveLength(0);
    },
  );
});

describe('the grouped view (E3, E5, E6)', () => {
  it('groups by period and subject in the API order', async () => {
    stub();
    await open(PAGE, () => main().querySelector('article'));
    expect(main().querySelector('h1')?.textContent).toBe(
      'Academic history: José Ñúñez Pérez',
    );
    const h2 = [...main().querySelectorAll('h2')].map((h) => h.textContent);
    expect(h2).toEqual([
      'Ciclo 2026-2027 (Aug 1, 2026 – Jul 31, 2027)',
      'No period',
    ]);
    const h3 = [...main().querySelectorAll('h3')].map((h) => h.textContent);
    expect(h3).toEqual(['Física', 'Matemáticas', 'Física']);
    expect(text()).toContain('2 evaluations · average 83.75 %');
    expect(text()).toContain('1 evaluation · average 60.00 %');
    const h4 = [...main().querySelectorAll('article h4')].map(
      (h) => h.textContent,
    );
    expect(h4).toEqual([
      'Diagnóstico de física',
      'Seguimiento de laboratorio',
      'Diagnóstico de matemáticas',
      'Curso de verano',
    ]);
    // The unknown kind is skipped.
    expect(text()).not.toContain('Tarea desconocida');
  });

  it('shows an entry as text, the numbers as sent', async () => {
    stub();
    await open(PAGE, () => main().querySelector('article'));
    const [first, second] = [...main().querySelectorAll('article')];
    expect(first!.textContent).toContain('Diagnostic · Nov 9, 2026');
    expect(first!.textContent).toContain('by Carmen Ruiz');
    expect(first!.textContent).toContain('Total 17.50 of 20.00 · 87.50 %');
    expect(first!.textContent).toContain('Comprensión: 8.00 of 10.00');
    expect(first!.textContent).toContain('Bien explicado.');
    expect(first!.textContent).toContain('Muy buen trabajo.');
    expect(first!.textContent).not.toContain('Updated');
    expect(second!.textContent).toContain('Updated');
    expect(second!.textContent).toContain(
      'Course assessment: Práctica de laboratorio (Física intensiva)',
    );
    expect(second!.textContent).toContain('by Pedro Gómez');
  });

  it('gives staff and tutors "Details"', async () => {
    stub();
    await open(PAGE, () => main().querySelector('article'));
    const details = main().querySelector<HTMLAnchorElement>(
      `article a[href="/evaluations/${GROUPED.entries[0]!.id}"]`,
    );
    expect(details?.textContent).toBe('Details');
    expect(details?.getAttribute('aria-label')).toBe(
      'Details: Diagnóstico de física, Nov 9, 2026',
    );
  });

  it('with no grouping: one unnamed section, subjects as h2', async () => {
    stub({ [historyRoute(STUDENT_ID)]: () => jsonResponse(200, UNGROUPED) });
    await open(`${PAGE}?group=none`, () => main().querySelector('article'));
    expect(
      [...main().querySelectorAll('h2')].map((h) => h.textContent),
    ).toEqual(['Física', 'Matemáticas']);
    expect(text()).not.toContain('No period');
  });
});

describe('the chronological view (E4)', () => {
  it('lists every entry in the API order with its subject', async () => {
    stub();
    await open(`${PAGE}?view=list`, () => main().querySelector('article'));
    const headings = [...main().querySelectorAll('article h2')].map(
      (h) => h.textContent,
    );
    expect(headings).toEqual([
      'Diagnóstico de física',
      'Seguimiento de laboratorio',
      'Diagnóstico de matemáticas',
      'Curso de verano',
    ]);
    expect(main().querySelectorAll('article')[2]!.textContent).toContain(
      'Matemáticas · Diagnostic · Sep 15, 2026',
    );
    expect(
      buttonNamed(main(), 'Chronological').getAttribute('aria-pressed'),
    ).toBe('true');
    expect(buttonNamed(main(), 'Grouped').getAttribute('aria-pressed')).toBe(
      'false',
    );
  });

  it('the toggle changes the URL', async () => {
    stub();
    await open(PAGE, () => main().querySelector('article'));
    await click(buttonNamed(main(), 'Chronological'));
    expect(app!.location()).toBe(`${PAGE}?view=list`);
  });
});

describe('the controls in the URL (E1, E2)', () => {
  it('sends group, subject, from and to', async () => {
    stub();
    await open(
      `${PAGE}?group=term&subject=${PHYSICS_ID}&from=2026-08-01&to=2026-12-31`,
      () => historyUrls.length > 0,
    );
    const params = historyUrls.at(-1)!.searchParams;
    expect(params.get('group_by')).toBe('term');
    expect(params.get('subject_id')).toBe(PHYSICS_ID);
    expect(params.get('from')).toBe('2026-08-01');
    expect(params.get('to')).toBe('2026-12-31');
  });

  it('ignores invalid values', async () => {
    stub();
    await open(
      `${PAGE}?group=decade&view=table&subject=x&from=2026-13-01&to=nope`,
      () => historyUrls.length > 0,
    );
    const params = historyUrls.at(-1)!.searchParams;
    expect(params.get('group_by')).toBe('school_year');
    expect([...params.keys()]).toEqual(['group_by']);
    expect(parseHistoryControls(new URLSearchParams('view=list'))).toEqual({
      group: 'school_year',
      view: 'list',
      subject: null,
      from: null,
      to: null,
    });
  });

  it('a group change goes to the URL', async () => {
    stub();
    await open(PAGE, () => main().querySelector('article'));
    setFieldValue(labelled<HTMLSelectElement>(main(), 'Group by'), 'semester');
    expect(app!.location()).toBe(`${PAGE}?group=semester`);
    await settleUntil(
      () => historyUrls.at(-1)!.searchParams.get('group_by') === 'semester',
    );
  });

  it('keeps every subject of the unfiltered history once one is chosen', async () => {
    stub();
    await open(PAGE, () => main().querySelector('article'));
    const subject = labelled<HTMLSelectElement>(main(), 'Subject');
    expect([...subject.options].map((o) => o.textContent)).toEqual([
      'All subjects',
      'Física',
      'Matemáticas',
    ]);
    api.set({
      [historyRoute(STUDENT_ID)]: (request) => {
        historyUrls.push(new URL(request.url));
        return jsonResponse(200, PHYSICS_ONLY);
      },
    });
    const before = api.requests.length;
    setFieldValue(subject, PHYSICS_ID);
    await settleUntil(
      () => historyUrls.at(-1)!.searchParams.get('subject_id') === PHYSICS_ID,
    );
    // One read (the filtered one), no extra request for the options.
    expect(api.requests.length - before).toBe(1);
    expect(
      [...labelled<HTMLSelectElement>(main(), 'Subject').options].map(
        (o) => o.textContent,
      ),
    ).toEqual(['All subjects', 'Física', 'Matemáticas']);
  });

  it('To before From: a field error and no request', async () => {
    stub();
    await open(`${PAGE}?from=2026-12-01&to=2026-11-01`, () =>
      text().includes('The end date must be on or after the start date.'),
    );
    expect(historyUrls).toHaveLength(0);
    expect(labelled(main(), 'To').getAttribute('aria-invalid')).toBe('true');
  });

  it("the server's range_invalid gives the same message", async () => {
    stub({
      [historyRoute(STUDENT_ID)]: () => problemWith(422, 'range_invalid'),
    });
    await open(`${PAGE}?from=2026-11-01`, () =>
      text().includes('The end date must be on or after the start date.'),
    );
  });
});

describe('the states (E7)', () => {
  it.each([
    [
      TRUNCATED,
      'Showing the latest 500 evaluations. Narrow the dates to see older ones.',
    ],
    [EMPTY, 'No evaluations yet.'],
  ] as const)('shows %#', async (body, message) => {
    stub({ [historyRoute(STUDENT_ID)]: () => jsonResponse(200, body) });
    await open(PAGE, ready);
    expect(
      [...main().querySelectorAll('[role="status"]')].some(
        (s) => s.textContent === message,
      ),
    ).toBe(true);
  });

  it('empty with filters: the message and "Clear filters"', async () => {
    stub({ [historyRoute(STUDENT_ID)]: () => jsonResponse(200, EMPTY) });
    await open(`${PAGE}?from=2020-01-01&view=list`, () =>
      text().includes('No evaluations match these filters.'),
    );
    await click(buttonNamed(main(), 'Clear filters'));
    expect(app!.location()).toBe(`${PAGE}?view=list`);
  });

  it('loading is busy', async () => {
    stub({ [historyRoute(STUDENT_ID)]: () => new Promise<Response>(() => {}) });
    await open(PAGE, () => main().querySelector('[aria-busy="true"]'));
  });

  it('an error offers "Try again"', async () => {
    stub({
      [historyRoute(STUDENT_ID)]: () => problemWith(500, 'internal_error'),
    });
    await open(PAGE, () => alerts(main()).length > 0);
    const before = api.calls(historyRoute(STUDENT_ID)).length;
    await click(buttonNamed(main(), 'Try again'));
    await settleUntil(
      () => api.calls(historyRoute(STUDENT_ID)).length > before,
    );
  });

  it('403 student_consent_required shows the consent message', async () => {
    stub(
      {
        [historyRoute(CHILD_ID)]: () =>
          problemWith(403, 'student_consent_required'),
      },
      GUARDIAN,
    );
    await open('/my-history', () =>
      text().includes(
        "This student hasn't shared their academic records with you",
      ),
    );
  });

  it("404: this history isn't available", async () => {
    stub({ [historyRoute(STUDENT_ID)]: () => problemWith(404, 'not_found') });
    await open(PAGE, () =>
      text().includes("This academic history isn't available."),
    );
    // The page itself stays (the title without the student is fine too).
    expect(main().querySelector('h1')?.textContent).toContain(
      'Academic history',
    );
  });
});

describe('the family page (E8)', () => {
  it('defaults to the first student, no tabs, no Details, no New evaluation', async () => {
    stub({}, GUARDIAN);
    await open('/my-history', () => main().querySelector('article'));
    expect(main().querySelector('h1')?.textContent).toBe('Academic history');
    expect(historyUrls.at(-1)!.pathname).toContain(CHILD_ID);
    const whose = labelled<HTMLSelectElement>(main(), 'Whose history');
    expect([...whose.options].map((o) => o.textContent)).toEqual([
      'Lucía Pérez',
      'Andrés Pérez',
      'Mateo Pérez (not shared)',
    ]);
    expect(main().querySelector('[role="tablist"]')).toBeNull();
    expect(main().querySelector('a[href^="/evaluations/"]')).toBeNull();
    expect(text()).not.toContain('New evaluation');
  });

  it('?student= chooses; the choice goes to the URL', async () => {
    stub({}, GUARDIAN);
    await open(`/my-history?student=${SIBLING_ID}`, () =>
      text().includes('No evaluations have been shared yet.'),
    );
    expect(historyUrls.at(-1)!.pathname).toContain(SIBLING_ID);
    setFieldValue(
      labelled<HTMLSelectElement>(main(), 'Whose history'),
      CHILD_ID,
    );
    expect(app!.location()).toBe(`/my-history?student=${CHILD_ID}`);
    await settleUntil(() => main().querySelector('article'));
  });

  it('a foreign ?student= falls back to the first, with no request for it', async () => {
    stub({}, GUARDIAN);
    const foreign = '00000000-0000-4000-8000-000000999999';
    await open(`/my-history?student=${foreign}`, () =>
      main().querySelector('article'),
    );
    expect(api.requests.filter((r) => r.path.includes(foreign))).toHaveLength(
      0,
    );
    expect(historyUrls.at(-1)!.pathname).toContain(CHILD_ID);
  });

  it('an adult who has not shared: the consent message and no request', async () => {
    stub({}, GUARDIAN);
    await open(`/my-history?student=${ADULT_ID}`, () =>
      text().includes(
        "This student hasn't shared their academic records with you",
      ),
    );
    expect(api.requests.filter((r) => r.path.includes(ADULT_ID))).toHaveLength(
      0,
    );
  });

  it('a student sees "My history"', async () => {
    stub(
      {
        [FAMILY]: () => jsonResponse(200, SELF_ONLY),
      },
      STUDENT,
    );
    await open('/my-history', () => main().querySelector('article'));
    expect(text()).toContain('Whose history My history');
  });

  it('with no students: the message', async () => {
    stub({ [FAMILY]: () => jsonResponse(200, { items: [] }) }, GUARDIAN);
    await open('/my-history', () =>
      text().includes("There's no student linked to your account."),
    );
    expect(
      api.requests.filter((r) => r.path.includes('academic-history')),
    ).toHaveLength(0);
  });
});

describe('the staff and tutor page (E8, D2)', () => {
  it('a tutor gets "New evaluation" and "Back to student"', async () => {
    stub();
    await open(PAGE, () => main().querySelector('article'));
    expect(
      main().querySelector(`a[href="/students/${STUDENT_ID}/evaluations/new"]`)
        ?.textContent,
    ).toBe('New evaluation');
    expect(
      main().querySelector(`a[href="/students/${STUDENT_ID}"]`)?.textContent,
    ).toBe('Back to student');
  });

  it('staff get no "New evaluation"', async () => {
    stub({}, COORDINATOR);
    await open(PAGE, () => main().querySelector('article'));
    expect(text()).not.toContain('New evaluation');
  });

  it('without the student: no name, no links, the history still loads', async () => {
    stub({ [STUDENT_ROUTE]: () => problemWith(404, 'not_found') });
    await open(PAGE, () => main().querySelector('article'));
    expect(main().querySelector('h1')?.textContent).toBe('Academic history');
    expect(text()).not.toContain('New evaluation');
    expect(text()).not.toContain('Back to student');
  });

  it('switches tabs in the URL, with arrow keys too', async () => {
    stub();
    await open(PAGE, () => main().querySelector('article'));
    const tabs = [...main().querySelectorAll<HTMLElement>('[role="tab"]')];
    expect(tabs.map((t) => t.textContent)).toEqual(['History', 'Evaluations']);
    expect(tabs[0]!.getAttribute('aria-selected')).toBe('true');
    expect(tabs[1]!.tabIndex).toBe(-1);
    tabs[0]!.focus();
    tabs[0]!.dispatchEvent(
      new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }),
    );
    await settleUntil(() => app!.location() === `${PAGE}?tab=evaluations`);
    expect(document.activeElement?.textContent).toBe('Evaluations');
  });

  it('the Evaluations tab lists the working list', async () => {
    stub();
    await open(`${PAGE}?tab=evaluations`, () => main().querySelector('li'));
    const rows = [...main().querySelectorAll('li')].map((li) => li.textContent);
    expect(rows[0]).toContain('Draft');
    expect(rows[1]).toContain('Final');
    expect(rows[1]).toContain('Total 17.50 of 20.00 · 87.50 %');
    expect(rows[2]).toContain('Voided');
    const link = main().querySelector<HTMLAnchorElement>(
      `li a[href="/evaluations/${DRAFT.id}"]`,
    );
    expect(link?.textContent).toBe(DRAFT.template.name);
    expect(workingUrls.at(-1)!.searchParams.get('limit')).toBe('20');
    expect(workingUrls.at(-1)!.searchParams.has('status')).toBe(false);
  });

  it.each([
    ['a tutor', TUTOR, ['All', 'Final', 'Drafts']],
    ['a Coordinator', COORDINATOR, ['All', 'Final', 'Voided']],
    [
      'a tutor who is also a Coordinator',
      TUTOR_COORDINATOR,
      ['All', 'Final', 'Drafts', 'Voided'],
    ],
  ] as const)('the status options for %s', async (_who, as, options) => {
    stub({}, as);
    await open(`${PAGE}?tab=evaluations`, () => main().querySelector('li'));
    const status = labelled<HTMLSelectElement>(main(), 'Status');
    expect([...status.options].map((o) => o.textContent)).toEqual(options);
  });

  it('filters by status and loads more with the cursor', async () => {
    stub({
      [WORKING]: (request) => {
        const url = new URL(request.url);
        workingUrls.push(url);
        return url.searchParams.get('cursor') === 'c2'
          ? jsonResponse(200, {
              items: [{ ...FINAL, id: '00000000-0000-4000-8000-000000308d09' }],
              next_cursor: null,
            })
          : jsonResponse(200, { items: [FINAL], next_cursor: 'c2' });
      },
    });
    await open(`${PAGE}?tab=evaluations`, () => main().querySelector('li'));
    setFieldValue(labelled<HTMLSelectElement>(main(), 'Status'), 'final');
    await settleUntil(
      () => workingUrls.at(-1)!.searchParams.get('status') === 'final',
    );
    await settleUntil(() =>
      [...main().querySelectorAll('button')].some(
        (b) => b.textContent === 'Load more',
      ),
    );
    await click(buttonNamed(main(), 'Load more'));
    await settleUntil(
      () => workingUrls.at(-1)!.searchParams.get('cursor') === 'c2',
    );
    await settleUntil(() => main().querySelectorAll('li').length === 2);
  });

  it('a 404 working list reads as empty', async () => {
    stub({ [WORKING]: () => problemWith(404, 'not_found') });
    await open(`${PAGE}?tab=evaluations`, () =>
      text().includes('No evaluations yet.'),
    );
  });
});
