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
  mountApp,
  renderApp,
  settle,
  type RenderedApp,
} from '@/test/render-app';
import type { StudentDetail, StudentListItem } from './api';
import {
  ACTIVE_LEVELS,
  ANA_INACTIVE_LEVEL,
  FIVE_CONTACTS,
  JOSE,
  JOSE_ID,
  JOSE_LIMITED,
  LIST_ITEMS,
  POSGRADO_REF,
  PRIMARIA_REF,
  SECUNDARIA_REF,
  studentPage,
} from './fixtures';
import { SEARCH_DEBOUNCE_MS } from './StudentsPage';

// #33 I4 and J4 (and I1-I3, J1-J3, K3): the Students navigation item and
// pages behind student.view / student.manage, the list's query string and
// debounce, the form's request bodies, contact rows, server errors and the
// version_conflict reload, and the limited detail.

const LIST = 'GET /api/v1/students';
const CREATE = 'POST /api/v1/students';
const LEVELS = 'GET /api/v1/education-levels';
const detailRoute = (student: StudentDetail) =>
  `GET /api/v1/students/${student.id}`;
const patchRoute = (student: StudentDetail) =>
  `PATCH /api/v1/students/${student.id}`;

/** An Owner-like manager (student.view + student.manage). */
const MANAGER: SessionUserBody = {
  ...TEST_USER,
  permissions: ['student.view', 'student.manage', 'user.view'],
};
/** Finance Staff: student.view only. */
const VIEWER: SessionUserBody = {
  ...TEST_USER,
  roles: ['finance_staff'],
  permissions: ['student.view'],
};

function listOf(items: StudentListItem[], total = items.length): Responder {
  return () => jsonResponse(200, studentPage(items, total));
}

function stub(
  user: SessionUserBody = MANAGER,
  routes: Record<string, Responder | Responder[]> = {},
): ApiStub {
  return stubApi({
    'GET /api/v1/auth/me': meAs(user),
    [LIST]: listOf(LIST_ITEMS),
    [LEVELS]: () => jsonResponse(200, ACTIVE_LEVELS),
    [detailRoute(JOSE)]: () => jsonResponse(200, JOSE),
    [detailRoute(ANA_INACTIVE_LEVEL)]: () =>
      jsonResponse(200, ANA_INACTIVE_LEVEL),
    ...routes,
  });
}

let app: RenderedApp | undefined;

const main = () => app!.container.querySelector('main')!;
const navItems = () =>
  [...app!.container.querySelectorAll('header nav a')].map(
    (a) => a.textContent,
  );
const field = (label: string) => fieldByLabel(main(), label);
const statusText = () =>
  [...main().querySelectorAll('[role="status"]')]
    .map((s) => s.textContent)
    .join('');

function listSearches(api: ApiStub): string[] {
  return api.fetch.mock.calls
    .map(([request]) => new URL(request.url))
    .filter((url) => url.pathname === '/api/v1/students')
    .map((url) => url.search);
}

function selectByLabel(label: string): HTMLSelectElement {
  const found = [...main().querySelectorAll('label')].find(
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

async function submit() {
  await click(buttonNamed(main(), 'Save'));
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

// --- I1, I4: access -----------------------------------------------------------------

describe('access (I1, I4, J1)', () => {
  it('shows the Students item last and the list page to student.view holders', async () => {
    stub();

    app = await renderApp('/students');

    expect(navItems().at(-1)).toBe('Students');
    expect(main().querySelectorAll('h1')).toHaveLength(1);
    expect(main().querySelector('h1')?.textContent).toBe('Students');
    expect(document.title).toBe('Students · Classora');
  });

  it('"Add student" appears only with student.manage', async () => {
    stub();
    app = await renderApp('/students');
    const add = [...main().querySelectorAll('a')].find(
      (a) => a.textContent === 'Add student',
    );
    expect(add?.getAttribute('href')).toBe('/students/new');
    app.unmount();

    stub(VIEWER);
    app = await renderApp('/students');

    expect(main().textContent).not.toContain('Add student');
  });

  it('without student.view: no navigation item and the forbidden page', async () => {
    const api = stub({ ...TEST_USER, permissions: [] });

    app = await renderApp('/students');

    expect(navItems()).not.toContain('Students');
    expect(main().querySelector('h1')?.textContent).toBe(
      "You don't have access to this page",
    );
    expect(api.calls(LIST)).toHaveLength(0);
  });

  it('without student.manage the new and edit pages are forbidden', async () => {
    const api = stub(VIEWER);

    app = await renderApp('/students/new');
    expect(main().querySelector('h1')?.textContent).toBe(
      "You don't have access to this page",
    );
    app.unmount();
    app = await renderApp(`/students/${JOSE_ID}/edit`);

    expect(main().querySelector('h1')?.textContent).toBe(
      "You don't have access to this page",
    );
    expect(api.calls(CREATE)).toHaveLength(0);
    expect(api.calls(detailRoute(JOSE))).toHaveLength(0);
  });
});

// --- I2, I3, I4: the list ----------------------------------------------------------

describe('the list (I2, I3, I4)', () => {
  it('shows name, level, grade, minor/adult and status as text', async () => {
    stub();

    app = await renderApp('/students');

    const rows = [...main().querySelectorAll('tbody tr')].map((row) =>
      [...row.querySelectorAll('td')].map((cell) => cell.textContent),
    );
    expect(rows).toEqual([
      [
        'García, Ana',
        'Posgrado (inactive)',
        '2.º de secundaria',
        'Adult',
        'Active',
      ],
      [
        'Ñúñez Pérez, José',
        'Secundaria',
        '2.º de secundaria',
        'Minor',
        'Active',
      ],
      ['Zamora, Mateo', '', '', 'Adult', 'Inactive'],
    ]);
    expect(main().querySelector('tbody a')?.getAttribute('href')).toBe(
      `/students/${ANA_INACTIVE_LEVEL.id}`,
    );
    expect(main().textContent).toContain('1–3 of 3');
  });

  it('sends each control in the query string and keeps it in the URL', async () => {
    const api = stub();
    app = await renderApp('/students?page=2');
    expect(listSearches(api)).toEqual(['?limit=25&offset=25']);

    const levelOptions = [...selectByLabel('Education level').options].map(
      (o) => o.textContent,
    );
    expect(levelOptions).toEqual(['All levels', 'Primaria', 'Secundaria']);
    selectOption(selectByLabel('Education level'), SECUNDARIA_REF.id);
    await settle();
    selectOption(selectByLabel('Status'), 'inactive');
    await settle();
    selectOption(selectByLabel('Status'), 'all');
    await settle();

    expect(listSearches(api).slice(1)).toEqual([
      `?education_level_id=${SECUNDARIA_REF.id}&limit=25&offset=0`,
      `?education_level_id=${SECUNDARIA_REF.id}&status=inactive&limit=25&offset=0`,
      `?education_level_id=${SECUNDARIA_REF.id}&status=all&limit=25&offset=0`,
    ]);
    expect(app.location()).toBe(
      `/students?level=${SECUNDARIA_REF.id}&status=all`,
    );
    // "Active" is the default: it leaves the URL.
    selectOption(selectByLabel('Status'), 'active');
    await settle();
    expect(app.location()).toBe(`/students?level=${SECUNDARIA_REF.id}`);
  });

  it('pages with Next', async () => {
    const api = stub(MANAGER, { [LIST]: listOf(LIST_ITEMS, 60) });
    app = await renderApp('/students?q=jose');

    await click(buttonNamed(main(), 'Next'));

    expect(app.location()).toBe('/students?q=jose&page=2');
    expect(listSearches(api).at(-1)).toBe('?q=jose&limit=25&offset=25');
  });

  it('debounces the search by 300 ms and goes back to page 1', async () => {
    vi.useFakeTimers();
    const api = stub();
    app = mountApp('/students?page=3');
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    const before = listSearches(api).length;

    typeInto(field('Search by name'), 'jo');
    await act(async () => {
      await vi.advanceTimersByTimeAsync(SEARCH_DEBOUNCE_MS - 50);
    });
    typeInto(field('Search by name'), 'josé ñúñez ');
    await act(async () => {
      await vi.advanceTimersByTimeAsync(SEARCH_DEBOUNCE_MS - 1);
    });
    expect(listSearches(api)).toHaveLength(before);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });

    expect(listSearches(api).slice(before)).toEqual([
      `?q=${encodeURIComponent('josé ñúñez')}&limit=25&offset=0`,
    ]);
    expect(app.location()).toBe(
      `/students?q=${encodeURIComponent('josé ñúñez').replaceAll('%20', '+')}`,
    );
  });

  it('says "no results" for a search and "no students" for filters', async () => {
    stub(MANAGER, { [LIST]: listOf([]) });
    app = await renderApp('/students?q=xyz');
    expect(main().textContent).toContain('No results for this search.');
    app.unmount();

    stub(MANAGER, { [LIST]: listOf([]) });
    app = await renderApp('/students?status=inactive');
    expect(main().textContent).toContain('No students match these filters.');
  });

  it('a failed list shows the error with Retry', async () => {
    const api = stub(MANAGER, {
      [LIST]: [() => problem(400, 'bad_request'), listOf(LIST_ITEMS)],
    });
    app = await renderApp('/students');

    expect(alerts(main()).join('')).toContain(
      "We couldn't process the request",
    );
    await click(buttonNamed(main(), 'Try again'));

    expect(api.calls(LIST)).toHaveLength(2);
    expect(main().querySelectorAll('tbody tr')).toHaveLength(3);
  });
});

// --- J1, J2, J4: the form ----------------------------------------------------------

function fillContact(
  n: number,
  name: string,
  relationship: string,
  phone: string,
) {
  typeInto(field(`Contact ${n} name`), name);
  typeInto(field(`Contact ${n} relationship`), relationship);
  typeInto(field(`Contact ${n} phone`), phone);
}

describe('creating (J1, J2, J4)', () => {
  it('sends the create body and goes to the detail', async () => {
    const created: StudentDetail = { ...JOSE, version: 1 };
    const api = stub(MANAGER, {
      [CREATE]: () => jsonResponse(201, created),
    });
    app = await renderApp('/students/new');

    expect(main().querySelector('h1')?.textContent).toBe('Add student');
    expect(field('Birthdate').type).toBe('date');
    expect(field('Birthdate').max).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    expect(field('Email (optional)').type).toBe('email');
    expect(field('Email (optional)').autocomplete).toBe('off');
    expect(field('Phone (optional)').type).toBe('tel');
    typeInto(field('Given names'), ' José ');
    typeInto(field('Family names'), 'Ñúñez Pérez');
    typeInto(field('Birthdate'), '2012-05-04');
    typeInto(field('Grade (optional)'), '2.º');
    selectOption(
      selectByLabel('Education level (optional)'),
      SECUNDARIA_REF.id,
    );
    fillContact(1, 'María Pérez', 'Madre', '+52 55 1234 5678');
    await click(buttonNamed(main(), 'Add contact'));
    fillContact(2, 'Luis Ñúñez', 'Padre', '55 8765 4321');
    await submit();

    expect(api.calls(CREATE).map((call) => call.body)).toEqual([
      {
        given_names: 'José',
        family_names: 'Ñúñez Pérez',
        birthdate: '2012-05-04',
        school_grade: '2.º',
        education_level_id: SECUNDARIA_REF.id,
        emergency_contacts: [
          {
            name: 'María Pérez',
            relationship: 'Madre',
            phone: '+52 55 1234 5678',
          },
          { name: 'Luis Ñúñez', relationship: 'Padre', phone: '55 8765 4321' },
        ],
      },
    ]);
    expect(app.location()).toBe(`/students/${JOSE_ID}`);
    expect(statusText()).toBe('Changes saved.');
    expect(main().querySelector('h1')?.textContent).toBe('José Ñúñez Pérez');
  });

  it('client checks block the request and mark the fields', async () => {
    const api = stub();
    app = await renderApp('/students/new');

    typeInto(field('Given names'), '   ');
    typeInto(field('Birthdate'), '2999-01-01');
    typeInto(field('Phone (optional)'), 'phone-ext');
    await submit();

    expect(api.calls(CREATE)).toHaveLength(0);
    expect(field('Given names').getAttribute('aria-invalid')).toBe('true');
    expect(field('Birthdate').getAttribute('aria-invalid')).toBe('true');
    expect(field('Phone (optional)').getAttribute('aria-invalid')).toBe('true');
    expect(field('Contact 1 phone').getAttribute('aria-invalid')).toBe('true');
    expect(main().textContent).toContain(
      'The date must be between January 1, 1900 and today.',
    );
  });

  it('the last contact cannot be removed and a sixth cannot be added', async () => {
    stub();
    app = await renderApp('/students/new');

    const remove = buttonNamed(main(), 'Remove contact 1');
    expect(remove.disabled).toBe(true);
    expect(
      document.getElementById(remove.getAttribute('aria-describedby')!)
        ?.textContent,
    ).toBe('At least one emergency contact is required.');
    for (let n = 2; n <= 5; n += 1) {
      await click(buttonNamed(main(), 'Add contact'));
      // Focus moves to the new row's first field.
      expect(document.activeElement).toBe(field(`Contact ${n} name`));
    }
    const add = buttonNamed(main(), 'Add contact');
    expect(add.disabled).toBe(true);
    expect(main().textContent).toContain('You can add up to 5 contacts.');
    expect(buttonNamed(main(), 'Remove contact 1').disabled).toBe(false);
  });
});

describe('editing (J1, J2, J4)', () => {
  it('sends only the changed fields and the loaded version', async () => {
    const saved = { ...JOSE, given_names: 'Joseph', version: 4 };
    const api = stub(MANAGER, {
      [patchRoute(JOSE)]: () => jsonResponse(200, saved),
      [detailRoute(JOSE)]: [
        () => jsonResponse(200, JOSE),
        () => jsonResponse(200, saved),
      ],
    });
    app = await renderApp(`/students/${JOSE_ID}/edit`);

    expect(field('Given names').value).toBe('José');
    typeInto(field('Given names'), 'Joseph');
    typeInto(field('Email (optional)'), '');
    await submit();

    expect(api.calls(patchRoute(JOSE)).map((c) => c.body)).toEqual([
      { version: 3, given_names: 'Joseph', email: null },
    ]);
    expect(app.location()).toBe(`/students/${JOSE_ID}`);
    expect(statusText()).toBe('Changes saved.');
    expect(main().querySelector('h1')?.textContent).toBe('Joseph Ñúñez Pérez');
  });

  it('sends the whole contact list with ids when a contact changes', async () => {
    const api = stub(MANAGER, {
      [patchRoute(JOSE)]: () => jsonResponse(200, { ...JOSE, version: 4 }),
    });
    app = await renderApp(`/students/${JOSE_ID}/edit`);

    typeInto(field('Contact 2 phone'), '55 0000 1111');
    await submit();

    const [contacts] = JOSE.personal!.emergency_contacts;
    expect(api.calls(patchRoute(JOSE)).map((c) => c.body)).toEqual([
      {
        version: 3,
        emergency_contacts: [
          {
            id: contacts!.id,
            name: 'María Pérez',
            relationship: 'Madre',
            phone: '+52 55 1234 5678',
          },
          {
            id: JOSE.personal!.emergency_contacts[1]!.id,
            name: 'Luis Ñúñez',
            relationship: 'Padre',
            phone: '55 0000 1111',
          },
        ],
      },
    ]);
  });

  it('reordering sends the new order and keeps focus on the moved row', async () => {
    const api = stub(MANAGER, {
      [patchRoute(JOSE)]: () => jsonResponse(200, { ...JOSE, version: 4 }),
    });
    app = await renderApp(`/students/${JOSE_ID}/edit`);

    expect(buttonNamed(main(), 'Move up contact 1').disabled).toBe(true);
    const up = buttonNamed(main(), 'Move up contact 2');
    act(() => up.focus());
    await click(up);
    // Luis is first now: his "Move up" is disabled, so focus is on "Move down".
    expect(field('Contact 1 name').value).toBe('Luis Ñúñez');
    expect(document.activeElement).toBe(
      buttonNamed(main(), 'Move down contact 1'),
    );
    await submit();

    const bodies = api.calls(patchRoute(JOSE)).map((c) => c.body) as {
      emergency_contacts: { name: string }[];
    }[];
    expect(bodies[0]!.emergency_contacts.map((c) => c.name)).toEqual([
      'Luis Ñúñez',
      'María Pérez',
    ]);
  });

  it('removing a contact moves focus to a neighbour', async () => {
    stub();
    app = await renderApp(`/students/${JOSE_ID}/edit`);

    await click(buttonNamed(main(), 'Remove contact 2'));

    expect(document.activeElement).toBe(field('Contact 1 name'));
    expect(buttonNamed(main(), 'Remove contact 1').disabled).toBe(true);
  });

  it('an unchanged form sends only the version', async () => {
    const api = stub(MANAGER, {
      [patchRoute(JOSE)]: () => jsonResponse(200, JOSE),
    });
    app = await renderApp(`/students/${JOSE_ID}/edit`);

    await submit();

    expect(api.calls(patchRoute(JOSE)).map((c) => c.body)).toEqual([
      { version: 3 },
    ]);
  });

  it('five contacts: "Add contact" is disabled', async () => {
    stub(MANAGER, {
      [detailRoute(FIVE_CONTACTS)]: () => jsonResponse(200, FIVE_CONTACTS),
    });
    app = await renderApp(`/students/${JOSE_ID}/edit`);

    expect(buttonNamed(main(), 'Add contact').disabled).toBe(true);
  });

  it('keeps an inactive current level as a selected "(inactive)" option', async () => {
    const api = stub(MANAGER, {
      [patchRoute(ANA_INACTIVE_LEVEL)]: () =>
        jsonResponse(200, ANA_INACTIVE_LEVEL),
    });
    app = await renderApp(`/students/${ANA_INACTIVE_LEVEL.id}/edit`);

    const select = selectByLabel('Education level (optional)');
    expect([...select.options].map((o) => o.textContent)).toEqual([
      'None',
      'Primaria',
      'Secundaria',
      'Posgrado (inactive)',
    ]);
    expect(select.value).toBe(POSGRADO_REF.id);
    typeInto(field('Grade (optional)'), '3.º');
    await submit();

    expect(
      api.calls(patchRoute(ANA_INACTIVE_LEVEL)).map((c) => c.body),
    ).toEqual([{ version: 1, school_grade: '3.º' }]);
  });

  it('puts education_level_unavailable and emergency_contact_unknown on their fields', async () => {
    const fieldProblem = new Response(
      JSON.stringify({
        type: 'about:blank',
        title: 'Unprocessable Content',
        status: 422,
        code: 'emergency_contact_unknown',
        request_id: 'req-422',
        errors: [
          {
            path: ['body', 'emergency_contacts', 1, 'id'],
            code: 'emergency_contact_unknown',
            message: 'x',
          },
        ],
      }),
      { status: 422, headers: { 'Content-Type': 'application/problem+json' } },
    );
    const levelProblem = new Response(
      JSON.stringify({
        type: 'about:blank',
        title: 'Unprocessable Content',
        status: 422,
        code: 'education_level_unavailable',
        request_id: 'req-422b',
        errors: [
          {
            path: ['body', 'education_level_id'],
            code: 'education_level_unavailable',
            message: 'x',
          },
        ],
      }),
      { status: 422, headers: { 'Content-Type': 'application/problem+json' } },
    );
    stub(MANAGER, {
      [patchRoute(JOSE)]: [() => fieldProblem, () => levelProblem],
    });
    app = await renderApp(`/students/${JOSE_ID}/edit`);

    typeInto(field('Contact 2 name'), 'Luis N.');
    await submit();
    expect(field('Contact 2 name').getAttribute('aria-invalid')).toBe('true');
    expect(main().textContent).toContain(
      'One of the contacts no longer exists. Reload and try again.',
    );
    expect(document.activeElement).toBe(field('Contact 2 name'));

    selectOption(selectByLabel('Education level (optional)'), PRIMARIA_REF.id);
    await submit();
    expect(
      selectByLabel('Education level (optional)').getAttribute('aria-invalid'),
    ).toBe('true');
    expect(main().textContent).toContain('Choose an active education level.');
  });

  it('version_conflict shows Reload, which refetches and resets the form', async () => {
    const newer: StudentDetail = { ...JOSE, given_names: 'Pepe', version: 4 };
    const api = stub(MANAGER, {
      [detailRoute(JOSE)]: [
        () => jsonResponse(200, JOSE),
        () => jsonResponse(200, newer),
      ],
      [patchRoute(JOSE)]: [
        () => problem(409, 'version_conflict'),
        () => jsonResponse(200, { ...newer, school_grade: '3.º', version: 5 }),
      ],
    });
    app = await renderApp(`/students/${JOSE_ID}/edit`);

    typeInto(field('Grade (optional)'), '3.º');
    await submit();
    expect(alerts(main()).join('')).toContain(
      'Someone else changed this record.',
    );
    await click(buttonNamed(main(), 'Reload'));

    expect(field('Given names').value).toBe('Pepe');
    expect(field('Grade (optional)').value).toBe('2.º de secundaria');
    typeInto(field('Grade (optional)'), '3.º');
    await submit();
    expect(api.calls(patchRoute(JOSE)).map((c) => c.body)).toEqual([
      { version: 3, school_grade: '3.º' },
      { version: 4, school_grade: '3.º' },
    ]);
  });

  it('changing the language keeps typed values', async () => {
    stub();
    app = await renderApp(`/students/${JOSE_ID}/edit`);

    typeInto(field('Given names'), 'Escrito');
    act(() => applyLanguage('es'));
    await settle();

    expect(fieldByLabel(main(), 'Nombre(s)').value).toBe('Escrito');
  });
});

// --- J3, J4: the detail ------------------------------------------------------------

describe('the detail (J3, J4)', () => {
  it('a full view shows the birthdate, contact details and contacts', async () => {
    stub();
    app = await renderApp(`/students/${JOSE_ID}`);

    const text = main().textContent ?? '';
    expect(main().querySelector('h1')?.textContent).toBe('José Ñúñez Pérez');
    expect(text).toContain('Minor');
    expect(text).toContain('Active');
    expect(text).toContain('May 4, 2012');
    expect(text).toContain('jose@example.com');
    const items = [...main().querySelectorAll('ol li')].map(
      (li) => li.textContent,
    );
    expect(items).toEqual([
      'María Pérez · Madre · +52 55 1234 5678',
      'Luis Ñúñez · Padre · 55 8765 4321',
    ]);
    expect(main().querySelector('ol li a')?.getAttribute('href')).toBe(
      'tel:+52 55 1234 5678',
    );
  });

  it('a limited view renders no birthdate, contact or emergency-contact text', async () => {
    stub(VIEWER, {
      [detailRoute(JOSE)]: () => jsonResponse(200, JOSE_LIMITED),
    });
    app = await renderApp(`/students/${JOSE_ID}`);

    const text = main().textContent ?? '';
    expect(main().querySelector('h1')?.textContent).toBe('José Ñúñez Pérez');
    expect(text).toContain('Secundaria');
    for (const hidden of [
      'May 4, 2012',
      '2012',
      'jose@example.com',
      'María Pérez',
      '55 1234 5678',
      'Emergency contacts',
      'Personal details',
      'Birthdate',
    ]) {
      expect(text).not.toContain(hidden);
    }
    expect(main().querySelector('a[href^="tel:"]')).toBeNull();
  });

  it('"Edit" and "Deactivate" appear only with student.manage', async () => {
    stub(VIEWER);
    app = await renderApp(`/students/${JOSE_ID}`);
    expect(main().textContent).not.toContain('Edit student');
    expect(main().textContent).not.toContain('Deactivate');
    app.unmount();

    stub();
    app = await renderApp(`/students/${JOSE_ID}`);
    const edit = [...main().querySelectorAll('a')].find(
      (a) => a.textContent === 'Edit student',
    );
    expect(edit?.getAttribute('href')).toBe(`/students/${JOSE_ID}/edit`);
    expect(buttonNamed(main(), 'Deactivate')).toBeTruthy();
  });

  it('deactivating confirms, sends {version, is_active} and returns focus', async () => {
    const api = stub(MANAGER, {
      [patchRoute(JOSE)]: () =>
        jsonResponse(200, { ...JOSE, is_active: false, version: 4 }),
    });
    app = await renderApp(`/students/${JOSE_ID}`);
    const deactivate = buttonNamed(main(), 'Deactivate');
    act(() => deactivate.focus());

    await click(deactivate);
    const dialog = document.querySelector<HTMLElement>('[role="dialog"]')!;
    expect(dialog.textContent).toContain('Deactivate student');
    expect(dialog.textContent).toContain(
      'The profile is kept and can be reactivated.',
    );
    await click(buttonNamed(dialog, 'Deactivate'));

    expect(api.calls(patchRoute(JOSE)).map((c) => c.body)).toEqual([
      { version: 3, is_active: false },
    ]);
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    expect(main().textContent).toContain('Inactive');
    expect(statusText()).toBe('Changes saved.');
    expect(document.activeElement).toBe(buttonNamed(main(), 'Reactivate'));
  });

  it('a missing or hidden student says it does not exist', async () => {
    stub(VIEWER, {
      [detailRoute(JOSE)]: () => problem(404, 'not_found'),
    });
    app = await renderApp(`/students/${JOSE_ID}`);

    expect(main().querySelector('h1')?.textContent).toBe(
      "This student doesn't exist.",
    );
    expect(alerts(main())).toEqual([]);
    const back = main().querySelector('a');
    expect(back?.textContent).toBe('Back to students');
    expect(back?.getAttribute('href')).toBe('/students');
  });
});
