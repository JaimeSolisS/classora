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
import type {
  EducationLevel,
  EducationLevelPage,
  Subject,
  SubjectPage,
} from './api';
import {
  ALGEBRA,
  HISTORIA,
  LEVELS,
  POSGRADO,
  POSGRADO_SUBJECT,
  PRIMARIA,
  SECUNDARIA,
} from './fixtures';

// #32 I3 (and H1-H4, I1, I2, J3): the Catalog navigation item, the levels
// page and the level page behind catalog.manage; the dialog's request
// bodies, error placement and version_conflict reload; "Add subject"
// disabled on an inactive level.

const LIST = 'GET /api/v1/education-levels';
const CREATE = 'POST /api/v1/education-levels';
const levelUrl = (level: EducationLevel) =>
  `/api/v1/education-levels/${level.id}`;
const SUBJECTS = 'GET /api/v1/subjects';
const subjectPatch = (subject: Subject) =>
  `PATCH /api/v1/subjects/${subject.id}`;

/** A catalog manager (an Owner or Administrator). */
const MANAGER: SessionUserBody = {
  ...TEST_USER,
  permissions: ['catalog.manage', 'user.view'],
};

function levelsPage(items: EducationLevel[]): Responder {
  const body: EducationLevelPage = {
    items,
    total: items.length,
    limit: 25,
    offset: 0,
  };
  return () => jsonResponse(200, body);
}

function subjectsPage(items: Subject[]): Responder {
  const body: SubjectPage = {
    items,
    total: items.length,
    limit: 25,
    offset: 0,
  };
  return () => jsonResponse(200, body);
}

function stub(
  user: SessionUserBody = MANAGER,
  routes: Record<string, Responder | Responder[]> = {},
): ApiStub {
  return stubApi({
    'GET /api/v1/auth/me': meAs(user),
    [LIST]: levelsPage(LEVELS),
    [`GET ${levelUrl(SECUNDARIA)}`]: () => jsonResponse(200, SECUNDARIA),
    [`GET ${levelUrl(POSGRADO)}`]: () => jsonResponse(200, POSGRADO),
    [SUBJECTS]: subjectsPage([ALGEBRA, HISTORIA]),
    ...routes,
  });
}

let app: RenderedApp | undefined;

const main = () => app!.container.querySelector('main')!;
const navItems = () =>
  [...app!.container.querySelectorAll('header nav a')].map(
    (a) => a.textContent,
  );
const dialog = () =>
  document.querySelector<HTMLElement>('[role="dialog"]') ?? null;
const statusText = () =>
  [...main().querySelectorAll('[role="status"]')]
    .map((s) => s.textContent)
    .join('');
const rowNames = () =>
  [...main().querySelectorAll('tbody tr')].map(
    (row) => row.querySelector('td')?.textContent,
  );
const editButton = (name: string) => buttonNamed(main(), `Edit ${name}`);

async function openWith(button: HTMLButtonElement) {
  act(() => button.focus());
  await click(button);
  expect(dialog()).not.toBeNull();
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

describe('access (H1, I1, I3)', () => {
  it('shows the Catalog item and the levels page to catalog managers', async () => {
    const api = stub();

    app = await renderApp('/catalog/education-levels');

    expect(navItems()).toContain('Catalog');
    expect(navItems().at(-1)).toBe('Catalog');
    expect(main().querySelectorAll('h1')).toHaveLength(1);
    expect(main().querySelector('h1')?.textContent).toBe(
      'Education levels and subjects',
    );
    expect(document.title).toBe('Education levels and subjects · Classora');
    const [request] = api.fetch.mock.calls
      .map(([r]) => new URL(r.url))
      .filter((url) => url.pathname === '/api/v1/education-levels');
    expect(request?.searchParams.get('status')).toBe('all');
  });

  it.each([
    '/catalog/education-levels',
    `/catalog/education-levels/${SECUNDARIA.id}`,
  ])(
    'without catalog.manage %s is the forbidden page and nothing is fetched',
    async (path) => {
      const api = stub({ ...TEST_USER, permissions: ['user.view'] });

      app = await renderApp(path);

      expect(navItems()).not.toContain('Catalog');
      expect(main().querySelector('h1')?.textContent).toBe(
        "You don't have access to this page",
      );
      expect(api.calls(LIST)).toHaveLength(0);
      expect(api.calls(SUBJECTS)).toHaveLength(0);
    },
  );
});

describe('the levels page (H2)', () => {
  it('lists every level with its link, position, count and a text status', async () => {
    stub();

    app = await renderApp('/catalog/education-levels');

    expect(rowNames()).toEqual([
      'Primaria',
      'Secundaria',
      'Preparatoria',
      'Posgrado',
    ]);
    const link = [...main().querySelectorAll('a')].find(
      (a) => a.textContent === 'Primaria',
    );
    expect(link?.getAttribute('href')).toBe(
      `/catalog/education-levels/${PRIMARIA.id}`,
    );
    const posgrado = [...main().querySelectorAll('tbody tr')][3]!;
    expect(posgrado.textContent).toContain('50');
    expect(posgrado.textContent).toContain('Inactive');
    expect([...main().querySelectorAll('tbody tr')][0]!.textContent).toContain(
      'Active',
    );
    expect(main().textContent).toContain('1–4 of 4');
  });

  it('shows the empty state', async () => {
    stub(MANAGER, { [LIST]: levelsPage([]) });

    app = await renderApp('/catalog/education-levels');

    expect(main().textContent).toContain('There are no education levels yet.');
  });
});

describe('the level dialog (H3, H4)', () => {
  it('creates a level with a position, closes, refetches and gives focus back', async () => {
    const api = stub(MANAGER, {
      [CREATE]: () => jsonResponse(201, { ...PRIMARIA, id: 'new' }),
    });
    app = await renderApp('/catalog/education-levels');
    const add = buttonNamed(main(), 'Add level');
    await openWith(add);

    typeInto(fieldByLabel(dialog()!, 'Name'), '  Maestría ');
    typeInto(fieldByLabel(dialog()!, 'Position'), '45');
    await click(buttonNamed(dialog()!, 'Save'));

    expect(api.calls(CREATE).map((c) => c.body)).toEqual([
      { name: 'Maestría', sort_order: 45 },
    ]);
    expect(dialog()).toBeNull();
    expect(api.calls(LIST)).toHaveLength(2);
    expect(statusText()).toBe('Saved.');
    expect(document.activeElement).toBe(add);
  });

  it('creates a level without a position', async () => {
    const api = stub(MANAGER, {
      [CREATE]: () => jsonResponse(201, PRIMARIA),
    });
    app = await renderApp('/catalog/education-levels');
    await openWith(buttonNamed(main(), 'Add level'));

    typeInto(fieldByLabel(dialog()!, 'Name'), 'Maestría');
    await click(buttonNamed(dialog()!, 'Save'));

    expect(api.calls(CREATE).map((c) => c.body)).toEqual([
      { name: 'Maestría' },
    ]);
  });

  it('checks the name and position before sending', async () => {
    const api = stub();
    app = await renderApp('/catalog/education-levels');
    await openWith(buttonNamed(main(), 'Add level'));

    typeInto(fieldByLabel(dialog()!, 'Name'), '   ');
    typeInto(fieldByLabel(dialog()!, 'Position'), '10000');
    await click(buttonNamed(dialog()!, 'Save'));

    expect(api.calls(CREATE)).toHaveLength(0);
    const name = fieldByLabel(dialog()!, 'Name');
    expect(name.getAttribute('aria-invalid')).toBe('true');
    expect(dialog()!.textContent).toContain('Enter a name.');
    expect(dialog()!.textContent).toContain(
      'The position is a whole number from 0 to 9999.',
    );
  });

  it('a PATCH sends the loaded version and only the changed fields', async () => {
    const patch = `PATCH ${levelUrl(SECUNDARIA)}`;
    const api = stub(MANAGER, {
      [patch]: () => jsonResponse(200, { ...SECUNDARIA, version: 5 }),
    });
    app = await renderApp('/catalog/education-levels');
    const edit = editButton('Secundaria');
    await openWith(edit);
    expect(fieldByLabel(dialog()!, 'Name').value).toBe('Secundaria');
    expect(fieldByLabel(dialog()!, 'Position').value).toBe('20');

    typeInto(fieldByLabel(dialog()!, 'Position'), '25');
    await click(buttonNamed(dialog()!, 'Save'));

    expect(api.calls(patch).map((c) => c.body)).toEqual([
      { version: 4, sort_order: 25 },
    ]);
    expect(dialog()).toBeNull();
    expect(document.activeElement).toBe(edit);
  });

  it('an unchanged form sends nothing and closes', async () => {
    const patch = `PATCH ${levelUrl(SECUNDARIA)}`;
    const api = stub();
    app = await renderApp('/catalog/education-levels');
    await openWith(editButton('Secundaria'));

    await click(buttonNamed(dialog()!, 'Save'));

    expect(api.calls(patch)).toHaveLength(0);
    expect(dialog()).toBeNull();
  });

  it('name_taken lands on the name field', async () => {
    stub(MANAGER, { [CREATE]: () => problem(409, 'name_taken') });
    app = await renderApp('/catalog/education-levels');
    await openWith(buttonNamed(main(), 'Add level'));

    typeInto(fieldByLabel(dialog()!, 'Name'), 'PRIMARIA');
    await click(buttonNamed(dialog()!, 'Save'));

    const name = fieldByLabel(dialog()!, 'Name');
    expect(name.getAttribute('aria-invalid')).toBe('true');
    const message = document.getElementById(
      name.getAttribute('aria-describedby')!,
    );
    expect(message?.textContent).toBe(
      'That name is already in use (possibly by an inactive record).',
    );
    expect(alerts(dialog()!)).toEqual([]);
    expect(document.activeElement).toBe(name);
  });

  it('Deactivate on a level with active subjects shows the server message', async () => {
    const patch = `PATCH ${levelUrl(SECUNDARIA)}`;
    const api = stub(MANAGER, {
      [patch]: () => problem(409, 'education_level_has_active_subjects'),
    });
    app = await renderApp('/catalog/education-levels');
    await openWith(editButton('Secundaria'));

    await click(buttonNamed(dialog()!, 'Deactivate'));

    expect(api.calls(patch).map((c) => c.body)).toEqual([
      { version: 4, is_active: false },
    ]);
    expect(alerts(dialog()!).join('')).toContain(
      "Deactivate this level's active subjects first.",
    );
    expect(dialog()).not.toBeNull();
  });

  it('an inactive level offers Reactivate', async () => {
    const patch = `PATCH ${levelUrl(POSGRADO)}`;
    const api = stub(MANAGER, {
      [patch]: () => jsonResponse(200, { ...POSGRADO, is_active: true }),
    });
    app = await renderApp('/catalog/education-levels');
    await openWith(editButton('Posgrado'));

    await click(buttonNamed(dialog()!, 'Reactivate'));

    expect(api.calls(patch).map((c) => c.body)).toEqual([
      { version: 2, is_active: true },
    ]);
    expect(statusText()).toBe('Saved.');
  });

  it('version_conflict shows Reload, which refetches and resets the form', async () => {
    const patch = `PATCH ${levelUrl(SECUNDARIA)}`;
    const newer = { ...SECUNDARIA, name: 'Secundaria técnica', version: 5 };
    const api = stub(MANAGER, {
      [LIST]: [levelsPage(LEVELS), levelsPage([PRIMARIA, newer])],
      [patch]: [
        () => problem(409, 'version_conflict'),
        () => jsonResponse(200, { ...newer, version: 6 }),
      ],
    });
    app = await renderApp('/catalog/education-levels');
    await openWith(editButton('Secundaria'));
    typeInto(fieldByLabel(dialog()!, 'Name'), 'Secundaria general');
    await click(buttonNamed(dialog()!, 'Save'));
    expect(alerts(dialog()!).join('')).toContain(
      'Someone else changed this record.',
    );

    await click(buttonNamed(dialog()!, 'Reload'));

    expect(api.calls(LIST)).toHaveLength(2);
    expect(fieldByLabel(dialog()!, 'Name').value).toBe('Secundaria técnica');
    expect(alerts(dialog()!)).toEqual([]);
    typeInto(fieldByLabel(dialog()!, 'Name'), 'Secundaria general');
    await click(buttonNamed(dialog()!, 'Save'));
    expect(api.calls(patch).map((c) => c.body)).toEqual([
      { version: 4, name: 'Secundaria general' },
      { version: 5, name: 'Secundaria general' },
    ]);
  });

  it('changing the language keeps the typed values', async () => {
    stub();
    app = await renderApp('/catalog/education-levels');
    await openWith(buttonNamed(main(), 'Add level'));
    typeInto(fieldByLabel(dialog()!, 'Name'), 'Maestría');

    act(() => applyLanguage('es'));

    expect(fieldByLabel(dialog()!, 'Nombre').value).toBe('Maestría');
  });
});

describe('the level page (I1, I2)', () => {
  it('shows the level, its subjects and adds one', async () => {
    const create = `POST ${levelUrl(SECUNDARIA)}/subjects`;
    const api = stub(MANAGER, {
      [create]: () => jsonResponse(201, { ...ALGEBRA, id: 'new' }),
    });
    app = await renderApp(`/catalog/education-levels/${SECUNDARIA.id}`);

    expect(main().querySelector('h1')?.textContent).toBe('Secundaria');
    expect(document.title).toBe('Secundaria · Classora');
    expect(rowNames()).toEqual(['Álgebra', 'Historia']);
    const [request] = api.fetch.mock.calls
      .map(([r]) => new URL(r.url))
      .filter((url) => url.pathname === '/api/v1/subjects');
    expect(request?.searchParams.get('education_level_id')).toBe(SECUNDARIA.id);
    expect(request?.searchParams.get('status')).toBe('all');
    const add = buttonNamed(main(), 'Add subject');
    expect(add.disabled).toBe(false);
    await openWith(add);

    typeInto(fieldByLabel(dialog()!, 'Name'), 'Geometría');
    await click(buttonNamed(dialog()!, 'Save'));

    expect(api.calls(create).map((c) => c.body)).toEqual([
      { name: 'Geometría' },
    ]);
    // The subjects and the level (its count changed) are refetched.
    expect(api.calls(SUBJECTS)).toHaveLength(2);
    expect(api.calls(`GET ${levelUrl(SECUNDARIA)}`)).toHaveLength(2);
    expect(document.activeElement).toBe(add);
    expect(statusText()).toBe('Saved.');
  });

  it('a subject PATCH sends only the changed name and the version', async () => {
    const api = stub(MANAGER, {
      [subjectPatch(ALGEBRA)]: () => jsonResponse(200, ALGEBRA),
    });
    app = await renderApp(`/catalog/education-levels/${SECUNDARIA.id}`);
    await openWith(editButton('Álgebra'));

    typeInto(fieldByLabel(dialog()!, 'Name'), 'Álgebra I');
    await click(buttonNamed(dialog()!, 'Save'));

    expect(api.calls(subjectPatch(ALGEBRA)).map((c) => c.body)).toEqual([
      { version: 1, name: 'Álgebra I' },
    ]);
  });

  it('name_taken on a subject lands on the name field', async () => {
    const create = `POST ${levelUrl(SECUNDARIA)}/subjects`;
    stub(MANAGER, { [create]: () => problem(409, 'name_taken') });
    app = await renderApp(`/catalog/education-levels/${SECUNDARIA.id}`);
    await openWith(buttonNamed(main(), 'Add subject'));

    typeInto(fieldByLabel(dialog()!, 'Name'), 'ÁLGEBRA');
    await click(buttonNamed(dialog()!, 'Save'));

    expect(fieldByLabel(dialog()!, 'Name').getAttribute('aria-invalid')).toBe(
      'true',
    );
  });

  it('education_level_inactive appears in the alert region', async () => {
    const api = stub(MANAGER, {
      [SUBJECTS]: subjectsPage([POSGRADO_SUBJECT]),
      [subjectPatch(POSGRADO_SUBJECT)]: () =>
        problem(409, 'education_level_inactive'),
    });
    app = await renderApp(`/catalog/education-levels/${POSGRADO.id}`);

    expect(main().textContent).toContain(
      'This level is inactive. Reactivate it to add or reactivate subjects.',
    );
    expect(buttonNamed(main(), 'Add subject').disabled).toBe(true);
    await openWith(editButton('Métodos'));
    await click(buttonNamed(dialog()!, 'Reactivate'));

    expect(
      api.calls(subjectPatch(POSGRADO_SUBJECT)).map((c) => c.body),
    ).toEqual([{ version: 1, is_active: true }]);
    expect(alerts(dialog()!).join('')).toContain(
      'The education level is inactive. Reactivate it first.',
    );
  });

  it('an unknown level shows the not-found heading and the way back', async () => {
    stub(MANAGER, {
      [`GET /api/v1/education-levels/missing`]: () => problem(404, 'not_found'),
    });

    app = await renderApp('/catalog/education-levels/missing');

    expect(main().querySelector('h1')?.textContent).toBe(
      "This education level doesn't exist.",
    );
    const back = [...main().querySelectorAll('a')].find(
      (a) => a.textContent === 'Back to education levels',
    );
    expect(back?.getAttribute('href')).toBe('/catalog/education-levels');
  });
});
