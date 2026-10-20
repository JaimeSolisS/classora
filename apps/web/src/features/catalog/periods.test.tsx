import { act } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { applyLanguage } from '@/i18n';
import type { SessionUserBody } from '@/features/auth/session';
import {
  meAs,
  problem,
  stubApi,
  TEST_USER,
  type ApiStub,
  type Responder,
} from '@/test/api-stub';
import { alerts, click, fieldByLabel, typeInto } from '@/test/dom';
import {
  buttonNamed,
  jsonResponse,
  renderApp,
  type RenderedApp,
} from '@/test/render-app';
import type { AcademicPeriod, Closure } from './api';
import {
  CLOSURES,
  INDEPENDENCE,
  OLD_YEAR,
  PERIODS,
  SCHOOL_YEAR,
  WINTER,
} from './fixtures';
import {
  closureUpdateBody,
  periodUpdateBody,
  yearOptions,
  yearParam,
  yearRange,
} from './rangeRules';

// #38 E1-E5: the catalog tab bar, the periods and closures pages behind
// catalog.manage, the dialogs' request bodies, the period_overlap alert,
// the date checks and the closures' year filter.

const PERIODS_LIST = 'GET /api/v1/academic-periods';
const PERIOD_CREATE = 'POST /api/v1/academic-periods';
const periodPatch = (period: AcademicPeriod) =>
  `PATCH /api/v1/academic-periods/${period.id}`;
const CLOSURES_LIST = 'GET /api/v1/closures';
const CLOSURE_CREATE = 'POST /api/v1/closures';
const closurePatch = (closure: Closure) =>
  `PATCH /api/v1/closures/${closure.id}`;

const MANAGER: SessionUserBody = {
  ...TEST_USER,
  permissions: ['catalog.manage'],
};

function page<T>(items: T[]): Responder {
  return () =>
    jsonResponse(200, { items, total: items.length, limit: 25, offset: 0 });
}

function stub(
  user: SessionUserBody = MANAGER,
  routes: Record<string, Responder | Responder[]> = {},
): ApiStub {
  return stubApi({
    'GET /api/v1/auth/me': meAs(user),
    'GET /api/v1/education-levels': page([]),
    [PERIODS_LIST]: page(PERIODS),
    [CLOSURES_LIST]: page(CLOSURES),
    ...routes,
  });
}

let app: RenderedApp | undefined;

const main = () => app!.container.querySelector('main')!;
const dialog = () =>
  document.querySelector<HTMLElement>('[role="dialog"]') ?? null;
const rows = () =>
  [...main().querySelectorAll('tbody tr')].map((row) =>
    [...row.querySelectorAll('td')].map((cell) => cell.textContent),
  );
const tabs = () =>
  [...main().querySelectorAll('nav[aria-label="Catalog sections"] a')].map(
    (a) => [a.textContent, a.getAttribute('aria-current')],
  );
const statusText = () =>
  [...main().querySelectorAll('[role="status"]')]
    .map((s) => s.textContent)
    .join('');
const selectByLabel = (root: ParentNode, text: string) => {
  const label = [...root.querySelectorAll('label')].find(
    (l) => l.textContent === text,
  )!;
  return document.getElementById(label.htmlFor) as HTMLSelectElement;
};

function choose(select: HTMLSelectElement, value: string) {
  act(() => {
    select.value = value;
    select.dispatchEvent(new Event('change', { bubbles: true }));
  });
}

function queryOf(api: ApiStub, pathname: string): URLSearchParams[] {
  return api.fetch.mock.calls
    .map(([r]) => new URL(r.url))
    .filter((url) => url.pathname === pathname)
    .map((url) => url.searchParams);
}

async function openWith(button: HTMLButtonElement) {
  act(() => button.focus());
  await click(button);
  expect(dialog()).not.toBeNull();
}

function fill(values: {
  name?: string;
  type?: string;
  start?: string;
  end?: string;
}) {
  const box = dialog()!;
  if (values.name !== undefined) {
    typeInto(fieldByLabel(box, 'Name'), values.name);
  }
  if (values.type !== undefined) {
    choose(selectByLabel(box, 'Type'), values.type);
  }
  if (values.start !== undefined) {
    typeInto(fieldByLabel(box, 'Start'), values.start);
  }
  if (values.end !== undefined) {
    typeInto(fieldByLabel(box, 'End'), values.end);
  }
}

beforeEach(() => {
  applyLanguage('en');
  vi.spyOn(console, 'error').mockImplementation(() => {});
});

afterEach(() => {
  app?.unmount();
  app = undefined;
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('access and tabs (E1)', () => {
  it.each([
    ['/catalog/education-levels', 'Levels and subjects'],
    ['/catalog/periods', 'Academic periods'],
    ['/catalog/closures', 'Closures'],
  ])('%s shows the tab bar with %s current', async (path, current) => {
    stub();

    app = await renderApp(path);

    expect(tabs()).toEqual([
      [
        'Levels and subjects',
        current === 'Levels and subjects' ? 'page' : null,
      ],
      ['Academic periods', current === 'Academic periods' ? 'page' : null],
      ['Closures', current === 'Closures' ? 'page' : null],
      ['Classrooms', null],
    ]);
    const hrefs = [
      ...main().querySelectorAll('nav[aria-label="Catalog sections"] a'),
    ].map((a) => a.getAttribute('href'));
    expect(hrefs).toEqual([
      '/catalog/education-levels',
      '/catalog/periods',
      '/catalog/closures',
      '/catalog/classrooms',
    ]);
  });

  it.each(['/catalog/periods', '/catalog/closures'])(
    'without catalog.manage %s is the forbidden page and nothing is fetched',
    async (path) => {
      const api = stub({ ...TEST_USER, permissions: ['user.view'] });

      app = await renderApp(path);

      expect(main().querySelector('h1')?.textContent).toBe(
        "You don't have access to this page",
      );
      expect(api.calls(PERIODS_LIST)).toHaveLength(0);
      expect(api.calls(CLOSURES_LIST)).toHaveLength(0);
    },
  );
});

describe('the periods page (E2)', () => {
  it('lists every period with type, dates and a text status', async () => {
    const api = stub();

    app = await renderApp('/catalog/periods');

    expect(main().querySelector('h1')?.textContent).toBe('Academic periods');
    expect(document.title).toBe('Academic periods · Classora');
    expect(queryOf(api, '/api/v1/academic-periods')[0]?.get('status')).toBe(
      'all',
    );
    const [first, , last] = rows();
    expect(first?.slice(0, 5)).toEqual([
      'Ciclo 2026-2027',
      'School year',
      'Aug 1, 2026',
      'Jul 15, 2027',
      'Active',
    ]);
    expect(last?.[4]).toBe('Inactive');
  });

  it('shows the empty state', async () => {
    stub(MANAGER, { [PERIODS_LIST]: page([]) });

    app = await renderApp('/catalog/periods');

    expect(main().textContent).toContain('There are no academic periods yet.');
  });

  it('creates a period with the chosen type and dates', async () => {
    const api = stub(MANAGER, {
      [PERIOD_CREATE]: () => jsonResponse(201, SCHOOL_YEAR),
    });
    app = await renderApp('/catalog/periods');
    const add = buttonNamed(main(), 'Add period');
    await openWith(add);

    fill({
      name: '  Semestre 1 ',
      type: 'semester',
      start: '2026-08-01',
      end: '2026-12-20',
    });
    await click(buttonNamed(dialog()!, 'Save'));

    expect(api.calls(PERIOD_CREATE).map((c) => c.body)).toEqual([
      {
        name: 'Semestre 1',
        period_type: 'semester',
        starts_on: '2026-08-01',
        ends_on: '2026-12-20',
      },
    ]);
    expect(dialog()).toBeNull();
    expect(api.calls(PERIODS_LIST)).toHaveLength(2);
    expect(statusText()).toBe('Saved.');
    expect(document.activeElement).toBe(add);
  });

  it('a PATCH sends the version and only the changed dates; the type is read-only', async () => {
    const api = stub(MANAGER, {
      [periodPatch(SCHOOL_YEAR)]: () =>
        jsonResponse(200, { ...SCHOOL_YEAR, version: 4 }),
    });
    app = await renderApp('/catalog/periods');
    await openWith(buttonNamed(main(), 'Edit Ciclo 2026-2027'));
    const type = fieldByLabel(dialog()!, 'Type');
    expect(type.readOnly).toBe(true);
    expect(type.value).toBe('School year');
    expect(fieldByLabel(dialog()!, 'Start').value).toBe('2026-08-01');

    fill({ end: '2027-07-10' });
    await click(buttonNamed(dialog()!, 'Save'));

    expect(api.calls(periodPatch(SCHOOL_YEAR)).map((c) => c.body)).toEqual([
      { version: 3, ends_on: '2027-07-10' },
    ]);
  });

  it('period_overlap shows in the dialog alert region', async () => {
    stub(MANAGER, { [PERIOD_CREATE]: () => problem(409, 'period_overlap') });
    app = await renderApp('/catalog/periods');
    await openWith(buttonNamed(main(), 'Add period'));

    fill({ name: 'Otro ciclo', start: '2027-01-01', end: '2027-12-31' });
    await click(buttonNamed(dialog()!, 'Save'));

    expect(alerts(dialog()!).join('')).toContain(
      'Another active period of this type overlaps these dates.',
    );
    expect(dialog()).not.toBeNull();
  });

  it('checks the dates before sending', async () => {
    const api = stub();
    app = await renderApp('/catalog/periods');
    await openWith(buttonNamed(main(), 'Add period'));

    fill({ name: 'Ciclo', start: '2026-08-01', end: '2026-07-31' });
    await click(buttonNamed(dialog()!, 'Save'));

    expect(api.calls(PERIOD_CREATE)).toHaveLength(0);
    const end = fieldByLabel(dialog()!, 'End');
    expect(end.getAttribute('aria-invalid')).toBe('true');
    expect(dialog()!.textContent).toContain(
      "The end date can't be before the start date.",
    );

    fill({ end: '2028-08-01' });
    await click(buttonNamed(dialog()!, 'Save'));
    expect(dialog()!.textContent).toContain(
      'The dates can be at most 730 days apart.',
    );

    fill({ start: '', end: '2026-08-02' });
    await click(buttonNamed(dialog()!, 'Save'));
    expect(dialog()!.textContent).toContain('Enter the start date.');
    expect(api.calls(PERIOD_CREATE)).toHaveLength(0);
  });

  it('name_taken lands on the name field', async () => {
    stub(MANAGER, { [PERIOD_CREATE]: () => problem(409, 'name_taken') });
    app = await renderApp('/catalog/periods');
    await openWith(buttonNamed(main(), 'Add period'));

    fill({ name: 'CICLO 2026-2027', start: '2030-01-01', end: '2030-06-01' });
    await click(buttonNamed(dialog()!, 'Save'));

    expect(fieldByLabel(dialog()!, 'Name').getAttribute('aria-invalid')).toBe(
      'true',
    );
    expect(alerts(dialog()!)).toEqual([]);
  });

  it('an inactive period offers Reactivate', async () => {
    const api = stub(MANAGER, {
      [periodPatch(OLD_YEAR)]: () => problem(409, 'period_overlap'),
    });
    app = await renderApp('/catalog/periods');
    await openWith(buttonNamed(main(), 'Edit Ciclo 2025-2026'));

    await click(buttonNamed(dialog()!, 'Reactivate'));

    expect(api.calls(periodPatch(OLD_YEAR)).map((c) => c.body)).toEqual([
      { version: 2, is_active: true },
    ]);
    expect(alerts(dialog()!).join('')).toContain(
      'Another active period of this type overlaps these dates.',
    );
  });

  it('version_conflict offers Reload', async () => {
    const api = stub(MANAGER, {
      [periodPatch(SCHOOL_YEAR)]: () => problem(409, 'version_conflict'),
    });
    app = await renderApp('/catalog/periods');
    await openWith(buttonNamed(main(), 'Edit Ciclo 2026-2027'));
    fill({ name: 'Ciclo 26-27' });
    await click(buttonNamed(dialog()!, 'Save'));

    await click(buttonNamed(dialog()!, 'Reload'));

    expect(api.calls(PERIODS_LIST)).toHaveLength(2);
    expect(alerts(dialog()!)).toEqual([]);
  });
});

describe('the closures page (E3)', () => {
  it('filters by the current year in the center time zone by default', async () => {
    // 2027-01-01T05:30Z is still 2026-12-31 in America/Mexico_City.
    vi.useFakeTimers({
      toFake: ['Date'],
      now: Date.parse('2027-01-01T05:30:00Z'),
    });
    const api = stub();

    app = await renderApp('/catalog/closures');

    expect(main().querySelector('h1')?.textContent).toBe('Closures');
    const [query] = queryOf(api, '/api/v1/closures');
    expect(query?.get('from')).toBe('2026-01-01');
    expect(query?.get('to')).toBe('2026-12-31');
    expect(query?.get('status')).toBe('all');
    const year = selectByLabel(main(), 'Year');
    expect(year.value).toBe('2026');
    expect([...year.options].map((o) => o.value)).toEqual([
      '2024',
      '2025',
      '2026',
      '2027',
      '2028',
    ]);

    choose(year, '2027');
    await click(main());

    const last = queryOf(api, '/api/v1/closures').at(-1);
    expect(last?.get('from')).toBe('2027-01-01');
    expect(last?.get('to')).toBe('2027-12-31');
  });

  it('shows one date for a one-day closure and a range otherwise', async () => {
    stub();

    app = await renderApp('/catalog/closures');

    const cells = rows();
    expect(cells[0]?.slice(0, 4)).toEqual([
      'Independencia',
      'Holiday',
      'Sep 16, 2026',
      'Active',
    ]);
    expect(cells[1]?.[3]).toBe('Inactive');
    expect(cells[2]?.[2]).toBe('Dec 19, 2026 – Jan 6, 2027');
  });

  it('creates a closure and patches only the changed fields', async () => {
    const api = stub(MANAGER, {
      [CLOSURE_CREATE]: () => jsonResponse(201, INDEPENDENCE),
      [closurePatch(WINTER)]: () => jsonResponse(200, WINTER),
    });
    app = await renderApp('/catalog/closures');
    await openWith(buttonNamed(main(), 'Add closure'));

    fill({ name: 'Navidad', start: '2026-12-25', end: '2026-12-25' });
    await click(buttonNamed(dialog()!, 'Save'));
    await openWith(buttonNamed(main(), 'Edit Vacaciones de invierno'));
    fill({ type: 'closure', start: '2026-12-20' });
    await click(buttonNamed(dialog()!, 'Save'));

    expect(api.calls(CLOSURE_CREATE).map((c) => c.body)).toEqual([
      {
        name: 'Navidad',
        closure_type: 'holiday',
        starts_on: '2026-12-25',
        ends_on: '2026-12-25',
      },
    ]);
    expect(api.calls(closurePatch(WINTER)).map((c) => c.body)).toEqual([
      { version: 2, closure_type: 'closure', starts_on: '2026-12-20' },
    ]);
  });

  it('a closure can be at most 366 days long', async () => {
    const api = stub();
    app = await renderApp('/catalog/closures');
    await openWith(buttonNamed(main(), 'Add closure'));

    fill({ name: 'Año', start: '2026-01-01', end: '2027-01-03' });
    await click(buttonNamed(dialog()!, 'Save'));

    expect(api.calls(CLOSURE_CREATE)).toHaveLength(0);
    expect(dialog()!.textContent).toContain(
      'The dates can be at most 366 days apart.',
    );
  });
});

describe('rules', () => {
  it('builds update bodies with only the changed fields', () => {
    expect(
      periodUpdateBody(SCHOOL_YEAR, {
        name: SCHOOL_YEAR.name,
        type: 'semester',
        startsOn: SCHOOL_YEAR.starts_on,
        endsOn: SCHOOL_YEAR.ends_on,
      }),
    ).toBeNull();
    expect(
      closureUpdateBody(INDEPENDENCE, {
        name: 'Grito',
        type: 'holiday',
        startsOn: '2026-09-15',
        endsOn: '2026-09-16',
      }),
    ).toEqual({ version: 1, name: 'Grito', starts_on: '2026-09-15' });
  });

  it('opens a linked year outside the five options', async () => {
    vi.useFakeTimers({
      toFake: ['Date'],
      now: Date.parse('2026-10-20T18:00:00Z'),
    });
    const api = stub();

    app = await renderApp('/catalog/closures?year=2345');

    const [query] = queryOf(api, '/api/v1/closures');
    expect(query?.get('from')).toBe('2345-01-01');
    expect(
      [...selectByLabel(main(), 'Year').options].map((o) => o.value),
    ).toEqual(['2024', '2025', '2026', '2027', '2028', '2345']);
  });

  it('gives the year options and range', () => {
    expect(yearParam(new URLSearchParams('year=1899'), 2026)).toBe(2026);
    expect(yearParam(new URLSearchParams('year=abc'), 2026)).toBe(2026);
    expect(yearParam(new URLSearchParams('year=2100'), 2026)).toBe(2100);
    expect(yearOptions(2026)).toEqual([2024, 2025, 2026, 2027, 2028]);
    expect(yearRange(2026)).toEqual({ from: '2026-01-01', to: '2026-12-31' });
  });
});
