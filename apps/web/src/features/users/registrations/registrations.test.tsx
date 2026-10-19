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
import { alerts, click, openDialog, pressEscape } from '@/test/dom';
import {
  buttonNamed,
  jsonResponse,
  renderApp,
  type RenderedApp,
} from '@/test/render-app';
import type { RegistrationRequestItem } from './api';

// #169 G1–G3: the /registration-requests page, its navigation item, the
// status filter, the table, and the approve and reject dialogs (with 409).

const LIST = 'GET /api/v1/registration-requests';
const ID = '00000000-0000-4000-8000-0000000000c1';
const APPROVE = `POST /api/v1/registration-requests/${ID}/approve`;
const REJECT = `POST /api/v1/registration-requests/${ID}/reject`;

const REVIEWER: SessionUserBody = {
  ...TEST_USER,
  permissions: ['registration.review', 'user.role.assign'],
};

const ROW: RegistrationRequestItem = {
  id: ID,
  username: 'rosa.diaz',
  display_name: 'Rosa Díaz',
  requested_role: 'guardian',
  status: 'pending',
  submitted_at: '2026-10-19T15:00:00Z',
  decided_at: null,
};

const APPROVED_ROW: RegistrationRequestItem = {
  ...ROW,
  id: '00000000-0000-4000-8000-0000000000c2',
  username: 'luis.s',
  display_name: 'Luis S',
  requested_role: 'student',
  status: 'approved',
  decided_at: '2026-10-19T16:30:00Z',
};

const page = (items: RegistrationRequestItem[], total = items.length) =>
  jsonResponse(200, { items, total, limit: 10, offset: 0 });

let app: RenderedApp | undefined;

const main = () => app!.container.querySelector('main')!;
const statusSelect = () => main().querySelector('select')!;
const statusText = () =>
  main().querySelector('[role="status"]')?.textContent ?? '';
const rows = () => [...main().querySelectorAll('tbody tr')];

function choose(select: HTMLSelectElement, value: string) {
  // eslint-disable-next-line @typescript-eslint/unbound-method
  const setter = Object.getOwnPropertyDescriptor(
    HTMLSelectElement.prototype,
    'value',
  )?.set;
  act(() => {
    setter?.call(select, value);
    select.dispatchEvent(new Event('change', { bubbles: true }));
  });
}

function stub(
  user: SessionUserBody = REVIEWER,
  routes: Record<string, Responder | Responder[]> = {},
): ApiStub {
  return stubApi({
    'GET /api/v1/auth/me': meAs(user),
    [LIST]: () => page([ROW]),
    ...routes,
  });
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

describe('route and navigation (G1)', () => {
  it('shows the page with one h1, the title and the navigation item', async () => {
    const api = stub();

    app = await renderApp('/registration-requests');

    expect(main().querySelectorAll('h1')).toHaveLength(1);
    expect(main().querySelector('h1')?.textContent).toBe('Account requests');
    expect(document.title).toBe('Account requests · Classora');
    const nav = [...app.container.querySelectorAll('header nav a')].map(
      (a) => a.textContent,
    );
    expect(nav).toEqual(['Home', 'Account requests']);
    const url = new URL(api.fetch.mock.calls.at(-1)![0].url);
    expect(url.pathname).toBe('/api/v1/registration-requests');
    expect(url.searchParams.get('status')).toBe('pending');
  });

  it('a user without registration.review gets no navigation item and the forbidden page', async () => {
    const api = stub({ ...TEST_USER, permissions: ['user.view'] });

    app = await renderApp('/registration-requests');

    const nav = [...app.container.querySelectorAll('header nav a')].map(
      (a) => a.textContent,
    );
    expect(nav).toEqual(['Home', 'Users']);
    expect(main().querySelector('h1')?.textContent).toBe(
      "You don't have access to this page",
    );
    expect(api.calls(LIST)).toHaveLength(0);
  });
});

describe('the list (G2)', () => {
  it('shows name, username, role name and submitted time; pending rows have the actions', async () => {
    stub();
    app = await renderApp('/registration-requests');

    const headers = [...main().querySelectorAll('thead th')].map(
      (th) => th.textContent,
    );
    expect(headers).toEqual([
      'Name',
      'Username',
      'Requested role',
      'Submitted',
      'Actions',
    ]);
    const cells = [...rows()[0]!.querySelectorAll('td')].map(
      (td) => td.textContent,
    );
    expect(cells.slice(0, 3)).toEqual([
      'Rosa Díaz',
      'rosa.diaz',
      'Parent or guardian',
    ]);
    expect(buttonNamed(main(), 'Approve rosa.diaz')).toBeTruthy();
    expect(buttonNamed(main(), 'Reject rosa.diaz')).toBeTruthy();
  });

  it('the status filter asks for decided rows and shows the decided column without actions', async () => {
    const api = stub(REVIEWER, {
      [LIST]: (request) =>
        new URL(request.url).searchParams.get('status') === 'approved'
          ? page([APPROVED_ROW])
          : page([ROW]),
    });
    app = await renderApp('/registration-requests');
    expect(
      [...statusSelect().options].map((o) => [o.value, o.textContent]),
    ).toEqual([
      ['pending', 'Pending'],
      ['approved', 'Approved'],
      ['rejected', 'Rejected'],
    ]);

    choose(statusSelect(), 'approved');
    await click(statusSelect());

    const url = new URL(api.fetch.mock.calls.at(-1)![0].url);
    expect(url.searchParams.get('status')).toBe('approved');
    const headers = [...main().querySelectorAll('thead th')].map(
      (th) => th.textContent,
    );
    expect(headers).toEqual([
      'Name',
      'Username',
      'Requested role',
      'Submitted',
      'Decided',
    ]);
    expect(rows()[0]!.textContent).toContain('Student');
    expect(main().querySelectorAll('tbody button')).toHaveLength(0);
  });

  it('shows the empty and the error states', async () => {
    // A 4xx: queries never retry it, so the error shows at once.
    stub(REVIEWER, {
      [LIST]: [() => page([]), () => problem(400, 'bad_request')],
    });
    app = await renderApp('/registration-requests');
    expect(main().textContent).toContain('There are no requests here.');

    choose(statusSelect(), 'rejected');
    await click(statusSelect());

    expect(alerts(main())[0]).toContain(
      "We couldn't process the request. Check the data and try again.",
    );
    expect(buttonNamed(main(), 'Try again')).toBeTruthy();
  });
});

describe('approve and reject (G3)', () => {
  it('approve opens a dialog naming the person and role; confirming sends it and refreshes', async () => {
    let answer: (response: Response) => void = () => {};
    const api = stub(REVIEWER, {
      [LIST]: [() => page([ROW]), () => page([])],
      [APPROVE]: () =>
        new Promise<Response>((resolve) => {
          answer = resolve;
        }),
    });
    app = await renderApp('/registration-requests');

    await click(buttonNamed(main(), 'Approve rosa.diaz'));

    const dialog = openDialog()!;
    expect(dialog.textContent).toContain(
      'Approve the request of Rosa Díaz (Parent or guardian)',
    );
    expect(dialog.textContent).toContain(
      'Confirm that you have verified the identity of Rosa Díaz (Parent or guardian).',
    );
    await click(buttonNamed(dialog, 'Approve'));

    // Pending: the confirm button waits with the spinner; one request.
    const confirm = [...dialog.querySelectorAll('button')].find((b) =>
      b.textContent?.includes('Approving…'),
    )!;
    expect(confirm.disabled).toBe(true);
    expect(api.calls(APPROVE)).toHaveLength(1);
    expect(api.calls(APPROVE)[0]!.headers.get(CSRF_HEADER)).toBe(
      REVIEWER.csrf_token,
    );

    await act(async () => {
      answer(jsonResponse(200, { ...ROW, status: 'approved' }));
      await Promise.resolve();
    });
    await click(statusSelect());

    expect(openDialog()).toBeNull();
    expect(statusText()).toBe('Request approved.');
    expect(api.calls(LIST).length).toBeGreaterThanOrEqual(2);
    expect(main().textContent).toContain('There are no requests here.');
  });

  it('reject warns that the username cannot be used again', async () => {
    const api = stub(REVIEWER, {
      [REJECT]: () => jsonResponse(200, { ...ROW, status: 'rejected' }),
    });
    app = await renderApp('/registration-requests');

    await click(buttonNamed(main(), 'Reject rosa.diaz'));
    const dialog = openDialog()!;
    expect(dialog.textContent).toContain(
      'Reject the request of Rosa Díaz (Parent or guardian)',
    );
    expect(dialog.textContent).toContain(
      "The username rosa.diaz can't be used again.",
    );
    await click(buttonNamed(dialog, 'Reject'));

    expect(api.calls(REJECT)).toHaveLength(1);
    expect(api.calls(APPROVE)).toHaveLength(0);
    expect(openDialog()).toBeNull();
    expect(statusText()).toBe('Request rejected.');
  });

  it('cancel and Escape close the dialog without a request', async () => {
    const api = stub();
    app = await renderApp('/registration-requests');

    await click(buttonNamed(main(), 'Approve rosa.diaz'));
    await click(buttonNamed(openDialog()!, 'Cancel'));
    expect(openDialog()).toBeNull();
    await click(buttonNamed(main(), 'Reject rosa.diaz'));
    await pressEscape();
    expect(openDialog()).toBeNull();

    expect(api.calls(APPROVE)).toHaveLength(0);
    expect(api.calls(REJECT)).toHaveLength(0);
  });

  it('409 registration_already_decided shows the message and refreshes the list', async () => {
    const api = stub(REVIEWER, {
      [LIST]: [() => page([ROW]), () => page([])],
      [APPROVE]: () => problem(409, 'registration_already_decided'),
    });
    app = await renderApp('/registration-requests');

    await click(buttonNamed(main(), 'Approve rosa.diaz'));
    await click(buttonNamed(openDialog()!, 'Approve'));

    expect(openDialog()).toBeNull();
    expect(alerts(main())[0]).toContain(
      'This request has already been decided.',
    );
    expect(api.calls(LIST)).toHaveLength(2);
    expect(statusText()).toBe('');
  });

  it('another error stays in the dialog', async () => {
    stub(REVIEWER, { [APPROVE]: () => problem(500, 'internal_error') });
    app = await renderApp('/registration-requests');

    await click(buttonNamed(main(), 'Approve rosa.diaz'));
    await click(buttonNamed(openDialog()!, 'Approve'));

    expect(openDialog()).not.toBeNull();
    expect(alerts(openDialog()!)[0]).toContain(
      'Something went wrong on the server.',
    );
  });
});
