import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { SessionUserBody } from '@/features/auth/session';
import { applyLanguage } from '@/i18n';
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
  settleUntil,
  type RenderedApp,
} from '@/test/render-app';
import type { BillingSettings } from '../settings/api';
import { SETTINGS_OFF, SETTINGS_ON } from '../settings/fixtures';
import type { UnbilledPage } from './api';
import {
  EMPTY_UNBILLED_PAGE,
  ITEM_NO_FAMILY,
  ITEM_NO_PRICE,
  UNBILLED_PAGE,
} from './fixtures';

// #366 E1 (A, C, D): the Unbilled tab behind billing.view: the rows (date
// and time in the center's zone, length, family or "No family", both
// reasons), the reason filter in the URL and the request, the pagination,
// the fix links per permission, both empty states (billing off with the
// Settings link; on with nothing unbilled), the 15-minute note and a
// failed load with retry.

const LIST = 'GET /api/v1/billing/unbilled-items';
const SETTINGS = 'GET /api/v1/billing/settings';
const ME = 'GET /api/v1/auth/me';

const user = (
  roles: SessionUserBody['roles'],
  permissions: SessionUserBody['permissions'],
): SessionUserBody => ({ ...TEST_USER, roles, permissions });

const FINANCE = user(
  ['finance_staff'],
  ['student.view', 'guardian.view', 'billing.view', 'billing.manage'],
);
const VIEWER = user(['finance_staff'], ['billing.view']);

/** The URLs the list was read with. */
let listUrls: URL[] = [];

function stub(
  routes: Record<string, Responder | Responder[]> = {},
  {
    settings = SETTINGS_ON,
    page = UNBILLED_PAGE,
    as = FINANCE,
  }: {
    settings?: BillingSettings;
    page?: UnbilledPage;
    as?: SessionUserBody;
  } = {},
): ApiStub {
  return stubApi({
    [ME]: meAs(as),
    [SETTINGS]: () => jsonResponse(200, settings),
    [LIST]: (request) => {
      listUrls.push(new URL(request.url));
      return jsonResponse(200, page);
    },
    ...routes,
  });
}

let app: RenderedApp | undefined;

const main = () => app!.container.querySelector('main')!;
const h1 = () => main().querySelector('h1')?.textContent;
const rowOf = (id: string) =>
  main().querySelector<HTMLTableRowElement>(
    `[data-unbilled-session-id="${id}"]`,
  )!;
const cells = (row: Element) =>
  [...row.querySelectorAll('td')].map((cell) => cell.textContent);
const linksIn = (row: Element) =>
  [...row.querySelectorAll('a')].map((a) => [
    a.textContent,
    a.getAttribute('href'),
    a.getAttribute('aria-label'),
  ]);
const reasonFilter = () => labelled<HTMLSelectElement>(main(), 'Reason');
const lastQuery = () => listUrls.at(-1)!.searchParams;

async function renderAt(path = '/billing/unbilled') {
  app = await renderApp(path);
  await settleUntil(() =>
    main().querySelector('table, [data-unbilled-empty], [role="alert"]'),
  );
}

beforeEach(() => {
  applyLanguage('en');
  listUrls = [];
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(new Date('2026-10-20T18:00:00Z'));
  vi.spyOn(console, 'error').mockImplementation(() => {});
});

afterEach(() => {
  app?.unmount();
  app = undefined;
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('the unbilled list (C1, C5)', () => {
  it('shows each row with its reason, under the tabs', async () => {
    stub();
    await renderAt();

    expect(h1()).toBe('Unbilled sessions');
    expect(document.title).toBe('Unbilled sessions · Classora');
    const tabs = [
      ...main().querySelectorAll<HTMLAnchorElement>(
        'nav[aria-label="Billing sections"] a',
      ),
    ].map((a) => [a.textContent, a.getAttribute('aria-current')]);
    expect(tabs).toEqual([
      ['Prices', null],
      ['Discounts', null],
      ['Custom prices', null],
      ['Unbilled', 'page'],
      ['Settings', null],
    ]);
    expect(main().textContent).toContain(
      'New confirmations are billed within about 15 minutes.',
    );
    expect(main().querySelector('table')?.getAttribute('aria-labelledby')).toBe(
      main().querySelector('h1')?.id,
    );
    // 16:00Z is 10:00 in Mexico City; 23:30Z is 17:30 the same day there.
    expect(cells(rowOf(ITEM_NO_FAMILY.class_session_id)).slice(0, 6)).toEqual([
      'Oct 5, 2026, 10:00 AM',
      'Ana López',
      'Álgebra',
      '60 min',
      'No family',
      'Not in a family',
    ]);
    expect(cells(rowOf(ITEM_NO_PRICE.class_session_id)).slice(0, 6)).toEqual([
      'Oct 6, 2026, 5:30 PM',
      'Bruno Díaz',
      'Física',
      '45 min',
      'Familia Díaz',
      'No price for this class',
    ]);
    expect(lastQuery().get('limit')).toBe('25');
    expect(lastQuery().get('offset')).toBe('0');
    expect(lastQuery().has('reason')).toBe(false);
  });

  it('gives fix links to Finance (C3)', async () => {
    stub();
    await renderAt();

    expect(linksIn(rowOf(ITEM_NO_FAMILY.class_session_id))).toEqual([
      ['Open families', '/families', 'Open families to add Ana López to one'],
    ]);
    expect(linksIn(rowOf(ITEM_NO_PRICE.class_session_id))).toEqual([
      ['Open prices', '/billing/prices', 'Open prices to add one for Física'],
    ]);
  });

  it('leaves out the links the user can’t use (C3)', async () => {
    stub({}, { as: VIEWER });
    await renderAt();

    expect(linksIn(rowOf(ITEM_NO_FAMILY.class_session_id))).toEqual([]);
    expect(linksIn(rowOf(ITEM_NO_PRICE.class_session_id))).toEqual([]);
  });

  it('pages through the list (C1)', async () => {
    stub({}, { page: { ...UNBILLED_PAGE, total: 30, limit: 25, offset: 0 } });
    await renderAt();

    await click(buttonNamed(main(), 'Next'));
    await settleUntil(() => lastQuery().get('offset') === '25');

    expect(app!.location()).toBe('/billing/unbilled?page=2');
    expect(lastQuery().get('limit')).toBe('25');
  });
});

describe('the reason filter (C2)', () => {
  it.each(['no_billing_account', 'no_price'] as const)(
    'sends reason=%s',
    async (reason) => {
      stub();
      await renderAt('/billing/unbilled?page=2');

      expect(
        [...reasonFilter().options].map((option) => option.textContent),
      ).toEqual(['All', 'Not in a family', 'No price for this class']);
      setFieldValue(reasonFilter(), reason);
      await settleUntil(() => lastQuery().get('reason') === reason);

      expect(lastQuery().getAll('reason')).toEqual([reason]);
      expect(lastQuery().get('offset')).toBe('0');
      expect(app!.location()).toBe(`/billing/unbilled?reason=${reason}`);
    },
  );

  it('reads the reason from the URL and ignores an unknown one', async () => {
    stub();
    await renderAt('/billing/unbilled?reason=no_price');
    expect(reasonFilter().value).toBe('no_price');
    expect(lastQuery().getAll('reason')).toEqual(['no_price']);
    app!.unmount();

    await renderAt('/billing/unbilled?reason=bogus');
    expect(reasonFilter().value).toBe('');
    expect(lastQuery().has('reason')).toBe(false);
  });

  it('says when no session has the chosen reason', async () => {
    stub({}, { page: EMPTY_UNBILLED_PAGE });
    await renderAt('/billing/unbilled?reason=no_price');

    expect(
      main().querySelector('[data-unbilled-empty="filtered"]')?.textContent,
    ).toBe('No unbilled sessions with this reason.');
    expect(main().querySelector('table')).toBeNull();
  });
});

describe('the empty states (C4)', () => {
  it('billing off: says nothing is charged and links to Settings', async () => {
    stub({}, { settings: SETTINGS_OFF, page: EMPTY_UNBILLED_PAGE });
    await renderAt();

    const empty = main().querySelector('[data-unbilled-empty="off"]')!;
    expect(empty.textContent).toContain(
      'Billing is off; nothing is charged yet.',
    );
    const link = empty.querySelector('a');
    expect(link?.textContent).toBe('Open billing settings');
    expect(link?.getAttribute('href')).toBe('/billing/settings');
    expect(main().querySelector('table')).toBeNull();
  });

  it('billing on and nothing unbilled: everything since the date is billed', async () => {
    stub({}, { page: EMPTY_UNBILLED_PAGE });
    await renderAt();

    expect(
      main().querySelector('[data-unbilled-empty="on"]')?.textContent,
    ).toBe('Every confirmed session since Sep 1, 2026 is billed.');
  });
});

describe('errors (C4)', () => {
  it('shows a failed list with Try again, which reloads', async () => {
    const api = stub({
      [LIST]: [
        // A 4xx is never retried by the query client.
        () => problem(400, 'bad_request'),
        (request) => {
          listUrls.push(new URL(request.url));
          return jsonResponse(200, UNBILLED_PAGE);
        },
      ],
    });
    await renderAt();

    expect(alerts(main())).toHaveLength(1);
    await click(buttonNamed(main(), 'Try again'));
    await settleUntil(() => main().querySelector('table'));

    expect(api.calls(LIST)).toHaveLength(2);
    expect(api.calls(SETTINGS)).toHaveLength(1);
  });

  it('shows a failed settings read with Try again', async () => {
    const api = stub({
      [SETTINGS]: [
        // A 4xx is never retried by the query client.
        () => problem(400, 'bad_request'),
        () => jsonResponse(200, SETTINGS_ON),
      ],
    });
    await renderAt();

    expect(alerts(main())).toHaveLength(1);
    await click(buttonNamed(main(), 'Try again'));
    await settleUntil(() => main().querySelector('table'));

    expect(api.calls(SETTINGS)).toHaveLength(2);
  });
});

describe('Spanish (D1)', () => {
  it('names the columns and the reasons in Spanish', async () => {
    applyLanguage('es');
    stub();
    await renderAt();

    expect(h1()).toBe('Sesiones sin cobrar');
    expect(cells(rowOf(ITEM_NO_FAMILY.class_session_id)).slice(0, 6)).toEqual([
      '5 oct 2026, 10:00',
      'Ana López',
      'Álgebra',
      '60 min',
      'Sin familia',
      'No pertenece a una familia',
    ]);
  });
});
