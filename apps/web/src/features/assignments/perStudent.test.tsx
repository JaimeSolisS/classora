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
  ASSIGNMENT_ID,
  FINANCE,
  GUARDIAN,
  PENDING_ITEM,
  REVIEWER,
  STUDENT,
  STUDENT_ID,
  STUDENT_LIST,
  TUTOR,
} from './fixtures';

// #313 D, A1, A3, G1 (per student): the list (h1 with the student's name
// when GET /students/{id} answers, without it otherwise, "Back to student",
// the shared summary and rows linking to the staff detail), the read-only
// detail (never the form, "Submission", "Open roster" for the creator or a
// holder), a 404, family-only users sent to their own page, Finance
// refused with no request, and the student page link per role (A3).

const LIST = `GET /api/v1/students/${STUDENT_ID}/assignments`;
const ITEM = `GET /api/v1/students/${STUDENT_ID}/assignments/${ASSIGNMENT_ID}`;
const STUDENT_ROUTE = `GET /api/v1/students/${STUDENT_ID}`;
const PAGE = `/students/${STUDENT_ID}/assignments`;
const DETAIL_PAGE = `${PAGE}/${ASSIGNMENT_ID}`;

let app: RenderedApp | undefined;
let api: ApiStub;
const main = () => app!.container.querySelector('main')!;
const text = () => main().textContent ?? '';
const heading = () => main().querySelector('h1');

function stub(routes: Routes = {}, as = TUTOR) {
  api = stubApi({
    'GET /api/v1/auth/me': meAs(as),
    [STUDENT_ROUTE]: () => jsonResponse(200, JOSE_LIMITED),
    [LIST]: () => jsonResponse(200, STUDENT_LIST),
    [ITEM]: () => jsonResponse(200, PENDING_ITEM),
    'GET /api/v1/schedule/family-students': () =>
      jsonResponse(200, { items: [] }),
    ...routes,
  });
}

async function open(path: string, routes: Routes = {}, as = TUTOR) {
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
  it.each([PAGE, DETAIL_PAGE])(
    'Finance gets access-denied on %s, no request',
    async (path) => {
      await open(path, {}, FINANCE);
      await settleUntil(() => heading());
      expect(heading()!.textContent).toBe("You don't have access to this page");
      expect(
        api.requests.filter((r) => r.path.includes('/assignments')),
      ).toHaveLength(0);
    },
  );

  it.each([
    ['a guardian', GUARDIAN, PAGE, `/my-homework?student=${STUDENT_ID}`],
    [
      'a student',
      STUDENT,
      DETAIL_PAGE,
      `/my-homework/${ASSIGNMENT_ID}?student=${STUDENT_ID}`,
    ],
  ] as const)(
    '%s goes to their own page, no staff request',
    async (_who, as, path, to) => {
      await open(path, {}, as);
      await settleUntil(() => app!.location() === to);
      expect(api.calls(STUDENT_ROUTE)).toHaveLength(0);
      expect(api.calls(LIST)).toHaveLength(0);
      expect(api.calls(ITEM)).toHaveLength(0);
    },
  );
});

describe('the list (D1)', () => {
  it('names the student, links back, rows open the staff detail', async () => {
    await open(PAGE);
    await settleUntil(() => main().querySelector('li'));
    await settleUntil(
      () => heading()?.textContent === 'Homework: José Ñúñez Pérez',
    );
    expect(
      main().querySelector(`a[href="/students/${STUDENT_ID}"]`)?.textContent,
    ).toBe('Back to student');
    expect(main().querySelector('li a')!.getAttribute('href')).toBe(
      DETAIL_PAGE,
    );
    expect(text()).toContain('Pending 1');
  });

  it('without the student: no name, no back link, the list still loads', async () => {
    await open(PAGE, { [STUDENT_ROUTE]: () => problemWith(404, 'not_found') });
    await settleUntil(() => main().querySelector('li'));
    expect(heading()!.textContent).toBe('Homework');
    expect(
      main().querySelector(`a[href="/students/${STUDENT_ID}"]`),
    ).toBeNull();
  });

  it('empty: the staff text', async () => {
    await open(PAGE, {
      [LIST]: () =>
        jsonResponse(200, {
          items: [],
          summary: { pending: 0, missing: 0, submitted: 0, graded: 0 },
          next_cursor: null,
        }),
    });
    await settleUntil(() =>
      text().includes('This student has no homework yet.'),
    );
  });
});

describe('the detail (D2)', () => {
  it.each([
    ['the creator (can_grade)', TUTOR, true, true],
    ['another tutor', TUTOR, false, false],
    ['a holder', REVIEWER, false, true],
  ] as const)('%s: "Open roster" %s', async (_who, as, canGrade, shown) => {
    await open(
      DETAIL_PAGE,
      {
        [ITEM]: () =>
          jsonResponse(200, { ...PENDING_ITEM, can_grade: canGrade }),
      },
      as,
    );
    await settleUntil(() => heading()?.textContent === PENDING_ITEM.title);
    const link = main().querySelector(
      `a[href="/assignments/${ASSIGNMENT_ID}"]`,
    );
    expect(Boolean(link)).toBe(shown);
    if (link) {
      expect(link.textContent).toBe('Open roster');
    }
    // Read-only: no form even though the fixture says can_submit.
    expect(main().querySelector('form')).toBeNull();
    expect(text()).toContain('Submission');
  });

  it('a 404 shows the not-found page', async () => {
    await open(DETAIL_PAGE, { [ITEM]: () => problemWith(404, 'not_found') });
    await settleUntil(() => heading()?.textContent === 'Page not found');
  });
});

describe('the student page link (A3)', () => {
  it.each([
    ['a holder', REVIEWER, true],
    ['a tutor', TUTOR, true],
    ['Finance', FINANCE, false],
    ['a guardian', GUARDIAN, false],
    ['a student', STUDENT, false],
  ] as const)('%s: %s', async (_who, as, shown) => {
    await open(
      `/students/${STUDENT_ID}`,
      { [STUDENT_ROUTE]: () => jsonResponse(200, JOSE) },
      as,
    );
    await settleUntil(() => heading()?.textContent?.includes('José'));
    const link = main().querySelector(`a[href="${PAGE}"]`);
    expect(Boolean(link)).toBe(shown);
    if (link) {
      expect(link.textContent).toBe('Homework');
    }
  });
});
