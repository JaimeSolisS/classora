import { act } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { routes } from '@/app/router';
import type { SessionUserBody } from '@/features/auth/session';
import { applyLanguage } from '@/i18n';
import type { LocalDate } from '@/lib/time';
import {
  meAs,
  problem,
  stubApi,
  TEST_USER,
  type ApiStub,
  type Responder,
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
import {
  CARMEN_ID,
  FETCHED_AT,
  FULL_PAGE,
  LEFT_ID,
  OVERDUE_ITEMS,
  overduePage,
} from './fixtures';
import { OverdueCloseoutsPage } from './OverdueCloseoutsPage';
import {
  hasOverdueFilters,
  overdueQuery,
  overdueSearchParams,
  parseOverdueState,
  rangeInvalid,
} from './urlState';

// #301b H1: the overdue closeouts page. The URL state and the query it
// sends, every column (the "Can't submit" badge, the Open session link to
// the Closeout section), the filters (tutor chip and its name fallback,
// the date range, `to` before `from` blocked, Clear filters, a filter
// change back to page 1), the empty, loading and error states, a page past
// the end, pagination and the scroll region. No student names.

const PATH = '/schedule/overdue-closeouts';
const LIST = 'GET /api/v1/closeouts/overdue';

const REVIEWER: SessionUserBody = {
  ...TEST_USER,
  roles: ['academic_coordinator'],
  permissions: ['schedule.view', 'teaching_hours.approve'],
};
const FINANCE: SessionUserBody = {
  ...TEST_USER,
  roles: ['finance_staff'],
  permissions: ['schedule.view'],
};

let app: RenderedApp | undefined;
let api: ApiStub;
const main = () => app!.container.querySelector('main')!;

function listUrls(): URL[] {
  return api.fetch.mock.calls
    .map(([request]) => new URL(request.url))
    .filter((url) => url.pathname === '/api/v1/closeouts/overdue');
}

async function open(
  path = PATH,
  list: Responder | Responder[] = () => jsonResponse(200, overduePage()),
  user: SessionUserBody = REVIEWER,
  extra: Record<string, Responder | Responder[]> = {},
): Promise<void> {
  api = stubApi({
    'GET /api/v1/auth/me': meAs(user),
    [LIST]: list,
    ...extra,
  });
  app = await renderApp(path);
  await settleUntil(() =>
    (main()?.textContent ?? '').includes('Overdue closeouts'),
  );
  await settle();
}

function setDate(label: string, value: string): void {
  const input = [...main().querySelectorAll('label')]
    .filter((l) => l.textContent === label)
    .map((l) =>
      main().querySelector<HTMLInputElement>(`#${CSS.escape(l.htmlFor)}`),
    )[0]!;
  // eslint-disable-next-line @typescript-eslint/unbound-method
  const setter = Object.getOwnPropertyDescriptor(
    HTMLInputElement.prototype,
    'value',
  )?.set;
  act(() => {
    setter?.call(input, value);
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
  });
}

beforeEach(() => {
  applyLanguage('en');
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(FETCHED_AT);
});

afterEach(() => {
  app?.unmount();
  app = undefined;
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('the URL state (#301b A5)', () => {
  it('reads every parameter and writes back only what is set', () => {
    const params = new URLSearchParams(
      `tutor=${CARMEN_ID.toUpperCase()}&from=2026-11-01&to=2026-11-30&page=3`,
    );
    const state = parseOverdueState(params);
    expect(state).toEqual({
      tutor: CARMEN_ID,
      from: '2026-11-01',
      to: '2026-11-30',
      page: 3,
    });
    expect(overdueSearchParams(state).toString()).toBe(
      `tutor=${CARMEN_ID}&from=2026-11-01&to=2026-11-30&page=3`,
    );
    expect(overdueQuery(state)).toEqual({
      limit: 25,
      offset: 50,
      tutor_user_id: CARMEN_ID,
      from: '2026-11-01',
      to: '2026-11-30',
    });
    expect(
      overdueSearchParams({
        tutor: null,
        from: null,
        to: null,
        page: 1,
      }).toString(),
    ).toBe('');
  });

  it('ignores invalid values', () => {
    expect(
      parseOverdueState(
        new URLSearchParams('tutor=abc&from=2026-13-01&to=x&page=0'),
      ),
    ).toEqual({ tutor: null, from: null, to: null, page: 1 });
    expect(parseOverdueState(new URLSearchParams('page=-2')).page).toBe(1);
    expect(parseOverdueState(new URLSearchParams('page=1.5')).page).toBe(1);
  });

  it('knows a reversed range and whether a filter is set', () => {
    const from = '2026-11-10' as LocalDate;
    const to = '2026-11-09' as LocalDate;
    expect(rangeInvalid({ tutor: null, from, to })).toBe(true);
    expect(rangeInvalid({ tutor: null, from, to: from })).toBe(false);
    expect(rangeInvalid({ tutor: null, from, to: null })).toBe(false);
    expect(hasOverdueFilters({ tutor: null, from: null, to: null })).toBe(
      false,
    );
    expect(hasOverdueFilters({ tutor: null, from, to: null })).toBe(true);
  });
});

describe('the overdue closeouts page (#301b A)', () => {
  it('is code-split and needs teaching_hours.approve (A1)', async () => {
    const route = routes
      .flatMap((r) => r.children ?? [])
      .flatMap((r) => r.children ?? [])
      .find((r) => r.path === 'schedule/overdue-closeouts');
    const lazy = route?.lazy as
      (() => Promise<{ Component: unknown }>) | undefined;
    expect(route?.element).toBeUndefined();
    expect(route?.handle).toEqual({ permission: 'teaching_hours.approve' });
    expect((await lazy!()).Component).toBe(OverdueCloseoutsPage);
  });

  it('denies Finance without reading the list (A1)', async () => {
    api = stubApi({
      'GET /api/v1/auth/me': meAs(FINANCE),
      [LIST]: () => jsonResponse(200, overduePage()),
    });
    app = await renderApp(PATH);
    await settleUntil(() => main()?.querySelector('h1') !== null);
    await settle();

    expect(main().querySelector('h1')?.textContent).not.toBe(
      'Overdue closeouts',
    );
    expect(listUrls()).toHaveLength(0);
  });

  it('shows every column in the API order, with no student names (A2-A4)', async () => {
    await open();

    expect(document.title).toContain('Overdue closeouts');
    expect(main().textContent).toContain(
      'Classes that have ended without a closeout, oldest first.',
    );
    expect(main().querySelector('a[href="/schedule"]')?.textContent).toBe(
      'Back to schedule',
    );
    const [url] = listUrls();
    expect(Object.fromEntries(url!.searchParams)).toEqual({
      limit: '25',
      offset: '0',
    });

    const rows = [...main().querySelectorAll('tbody tr')];
    expect(rows).toHaveLength(3);
    const first = rows[0]!.textContent ?? '';
    expect(first).toContain('Nov 9, 2026, 10:00 AM – 11:00 AM');
    expect(first).toContain('Ended 26 hours ago');
    expect(first).toContain('Matemáticas');
    expect(first).toContain('Secundaria');
    expect(first).toContain('Carmen Ruiz');
    expect(first).not.toContain("Can't submit");
    expect(first).toContain('Group');
    expect(first).toContain('3 students');
    expect(first).toContain('1 of 3 marked');
    const second = rows[1]!.textContent ?? '';
    expect(second).toContain('Diego Salas');
    expect(second).toContain("Can't submit");
    expect(second).toContain('Individual');
    expect(second).toContain('1 student');
    expect(rows[2]!.textContent).toContain('Ended 2 hours ago');

    const link = rows[0]!.querySelector('a')!;
    expect(link.getAttribute('href')).toBe(
      `/schedule/sessions/${OVERDUE_ITEMS[0]!.class_session_id}#closeout`,
    );
    expect(link.textContent).toBe('Open session');
    expect(link.getAttribute('aria-label')).toBe(
      'Open session: Matemáticas, Nov 9, 2026, 10:00 AM',
    );
    expect(main().querySelectorAll('th button')).toHaveLength(0);
  });

  it('puts the table in a focusable region named by the heading (A8)', async () => {
    await open();
    const heading = main().querySelector('h1')!;
    const region = main().querySelector<HTMLElement>('[role="region"]')!;
    expect(region.getAttribute('aria-labelledby')).toBe(heading.id);
    expect(region.tabIndex).toBe(0);
    expect(region.querySelector('table')?.getAttribute('aria-labelledby')).toBe(
      heading.id,
    );
  });

  it('sends the URL filters and names the tutor from the first row (A5)', async () => {
    await open(
      `${PATH}?tutor=${CARMEN_ID}&from=2026-11-01&to=2026-11-30&page=2`,
      () =>
        jsonResponse(
          200,
          overduePage([OVERDUE_ITEMS[0]!], { total: 26, offset: 25 }),
        ),
    );
    const [url] = listUrls();
    expect(Object.fromEntries(url!.searchParams)).toEqual({
      limit: '25',
      offset: '25',
      tutor_user_id: CARMEN_ID,
      from: '2026-11-01',
      to: '2026-11-30',
    });
    expect(buttonNamed(main(), 'Tutor: Carmen Ruiz')).toBeTruthy();
    expect(
      buttonNamed(main(), 'Tutor: Carmen Ruiz').getAttribute('aria-label'),
    ).toBe('Remove filter: Tutor Carmen Ruiz');
    // Pagination (total > 25).
    expect(main().textContent).toContain('26–26 of 26');
  });

  it('falls back to "Selected tutor" and removes the chip back to page 1', async () => {
    await open(`${PATH}?tutor=${LEFT_ID}&page=2`, [
      () =>
        jsonResponse(
          200,
          overduePage([OVERDUE_ITEMS[0]!], { total: 30, offset: 25 }),
        ),
      () => jsonResponse(200, overduePage()),
    ]);
    await click(buttonNamed(main(), 'Tutor: Selected tutor'));
    await settle();
    expect(app!.location()).toBe(PATH);
  });

  it('ignores invalid URL values', async () => {
    await open(`${PATH}?tutor=nope&from=2026-02-30&page=x`);
    const [url] = listUrls();
    expect(Object.fromEntries(url!.searchParams)).toEqual({
      limit: '25',
      offset: '0',
    });
    expect(main().textContent).not.toContain('Clear filters');
  });

  it('resets the page when a filter changes, and clears the filters', async () => {
    await open(`${PATH}?from=2026-11-01&page=2`, () =>
      jsonResponse(200, overduePage(FULL_PAGE, { total: 30, offset: 25 })),
    );
    setDate('To', '2026-11-30');
    await settle();
    expect(app!.location()).toBe(`${PATH}?from=2026-11-01&to=2026-11-30`);

    await click(buttonNamed(main(), 'Clear filters'));
    await settle();
    expect(app!.location()).toBe(PATH);
  });

  it('blocks a reversed range without a request (A5)', async () => {
    await open(`${PATH}?from=2026-11-10&to=2026-11-01`);
    expect(listUrls()).toHaveLength(0);
    const label = [...main().querySelectorAll('label')].find(
      (l) => l.textContent === 'To',
    )!;
    const input = main().querySelector(`#${CSS.escape(label.htmlFor)}`)!;
    expect(input.getAttribute('aria-invalid')).toBe('true');
    const error = main().querySelector(
      `#${CSS.escape(input.getAttribute('aria-describedby')!)}`,
    );
    expect(error?.textContent).toBe("“To” can't be before “From”.");
  });

  it('maps a server range_invalid to the same message', async () => {
    await open(
      `${PATH}?from=2026-11-01`,
      () =>
        new Response(
          JSON.stringify({
            type: 'about:blank',
            title: 'Unprocessable Content',
            status: 422,
            code: 'validation_error',
            request_id: 'req-422',
            errors: [
              { path: ['query', 'to'], code: 'range_invalid', message: 'x' },
            ],
          }),
          {
            status: 422,
            headers: { 'Content-Type': 'application/problem+json' },
          },
        ),
    );
    await settleUntil(() =>
      (main().textContent ?? '').includes("“To” can't be before “From”."),
    );
    expect(main().querySelector('[role="alert"]')).toBeNull();
  });

  it('says when every ended class has a closeout (A7)', async () => {
    await open(PATH, () => jsonResponse(200, overduePage([])));
    const statuses = [...main().querySelectorAll('[role="status"]')].map(
      (node) => node.textContent,
    );
    expect(statuses).toContain('Every class that has ended has a closeout.');
    expect(main().querySelector('table')).toBeNull();
    expect(main().textContent).not.toContain('Clear filters');
  });

  it('says when no class matches the filters (A7)', async () => {
    await open(`${PATH}?from=2026-11-01`, () =>
      jsonResponse(200, overduePage([])),
    );
    expect(main().textContent).toContain(
      'No overdue closeouts match these filters.',
    );
    expect(buttonNamed(main(), 'Clear filters')).toBeTruthy();
  });

  it('shows loading, then an error with Try again (A7)', async () => {
    api = stubApi({ 'GET /api/v1/auth/me': meAs(REVIEWER) });
    app = await renderApp(PATH);
    await settleUntil(() => main()?.querySelector('h1') !== null);
    expect(main().querySelector('[aria-busy="true"]')).not.toBeNull();
    app.unmount();

    await open(PATH, [
      () => problem(403, 'permission_denied'),
      () => jsonResponse(200, overduePage()),
    ]);
    await settleUntil(() => main().querySelector('[role="alert"]') !== null);
    expect(main().textContent).toContain(
      "You don't have permission to do this.",
    );
    await click(buttonNamed(main(), 'Try again'));
    await settleUntil(() => main().querySelectorAll('tbody tr').length === 3);
  });

  it('goes back to page 1 from a page past the end (A5)', async () => {
    await open(`${PATH}?page=4`, (request) =>
      new URL(request.url).searchParams.get('offset') === '75'
        ? jsonResponse(200, overduePage([], { total: 3, offset: 75 }))
        : jsonResponse(200, overduePage()),
    );
    await settleUntil(() => app!.location() === PATH);
    await settleUntil(() => main().querySelectorAll('tbody tr').length === 3);
  });

  it('pages by 25 through Next (A6)', async () => {
    await open(PATH, () =>
      jsonResponse(200, overduePage(FULL_PAGE, { total: 30 })),
    );
    expect(main().textContent).toContain('1–25 of 30');
    await click(buttonNamed(main(), 'Next'));
    await settle();
    expect(app!.location()).toBe(`${PATH}?page=2`);
  });
});
