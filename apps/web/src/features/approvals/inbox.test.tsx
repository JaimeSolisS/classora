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
import { alerts, click, pressEscape, setFieldValue } from '@/test/dom';
import {
  buttonNamed,
  jsonResponse,
  renderApp,
  settle,
  settleUntil,
  type RenderedApp,
} from '@/test/render-app';
import { APPROVAL_TYPES, type ApprovalRequestItem } from './api';
import { ITEMS_BY_TYPE, PENDING_REGISTRATION } from './fixtures';

// M8-01b: the full inbox on /approvals. Filters and sort in the URL (A),
// the "View details" button and the detail panel with its deep link (B),
// assignment (C), the count and the one polite live region (D2).

const LIST = 'GET /api/v1/approval-requests';
const ID = PENDING_REGISTRATION.id;
const DETAIL = `GET /api/v1/approval-requests/${ID}`;
const OPTIONS = `GET /api/v1/approval-requests/${ID}/assignee-options`;
const ASSIGN = `POST /api/v1/approval-requests/${ID}/assign`;
const APPROVE = `POST /api/v1/approval-requests/${ID}/approve`;

const ME = { id: TEST_USER.id, display_name: TEST_USER.display_name };
const LAURA = {
  id: '00000000-0000-4000-8000-0000000000d7',
  display_name: 'Laura Méndez',
};

const REVIEWER: SessionUserBody = {
  ...TEST_USER,
  permissions: [
    'registration.review',
    'qualification.review',
    'schedule.assign',
    'schedule.manage',
    'tutor.manage',
    'teaching_hours.approve',
    'progress_report.review',
    'payroll.finalize',
  ],
};

const page = (items: ApprovalRequestItem[], total = items.length) =>
  jsonResponse(200, { items, total, limit: 10, offset: 0 });

let app: RenderedApp | undefined;

const main = () => app!.container.querySelector('main')!;
const selects = () => [...main().querySelectorAll('select')];
const statusText = () =>
  main().querySelector('[role="status"]')?.textContent ?? '';
const countText = () =>
  main().querySelector('p[aria-live="polite"]')?.textContent ?? '';
const dialogs = () => [
  ...document.querySelectorAll<HTMLElement>('[role="dialog"]'),
];
const panel = () => dialogs()[0]!;
const lastListUrl = (api: ApiStub) => {
  const lists = api.fetch.mock.calls.filter(
    ([request]) =>
      new URL(request.url).pathname === '/api/v1/approval-requests',
  );
  return new URL(lists.at(-1)![0].url);
};

async function choose(select: HTMLSelectElement, value: string) {
  setFieldValue(select, value);
  await settle();
}

function stub(routes: Record<string, Responder | Responder[]> = {}): ApiStub {
  return stubApi({
    'GET /api/v1/auth/me': meAs(REVIEWER),
    [LIST]: () => page([PENDING_REGISTRATION]),
    [DETAIL]: () => jsonResponse(200, PENDING_REGISTRATION),
    [OPTIONS]: () => jsonResponse(200, { items: [ME, LAURA] }),
    ...routes,
  });
}

async function openPanel() {
  await click(
    buttonNamed(
      main(),
      'View details of Rosa Díaz (rosa.diaz) · Parent or guardian',
    ),
  );
  await settleUntil(() => panel()?.textContent?.includes('Assignment'));
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

describe('filters and sort (A1, A2, D2)', () => {
  it('priority and sort go to the query and the URL; defaults are left out', async () => {
    const api = stub();
    app = await renderApp('/approvals');
    const [, , , priority, sort] = selects();
    expect([...priority!.options].map((o) => [o.value, o.textContent])).toEqual(
      [
        ['', 'All'],
        ['high', 'High'],
        ['normal', 'Normal'],
        ['low', 'Low'],
      ],
    );
    expect([...sort!.options].map((o) => [o.value, o.textContent])).toEqual([
      ['priority', 'Priority (default)'],
      ['oldest', 'Oldest first'],
      ['newest', 'Newest first'],
    ]);
    expect(lastListUrl(api).searchParams.get('sort')).toBe('priority');
    expect(lastListUrl(api).searchParams.has('priority')).toBe(false);

    await choose(priority!, 'high');
    await choose(selects()[4]!, 'newest');

    expect(app.location()).toBe('/approvals?priority=high&sort=newest');
    expect(lastListUrl(api).searchParams.get('priority')).toBe('high');
    expect(lastListUrl(api).searchParams.get('sort')).toBe('newest');

    await choose(selects()[4]!, 'priority');
    expect(app.location()).toBe('/approvals?priority=high');
  });

  it('a filter change goes back to page 1; back and forward restore the filters', async () => {
    stub();
    app = await renderApp('/approvals?sort=oldest&page=3');

    await choose(selects()[3]!, 'low');
    expect(app.location()).toBe('/approvals?priority=low&sort=oldest');

    await act(async () => {
      await app!.router.navigate(-1);
    });
    await settle();
    expect(app.location()).toBe('/approvals?sort=oldest&page=3');
    expect(selects()[3]!.value).toBe('');
    expect(selects()[4]!.value).toBe('oldest');

    await act(async () => {
      await app!.router.navigate(1);
    });
    await settle();
    expect(selects()[3]!.value).toBe('low');
  });

  it('unknown priority and sort values fall back to the defaults', async () => {
    const api = stub();
    app = await renderApp('/approvals?priority=urgent&sort=random');

    expect(lastListUrl(api).searchParams.has('priority')).toBe(false);
    expect(lastListUrl(api).searchParams.get('sort')).toBe('priority');
    expect(selects()[3]!.value).toBe('');
    expect(selects()[4]!.value).toBe('priority');
  });

  it('"Clear filters" shows only when a filter differs and resets them all', async () => {
    stub();
    app = await renderApp('/approvals');
    const clear = () =>
      [...main().querySelectorAll('button')].find(
        (button) => button.textContent === 'Clear filters',
      );
    expect(clear()).toBeUndefined();

    app.unmount();
    app = await renderApp(
      '/approvals?status=all&type=staff.qualification&assignee=me&priority=low&sort=newest&page=2',
    );
    await click(clear()!);

    expect(app.location()).toBe('/approvals');
    expect(selects().map((select) => select.value)).toEqual([
      'open',
      '',
      'any',
      '',
      'priority',
    ]);
    expect(clear()).toBeUndefined();
    expect(document.activeElement).toBe(selects()[0]);
  });

  it('every filter has a label; the count is in a polite live region', async () => {
    stub({
      [LIST]: [() => page([PENDING_REGISTRATION], 12), () => page([], 0)],
    });
    app = await renderApp('/approvals');

    for (const select of selects()) {
      expect(
        main().querySelector(`label[for="${select.id}"]`)?.textContent,
      ).toBeTruthy();
    }
    expect(countText()).toBe('12 requests');
    await choose(selects()[0]!, 'rejected');
    expect(countText()).toBe('0 requests');
  });
});

describe('the list (A3)', () => {
  it('shows the assignee and a "View details" button named with the summary', async () => {
    stub({
      [LIST]: () =>
        page([
          { ...PENDING_REGISTRATION, assignee: LAURA },
          ITEMS_BY_TYPE['staff.qualification']!,
        ]),
    });
    app = await renderApp('/approvals');

    const [first, second] = [...main().querySelectorAll('tbody tr')];
    expect(first!.querySelectorAll('td')[6]!.textContent).toBe('Laura Méndez');
    expect(second!.querySelectorAll('td')[6]!.textContent).toBe('Unassigned');
    const names = [...main().querySelectorAll('tbody button')]
      .filter((button) => button.textContent === 'View details')
      .map((button) => button.getAttribute('aria-label'));
    expect(names).toEqual([
      'View details of Rosa Díaz (rosa.diaz) · Parent or guardian',
      'View details of Carmen Ruiz — Física (Preparatoria)',
    ]);
    // The row's own actions stay.
    expect(buttonNamed(first!, 'Approve rosa.diaz')).toBeTruthy();
  });
});

describe('the detail panel (B)', () => {
  it('opens from the row, writes ?item= and gives focus back on close', async () => {
    const api = stub();
    app = await renderApp('/approvals?type=identity.registration');
    const trigger = buttonNamed(
      main(),
      'View details of Rosa Díaz (rosa.diaz) · Parent or guardian',
    );
    trigger.focus();

    await openPanel();

    expect(app.location()).toBe(
      `/approvals?type=identity.registration&item=${ID}`,
    );
    const dialog = panel();
    const title = document.getElementById(
      dialog.getAttribute('aria-labelledby')!,
    );
    expect(title?.textContent).toBe(
      'Account request: Rosa Díaz (rosa.diaz) · Parent or guardian',
    );
    const text = dialog.textContent ?? '';
    for (const part of [
      'StatusPending',
      'PriorityNormal',
      'Requested byRosa Díaz',
      'Assigned toUnassigned',
      'Usernamerosa.diaz',
      'Requested roleParent or guardian',
    ]) {
      expect(text).toContain(part);
    }
    expect(api.calls(OPTIONS)).toHaveLength(1);

    await pressEscape();

    expect(dialogs()).toHaveLength(0);
    expect(app.location()).toBe('/approvals?type=identity.registration');
    expect(document.activeElement).toBe(trigger);
  });

  it('a deep link opens the panel; closing focuses the page heading', async () => {
    const api = stub();
    app = await renderApp(`/approvals?item=${ID}`);
    await settleUntil(() => panel()?.textContent?.includes('rosa.diaz'));

    expect(api.calls(DETAIL)).toHaveLength(1);
    await click(buttonNamed(panel(), 'Close'));

    expect(dialogs()).toHaveLength(0);
    expect(app.location()).toBe('/approvals');
    expect(document.activeElement).toBe(main().querySelector('h1'));
  });

  it('a 404 or a malformed id shows the not-found state with a close button', async () => {
    stub({ [DETAIL]: () => problem(404, 'not_found') });
    app = await renderApp(`/approvals?item=${ID}`);
    await settleUntil(() => alerts(panel()).length > 0);
    expect(alerts(panel())).toEqual([
      "This request doesn't exist or you can't review it.",
    ]);
    expect(buttonNamed(panel(), 'Close')).toBeTruthy();
    app.unmount();

    const api = stub();
    app = await renderApp('/approvals?item=not-an-id');
    await settleUntil(() => alerts(panel()).length > 0);
    expect(alerts(panel())[0]).toBe(
      "This request doesn't exist or you can't review it.",
    );
    expect(api.calls('GET /api/v1/approval-requests/not-an-id')).toHaveLength(
      0,
    );
  });

  it.each(APPROVAL_TYPES)(
    'renders %s: its fields and its decisions or review link',
    async (type) => {
      const item = ITEMS_BY_TYPE[type]!;
      stub({
        [LIST]: () => page([item]),
        [`GET /api/v1/approval-requests/${item.id}`]: () =>
          jsonResponse(200, item),
        [`GET /api/v1/approval-requests/${item.id}/assignee-options`]: () =>
          jsonResponse(200, { items: [ME] }),
      });
      app = await renderApp(`/approvals?item=${item.id}`);
      await settleUntil(() => panel()?.textContent?.includes('Details'));

      const dialog = panel();
      expect(dialog.textContent).not.toContain('Type: ');
      const decisions = [...dialog.querySelectorAll('button')]
        .map((button) => button.textContent)
        .filter((text) =>
          ['Approve', 'Reject', 'Mark resolved', 'Dismiss'].includes(text),
        );
      const review = [...dialog.querySelectorAll('a')].find(
        (anchor) => anchor.textContent === 'Review on its page',
      );
      if (item.allowed_actions.length > 0) {
        expect(decisions).toHaveLength(item.allowed_actions.length);
        expect(review).toBeUndefined();
      } else {
        expect(decisions).toEqual([]);
        expect(review?.getAttribute('href')).toMatch(
          /^\/(schedule|progress-reports|payroll)\//,
        );
      }
    },
  );

  it('an unknown type shows its code and the raw keys', async () => {
    const unknown = {
      ...PENDING_REGISTRATION,
      request_type: 'finance.refund',
      subject: { amount: '10.00' },
      allowed_actions: [],
    } as ApprovalRequestItem;
    stub({ [DETAIL]: () => jsonResponse(200, unknown) });
    app = await renderApp(`/approvals?item=${ID}`);
    await settleUntil(() => panel()?.textContent?.includes('Details'));

    expect(panel().textContent).toContain('Type: finance.refund');
    expect(panel().textContent).toContain('amount10.00');
  });

  it('a decided item shows the resolution and no assignment controls', async () => {
    const decided = {
      ...PENDING_REGISTRATION,
      status: 'rejected',
      assignee: LAURA,
      resolved_at: '2026-10-19T17:05:00Z',
      resolved_by: ME,
      reason: 'Unknown family.',
      allowed_actions: [],
    } as ApprovalRequestItem;
    const api = stub({ [DETAIL]: () => jsonResponse(200, decided) });
    app = await renderApp(`/approvals?item=${ID}`);
    await settleUntil(() => panel()?.textContent?.includes('Details'));

    const text = panel().textContent ?? '';
    expect(text).toContain('Assigned toLaura Méndez');
    expect(text).toContain('Resolved byDemo Owner');
    expect(text).toContain('ReasonUnknown family.');
    expect(text).not.toContain('Assign to me');
    expect(panel().querySelector('select')).toBeNull();
    expect(api.calls(OPTIONS)).toHaveLength(0);
  });

  it('a decision opens the dialog on the panel; then the panel shows the new status', async () => {
    const approved = {
      ...PENDING_REGISTRATION,
      status: 'approved',
      resolved_at: '2026-10-19T17:00:00Z',
      resolved_by: ME,
      allowed_actions: [],
      version: 2,
    } as ApprovalRequestItem;
    const api = stub({
      [LIST]: [() => page([PENDING_REGISTRATION]), () => page([])],
      [DETAIL]: [
        () => jsonResponse(200, PENDING_REGISTRATION),
        () => jsonResponse(200, approved),
      ],
      [APPROVE]: () => jsonResponse(200, approved),
    });
    app = await renderApp('/approvals');
    await openPanel();

    await click(buttonNamed(panel(), 'Approve'));
    expect(dialogs()).toHaveLength(2);
    const decision = dialogs()[1]!;
    expect(decision.contains(document.activeElement)).toBe(true);
    await click(buttonNamed(decision, 'Approve'));

    expect(api.calls(APPROVE)).toHaveLength(1);
    await settleUntil(() => dialogs().length === 1);
    expect(panel().textContent).toContain('StatusApproved');
    expect(panel().contains(document.activeElement)).toBe(true);
    expect(statusText()).toBe('Approved.');
    expect(api.calls(LIST).length).toBeGreaterThanOrEqual(2);
  });

  it('Escape in the decision dialog closes only it', async () => {
    stub();
    app = await renderApp('/approvals');
    await openPanel();
    const approve = buttonNamed(panel(), 'Approve');
    approve.focus();
    await click(approve);
    expect(dialogs()).toHaveLength(2);

    await pressEscape();

    expect(dialogs()).toHaveLength(1);
    expect(document.activeElement).toBe(approve);
  });
});

describe('assignment (C1, C2)', () => {
  it('assign to me, to someone, and unassign; each is announced', async () => {
    const mine = { ...PENDING_REGISTRATION, assignee: ME, version: 2 };
    const lauras = { ...PENDING_REGISTRATION, assignee: LAURA, version: 3 };
    const none = { ...PENDING_REGISTRATION, assignee: null, version: 4 };
    const api = stub({
      [DETAIL]: [
        () => jsonResponse(200, PENDING_REGISTRATION),
        () => jsonResponse(200, mine),
        () => jsonResponse(200, lauras),
        () => jsonResponse(200, none),
      ],
      [ASSIGN]: [
        () => jsonResponse(200, mine),
        () => jsonResponse(200, lauras),
        () => jsonResponse(200, none),
      ],
    });
    app = await renderApp('/approvals');
    await openPanel();
    const select = () => panel().querySelector('select')!;
    expect([...select().options].map((o) => o.textContent)).toEqual([
      'Choose a person',
      'Demo Owner',
      'Laura Méndez',
    ]);
    expect(select().value).toBe('');
    expect(
      [...panel().querySelectorAll('button')].some(
        (button) => button.textContent === 'Remove assignment',
      ),
    ).toBe(false);

    await click(buttonNamed(panel(), 'Assign to me'));
    expect(api.calls(ASSIGN)[0]!.body).toEqual({
      assignee_user_id: ME.id,
      version: 1,
    });
    expect(statusText()).toBe('Assigned to Demo Owner');
    expect(panel().textContent).toContain('Assigned toDemo Owner');
    // The assignee doesn't get "Assign to me"; the select shows them.
    expect(
      [...panel().querySelectorAll('button')].some(
        (button) => button.textContent === 'Assign to me',
      ),
    ).toBe(false);
    expect(select().value).toBe(ME.id);

    setFieldValue(select(), LAURA.id);
    await click(buttonNamed(panel(), 'Assign'));
    expect(api.calls(ASSIGN)[1]!.body).toEqual({
      assignee_user_id: LAURA.id,
      version: 2,
    });
    expect(statusText()).toBe('Assigned to Laura Méndez');

    await click(buttonNamed(panel(), 'Remove assignment'));
    expect(api.calls(ASSIGN)[2]!.body).toEqual({
      assignee_user_id: null,
      version: 3,
    });
    expect(statusText()).toBe('Unassigned');
    expect(panel().textContent).toContain('Assigned toUnassigned');
    // The live region stays exposed while the modal panel hides the page.
    expect(
      main().querySelector('[role="status"]')?.getAttribute('aria-live'),
    ).toBe('polite');
  });

  it.each(['version_conflict', 'approval_request_closed'])(
    '%s reloads the request and says so',
    async (code) => {
      const changed = { ...PENDING_REGISTRATION, assignee: LAURA, version: 5 };
      const api = stub({
        [DETAIL]: [
          () => jsonResponse(200, PENDING_REGISTRATION),
          () => jsonResponse(200, changed),
        ],
        [ASSIGN]: () => problem(409, code),
      });
      app = await renderApp('/approvals');
      await openPanel();

      await click(buttonNamed(panel(), 'Assign to me'));

      expect(api.calls(DETAIL)).toHaveLength(2);
      expect(alerts(panel())).toContain(
        'The request changed; it was reloaded.',
      );
      expect(panel().textContent).toContain('Assigned toLaura Méndez');
      expect(statusText()).toBe('');
    },
  );

  it('a failed reload keeps the shown data and never claims it reloaded', async () => {
    stub({
      [DETAIL]: [
        () => jsonResponse(200, PENDING_REGISTRATION),
        () => problem(400, 'bad_request'),
      ],
      [ASSIGN]: () => problem(409, 'version_conflict'),
    });
    app = await renderApp('/approvals');
    await openPanel();

    await click(buttonNamed(panel(), 'Assign to me'));

    const shown = alerts(panel());
    expect(shown.join(' ')).toContain("couldn't be reloaded");
    expect(shown.join(' ')).not.toContain('it was reloaded');
    expect(panel().textContent).toContain('Usernamerosa.diaz');
    expect(panel().textContent).toContain('Assigned toUnassigned');
  });

  it('approval_assignee_ineligible reloads the options and says so', async () => {
    const api = stub({
      [OPTIONS]: [
        () => jsonResponse(200, { items: [ME, LAURA] }),
        () => jsonResponse(200, { items: [ME] }),
      ],
      [ASSIGN]: () => problem(422, 'approval_assignee_ineligible'),
    });
    app = await renderApp('/approvals');
    await openPanel();

    setFieldValue(panel().querySelector('select')!, LAURA.id);
    await click(buttonNamed(panel(), 'Assign'));
    await settle();

    expect(api.calls(OPTIONS)).toHaveLength(2);
    expect(alerts(panel())).toContain(
      'That person can no longer review this type of request.',
    );
    expect(
      [...panel().querySelector('select')!.options].map((o) => o.textContent),
    ).toEqual(['Choose a person', 'Demo Owner']);
  });

  it('a 404 shows the not-found state', async () => {
    stub({ [ASSIGN]: () => problem(404, 'not_found') });
    app = await renderApp('/approvals');
    await openPanel();

    await click(buttonNamed(panel(), 'Assign to me'));

    expect(alerts(panel())).toEqual([
      "This request doesn't exist or you can't review it.",
    ]);
  });
});
