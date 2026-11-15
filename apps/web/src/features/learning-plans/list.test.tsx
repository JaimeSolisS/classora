import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { problemWith } from '@/features/academics/testing';
import { JOSE, JOSE_LIMITED } from '@/features/students/fixtures';
import { applyLanguage } from '@/i18n';
import { meAs, stubApi, type ApiStub, type Routes } from '@/test/api-stub';
import {
  jsonResponse,
  renderApp,
  settleUntil,
  type RenderedApp,
} from '@/test/render-app';
import {
  EMPTY_LIST,
  FINANCE,
  GUARDIAN,
  MANAGER,
  MANAGER_LIST,
  READ_ONLY_LIST,
  STUDENT,
  STUDENT_ID,
  TRUNCATED_LIST,
  TUTOR,
  TUTOR_GUARDIAN,
  TUTOR_LIST,
} from './fixtures';

// #310 B, A1, A3, G1 (list): the header with the student's name (or
// without it for a former tutor), "New plan" by can_create, the rows,
// truncated, empty, loading, error, the not-found page; the route access
// (Finance denied with no request, families sent to their page) and the
// student page link per role.

const LIST = `GET /api/v1/students/${STUDENT_ID}/learning-plans`;
const STUDENT_ROUTE = `GET /api/v1/students/${STUDENT_ID}`;
const PAGE = `/students/${STUDENT_ID}/learning-plans`;

let app: RenderedApp | undefined;
let api: ApiStub;
const main = () => app!.container.querySelector('main')!;
const text = () => main().textContent ?? '';
const heading = () => main().querySelector('h1');
const newLink = () => main().querySelector(`a[href="${PAGE}/new"]`);

function stub(routes: Routes = {}, as = MANAGER) {
  api = stubApi({
    'GET /api/v1/auth/me': meAs(as),
    [STUDENT_ROUTE]: () => jsonResponse(200, JOSE_LIMITED),
    [LIST]: () => jsonResponse(200, MANAGER_LIST),
    ...routes,
  });
}

async function open(routes: Routes = {}, as = MANAGER, path = PAGE) {
  stub(routes, as);
  app = await renderApp(path);
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

describe('access (A1)', () => {
  it('Finance gets access-denied and no learning-plan request', async () => {
    await open({}, FINANCE);
    await settleUntil(() => heading());
    expect(heading()!.textContent).toBe("You don't have access to this page");
    expect(api.calls(LIST)).toHaveLength(0);
    expect(api.calls(STUDENT_ROUTE)).toHaveLength(0);
  });

  it.each([
    ['a guardian', GUARDIAN],
    ['a student', STUDENT],
  ] as const)(
    '%s goes to /my-learning-plans with the student, no staff request',
    async (_who, as) => {
      await open(
        {
          'GET /api/v1/schedule/family-students': () =>
            jsonResponse(200, { items: [] }),
        },
        as,
      );
      await settleUntil(
        () => app!.location() === `/my-learning-plans?student=${STUDENT_ID}`,
      );
      expect(api.calls(STUDENT_ROUTE)).toHaveLength(0);
      expect(api.calls(LIST)).toHaveLength(0);
    },
  );

  it.each([
    ['a guardian', GUARDIAN],
    ['Finance', FINANCE],
  ] as const)(
    '%s on the new-plan URL sends no staff request',
    async (_who, as) => {
      await open(
        {
          'GET /api/v1/schedule/family-students': () =>
            jsonResponse(200, { items: [] }),
        },
        as,
        `${PAGE}/new`,
      );
      await settleUntil(() => heading());
      expect(api.calls(STUDENT_ROUTE)).toHaveLength(0);
      expect(api.calls(LIST)).toHaveLength(0);
    },
  );

  it('a tutor who is also a guardian keeps the staff list', async () => {
    await open({ [LIST]: () => jsonResponse(200, TUTOR_LIST) }, TUTOR_GUARDIAN);
    await settleUntil(() => main().querySelector('li'));
    expect(app!.location()).toBe(PAGE);
  });
});

describe('the list (B)', () => {
  it('the header names the student; "New plan" when can_create', async () => {
    await open({ [LIST]: () => jsonResponse(200, TUTOR_LIST) }, TUTOR);
    await settleUntil(() => heading()?.textContent?.includes('José'));
    expect(heading()!.textContent).toBe('Learning plans: José Ñúñez Pérez');
    expect(document.title).toContain('Learning plans: José Ñúñez Pérez');
    expect(
      main().querySelector(`a[href="/students/${STUDENT_ID}"]`)?.textContent,
    ).toBe('Back to student');
    await settleUntil(() => newLink());
    expect(newLink()!.textContent).toBe('New plan');
  });

  it('no "New plan" without can_create (a former tutor: no name, no back link)', async () => {
    await open(
      {
        [LIST]: () => jsonResponse(200, READ_ONLY_LIST),
        [STUDENT_ROUTE]: () => problemWith(404, 'not_found'),
      },
      TUTOR,
    );
    await settleUntil(() => main().querySelector('li'));
    expect(heading()!.textContent).toBe('Learning plans');
    expect(newLink()).toBeNull();
    expect(
      main().querySelector(`a[href="/students/${STUDENT_ID}"]`),
    ).toBeNull();
  });

  it('rows in the API order, with every field as text', async () => {
    await open();
    await settleUntil(() => main().querySelectorAll('li').length === 3);
    const rows = [...main().querySelectorAll('li')];
    expect(rows.map((row) => row.querySelector('h2')?.textContent)).toEqual([
      'Leer con fluidez',
      'Fracciones',
      'Ortografía',
    ]);
    const [active, draft, completed] = rows.map((row) => row.textContent);
    expect(active).toContain('Active');
    expect(active).toContain('Física');
    expect(active).toContain('Nov 2, 2026 – Jan 29, 2027');
    expect(active).toContain('1 of 3 milestones done · 33 %');
    expect(active).toContain('0 of 2 objectives complete');
    expect(active).toContain('1 overdue');
    expect(active).toContain('Created by Carmen Ruiz');
    expect(draft).toContain('Draft');
    expect(draft).toContain('Matemáticas');
    expect(draft).toContain('From Nov 2, 2026 · No target date');
    expect(draft).not.toContain('overdue');
    expect(completed).toContain('Completed');
    expect(completed).not.toContain('overdue'); // #332: closed plans have none
    expect(rows[0]!.querySelector('a')!.getAttribute('href')).toBe(
      `/learning-plans/${MANAGER_LIST.items[0]!.id}`,
    );
  });

  it('truncated: the status line', async () => {
    await open({ [LIST]: () => jsonResponse(200, TRUNCATED_LIST) });
    await settleUntil(() => text().includes('Showing the latest 100 plans.'));
    expect(main().querySelector('[role="status"]')?.textContent).toBe(
      'Showing the latest 100 plans.',
    );
  });

  it('empty: the message and "New plan"', async () => {
    await open({ [LIST]: () => jsonResponse(200, EMPTY_LIST) });
    await settleUntil(() => text().includes('No learning plans yet.'));
    expect(newLink()).not.toBeNull();
  });

  it('loading, then an error with "Try again"', async () => {
    await open({
      [LIST]: [
        () => problemWith(500, 'internal_error'),
        () => jsonResponse(200, MANAGER_LIST),
      ],
    });
    await settleUntil(() => main().querySelector('[role="alert"]'));
    const retry = [...main().querySelectorAll('button')].find(
      (button) => button.textContent === 'Try again',
    )!;
    retry.click();
    await settleUntil(() => main().querySelectorAll('li').length === 3);
  });

  it('shows the loading state while the list loads', async () => {
    await open({ [LIST]: () => new Promise<Response>(() => {}) });
    await settleUntil(() => main().querySelector('[aria-busy="true"]'));
  });

  it('a 404 shows the not-found page', async () => {
    await open({ [LIST]: () => problemWith(404, 'not_found') });
    await settleUntil(() => heading()?.textContent === 'Page not found');
  });
});

describe('the student page link (A3)', () => {
  it.each([
    ['a holder', MANAGER, true],
    ['a tutor', TUTOR, true],
    ['Finance', FINANCE, false],
    ['a guardian', GUARDIAN, false],
    ['a student', STUDENT, false],
  ] as const)('%s: %s', async (_who, as, shown) => {
    await open(
      { [STUDENT_ROUTE]: () => jsonResponse(200, JOSE) },
      as,
      `/students/${STUDENT_ID}`,
    );
    await settleUntil(() => heading()?.textContent?.includes('José'));
    const link = main().querySelector(`a[href="${PAGE}"]`);
    expect(Boolean(link)).toBe(shown);
    if (link) {
      expect(link.textContent).toBe('Learning plans');
    }
  });
});
