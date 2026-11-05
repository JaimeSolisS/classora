import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { applyLanguage } from '@/i18n';
import type { SessionUserBody } from '@/features/auth/session';
import { ACTIVE_TUTOR } from '@/features/users/admin/fixtures';
import {
  meAs,
  problem,
  stubApi,
  TEST_USER,
  type ApiStub,
  type Responder,
} from '@/test/api-stub';
import { alerts, click, labelled, setFieldValue } from '@/test/dom';
import {
  buttonNamed,
  jsonResponse,
  renderApp,
  settle,
  settleUntil,
  type RenderedApp,
} from '@/test/render-app';
import type { AuditEvent } from './api';
import {
  APPROVAL_ID,
  ASSIGNMENT,
  DELETED,
  LAURA,
  LOGIN_FAILED,
  manyEvents,
  MIXED_EVENTS,
  OWNER,
  RECORD_TYPES,
  TUTOR,
} from './fixtures';

// M8-03 (#120): the /audit page. Filters in the URL and the C2 checks (A),
// the list, paging and its states (C3, C6), row actions (C4), the
// before/after view with masked values and names (C5), read only (A3),
// access (A4) and the user detail links (D3).

const LIST = 'GET /api/v1/audit-events';
const TYPES = 'GET /api/v1/audit-events/record-types';
const UUID = '0199a000-0000-7000-8000-0000000000aa';

// The times of the fixtures in America/Mexico_City (UTC−6 in November).
const ASSIGNMENT_TIME = 'Nov 4, 2026, 12:30:05 PM';
const MASKED_TIME = 'Nov 4, 2026, 11:00:00 AM';
const LOGIN_TIME = 'Nov 1, 2026, 6:00:00 AM';

const page = (items: AuditEvent[], next: string | null = null) =>
  jsonResponse(200, { items, next_cursor: next });

let app: RenderedApp | undefined;

const main = () => app!.container.querySelector('main')!;
const liveText = () =>
  main().querySelector('p[aria-live="polite"]')?.textContent ?? '';
const listCalls = (api: ApiStub) =>
  api.fetch.mock.calls
    .map(([request]) => new URL(request.url))
    .filter((url) => url.pathname === '/api/v1/audit-events');
const lastListQuery = (api: ApiStub) =>
  Object.fromEntries(listCalls(api).at(-1)!.searchParams);

function stub(routes: Record<string, Responder | Responder[]> = {}): ApiStub {
  return stubApi({
    'GET /api/v1/auth/me': meAs(),
    [TYPES]: () => jsonResponse(200, { items: RECORD_TYPES }),
    [LIST]: () => page(MIXED_EVENTS),
    ...routes,
  });
}

async function open(path = '/audit') {
  app = await renderApp(path);
  await settleUntil(() => main()?.querySelector('h1'));
  await settle();
}

async function submitFilters() {
  const form = main().querySelector('form')!;
  await click(buttonNamed(form, 'Apply filters'));
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

describe('the list (C3)', () => {
  it('shows each event newest first with time, person, action and record', async () => {
    const api = stub();
    await open();
    await settleUntil(() => main().querySelector('tbody'));

    expect(main().querySelector('h1')!.textContent).toBe('Audit log');
    expect(document.title).toContain('Audit log');
    const rows = [...main().querySelectorAll('tbody > tr')].map(
      (row) => row.textContent ?? '',
    );
    expect(rows).toHaveLength(MIXED_EVENTS.length);
    expect(rows[0]).toContain(ASSIGNMENT_TIME);
    expect(rows[0]).toContain('Ana Owner');
    expect(rows[0]).toContain('Updated');
    expect(rows[0]).toContain('approval_requests');
    expect(rows[0]).toContain(APPROVAL_ID.slice(0, 8));
    expect(rows[3]).toContain('System');
    expect(rows[3]).toContain('Created');
    // A named event: its code, no record ("—").
    expect(rows[4]).toContain(LOGIN_TIME);
    expect(rows[4]).toContain('identity.login_failed');
    expect(rows[4]).toContain('—');
    // An actor id without a name.
    expect(rows[5]).toContain('Unknown user (0199a000)');
    expect(rows[5]).toContain('Archived');
    expect(liveText()).toBe('6 events shown');
    expect(lastListQuery(api)).toEqual({ limit: '25' });
  });

  it('loads more with the cursor and hides the button on the last page', async () => {
    const api = stub({
      [LIST]: [
        () => page(manyEvents(25), 'cursor-2'),
        () => page(manyEvents(3, 25)),
      ],
    });
    await open();
    await settleUntil(
      () => main().querySelectorAll('tbody > tr').length === 25,
    );
    expect(liveText()).toBe('25 events shown');

    await click(buttonNamed(main(), 'Load more'));
    await settleUntil(
      () => main().querySelectorAll('tbody > tr').length === 28,
    );

    expect(lastListQuery(api)).toEqual({ limit: '25', cursor: 'cursor-2' });
    expect(liveText()).toBe('28 events shown');
    expect(() => buttonNamed(main(), 'Load more')).toThrow();
  });

  it('starts again from the first page when a filter changes', async () => {
    const api = stub({
      [LIST]: [() => page(manyEvents(25), 'cursor-2'), () => page([])],
    });
    await open();
    await settleUntil(() => main().querySelector('tbody'));

    setFieldValue(labelled(main(), 'From'), '2026-11-01');
    await submitFilters();
    await settleUntil(() => main().textContent?.includes('No events match'));

    const query = lastListQuery(api);
    expect(query.cursor).toBeUndefined();
    expect(app!.location()).toBe('/audit?from=2026-11-01');
  });
});

describe('the filters (C1, C2, E5)', () => {
  it('reads every filter from the URL and sends center-local day bounds', async () => {
    const api = stub();
    await open(
      `/audit?record_type=users&record_id=${UUID}&actor_id=${OWNER.id}&from=2026-11-01&to=2026-11-04`,
    );
    await settleUntil(() => main().querySelector('tbody'));

    expect(lastListQuery(api)).toEqual({
      record_type: 'users',
      record_id: UUID,
      actor_id: OWNER.id,
      // 00:00 in Mexico City (UTC−6), and the start of the day after "To".
      occurred_from: '2026-11-01T06:00:00.000Z',
      occurred_to: '2026-11-05T06:00:00.000Z',
      limit: '25',
    });
    expect(labelled<HTMLSelectElement>(main(), 'Record type').value).toBe(
      'users',
    );
    expect(labelled<HTMLInputElement>(main(), 'Record ID').value).toBe(UUID);
    expect(labelled<HTMLInputElement>(main(), 'From').value).toBe('2026-11-01');
    expect(labelled<HTMLInputElement>(main(), 'To').value).toBe('2026-11-04');
    // The person filter is a chip with the name the list gives.
    expect(main().textContent).toContain('Ana Owner');
    buttonNamed(main(), 'Remove the person filter: Ana Owner');
    buttonNamed(main(), 'Clear filters');
  });

  it('applies the form to the URL and offers every record type', async () => {
    const api = stub();
    await open();
    await settleUntil(() => main().querySelector('tbody'));
    expect(() => buttonNamed(main(), 'Clear filters')).toThrow();
    const options = [
      ...labelled<HTMLSelectElement>(main(), 'Record type').options,
    ].map((option) => option.value);
    expect(options).toEqual(['', ...RECORD_TYPES]);

    setFieldValue(labelled(main(), 'Record type'), 'compensation_rates');
    setFieldValue(labelled(main(), 'Record ID'), UUID);
    await submitFilters();
    await settle();

    expect(app!.location()).toBe(
      `/audit?record_type=compensation_rates&record_id=${UUID}`,
    );
    expect(lastListQuery(api)).toMatchObject({
      record_type: 'compensation_rates',
      record_id: UUID,
    });

    await click(buttonNamed(main(), 'Clear filters'));
    expect(app!.location()).toBe('/audit');
  });

  it.each([
    [
      'a type without an ID',
      { 'Record type': 'users' },
      'Enter both the record type and the record ID.',
    ],
    [
      'an ID without a type',
      { 'Record ID': UUID },
      'Enter both the record type and the record ID.',
    ],
    [
      'an ID that is not a UUID',
      { 'Record type': 'users', 'Record ID': 'abc' },
      'The ID is not valid.',
    ],
    [
      'To before From',
      { From: '2026-11-04', To: '2026-11-01' },
      'The end date must be on or after the start date.',
    ],
  ] as const)('%s: no request, a message', async (_case, fields, message) => {
    const api = stub();
    await open();
    await settleUntil(() => main().querySelector('tbody'));
    const before = listCalls(api).length;

    for (const [label, value] of Object.entries(fields)) {
      setFieldValue(labelled(main(), label), value);
    }
    await submitFilters();
    await settle();

    expect(main().textContent).toContain(message);
    expect(listCalls(api)).toHaveLength(before);
    expect(app!.location()).toBe('/audit');
    expect(main().querySelector('[aria-invalid="true"]')).not.toBeNull();
    expect(document.activeElement?.getAttribute('aria-invalid')).toBe('true');
  });

  it('sends no request for an invalid URL', async () => {
    const api = stub();
    await open('/audit?record_type=users');

    expect(main().textContent).toContain(
      'Enter both the record type and the record ID.',
    );
    expect(main().textContent).toContain('Fix the filters to see the events.');
    expect(listCalls(api)).toHaveLength(0);
  });

  it('sends an unknown record type as is (an empty list)', async () => {
    const api = stub({ [LIST]: () => page([]) });
    await open(`/audit?record_type=no_such_table&record_id=${UUID}`);
    await settleUntil(() => main().textContent?.includes('No events match'));

    expect(lastListQuery(api)).toMatchObject({
      record_type: 'no_such_table',
      record_id: UUID,
    });
    expect(labelled<HTMLSelectElement>(main(), 'Record type').value).toBe(
      'no_such_table',
    );
  });
});

describe('row actions (C4)', () => {
  it("filters by the row's record, then by its person", async () => {
    const api = stub();
    await open('/audit?from=2026-11-01');
    await settleUntil(() => main().querySelector('tbody'));

    await click(
      buttonNamed(
        main(),
        `View this record's history, event at ${ASSIGNMENT_TIME}`,
      ),
    );
    await settle();
    expect(app!.location()).toBe(
      `/audit?record_type=approval_requests&record_id=${APPROVAL_ID}`,
    );
    expect(document.activeElement).toBe(main().querySelector('h1'));

    await click(
      buttonNamed(
        main(),
        `View this person's actions, event at ${MASKED_TIME}`,
      ),
    );
    await settle();
    expect(app!.location()).toBe(`/audit?actor_id=${OWNER.id}`);
    expect(lastListQuery(api)).toEqual({ actor_id: OWNER.id, limit: '25' });

    await click(buttonNamed(main(), 'Remove the person filter: Ana Owner'));
    await settle();
    expect(app!.location()).toBe('/audit');
  });

  it('hides the history and person buttons when there is none', async () => {
    stub({ [LIST]: () => page([LOGIN_FAILED]) });
    await open();
    await settleUntil(() => main().querySelector('tbody'));

    const names = [...main().querySelectorAll('tbody button')].map((button) =>
      button.getAttribute('aria-label'),
    );
    expect(names).toEqual([`View changes of the event at ${LOGIN_TIME}`]);
  });
});

describe('the before/after view (C5)', () => {
  async function expand(time: string) {
    const button = buttonNamed(main(), `View changes of the event at ${time}`);
    expect(button.getAttribute('aria-expanded')).toBe('false');
    await click(button);
    expect(button.getAttribute('aria-expanded')).toBe('true');
    const region = document.getElementById(
      button.getAttribute('aria-controls')!,
    )!;
    expect(region).not.toBeNull();
    return region;
  }

  const cells = (region: HTMLElement) =>
    [...region.querySelectorAll(':scope tbody tr')].map((row) =>
      [...row.children].map((cell) => cell.textContent ?? ''),
    );

  it('names user ids, keeps the id as secondary text, shows the request ID', async () => {
    stub();
    await open();
    await settleUntil(() => main().querySelector('tbody'));

    const region = await expand(ASSIGNMENT_TIME);

    expect(cells(region)).toEqual([
      [
        'assignee_user_id',
        `${LAURA.display_name}${LAURA.id}`,
        `${TUTOR.display_name}${TUTOR.id}`,
      ],
    ]);
    expect(region.textContent).toContain('Request ID: req-assign-1');
  });

  it('shows a masked value as a Hidden badge, never as a value; a create has no Before', async () => {
    stub();
    await open();
    await settleUntil(() => main().querySelector('tbody'));

    const region = await expand(MASKED_TIME);

    expect(region.textContent).not.toContain('[masked]');
    expect(cells(region)).toEqual([
      ['rate', '', 'HiddenValue hidden in the audit log'],
      ['rate_model', '', 'per_hour'],
      ['tutor_user_id', '', `${TUTOR.display_name}${TUTOR.id}`],
    ]);
    const badge = region.querySelector(':scope tbody td .sr-only')!;
    expect(badge.textContent).toBe('Value hidden in the audit log');
  });

  it('leaves After empty on a delete and pretty-prints nested JSON', async () => {
    stub({ [LIST]: () => page([DELETED]) });
    await open();
    await settleUntil(() => main().querySelector('tbody'));

    const region = await expand('Nov 3, 2026, 9:00:00 AM');

    expect(cells(region)).toEqual([
      ['label', 'Mañanas', ''],
      ['windows', JSON.stringify(DELETED.changes!.windows!.old, null, 2), ''],
    ]);
    expect(region.querySelector('pre')).not.toBeNull();
  });

  it("lists a named event's details and an id without a name as text", async () => {
    const unnamed: AuditEvent = {
      ...ASSIGNMENT,
      accounts: [],
      changes: { assignee_user_id: { old: null, new: LAURA.id } },
      request_id: null,
    };
    stub({ [LIST]: () => page([LOGIN_FAILED, unnamed]) });
    await open();
    await settleUntil(() => main().querySelector('tbody'));

    const details = await expand(LOGIN_TIME);
    expect(cells(details)).toEqual([
      ['attempts', '3'],
      ['reason', 'invalid_credentials'],
    ]);
    expect(details.textContent).not.toContain('Request ID');

    const update = await expand(ASSIGNMENT_TIME);
    expect(cells(update)).toEqual([['assignee_user_id', '—', LAURA.id]]);
  });
});

describe('states (C6)', () => {
  it('says when nothing matches', async () => {
    stub({ [LIST]: () => page([]) });
    await open();
    await settleUntil(() => main().textContent?.includes('No events match'));

    expect(main().textContent).toContain('No events match these filters.');
    expect(liveText()).toBe('0 events shown');
  });

  it('shows the shared alert with a retry on an error', async () => {
    const api = stub({
      [LIST]: [() => problem(500, 'internal_error'), () => page([ASSIGNMENT])],
    });
    await open();
    await settleUntil(() => alerts(main()).length > 0);

    await click(buttonNamed(main(), 'Try again'));
    await settleUntil(() => main().querySelector('tbody'));
    expect(listCalls(api)).toHaveLength(2);
  });

  it('keeps the rows when "Load more" fails', async () => {
    stub({
      [LIST]: [
        () => page(manyEvents(25), 'cursor-2'),
        () => problem(500, 'internal_error'),
      ],
    });
    await open();
    await settleUntil(() => main().querySelector('tbody'));

    await click(buttonNamed(main(), 'Load more'));
    await settleUntil(() => alerts(main()).length > 0);

    expect(main().querySelectorAll('tbody > tr')).toHaveLength(25);
  });

  it('restarts from page 1 when the cursor is no longer valid', async () => {
    const api = stub({
      [LIST]: [
        () => page(manyEvents(25), 'cursor-2'),
        () => problem(400, 'invalid_cursor'),
        () => page([ASSIGNMENT]),
      ],
    });
    await open();
    await settleUntil(() => main().querySelector('tbody'));

    await click(buttonNamed(main(), 'Load more'));
    await settleUntil(() =>
      alerts(main()).some((text) =>
        text.includes('The list changed; load it again.'),
      ),
    );
    expect(() => buttonNamed(main(), 'Load more')).toThrow();

    await click(buttonNamed(main(), 'Load again'));
    await settleUntil(() => main().querySelectorAll('tbody > tr').length === 1);
    expect(lastListQuery(api)).toEqual({ limit: '25' });
  });
});

describe('read only (A3)', () => {
  it('sends only GET requests and has no write control', async () => {
    const api = stub();
    await open();
    await settleUntil(() => main().querySelector('tbody'));
    await click(
      buttonNamed(main(), `View changes of the event at ${MASKED_TIME}`),
    );
    await click(
      buttonNamed(
        main(),
        `View this record's history, event at ${ASSIGNMENT_TIME}`,
      ),
    );
    await settle();

    const audit = api.requests.filter((request) =>
      request.path.startsWith('/api/v1/audit-events'),
    );
    expect(audit.length).toBeGreaterThan(1);
    expect(audit.every((request) => request.method === 'GET')).toBe(true);
    // The only other request the app may send is its own preferences sync.
    expect(
      api.requests.filter(
        (request) =>
          request.method !== 'GET' &&
          !request.path.startsWith('/api/v1/auth/me'),
      ),
    ).toEqual([]);
    const labels = [...main().querySelectorAll('button')].map(
      (button) => button.textContent ?? '',
    );
    for (const word of ['Edit', 'Delete', 'Export', 'Save']) {
      expect(labels.some((label) => label.includes(word))).toBe(false);
    }
  });
});

describe('access (A4) and the user detail links (D3)', () => {
  const STAFF: SessionUserBody = { ...TEST_USER, permissions: ['user.view'] };

  it('shows the Forbidden page and the nav without the item for a non-holder', async () => {
    const api = stubApi({ 'GET /api/v1/auth/me': meAs(STAFF) });
    app = await renderApp('/audit');
    await settleUntil(() =>
      main()?.textContent?.includes("You don't have access to this page"),
    );

    expect(listCalls(api)).toHaveLength(0);
    expect(app.container.querySelector('a[href="/audit"]')).toBeNull();
  });

  it('shows the nav item to an audit.view holder', async () => {
    stub();
    await open();

    expect(app!.container.querySelector('a[href="/audit"]')?.textContent).toBe(
      'Audit log',
    );
  });

  it.each([
    ['an audit.view holder', TEST_USER, true],
    ['a user.view holder without audit.view', STAFF, false],
  ] as const)(
    'the user detail page links to the log for %s',
    async (_who, me, shown) => {
      stubApi({
        'GET /api/v1/auth/me': meAs(me),
        [`GET /api/v1/users/${ACTIVE_TUTOR.id}`]: () =>
          jsonResponse(200, ACTIVE_TUTOR),
      });
      app = await renderApp(`/users/${ACTIVE_TUTOR.id}`);
      await settleUntil(() => main()?.querySelector('h1')?.textContent);

      const changes = main().querySelector(
        `a[href="/audit?record_type=users&record_id=${ACTIVE_TUTOR.id}"]`,
      );
      const actions = main().querySelector(
        `a[href="/audit?actor_id=${ACTIVE_TUTOR.id}"]`,
      );
      if (shown) {
        expect(changes?.textContent).toBe('Change history of this account');
        expect(actions?.textContent).toBe("This person's actions");
      } else {
        expect(changes).toBeNull();
        expect(actions).toBeNull();
      }
    },
  );
});
