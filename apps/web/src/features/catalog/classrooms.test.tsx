import type { TFunction } from 'i18next';
import { act } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { windowsSummary } from '@/components/weekly-windows/summary';
import {
  sameWindows,
  validateWindows,
  type WindowRow,
} from '@/components/weekly-windows/windows';
import { applyLanguage, i18n } from '@/i18n';
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
import type { Classroom } from './api';
import { classroomUpdateBody } from './classroomRules';
import { AULA_1, BODEGA, CLASSROOMS, LABORATORIO } from './fixtures';

// #39 D1-D5: the Classrooms tab and route behind catalog.manage, the table
// with the availability summary, the create and PATCH bodies, the weekly
// windows editor (order and overlap checks, focus, labels), name_taken,
// version_conflict and the summary text in es and en.

const LIST = 'GET /api/v1/classrooms';
const CREATE = 'POST /api/v1/classrooms';
const patchOf = (classroom: Classroom) =>
  `PATCH /api/v1/classrooms/${classroom.id}`;

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
    [LIST]: page(CLASSROOMS),
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
const statusText = () =>
  [...main().querySelectorAll('[role="status"]')]
    .map((s) => s.textContent)
    .join('');
const named = <T extends HTMLElement>(name: string) =>
  dialog()!.querySelector<T>(`[aria-label="${name}"]`)!;

function choose(select: HTMLSelectElement, value: string) {
  act(() => {
    select.value = value;
    select.dispatchEvent(new Event('change', { bubbles: true }));
  });
}

async function openWith(button: HTMLButtonElement) {
  act(() => button.focus());
  await click(button);
  expect(dialog()).not.toBeNull();
}

async function addWindow(
  number: number,
  weekday: string,
  start: string,
  end: string,
) {
  await click(buttonNamed(dialog()!, 'Add window'));
  choose(named<HTMLSelectElement>(`Window ${number} day`), weekday);
  typeInto(named<HTMLInputElement>(`Window ${number} start time`), start);
  typeInto(named<HTMLInputElement>(`Window ${number} end time`), end);
}

function row(
  key: string,
  weekday: number,
  startsAt: string,
  endsAt: string,
): WindowRow {
  return { key, weekday, startsAt, endsAt };
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

describe('access and tab (D1)', () => {
  it('the Classrooms tab is current on /catalog/classrooms', async () => {
    stub();

    app = await renderApp('/catalog/classrooms');

    const links = [
      ...main().querySelectorAll('nav[aria-label="Catalog sections"] a'),
    ];
    expect(
      links.map((a) => [
        a.textContent,
        a.getAttribute('href'),
        a.getAttribute('aria-current'),
      ]),
    ).toEqual([
      ['Levels and subjects', '/catalog/education-levels', null],
      ['Academic periods', '/catalog/periods', null],
      ['Closures', '/catalog/closures', null],
      ['Classrooms', '/catalog/classrooms', 'page'],
    ]);
  });

  it('without catalog.manage the route is forbidden and nothing is fetched', async () => {
    const api = stub({ ...TEST_USER, permissions: ['user.view'] });

    app = await renderApp('/catalog/classrooms');

    expect(main().querySelector('h1')?.textContent).toBe(
      "You don't have access to this page",
    );
    expect(api.calls(LIST)).toHaveLength(0);
  });
});

describe('the classrooms page (D2)', () => {
  it('lists every classroom with capacity, the summary and a text status', async () => {
    const api = stub();

    app = await renderApp('/catalog/classrooms');

    expect(main().querySelector('h1')?.textContent).toBe('Classrooms');
    expect(document.title).toBe('Classrooms · Classora');
    const request = api.fetch.mock.calls
      .map(([r]) => new URL(r.url))
      .find((url) => url.pathname === '/api/v1/classrooms');
    expect(request?.searchParams.get('status')).toBe('all');
    expect(rows().map((cells) => cells.slice(0, 4))).toEqual([
      ['Aula 1', '12', 'Mon–Fri 09:00–20:00; Sat 09:00–14:00', 'Active'],
      ['Laboratorio', '20', 'No restriction', 'Active'],
      ['Sala de juntas', '6', 'Mon 09:00–12:00, 12:00–14:00', 'Inactive'],
    ]);
  });

  it('shows the empty state', async () => {
    stub(MANAGER, { [LIST]: page([]) });

    app = await renderApp('/catalog/classrooms');

    expect(main().textContent).toContain('There are no classrooms yet.');
  });
});

describe('the classroom dialog (D3, D4)', () => {
  it('creates a classroom with two touching windows', async () => {
    const api = stub(MANAGER, {
      [CREATE]: () => jsonResponse(201, LABORATORIO),
    });
    app = await renderApp('/catalog/classrooms');
    const add = buttonNamed(main(), 'Add classroom');
    await openWith(add);

    typeInto(fieldByLabel(dialog()!, 'Name'), '  Aula 2 ');
    typeInto(fieldByLabel(dialog()!, 'Capacity'), '12');
    await addWindow(1, '1', '09:00', '12:00');
    await addWindow(2, '1', '12:00', '14:00');
    await click(buttonNamed(dialog()!, 'Save'));

    expect(api.calls(CREATE).map((c) => c.body)).toEqual([
      {
        name: 'Aula 2',
        capacity: 12,
        availability: [
          { weekday: 1, starts_at: '09:00', ends_at: '12:00' },
          { weekday: 1, starts_at: '12:00', ends_at: '14:00' },
        ],
      },
    ]);
    expect(dialog()).toBeNull();
    expect(api.calls(LIST)).toHaveLength(2);
    expect(statusText()).toBe('Saved.');
    expect(document.activeElement).toBe(add);
  });

  it('checks the capacity before sending', async () => {
    const api = stub();
    app = await renderApp('/catalog/classrooms');
    await openWith(buttonNamed(main(), 'Add classroom'));

    typeInto(fieldByLabel(dialog()!, 'Name'), 'Aula');
    for (const value of ['0', '501', '', '2.5']) {
      typeInto(fieldByLabel(dialog()!, 'Capacity'), value);
      await click(buttonNamed(dialog()!, 'Save'));
      const capacity = fieldByLabel(dialog()!, 'Capacity');
      expect(capacity.getAttribute('aria-invalid')).toBe('true');
      expect(dialog()!.textContent).toContain(
        'The capacity is a whole number from 1 to 500.',
      );
    }
    expect(api.calls(CREATE)).toHaveLength(0);
  });

  it('an overlapping window gets a row error and nothing is sent', async () => {
    const api = stub();
    app = await renderApp('/catalog/classrooms');
    await openWith(buttonNamed(main(), 'Add classroom'));
    typeInto(fieldByLabel(dialog()!, 'Name'), 'Aula');
    typeInto(fieldByLabel(dialog()!, 'Capacity'), '10');

    await addWindow(1, '1', '09:00', '12:00');
    await addWindow(2, '1', '12:00', '14:00');
    await addWindow(3, '1', '11:00', '13:00');
    await click(buttonNamed(dialog()!, 'Save'));

    expect(api.calls(CREATE)).toHaveLength(0);
    const start = named<HTMLInputElement>('Window 3 start time');
    expect(start.getAttribute('aria-invalid')).toBe('true');
    expect(document.activeElement).toBe(start);
    const error = document.getElementById(
      start.getAttribute('aria-describedby')!,
    );
    expect(error?.textContent).toBe(
      'This window overlaps another window on the same day.',
    );
    expect(
      named<HTMLInputElement>('Window 2 start time').getAttribute(
        'aria-invalid',
      ),
    ).toBeNull();

    // Fixed on another weekday: the error goes away while editing.
    choose(named<HTMLSelectElement>('Window 3 day'), '2');
    expect(start.getAttribute('aria-invalid')).toBeNull();
  });

  it('an end before the start is an error on the end', async () => {
    stub();
    app = await renderApp('/catalog/classrooms');
    await openWith(buttonNamed(main(), 'Add classroom'));
    typeInto(fieldByLabel(dialog()!, 'Name'), 'Aula');
    typeInto(fieldByLabel(dialog()!, 'Capacity'), '10');

    await addWindow(1, '3', '14:00', '09:00');
    await click(buttonNamed(dialog()!, 'Save'));

    const end = named<HTMLInputElement>('Window 1 end time');
    expect(end.getAttribute('aria-invalid')).toBe('true');
    expect(dialog()!.textContent).toContain(
      'The end time must be after the start time.',
    );
  });

  it('Add window focuses the new day; Remove focuses Add window', async () => {
    stub();
    app = await renderApp('/catalog/classrooms');
    await openWith(buttonNamed(main(), 'Add classroom'));

    await click(buttonNamed(dialog()!, 'Add window'));
    expect(document.activeElement).toBe(named('Window 1 day'));
    await click(buttonNamed(dialog()!, 'Add window'));
    expect(document.activeElement).toBe(named('Window 2 day'));

    await click(named<HTMLButtonElement>('Remove window 1'));
    expect(dialog()!.querySelector('[aria-label="Window 2 day"]')).toBeNull();
    expect(document.activeElement).toBe(buttonNamed(dialog()!, 'Add window'));
  });

  it('a PATCH sends the version and only the changed fields', async () => {
    const api = stub(MANAGER, {
      [patchOf(AULA_1)]: () => jsonResponse(200, { ...AULA_1, version: 2 }),
    });
    app = await renderApp('/catalog/classrooms');
    await openWith(buttonNamed(main(), 'Edit Aula 1'));
    expect(fieldByLabel(dialog()!, 'Capacity').value).toBe('12');
    expect(named<HTMLInputElement>('Window 6 end time').value).toBe('14:00');

    typeInto(fieldByLabel(dialog()!, 'Capacity'), '15');
    await click(buttonNamed(dialog()!, 'Save'));

    expect(api.calls(patchOf(AULA_1)).map((c) => c.body)).toEqual([
      { version: 1, capacity: 15 },
    ]);
  });

  it('a changed window sends the full list', async () => {
    const api = stub(MANAGER, {
      [patchOf(BODEGA)]: () => jsonResponse(200, { ...BODEGA, version: 3 }),
    });
    app = await renderApp('/catalog/classrooms');
    await openWith(buttonNamed(main(), 'Edit Sala de juntas'));

    typeInto(named<HTMLInputElement>('Window 2 end time'), '15:00');
    await click(buttonNamed(dialog()!, 'Save'));

    expect(api.calls(patchOf(BODEGA)).map((c) => c.body)).toEqual([
      {
        version: 2,
        availability: [
          { weekday: 1, starts_at: '09:00', ends_at: '12:00' },
          { weekday: 1, starts_at: '12:00', ends_at: '15:00' },
        ],
      },
    ]);
  });

  it('removing every window sends an empty list', async () => {
    const api = stub(MANAGER, {
      [patchOf(BODEGA)]: () => jsonResponse(200, { ...BODEGA, version: 3 }),
    });
    app = await renderApp('/catalog/classrooms');
    await openWith(buttonNamed(main(), 'Edit Sala de juntas'));

    await click(named<HTMLButtonElement>('Remove window 2'));
    await click(named<HTMLButtonElement>('Remove window 1'));
    expect(dialog()!.textContent).toContain(
      'No windows: available at any time.',
    );
    await click(buttonNamed(dialog()!, 'Save'));

    expect(api.calls(patchOf(BODEGA)).map((c) => c.body)).toEqual([
      { version: 2, availability: [] },
    ]);
  });

  it('Save without a change sends nothing and closes', async () => {
    const api = stub();
    app = await renderApp('/catalog/classrooms');
    await openWith(buttonNamed(main(), 'Edit Aula 1'));

    await click(buttonNamed(dialog()!, 'Save'));

    expect(api.calls(patchOf(AULA_1))).toHaveLength(0);
    expect(dialog()).toBeNull();
  });

  it('Deactivate sends is_active false; an inactive room offers Reactivate', async () => {
    const api = stub(MANAGER, {
      [patchOf(AULA_1)]: () =>
        jsonResponse(200, { ...AULA_1, is_active: false, version: 2 }),
    });
    app = await renderApp('/catalog/classrooms');
    await openWith(buttonNamed(main(), 'Edit Aula 1'));

    await click(buttonNamed(dialog()!, 'Deactivate'));

    expect(api.calls(patchOf(AULA_1)).map((c) => c.body)).toEqual([
      { version: 1, is_active: false },
    ]);
    await openWith(buttonNamed(main(), 'Edit Sala de juntas'));
    expect(buttonNamed(dialog()!, 'Reactivate')).toBeTruthy();
  });

  it('name_taken lands on the name field', async () => {
    stub(MANAGER, { [CREATE]: () => problem(409, 'name_taken') });
    app = await renderApp('/catalog/classrooms');
    await openWith(buttonNamed(main(), 'Add classroom'));

    typeInto(fieldByLabel(dialog()!, 'Name'), 'AULA 1');
    typeInto(fieldByLabel(dialog()!, 'Capacity'), '5');
    await click(buttonNamed(dialog()!, 'Save'));

    const name = fieldByLabel(dialog()!, 'Name');
    expect(name.getAttribute('aria-invalid')).toBe('true');
    expect(document.activeElement).toBe(name);
    expect(alerts(dialog()!)).toEqual([]);
  });

  it('a server window error lands on its row', async () => {
    stub(MANAGER, {
      [CREATE]: () =>
        new Response(
          JSON.stringify({
            type: 'about:blank',
            title: 'Problem',
            status: 422,
            code: 'validation_error',
            request_id: 'req-1',
            errors: [
              {
                path: ['body', 'availability', 0, 'starts_at'],
                code: 'window_overlap',
                message: 'x',
              },
            ],
          }),
          {
            status: 422,
            headers: { 'Content-Type': 'application/problem+json' },
          },
        ),
    });
    app = await renderApp('/catalog/classrooms');
    await openWith(buttonNamed(main(), 'Add classroom'));
    typeInto(fieldByLabel(dialog()!, 'Name'), 'Aula');
    typeInto(fieldByLabel(dialog()!, 'Capacity'), '5');
    await addWindow(1, '1', '09:00', '10:00');

    await click(buttonNamed(dialog()!, 'Save'));

    const start = named<HTMLInputElement>('Window 1 start time');
    expect(start.getAttribute('aria-invalid')).toBe('true');
    expect(document.activeElement).toBe(start);
  });

  it('version_conflict offers Reload, which shows the new version', async () => {
    const newer = { ...AULA_1, capacity: 30, version: 2 };
    const api = stub(MANAGER, {
      [patchOf(AULA_1)]: () => problem(409, 'version_conflict'),
      [LIST]: [page(CLASSROOMS), page([newer, LABORATORIO, BODEGA])],
    });
    app = await renderApp('/catalog/classrooms');
    await openWith(buttonNamed(main(), 'Edit Aula 1'));
    typeInto(fieldByLabel(dialog()!, 'Capacity'), '15');
    await click(buttonNamed(dialog()!, 'Save'));

    await click(buttonNamed(dialog()!, 'Reload'));

    expect(api.calls(LIST)).toHaveLength(2);
    expect(fieldByLabel(dialog()!, 'Capacity').value).toBe('30');
  });
});

describe('weekly windows rules (D3, D5)', () => {
  it('reports the later of two overlapping windows; touching is fine', () => {
    expect(
      validateWindows([
        row('a', 1, '09:00', '12:00'),
        row('b', 1, '12:00', '14:00'),
        row('c', 1, '11:00', '13:00'),
        row('d', 2, '11:00', '13:00'),
      ]),
    ).toEqual([{}, {}, { startsAt: 'weeklyWindows.validation.overlap' }, {}]);
  });

  it('checks the times and their order', () => {
    expect(
      validateWindows([
        row('a', 1, '', '12:00'),
        row('b', 1, '12:00', '12:00'),
        row('c', 1, '13:00', '12:30'),
      ]),
    ).toEqual([
      { startsAt: 'weeklyWindows.validation.timeRequired' },
      { endsAt: 'weeklyWindows.validation.endsAfterStart' },
      { endsAt: 'weeklyWindows.validation.endsAfterStart' },
    ]);
  });

  it('compares windows whatever their order', () => {
    const monday = { weekday: 1, starts_at: '09:00', ends_at: '12:00' };
    const friday = { weekday: 5, starts_at: '09:00', ends_at: '12:00' };

    expect(sameWindows([monday, friday], [friday, monday])).toBe(true);
    expect(sameWindows([monday], [monday, friday])).toBe(false);
    expect(sameWindows([], [])).toBe(true);
  });

  it('classroomUpdateBody sends availability only when the set changed', () => {
    const same = {
      name: BODEGA.name,
      capacity: BODEGA.capacity,
      availability: [...BODEGA.availability].reverse(),
    };

    expect(classroomUpdateBody(BODEGA, same)).toBeNull();
    expect(classroomUpdateBody(BODEGA, { ...same, availability: [] })).toEqual({
      version: 2,
      availability: [],
    });
  });

  it.each([
    ['es', 'Lun–Vie 09:00–20:00; Sáb 09:00–14:00', 'Sin restricción'],
    ['en', 'Mon–Fri 09:00–20:00; Sat 09:00–14:00', 'No restriction'],
  ] as const)('the summary in %s', (language, week, none) => {
    applyLanguage(language);
    const t = i18n.t.bind(i18n) as TFunction;

    expect(windowsSummary(AULA_1.availability, t)).toBe(week);
    expect(windowsSummary([], t)).toBe(none);
  });

  it('groups only consecutive days with the same windows', () => {
    const t = i18n.t.bind(i18n) as TFunction;

    expect(
      windowsSummary(
        [
          { weekday: 3, starts_at: '09:00', ends_at: '12:00' },
          { weekday: 1, starts_at: '09:00', ends_at: '12:00' },
          { weekday: 2, starts_at: '09:00', ends_at: '12:00' },
          { weekday: 2, starts_at: '16:00', ends_at: '18:00' },
          { weekday: 7, starts_at: '10:00', ends_at: '11:00' },
        ],
        t,
      ),
    ).toBe(
      'Mon 09:00–12:00; Tue 09:00–12:00, 16:00–18:00; Wed 09:00–12:00; Sun 10:00–11:00',
    );
  });
});
