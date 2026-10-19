import { act } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { applyLanguage } from '@/i18n';
import type { SessionUserBody } from '@/features/auth/session';
import {
  LAURA,
  LIST_ITEMS as GUARDIAN_ITEMS,
} from '@/features/guardians/fixtures';
import { guardianPage } from '@/features/guardians/fixtures';
import { PICKER_DEBOUNCE_MS } from '@/features/guardians/SearchPicker';
import { JOSE, JOSE_LIMITED } from '@/features/students/fixtures';
import {
  meAs,
  noContent,
  problem,
  stubApi,
  TEST_USER,
  type ApiStub,
  type Responder,
} from '@/test/api-stub';
import { click, fieldByLabel, typeInto } from '@/test/dom';
import {
  buttonNamed,
  jsonResponse,
  renderApp,
  settle,
  type RenderedApp,
} from '@/test/render-app';
import type { FamilyDetail } from './api';
import {
  familyPage,
  LIST_ITEMS,
  NUNEZ,
  NUNEZ_GUARDIAN_VIEW,
  NUNEZ_ID,
  OTRA,
  STUDENT_OPTIONS,
} from './fixtures';

// #172 H5 (and H1-H4, I1): the Families navigation item and pages behind
// guardian.view / family.manage, the list's query string, the name form's
// bodies, the detail's add dialogs (PUT, the conflict message) and remove
// confirmation (DELETE), controls only with family.manage, and the
// "Family" line of the student and guardian details.

const LIST = 'GET /api/v1/families';
const CREATE = 'POST /api/v1/families';
const detailRoute = (family: FamilyDetail) =>
  `GET /api/v1/families/${family.id}`;
const patchRoute = (family: FamilyDetail) =>
  `PATCH /api/v1/families/${family.id}`;
const memberRoute = (
  method: 'PUT' | 'DELETE',
  family: FamilyDetail,
  kind: 'students' | 'guardians',
  id: string,
) => `${method} /api/v1/families/${family.id}/${kind}/${id}`;

/** An Owner. */
const MANAGER: SessionUserBody = {
  ...TEST_USER,
  permissions: [
    'guardian.view',
    'family.manage',
    'user.view',
    'student.view',
    'student.manage',
  ],
};
/** Finance Staff: guardian.view, no family.manage. */
const FINANCE: SessionUserBody = {
  ...TEST_USER,
  roles: ['finance_staff'],
  permissions: ['guardian.view', 'student.view'],
};
/** A guardian user. */
const PARENT: SessionUserBody = {
  ...TEST_USER,
  roles: ['guardian'],
  permissions: ['guardian.view', 'student.view'],
};

function stub(
  user: SessionUserBody = MANAGER,
  routes: Record<string, Responder | Responder[]> = {},
): ApiStub {
  return stubApi({
    'GET /api/v1/auth/me': meAs(user),
    [LIST]: () => jsonResponse(200, familyPage(LIST_ITEMS)),
    [detailRoute(NUNEZ)]: () => jsonResponse(200, NUNEZ),
    [detailRoute(OTRA)]: () => jsonResponse(200, OTRA),
    'GET /api/v1/students': () =>
      jsonResponse(200, {
        items: STUDENT_OPTIONS,
        total: 1,
        limit: 10,
        offset: 0,
      }),
    'GET /api/v1/guardians': () =>
      jsonResponse(200, guardianPage([GUARDIAN_ITEMS[1]!])),
    [`GET /api/v1/students/${JOSE.id}`]: () => jsonResponse(200, JOSE),
    [`GET /api/v1/guardians/${LAURA.id}`]: () => jsonResponse(200, LAURA),
    ...routes,
  });
}

let app: RenderedApp | undefined;

const main = () => app!.container.querySelector('main')!;
const navItems = () =>
  [...app!.container.querySelectorAll('header nav a')].map(
    (a) => a.textContent,
  );
const field = (label: string, root: ParentNode = main()) =>
  fieldByLabel(root, label);
const statusText = (root: ParentNode = main()) =>
  [...root.querySelectorAll('[role="status"]')]
    .map((s) => s.textContent)
    .join('|');
const links = (text: string) =>
  [...main().querySelectorAll('a')].filter((a) => a.textContent === text);
const dialog = () => document.querySelector<HTMLElement>('[role="dialog"]');
const section = (title: string) =>
  [...main().querySelectorAll('section')].find(
    (s) => s.querySelector('h2')?.textContent === title,
  );

function searches(api: ApiStub, path: string): string[] {
  return api.fetch.mock.calls
    .map(([request]) => new URL(request.url))
    .filter((url) => url.pathname === path)
    .map((url) => url.search);
}

async function afterDebounce() {
  await act(async () => {
    await new Promise((resolve) =>
      setTimeout(resolve, PICKER_DEBOUNCE_MS + 30),
    );
  });
  await settle();
}

function radioLabelled(root: ParentNode, text: string): HTMLInputElement {
  const label = [...root.querySelectorAll('label')].find((l) =>
    l.textContent?.includes(text),
  );
  const radio = label?.querySelector('input[type="radio"]');
  if (!(radio instanceof HTMLInputElement)) {
    throw new Error(`No option "${text}"`);
  }
  return radio;
}

function selectByLabel(root: ParentNode, label: string): HTMLSelectElement {
  const found = [...root.querySelectorAll('label')].find(
    (l) => l.textContent === label,
  );
  const element = found ? document.getElementById(found.htmlFor) : null;
  if (!(element instanceof HTMLSelectElement)) {
    throw new Error(`No select labelled "${label}"`);
  }
  return element;
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

// --- H1: access and the list --------------------------------------------------------

describe('access and the list (H1)', () => {
  it('shows "Families" after "Guardians" and the list with text badges', async () => {
    stub();

    app = await renderApp('/families');

    const items = navItems();
    expect(items.indexOf('Families')).toBe(items.indexOf('Guardians') + 1);
    expect(main().querySelectorAll('h1')).toHaveLength(1);
    expect(document.title).toBe('Families · Classora');
    const rows = [...main().querySelectorAll('tbody tr')].map((row) =>
      [...row.querySelectorAll('td')].map((cell) => cell.textContent),
    );
    expect(rows).toEqual([
      ['Familia Ñúñez Pérez', '2', 'Active'],
      ['Familia Otra Prueba', '0', 'Active'],
      ['Familia Zamora', '0', 'Inactive'],
    ]);
    expect(links('Familia Ñúñez Pérez')[0]?.getAttribute('href')).toBe(
      `/families/${NUNEZ_ID}`,
    );
  });

  it('"Add family" appears only with family.manage', async () => {
    stub();
    app = await renderApp('/families');
    expect(links('Add family')[0]?.getAttribute('href')).toBe('/families/new');
    app.unmount();

    stub(FINANCE);
    app = await renderApp('/families');
    expect(links('Add family')).toHaveLength(0);
  });

  it('a guardian sees the item and only what the API returns', async () => {
    stub(PARENT, {
      [LIST]: () => jsonResponse(200, familyPage([LIST_ITEMS[0]!])),
    });

    app = await renderApp('/families');

    expect(navItems()).toContain('Families');
    expect(main().querySelectorAll('tbody tr')).toHaveLength(1);
    expect(links('Add family')).toHaveLength(0);
  });

  it('without guardian.view: no item and the forbidden page', async () => {
    const api = stub({ ...TEST_USER, roles: ['tutor'], permissions: [] });

    app = await renderApp('/families');

    expect(navItems()).not.toContain('Families');
    expect(main().querySelector('h1')?.textContent).toBe(
      "You don't have access to this page",
    );
    expect(api.calls(LIST)).toHaveLength(0);
  });

  it('without family.manage the new and edit pages are forbidden', async () => {
    const api = stub(FINANCE);

    app = await renderApp('/families/new');
    expect(main().querySelector('h1')?.textContent).toBe(
      "You don't have access to this page",
    );
    app.unmount();
    app = await renderApp(`/families/${NUNEZ_ID}/edit`);

    expect(main().querySelector('h1')?.textContent).toBe(
      "You don't have access to this page",
    );
    expect(api.calls(CREATE)).toHaveLength(0);
    expect(api.calls(detailRoute(NUNEZ))).toHaveLength(0);
  });

  it('sends each control in the query string', async () => {
    const api = stub();

    app = await renderApp('/families?q=garcia&status=all&page=2');
    act(() => {
      const select = selectByLabel(main(), 'Status');
      select.value = 'inactive';
      select.dispatchEvent(new Event('change', { bubbles: true }));
    });
    await settle();

    expect(searches(api, '/api/v1/families')).toEqual([
      '?q=garcia&status=all&limit=25&offset=25',
      '?q=garcia&status=inactive&limit=25&offset=0',
    ]);
  });

  it('shows the empty states', async () => {
    stub(MANAGER, { [LIST]: () => jsonResponse(200, familyPage([])) });
    app = await renderApp('/families');
    expect(main().textContent).toContain('No families match these filters.');
    app.unmount();

    stub(MANAGER, { [LIST]: () => jsonResponse(200, familyPage([])) });
    app = await renderApp('/families?q=nadie');
    expect(main().textContent).toContain('No results for this search.');
  });
});

// --- H2: the form -------------------------------------------------------------------

describe('the form (H2)', () => {
  it('creates with the trimmed name, then goes to the detail', async () => {
    const api = stub(MANAGER, { [CREATE]: () => jsonResponse(201, OTRA) });
    app = await renderApp('/families/new');

    typeInto(field('Family name'), '  Familia Otra Prueba ');
    await click(buttonNamed(main(), 'Save'));

    expect(api.calls(CREATE).map((c) => c.body)).toEqual([
      { name: 'Familia Otra Prueba' },
    ]);
    expect(app.location()).toBe(`/families/${OTRA.id}`);
    expect(statusText()).toContain('Changes saved.');
  });

  it('validates the name before sending', async () => {
    const api = stub();
    app = await renderApp('/families/new');

    typeInto(field('Family name'), '   ');
    await click(buttonNamed(main(), 'Save'));

    expect(api.calls(CREATE)).toHaveLength(0);
    expect(main().textContent).toContain('Enter the family name.');
  });

  it('edit sends the version and the changed name', async () => {
    const api = stub(MANAGER, {
      [patchRoute(NUNEZ)]: () =>
        jsonResponse(200, { ...NUNEZ, name: 'Familia Ñúñez', version: 3 }),
    });
    app = await renderApp(`/families/${NUNEZ_ID}/edit`);

    typeInto(field('Family name'), 'Familia Ñúñez');
    await click(buttonNamed(main(), 'Save'));

    expect(api.calls(patchRoute(NUNEZ)).map((c) => c.body)).toEqual([
      { version: 2, name: 'Familia Ñúñez' },
    ]);
    expect(app.location()).toBe(`/families/${NUNEZ_ID}`);
  });

  it('a version conflict offers "Reload", which refetches', async () => {
    const api = stub(MANAGER, {
      [patchRoute(NUNEZ)]: () => problem(409, 'version_conflict'),
    });
    app = await renderApp(`/families/${NUNEZ_ID}/edit`);
    const before = api.calls(detailRoute(NUNEZ)).length;

    typeInto(field('Family name'), 'Otro nombre');
    await click(buttonNamed(main(), 'Save'));
    await click(buttonNamed(main(), 'Reload'));

    expect(api.calls(detailRoute(NUNEZ)).length).toBe(before + 1);
  });
});

// --- H3: the detail -----------------------------------------------------------------

describe('the detail (H3)', () => {
  it('shows both sections with links, badges and the phone; controls for managers', async () => {
    stub();
    app = await renderApp(`/families/${NUNEZ_ID}`);

    expect(main().querySelector('h1')?.textContent).toBe('Familia Ñúñez Pérez');
    const students = section('Students')!;
    expect(
      students.querySelector(`a[href="/students/${JOSE.id}"]`)?.textContent,
    ).toBe('José Ñúñez Pérez');
    expect(students.textContent).toContain('Minor');
    expect(students.textContent).toContain('Adult');
    const guardians = section('Guardians')!;
    expect(
      guardians.querySelector(`a[href="/guardians/${LAURA.id}"]`),
    ).not.toBeNull();
    expect(
      guardians.querySelector('a[href="tel:+52 00 1234 5678"]'),
    ).not.toBeNull();
    expect(guardians.textContent).toContain('Has account');
    expect(links('Edit family')[0]?.getAttribute('href')).toBe(
      `/families/${NUNEZ_ID}/edit`,
    );
    expect(buttonNamed(main(), 'Deactivate')).toBeTruthy();
    expect(buttonNamed(students, 'Add student')).toBeTruthy();
    expect(buttonNamed(guardians, 'Add guardian')).toBeTruthy();
    expect(buttonNamed(students, 'Remove José Ñúñez Pérez')).toBeTruthy();
  });

  it('without family.manage there are no controls', async () => {
    stub(FINANCE);
    app = await renderApp(`/families/${NUNEZ_ID}`);

    expect(main().querySelectorAll('button')).toHaveLength(0);
    expect(links('Edit family')).toHaveLength(0);
  });

  it("a guardian's view lists only themselves and has no controls", async () => {
    stub(PARENT, {
      [detailRoute(NUNEZ)]: () => jsonResponse(200, NUNEZ_GUARDIAN_VIEW),
    });
    app = await renderApp(`/families/${NUNEZ_ID}`);

    const guardians = section('Guardians')!;
    expect(guardians.querySelectorAll('li')).toHaveLength(1);
    expect(guardians.textContent).toContain('Laura Ñúñez Prieto');
    expect(main().querySelectorAll('button')).toHaveLength(0);
  });

  it('shows the empty texts', async () => {
    stub(MANAGER);
    app = await renderApp(`/families/${OTRA.id}`);

    expect(section('Students')!.textContent).toContain('No students.');
    expect(section('Guardians')!.textContent).toContain('No guardians.');
  });

  it('a 404 says the family does not exist', async () => {
    stub(PARENT, { [detailRoute(OTRA)]: () => problem(404, 'not_found') });

    app = await renderApp(`/families/${OTRA.id}`);

    expect(main().querySelector('h1')?.textContent).toBe(
      "This family doesn't exist.",
    );
    expect(links('Back to families')).toHaveLength(1);
  });

  it('"Add student" searches students, PUTs, shows the conflict inside, then adds', async () => {
    const student = STUDENT_OPTIONS[0]!;
    const api = stub(MANAGER, {
      [memberRoute('PUT', OTRA, 'students', student.id)]: [
        () => problem(409, 'family_member_conflict'),
        noContent,
      ],
    });
    app = await renderApp(`/families/${OTRA.id}`);
    const before = api.calls(detailRoute(OTRA)).length;

    await click(buttonNamed(section('Students')!, 'Add student'));
    typeInto(field('Student', dialog()!), 'mateo');
    await afterDebounce();
    expect(searches(api, '/api/v1/students')).toEqual([
      '?q=mateo&status=all&limit=10',
    ]);
    await click(radioLabelled(dialog()!, 'Ríos Luna, Mateo'));
    await click(buttonNamed(dialog()!, 'Add'));

    expect(dialog()!.textContent).toContain(
      'This student already belongs to another family. Remove them from it first.',
    );
    await click(buttonNamed(dialog()!, 'Add'));

    expect(
      api.calls(memberRoute('PUT', OTRA, 'students', student.id)),
    ).toHaveLength(2);
    expect(dialog()).toBeNull();
    expect(statusText()).toContain('Student added.');
    expect(api.calls(detailRoute(OTRA)).length).toBe(before + 1);
  });

  it('"Add guardian" searches active guardians and PUTs', async () => {
    const guardian = GUARDIAN_ITEMS[1]!;
    const api = stub(MANAGER, {
      [memberRoute('PUT', OTRA, 'guardians', guardian.id)]: noContent,
    });
    app = await renderApp(`/families/${OTRA.id}`);

    await click(buttonNamed(section('Guardians')!, 'Add guardian'));
    typeInto(field('Guardian', dialog()!), 'ramon');
    await afterDebounce();
    expect(searches(api, '/api/v1/guardians')).toEqual([
      '?q=ramon&status=active&limit=10',
    ]);
    await click(radioLabelled(dialog()!, 'Pérez Solís, Ramón'));
    await click(buttonNamed(dialog()!, 'Add'));

    expect(
      api.calls(memberRoute('PUT', OTRA, 'guardians', guardian.id)),
    ).toHaveLength(1);
    expect(statusText()).toContain('Guardian added.');
  });

  it('the add dialog asks for a choice and shows other errors', async () => {
    const student = STUDENT_OPTIONS[0]!;
    const api = stub(MANAGER, {
      [memberRoute('PUT', OTRA, 'students', student.id)]: () =>
        problem(409, 'family_inactive'),
    });
    app = await renderApp(`/families/${OTRA.id}`);

    await click(buttonNamed(section('Students')!, 'Add student'));
    await click(buttonNamed(dialog()!, 'Add'));
    expect(dialog()!.textContent).toContain(
      'Choose a student from the results.',
    );
    expect(api.calls(memberRoute('PUT', OTRA, 'students', student.id))).toEqual(
      [],
    );

    typeInto(field('Student', dialog()!), 'mateo');
    await afterDebounce();
    await click(radioLabelled(dialog()!, 'Ríos Luna, Mateo'));
    await click(buttonNamed(dialog()!, 'Add'));

    expect(dialog()!.textContent).toContain(
      'Reactivate the family to add members.',
    );
  });

  it('remove confirms, sends DELETE and announces it', async () => {
    const api = stub(MANAGER, {
      [memberRoute('DELETE', NUNEZ, 'guardians', LAURA.id)]: noContent,
    });
    app = await renderApp(`/families/${NUNEZ_ID}`);

    await click(
      buttonNamed(section('Guardians')!, 'Remove Laura Ñúñez Prieto'),
    );
    expect(dialog()!.textContent).toContain(
      'Laura Ñúñez Prieto will no longer belong to this family.',
    );
    await click(buttonNamed(dialog()!, 'Remove'));

    expect(
      api.calls(memberRoute('DELETE', NUNEZ, 'guardians', LAURA.id)),
    ).toHaveLength(1);
    expect(statusText()).toContain('Guardian removed.');
  });

  it('deactivating confirms and sends {version, is_active}', async () => {
    const api = stub(MANAGER, {
      [patchRoute(NUNEZ)]: () =>
        jsonResponse(200, { ...NUNEZ, is_active: false, version: 3 }),
    });
    app = await renderApp(`/families/${NUNEZ_ID}`);

    await click(buttonNamed(main(), 'Deactivate'));
    expect(dialog()!.textContent).toContain(
      'The family and its members are kept, and it can be reactivated.',
    );
    await click(buttonNamed(dialog()!, 'Deactivate'));

    expect(api.calls(patchRoute(NUNEZ)).map((c) => c.body)).toEqual([
      { version: 2, is_active: false },
    ]);
    expect(buttonNamed(main(), 'Reactivate')).toBeTruthy();
    expect(main().textContent).toContain('Inactive');
  });
});

// --- H4: the Family line ------------------------------------------------------------

describe('the Family line (H4)', () => {
  const familyLine = () =>
    main().querySelector<HTMLElement>('[data-slot="family-line"]');

  it('links the family on the student and guardian details', async () => {
    stub();
    app = await renderApp(`/students/${JOSE.id}`);
    expect(familyLine()?.textContent).toBe('Family: Familia Ñúñez Pérez');
    expect(familyLine()?.querySelector('a')?.getAttribute('href')).toBe(
      `/families/${NUNEZ_ID}`,
    );
    app.unmount();

    stub();
    app = await renderApp(`/guardians/${LAURA.id}`);
    expect(familyLine()?.querySelector('a')?.getAttribute('href')).toBe(
      `/families/${NUNEZ_ID}`,
    );
  });

  it('is absent when family is null', async () => {
    stub(PARENT, {
      [`GET /api/v1/students/${JOSE.id}`]: () =>
        jsonResponse(200, JOSE_LIMITED),
      [`GET /api/v1/guardians/${LAURA.id}`]: () =>
        jsonResponse(200, { ...LAURA, family: null }),
    });
    app = await renderApp(`/students/${JOSE.id}`);
    expect(familyLine()).toBeNull();
    app.unmount();

    app = await renderApp(`/guardians/${LAURA.id}`);
    expect(familyLine()).toBeNull();
  });
});

// --- I1: translations ---------------------------------------------------------------

describe('translations (I1)', () => {
  it('shows Spanish labels and keeps names as stored', async () => {
    applyLanguage('es');
    stub();

    app = await renderApp(`/families/${NUNEZ_ID}`);

    expect(navItems()).toContain('Familias');
    expect(section('Estudiantes')).toBeTruthy();
    expect(section('Padres y tutores')).toBeTruthy();
    expect(main().textContent).toContain('Activa');
    expect(main().querySelector('h1')?.textContent).toBe('Familia Ñúñez Pérez');
  });
});
