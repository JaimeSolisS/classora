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
import type { ApprovalRequestItem } from './api';

// #40 F1–F5: the /approvals page, its navigation item and route access, the
// filters in the URL, the per-type summary, the decisions (reject body,
// handler errors) and the /registration-requests redirect.

const LIST = 'GET /api/v1/approval-requests';
const ID = '00000000-0000-4000-8000-0000000000c1';
const APPROVE = `POST /api/v1/approval-requests/${ID}/approve`;
const REJECT = `POST /api/v1/approval-requests/${ID}/reject`;

const REVIEWER: SessionUserBody = {
  ...TEST_USER,
  permissions: ['registration.review', 'user.role.assign'],
};

const ROW: ApprovalRequestItem = {
  id: ID,
  request_type: 'identity.registration',
  status: 'pending',
  priority: 'normal',
  requester: {
    id: '00000000-0000-4000-8000-0000000000a1',
    display_name: 'Rosa Díaz',
  },
  assignee: null,
  created_at: '2026-10-19T15:00:00Z',
  resolved_at: null,
  resolved_by: null,
  reason: null,
  subject: {
    username: 'rosa.diaz',
    display_name: 'Rosa Díaz',
    requested_role: 'guardian',
  },
  allowed_actions: ['approve', 'reject'],
  version: 1,
};

const OTHER_TYPE: ApprovalRequestItem = {
  ...ROW,
  id: '00000000-0000-4000-8000-0000000000c2',
  request_type: 'finance.refund',
  priority: 'high',
  status: 'changes_requested',
  requester: null,
  subject: { subject_name: 'Math' },
  allowed_actions: ['reject'],
  reason: 'Add the certificate.',
};

const page = (items: ApprovalRequestItem[], total = items.length) =>
  jsonResponse(200, { items, total, limit: 10, offset: 0 });

let app: RenderedApp | undefined;

const main = () => app!.container.querySelector('main')!;
const selects = () => [...main().querySelectorAll('select')];
const statusText = () =>
  main().querySelector('[role="status"]')?.textContent ?? '';
const rows = () => [...main().querySelectorAll('tbody tr')];
const lastListUrl = (api: ApiStub) => {
  const lists = api.fetch.mock.calls.filter(
    ([request]) =>
      new URL(request.url).pathname === '/api/v1/approval-requests',
  );
  return new URL(lists.at(-1)![0].url);
};

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

function typeReason(field: HTMLTextAreaElement, value: string) {
  // eslint-disable-next-line @typescript-eslint/unbound-method
  const setter = Object.getOwnPropertyDescriptor(
    HTMLTextAreaElement.prototype,
    'value',
  )?.set;
  act(() => {
    setter?.call(field, value);
    field.dispatchEvent(new Event('input', { bubbles: true }));
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

describe('route and navigation (F1, F2)', () => {
  it('shows the page and the Approvals item with registration.review only', async () => {
    const api = stub({ ...TEST_USER, permissions: ['registration.review'] });

    app = await renderApp('/approvals');

    expect(main().querySelectorAll('h1')).toHaveLength(1);
    expect(main().querySelector('h1')?.textContent).toBe('Approvals');
    expect(document.title).toBe('Approvals · Classora');
    const nav = [...app.container.querySelectorAll('header nav a')].map(
      (a) => a.textContent,
    );
    expect(nav).toEqual(['Home', 'Approvals']);
    const url = lastListUrl(api);
    expect(url.searchParams.get('status')).toBe('open');
    expect(url.searchParams.get('assignee')).toBe('any');
    expect(url.searchParams.has('request_type')).toBe(false);
  });

  it('a user without a review permission gets no item and the forbidden page', async () => {
    const api = stub({ ...TEST_USER, permissions: ['user.view'] });

    app = await renderApp('/approvals');

    const nav = [...app.container.querySelectorAll('header nav a')].map(
      (a) => a.textContent,
    );
    expect(nav).toEqual(['Home', 'Users']);
    expect(main().querySelector('h1')?.textContent).toBe(
      "You don't have access to this page",
    );
    expect(api.calls(LIST)).toHaveLength(0);
  });

  it('/registration-requests redirects to the registrations in Approvals', async () => {
    const api = stub();

    app = await renderApp('/registration-requests');

    expect(app.location()).toBe('/approvals?type=identity.registration');
    expect(main().querySelector('h1')?.textContent).toBe('Approvals');
    expect(lastListUrl(api).searchParams.get('request_type')).toBe(
      'identity.registration',
    );
    expect(selects()[1]!.value).toBe('identity.registration');
  });
});

describe('the list (F3)', () => {
  it('shows type, summary, requester, submitted, priority, status and the allowed actions', async () => {
    stub(REVIEWER, { [LIST]: () => page([ROW, OTHER_TYPE]) });
    app = await renderApp('/approvals');

    const headers = [...main().querySelectorAll('thead th')].map(
      (th) => th.textContent,
    );
    expect(headers).toEqual([
      'Type',
      'Request',
      'Requested by',
      'Submitted',
      'Priority',
      'Status',
      'Actions',
    ]);
    const cells = (row: Element) =>
      [...row.querySelectorAll('td')].map((td) => td.textContent);
    const [first, second] = rows();
    expect(cells(first!).slice(0, 3)).toEqual([
      'Account request',
      'Rosa Díaz (rosa.diaz) · Parent or guardian',
      'Rosa Díaz',
    ]);
    expect(cells(first!).slice(4, 6)).toEqual(['Normal', 'Pending']);
    expect(buttonNamed(first!, 'Approve rosa.diaz')).toBeTruthy();
    expect(buttonNamed(first!, 'Reject rosa.diaz')).toBeTruthy();
    // An unknown type shows its code; a system item has no requester.
    expect(cells(second!).slice(0, 3)).toEqual([
      'finance.refund',
      'finance.refund',
      'System',
    ]);
    expect(cells(second!).slice(4, 6)).toEqual(['High', 'Changes requested']);
    expect(second!.querySelectorAll('button')).toHaveLength(1);
  });

  it('the filters go to the query and the URL', async () => {
    const api = stub();
    app = await renderApp('/approvals');
    const [status, type, assignee] = selects();
    expect([...status!.options].map((o) => [o.value, o.textContent])).toEqual([
      ['open', 'Open'],
      ['approved', 'Approved'],
      ['rejected', 'Rejected'],
      ['cancelled', 'Cancelled'],
      ['all', 'All'],
    ]);
    expect([...type!.options].map((o) => [o.value, o.textContent])).toEqual([
      ['', 'All types'],
      ['identity.registration', 'Account request'],
      ['staff.qualification', 'Tutor qualification'],
    ]);
    expect([...assignee!.options].map((o) => [o.value, o.textContent])).toEqual(
      [
        ['any', 'Anyone'],
        ['me', 'Assigned to me'],
        ['unassigned', 'Unassigned'],
      ],
    );

    choose(status!, 'rejected');
    await click(status!);
    choose(selects()[1]!, 'identity.registration');
    await click(selects()[1]!);
    choose(selects()[2]!, 'me');
    await click(selects()[2]!);

    expect(app.location()).toBe(
      '/approvals?status=rejected&type=identity.registration&assignee=me',
    );
    const url = lastListUrl(api);
    expect(url.searchParams.get('status')).toBe('rejected');
    expect(url.searchParams.get('request_type')).toBe('identity.registration');
    expect(url.searchParams.get('assignee')).toBe('me');
  });

  it('reads the filters and the page from the URL; unknown values fall back', async () => {
    const api = stub();
    app = await renderApp('/approvals?status=all&type=nope&assignee=x&page=3');

    const url = lastListUrl(api);
    expect(url.searchParams.get('status')).toBe('all');
    expect(url.searchParams.has('request_type')).toBe(false);
    expect(url.searchParams.get('assignee')).toBe('any');
    expect(url.searchParams.get('offset')).toBe('20');
  });

  it('shows the empty and the error states', async () => {
    // A 4xx: queries never retry it, so the error shows at once.
    stub(REVIEWER, {
      [LIST]: [() => page([]), () => problem(400, 'bad_request')],
    });
    app = await renderApp('/approvals');
    expect(main().textContent).toContain('Nothing to review.');

    choose(selects()[0]!, 'rejected');
    await click(selects()[0]!);

    expect(alerts(main())[0]).toContain(
      "We couldn't process the request. Check the data and try again.",
    );
    expect(buttonNamed(main(), 'Try again')).toBeTruthy();
  });
});

describe('decisions (F4)', () => {
  it('approve asks for confirmation, sends it and announces it', async () => {
    let answer: (response: Response) => void = () => {};
    const api = stub(REVIEWER, {
      [LIST]: [() => page([ROW]), () => page([])],
      [APPROVE]: () =>
        new Promise<Response>((resolve) => {
          answer = resolve;
        }),
    });
    app = await renderApp('/approvals');

    await click(buttonNamed(main(), 'Approve rosa.diaz'));

    const dialog = openDialog()!;
    expect(dialog.textContent).toContain(
      'Approve: Rosa Díaz (rosa.diaz) · Parent or guardian',
    );
    expect(dialog.textContent).toContain(
      'Confirm that you have verified the identity of Rosa Díaz (Parent or guardian).',
    );
    expect(dialog.querySelector('textarea')).toBeNull();
    await click(buttonNamed(dialog, 'Approve'));

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
    await click(selects()[0]!);

    expect(openDialog()).toBeNull();
    expect(statusText()).toBe('Approved.');
    expect(main().textContent).toContain('Nothing to review.');
  });

  it('reject sends the optional reason trimmed, or no reason', async () => {
    const api = stub(REVIEWER, {
      [REJECT]: () => jsonResponse(200, { ...ROW, status: 'rejected' }),
    });
    app = await renderApp('/approvals');

    await click(buttonNamed(main(), 'Reject rosa.diaz'));
    let dialog = openDialog()!;
    expect(dialog.textContent).toContain(
      "The username rosa.diaz can't be used again.",
    );
    expect(dialog.textContent).toContain('Reason (optional)');
    typeReason(dialog.querySelector('textarea')!, '  Unknown family.  ');
    await click(buttonNamed(dialog, 'Reject'));

    expect(api.calls(REJECT)[0]!.body).toEqual({ reason: 'Unknown family.' });
    expect(api.calls(APPROVE)).toHaveLength(0);
    expect(openDialog()).toBeNull();
    expect(statusText()).toBe('Rejected.');

    await click(buttonNamed(main(), 'Reject rosa.diaz'));
    dialog = openDialog()!;
    await click(buttonNamed(dialog, 'Reject'));
    expect(api.calls(REJECT)[1]!.body).toEqual({});
  });

  it('request changes needs a reason before sending', async () => {
    const changes = { ...ROW, allowed_actions: ['approve', 'request_changes'] };
    const REQUEST_CHANGES = `POST /api/v1/approval-requests/${ID}/request-changes`;
    const api = stub(REVIEWER, {
      [LIST]: () => page([changes as ApprovalRequestItem]),
      [REQUEST_CHANGES]: () =>
        jsonResponse(200, { ...ROW, status: 'changes_requested' }),
    });
    app = await renderApp('/approvals');

    await click(buttonNamed(main(), 'Request changes from rosa.diaz'));
    const dialog = openDialog()!;
    expect(dialog.textContent).toContain('Reason (required)');
    await click(buttonNamed(dialog, 'Request changes'));

    expect(api.calls(REQUEST_CHANGES)).toHaveLength(0);
    const field = dialog.querySelector('textarea')!;
    expect(field.getAttribute('aria-invalid')).toBe('true');
    expect(
      document.getElementById(field.getAttribute('aria-describedby')!)
        ?.textContent,
    ).toBe('Give a reason.');

    typeReason(field, 'Add the date of birth.');
    await click(buttonNamed(dialog, 'Request changes'));
    expect(api.calls(REQUEST_CHANGES)[0]!.body).toEqual({
      reason: 'Add the date of birth.',
    });
    expect(statusText()).toBe('Changes requested.');
  });

  it("a handler's error shows in the dialog and the request stays", async () => {
    stub(REVIEWER, {
      [APPROVE]: () => problem(409, 'guardian_consent_required'),
    });
    app = await renderApp('/approvals');

    await click(buttonNamed(main(), 'Approve rosa.diaz'));
    await click(buttonNamed(openDialog()!, 'Approve'));

    expect(openDialog()).not.toBeNull();
    expect(alerts(openDialog()!)[0]).toContain(
      'The student is a minor and needs consent from a linked guardian.',
    );
    expect(statusText()).toBe('');
  });

  it('approval_transition_invalid shows in the dialog and refreshes the list', async () => {
    const api = stub(REVIEWER, {
      [LIST]: [() => page([ROW]), () => page([])],
      [APPROVE]: () => problem(409, 'approval_transition_invalid'),
    });
    app = await renderApp('/approvals');

    await click(buttonNamed(main(), 'Approve rosa.diaz'));
    await click(buttonNamed(openDialog()!, 'Approve'));

    expect(alerts(openDialog()!)[0]).toContain(
      "This request was already decided or can't take that action now.",
    );
    expect(api.calls(LIST)).toHaveLength(2);
  });

  it('cancel and Escape close the dialog without a request', async () => {
    const api = stub();
    app = await renderApp('/approvals');

    await click(buttonNamed(main(), 'Approve rosa.diaz'));
    await click(buttonNamed(openDialog()!, 'Cancel'));
    expect(openDialog()).toBeNull();
    await click(buttonNamed(main(), 'Reject rosa.diaz'));
    await pressEscape();
    expect(openDialog()).toBeNull();

    expect(api.calls(APPROVE)).toHaveLength(0);
    expect(api.calls(REJECT)).toHaveLength(0);
  });
});
