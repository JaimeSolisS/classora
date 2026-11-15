import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { problemWith } from '@/features/academics/testing';
import type { SessionUserBody } from '@/features/auth/session';
import { applyLanguage } from '@/i18n';
import type { LocalDate } from '@/lib/time';
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
import type { ExpenseTemplate } from '../api';
import {
  CAT_MAINTENANCE,
  CAT_RENT,
  CAT_RETIRED,
  expensePage,
  expenseTemplate,
  MIXED_CATEGORIES,
  TEMPLATE_ENDED,
  TEMPLATE_RENT,
  TEMPLATES as TEMPLATE_ROWS,
  VENDOR_PAPER,
  VENDOR_RETIRED,
  vendorPage,
  VENDORS as VENDOR_ROWS,
} from '../fixtures';
import {
  CATEGORIES,
  EXPENSES,
  FINANCE,
  OWNER,
  TEMPLATE_POST,
  templateGet,
  TEMPLATES,
  VENDORS,
} from '../testing';
import {
  templateCreateBody,
  templateListParams,
  templateListSearch,
  templateUpdateBody,
  templateForm,
  validateTemplateForm,
  emptyTemplateForm,
} from './rules';

// M7-13b A3, B, D1, D5, D6: the recurring template list (columns, badges,
// filters to the URL, empty states, the out-of-date alert) and "Nueva
// plantilla" with its mapped refusals and the #458 config-page close rules.

const VIEWER: SessionUserBody = {
  ...FINANCE,
  permissions: ['expense.configure', 'expense.view'],
};
const TODAY = '2026-10-20' as LocalDate;
const CREATED = expenseTemplate(9, {
  name: 'Renta de bodega',
  starts_on: TODAY,
  next_period_start: TODAY,
  last_period_start: null,
});
const lost = () => Promise.reject(new TypeError('Failed to fetch'));

let app: RenderedApp | undefined;
const main = () => app!.container.querySelector('main')!;
const table = () => main().querySelector('[data-templates-table]');
const rows = () => [
  ...main().querySelectorAll<HTMLElement>('[data-templates-table] tbody tr'),
];
const dialog = () =>
  document.querySelector<HTMLElement>('[data-template-dialog="create"]');
const addButton = () => buttonNamed(main(), 'New template');
let listed: string[] = [];
const list =
  (items: ExpenseTemplate[] = TEMPLATE_ROWS): Responder =>
  (request) => {
    listed.push(new URL(request.url).search);
    return jsonResponse(200, items);
  };

function stub(
  routes: Record<string, Responder | Responder[]> = {},
  as: SessionUserBody = OWNER,
): ApiStub {
  return stubApi({
    'GET /api/v1/auth/me': meAs(as),
    [TEMPLATES]: list(),
    [CATEGORIES]: () => jsonResponse(200, MIXED_CATEGORIES),
    [VENDORS]: () => jsonResponse(200, vendorPage(VENDOR_ROWS)),
    [templateGet(CREATED.id)]: () => jsonResponse(200, CREATED),
    [EXPENSES]: () => jsonResponse(200, expensePage([])),
    ...routes,
  });
}

async function openList(path = '/expenses/templates') {
  app = await renderApp(path);
  await settleUntil(() =>
    main().querySelector(
      '[data-templates-table], [data-templates-empty], [data-templates-no-match], [role="alert"]',
    ),
  );
}

async function openCreate() {
  await openList();
  await click(addButton());
  await settleUntil(() => dialog()?.querySelector('form'));
}

function fill(
  root: HTMLElement,
  {
    name = 'Renta de bodega',
    category = CAT_RENT.id,
    amount = '9500',
  }: { name?: string; category?: string; amount?: string } = {},
) {
  setFieldValue(labelled(root, 'Name'), name);
  setFieldValue(labelled(root, 'Category'), category);
  setFieldValue(labelled(root, 'Amount of each expense'), amount);
}

const submit = () => click(buttonNamed(dialog()!, 'Create'));
const fieldError = (label: string) => {
  const field = labelled(dialog()!, label);
  const ids = field.getAttribute('aria-describedby')?.split(' ') ?? [];
  return ids
    .map((id) => document.getElementById(id))
    .filter((node) => node?.id.endsWith('-error'))
    .map((node) => node?.textContent ?? '')
    .join('');
};

const closeRoutes = [
  ['Cancel', (root: HTMLElement) => click(buttonNamed(root, 'Cancel'))],
  ['Escape', () => pressEscape()],
  [
    'the close button',
    (root: HTMLElement) =>
      click(root.querySelector<HTMLElement>('[data-slot="dialog-close"]')!),
  ],
] as const;

beforeEach(() => {
  applyLanguage('en');
  listed = [];
  vi.useFakeTimers({ toFake: ['Date'] });
  // 12:00 on 2026-10-20 in Mexico City.
  vi.setSystemTime(new Date('2026-10-20T18:00:00Z'));
  vi.spyOn(console, 'error').mockImplementation(() => {});
});

afterEach(() => {
  app?.unmount();
  app = undefined;
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('rules', () => {
  it('reads and writes the list URL, ignoring anything invalid', () => {
    const params = templateListParams(
      new URLSearchParams(
        `status=paused&status=bogus&status=active&category=${CAT_RENT.id.toUpperCase()}&vendor=nope`,
      ),
    );
    expect(params).toEqual({
      statuses: ['active', 'paused'],
      categoryId: CAT_RENT.id,
      vendorId: '',
    });
    expect(templateListSearch(params)).toBe(
      `status=active&status=paused&category=${CAT_RENT.id}`,
    );
  });

  it('checks the create form like the API (#116 C1, E5, E6)', () => {
    const base = { ...emptyTemplateForm(TODAY), name: 'X', categoryId: 'c' };
    expect(
      validateTemplateForm({ ...base, amount: '10' }, TODAY, null),
    ).toEqual({});
    expect(
      validateTemplateForm(
        { ...base, amount: '0', dueOffset: '61', startsOn: '2026-09-18' },
        TODAY,
        null,
      ),
    ).toEqual({
      amount: 'finance.expenses.validation.amountPositive',
      dueOffset: 'finance.templates.validation.dueOffsetRange',
      startsOn: 'errors:expense_template_starts_on_invalid',
    });
    // Exactly 31 days back and 366 ahead are allowed.
    for (const startsOn of ['2026-09-19', '2027-10-21']) {
      expect(
        validateTemplateForm({ ...base, amount: '1', startsOn }, TODAY, null),
      ).toEqual({});
    }
    expect(
      validateTemplateForm(
        { ...base, amount: '1', startsOn: '2027-10-22' },
        TODAY,
        null,
      ).startsOn,
    ).toBe('errors:expense_template_starts_on_invalid');
    expect(
      validateTemplateForm(
        { ...base, amount: '1', endsOn: '2026-10-19' },
        TODAY,
        null,
      ).endsOn,
    ).toBe('errors:expense_template_ends_on_invalid');
    expect(
      validateTemplateForm(
        { ...base, name: ' ', categoryId: '', amount: '1' },
        TODAY,
        null,
      ),
    ).toEqual({
      name: 'finance.templates.validation.nameRequired',
      categoryId: 'finance.templates.validation.categoryRequired',
    });
  });

  it('an edit may not end before the latest generated period', () => {
    const values = { ...templateForm(TEMPLATE_RENT), endsOn: '2026-09-30' };
    expect(validateTemplateForm(values, TODAY, TEMPLATE_RENT).endsOn).toBe(
      'errors:expense_template_ends_on_invalid',
    );
    expect(
      validateTemplateForm(
        { ...values, endsOn: '2026-10-01' },
        TODAY,
        TEMPLATE_RENT,
      ),
    ).toEqual({});
  });

  it('builds the bodies: the create, and an edit with only what changed', () => {
    expect(
      templateCreateBody({
        ...emptyTemplateForm(TODAY),
        name: '  Renta  ',
        categoryId: CAT_RENT.id,
        amount: ' 1250.5 ',
        dueOffset: '7',
      }),
    ).toEqual({
      name: 'Renta',
      category_id: CAT_RENT.id,
      vendor_id: null,
      amount: '1250.5',
      frequency: 'monthly',
      starts_on: TODAY,
      ends_on: null,
      due_offset_days: 7,
    });
    const values = templateForm({ ...TEMPLATE_RENT, ends_on: '2026-12-31' });
    expect(
      templateUpdateBody(values, { ...TEMPLATE_RENT, ends_on: '2026-12-31' }),
    ).toBeNull();
    expect(
      templateUpdateBody(
        { ...values, amount: '12500', vendorId: '', endsOn: '' },
        { ...TEMPLATE_RENT, ends_on: '2026-12-31' },
      ),
    ).toEqual({ version: 1, vendor_id: null, ends_on: null });
  });
});

describe('the list (B1, B2)', () => {
  it('shows every column, the three statuses and the inactive badges', async () => {
    stub();
    await openList();
    expect(table()).not.toBeNull();
    const heads = [...table()!.querySelectorAll('thead th')].map(
      (th) => th.textContent,
    );
    expect(heads).toEqual([
      'Name',
      'Category',
      'Vendor',
      'Amount',
      'Frequency',
      'Next period',
      'Last generated',
      'Status',
    ]);
    const cells = rows().map((row) =>
      [...row.querySelectorAll('td')].map((td) => td.textContent),
    );
    expect(cells[0]).toEqual([
      'Renta del local',
      'Rent',
      VENDOR_PAPER.name,
      '$12,500.00',
      'Monthly',
      'Nov 1, 2026',
      'Oct 1, 2026',
      'Active',
    ]);
    expect(cells[1]![6]).toBe('—');
    expect(cells[2]![2]).toBe('—');
    expect(cells[2]![4]).toBe('Weekly');
    expect(cells[2]![7]).toBe('Paused');
    // Ended: no next period; the inactive category and vendor say so.
    expect(cells[3]).toEqual([
      TEMPLATE_ENDED.name,
      `${CAT_RETIRED.name}Inactive`,
      `${VENDOR_RETIRED.name}Inactive`,
      '$3,000.00',
      'Annual',
      '—',
      'Oct 1, 2025',
      'Ended',
    ]);
    const link = rows()[0]!.querySelector('a');
    expect(link?.getAttribute('href')).toBe(
      `/expenses/templates/${TEMPLATE_RENT.id}`,
    );
    // Cards below md.
    expect(main().querySelectorAll('[data-templates-cards] li')).toHaveLength(
      4,
    );
  });

  it('labels statuses and frequencies in Spanish; the name stays as typed', async () => {
    applyLanguage('es');
    stub();
    await openList();
    const cells = rows().map((row) =>
      [...row.querySelectorAll('td')].map((td) => td.textContent),
    );
    expect(cells[0]![0]).toBe('Renta del local');
    expect(cells[0]![1]).toBe('Renta');
    expect(cells[0]![4]).toBe('Mensual');
    expect(cells.map((c) => c[7])).toEqual([
      'Activa',
      'Activa',
      'Pausada',
      'Finalizada',
    ]);
    expect(cells[2]![4]).toBe('Semanal');
    expect(cells[3]![4]).toBe('Anual');
  });

  it('writes the filters to the URL and asks the API for them', async () => {
    stub();
    await openList();
    const statusBox = [
      ...main().querySelectorAll<HTMLInputElement>('input[type="checkbox"]'),
    ].find((box) => box.parentElement?.textContent === 'Paused')!;
    await click(statusBox);
    await settleUntil(() => app!.location().includes('status=paused'));
    setFieldValue(labelled(main(), 'Category'), CAT_RETIRED.id);
    await settleUntil(() => app!.location().includes('category='));
    expect(app!.location()).toBe(
      `/expenses/templates?status=paused&category=${CAT_RETIRED.id}`,
    );
    await settleUntil(() => listed.at(-1)?.includes('category_id') === true);
    expect(listed.at(-1)).toBe(`?status=paused&category_id=${CAT_RETIRED.id}`);
    // Inactive categories and vendors are offered, marked.
    const options = [
      ...labelled<HTMLSelectElement>(main(), 'Vendor').options,
    ].map((o) => o.textContent);
    expect(options).toContain(`${VENDOR_RETIRED.name} (inactive)`);
  });

  it('reads the filters from the URL', async () => {
    stub();
    await openList(
      `/expenses/templates?status=ended&vendor=${VENDOR_RETIRED.id}`,
    );
    expect(listed[0]).toBe(`?status=ended&vendor_id=${VENDOR_RETIRED.id}`);
    expect(labelled<HTMLSelectElement>(main(), 'Vendor').value).toBe(
      VENDOR_RETIRED.id,
    );
  });

  it('empty: offers "New template" to managers only; filtered: no match', async () => {
    stub({ [TEMPLATES]: list([]) });
    await openList();
    expect(main().querySelector('[data-templates-empty]')?.textContent).toBe(
      'There are no recurring expenses yet.New template',
    );
    app!.unmount();
    stub({ [TEMPLATES]: list([]) }, VIEWER);
    await openList();
    expect(main().querySelector('[data-templates-empty]')?.textContent).toBe(
      'There are no recurring expenses yet.',
    );
    expect(() => addButton()).toThrow();
    app!.unmount();
    stub({ [TEMPLATES]: list([]) });
    await openList('/expenses/templates?status=paused');
    expect(main().querySelector('[data-templates-no-match]')?.textContent).toBe(
      'No recurring expense matches the filters.',
    );
  });

  it('A3: Finance Staff see "New template"; a view-only user doesn\'t', async () => {
    stub({}, FINANCE);
    await openList();
    expect(addButton()).toBeDefined();
    app!.unmount();
    stub({}, VIEWER);
    await openList();
    expect(() => addButton()).toThrow();
    expect(rows()).toHaveLength(4);
  });

  it('a 403 shows the forbidden page', async () => {
    stub({ [TEMPLATES]: () => problem(403, 'permission_denied') });
    app = await renderApp('/expenses/templates');
    await settleUntil(
      () =>
        main().querySelector('h1')?.textContent ===
        "You don't have access to this page",
    );
  });
});

describe('"New template" (D1, D5)', () => {
  it('offers active categories and vendors only, with the keyed label', async () => {
    stub();
    await openCreate();
    const categories = [
      ...labelled<HTMLSelectElement>(dialog()!, 'Category').options,
    ].map((o) => o.textContent);
    expect(categories).toContain('Rent');
    expect(categories).not.toContain(CAT_RETIRED.name);
    expect(categories.some((c) => c?.includes('inactive'))).toBe(false);
    const vendors = [
      ...labelled<HTMLSelectElement>(dialog()!, 'Vendor (optional)').options,
    ].map((o) => o.textContent);
    expect(vendors[0]).toBe('No vendor');
    expect(vendors).not.toContain(VENDOR_RETIRED.name);
    // Defaults: monthly, today, due on the period's first day, no end.
    expect(labelled<HTMLSelectElement>(dialog()!, 'Frequency').value).toBe(
      'monthly',
    );
    const start = labelled<HTMLInputElement>(
      dialog()!,
      'First period starts on',
    );
    expect(start.value).toBe(TODAY);
    expect(start.min).toBe('2026-09-19');
    expect(start.max).toBe('2027-10-21');
    expect(labelled<HTMLInputElement>(dialog()!, 'Days until due').value).toBe(
      '0',
    );
    expect(
      labelled<HTMLInputElement>(dialog()!, 'Ends on (optional)').value,
    ).toBe('');
  });

  it('creates it and opens the new template', async () => {
    const api = stub({
      [TEMPLATE_POST]: () => jsonResponse(201, CREATED),
    });
    await openCreate();
    fill(dialog()!);
    setFieldValue(labelled(dialog()!, 'Vendor (optional)'), VENDOR_PAPER.id);
    setFieldValue(labelled(dialog()!, 'Frequency'), 'weekly');
    setFieldValue(labelled(dialog()!, 'Days until due'), '3');
    setFieldValue(labelled(dialog()!, 'Ends on (optional)'), '2027-06-30');
    await submit();
    await settleUntil(
      () => app!.location() === `/expenses/templates/${CREATED.id}`,
    );
    expect(api.calls(TEMPLATE_POST)[0]!.body).toEqual({
      name: 'Renta de bodega',
      category_id: CAT_RENT.id,
      vendor_id: VENDOR_PAPER.id,
      amount: '9500',
      frequency: 'weekly',
      starts_on: TODAY,
      ends_on: '2027-06-30',
      due_offset_days: 3,
    });
    // No Idempotency-Key: the name is the natural key (#116 E3).
    expect(
      api.calls(TEMPLATE_POST)[0]!.headers.get('Idempotency-Key'),
    ).toBeNull();
    await settleUntil(
      () =>
        main().querySelector('[data-template-announcement]')?.textContent ===
        'Recurring expense created.',
    );
  });

  it('says past periods are generated when the start is in the past', async () => {
    stub();
    await openCreate();
    expect(dialog()!.querySelector('[data-past-start]')).toBeNull();
    setFieldValue(labelled(dialog()!, 'First period starts on'), '2026-10-01');
    await settle();
    expect(dialog()!.querySelector('[data-past-start]')?.textContent).toBe(
      'The expenses of the periods from that date up to today are generated automatically.',
    );
  });

  it('checks the start window, the end and the due offset before sending', async () => {
    const api = stub();
    await openCreate();
    fill(dialog()!);
    setFieldValue(labelled(dialog()!, 'First period starts on'), '2026-09-18');
    setFieldValue(labelled(dialog()!, 'Days until due'), '61');
    await submit();
    expect(api.calls(TEMPLATE_POST)).toHaveLength(0);
    expect(fieldError('First period starts on')).toBe(
      'The first period must start at most a month before today and at most a year after.',
    );
    expect(fieldError('Days until due')).toBe(
      'Enter a whole number from 0 to 60.',
    );
    setFieldValue(labelled(dialog()!, 'First period starts on'), TODAY);
    setFieldValue(labelled(dialog()!, 'Days until due'), '0');
    setFieldValue(labelled(dialog()!, 'Ends on (optional)'), '2026-10-19');
    await submit();
    expect(api.calls(TEMPLATE_POST)).toHaveLength(0);
    expect(fieldError('Ends on (optional)')).toBe(
      "The end can't be before the start or before the latest period already generated.",
    );
  });

  it.each([
    [
      'expense_template_exists',
      () => problem(409, 'expense_template_exists'),
      'Name',
      'A recurring expense with this name already exists (paused and ended ones count too). Use another name or rename the old one.',
    ],
    [
      'expense_template_starts_on_invalid',
      () =>
        problemWith(422, 'expense_template_starts_on_invalid', {
          errors: [
            {
              path: ['body', 'starts_on'],
              code: 'expense_template_starts_on_invalid',
            },
          ],
        }),
      'First period starts on',
      'The first period must start at most a month before today and at most a year after.',
    ],
    [
      'text_too_long',
      () =>
        problemWith(422, 'validation_error', {
          errors: [{ path: ['body', 'name'], code: 'text_too_long' }],
        }),
      'Name',
      'This text is too long.',
    ],
    [
      'money_invalid',
      () =>
        problemWith(422, 'validation_error', {
          errors: [{ path: ['body', 'amount'], code: 'money_invalid' }],
        }),
      'Amount of each expense',
      'Enter the amount in pesos, with up to 2 decimals.',
    ],
    [
      'amount_not_positive',
      () =>
        problemWith(422, 'validation_error', {
          errors: [{ path: ['body', 'amount'], code: 'amount_not_positive' }],
        }),
      'Amount of each expense',
      'Enter an amount greater than 0.',
    ],
  ])('maps %s onto its field', async (_code, answer, label, text) => {
    stub({ [TEMPLATE_POST]: answer });
    await openCreate();
    fill(dialog()!);
    await submit();
    await settleUntil(() => fieldError(label) !== '');
    expect(fieldError(label)).toBe(text);
    await settleUntil(
      () => document.activeElement === labelled(dialog()!, label),
    );
  });

  it.each([
    ['expense_category_inactive', 409, 'Category'],
    ['expense_category_unknown', 422, 'Category'],
    ['vendor_inactive', 409, 'Vendor (optional)'],
    ['vendor_unknown', 422, 'Vendor (optional)'],
  ] as const)(
    '%s goes on its field and reloads the pickers',
    async (code, status, label) => {
      const api = stub({ [TEMPLATE_POST]: () => problem(status, code) });
      await openCreate();
      fill(dialog()!);
      const categories = api.calls(CATEGORIES).length;
      const vendors = api.calls(VENDORS).length;
      await submit();
      await settleUntil(() => fieldError(label) !== '');
      expect(api.calls(CATEGORIES).length).toBeGreaterThan(categories);
      expect(api.calls(VENDORS).length).toBeGreaterThan(vendors);
      expect(dialog()!.textContent).toContain(
        'The categories and vendors were reloaded.',
      );
    },
  );
});

describe('a lost answer (E2 (a), D6)', () => {
  it('a create retried after a lost answer that ends in "exists" reloads and says it may be saved', async () => {
    const api = stub({
      [TEMPLATE_POST]: [lost, () => problem(409, 'expense_template_exists')],
    });
    await openCreate();
    fill(dialog()!);
    await submit();
    await settleUntil(() => alerts(dialog()!).length > 0);
    const before = api.calls(TEMPLATES).length;
    await submit();
    await settleUntil(() =>
      dialog()!.textContent.includes('may already have created'),
    );
    expect(api.calls(TEMPLATES).length).toBeGreaterThan(before);
    expect(alerts(dialog()!).join()).toContain(
      'Your earlier attempt may already have created this template. The list was reloaded: check it before trying again.',
    );
  });

  it("…and never claims the list reloaded when it didn't", async () => {
    let failing = false;
    stub({
      [TEMPLATE_POST]: [lost, () => problem(409, 'expense_template_exists')],
      [TEMPLATES]: () =>
        failing
          ? problem(503, 'database_unavailable')
          : list()(new Request('http://x/'), undefined),
    });
    await openCreate();
    fill(dialog()!);
    await submit();
    await settleUntil(() => alerts(dialog()!).length > 0);
    failing = true;
    await submit();
    await settleUntil(() =>
      dialog()!.textContent.includes('may already have created'),
    );
    expect(dialog()!.textContent).toContain("The list couldn't be reloaded");
    expect(dialog()!.textContent).not.toContain('The list was reloaded');
  });

  it('a plain "exists" (no lost answer before) says nothing extra', async () => {
    stub({ [TEMPLATE_POST]: () => problem(409, 'expense_template_exists') });
    await openCreate();
    fill(dialog()!);
    await submit();
    await settleUntil(() => fieldError('Name') !== '');
    expect(dialog()!.textContent).not.toContain('may already have created');
  });

  it.each(closeRoutes)(
    'closing by %s after a failed save refetches the list and holds "New template" until it lands',
    async (_route, close) => {
      let release: () => void = () => {};
      let held = false;
      const api = stub({
        [TEMPLATE_POST]: lost,
        [TEMPLATES]: (request) =>
          held
            ? new Promise<Response>((resolve) => {
                release = () => resolve(list()(request, undefined));
              })
            : list()(request, undefined),
      });
      await openCreate();
      fill(dialog()!);
      await submit();
      await settleUntil(() => alerts(dialog()!).length > 0);
      const before = api.calls(TEMPLATES).length;
      held = true;
      await close(dialog()!);
      await settleUntil(() => !dialog());
      await settleUntil(() => api.calls(TEMPLATES).length > before);
      expect(addButton().disabled).toBe(true);
      release();
      await settleUntil(() => !addButton().disabled);
      expect(main().textContent).not.toMatch(/reloaded/);
    },
  );

  it('a failed refetch after close keeps the rows with the out-of-date alert', async () => {
    let failing = false;
    stub({
      [TEMPLATE_POST]: lost,
      [TEMPLATES]: (request) =>
        failing
          ? problem(503, 'database_unavailable')
          : list()(request, undefined),
    });
    await openCreate();
    fill(dialog()!);
    await submit();
    await settleUntil(() => alerts(dialog()!).length > 0);
    failing = true;
    await click(buttonNamed(dialog()!, 'Cancel'));
    await settleUntil(() => alerts(main()).length > 0);
    expect(alerts(main()).join()).toContain(
      "The list couldn't be refreshed and may be out of date.",
    );
    expect(main().textContent).not.toMatch(/was reloaded/);
    expect(rows()).toHaveLength(4);
    expect(addButton().disabled).toBe(true);
    failing = false;
    await click(buttonNamed(main(), 'Reload the list'));
    await settleUntil(() => !addButton().disabled);
    expect(alerts(main())).toEqual([]);
  });

  it('a close with no attempt makes no request', async () => {
    const api = stub();
    await openCreate();
    const before = api.requests.length;
    await click(buttonNamed(dialog()!, 'Cancel'));
    await settle();
    expect(api.requests.length).toBe(before);
    expect(addButton().disabled).toBe(false);
  });

  it('a settled refusal then a close also refetches (any failed save)', async () => {
    const api = stub({
      [TEMPLATE_POST]: () => problem(409, 'expense_template_exists'),
    });
    await openCreate();
    fill(dialog()!, { name: CAT_MAINTENANCE.name });
    await submit();
    await settleUntil(() => fieldError('Name') !== '');
    const before = api.calls(TEMPLATES).length;
    await pressEscape();
    await settleUntil(() => api.calls(TEMPLATES).length > before);
  });
});
