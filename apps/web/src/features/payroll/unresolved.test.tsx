import { act } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { SessionUserBody } from '@/features/auth/session';
import { applyLanguage } from '@/i18n';
import es from '@/i18n/locales/es/common.json';
import {
  meAs,
  problem,
  stubApi,
  TEST_USER,
  type ApiStub,
  type Responder,
} from '@/test/api-stub';
import {
  alerts,
  click,
  comboboxOption,
  comboboxOptions,
  labelled,
  setFieldValue,
} from '@/test/dom';
import {
  buttonNamed,
  jsonResponse,
  renderApp,
  settle,
  settleUntil,
  type RenderedApp,
} from '@/test/render-app';
import type { PayrollSettings, UnresolvedItem } from './api';
import {
  SETTINGS_OFF,
  SETTINGS_ON,
  TUTOR_ID,
  TUTOR_HOURLY,
  TUTORS_PAGE,
  UNRESOLVED_BEFORE_FIRST,
  UNRESOLVED_NO_RATE,
  UNRESOLVED_NO_TERMS,
} from './fixtures';
import { parseUnresolvedFilters } from './unresolvedUrl';

// #444 C: the table and the cards, each reason with its fix, the filters in
// the URL (two quick picks, back and forward), "Load more", the empty, off and
// error states, and no amount anywhere (C5).

const ME = 'GET /api/v1/auth/me';
const LIST = 'GET /api/v1/payroll/unresolved-work';
const SETTINGS = 'GET /api/v1/payroll/settings';
const TUTORS = 'GET /api/v1/payroll/tutors';

const user = (
  permissions: SessionUserBody['permissions'],
): SessionUserBody => ({
  ...TEST_USER,
  roles: ['finance_staff'],
  permissions,
});
const FINANCE = user(['payroll.view', 'payroll.manage', 'schedule.view']);
const NO_SCHEDULE = user(['payroll.view']);

const page = (items: UnresolvedItem[], next_cursor: string | null = null) => ({
  items,
  next_cursor,
});

let urls: URL[] = [];

function stub(
  routes: Record<string, Responder | Responder[]> = {},
  {
    as = FINANCE,
    items = [UNRESOLVED_NO_RATE, UNRESOLVED_NO_TERMS, UNRESOLVED_BEFORE_FIRST],
    settings = SETTINGS_ON,
  }: {
    as?: SessionUserBody;
    items?: UnresolvedItem[];
    settings?: PayrollSettings;
  } = {},
): ApiStub {
  return stubApi({
    [ME]: meAs(as),
    [LIST]: (request) => {
      urls.push(new URL(request.url));
      return jsonResponse(200, page(items));
    },
    [SETTINGS]: () => jsonResponse(200, settings),
    [TUTORS]: () => jsonResponse(200, TUTORS_PAGE),
    ...routes,
  });
}

let app: RenderedApp | undefined;
const main = () => app!.container.querySelector('main')!;
const row = (item: UnresolvedItem) =>
  main().querySelector<HTMLElement>(
    `[data-unresolved-id="${item.class_session_id}"]`,
  )!;
const card = (item: UnresolvedItem) =>
  main().querySelector<HTMLElement>(
    `[data-unresolved-card="${item.class_session_id}"]`,
  )!;
const links = (root: HTMLElement) =>
  [...root.querySelectorAll('a')].map((link) => link.getAttribute('href'));
const reason = () => labelled<HTMLSelectElement>(main(), 'Reason');
const lastUrl = () => urls.at(-1)!.searchParams;

async function open(path = '/payroll/unresolved') {
  app = await renderApp(path);
  await settleUntil(() => main().querySelector('h1'));
  await settle();
}

async function openWithRows(path?: string) {
  await open(path);
  await settleUntil(() => main().querySelector('[data-unresolved-id]'));
}

beforeEach(() => {
  urls = [];
  applyLanguage('en');
});

afterEach(() => {
  app?.unmount();
  app = undefined;
  vi.unstubAllGlobals();
});

describe('the table (C1, C2, C3, C5)', () => {
  it('shows date, tutor, kind, source, reason and whether it blocks', async () => {
    stub();
    await openWithRows();
    const text = row(UNRESOLVED_NO_TERMS).textContent;
    expect(text).toContain('Ana Tutora');
    expect(text).toContain('Group');
    expect(text).toContain('Cancellation');
    expect(text).toContain('Has no pay terms on that date');
    expect(text).toContain('Blocks payroll');
    expect(row(UNRESOLVED_BEFORE_FIRST).textContent).toContain("Doesn't block");
  });

  it.each([
    [
      'es',
      [
        'No tiene tarifa para este tipo de clase',
        'No tiene condiciones de pago en esa fecha',
        'Antes de su primer periodo: no bloquea; se paga con un concepto único',
      ],
    ],
    [
      'en',
      [
        'Has no rate for this class type',
        'Has no pay terms on that date',
        "Before their first period: doesn't block; it's paid with a one-time item",
      ],
    ],
  ])('words each reason in %s', async (language, words) => {
    applyLanguage(language as 'en' | 'es');
    stub();
    await openWithRows();
    expect(row(UNRESOLVED_NO_RATE).textContent).toContain(words[0]);
    expect(row(UNRESOLVED_NO_TERMS).textContent).toContain(words[1]);
    expect(row(UNRESOLVED_BEFORE_FIRST).textContent).toContain(words[2]);
  });

  it('links the two fixable reasons to the tutor, and the third to the items', async () => {
    stub();
    await openWithRows();
    const pay = `/payroll/tutors/${TUTOR_ID}`;
    expect(links(row(UNRESOLVED_NO_RATE))).toContain(pay);
    expect(links(row(UNRESOLVED_NO_TERMS))).toContain(pay);
    expect(links(row(UNRESOLVED_BEFORE_FIRST))).toContain(`${pay}#pay-items`);
    expect(links(row(UNRESOLVED_BEFORE_FIRST))).not.toContain(pay);
  });

  it('names the rule and says the work stays listed (#454 C1)', async () => {
    stub();
    await openWithRows();
    const text = row(UNRESOLVED_BEFORE_FIRST).textContent;
    expect(text).toContain('what the line would have paid');
    expect(text).toContain('stays on this list');
  });

  it('says Configuración, never Ajustes, for the settings link (#454 C2)', () => {
    expect(es.payroll.unresolved.payrollOffLink).toBe('Ir a Configuración');
  });

  it('links the session with schedule.view', async () => {
    stub();
    await openWithRows();
    expect(links(row(UNRESOLVED_BEFORE_FIRST))).toEqual([
      `/payroll/tutors/${TUTOR_ID}#pay-items`,
      `/schedule/sessions/${UNRESOLVED_BEFORE_FIRST.class_session_id}`,
    ]);
  });

  it('has no session link without schedule.view', async () => {
    stub({}, { as: NO_SCHEDULE });
    await openWithRows();
    expect(links(row(UNRESOLVED_BEFORE_FIRST))).toEqual([
      `/payroll/tutors/${TUTOR_ID}#pay-items`,
    ]);
    expect(main().querySelector('[href^="/schedule/"]')).toBeNull();
    expect(main().textContent).not.toContain('Session');
  });

  it('names the table in a caption, and shows the same items as cards', async () => {
    stub();
    await openWithRows();
    expect(main().querySelector('caption')?.textContent).toBe(
      "Approved work that can't be paid",
    );
    expect(card(UNRESOLVED_NO_TERMS).textContent).toContain(
      'Has no pay terms on that date',
    );
    expect(links(card(UNRESOLVED_NO_TERMS))).toEqual([
      `/payroll/tutors/${TUTOR_ID}`,
      `/schedule/sessions/${UNRESOLVED_NO_TERMS.class_session_id}`,
    ]);
  });

  it('shows no amount (C5)', async () => {
    stub();
    await openWithRows();
    expect(main().textContent).not.toMatch(/\$|MXN|\d+\.\d{2}/);
  });
});

describe('filters in the URL (C1)', () => {
  it('sends the URL filters to the API', async () => {
    stub();
    await open(`/payroll/unresolved?tutor=${TUTOR_ID}&reason=no_rate`);
    await settleUntil(() => urls.length > 0);
    expect(lastUrl().get('tutor_user_id')).toBe(TUTOR_ID);
    expect(lastUrl().get('reason')).toBe('no_rate');
    expect(reason().value).toBe('no_rate');
  });

  it('ignores a bad tutor and a bad reason instead of a 422', async () => {
    stub();
    await open('/payroll/unresolved?tutor=not-a-uuid&reason=nope');
    await settleUntil(() => urls.length > 0);
    expect(lastUrl().get('tutor_user_id')).toBeNull();
    expect(lastUrl().get('reason')).toBeNull();
  });

  it('a reason writes the URL', async () => {
    stub();
    await openWithRows();
    setFieldValue(reason(), 'no_pay_terms');
    await settleUntil(
      () => app!.location() === '/payroll/unresolved?reason=no_pay_terms',
    );
    await settleUntil(() => lastUrl().get('reason') === 'no_pay_terms');
  });

  it('two different filters with no commit between them both stay (QA #425 D6)', async () => {
    stub();
    await openWithRows(`/payroll/unresolved?tutor=${TUTOR_ID}`);
    const chip = main().querySelector<HTMLButtonElement>(
      'button[aria-label^="Remove filter"]',
    )!;
    act(() => {
      chip.click();
      setFieldValue(reason(), 'no_pay_terms');
    });
    await settleUntil(
      () => app!.location() === '/payroll/unresolved?reason=no_pay_terms',
    );
    await settle();
    expect(app!.location()).toBe('/payroll/unresolved?reason=no_pay_terms');
  });

  it('the picker lists the Payroll tutors on a click and sets the tutor (#548)', async () => {
    const api = stub();
    await openWithRows();
    const field = labelled<HTMLInputElement>(main(), 'Tutor');
    act(() => field.focus());
    await click(field);
    await settleUntil(() => comboboxOptions().length === 3);
    expect(api.calls(TUTORS)).toHaveLength(1);
    // Local filtering (3 of 3 loaded): no request while typing.
    setFieldValue(field, 'tutora');
    await settleUntil(() => comboboxOptions().length === 1);
    expect(api.calls(TUTORS)).toHaveLength(1);
    await click(comboboxOption(TUTOR_HOURLY.display_name)!);
    await settleUntil(
      () =>
        app!.location() === `/payroll/unresolved?tutor=${TUTOR_HOURLY.user_id}`,
    );
    expect(main().textContent).toContain(`Tutor: ${TUTOR_HOURLY.display_name}`);
  });

  it('the picker reaches every state, not only active tutors', async () => {
    let search = new URL('http://x');
    stub({
      [TUTORS]: (request) => {
        search = new URL(request.url);
        return jsonResponse(200, TUTORS_PAGE);
      },
    });
    await openWithRows();
    const field = labelled<HTMLInputElement>(main(), 'Tutor');
    act(() => field.focus());
    await click(field);
    await settleUntil(() => comboboxOptions().length > 0);
    expect(Object.fromEntries(search.searchParams)).toEqual({ limit: '100' });
  });

  it('the chip and "Clear filters" remove the filters', async () => {
    stub();
    await openWithRows(`/payroll/unresolved?tutor=${TUTOR_ID}&reason=no_rate`);
    await click(buttonNamed(main(), 'Clear filters'));
    await settleUntil(() => app!.location() === '/payroll/unresolved');
  });

  it('back replaces what the page would write next', async () => {
    stub();
    await openWithRows();
    setFieldValue(reason(), 'no_rate');
    await settleUntil(
      () => app!.location() === '/payroll/unresolved?reason=no_rate',
    );
    await act(async () => {
      await app!.router.navigate(-1);
    });
    await settleUntil(() => app!.location() === '/payroll/unresolved');
    expect(reason().value).toBe('');
    setFieldValue(reason(), 'no_pay_terms');
    await settleUntil(
      () => app!.location() === '/payroll/unresolved?reason=no_pay_terms',
    );
  });

  it('after back, another filter keeps what back restored', async () => {
    stub();
    await openWithRows(`/payroll/unresolved?tutor=${TUTOR_ID}`);
    await click(
      main().querySelector<HTMLButtonElement>(
        'button[aria-label^="Remove filter"]',
      )!,
    );
    await settleUntil(() => app!.location() === '/payroll/unresolved');
    await act(async () => {
      await app!.router.navigate(-1);
    });
    await settleUntil(
      () => app!.location() === `/payroll/unresolved?tutor=${TUTOR_ID}`,
    );
    setFieldValue(reason(), 'no_rate');
    await settleUntil(
      () =>
        app!.location() ===
        `/payroll/unresolved?tutor=${TUTOR_ID}&reason=no_rate`,
    );
  });

  it('parses only what the API takes', () => {
    expect(
      parseUnresolvedFilters(
        new URLSearchParams(`tutor=${TUTOR_ID.toUpperCase()}&reason=no_rate`),
      ),
    ).toEqual({ tutor: TUTOR_ID, reason: 'no_rate' });
  });
});

describe('pages and states (C4, edge cases)', () => {
  it('follows the cursor with "Load more", keeping the filters', async () => {
    stub({
      [LIST]: [
        (request) => {
          urls.push(new URL(request.url));
          return jsonResponse(200, page([UNRESOLVED_NO_RATE], 'next-1'));
        },
        (request) => {
          urls.push(new URL(request.url));
          return jsonResponse(200, page([UNRESOLVED_NO_TERMS]));
        },
      ],
    });
    await openWithRows('/payroll/unresolved?reason=no_rate');
    await click(buttonNamed(main(), 'Load more'));
    await settleUntil(() => row(UNRESOLVED_NO_TERMS));
    expect(lastUrl().get('cursor')).toBe('next-1');
    expect(lastUrl().get('reason')).toBe('no_rate');
    expect(row(UNRESOLVED_NO_RATE)).not.toBeNull();
    expect(
      [...main().querySelectorAll('button')].some(
        (button) => button.textContent === 'Load more',
      ),
    ).toBe(false);
  });

  it('keeps the rows when a next page fails, and retries it', async () => {
    stub({
      [LIST]: [
        () => jsonResponse(200, page([UNRESOLVED_NO_RATE], 'next-1')),
        () => problem(400, 'bad_request'),
        () => jsonResponse(200, page([UNRESOLVED_NO_TERMS])),
      ],
    });
    await openWithRows();
    await click(buttonNamed(main(), 'Load more'));
    await settleUntil(() => alerts(main()).length > 0);
    expect(row(UNRESOLVED_NO_RATE)).not.toBeNull();
    await click(buttonNamed(main(), 'Try again'));
    await settleUntil(() => row(UNRESOLVED_NO_TERMS));
    expect(row(UNRESOLVED_NO_RATE)).not.toBeNull();
  });

  it('says all work has pay when nothing is listed', async () => {
    stub({}, { items: [] });
    await open();
    await settleUntil(() => main().querySelector('[data-unresolved-empty]'));
    expect(main().querySelector('[data-unresolved-empty]')?.textContent).toBe(
      'All approved work has pay',
    );
    expect(main().querySelector('[data-payroll-off]')).toBeNull();
  });

  it('in Spanish, as groomed', async () => {
    applyLanguage('es');
    stub({}, { items: [] });
    await open();
    await settleUntil(() => main().querySelector('[data-unresolved-empty]'));
    expect(main().querySelector('[data-unresolved-empty]')?.textContent).toBe(
      'Todo el trabajo aprobado tiene pago',
    );
  });

  it("doesn't claim that under a filter", async () => {
    stub({}, { items: [] });
    await open('/payroll/unresolved?reason=no_rate');
    await settleUntil(() =>
      main().textContent?.includes('Nothing matches these filters.'),
    );
    expect(main().querySelector('[data-unresolved-empty]')).toBeNull();
  });

  it("doesn't claim that while payroll is off, and links to Settings", async () => {
    stub({}, { items: [], settings: SETTINGS_OFF });
    await open();
    await settleUntil(() => main().querySelector('[data-payroll-off]'));
    expect(main().querySelector('[data-unresolved-empty]')).toBeNull();
    expect(links(main().querySelector('[data-payroll-off]')!)).toEqual([
      '/payroll/settings',
    ]);
  });

  it("doesn't claim that while the settings can't be read", async () => {
    stub({ [SETTINGS]: () => problem(500, 'internal_error') }, { items: [] });
    await open();
    await settleUntil(() => alerts(main()).length > 0);
    expect(main().querySelector('[data-unresolved-empty]')).toBeNull();
  });

  it('shows an error with a retry', async () => {
    const api = stub({
      [LIST]: [
        () => problem(400, 'bad_request'),
        () => jsonResponse(200, page([UNRESOLVED_NO_RATE])),
      ],
    });
    await open();
    await settleUntil(() => alerts(main()).length > 0);
    await click(buttonNamed(main(), 'Try again'));
    await settleUntil(() => row(UNRESOLVED_NO_RATE));
    expect(api.calls(LIST)).toHaveLength(2);
  });

  it('adds both tabs, marking the current one', async () => {
    stub();
    await openWithRows();
    const nav = main().querySelector('nav')!;
    expect(links(nav)).toEqual([
      '/payroll/periods',
      '/payroll/tutors',
      '/payroll/pay-rules',
      '/payroll/pay-item-types',
      '/payroll/unresolved',
      '/payroll/settings',
    ]);
    expect(nav.querySelector('[aria-current="page"]')?.textContent).toBe(
      'Unresolved work',
    );
  });
});
