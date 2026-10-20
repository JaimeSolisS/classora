import { act } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { applyLanguage } from '@/i18n';
import { CSRF_HEADER } from '@/features/auth/csrf';
import type { SessionUserBody } from '@/features/auth/session';
import {
  meAs,
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
import type { TutorDetail } from './api';
import {
  NEW_TUTOR,
  TUTOR_AS_ADMIN,
  TUTOR_AS_SELF,
  TUTOR_ID,
  TUTOR_ITEM,
  TUTOR_ITEMS,
  tutorPage,
} from './fixtures';

// #43 E1-E4: the Tutors navigation item and routes (tutor.view), the list,
// the profile page, the form fields for an Administrator and for the tutor
// themselves, the PATCH body (version 0 for a tutor without a profile), the
// version conflict and the 404 view.

const LIST = 'GET /api/v1/tutors';
const DETAIL = `GET /api/v1/tutors/${TUTOR_ID}`;
const PATCH = `PATCH /api/v1/tutors/${TUTOR_ID}`;

/** An Administrator: views and manages tutors. */
const ADMIN: SessionUserBody = {
  ...TEST_USER,
  roles: ['administrator'],
  permissions: ['tutor.view', 'tutor.manage'],
};

/** The tutor themselves: tutor.view only. */
const SELF: SessionUserBody = {
  ...TEST_USER,
  id: TUTOR_ID,
  username: TUTOR_ITEM.username,
  display_name: TUTOR_ITEM.display_name,
  roles: ['tutor'],
  permissions: ['student.view', 'tutor.view'],
};

let app: RenderedApp | undefined;

const main = () => app!.container.querySelector('main')!;
const field = (label: string) => fieldByLabel(main(), label);
const labels = () =>
  [...main().querySelectorAll('label')].map((l) => l.textContent);
const navItems = () =>
  [...app!.container.querySelectorAll('header nav a')].map(
    (a) => a.textContent,
  );

/** The query strings of every list request, in order. */
function listSearches(api: ApiStub): string[] {
  return api.fetch.mock.calls
    .map(([request]) => new URL(request.url))
    .filter((url) => url.pathname === '/api/v1/tutors')
    .map((url) => url.search);
}

function stub(
  user: SessionUserBody = ADMIN,
  detail: TutorDetail = TUTOR_AS_ADMIN,
  routes: Record<string, Responder | Responder[]> = {},
): ApiStub {
  return stubApi({
    'GET /api/v1/auth/me': meAs(user),
    [LIST]: () => jsonResponse(200, tutorPage()),
    [DETAIL]: () => jsonResponse(200, detail),
    ...routes,
  });
}

/** Types into the textarea labelled `label` (React sees an input event). */
function typeIntoTextarea(label: string, value: string) {
  const found = [...main().querySelectorAll('label')].find(
    (l) => l.textContent === label,
  );
  const area = found ? document.getElementById(found.htmlFor) : null;
  if (!(area instanceof HTMLTextAreaElement)) {
    throw new Error(`No textarea labelled "${label}"`);
  }
  // eslint-disable-next-line @typescript-eslint/unbound-method
  const setter = Object.getOwnPropertyDescriptor(
    HTMLTextAreaElement.prototype,
    'value',
  )?.set;
  act(() => {
    setter?.call(area, value);
    area.dispatchEvent(new Event('input', { bubbles: true }));
  });
}

async function openForm() {
  await click(buttonNamed(main(), 'Edit'));
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
  window.localStorage.clear();
  window.sessionStorage.clear();
});

describe('the /tutors route and navigation (E1)', () => {
  it('with tutor.view: the Tutors item and the list with one h1', async () => {
    stub();

    app = await renderApp('/tutors');

    expect(navItems()).toContain('Tutors');
    expect(main().querySelectorAll('h1')).toHaveLength(1);
    expect(main().querySelector('h1')?.textContent).toBe('Tutors');
    expect(document.title).toBe('Tutors · Classora');
  });

  it('a tutor sees the item too and a list with only themselves', async () => {
    const api = stub(SELF, TUTOR_AS_SELF, {
      [LIST]: () => jsonResponse(200, tutorPage([TUTOR_ITEM])),
    });

    app = await renderApp('/tutors');

    expect(navItems()).toContain('Tutors');
    expect(main().querySelectorAll('tbody tr')).toHaveLength(1);
    expect(listSearches(api)).toEqual(['?limit=25&offset=0']);
  });

  it('without tutor.view: no item, the forbidden page and no request', async () => {
    const api = stub({ ...TEST_USER, permissions: ['student.view'] });

    app = await renderApp('/tutors');

    expect(navItems()).not.toContain('Tutors');
    expect(main().querySelector('h1')?.textContent).toBe(
      "You don't have access to this page",
    );
    expect(api.calls(LIST)).toHaveLength(0);
  });

  it('the profile route needs tutor.view too', async () => {
    const api = stub({ ...TEST_USER, permissions: [] });

    app = await renderApp(`/tutors/${TUTOR_ID}`);

    expect(main().querySelector('h1')?.textContent).toBe(
      "You don't have access to this page",
    );
    expect(api.calls(DETAIL)).toHaveLength(0);
  });
});

describe('the list (E2)', () => {
  it('shows name, username, state, email and phone with a link', async () => {
    stub();

    app = await renderApp('/tutors');

    const rows = main().querySelectorAll('tbody tr');
    expect(rows).toHaveLength(TUTOR_ITEMS.length);
    const cells = [...rows[1]!.querySelectorAll('td')].map(
      (c) => c.textContent,
    );
    expect(cells).toEqual([
      'Pablo Ortega',
      'pablo.tutor',
      'Suspended',
      'Not set',
      '55 8765 4321',
    ]);
    expect(rows[0]!.querySelector('a')?.getAttribute('href')).toBe(
      `/tutors/${TUTOR_ID}`,
    );
  });

  it('sends the state filter and keeps it in the URL', async () => {
    const api = stub();
    app = await renderApp('/tutors?page=2');

    const state = main().querySelector('select')!;
    act(() => {
      state.value = 'suspended';
      state.dispatchEvent(new Event('change', { bubbles: true }));
    });
    await settle();

    expect(listSearches(api)).toEqual([
      '?limit=25&offset=25',
      '?state=suspended&limit=25&offset=0',
    ]);
    expect(app.location()).toBe('/tutors?state=suspended');
  });

  it('shows the empty state', async () => {
    stub(ADMIN, TUTOR_AS_ADMIN, {
      [LIST]: () => jsonResponse(200, tutorPage([])),
    });

    app = await renderApp('/tutors?q=nobody');

    expect(main().textContent).toContain('No tutors match these filters.');
  });
});

describe('the profile page and form (E3, E4)', () => {
  it('shows the profile as text with Edit for an Administrator', async () => {
    stub();

    app = await renderApp(`/tutors/${TUTOR_ID}`);

    expect(main().querySelector('h1')?.textContent).toBe('Lucía Méndez');
    expect(document.title).toBe('Lucía Méndez · Classora');
    expect(main().textContent).toContain('lucia@example.com');
    expect(main().textContent).toContain('Eight years teaching mathematics.');
    expect(main().querySelector('form')).toBeNull();
    await openForm();
    expect(labels()).toEqual([
      'Name',
      'Email (optional)',
      'Phone (optional)',
      'Professional experience (optional)',
    ]);
  });

  it('the tutor themselves gets no name field', async () => {
    stub(SELF, TUTOR_AS_SELF);
    app = await renderApp(`/tutors/${TUTOR_ID}`);

    await openForm();

    expect(labels()).toEqual([
      'Email (optional)',
      'Phone (optional)',
      'Professional experience (optional)',
    ]);
  });

  it('a read-only viewer gets no Edit button', async () => {
    stub(
      {
        ...TEST_USER,
        roles: ['academic_coordinator'],
        permissions: ['tutor.view'],
      },
      { ...TUTOR_AS_ADMIN, editable_fields: [] },
    );

    app = await renderApp(`/tutors/${TUTOR_ID}`);

    expect(
      [...main().querySelectorAll('button')].map((b) => b.textContent),
    ).not.toContain('Edit');
  });

  it('the tutor sends the loaded version and only the changed fields', async () => {
    const saved = { ...TUTOR_AS_SELF, phone: '55 0000 1111', version: 3 };
    const api = stub(SELF, TUTOR_AS_SELF, {
      [PATCH]: () => jsonResponse(200, saved),
    });
    app = await renderApp(`/tutors/${TUTOR_ID}`);
    await openForm();

    typeInto(field('Phone (optional)'), ' 55 0000 1111 ');
    await click(buttonNamed(main(), 'Save'));

    const [call] = api.calls(PATCH);
    expect(call!.body).toEqual({ version: 2, phone: '55 0000 1111' });
    expect(call!.headers.get(CSRF_HEADER)).toBe(TEST_USER.csrf_token);
    expect(main().querySelector('form')).toBeNull();
    expect(main().textContent).toContain('Changes saved.');
    expect(main().textContent).toContain('55 0000 1111');
    expect(api.calls(DETAIL)).toHaveLength(1);
  });

  it('a tutor without a profile sends version 0; clearing sends null', async () => {
    const api = stub(
      ADMIN,
      { ...NEW_TUTOR, email: 'old@example.com' },
      {
        [PATCH]: () => jsonResponse(200, { ...NEW_TUTOR, version: 1 }),
      },
    );
    app = await renderApp(`/tutors/${TUTOR_ID}`);
    await openForm();

    typeInto(field('Email (optional)'), '  ');
    typeIntoTextarea(
      'Professional experience (optional)',
      '  Physics teacher.  ',
    );
    await click(buttonNamed(main(), 'Save'));

    expect(api.calls(PATCH)[0]!.body).toEqual({
      version: 0,
      email: null,
      professional_experience: 'Physics teacher.',
    });
  });

  it('validates on the client without a request', async () => {
    const api = stub();
    app = await renderApp(`/tutors/${TUTOR_ID}`);
    await openForm();

    typeInto(field('Name'), '  ');
    typeInto(field('Phone (optional)'), 'call me');
    await click(buttonNamed(main(), 'Save'));

    expect(api.calls(PATCH)).toHaveLength(0);
    expect(field('Name').getAttribute('aria-invalid')).toBe('true');
    expect(field('Phone (optional)').getAttribute('aria-invalid')).toBe('true');
    expect(main().textContent).toContain('Enter a name.');
  });

  it('a version conflict offers Reload, which refetches the profile', async () => {
    const api = stub(ADMIN, TUTOR_AS_ADMIN, {
      [PATCH]: () => problem(409, 'version_conflict'),
    });
    app = await renderApp(`/tutors/${TUTOR_ID}`);
    await openForm();

    typeInto(field('Phone (optional)'), '55 0000 2222');
    await click(buttonNamed(main(), 'Save'));
    const alert = main().querySelector('[role="alert"]');
    expect(alert).not.toBeNull();
    await click(buttonNamed(alert as HTMLElement, 'Reload'));

    expect(api.calls(DETAIL)).toHaveLength(2);
  });

  it('Cancel closes the form without a request', async () => {
    const api = stub();
    app = await renderApp(`/tutors/${TUTOR_ID}`);
    await openForm();

    typeInto(field('Phone (optional)'), '55 0000 3333');
    await click(buttonNamed(main(), 'Cancel'));

    expect(main().querySelector('form')).toBeNull();
    expect(api.calls(PATCH)).toHaveLength(0);
    expect(document.activeElement?.textContent).toBe('Edit');
  });

  it('a 404 says the tutor does not exist, with a link back', async () => {
    stub(ADMIN, TUTOR_AS_ADMIN, { [DETAIL]: () => problem(404, 'not_found') });

    app = await renderApp(`/tutors/${TUTOR_ID}`);

    expect(main().querySelector('h1')?.textContent).toBe(
      "This tutor doesn't exist.",
    );
    expect(main().querySelector('[role="alert"]')).toBeNull();
    const back = main().querySelector('a');
    expect(back?.textContent).toBe('Back to tutors');
    expect(back?.getAttribute('href')).toBe('/tutors');
  });
});
