import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { problemWith } from '@/features/academics/testing';
import type { SessionUserBody } from '@/features/auth/session';
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
  EXPENSE_GENERATED,
  EXPENSE_METHODS,
  EXPENSE_PAID,
  EXPENSE_PARTIAL,
  EXPENSE_UNPAID,
  EXPENSE_VOID,
  expensePage,
  PAYMENT_VALID,
} from '../fixtures';
import type { ExpenseDetail } from '../api';
import {
  ADMIN_REFUNDS,
  adjustmentPost,
  EXPENSES,
  expenseGet,
  FINANCE,
  METHODS,
  OWNER,
  paymentPost,
  REAUTH,
  reversalPost,
} from '../testing';

// M7-12b A3, C, D2-D7: the detail page per role, the four dialogs' success
// and mapped refusals, the password then the same key, and the locked
// resend after a lost answer with the three close routes.

const VIEWER: SessionUserBody = {
  ...FINANCE,
  permissions: ['expense.configure', 'expense.view'],
};
const lost = () => Promise.reject(new TypeError('Failed to fetch'));
const ok =
  (status = 'partially_paid') =>
  () =>
    jsonResponse(201, {
      id: '84900000-0000-4000-8000-000000000001',
      expense_id: EXPENSE_UNPAID.id,
      status,
    });

let app: RenderedApp | undefined;
const main = () => app!.container.querySelector('main')!;
const head = () => main().querySelector('[data-expense-head]');
const dialog = (kind: string) =>
  document.querySelector<HTMLElement>(`[data-expense-dialog="${kind}"]`);
const said = () =>
  main().querySelector('[data-expense-announcement]')?.textContent ?? '';
const action = (name: string) => {
  const group = main().querySelector('[role="group"]');
  return group
    ? ([...group.querySelectorAll('button')].find(
        (button) => button.textContent === name,
      ) ?? null)
    : null;
};
const reverseButtons = () => [
  ...main().querySelectorAll<HTMLButtonElement>(
    '[data-payments-table] [data-reverse-payment]',
  ),
];
const keys = (api: ApiStub, route: string) =>
  api.calls(route).map((call) => call.headers.get('Idempotency-Key'));

function stub(
  expense: ExpenseDetail = EXPENSE_UNPAID,
  routes: Record<string, Responder | Responder[]> = {},
  as: SessionUserBody = OWNER,
): ApiStub {
  return stubApi({
    'GET /api/v1/auth/me': meAs(as),
    [expenseGet(expense.id)]: () => jsonResponse(200, expense),
    [EXPENSES]: () => jsonResponse(200, expensePage()),
    [METHODS]: () => jsonResponse(200, EXPENSE_METHODS),
    [REAUTH]: () =>
      jsonResponse(200, { reauthenticated_until: '2026-10-20T18:05:00Z' }),
    ...routes,
  });
}

async function openDetail(expense: ExpenseDetail = EXPENSE_UNPAID) {
  app = await renderApp(`/expenses/records/${expense.id}`);
  await settleUntil(
    () => head() ?? main().querySelector('[data-expense-not-found]'),
  );
}

async function openDialog(
  name: string,
  kind: string,
  expense = EXPENSE_UNPAID,
) {
  await openDetail(expense);
  await click(action(name)!);
  await settleUntil(() => dialog(kind)?.querySelector('form'));
}

async function confirmPassword() {
  await settleUntil(() =>
    [...document.querySelectorAll<HTMLElement>('[role="dialog"]')].some(
      (d) => d.querySelector('h2')?.textContent === 'Confirm your password',
    ),
  );
  const password = [
    ...document.querySelectorAll<HTMLElement>('[role="dialog"]'),
  ].find(
    (d) => d.querySelector('h2')?.textContent === 'Confirm your password',
  )!;
  setFieldValue(labelled(password, 'Password'), 'my-own-password');
  await click(buttonNamed(password, 'Confirm'));
}

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

describe('the detail (C1, C2)', () => {
  it('shows the figures, the payments with a reversed one, and adjustments', async () => {
    stub(EXPENSE_PARTIAL);
    await openDetail(EXPENSE_PARTIAL);
    const field = (key: string) =>
      head()!.querySelector(`[data-expense-field="${key}"] dd`)?.textContent;
    expect(field('category')).toBe('Utilities');
    expect(field('vendor')).toBe('—');
    expect(field('net')).toBe('$1,000.00');
    expect(field('paid')).toBe('$400.00');
    expect(field('open')).toBe('$600.00');
    expect(field('status')).toBe('Partly paid');
    expect(field('createdBy')).toContain('Demo Owner');
    const rows = [...main().querySelectorAll('[data-payments-table] tbody tr')];
    expect(rows).toHaveLength(2);
    const reversed = rows[1]!;
    expect(reversed.getAttribute('data-reversed')).toBe('true');
    expect(reversed.querySelector('td:nth-child(2)')?.className).toContain(
      'line-through',
    );
    expect(reversed.textContent).toContain('Reversed by Demo Owner');
    expect(reversed.textContent).toContain('Reason: Monto equivocado');
    expect(main().querySelector('[data-adjustments-empty]')).not.toBeNull();
  });

  it('a void expense shows its adjustment and offers nothing', async () => {
    stub(EXPENSE_VOID);
    await openDetail(EXPENSE_VOID);
    expect(main().querySelector('[data-adjustment-id]')?.textContent).toContain(
      '−$1,000.00',
    );
    expect(main().querySelector('[role="group"]')).toBeNull();
  });

  it('a 404 shows the not-found state with a link back', async () => {
    stub(EXPENSE_UNPAID, {
      [expenseGet(EXPENSE_UNPAID.id)]: () => problem(404, 'not_found'),
    });
    await openDetail();
    const gone = main().querySelector('[data-expense-not-found]')!;
    expect(gone.textContent).toContain("This expense doesn't exist.");
    expect(gone.querySelector('a')?.getAttribute('href')).toBe(
      '/expenses/records',
    );
  });

  it('A3: a 403 shows the forbidden page', async () => {
    stub(EXPENSE_UNPAID, {
      [expenseGet(EXPENSE_UNPAID.id)]: () => problem(403, 'permission_denied'),
    });
    app = await renderApp(`/expenses/records/${EXPENSE_UNPAID.id}`);
    await settleUntil(
      () =>
        main().querySelector('h1')?.textContent ===
        "You don't have access to this page",
    );
  });

  it('C3: a failed refetch keeps the expense, holds the actions, reloads', async () => {
    stub(EXPENSE_PARTIAL, {
      [expenseGet(EXPENSE_PARTIAL.id)]: [
        () => jsonResponse(200, EXPENSE_PARTIAL),
        () => problem(400, 'database_unavailable'),
        () => jsonResponse(200, EXPENSE_PARTIAL),
      ],
    });
    await openDetail(EXPENSE_PARTIAL);
    await app!.queryClient.invalidateQueries({
      queryKey: ['finance', 'expenses'],
    });
    await settleUntil(() => alerts(main()).length > 0);
    expect(alerts(main()).join()).toContain('may be out of date');
    expect(head()).not.toBeNull();
    expect(action('Record payment')!.disabled).toBe(true);
    await click(buttonNamed(main(), 'Reload'));
    await settleUntil(() => alerts(main()).length === 0);
    expect(action('Record payment')!.disabled).toBe(false);
  });
});

describe('who may do what (A3, E3)', () => {
  it.each([
    ['the Owner', OWNER, ['Record payment', 'Adjust', 'Void'], 1],
    ['Finance Staff', FINANCE, ['Record payment', 'Adjust', 'Void'], 0],
    ['a view-only user', VIEWER, [], 0],
  ] as const)('%s', async (_who, as, buttons, reverses) => {
    stub(EXPENSE_PARTIAL, {}, as);
    await openDetail(EXPENSE_PARTIAL);
    const group = main().querySelector('[role="group"]');
    expect(
      group
        ? [...group.querySelectorAll('button')].map((b) => b.textContent)
        : [],
    ).toEqual(buttons);
    expect(reverseButtons()).toHaveLength(reverses);
  });

  it('an Administrator delegated refunds is forbidden on the page', async () => {
    const api = stub(EXPENSE_PARTIAL, {}, ADMIN_REFUNDS);
    app = await renderApp(`/expenses/records/${EXPENSE_PARTIAL.id}`);
    await settleUntil(
      () =>
        main().querySelector('h1')?.textContent ===
        "You don't have access to this page",
    );
    expect(api.calls(expenseGet(EXPENSE_PARTIAL.id))).toHaveLength(0);
  });

  it('void is enabled only with no valid payment', async () => {
    stub(EXPENSE_PARTIAL);
    await openDetail(EXPENSE_PARTIAL);
    expect(action('Void')!.disabled).toBe(true);
    expect(main().textContent).toContain('Reverse the payments first.');
    app!.unmount();
    stub(EXPENSE_UNPAID);
    await openDetail();
    expect(action('Void')!.disabled).toBe(false);
    expect(main().textContent).not.toContain('Reverse the payments first.');
  });

  it('a paid expense: no "Record payment", adjust still offered', async () => {
    stub(EXPENSE_PAID);
    await openDetail(EXPENSE_PAID);
    expect(action('Record payment')).toBeNull();
    expect(action('Adjust')).not.toBeNull();
  });
});

describe('record payment (D2)', () => {
  it('defaults, success body and key, refetch and the status', async () => {
    const api = stub(EXPENSE_PARTIAL, {
      [paymentPost(EXPENSE_PARTIAL.id)]: ok(),
    });
    await openDialog('Record payment', 'payment', EXPENSE_PARTIAL);
    const d = dialog('payment')!;
    expect(d.textContent).toContain('Open: $600.00');
    expect(labelled<HTMLInputElement>(d, 'Amount').value).toBe('600.00');
    expect(labelled<HTMLInputElement>(d, 'Payment date').value).toBe(
      '2026-10-20',
    );
    const methods = [...labelled<HTMLSelectElement>(d, 'Method').options].map(
      (o) => o.text,
    );
    expect(methods).toEqual(['Choose…', 'Cash', 'Transfer']);
    setFieldValue(labelled(d, 'Amount'), '200');
    setFieldValue(labelled(d, 'Method'), 'cash');
    setFieldValue(labelled(d, 'Payment date'), '2026-09-01');
    const before = api.calls(expenseGet(EXPENSE_PARTIAL.id)).length;
    await click(buttonNamed(d, 'Record payment'));
    await settleUntil(() => !dialog('payment'));
    const [call] = api.calls(paymentPost(EXPENSE_PARTIAL.id));
    expect(call!.body).toEqual({
      paid_on: '2026-09-01',
      amount: '200',
      method_code: 'cash',
      reference: null,
      note: null,
    });
    expect(call!.headers.get('Idempotency-Key')).toMatch(/^[0-9a-f-]{36}$/);
    await settleUntil(() => said() === 'Payment recorded.');
    expect(api.calls(expenseGet(EXPENSE_PARTIAL.id)).length).toBeGreaterThan(
      before,
    );
  });

  it('client checks: above open, a future date, no method', async () => {
    const api = stub();
    await openDialog('Record payment', 'payment');
    const d = dialog('payment')!;
    setFieldValue(labelled(d, 'Amount'), '1000.01');
    setFieldValue(labelled(d, 'Payment date'), '2026-10-21');
    await click(buttonNamed(d, 'Record payment'));
    expect(d.textContent).toContain(
      "The amount can't be more than what's open.",
    );
    expect(d.textContent).toContain("The payment date can't be after today.");
    expect(d.textContent).toContain('Choose the method.');
    expect(api.calls(paymentPost(EXPENSE_UNPAID.id))).toHaveLength(0);
  });

  it('a duplicate refetches and names the stored payment', async () => {
    const withPayment = { ...EXPENSE_UNPAID, payments: [PAYMENT_VALID] };
    const api = stub(EXPENSE_UNPAID, {
      [expenseGet(EXPENSE_UNPAID.id)]: [
        () => jsonResponse(200, EXPENSE_UNPAID),
        () => jsonResponse(200, withPayment),
      ],
      [paymentPost(EXPENSE_UNPAID.id)]: () =>
        problemWith(409, 'expense_payment_duplicate', {
          details: { payment_id: PAYMENT_VALID.id },
        }),
    });
    await openDialog('Record payment', 'payment');
    const d = dialog('payment')!;
    setFieldValue(labelled(d, 'Method'), 'transfer');
    setFieldValue(labelled(d, 'Reference (optional)'), 'SPEI 1');
    await click(buttonNamed(d, 'Record payment'));
    await settleUntil(() => alerts(d).length > 0);
    expect(alerts(d).join()).toContain(
      'This payment is already recorded: Oct 15, 2026, $400.00, Transfer SPEI 1.',
    );
    expect(api.calls(expenseGet(EXPENSE_UNPAID.id)).length).toBe(2);
  });

  it('the cap shows details.open', async () => {
    stub(EXPENSE_UNPAID, {
      [paymentPost(EXPENSE_UNPAID.id)]: () =>
        problemWith(409, 'expense_payment_exceeds_open', {
          details: { open: '250.00' },
        }),
    });
    await openDialog('Record payment', 'payment');
    const d = dialog('payment')!;
    setFieldValue(labelled(d, 'Method'), 'cash');
    await click(buttonNamed(d, 'Record payment'));
    await settleUntil(() => alerts(d).length > 0);
    expect(alerts(d).join()).toContain(
      "The payment is more than what's open ($250.00).",
    );
  });

  it.each([
    [
      'paid_on_in_future',
      'paid_on',
      'Payment date',
      "The payment date can't be after today.",
    ],
    [
      'payment_method_unavailable',
      'method_code',
      'Method',
      "This payment method isn't available any more.",
    ],
  ])('%s goes on its field', async (code, path, label, text) => {
    const api = stub(EXPENSE_UNPAID, {
      [paymentPost(EXPENSE_UNPAID.id)]: () =>
        code === 'paid_on_in_future'
          ? problemWith(422, code, { errors: [{ path: ['body', path], code }] })
          : problem(422, code),
    });
    await openDialog('Record payment', 'payment');
    const d = dialog('payment')!;
    setFieldValue(labelled(d, 'Method'), 'cash');
    const methodsBefore = api.calls(METHODS).length;
    await click(buttonNamed(d, 'Record payment'));
    await settleUntil(() => d.textContent.includes(text));
    expect(labelled(d, label).getAttribute('aria-invalid')).toBe('true');
    if (code === 'payment_method_unavailable') {
      await settleUntil(() => api.calls(METHODS).length > methodsBefore);
    }
  });

  it('D7: expense_void refetches the expense and says it is void', async () => {
    const api = stub(EXPENSE_UNPAID, {
      [paymentPost(EXPENSE_UNPAID.id)]: () => problem(409, 'expense_void'),
    });
    await openDialog('Record payment', 'payment');
    const d = dialog('payment')!;
    setFieldValue(labelled(d, 'Method'), 'cash');
    await click(buttonNamed(d, 'Record payment'));
    await settleUntil(() => alerts(d).length > 0);
    expect(alerts(d).join()).toContain('This expense is void');
    expect(api.calls(expenseGet(EXPENSE_UNPAID.id)).length).toBe(2);
  });
});

describe('adjust (D3) and void (D4)', () => {
  it('a reduction posts a negative amount after the password, with the same key', async () => {
    const api = stub(EXPENSE_UNPAID, {
      [adjustmentPost(EXPENSE_UNPAID.id)]: [
        () => problem(403, 'reauth_required'),
        ok('unpaid'),
      ],
    });
    await openDialog('Adjust', 'adjust');
    const d = dialog('adjust')!;
    expect(d.textContent).toContain(
      'Amount: $1,000.00. Paid: $0.00. Open: $1,000.00.',
    );
    await click(d.querySelector<HTMLInputElement>('input[value="decrease"]')!);
    setFieldValue(labelled(d, 'By how much'), '100');
    setFieldValue(labelled(d, 'Reason'), ' Descuento ');
    await click(buttonNamed(d, 'Save adjustment'));
    await confirmPassword();
    await settleUntil(() => said() === 'Adjustment saved.');
    const [first, second] = api.calls(adjustmentPost(EXPENSE_UNPAID.id));
    expect(first!.body).toEqual({ amount: '-100.00', reason: 'Descuento' });
    expect(second!.body).toEqual(first!.body);
    expect(second!.headers.get('Idempotency-Key')).toBe(
      first!.headers.get('Idempotency-Key'),
    );
  });

  it('client checks: a direction, a reason, no reduction beyond open or a whole void', async () => {
    const api = stub(EXPENSE_PARTIAL);
    await openDialog('Adjust', 'adjust', EXPENSE_PARTIAL);
    const d = dialog('adjust')!;
    setFieldValue(labelled(d, 'By how much'), '700');
    await click(buttonNamed(d, 'Save adjustment'));
    expect(d.textContent).toContain(
      'Choose whether to increase or reduce the amount.',
    );
    expect(d.textContent).toContain('Enter the reason.');
    await click(d.querySelector<HTMLInputElement>('input[value="decrease"]')!);
    setFieldValue(labelled(d, 'Reason'), 'r');
    await click(buttonNamed(d, 'Save adjustment'));
    expect(d.textContent).toContain("You can reduce at most what's open.");
    expect(api.calls(adjustmentPost(EXPENSE_PARTIAL.id))).toHaveLength(0);
  });

  it('expense_adjustment_exceeds_open shows details.open and the hint', async () => {
    stub(EXPENSE_UNPAID, {
      [adjustmentPost(EXPENSE_UNPAID.id)]: () =>
        problemWith(409, 'expense_adjustment_exceeds_open', {
          details: { open: '50.00' },
        }),
    });
    await openDialog('Adjust', 'adjust');
    const d = dialog('adjust')!;
    await click(d.querySelector<HTMLInputElement>('input[value="decrease"]')!);
    setFieldValue(labelled(d, 'By how much'), '100');
    setFieldValue(labelled(d, 'Reason'), 'r');
    await click(buttonNamed(d, 'Save adjustment'));
    await settleUntil(() => alerts(d).length > 0);
    expect(alerts(d).join()).toContain(
      "You can reduce at most what's open ($50.00). To reduce more, reverse payments first.",
    );
  });

  it('amount_zero goes on the amount', async () => {
    stub(EXPENSE_UNPAID, {
      [adjustmentPost(EXPENSE_UNPAID.id)]: () =>
        problemWith(422, 'validation_error', {
          errors: [{ path: ['body', 'amount'], code: 'amount_zero' }],
        }),
    });
    await openDialog('Adjust', 'adjust');
    const d = dialog('adjust')!;
    await click(d.querySelector<HTMLInputElement>('input[value="increase"]')!);
    setFieldValue(labelled(d, 'By how much'), '1');
    setFieldValue(labelled(d, 'Reason'), 'r');
    await click(buttonNamed(d, 'Save adjustment'));
    await settleUntil(() =>
      d.textContent.includes('Enter an amount other than 0.'),
    );
  });

  it('void: minus the net, the reason, the final warning', async () => {
    const api = stub(EXPENSE_UNPAID, {
      [adjustmentPost(EXPENSE_UNPAID.id)]: ok('void'),
    });
    await openDialog('Void', 'void');
    const d = dialog('void')!;
    expect(d.textContent).toContain('A void is final');
    expect(d.textContent).toContain('Amount voided: $1,000.00');
    await click(buttonNamed(d, 'Void the expense'));
    expect(d.textContent).toContain('Enter the reason.');
    setFieldValue(labelled(d, 'Reason'), 'Categoría equivocada');
    await click(buttonNamed(d, 'Void the expense'));
    await settleUntil(() => said() === 'Expense voided.');
    expect(api.calls(adjustmentPost(EXPENSE_UNPAID.id))[0]!.body).toEqual({
      amount: '-1000.00',
      reason: 'Categoría equivocada',
    });
  });
});

describe('reverse a payment (D5)', () => {
  const route = reversalPost(PAYMENT_VALID.id);

  async function openReverse() {
    await openDetail(EXPENSE_PARTIAL);
    await click(reverseButtons()[0]!);
    await settleUntil(() => dialog('reverse')?.querySelector('form'));
  }

  it('the reason is required; the password, then the same key and reason', async () => {
    const api = stub(EXPENSE_PARTIAL, {
      [route]: [() => problem(403, 'reauth_required'), ok('unpaid')],
    });
    await openReverse();
    const d = dialog('reverse')!;
    expect(d.textContent).toContain(
      'Payment: Oct 15, 2026, $400.00, Transfer SPEI 1',
    );
    await click(buttonNamed(d, 'Reverse payment'));
    expect(d.textContent).toContain('Enter the reason.');
    expect(api.calls(route)).toHaveLength(0);
    setFieldValue(labelled(d, 'Reason'), ' Pago duplicado ');
    await click(buttonNamed(d, 'Reverse payment'));
    await confirmPassword();
    await settleUntil(() => said() === 'Payment reversed.');
    const [first, second] = api.calls(route);
    expect(first!.body).toEqual({ reason: 'Pago duplicado' });
    expect(second!.body).toEqual(first!.body);
    expect(second!.headers.get('Idempotency-Key')).toBe(
      first!.headers.get('Idempotency-Key'),
    );
  });

  it('already reversed refetches, then says so', async () => {
    const api = stub(EXPENSE_PARTIAL, {
      [route]: () => problem(409, 'expense_payment_already_reversed'),
    });
    await openReverse();
    const d = dialog('reverse')!;
    setFieldValue(labelled(d, 'Reason'), 'r');
    await click(buttonNamed(d, 'Reverse payment'));
    await settleUntil(() => alerts(d).length > 0);
    expect(alerts(d).join()).toContain('This payment was already reversed.');
    expect(api.calls(expenseGet(EXPENSE_PARTIAL.id)).length).toBe(2);
  });
});

it('D6: the resend posts the stored payment even after a refetch lowered what is open (QA #474 D1)', async () => {
  const api = stub(EXPENSE_UNPAID, {
    [paymentPost(EXPENSE_UNPAID.id)]: [lost, ok('paid')],
    [expenseGet(EXPENSE_UNPAID.id)]: [
      () => jsonResponse(200, EXPENSE_UNPAID),
      () => jsonResponse(200, { ...EXPENSE_PAID, id: EXPENSE_UNPAID.id }),
    ],
  });
  await openDialog('Record payment', 'payment');
  const d = dialog('payment')!;
  setFieldValue(labelled(d, 'Method'), 'cash');
  await click(buttonNamed(d, 'Record payment'));
  await settleUntil(() => alerts(d).length > 0);
  // The lost payment landed: a refetch now reads nothing open.
  await app!.queryClient.invalidateQueries({
    queryKey: ['finance', 'expenses', 'detail'],
  });
  await settleUntil(() => d.textContent.includes('Open: $0.00'));
  await click(buttonNamed(d, 'Resend the same payment'));
  await settleUntil(() => !dialog('payment'));
  const [first, second] = api.calls(paymentPost(EXPENSE_UNPAID.id));
  expect(second!.body).toEqual(first!.body);
  expect(second!.headers.get('Idempotency-Key')).toBe(
    first!.headers.get('Idempotency-Key'),
  );
});

// D6 for each of the three detail dialogs (the create dialog's is in
// expenses.test.tsx).
const LOST_CASES = [
  {
    name: 'payment',
    expense: EXPENSE_UNPAID,
    route: paymentPost(EXPENSE_UNPAID.id),
    open: () => click(action('Record payment')!),
    fill: (d: HTMLElement) => setFieldValue(labelled(d, 'Method'), 'cash'),
    submit: 'Record payment',
    resend: 'Resend the same payment',
    locked: 'Amount',
    announced:
      'The payment may already have been recorded. Check the payments before recording it again.',
    action: 'Record payment',
  },
  {
    name: 'adjust',
    expense: EXPENSE_UNPAID,
    route: adjustmentPost(EXPENSE_UNPAID.id),
    open: () => click(action('Adjust')!),
    fill: async (d: HTMLElement) => {
      await click(
        d.querySelector<HTMLInputElement>('input[value="increase"]')!,
      );
      setFieldValue(labelled(d, 'By how much'), '10');
      setFieldValue(labelled(d, 'Reason'), 'r');
    },
    submit: 'Save adjustment',
    resend: 'Resend the same adjustment',
    locked: 'By how much',
    announced:
      'The adjustment may already have been saved. Check the adjustments before adjusting again.',
    action: 'Adjust',
  },
  {
    name: 'void',
    expense: EXPENSE_UNPAID,
    route: adjustmentPost(EXPENSE_UNPAID.id),
    open: () => click(action('Void')!),
    fill: (d: HTMLElement) => setFieldValue(labelled(d, 'Reason'), 'r'),
    submit: 'Void the expense',
    resend: 'Resend the same void',
    locked: 'Reason',
    announced:
      'The expense may already have been voided. Check it before trying again.',
    action: 'Void',
  },
  {
    name: 'reverse',
    expense: EXPENSE_PARTIAL,
    route: reversalPost(PAYMENT_VALID.id),
    open: () => click(reverseButtons()[0]!),
    fill: (d: HTMLElement) => setFieldValue(labelled(d, 'Reason'), 'r'),
    submit: 'Reverse payment',
    resend: 'Resend the same reversal',
    locked: 'Reason',
    announced:
      'The payment may already have been reversed. Check the payments before trying again.',
    action: 'Adjust',
  },
] as const;

describe.each(LOST_CASES)('a lost answer in the $name dialog (D6)', (c) => {
  const opened = () => dialog(c.name)!;

  async function start(api: () => ApiStub) {
    const stubbed = api();
    await openDetail(c.expense);
    await c.open();
    await settleUntil(() => opened()?.querySelector('form'));
    await c.fill(opened());
    return stubbed;
  }

  it('locks the fields; resends the stored body with the stored key', async () => {
    const api = await start(() =>
      stub(c.expense, {
        [c.route]: [lost, () => problem(503, 'internal_error'), ok()],
      }),
    );
    await click(buttonNamed(opened(), c.submit));
    await settleUntil(() => alerts(opened()).length > 0);
    expect(alerts(opened()).join()).toContain("We don't know whether");
    expect(labelled<HTMLInputElement>(opened(), c.locked).disabled).toBe(true);
    await click(buttonNamed(opened(), c.resend));
    await settleUntil(() => api.calls(c.route).length === 2);
    expect(buttonNamed(opened(), c.resend)).toBeDefined();
    await click(buttonNamed(opened(), c.resend));
    await settleUntil(() => !opened());
    const [a, b, d] = keys(api, c.route);
    expect(b).toBe(a);
    expect(d).toBe(a);
    const bodies = api.calls(c.route).map((call) => call.body);
    expect(bodies[1]).toEqual(bodies[0]);
    expect(bodies[2]).toEqual(bodies[0]);
  });

  it('a settled refusal after it unlocks the fields', async () => {
    await start(() =>
      stub(c.expense, {
        [c.route]: [lost, () => problem(403, 'permission_denied')],
      }),
    );
    await click(buttonNamed(opened(), c.submit));
    await settleUntil(() => alerts(opened()).length > 0);
    await click(buttonNamed(opened(), c.resend));
    await settleUntil(
      () => labelled<HTMLInputElement>(opened(), c.locked).disabled === false,
    );
    expect(buttonNamed(opened(), c.submit)).toBeDefined();
  });

  it.each(closeRoutes)(
    'closing by %s refetches and holds the actions until it lands',
    async (_route, close) => {
      let release: () => void = () => {};
      let held = false;
      const api = await start(() =>
        stub(c.expense, {
          [c.route]: lost,
          [expenseGet(c.expense.id)]: () =>
            held
              ? new Promise<Response>((resolve) => {
                  release = () => resolve(jsonResponse(200, c.expense));
                })
              : jsonResponse(200, c.expense),
        }),
      );
      await click(buttonNamed(opened(), c.submit));
      await settleUntil(() => alerts(opened()).length > 0);
      const before = api.calls(expenseGet(c.expense.id)).length;
      held = true;
      await close(opened());
      await settleUntil(() => !opened());
      await settleUntil(
        () => api.calls(expenseGet(c.expense.id)).length > before,
      );
      expect(action(c.action)!.disabled).toBe(true);
      expect(said()).toBe(c.announced);
      release();
      await settleUntil(() => action(c.action)!.disabled === false);
      // A new opening is a new key.
      api.set({ [c.route]: ok() });
      await c.open();
      await settleUntil(() => opened()?.querySelector('form'));
      await c.fill(opened());
      await click(buttonNamed(opened(), c.submit));
      await settleUntil(() => api.calls(c.route).length === 2);
      const [first, second] = keys(api, c.route);
      expect(second).not.toBe(first);
    },
  );

  it('a failed refetch after close says out of date and never reloaded', async () => {
    let failing = false;
    await start(() =>
      stub(c.expense, {
        [c.route]: lost,
        [expenseGet(c.expense.id)]: () =>
          failing
            ? problem(400, 'database_unavailable')
            : jsonResponse(200, c.expense),
      }),
    );
    await click(buttonNamed(opened(), c.submit));
    await settleUntil(() => alerts(opened()).length > 0);
    failing = true;
    await click(buttonNamed(opened(), 'Cancel'));
    await settleUntil(() => alerts(main()).length > 0);
    expect(alerts(main()).join()).toContain('may be out of date');
    expect(main().textContent).not.toMatch(/was reloaded/);
    expect(action(c.action)!.disabled).toBe(true);
  });
});

it('a close with no attempt makes no request', async () => {
  const api = stub();
  await openDialog('Adjust', 'adjust');
  const before = api.requests.length;
  await click(buttonNamed(dialog('adjust')!, 'Cancel'));
  await settle();
  expect(api.requests.length).toBe(before);
});

describe('a generated expense (M7-13b E1, E2)', () => {
  it('names its template with a link and shows "Automatic" as its recorder', async () => {
    stub(EXPENSE_GENERATED);
    await openDetail(EXPENSE_GENERATED);
    const line = main().querySelector('[data-expense-template]');
    expect(line?.textContent).toBe(
      'Generated automatically by the template Renta del local',
    );
    expect(line?.querySelector('a')?.getAttribute('href')).toBe(
      `/expenses/templates/${EXPENSE_GENERATED.template!.id}`,
    );
    expect(
      head()!.querySelector('[data-expense-field="createdBy"] dd')?.textContent,
    ).toBe('Automatic');
    // It is paid, adjusted and voided like any other.
    for (const name of ['Record payment', 'Adjust', 'Void']) {
      expect(action(name)).not.toBeNull();
    }
  });

  it('says it in Spanish', async () => {
    applyLanguage('es');
    stub(EXPENSE_GENERATED);
    await openDetail(EXPENSE_GENERATED);
    expect(main().querySelector('[data-expense-template]')?.textContent).toBe(
      'Generado automáticamente por la plantilla Renta del local',
    );
    expect(
      head()!.querySelector('[data-expense-field="createdBy"] dd')?.textContent,
    ).toBe('Automático');
  });

  it('a hand-entered expense has no template line', async () => {
    stub();
    await openDetail();
    expect(main().querySelector('[data-expense-template]')).toBeNull();
  });
});
