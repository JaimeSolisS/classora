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
import {
  CAT_RENT,
  CAT_RETIRED,
  EXPENSE_ITEMS,
  EXPENSE_OVERDUE,
  EXPENSE_UNPAID,
  expenseDetail,
  expensePage,
  GENERATED_EXPENSES,
  MIXED_CATEGORIES,
  VENDOR_PAPER,
  VENDOR_RETIRED,
  vendorPage,
  VENDORS as VENDOR_ROWS,
} from '../fixtures';
import {
  CATEGORIES,
  EXPENSE_POST,
  EXPENSES,
  expenseGet,
  FINANCE,
  OWNER,
  VENDORS,
} from '../testing';
import {
  adjustmentBody,
  expenseCreateBody,
  expenseListParams,
  expenseListSearch,
  validateAdjustmentForm,
  validateExpenseForm,
  emptyExpenseForm,
} from './rules';

// M7-12b B, D1, D6: the expense list (columns, badges, filters to the URL,
// pagination, empty states, the out-of-date alert), "Nuevo gasto" with its
// mapped refusals, and the locked resend after a lost answer.

const VIEWER: SessionUserBody = {
  ...FINANCE,
  permissions: ['expense.configure', 'expense.view'],
};
const TODAY = '2026-10-20' as LocalDate;
const CREATED_ID = '84000000-0000-4000-8000-000000000099';
const lost = () => Promise.reject(new TypeError('Failed to fetch'));

let app: RenderedApp | undefined;
const main = () => app!.container.querySelector('main')!;
const table = () => main().querySelector('[data-expenses-table]');
const dialog = () =>
  document.querySelector<HTMLElement>('[data-expense-dialog="create"]');
const said = () =>
  main().querySelector('[data-expenses-announcement]')?.textContent ?? '';
const addButton = () => buttonNamed(main(), 'New expense');
const keys = (api: ApiStub) =>
  api.calls(EXPENSE_POST).map((call) => call.headers.get('Idempotency-Key'));
// The list requests' search strings (the stub records paths only).
let listed: string[] = [];
const listQueries = () => listed;
const page =
  (body = expensePage()): Responder =>
  (request) => {
    listed.push(new URL(request.url).search);
    return jsonResponse(200, body);
  };

function stub(
  routes: Record<string, Responder | Responder[]> = {},
  as: SessionUserBody = OWNER,
): ApiStub {
  return stubApi({
    'GET /api/v1/auth/me': meAs(as),
    [EXPENSES]: page(),
    [CATEGORIES]: () => jsonResponse(200, MIXED_CATEGORIES),
    [VENDORS]: () => jsonResponse(200, vendorPage(VENDOR_ROWS)),
    [expenseGet(CREATED_ID)]: () =>
      jsonResponse(200, expenseDetail(99, { id: CREATED_ID })),
    ...routes,
  });
}

async function openList(path = '/expenses/records') {
  app = await renderApp(path);
  await settleUntil(() =>
    main().querySelector(
      '[data-expenses-table], [data-expenses-empty], [data-expenses-no-match], [role="alert"]',
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
    category = CAT_RENT.id,
    description = 'Renta de noviembre',
    amount = '1250.50',
  }: { category?: string; description?: string; amount?: string } = {},
) {
  setFieldValue(labelled(root, 'Category'), category);
  setFieldValue(labelled(root, 'Description'), description);
  setFieldValue(labelled(root, 'Amount'), amount);
}

const submit = () => click(buttonNamed(dialog()!, 'Record expense'));
const resend = () => click(buttonNamed(dialog()!, 'Resend the same expense'));

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
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('rules', () => {
  it('reads the URL and writes it back, ignoring anything invalid', () => {
    const params = expenseListParams(
      new URLSearchParams(
        `status=void&status=overdue&status=bogus&category=${CAT_RENT.id}&vendor=x&from=2026-10-01&to=2026-13-01&q=%20luz%20&sort=due_on&page=2`,
      ),
    );
    expect(params).toEqual({
      statuses: ['overdue', 'void'],
      categoryId: CAT_RENT.id,
      vendorId: '',
      from: '2026-10-01',
      to: '',
      q: 'luz',
      sort: 'due_on',
      page: 2,
    });
    expect(expenseListSearch(params)).toBe(
      `status=overdue&status=void&category=${CAT_RENT.id}&from=2026-10-01&q=luz&sort=due_on&page=2`,
    );
  });

  it('the create body: trimmed texts, blank optionals as null', () => {
    expect(
      expenseCreateBody({
        ...emptyExpenseForm(TODAY),
        categoryId: CAT_RENT.id,
        description: '  Renta ',
        amount: ' 100 ',
        receiptReference: '   ',
        note: ' ',
      }),
    ).toEqual({
      category_id: CAT_RENT.id,
      vendor_id: null,
      description: 'Renta',
      amount: '100',
      incurred_on: '2026-10-20',
      due_on: '2026-10-20',
      receipt_reference: null,
      note: null,
    });
  });

  it('the create checks: future date, due before date, code points', () => {
    expect(
      validateExpenseForm(
        {
          ...emptyExpenseForm(TODAY),
          incurredOn: '2026-10-21',
          dueOn: '2026-10-19',
          description: 'x'.repeat(501),
          receiptReference: '😀'.repeat(100),
        },
        TODAY,
      ),
    ).toEqual({
      categoryId: 'finance.expenses.validation.categoryRequired',
      description: 'finance.expenses.validation.descriptionTooLong',
      amount: 'finance.expenses.validation.amountRequired',
      incurredOn: 'errors:incurred_on_in_future',
      dueOn: 'errors:due_on_before_incurred_on',
    });
  });

  it('an adjustment is signed by its direction; a whole reduction is a void', () => {
    expect(
      adjustmentBody({ direction: 'decrease', amount: '12.5', reason: ' x ' }),
    ).toEqual({ amount: '-12.50', reason: 'x' });
    expect(
      adjustmentBody({ direction: 'increase', amount: '3', reason: 'y' }),
    ).toEqual({ amount: '3.00', reason: 'y' });
    const expense = {
      adjusted_amount: '1000.00',
      open_amount: '600.00',
      payments: [],
    };
    expect(
      validateAdjustmentForm(
        { direction: 'decrease', amount: '600.01', reason: 'r' },
        expense,
      ).amount,
    ).toBe('finance.expenses.validation.reductionAboveOpen');
    expect(
      validateAdjustmentForm(
        { direction: 'decrease', amount: '1000', reason: 'r' },
        { ...expense, open_amount: '1000.00' },
      ).amount,
    ).toBe('finance.expenses.validation.reductionIsVoid');
  });
});

describe('the list (B1)', () => {
  it('shows each column, the five status badges and inactive rows', async () => {
    stub();
    await openList();
    const rows = [...table()!.querySelectorAll('tbody tr')];
    expect(rows).toHaveLength(5);
    expect(
      [...rows[0]!.querySelectorAll('td')].map((cell) => cell.textContent),
    ).toEqual([
      'Oct 10, 2026',
      'Rent',
      'Papelería Ejemplo',
      'Gasto de prueba 1',
      '$1,000.00',
      '$1,000.00',
      'Oct 30, 2026',
      'Unpaid',
    ]);
    expect(
      rows.map(
        (row) =>
          row.querySelector('[data-slot="expense-status-badge"]')?.textContent,
      ),
    ).toEqual(['Unpaid', 'Partly paid', 'Overdue', 'Paid', 'Void']);
    const overdue = table()!.querySelector(
      `tr[data-expense-id="${EXPENSE_OVERDUE.id}"]`,
    )!;
    expect(overdue.textContent).toContain('CafeteríaInactive');
    expect(overdue.textContent).toContain('Agua Purificada DemoInactive');
    expect(
      table()!
        .querySelector(`tr[data-expense-id="${EXPENSE_UNPAID.id}"] a`)
        ?.getAttribute('href'),
    ).toBe(`/expenses/records/${EXPENSE_UNPAID.id}`);
    expect(main().querySelectorAll('[data-expense-card]')).toHaveLength(5);
  });

  it('reads Spanish: keyed labels and badges change, descriptions do not', async () => {
    applyLanguage('es');
    stub();
    await openList();
    const first = table()!.querySelector('tbody tr')!;
    expect(first.textContent).toContain('Renta');
    expect(first.textContent).toContain('Sin pagar');
    expect(first.textContent).toContain('Gasto de prueba 1');
    expect(buttonNamed(main(), 'Nuevo gasto')).toBeDefined();
  });

  it('A3: "New expense" for Owner and Finance, not for a view-only user', async () => {
    stub({}, FINANCE);
    await openList();
    expect(addButton()).toBeDefined();
    app!.unmount();
    stub({}, VIEWER);
    await openList();
    expect(() => addButton()).toThrow();
  });
});

describe('filters, sort and pages (B2)', () => {
  it('a status, a category, a vendor, the dates and the sort go to the URL and the request', async () => {
    stub();
    await openList();
    await click(
      [
        ...main().querySelectorAll<HTMLInputElement>('input[type=checkbox]'),
      ][2]!,
    );
    await settleUntil(() => app!.location().includes('status=overdue'));
    setFieldValue(labelled(main(), 'Category'), CAT_RETIRED.id);
    await settle();
    setFieldValue(labelled(main(), 'Vendor'), VENDOR_RETIRED.id);
    await settle();
    setFieldValue(labelled(main(), 'Incurred from'), '2026-09-01');
    await settle();
    setFieldValue(labelled(main(), 'Incurred to'), '2026-09-30');
    await settle();
    setFieldValue(labelled(main(), 'Sort by'), 'due_on');
    await settleUntil(() => app!.location().includes('sort=due_on'));
    expect(app!.location()).toBe(
      `/expenses/records?status=overdue&category=${CAT_RETIRED.id}&vendor=${VENDOR_RETIRED.id}&from=2026-09-01&to=2026-09-30&sort=due_on`,
    );
    await settleUntil(() =>
      listQueries().some((path) => path.includes('sort=due_on')),
    );
    const last = new URLSearchParams(listQueries().at(-1));
    expect(last.getAll('status')).toEqual(['overdue']);
    expect(last.get('category_id')).toBe(CAT_RETIRED.id);
    expect(last.get('vendor_id')).toBe(VENDOR_RETIRED.id);
    expect(last.get('incurred_from')).toBe('2026-09-01');
    expect(last.get('incurred_to')).toBe('2026-09-30');
    // Inactive categories and vendors are offered as filters.
    expect(
      labelled<HTMLSelectElement>(main(), 'Category').textContent,
    ).toContain('Cafetería (inactive)');
  });

  it('the search waits 300 ms, then writes q and goes back to page 1', async () => {
    stub({
      [EXPENSES]: page(expensePage(EXPENSE_ITEMS, { total: 60 })),
    });
    await openList('/expenses/records?page=2');
    setFieldValue(
      labelled(main(), 'Search the description or the receipt'),
      ' luz ',
    );
    await settle();
    expect(app!.location()).toBe('/expenses/records?page=2');
    await settleUntil(() => app!.location() === '/expenses/records?q=luz');
    await settleUntil(() =>
      listQueries().some((path) => path.includes('q=luz')),
    );
    expect(listQueries().at(-1)).toContain('offset=0');
  });

  it('pagination asks for the next offset', async () => {
    stub({
      [EXPENSES]: page(expensePage(EXPENSE_ITEMS, { total: 60 })),
    });
    await openList();
    await click(buttonNamed(main(), 'Next'));
    await settleUntil(() => app!.location() === '/expenses/records?page=2');
    await settleUntil(() =>
      listQueries().some((path) => path.includes('offset=25')),
    );
  });

  it('a "to" before "from" says so and asks nothing', async () => {
    const api = stub();
    await renderApp('/expenses/records?from=2026-10-10&to=2026-10-01').then(
      (rendered) => (app = rendered),
    );
    await settleUntil(() =>
      main().textContent.includes(
        'The end of the range must be after its start.',
      ),
    );
    expect(api.calls(EXPENSES)).toHaveLength(0);
  });

  it('empty: no expenses yet with "New expense"; no match when filtered', async () => {
    stub({ [EXPENSES]: () => jsonResponse(200, expensePage([])) });
    await openList();
    const empty = main().querySelector('[data-expenses-empty]')!;
    expect(empty.textContent).toContain('There are no expenses yet.');
    expect(buttonNamed(empty, 'New expense')).toBeDefined();
    app!.unmount();
    stub({ [EXPENSES]: () => jsonResponse(200, expensePage([])) });
    await openList('/expenses/records?status=void');
    expect(main().querySelector('[data-expenses-no-match]')?.textContent).toBe(
      'No expense matches the filters.',
    );
  });

  it('B3: a failed refetch keeps the rows with the out-of-date alert', async () => {
    const api = stub({
      [EXPENSES]: [
        () => jsonResponse(200, expensePage()),
        () => problem(400, 'database_unavailable'),
        () => jsonResponse(200, expensePage()),
      ],
    });
    await openList();
    await app!.queryClient.invalidateQueries({
      queryKey: ['finance', 'expenses'],
    });
    await settleUntil(() => alerts(main()).length > 0);
    expect(alerts(main()).join()).toContain(
      "The list couldn't be refreshed and may be out of date.",
    );
    expect(table()!.querySelectorAll('tbody tr')).toHaveLength(5);
    await click(buttonNamed(main(), 'Reload the list'));
    await settleUntil(() => alerts(main()).length === 0);
    expect(api.calls(EXPENSES).length).toBe(3);
  });
});

describe('"New expense" (D1)', () => {
  it('offers active categories (keyed labels) and active vendors only', async () => {
    stub();
    await openCreate();
    const categories = [
      ...labelled<HTMLSelectElement>(dialog()!, 'Category').options,
    ].map((option) => option.text);
    expect(categories).toEqual([
      'Choose…',
      'Equipment',
      'Luz y agua',
      'Maintenance',
      'Publicidad',
      'Rent',
      'Supplies',
    ]);
    const vendors = [
      ...labelled<HTMLSelectElement>(dialog()!, 'Vendor (optional)').options,
    ].map((option) => option.text);
    expect(vendors).not.toContain(VENDOR_RETIRED.name);
    expect(vendors).toContain(VENDOR_PAPER.name);
    // Today in the center by default; the due date follows it.
    expect(labelled<HTMLInputElement>(dialog()!, 'Expense date').value).toBe(
      '2026-10-20',
    );
    setFieldValue(labelled(dialog()!, 'Expense date'), '2026-10-05');
    expect(labelled<HTMLInputElement>(dialog()!, 'Due date').value).toBe(
      '2026-10-05',
    );
  });

  it('posts the body with a key, then opens the new expense', async () => {
    const api = stub({
      [EXPENSE_POST]: () =>
        jsonResponse(201, { id: CREATED_ID, status: 'unpaid' }),
    });
    await openCreate();
    fill(dialog()!);
    setFieldValue(labelled(dialog()!, 'Vendor (optional)'), VENDOR_PAPER.id);
    setFieldValue(
      labelled(dialog()!, 'Receipt or invoice number (optional)'),
      ' F-77 ',
    );
    await submit();
    await settleUntil(
      () => app!.location() === `/expenses/records/${CREATED_ID}`,
    );
    const [call] = api.calls(EXPENSE_POST);
    expect(call!.body).toEqual({
      category_id: CAT_RENT.id,
      vendor_id: VENDOR_PAPER.id,
      description: 'Renta de noviembre',
      amount: '1250.50',
      incurred_on: '2026-10-20',
      due_on: '2026-10-20',
      receipt_reference: 'F-77',
      note: null,
    });
    expect(call!.headers.get('Idempotency-Key')).toMatch(/^[0-9a-f-]{36}$/);
    await settleUntil(() => main().textContent.includes('Expense recorded.'));
  });

  it('client checks block the post', async () => {
    const api = stub();
    await openCreate();
    setFieldValue(labelled(dialog()!, 'Expense date'), '2026-10-21');
    await submit();
    const text = dialog()!.textContent;
    expect(text).toContain('Choose the category.');
    expect(text).toContain('Enter the description.');
    expect(text).toContain('Enter the amount.');
    expect(text).toContain("The expense date can't be after today.");
    expect(api.calls(EXPENSE_POST)).toHaveLength(0);
  });

  it.each([
    [
      'incurred_on_in_future',
      'incurred_on',
      "The expense date can't be after today.",
    ],
    [
      'due_on_before_incurred_on',
      'due_on',
      "The due date can't be before the expense date.",
    ],
    ['amount_not_positive', 'amount', 'Enter an amount greater than 0.'],
    ['money_precision', 'amount', 'An amount has at most 2 decimal places.'],
    ['text_too_long', 'description', 'This text is too long.'],
    [
      'text_invalid',
      'note',
      'This text has an invalid character. Enter it again.',
    ],
  ])('a 422 %s goes on its field', async (code, field, text) => {
    stub({
      [EXPENSE_POST]: () =>
        problemWith(
          422,
          code === 'incurred_on_in_future' ||
            code === 'due_on_before_incurred_on'
            ? code
            : 'validation_error',
          {
            errors: [{ path: ['body', field], code }],
          },
        ),
    });
    await openCreate();
    fill(dialog()!);
    await submit();
    await settleUntil(() => dialog()!.textContent.includes(text));
    expect(alerts(dialog()!)).toEqual([]);
    // A settled refusal leaves the fields editable.
    expect(labelled<HTMLInputElement>(dialog()!, 'Description').disabled).toBe(
      false,
    );
  });

  it.each([
    ['expense_category_inactive', 'Category', 'This category is inactive.'],
    ['expense_category_unknown', 'Category', ''],
    ['vendor_inactive', 'Vendor (optional)', 'This vendor is inactive.'],
    ['vendor_unknown', 'Vendor (optional)', ''],
  ])('%s reloads the pickers and goes on its field', async (code) => {
    const api = stub({
      [EXPENSE_POST]: () => problem(code.endsWith('unknown') ? 422 : 409, code),
    });
    await openCreate();
    fill(dialog()!);
    const before = {
      categories: api.calls(CATEGORIES).length,
      vendors: api.calls(VENDORS).length,
    };
    await submit();
    await settleUntil(
      () =>
        api.calls(CATEGORIES).length > before.categories &&
        api.calls(VENDORS).length > before.vendors,
    );
    await settleUntil(() =>
      dialog()!.textContent.includes(
        'The categories and vendors were reloaded.',
      ),
    );
    const invalid = dialog()!.querySelector('[aria-invalid="true"]');
    expect(invalid?.tagName).toBe('SELECT');
  });

  it.each([
    [
      'idempotency_key_reused',
      'This submission was already used for different data.',
    ],
    ['permission_denied', ''],
  ])('%s is an alert', async (code, text) => {
    stub({
      [EXPENSE_POST]: () =>
        problem(code === 'permission_denied' ? 403 : 422, code),
    });
    await openCreate();
    fill(dialog()!);
    await submit();
    await settleUntil(() => alerts(dialog()!).length > 0);
    expect(alerts(dialog()!).join()).toContain(text);
  });
});

describe('a lost answer (D6, E2 (a))', () => {
  it('locks the fields; the resend posts the stored body with the stored key', async () => {
    const api = stub({
      [EXPENSE_POST]: [
        lost,
        () => problem(502, 'internal_error'),
        () => jsonResponse(201, { id: CREATED_ID, status: 'unpaid' }),
      ],
    });
    await openCreate();
    fill(dialog()!);
    await submit();
    await settleUntil(() => alerts(dialog()!).length > 0);
    expect(alerts(dialog()!).join()).toContain(
      "We don't know whether the expense was recorded.",
    );
    for (const label of ['Category', 'Description', 'Amount', 'Expense date']) {
      expect(labelled<HTMLInputElement>(dialog()!, label).disabled).toBe(true);
    }
    await resend();
    // A second lost answer keeps the state.
    await settleUntil(() => api.calls(EXPENSE_POST).length === 2);
    expect(buttonNamed(dialog()!, 'Resend the same expense')).toBeDefined();
    await resend();
    await settleUntil(
      () => app!.location() === `/expenses/records/${CREATED_ID}`,
    );
    const [a, b, c] = keys(api);
    expect(b).toBe(a);
    expect(c).toBe(a);
    const bodies = api.calls(EXPENSE_POST).map((call) => call.body);
    expect(bodies[1]).toEqual(bodies[0]);
    expect(bodies[2]).toEqual(bodies[0]);
  });

  it('a settled refusal after a lost answer unlocks the fields', async () => {
    stub({
      [EXPENSE_POST]: [lost, () => problem(409, 'expense_category_inactive')],
    });
    await openCreate();
    fill(dialog()!);
    await submit();
    await settleUntil(() => alerts(dialog()!).length > 0);
    await resend();
    await settleUntil(
      () =>
        labelled<HTMLInputElement>(dialog()!, 'Description').disabled === false,
    );
    expect(buttonNamed(dialog()!, 'Record expense')).toBeDefined();
  });

  it.each(closeRoutes)(
    'closing by %s refetches the list and holds "New expense" until it lands',
    async (_route, close) => {
      let release: () => void = () => {};
      let held = false;
      const api = stub({
        [EXPENSE_POST]: lost,
        [EXPENSES]: () =>
          held
            ? new Promise<Response>((resolve) => {
                release = () => resolve(jsonResponse(200, expensePage()));
              })
            : jsonResponse(200, expensePage()),
      });
      await openCreate();
      fill(dialog()!);
      await submit();
      await settleUntil(() => alerts(dialog()!).length > 0);
      const before = api.calls(EXPENSES).length;
      held = true;
      await close(dialog()!);
      await settleUntil(() => !dialog());
      await settleUntil(() => api.calls(EXPENSES).length > before);
      expect(addButton().disabled).toBe(true);
      expect(said()).toBe(
        'The expense may already have been recorded. Check the list before entering it again.',
      );
      release();
      await settleUntil(() => !addButton().disabled);
      // A new opening is a new key and empty fields.
      await click(addButton());
      await settleUntil(() => dialog()?.querySelector('form'));
      expect(labelled<HTMLInputElement>(dialog()!, 'Description').value).toBe(
        '',
      );
      fill(dialog()!);
      api.set({
        [EXPENSE_POST]: () =>
          jsonResponse(201, { id: CREATED_ID, status: 'unpaid' }),
      });
      await submit();
      await settleUntil(() => api.calls(EXPENSE_POST).length === 2);
      const [first, second] = keys(api);
      expect(second).not.toBe(first);
    },
  );

  it('a failed refetch after close shows the out-of-date alert and keeps "New expense" disabled', async () => {
    let failing = false;
    stub({
      [EXPENSE_POST]: lost,
      [EXPENSES]: () =>
        failing
          ? problem(400, 'database_unavailable')
          : jsonResponse(200, expensePage()),
    });
    await openCreate();
    fill(dialog()!);
    await submit();
    await settleUntil(() => alerts(dialog()!).length > 0);
    failing = true;
    await click(buttonNamed(dialog()!, 'Cancel'));
    await settleUntil(() => alerts(main()).length > 0);
    expect(alerts(main()).join()).toContain('may be out of date');
    expect(main().textContent).not.toMatch(/was reloaded/);
    expect(addButton().disabled).toBe(true);
    failing = false;
    await click(buttonNamed(main(), 'Reload the list'));
    await settleUntil(() => !addButton().disabled);
  });

  it('a close with no attempt makes no request', async () => {
    const api = stub();
    await openCreate();
    const before = api.requests.length;
    await click(buttonNamed(dialog()!, 'Cancel'));
    await settle();
    expect(api.requests.length).toBe(before);
    expect(said()).toBe('');
  });
});

describe('generated expenses in the list (M7-13b E1)', () => {
  it('marks rows with a template, with an accessible label', async () => {
    stub({
      [EXPENSES]: page(expensePage([...GENERATED_EXPENSES, ...EXPENSE_ITEMS])),
    });
    await openList();
    const markers = [...table()!.querySelectorAll('[data-recurring-marker]')];
    expect(markers).toHaveLength(2);
    expect(markers[0]!.getAttribute('role')).toBe('img');
    expect(markers[0]!.getAttribute('aria-label')).toBe('Recurring');
    expect(
      main().querySelectorAll('[data-expenses-cards] [data-recurring-marker]'),
    ).toHaveLength(2);
  });
});
