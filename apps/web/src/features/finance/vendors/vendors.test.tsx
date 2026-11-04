import { act } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { problemWith } from '@/features/academics/testing';
import { applyLanguage } from '@/i18n';
import {
  meAs,
  problem,
  stubApi,
  type ApiStub,
  type Responder,
} from '@/test/api-stub';
import {
  alerts,
  click,
  labelled,
  openDialog,
  pressEscape,
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
import type { Vendor, VendorPage } from '../api';
import {
  VENDOR_CLEANING,
  VENDOR_LONG,
  VENDOR_PAPER,
  VENDOR_RETIRED,
  VENDORS,
  vendorPage,
} from '../fixtures';
import {
  validateVendorForm,
  vendorCreateBody,
  vendorUpdateBody,
} from '../forms';
import { FINANCE, VENDOR_POST, VENDORS as LIST, vendorPatch } from '../testing';
import { vendorListParams } from './listParams';

// M7-11b C, D, E: the vendors page. The list and its URL (search, filter,
// page), both empty states, each dialog's success and refusal, `contact:
// null` on clearing, stale confirmations, and the #458 rules plus D2.

let app: RenderedApp | undefined;
let listUrls: URL[] = [];
const main = () => app!.container.querySelector('main')!;
const dialog = () => openDialog()!;
const row = (vendor: Vendor) =>
  main().querySelector<HTMLElement>(
    `[data-vendors-table] [data-vendor-id="${vendor.id}"]`,
  )!;
const named = (root: ParentNode, name: string) =>
  [...root.querySelectorAll<HTMLButtonElement>('button')].find(
    (button) =>
      button.textContent === name || button.getAttribute('aria-label') === name,
  );
const statusText = () =>
  [...main().querySelectorAll('[role="status"]')]
    .map((node) => node.textContent)
    .join(' ');
const search = () => labelled<HTMLInputElement>(main(), 'Search by name');
const stateFilter = () => labelled<HTMLSelectElement>(main(), 'State');

function stub(
  routes: Record<string, Responder | Responder[]> = {},
  { page = vendorPage() }: { page?: VendorPage } = {},
): ApiStub {
  return stubApi({
    'GET /api/v1/auth/me': meAs(FINANCE),
    [LIST]: (request) => {
      listUrls.push(new URL(request.url));
      return jsonResponse(200, page);
    },
    ...routes,
  });
}

async function openPage(path = '/expenses/vendors') {
  app = await renderApp(path);
  await settleUntil(() => main().querySelector('h1'));
  await settle();
}

async function openAdd() {
  await click(named(main(), 'Add vendor')!);
  await settleUntil(() => openDialog());
}

async function openEdit(vendor: Vendor) {
  await click(named(row(vendor), `Edit ${vendor.name}`)!);
  await settleUntil(() => openDialog());
}

beforeEach(() => {
  listUrls = [];
  applyLanguage('en');
});

afterEach(() => {
  app?.unmount();
  app = undefined;
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('the rules', () => {
  it('reads the URL, ignoring anything invalid', () => {
    expect(
      vendorListParams(
        new URLSearchParams('q=%20pap%20&state=inactive&page=3'),
      ),
    ).toEqual({ q: 'pap', state: 'inactive', page: 3 });
    expect(vendorListParams(new URLSearchParams('state=gone&page=-1'))).toEqual(
      { q: '', state: 'all', page: 1 },
    );
  });

  it('counts the contact in code points, up to 500', () => {
    expect(
      validateVendorForm({ name: 'A', contact: '😀'.repeat(500) }),
    ).toEqual({});
    expect(validateVendorForm({ name: 'A', contact: 'a'.repeat(501) })).toEqual(
      { contact: 'finance.validation.contactTooLong' },
    );
    expect(validateVendorForm({ name: 'a'.repeat(201), contact: '' })).toEqual({
      name: 'finance.validation.vendorNameTooLong',
    });
  });

  it('sends only what changed, and contact: null when cleared', () => {
    expect(vendorCreateBody({ name: ' A ', contact: '  ' })).toEqual({
      name: 'A',
    });
    expect(
      vendorUpdateBody(
        { name: VENDOR_PAPER.name, contact: VENDOR_PAPER.contact! },
        VENDOR_PAPER,
      ),
    ).toBeNull();
    expect(
      vendorUpdateBody({ name: VENDOR_PAPER.name, contact: ' ' }, VENDOR_PAPER),
    ).toEqual({ version: 1, contact: null });
    expect(
      vendorUpdateBody({ name: 'Nuevo', contact: '' }, VENDOR_CLEANING),
    ).toEqual({ version: 1, name: 'Nuevo' });
  });
});

describe('list (C1, C4)', () => {
  it('shows name, contact or a dash, and the state, in the API order', async () => {
    stub();
    await openPage();
    const rows = [
      ...main().querySelectorAll('[data-vendors-table] tbody tr'),
    ].map((tr) =>
      [...tr.querySelectorAll('td')].slice(0, 3).map((td) => td.textContent),
    );
    expect(rows).toEqual([
      ['Limpieza Muestra', '—', 'Active'],
      ['Papelería Ejemplo', VENDOR_PAPER.contact, 'Active'],
      [VENDOR_LONG.name, VENDOR_LONG.contact, 'Active'],
      ['Agua Purificada Demo', '55 0000 0004', 'Inactive'],
    ]);
    expect(
      named(row(VENDOR_RETIRED), 'Reactivate Agua Purificada Demo'),
    ).toBeDefined();
    expect(
      main().querySelectorAll('[data-vendors-cards] [data-vendor-card]'),
    ).toHaveLength(VENDORS.length);
    // Every vendor by default: no `active` filter is sent.
    expect(listUrls[0]?.searchParams.has('active')).toBe(false);
    expect(listUrls[0]?.searchParams.get('offset')).toBe('0');
  });

  it('takes the filters from the URL', async () => {
    stub();
    await openPage('/expenses/vendors?q=pap&state=inactive&page=2');
    expect(listUrls[0]?.searchParams.get('q')).toBe('pap');
    expect(listUrls[0]?.searchParams.get('active')).toBe('false');
    expect(listUrls[0]?.searchParams.get('offset')).toBe('25');
    expect(search().value).toBe('pap');
    expect(stateFilter().value).toBe('inactive');
  });

  it('writes the search to the URL after the wait, back on page 1', async () => {
    stub();
    await openPage('/expenses/vendors?page=2');
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
    setFieldValue(search(), 'pap');
    await act(() => vi.advanceTimersByTimeAsync(299));
    expect(app!.location()).toBe('/expenses/vendors?page=2');
    await act(() => vi.advanceTimersByTimeAsync(1));
    vi.useRealTimers();
    await settleUntil(() => app!.location() === '/expenses/vendors?q=pap');
    await settleUntil(() => listUrls.at(-1)?.searchParams.get('q') === 'pap');
    expect(listUrls.at(-1)?.searchParams.get('offset')).toBe('0');
  });

  it('keeps a state picked while the search waits', async () => {
    stub();
    await openPage();
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
    setFieldValue(search(), 'pap');
    await act(() => vi.advanceTimersByTimeAsync(100));
    setFieldValue(stateFilter(), 'active');
    await act(() => vi.advanceTimersByTimeAsync(600));
    vi.useRealTimers();
    await settleUntil(
      () => listUrls.at(-1)?.searchParams.get('active') === 'true',
    );
    expect(listUrls.at(-1)?.searchParams.get('q')).toBe('pap');
    expect(app!.location()).toContain('state=active');
    expect(app!.location()).toContain('q=pap');
  });

  it('a page change while a search waits starts the search on page 1', async () => {
    stub({}, { page: vendorPage(VENDORS, { total: 60 }) });
    await openPage();
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
    setFieldValue(search(), 'pap');
    await act(() => vi.advanceTimersByTimeAsync(100));
    await act(async () => {
      named(main(), 'Next')!.click();
      await vi.advanceTimersByTimeAsync(600);
    });
    vi.useRealTimers();
    await settleUntil(() => app!.location() === '/expenses/vendors?q=pap');
    await settle();
    expect(app!.location()).toBe('/expenses/vendors?q=pap');
  });

  it.each([
    ['active', 'true'],
    ['inactive', 'false'],
  ])('the %s filter sends active=%s', async (value, sent) => {
    stub();
    await openPage();
    setFieldValue(stateFilter(), value);
    await settleUntil(
      () => listUrls.at(-1)?.searchParams.get('active') === sent,
    );
    expect(app!.location()).toBe(`/expenses/vendors?state=${value}`);
  });

  it('pages by offset with the shared pagination', async () => {
    stub({}, { page: vendorPage(VENDORS, { total: 60 }) });
    await openPage();
    expect(main().textContent).toContain('1–4 of 60');
    await click(named(main(), 'Next')!);
    await settleUntil(
      () => listUrls.at(-1)?.searchParams.get('offset') === '25',
    );
    expect(app!.location()).toBe('/expenses/vendors?page=2');
  });

  it('says there are no vendors yet, with Add vendor', async () => {
    stub({}, { page: vendorPage([]) });
    await openPage();
    const empty = main().querySelector('[data-vendors-empty]')!;
    expect(empty.textContent).toContain('There are no vendors yet.');
    expect(named(empty, 'Add vendor')).toBeDefined();
    expect(main().querySelector('[data-vendors-no-match]')).toBeNull();
  });

  it('says nothing matches a search', async () => {
    stub({}, { page: vendorPage([]) });
    await openPage('/expenses/vendors?q=zzz');
    expect(main().querySelector('[data-vendors-no-match]')?.textContent).toBe(
      'No vendor matches the search or the filter.',
    );
    expect(main().querySelector('[data-vendors-empty]')).toBeNull();
  });

  it('shows the error with a retry', async () => {
    stub({ [LIST]: () => problem(400, 'database_unavailable') });
    await openPage();
    await settleUntil(() => alerts(main()).length > 0);
    expect(named(main(), 'Try again')).toBeDefined();
  });
});

describe('dialogs (C2)', () => {
  it('adds a vendor with a multi-line contact', async () => {
    const api = stub({
      [VENDOR_POST]: () => jsonResponse(201, VENDOR_PAPER),
    });
    await openPage();
    await openAdd();
    await click(buttonNamed(dialog(), 'Save'));
    expect(dialog().textContent).toContain('Enter the name.');
    expect(api.calls(VENDOR_POST)).toHaveLength(0);

    setFieldValue(labelled(dialog(), 'Name'), ' Papelería Ejemplo ');
    setFieldValue(
      labelled(dialog(), 'Contact (optional)'),
      'ventas@papeleria.example\n55 0000 0001 ',
    );
    expect(dialog().querySelector('[data-contact-counter]')?.textContent).toBe(
      '37 of 500 characters',
    );
    await click(buttonNamed(dialog(), 'Save'));
    await settleUntil(() => !openDialog());
    expect(api.calls(VENDOR_POST)[0]!.body).toEqual({
      name: 'Papelería Ejemplo',
      contact: 'ventas@papeleria.example\n55 0000 0001',
    });
    expect(statusText()).toContain('Vendor saved.');
  });

  it('adds a vendor with no contact', async () => {
    const api = stub({
      [VENDOR_POST]: () => jsonResponse(201, VENDOR_CLEANING),
    });
    await openPage();
    await openAdd();
    setFieldValue(labelled(dialog(), 'Name'), 'Limpieza Muestra');
    await click(buttonNamed(dialog(), 'Save'));
    await settleUntil(() => !openDialog());
    expect(api.calls(VENDOR_POST)[0]!.body).toEqual({
      name: 'Limpieza Muestra',
    });
  });

  it('stops a contact over 500 characters with no request', async () => {
    const api = stub();
    await openPage();
    await openAdd();
    setFieldValue(labelled(dialog(), 'Name'), 'A');
    setFieldValue(labelled(dialog(), 'Contact (optional)'), 'x'.repeat(501));
    await click(buttonNamed(dialog(), 'Save'));
    expect(dialog().textContent).toContain('Use at most 500 characters.');
    expect(api.calls(VENDOR_POST)).toHaveLength(0);
  });

  it('clearing the contact on edit sends contact: null', async () => {
    const api = stub({
      [vendorPatch(VENDOR_PAPER.id)]: () =>
        jsonResponse(200, { ...VENDOR_PAPER, contact: null, version: 2 }),
    });
    await openPage();
    await openEdit(VENDOR_PAPER);
    expect(
      labelled<HTMLTextAreaElement>(dialog(), 'Contact (optional)').value,
    ).toBe(VENDOR_PAPER.contact);
    setFieldValue(labelled(dialog(), 'Contact (optional)'), '');
    await click(buttonNamed(dialog(), 'Save'));
    await settleUntil(() => !openDialog());
    expect(api.calls(vendorPatch(VENDOR_PAPER.id))[0]!.body).toEqual({
      version: 1,
      contact: null,
    });
  });

  it('sends nothing when nothing changed', async () => {
    const api = stub();
    await openPage();
    await openEdit(VENDOR_PAPER);
    await click(buttonNamed(dialog(), 'Save'));
    await settleUntil(() => !openDialog());
    expect(api.calls(vendorPatch(VENDOR_PAPER.id))).toHaveLength(0);
  });

  it('puts vendor_exists on the name, with the reactivation hint', async () => {
    stub({ [VENDOR_POST]: () => problem(409, 'vendor_exists') });
    await openPage();
    await openAdd();
    setFieldValue(labelled(dialog(), 'Name'), 'Agua Purificada Demo');
    await click(buttonNamed(dialog(), 'Save'));
    await settleUntil(() =>
      dialog().textContent?.includes('A vendor with this name already exists'),
    );
    expect(labelled(dialog(), 'Name').getAttribute('aria-invalid')).toBe(
      'true',
    );
    expect(dialog().querySelector('[data-exists-hint]')?.textContent).toContain(
      'A deactivated vendor with this name may exist',
    );
  });

  it.each([['text_empty'], ['text_too_long'], ['text_invalid']])(
    'puts %s on the contact',
    async (code) => {
      stub({
        [VENDOR_POST]: () =>
          problemWith(422, 'validation_error', {
            errors: [{ path: ['body', 'contact'], code, message: 'x' }],
          }),
      });
      await openPage();
      await openAdd();
      setFieldValue(labelled(dialog(), 'Name'), 'A');
      setFieldValue(labelled(dialog(), 'Contact (optional)'), 'b');
      await click(buttonNamed(dialog(), 'Save'));
      await settleUntil(
        () =>
          labelled(dialog(), 'Contact (optional)').getAttribute(
            'aria-invalid',
          ) === 'true',
      );
    },
  );

  it('on version_conflict reloads and resets the form', async () => {
    const newer = { ...VENDOR_CLEANING, name: 'Limpieza Nueva', version: 2 };
    stub({
      [LIST]: [
        () => jsonResponse(200, vendorPage()),
        () => jsonResponse(200, vendorPage([newer])),
      ],
      [vendorPatch(VENDOR_CLEANING.id)]: () => problem(409, 'version_conflict'),
    });
    await openPage();
    await openEdit(VENDOR_CLEANING);
    setFieldValue(labelled(dialog(), 'Name'), 'Otra');
    await click(buttonNamed(dialog(), 'Save'));
    await settleUntil(() =>
      dialog().querySelector('[data-row-notice="conflictReloaded"]'),
    );
    expect(labelled<HTMLInputElement>(dialog(), 'Name').value).toBe(
      'Limpieza Nueva',
    );
  });

  it('on 404 says the vendor is gone and locks the form', async () => {
    stub({
      [vendorPatch(VENDOR_CLEANING.id)]: () => problem(404, 'not_found'),
    });
    await openPage();
    await openEdit(VENDOR_CLEANING);
    setFieldValue(labelled(dialog(), 'Name'), 'Otra');
    await click(buttonNamed(dialog(), 'Save'));
    await settleUntil(() =>
      dialog().textContent?.includes(
        'This vendor no longer exists. The list was reloaded.',
      ),
    );
    expect(buttonNamed(dialog(), 'Save').disabled).toBe(true);
  });
});

describe('deactivate and reactivate (C3)', () => {
  it('deactivates behind the confirmation and announces it', async () => {
    const api = stub({
      [vendorPatch(VENDOR_PAPER.id)]: () =>
        jsonResponse(200, { ...VENDOR_PAPER, is_active: false }),
    });
    await openPage();
    await click(named(row(VENDOR_PAPER), `Deactivate ${VENDOR_PAPER.name}`)!);
    await settleUntil(() => openDialog());
    expect(dialog().textContent).toContain(
      "It can't be chosen for new expenses; past expenses and reports keep it.",
    );
    await click(buttonNamed(dialog(), 'Deactivate'));
    await settleUntil(() => !openDialog());
    expect(api.calls(vendorPatch(VENDOR_PAPER.id))[0]!.body).toEqual({
      version: 1,
      is_active: false,
    });
    expect(statusText()).toBe('Vendor deactivated.');
  });

  it('says the vendor wording in Spanish', async () => {
    applyLanguage('es');
    stub();
    await openPage();
    await click(named(row(VENDOR_PAPER), `Desactivar ${VENDOR_PAPER.name}`)!);
    await settleUntil(() => openDialog());
    expect(dialog().textContent).toContain(
      'No se podrá elegir en gastos nuevos; los gastos y reportes anteriores lo conservan.',
    );
  });

  it('reactivates an inactive vendor', async () => {
    const api = stub({
      [vendorPatch(VENDOR_RETIRED.id)]: () => jsonResponse(200, VENDOR_RETIRED),
    });
    await openPage();
    await click(
      named(row(VENDOR_RETIRED), `Reactivate ${VENDOR_RETIRED.name}`)!,
    );
    await settleUntil(() => openDialog());
    await click(buttonNamed(dialog(), 'Reactivate'));
    await settleUntil(() => !openDialog());
    expect(api.calls(vendorPatch(VENDOR_RETIRED.id))[0]!.body).toEqual({
      version: 2,
      is_active: true,
    });
  });

  it('a stale confirmation closes, refreshes and says so', async () => {
    const api = stub({
      [vendorPatch(VENDOR_PAPER.id)]: () => problem(409, 'version_conflict'),
    });
    await openPage();
    await click(named(row(VENDOR_PAPER), `Deactivate ${VENDOR_PAPER.name}`)!);
    await settleUntil(() => openDialog());
    const reads = api.calls(LIST).length;
    await click(buttonNamed(dialog(), 'Deactivate'));
    await settleUntil(() => !openDialog());
    expect(statusText()).toContain(
      'Someone else changed this vendor. The list was reloaded',
    );
    expect(api.calls(LIST).length).toBeGreaterThan(reads);
  });
});

describe('lost answers and failed reloads (D1, D2)', () => {
  it('a gone vendor says the list reloaded only once it did', async () => {
    let down = false;
    stub({
      [LIST]: () =>
        down
          ? problem(400, 'database_unavailable')
          : jsonResponse(200, vendorPage()),
      [vendorPatch(VENDOR_PAPER.id)]: () => {
        down = true;
        return problem(404, 'not_found');
      },
    });
    await openPage();
    await click(named(row(VENDOR_PAPER), `Deactivate ${VENDOR_PAPER.name}`)!);
    await settleUntil(() => openDialog());
    await click(buttonNamed(dialog(), 'Deactivate'));
    await settleUntil(() => statusText().includes("couldn't be reloaded"));
    expect(statusText()).not.toContain('was reloaded');
    expect(row(VENDOR_PAPER)).not.toBeNull();
    expect(alerts(main()).join(' ')).toContain('may be out of date');

    down = false;
    await click(named(main(), 'Reload the list')!);
    await settleUntil(() => !named(main(), 'Reload the list'));
    expect(statusText()).toContain(
      'That vendor no longer exists. The list was reloaded.',
    );
  });

  it.each([
    ['Cancel', () => click(buttonNamed(dialog(), 'Cancel'))],
    ['Escape', () => pressEscape()],
    ['the close button', () => click(buttonNamed(dialog(), 'Close'))],
  ] as const)(
    'after a failed save, %s refreshes the list',
    async (_how, close) => {
      const api = stub({
        [vendorPatch(VENDOR_PAPER.id)]: () => problem(502, 'internal_error'),
      });
      await openPage();
      await openEdit(VENDOR_PAPER);
      setFieldValue(labelled(dialog(), 'Name'), 'Otro');
      await click(buttonNamed(dialog(), 'Save'));
      await settleUntil(() => alerts(dialog()).length > 0);
      const reads = api.calls(LIST).length;
      await close();
      await settleUntil(() => !openDialog());
      await settleUntil(() => api.calls(LIST).length > reads);
    },
  );

  it('a plain close refreshes nothing', async () => {
    const api = stub();
    await openPage();
    await openAdd();
    await click(buttonNamed(dialog(), 'Cancel'));
    await settleUntil(() => !openDialog());
    await settle();
    expect(api.calls(LIST)).toHaveLength(1);
  });

  it('a lost create retried into vendor_exists reloads and says so', async () => {
    let lost = true;
    const api = stub({
      [VENDOR_POST]: () => {
        if (lost) {
          lost = false;
          return Promise.reject(new TypeError('Failed to fetch'));
        }
        return problem(409, 'vendor_exists');
      },
    });
    await openPage();
    await openAdd();
    setFieldValue(labelled(dialog(), 'Name'), 'Nuevo');
    await click(buttonNamed(dialog(), 'Save'));
    await settleUntil(() => alerts(dialog()).length > 0);
    const reads = api.calls(LIST).length;
    await click(buttonNamed(dialog(), 'Save'));
    await settleUntil(() =>
      dialog().querySelector('[data-row-notice="maybeSavedReloaded"]'),
    );
    expect(dialog().textContent).toContain(
      'Your earlier attempt may already have saved this name. The list was reloaded',
    );
    expect(api.calls(LIST).length).toBeGreaterThan(reads);
  });

  it('a plain vendor_exists says nothing about an earlier attempt', async () => {
    const api = stub({ [VENDOR_POST]: () => problem(409, 'vendor_exists') });
    await openPage();
    await openAdd();
    setFieldValue(labelled(dialog(), 'Name'), 'Nuevo');
    await click(buttonNamed(dialog(), 'Save'));
    await settleUntil(() => dialog().querySelector('[data-exists-hint]'));
    expect(dialog().querySelector('[data-row-notice]')).toBeNull();
    expect(api.calls(LIST)).toHaveLength(1);
  });
});
