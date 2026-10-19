import { act } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { applyLanguage } from '@/i18n';
import type { SessionUserBody } from '@/features/auth/session';
import { JOSE, JOSE_LIMITED } from '@/features/students/fixtures';
import type { StudentDetail } from '@/features/students/api';
import {
  meAs,
  noContent,
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
  mountApp,
  renderApp,
  settle,
  type RenderedApp,
} from '@/test/render-app';
import type { GuardianDetail } from './api';
import {
  ACCOUNT_OPTIONS,
  guardianPage,
  LAURA,
  LAURA_ID,
  LAURA_OWN_VIEW,
  LIST_ITEMS,
  RAMON,
} from './fixtures';
import { SEARCH_DEBOUNCE_MS } from './GuardiansPage';
import { PICKER_DEBOUNCE_MS } from './SearchPicker';

// #34 I5 (and I1-I4, J1, J2): the Guardians navigation item and pages behind
// guardian.view / family.manage, the list's query string, the form's request
// bodies and the account picker, account errors on the field, the student
// detail's Guardians section with the link dialog and the unlink
// confirmation.

const LIST = 'GET /api/v1/guardians';
const CREATE = 'POST /api/v1/guardians';
const USERS = 'GET /api/v1/users';
const detailRoute = (guardian: GuardianDetail) =>
  `GET /api/v1/guardians/${guardian.id}`;
const patchRoute = (guardian: GuardianDetail) =>
  `PATCH /api/v1/guardians/${guardian.id}`;
const studentRoute = (student: StudentDetail) =>
  `GET /api/v1/students/${student.id}`;
const linkRoute = (student: StudentDetail) =>
  `POST /api/v1/students/${student.id}/guardians`;
const unlinkRoute = (student: StudentDetail, guardianId: string) =>
  `DELETE /api/v1/students/${student.id}/guardians/${guardianId}`;

/** An Owner: every permission these pages use. */
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
/** An Academic Coordinator: guardian.view and student.manage, no family.manage. */
const COORDINATOR: SessionUserBody = {
  ...TEST_USER,
  roles: ['academic_coordinator'],
  permissions: ['guardian.view', 'student.view', 'student.manage'],
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
    [LIST]: () => jsonResponse(200, guardianPage(LIST_ITEMS)),
    [detailRoute(LAURA)]: () => jsonResponse(200, LAURA),
    [detailRoute(RAMON)]: () => jsonResponse(200, RAMON),
    [USERS]: () =>
      jsonResponse(200, {
        items: ACCOUNT_OPTIONS,
        total: 2,
        limit: 10,
        offset: 0,
      }),
    [studentRoute(JOSE)]: () => jsonResponse(200, JOSE),
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

function searches(api: ApiStub, path: string): string[] {
  return api.fetch.mock.calls
    .map(([request]) => new URL(request.url))
    .filter((url) => url.pathname === path)
    .map((url) => url.search);
}

/** Lets the picker's debounce pass (real timers), then settles. */
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

async function choose(radio: HTMLInputElement) {
  await click(radio);
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

function selectOption(select: HTMLSelectElement, value: string) {
  act(() => {
    select.value = value;
    select.dispatchEvent(new Event('change', { bubbles: true }));
  });
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

// --- I1: access and the list ------------------------------------------------------

describe('access and the list (I1)', () => {
  it('shows "Guardians" after "Students" and the list with text badges', async () => {
    stub();

    app = await renderApp('/guardians');

    const items = navItems();
    expect(items.indexOf('Guardians')).toBe(items.indexOf('Students') + 1);
    expect(main().querySelectorAll('h1')).toHaveLength(1);
    expect(document.title).toBe('Guardians · Classora');
    const rows = [...main().querySelectorAll('tbody tr')].map((row) =>
      [...row.querySelectorAll('td')].map((cell) => cell.textContent),
    );
    expect(rows).toEqual([
      ['Ñúñez Prieto, Laura', 'Has account', '2', 'Active'],
      ['Pérez Solís, Ramón', 'No account', '0', 'Active'],
      ['Zamora, Inés', 'No account', '0', 'Inactive'],
    ]);
    expect(links('Ñúñez Prieto, Laura')[0]?.getAttribute('href')).toBe(
      `/guardians/${LAURA_ID}`,
    );
  });

  it('"Add guardian" appears only with family.manage', async () => {
    stub();
    app = await renderApp('/guardians');
    expect(links('Add guardian')[0]?.getAttribute('href')).toBe(
      '/guardians/new',
    );
    app.unmount();

    stub(COORDINATOR);
    app = await renderApp('/guardians');
    expect(links('Add guardian')).toHaveLength(0);
  });

  it('a guardian sees the item and only what the API returns', async () => {
    stub(PARENT, {
      [LIST]: () => jsonResponse(200, guardianPage([LIST_ITEMS[0]!])),
    });

    app = await renderApp('/guardians');

    expect(navItems()).toContain('Guardians');
    expect(main().querySelectorAll('tbody tr')).toHaveLength(1);
    expect(links('Add guardian')).toHaveLength(0);
  });

  it('without guardian.view: no item and the forbidden page', async () => {
    const api = stub({ ...TEST_USER, roles: ['tutor'], permissions: [] });

    app = await renderApp('/guardians');

    expect(navItems()).not.toContain('Guardians');
    expect(main().querySelector('h1')?.textContent).toBe(
      "You don't have access to this page",
    );
    expect(api.calls(LIST)).toHaveLength(0);
  });

  it('without family.manage the new and edit pages are forbidden', async () => {
    const api = stub(COORDINATOR);

    app = await renderApp('/guardians/new');
    expect(main().querySelector('h1')?.textContent).toBe(
      "You don't have access to this page",
    );
    app.unmount();
    app = await renderApp(`/guardians/${LAURA_ID}/edit`);

    expect(main().querySelector('h1')?.textContent).toBe(
      "You don't have access to this page",
    );
    expect(api.calls(CREATE)).toHaveLength(0);
    expect(api.calls(detailRoute(LAURA))).toHaveLength(0);
  });

  it('sends each control in the query string', async () => {
    const api = stub();

    app = await renderApp('/guardians?status=all&page=2');
    selectOption(selectByLabel(main(), 'Status'), 'inactive');
    await settle();

    expect(searches(api, '/api/v1/guardians')).toEqual([
      '?status=all&limit=25&offset=25',
      '?status=inactive&limit=25&offset=0',
    ]);
  });

  it('debounces the search by 300 ms and goes back to page 1', async () => {
    vi.useFakeTimers();
    const api = stub();
    app = mountApp('/guardians?page=3');
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    const before = searches(api, '/api/v1/guardians').length;

    typeInto(field('Search by name'), 'ñúñez ');
    await act(async () => {
      await vi.advanceTimersByTimeAsync(SEARCH_DEBOUNCE_MS - 1);
    });
    expect(searches(api, '/api/v1/guardians')).toHaveLength(before);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });

    expect(searches(api, '/api/v1/guardians').slice(before)).toEqual([
      `?q=${encodeURIComponent('ñúñez')}&limit=25&offset=0`,
    ]);
    expect(app.location()).toBe(`/guardians?q=${encodeURIComponent('ñúñez')}`);
  });

  it('shows the empty states', async () => {
    stub(MANAGER, { [LIST]: () => jsonResponse(200, guardianPage([])) });

    app = await renderApp('/guardians');

    expect(main().textContent).toContain('No guardians match these filters.');
  });
});

// --- I2: the form -------------------------------------------------------------------

describe('the form (I2)', () => {
  it('creates with the names only, then goes to the detail', async () => {
    const api = stub(MANAGER, {
      [CREATE]: () => jsonResponse(201, RAMON),
    });
    app = await renderApp('/guardians/new');

    typeInto(field('Given names'), ' Ramón ');
    typeInto(field('Family names'), 'Pérez Solís');
    await click(buttonNamed(main(), 'Save'));

    expect(api.calls(CREATE).map((c) => c.body)).toEqual([
      { given_names: 'Ramón', family_names: 'Pérez Solís' },
    ]);
    expect(app.location()).toBe(`/guardians/${RAMON.id}`);
    expect(statusText()).toContain('Changes saved.');
  });

  it('the account picker searches guardians and disables archived accounts', async () => {
    const api = stub(MANAGER, {
      [CREATE]: () => jsonResponse(201, LAURA),
    });
    app = await renderApp('/guardians/new');

    typeInto(field('Search accounts by username or name'), 'parent');
    await afterDebounce();

    expect(searches(api, '/api/v1/users')).toEqual([
      '?q=parent&role=guardian&limit=10',
    ]);
    expect(statusText()).toContain('Results: 2');
    const archived = radioLabelled(main(), 'old.parent');
    expect(archived.disabled).toBe(true);
    expect(archived.closest('label')?.textContent).toContain('(archived)');
    const available = radioLabelled(main(), 'e2e.guardians.parent');
    expect(available.disabled).toBe(false);
    expect(main().textContent).toContain(
      'This account will see the records of the linked children.',
    );

    await choose(available);
    expect(available.checked).toBe(true);
    expect(
      main().querySelector('[data-slot="guardian-account-chip"]')?.textContent,
    ).toContain('e2e.guardians.parent');
    typeInto(field('Given names'), 'Laura');
    typeInto(field('Family names'), 'Ñúñez');
    await click(buttonNamed(main(), 'Save'));

    expect(api.calls(CREATE)[0]?.body).toEqual({
      given_names: 'Laura',
      family_names: 'Ñúñez',
      user_id: ACCOUNT_OPTIONS[0]!.id,
    });
  });

  it('edit sends only the changed fields with the version', async () => {
    const api = stub(MANAGER, {
      [patchRoute(LAURA)]: () =>
        jsonResponse(200, { ...LAURA, phone: null, version: 3 }),
    });
    app = await renderApp(`/guardians/${LAURA_ID}/edit`);

    typeInto(field('Phone (optional)'), '');
    await click(buttonNamed(main(), 'Save'));

    expect(api.calls(patchRoute(LAURA)).map((c) => c.body)).toEqual([
      { version: 2, phone: null },
    ]);
  });

  it('"Remove account" sends user_id: null', async () => {
    const api = stub(MANAGER, {
      [patchRoute(LAURA)]: () =>
        jsonResponse(200, { ...LAURA, account: null, has_account: false }),
    });
    app = await renderApp(`/guardians/${LAURA_ID}/edit`);
    expect(
      main().querySelector('[data-slot="guardian-account-chip"]')?.textContent,
    ).toContain('laura.nunez');

    await click(buttonNamed(main(), 'Remove account'));
    await click(buttonNamed(main(), 'Save'));

    expect(api.calls(patchRoute(LAURA)).map((c) => c.body)).toEqual([
      { version: 2, user_id: null },
    ]);
  });

  it.each([
    [
      'guardian_account_in_use',
      'That account is already linked to another guardian.',
    ],
    [
      'guardian_account_unavailable',
      'Choose an active or pending account with the guardian role.',
    ],
  ])('%s lands on the Account field', async (code, text) => {
    stub(MANAGER, {
      [patchRoute(LAURA)]: () =>
        code === 'guardian_account_in_use'
          ? problem(409, code)
          : new Response(
              JSON.stringify({
                type: 'about:blank',
                title: 'Problem',
                status: 422,
                code,
                request_id: 'req-1',
                errors: [
                  { path: ['body', 'user_id'], code, message: 'server text' },
                ],
              }),
              {
                status: 422,
                headers: { 'Content-Type': 'application/problem+json' },
              },
            ),
    });
    app = await renderApp(`/guardians/${LAURA_ID}/edit`);

    typeInto(field('Given names'), 'Lucía');
    await click(buttonNamed(main(), 'Save'));

    const section = [...main().querySelectorAll('section')].find(
      (s) => s.querySelector('h2')?.textContent === 'Login account (optional)',
    )!;
    expect(section.textContent).toContain(text);
    expect(alerts(main())).toEqual([]);
    expect(app.location()).toBe(`/guardians/${LAURA_ID}/edit`);
  });

  it('validates names before sending', async () => {
    const api = stub();
    app = await renderApp('/guardians/new');

    await click(buttonNamed(main(), 'Save'));

    expect(api.calls(CREATE)).toHaveLength(0);
    expect(main().textContent).toContain('Enter this information.');
  });
});

// --- I3: the detail ------------------------------------------------------------------

describe('the detail (I3)', () => {
  it('shows contact, account and linked students; Edit and Deactivate for managers', async () => {
    stub();

    app = await renderApp(`/guardians/${LAURA_ID}`);

    expect(main().querySelector('h1')?.textContent).toBe('Laura Ñúñez Prieto');
    const text = main().textContent ?? '';
    expect(text).toContain('laura.nunez');
    expect(text).toContain('Mother');
    expect(text).toContain('Legal guardian');
    expect(text).toContain('Minor');
    expect(text).toContain('Adult');
    expect(links('José Ñúñez Pérez')[0]?.getAttribute('href')).toBe(
      '/students/00000000-0000-4000-8000-0000000e0001',
    );
    expect(links('Edit guardian')).toHaveLength(1);
    expect(buttonNamed(main(), 'Deactivate')).toBeTruthy();
  });

  it("a guardian's own view has no account details and no actions", async () => {
    stub(PARENT, {
      [detailRoute(LAURA)]: () => jsonResponse(200, LAURA_OWN_VIEW),
    });

    app = await renderApp(`/guardians/${LAURA_ID}`);

    expect(main().textContent).not.toContain('laura.nunez');
    expect(main().textContent).toContain('Has account');
    expect(links('Edit guardian')).toHaveLength(0);
    expect(main().querySelectorAll('button')).toHaveLength(0);
  });

  it('deactivating confirms and sends {version, is_active}', async () => {
    const api = stub(MANAGER, {
      [patchRoute(LAURA)]: () =>
        jsonResponse(200, { ...LAURA, is_active: false, version: 3 }),
    });
    app = await renderApp(`/guardians/${LAURA_ID}`);

    await click(buttonNamed(main(), 'Deactivate'));
    expect(dialog()?.textContent).toContain(
      'The profile is kept, but its account stops seeing the linked students.',
    );
    await click(buttonNamed(dialog()!, 'Deactivate'));

    expect(api.calls(patchRoute(LAURA)).map((c) => c.body)).toEqual([
      { version: 2, is_active: false },
    ]);
    expect(dialog()).toBeNull();
    expect(statusText()).toContain('Changes saved.');
  });

  it('a 404 says the guardian does not exist', async () => {
    stub(MANAGER, {
      [detailRoute(LAURA)]: () => problem(404, 'not_found'),
    });

    app = await renderApp(`/guardians/${LAURA_ID}`);

    expect(main().querySelector('h1')?.textContent).toBe(
      "This guardian doesn't exist.",
    );
  });
});

// --- I4: the student's Guardians section ------------------------------------------

describe("the student's Guardians section (I4)", () => {
  const section = () =>
    [...main().querySelectorAll('section')].find(
      (s) => s.querySelector('h2')?.textContent === 'Guardians',
    );

  it('is absent when guardians is null', async () => {
    stub(PARENT, {
      [studentRoute(JOSE)]: () => jsonResponse(200, JOSE_LIMITED),
    });

    app = await renderApp(`/students/${JOSE.id}`);

    expect(section()).toBeUndefined();
  });

  it('lists the guardians; Link and Unlink only with family.manage', async () => {
    stub(COORDINATOR);
    app = await renderApp(`/students/${JOSE.id}`);

    const text = section()!.textContent ?? '';
    expect(text).toContain('Laura Ñúñez Prieto');
    expect(text).toContain('Mother');
    expect(text).toContain('Grandparent');
    expect(text).toContain('Inactive');
    expect(
      section()!.querySelector('a[href="tel:+52 00 1234 5678"]'),
    ).not.toBeNull();
    expect(section()!.querySelectorAll('button')).toHaveLength(0);
    app.unmount();

    stub();
    app = await renderApp(`/students/${JOSE.id}`);
    expect(buttonNamed(section()!, 'Link guardian')).toBeTruthy();
    expect(buttonNamed(section()!, 'Unlink Laura Ñúñez Prieto')).toBeTruthy();
  });

  it('shows the empty text', async () => {
    stub(MANAGER, {
      [studentRoute(JOSE)]: () => jsonResponse(200, { ...JOSE, guardians: [] }),
    });

    app = await renderApp(`/students/${JOSE.id}`);

    expect(section()!.textContent).toContain('No guardians linked yet.');
  });

  it('the link dialog sends the body, shows a 409 inside, then links', async () => {
    const api = stub(MANAGER, {
      [LIST]: () => jsonResponse(200, guardianPage([LIST_ITEMS[1]!])),
      [linkRoute(JOSE)]: [
        () => problem(409, 'guardian_already_linked'),
        () =>
          jsonResponse(201, {
            guardian_id: RAMON.id,
            given_names: RAMON.given_names,
            family_names: RAMON.family_names,
            relationship: 'father',
            phone: null,
            is_active: true,
            has_account: false,
          }),
      ],
    });
    app = await renderApp(`/students/${JOSE.id}`);
    const before = api.calls(studentRoute(JOSE)).length;

    await click(buttonNamed(section()!, 'Link guardian'));
    typeInto(field('Guardian', dialog()!), 'ramon');
    await afterDebounce();
    expect(searches(api, '/api/v1/guardians')).toEqual([
      '?q=ramon&status=active&limit=10',
    ]);
    await choose(radioLabelled(dialog()!, 'Pérez Solís, Ramón'));
    selectOption(selectByLabel(dialog()!, 'Relationship'), 'father');
    await click(buttonNamed(dialog()!, 'Link'));

    expect(dialog()?.textContent).toContain(
      'That guardian is already linked to this student.',
    );
    await click(buttonNamed(dialog()!, 'Link'));

    expect(api.calls(linkRoute(JOSE)).map((c) => c.body)).toEqual([
      { guardian_id: RAMON.id, relationship: 'father' },
      { guardian_id: RAMON.id, relationship: 'father' },
    ]);
    expect(dialog()).toBeNull();
    expect(api.calls(studentRoute(JOSE)).length).toBe(before + 1);
    expect(statusText(section())).toBe('Guardian linked.');
  });

  it('the link dialog asks for a guardian and a relationship', async () => {
    const api = stub();
    app = await renderApp(`/students/${JOSE.id}`);

    await click(buttonNamed(section()!, 'Link guardian'));
    await click(buttonNamed(dialog()!, 'Link'));

    expect(dialog()?.textContent).toContain(
      'Choose a guardian from the results.',
    );
    expect(api.calls(linkRoute(JOSE))).toHaveLength(0);
  });

  it('offers the eight translated relationships', async () => {
    stub();
    app = await renderApp(`/students/${JOSE.id}`);

    await click(buttonNamed(section()!, 'Link guardian'));
    const options = [
      ...selectByLabel(dialog()!, 'Relationship').querySelectorAll('option'),
    ].map((o) => o.textContent);

    expect(options.slice(1)).toEqual([
      'Mother',
      'Father',
      'Grandparent',
      'Stepparent',
      'Sibling',
      'Other relative',
      'Legal guardian',
      'Other',
    ]);
  });

  it('unlink confirms, sends DELETE and announces it', async () => {
    const guardianId = JOSE.guardians![0]!.guardian_id;
    const api = stub(MANAGER, {
      [unlinkRoute(JOSE, guardianId)]: noContent,
    });
    app = await renderApp(`/students/${JOSE.id}`);

    await click(buttonNamed(section()!, 'Unlink Laura Ñúñez Prieto'));
    expect(dialog()?.textContent).toContain(
      "Laura Ñúñez Prieto will no longer see this student's records.",
    );
    expect(api.calls(unlinkRoute(JOSE, guardianId))).toHaveLength(0);
    await click(buttonNamed(dialog()!, 'Unlink'));

    expect(api.calls(unlinkRoute(JOSE, guardianId))).toHaveLength(1);
    expect(dialog()).toBeNull();
    expect(statusText(section())).toBe('Guardian unlinked.');
  });
});

// --- J1: language ------------------------------------------------------------------

describe('translations (J1)', () => {
  it('shows Spanish labels and keeps names as stored', async () => {
    applyLanguage('es');
    stub();

    app = await renderApp(`/guardians/${LAURA_ID}`);

    expect(main().querySelector('h1')?.textContent).toBe('Laura Ñúñez Prieto');
    expect(main().textContent).toContain('Madre');
    expect(main().textContent).toContain('Tutor(a) legal');
    expect(navItems()).toContain('Padres y tutores');
  });

  it('changing the language keeps typed values (J2)', async () => {
    stub();
    app = await renderApp('/guardians/new');
    typeInto(field('Given names'), 'Laura');

    act(() => applyLanguage('es'));
    await settle();

    expect(field('Nombres').value).toBe('Laura');
  });
});
