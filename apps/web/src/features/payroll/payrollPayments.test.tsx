import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { problemWith } from '@/features/academics/testing';
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
  settleUntil,
  type RenderedApp,
} from '@/test/render-app';
import type { LocalDate } from '@/lib/time';
import { TUTOR_ID } from './fixtures';
import {
  paymentCreateBody,
  remainingOf,
  validatePaymentForm,
  validateReversalReason,
} from './paymentForm';
import {
  ADMIN_ID,
  FINANCE_ID,
  LINE_HOURLY,
  listRow,
  OWNER_ID,
  payment,
  PAYMENT_METHODS,
  PAYMENT_REVERSED,
  PAYMENT_TRANSFER,
  PERIOD_FINALIZED,
  PERIOD_ID,
  PERIOD_OPEN,
  PERIOD_PAID,
  PERIOD_PARTIAL,
  PERIOD_PREPARED,
  PERIOD_ZERO,
  review,
} from './periodFixtures';
import type { PayrollPeriodDetail } from './api';

// M7-09b E1: who sees what per role and delegation, both dialogs' success,
// every mapped refusal, the password then the same key, a lost response
// closed three ways, an edited resend refused as a duplicate that names the
// payment, a failed refetch, and the list's badge and filter.

const ME = 'GET /api/v1/auth/me';
const SETTINGS = 'GET /api/v1/payroll/settings';
const LIST = 'GET /api/v1/payroll/periods';
const LINES = `GET /api/v1/payroll/periods/${PERIOD_ID}/lines`;
const PAYMENTS = `GET /api/v1/payroll/periods/${PERIOD_ID}/payments`;
const PAY = `POST /api/v1/payroll/periods/${PERIOD_ID}/payments`;
const REVERSE = `POST /api/v1/payroll/payments/${PAYMENT_TRANSFER.id}/reversal`;
const METHODS = 'GET /api/v1/billing/payment-methods';
const REAUTH = 'POST /api/v1/auth/reauthenticate';

const user = (
  roles: SessionUserBody['roles'],
  permissions: SessionUserBody['permissions'],
  id = TEST_USER.id,
): SessionUserBody => ({ ...TEST_USER, id, roles, permissions });

const FINANCE_PERMISSIONS: SessionUserBody['permissions'] = [
  'payroll.view',
  'payroll.manage',
  'payroll.prepare',
  'payroll.payment.record',
  'billing.view',
];
const OWNER_PERMISSIONS: SessionUserBody['permissions'] = [
  ...FINANCE_PERMISSIONS,
  'payroll.finalize',
  'finance.refund.approve',
];
const FINANCE = user(['finance_staff'], FINANCE_PERMISSIONS, FINANCE_ID);
const OWNER = user(['owner'], OWNER_PERMISSIONS, OWNER_ID);
const ADMIN = user(['administrator'], ['payroll.finalize'], ADMIN_ID);
const ADMIN_REFUNDS = user(
  ['administrator'],
  ['payroll.finalize', 'finance.refund.approve'],
  ADMIN_ID,
);
const FINANCE_TUTOR = user(
  ['finance_staff', 'tutor'],
  FINANCE_PERMISSIONS,
  TUTOR_ID,
);
const OWNER_TUTOR = user(['owner', 'tutor'], OWNER_PERMISSIONS, TUTOR_ID);

const finalized = (period: PayrollPeriodDetail = PERIOD_PARTIAL) =>
  review(period, [LINE_HOURLY], period.total_amount ?? '0.00');
const recorded = (state = 'partially_paid') =>
  jsonResponse(201, {
    id: '96000000-0000-4000-8000-0000000000aa',
    payroll_period_id: PERIOD_ID,
    paid_state: state,
  });
const lost = () => Promise.reject(new TypeError('Failed to fetch'));

function stub(
  routes: Record<string, Responder | Responder[]> = {},
  as: SessionUserBody = FINANCE,
): ApiStub {
  return stubApi({
    [ME]: meAs(as),
    [SETTINGS]: () =>
      jsonResponse(200, {
        payroll_starts_on: '2026-10-01',
        biweekly_anchor: null,
      }),
    [LIST]: () =>
      jsonResponse(200, { items: [], total: 0, limit: 25, offset: 0 }),
    [LINES]: () => jsonResponse(200, finalized()),
    [PAYMENTS]: () => jsonResponse(200, [PAYMENT_TRANSFER, PAYMENT_REVERSED]),
    [METHODS]: () => jsonResponse(200, PAYMENT_METHODS),
    [REAUTH]: () =>
      jsonResponse(200, { reauthenticated_until: '2026-10-19T18:05:00Z' }),
    ...routes,
  });
}

let app: RenderedApp | undefined;

const main = () => app!.container.querySelector('main')!;
const section = () =>
  main().querySelector<HTMLElement>('[data-payments-section]');
const payDialog = () =>
  document.querySelector<HTMLElement>('[data-payment-dialog]');
const reverseDialog = () =>
  document.querySelector<HTMLElement>('[data-reversal-dialog]');
const said = () =>
  main().querySelector('[data-payments-announcement]')?.textContent ?? '';
const recordButton = () =>
  [...(section()?.querySelectorAll('button') ?? [])].find(
    (button) => button.textContent === 'Record payment',
  );
const reverseButtons = () => [
  ...main().querySelectorAll<HTMLElement>(
    '[data-payments-table] [data-reverse-payment]',
  ),
];
const keys = (api: ApiStub, route: string) =>
  api.calls(route).map((call) => call.headers.get('Idempotency-Key'));

async function openReview() {
  app = await renderApp(`/payroll/periods/${PERIOD_ID}`);
  await settleUntil(() => main().querySelector('[data-period-head]'));
}

async function openSection() {
  await openReview();
  await settleUntil(() =>
    section()?.querySelector('[data-payments-table], [data-payments-empty]'),
  );
}

async function openPay() {
  await openSection();
  await click(recordButton()!);
  await settleUntil(() => payDialog()?.querySelector('form'));
}

function fill(
  dialog: HTMLElement,
  {
    amount,
    method = 'transfer',
    reference = 'SPEI 777',
    note,
    paidOn,
  }: {
    amount?: string;
    method?: string;
    reference?: string;
    note?: string;
    paidOn?: string;
  } = {},
) {
  if (paidOn !== undefined) {
    setFieldValue(labelled(dialog, 'Payment date'), paidOn);
  }
  if (amount !== undefined) setFieldValue(labelled(dialog, 'Amount'), amount);
  setFieldValue(labelled(dialog, 'Method'), method);
  setFieldValue(labelled(dialog, 'Reference'), reference);
  if (note !== undefined) {
    setFieldValue(labelled(dialog, 'Note (optional)'), note);
  }
}

async function submitPay() {
  await click(buttonNamed(payDialog()!, 'Record payment'));
}

const closeRoutes = [
  ['Cancel', (dialog: HTMLElement) => click(buttonNamed(dialog, 'Cancel'))],
  ['Escape', () => pressEscape()],
  [
    'the close button',
    (dialog: HTMLElement) =>
      click(dialog.querySelector<HTMLElement>('[data-slot="dialog-close"]')!),
  ],
] as const;

beforeEach(() => {
  applyLanguage('en');
});

afterEach(() => {
  app?.unmount();
  app = undefined;
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('access (A)', () => {
  it.each([
    ['Finance', FINANCE, true, false],
    ['the Owner', OWNER, true, true],
    ['an Owner who is the tutor', OWNER_TUTOR, true, true],
    ['Finance who is the tutor', FINANCE_TUTOR, false, false],
    ['a delegated Administrator', ADMIN, false, false],
    ['an Administrator delegated refunds too', ADMIN_REFUNDS, false, true],
  ])('%s: record %s, reverse %s', async (_who, as, records, reverses) => {
    stub({}, as);
    await openSection();
    expect(section()).not.toBeNull();
    expect(recordButton() !== undefined).toBe(records);
    // Only the unreversed payment offers it.
    expect(reverseButtons().map((b) => b.dataset.reversePayment)).toEqual(
      reverses ? [PAYMENT_TRANSFER.id] : [],
    );
  });

  it.each([
    ['open', PERIOD_OPEN],
    ['prepared', PERIOD_PREPARED],
  ])('an %s period has no payments section', async (_status, period) => {
    const api = stub({ [LINES]: () => jsonResponse(200, finalized(period)) });
    await openReview();
    expect(section()).toBeNull();
    expect(api.calls(PAYMENTS)).toHaveLength(0);
  });

  it('a paid period: the badge, no "Record payment"', async () => {
    stub({ [LINES]: () => jsonResponse(200, finalized(PERIOD_PAID)) }, OWNER);
    await openSection();
    expect(section()!.querySelector('[data-paid-state]')?.textContent).toBe(
      'Paid',
    );
    expect(recordButton()).toBeUndefined();
    expect(reverseButtons()).toHaveLength(1);
  });

  it('D3: a 0.00 total is paid, with nothing to pay and no button', async () => {
    stub({
      [LINES]: () => jsonResponse(200, finalized(PERIOD_ZERO)),
      [PAYMENTS]: () => jsonResponse(200, []),
    });
    await openSection();
    expect(section()!.querySelector('[data-paid-state]')?.textContent).toBe(
      'Paid',
    );
    expect(section()!.textContent).toContain('there is nothing to pay');
    expect(recordButton()).toBeUndefined();
  });

  it("A2: the API's own-pay refusal still shows", async () => {
    const api = stub({
      [PAY]: () => problem(403, 'payroll_own_compensation'),
    });
    await openPay();
    fill(payDialog()!);
    await submitPay();
    await settleUntil(() => alerts(payDialog()!).length > 0);
    expect(alerts(payDialog()!).join()).toContain(
      'Someone else must change your pay.',
    );
    expect(api.calls(PAY)).toHaveLength(1);
  });
});

describe('display (D1, C3)', () => {
  it('totals, the badge, each payment and a reversed one struck through', async () => {
    stub();
    await openSection();
    const totals: Record<string, string> = {};
    for (const dd of section()!.querySelectorAll<HTMLElement>(
      '[data-payments-total]',
    )) {
      totals[dd.dataset.paymentsTotal ?? ''] = dd.textContent;
    }
    expect(totals).toEqual({
      total: '$4,116.67',
      paid: '$1,000.00',
      remaining: '$3,116.67',
    });
    expect(section()!.querySelector('[data-paid-state]')?.textContent).toBe(
      'Partly paid',
    );

    const cells = (id: string) =>
      [
        ...section()!.querySelectorAll(
          `[data-payments-table] tr[data-payment-id="${id}"] td`,
        ),
      ].map((cell) => cell.textContent);
    expect(cells(PAYMENT_TRANSFER.id)).toEqual([
      'Oct 20, 2026',
      '$1,000.00',
      'Transfer',
      'SPEI 1',
      'Primera parte',
      expect.stringContaining('Demo Finance, Oct 20, 2026'),
      '—',
    ]);
    const reversed = cells(PAYMENT_REVERSED.id);
    expect(reversed[1]).toBe('$500.00');
    expect(reversed[6]).toContain('Reversed by Demo Owner, Oct 19, 2026');
    expect(reversed[6]).toContain('Reason: Monto equivocado');
    expect(
      section()!
        .querySelector(
          `tr[data-payment-id="${PAYMENT_REVERSED.id}"] [data-payment-amount]`,
        )
        ?.classList.contains('line-through'),
    ).toBe(true);
  });

  it('in Spanish', async () => {
    applyLanguage('es');
    stub();
    await openSection();
    expect(section()!.textContent).toContain('Pagos');
    expect(section()!.querySelector('[data-paid-state]')?.textContent).toBe(
      'Pago parcial',
    );
    expect(section()!.textContent).toContain('Registrar pago');
  });

  it('D4: a failed refetch keeps the rows marked stale and locks the actions until a reload works', async () => {
    const api = stub(
      {
        // A 4xx: the query client retries a 5xx by itself.
        [PAYMENTS]: [
          () => jsonResponse(200, [PAYMENT_TRANSFER, PAYMENT_REVERSED]),
          () => problem(400, 'bad_request'),
          () => jsonResponse(200, [PAYMENT_TRANSFER, PAYMENT_REVERSED]),
        ],
      },
      OWNER,
    );
    await openSection();
    await app!.queryClient.invalidateQueries({
      queryKey: ['payroll', 'periods', 'payments'],
    });
    await settleUntil(() =>
      section()!.textContent.includes('may be out of date'),
    );
    expect(
      section()!.querySelector('[data-payments-stale="true"]'),
    ).not.toBeNull();
    expect(section()!.querySelectorAll('tr[data-payment-id]')).toHaveLength(2);
    expect(recordButton()!.disabled).toBe(true);
    expect(
      reverseButtons().every((b) => (b as HTMLButtonElement).disabled),
    ).toBe(true);
    expect(said()).toBe('');

    await click(buttonNamed(section()!, 'Reload'));
    await settleUntil(
      () => !section()!.textContent.includes('may be out of date'),
    );
    expect(recordButton()!.disabled).toBe(false);
    expect(api.calls(PAYMENTS)).toHaveLength(3);
  });

  it('a first load that fails says so, with a retry', async () => {
    stub({
      [PAYMENTS]: [
        () => problem(400, 'bad_request'),
        () => jsonResponse(200, []),
      ],
    });
    await openReview();
    await settleUntil(() => alerts(section()!).length > 0);
    expect(recordButton()!.disabled).toBe(true);
    await click(buttonNamed(section()!, 'Try again'));
    await settleUntil(() => section()!.querySelector('[data-payments-empty]'));
  });
});

describe('record a payment (B)', () => {
  it('B1: defaults to today and what is left, offers only active methods', async () => {
    stub();
    await openPay();
    const dialog = payDialog()!;
    expect(labelled<HTMLInputElement>(dialog, 'Amount').value).toBe('3116.67');
    expect(dialog.querySelector('[data-payment-remaining]')?.textContent).toBe(
      'Left to pay: $3,116.67',
    );
    const date = labelled<HTMLInputElement>(dialog, 'Payment date');
    expect(date.min).toBe(PERIOD_PARTIAL.starts_on);
    expect(date.value).toBe(date.max);
    expect(
      [...labelled<HTMLSelectElement>(dialog, 'Method').options].map(
        (option) => option.value,
      ),
    ).toEqual(['', 'cash', 'transfer']);
  });

  it('B5: sends the body with a key, closes, refetches and says so', async () => {
    const api = stub({ [PAY]: () => recorded() });
    await openPay();
    const before = {
      payments: api.calls(PAYMENTS).length,
      lines: api.calls(LINES).length,
    };
    fill(payDialog()!, { amount: '500', note: '  Segunda parte ' });
    await submitPay();
    await settleUntil(() => !payDialog());

    const [call] = api.calls(PAY);
    expect(call!.body).toEqual({
      paid_on: labelledToday(),
      amount: '500',
      method_code: 'transfer',
      reference: 'SPEI 777',
      note: 'Segunda parte',
    });
    expect(call!.headers.get('Idempotency-Key')).toMatch(/^[0-9a-f-]{36}$/);
    await settleUntil(() => said() === 'Payment recorded.');
    await settleUntil(
      () =>
        api.calls(PAYMENTS).length > before.payments &&
        api.calls(LINES).length > before.lines,
    );
  });

  it('B1: checks the fields before sending', async () => {
    const api = stub();
    await openPay();
    const dialog = payDialog()!;
    setFieldValue(labelled(dialog, 'Amount'), '3116.68');
    setFieldValue(labelled(dialog, 'Payment date'), '2099-01-01');
    await submitPay();
    const text = dialog.textContent;
    expect(text).toContain("The payment date can't be after today.");
    expect(text).toContain("The amount can't be more than what's left to pay.");
    expect(text).toContain('Choose the payment method.');
    expect(text).toContain('Enter the reference.');
    expect(api.calls(PAY)).toHaveLength(0);
  });

  it('B2: the same key on a resend, a new one after an edit and on a new opening', async () => {
    const api = stub({
      [PAY]: [() => problem(502, 'internal_error'), lost, () => recorded()],
    });
    await openPay();
    fill(payDialog()!, { amount: '500' });
    await submitPay();
    await settleUntil(() => alerts(payDialog()!).length > 0);
    await submitPay();
    await settleUntil(() => api.calls(PAY).length === 2);
    const [first, second] = keys(api, PAY);
    expect(second).toBe(first);

    setFieldValue(labelled(payDialog()!, 'Amount'), '400');
    await submitPay();
    await settleUntil(() => !payDialog());
    expect(keys(api, PAY)[2]).not.toBe(first);

    await settleUntil(() => recordButton() && !recordButton()!.disabled);
    await click(recordButton()!);
    await settleUntil(() => payDialog()?.querySelector('form'));
    fill(payDialog()!, { amount: '400' });
    await submitPay();
    await settleUntil(() => api.calls(PAY).length === 4);
    expect(new Set(keys(api, PAY)).size).toBe(3);
  });

  it.each(closeRoutes)(
    'B3: a lost response, then %s: the payments and the period refetch',
    async (_route, close) => {
      const api = stub({ [PAY]: lost });
      await openPay();
      fill(payDialog()!, { amount: '500' });
      await submitPay();
      await settleUntil(() => alerts(payDialog()!).length > 0);
      expect(alerts(payDialog()!).join()).toContain(
        "We don't know whether the payment was recorded.",
      );
      // Still open, the fields editable.
      expect(labelled<HTMLInputElement>(payDialog()!, 'Amount').disabled).toBe(
        false,
      );
      const before = {
        payments: api.calls(PAYMENTS).length,
        lines: api.calls(LINES).length,
      };

      await close(payDialog()!);
      await settleUntil(() => !payDialog());
      await settleUntil(
        () =>
          api.calls(PAYMENTS).length > before.payments &&
          api.calls(LINES).length > before.lines,
      );
      expect(said()).toContain('The payment may have been recorded.');
    },
  );

  it.each(closeRoutes)(
    'B3: a settled refusal, then %s, refetches too',
    async (_route, close) => {
      const api = stub({
        [PAY]: () => problem(422, 'idempotency_key_reused'),
      });
      await openPay();
      fill(payDialog()!);
      await submitPay();
      await settleUntil(() => alerts(payDialog()!).length > 0);
      const before = api.calls(PAYMENTS).length;
      await close(payDialog()!);
      await settleUntil(() => !payDialog());
      await settleUntil(() => api.calls(PAYMENTS).length > before);
      expect(said()).toBe('');
    },
  );

  it('closing without any attempt refetches nothing', async () => {
    const api = stub();
    await openPay();
    const before = api.calls(PAYMENTS).length;
    await pressEscape();
    await settleUntil(() => !payDialog());
    expect(api.calls(PAYMENTS)).toHaveLength(before);
  });

  it('B3: a lost response, an edited amount, then the duplicate names the stored payment', async () => {
    const stored = payment(9, {
      amount: '500.00',
      reference: 'SPEI 777',
      paid_on: '2026-10-21',
    });
    let saved = false;
    const api = stub({
      [PAY]: [
        () => {
          saved = true;
          return lost();
        },
        () =>
          problemWith(409, 'payroll_payment_duplicate', {
            details: { payment_id: stored.id },
          }),
      ],
      [PAYMENTS]: () =>
        jsonResponse(
          200,
          saved ? [stored, PAYMENT_TRANSFER] : [PAYMENT_TRANSFER],
        ),
    });
    await openPay();
    fill(payDialog()!, { amount: '500' });
    await submitPay();
    await settleUntil(() => alerts(payDialog()!).length > 0);
    setFieldValue(labelled(payDialog()!, 'Amount'), '450');
    await submitPay();
    await settleUntil(() =>
      alerts(payDialog()!).join().includes('already recorded'),
    );
    expect(alerts(payDialog()!).join()).toContain(
      'This payment is already recorded: Oct 21, 2026, $500.00, Transfer SPEI 777.',
    );
    const [first, second] = keys(api, PAY);
    expect(second).not.toBe(first);
  });

  it('a duplicate the list does not hold falls back to the general message', async () => {
    stub({
      [PAY]: () =>
        problemWith(409, 'payroll_payment_duplicate', {
          details: { payment_id: '96000000-0000-4000-8000-0000000000ff' },
        }),
    });
    await openPay();
    fill(payDialog()!);
    await submitPay();
    await settleUntil(() => alerts(payDialog()!).length > 0);
    expect(alerts(payDialog()!).join()).toContain(
      'A payment with this method and reference is already recorded',
    );
  });

  it('B4: the cap names what is left and refetches the period', async () => {
    const api = stub({
      [PAY]: () =>
        problemWith(409, 'payroll_payment_exceeds_balance', {
          details: { remaining: '30.00' },
        }),
    });
    await openPay();
    const lines = api.calls(LINES).length;
    fill(payDialog()!, { amount: '500' });
    await submitPay();
    await settleUntil(() => alerts(payDialog()!).length > 0);
    expect(alerts(payDialog()!).join()).toContain(
      'This payment is more than what is left to pay: $30.00.',
    );
    expect(api.calls(LINES).length).toBeGreaterThan(lines);
  });

  it.each([
    [
      'payroll_period_not_finalized',
      () =>
        problemWith(409, 'payroll_period_not_finalized', {
          details: { status: 'prepared' },
        }),
      "This period isn't finalized, so it can't take payments.",
    ],
    [
      'idempotency_key_reused',
      () => problem(422, 'idempotency_key_reused'),
      'This submission was already used for different data.',
    ],
    [
      'payroll_own_compensation',
      () => problem(403, 'payroll_own_compensation'),
      'Someone else must change your pay.',
    ],
  ])('B4: %s is the alert', async (_code, answer, text) => {
    stub({ [PAY]: answer });
    await openPay();
    fill(payDialog()!);
    await submitPay();
    await settleUntil(() => alerts(payDialog()!).length > 0);
    expect(alerts(payDialog()!).join()).toContain(text);
  });

  it.each([
    ['paid_on_in_future', 'paid_on', 'Payment date', "can't be after today"],
    [
      'paid_on_before_period',
      'paid_on',
      'Payment date',
      "can't be before the payroll period starts",
    ],
    [
      'payment_method_unavailable',
      'method_code',
      'Method',
      "This payment method isn't available any more.",
    ],
    ['text_too_long', 'reference', 'Reference', ''],
    ['amount_not_positive', 'amount', 'Amount', ''],
  ])('B4: %s lands on its field', async (code, field, label, text) => {
    const api = stub({
      [PAY]: () =>
        problemWith(422, code === 'text_too_long' ? 'validation_error' : code, {
          errors: [{ path: ['body', field], code }],
        }),
    });
    await openPay();
    fill(payDialog()!);
    const methods = api.calls(METHODS).length;
    await submitPay();
    const input = labelled(payDialog()!, label);
    await settleUntil(() => input.getAttribute('aria-invalid') === 'true');
    const message = document.getElementById(
      input.getAttribute('aria-describedby')!.split(' ').at(-1)!,
    )?.textContent;
    expect(message).toBeTruthy();
    if (text) expect(message).toContain(text);
    expect(alerts(payDialog()!)).toHaveLength(0);
    if (code === 'payment_method_unavailable') {
      await settleUntil(() => api.calls(METHODS).length > methods);
    }
  });
});

describe('reverse a payment (C)', () => {
  async function openReverse() {
    await openSection();
    await click(reverseButtons()[0]!);
    await settleUntil(() => reverseDialog());
  }

  async function submitReverse() {
    await click(buttonNamed(reverseDialog()!, 'Reverse payment'));
  }

  const reason = (text = 'Monto equivocado') =>
    setFieldValue(labelled(reverseDialog()!, 'Reason'), text);

  it('C1: a reason is required; then sends it with a key and says so', async () => {
    const api = stub(
      {
        [REVERSE]: () =>
          jsonResponse(201, {
            id: '97000000-0000-4000-8000-000000000001',
            payroll_payment_id: PAYMENT_TRANSFER.id,
            paid_state: 'unpaid',
          }),
      },
      OWNER,
    );
    await openReverse();
    expect(reverseDialog()!.textContent).toContain(
      'Payment: Oct 20, 2026, $1,000.00, Transfer SPEI 1',
    );
    await submitReverse();
    expect(reverseDialog()!.textContent).toContain('Enter the reason.');
    expect(api.calls(REVERSE)).toHaveLength(0);

    const before = api.calls(PAYMENTS).length;
    reason('  Monto equivocado ');
    await submitReverse();
    await settleUntil(() => !reverseDialog());
    const [call] = api.calls(REVERSE);
    expect(call!.body).toEqual({ reason: 'Monto equivocado' });
    expect(call!.headers.get('Idempotency-Key')).toMatch(/^[0-9a-f-]{36}$/);
    await settleUntil(() => said() === 'Payment reversed.');
    await settleUntil(() => api.calls(PAYMENTS).length > before);
  });

  it('C1: the password dialog, then the same key and reason', async () => {
    const api = stub(
      {
        [REVERSE]: [
          () => problem(403, 'reauth_required'),
          () =>
            jsonResponse(201, {
              id: '97000000-0000-4000-8000-000000000001',
              payroll_payment_id: PAYMENT_TRANSFER.id,
              paid_state: 'unpaid',
            }),
        ],
      },
      OWNER,
    );
    await openReverse();
    reason();
    await submitReverse();
    const password = [
      ...document.querySelectorAll<HTMLElement>('[role="dialog"]'),
    ].find(
      (d) => d.querySelector('h2')?.textContent === 'Confirm your password',
    )!;
    setFieldValue(labelled(password, 'Password'), 'my-own-password');
    await click(buttonNamed(password, 'Confirm'));
    await settleUntil(() => said() === 'Payment reversed.');
    const [first, second] = api.calls(REVERSE);
    expect(second!.headers.get('Idempotency-Key')).toBe(
      first!.headers.get('Idempotency-Key'),
    );
    expect(second!.body).toEqual(first!.body);
  });

  it.each(closeRoutes)(
    'C1: a lost response, a resend with the same key, then %s refetches',
    async (_route, close) => {
      const api = stub({ [REVERSE]: lost }, OWNER);
      await openReverse();
      reason();
      await submitReverse();
      await settleUntil(() => alerts(reverseDialog()!).length > 0);
      expect(alerts(reverseDialog()!).join()).toContain(
        "We don't know whether the payment was reversed.",
      );
      await submitReverse();
      await settleUntil(() => api.calls(REVERSE).length === 2);
      const [first, second] = keys(api, REVERSE);
      expect(second).toBe(first);
      const before = {
        payments: api.calls(PAYMENTS).length,
        lines: api.calls(LINES).length,
      };
      await close(reverseDialog()!);
      await settleUntil(() => !reverseDialog());
      await settleUntil(
        () =>
          api.calls(PAYMENTS).length > before.payments &&
          api.calls(LINES).length > before.lines,
      );
      expect(said()).toContain('The payment may have been reversed.');
    },
  );

  it('C2: already reversed refetches, then says so', async () => {
    const api = stub(
      { [REVERSE]: () => problem(409, 'payroll_payment_already_reversed') },
      OWNER,
    );
    await openReverse();
    const before = api.calls(PAYMENTS).length;
    reason();
    await submitReverse();
    await settleUntil(() => alerts(reverseDialog()!).length > 0);
    expect(alerts(reverseDialog()!).join()).toContain(
      'This payment was already reversed.',
    );
    expect(api.calls(PAYMENTS).length).toBeGreaterThan(before);
  });

  it("C2: the API's own-pay refusal", async () => {
    stub({ [REVERSE]: () => problem(403, 'payroll_own_compensation') }, OWNER);
    await openReverse();
    reason();
    await submitReverse();
    await settleUntil(() => alerts(reverseDialog()!).length > 0);
    expect(alerts(reverseDialog()!).join()).toContain(
      'Someone else must change your pay.',
    );
  });
});

describe('period list (D2)', () => {
  const PAGE = {
    items: [
      listRow(1, { status: 'open' }),
      listRow(2, {
        status: 'finalized',
        finalized_at: '2026-10-03T15:00:00Z',
        paid_total: '400.00',
        paid_state: 'partially_paid',
      }),
    ],
    total: 2,
    limit: 25,
    offset: 0,
  };

  it('a badge on finalized rows only; the filter is in the URL and the query', async () => {
    const urls: URL[] = [];
    stub({
      [LIST]: (request) => {
        urls.push(new URL(request.url));
        return jsonResponse(200, PAGE);
      },
    });
    app = await renderApp('/payroll/periods?page=2');
    await settleUntil(
      () =>
        main().querySelectorAll('[data-periods-table] tbody tr').length === 2,
    );
    const badges = [
      ...main().querySelectorAll('[data-periods-table] [data-paid-state]'),
    ].map((badge) => badge.textContent);
    expect(badges).toEqual(['Partly paid']);

    setFieldValue(labelled(main(), 'Payment'), 'unpaid');
    await settleUntil(() => app!.location() === '/payroll/periods?paid=unpaid');
    await settleUntil(() =>
      urls.some((url) => url.searchParams.get('paid_state') === 'unpaid'),
    );
    const last = urls.at(-1)!;
    expect(last.searchParams.get('paid_state')).toBe('unpaid');
    expect(last.searchParams.get('offset')).toBe('0');

    await click(buttonNamed(main(), 'Clear filters'));
    await settleUntil(() => app!.location() === '/payroll/periods');
  });

  it('an invalid paid filter in the URL is ignored', async () => {
    const urls: URL[] = [];
    stub({
      [LIST]: (request) => {
        urls.push(new URL(request.url));
        return jsonResponse(200, PAGE);
      },
    });
    app = await renderApp('/payroll/periods?paid=everything');
    await settleUntil(() => urls.length > 0);
    expect(urls[0]!.searchParams.has('paid_state')).toBe(false);
  });
});

describe('pure rules', () => {
  const limits = {
    startsOn: '2026-10-01',
    today: '2026-10-20' as LocalDate,
    remaining: '30.00',
  };
  const values = {
    paidOn: '2026-10-20',
    amount: '30',
    methodCode: 'cash',
    reference: 'R',
    note: '',
  };

  it('what is left, as whole cents', () => {
    expect(remainingOf(PERIOD_PARTIAL)).toBe('3116.67');
    expect(remainingOf(PERIOD_ZERO)).toBe('0.00');
    expect(remainingOf(PERIOD_FINALIZED)).toBe('4116.67');
    expect(remainingOf(PERIOD_OPEN)).toBeNull();
    expect(remainingOf({ total_amount: '0.30', paid_total: '0.10' })).toBe(
      '0.20',
    );
  });

  it('the client checks mirror the API', () => {
    expect(validatePaymentForm(values, limits)).toEqual({});
    expect(
      validatePaymentForm({ ...values, amount: '30.01' }, limits).amount,
    ).toBe('payroll.validation.amountAboveRemaining');
    expect(validatePaymentForm({ ...values, amount: '0' }, limits).amount).toBe(
      'payroll.validation.amountPositive',
    );
    expect(
      validatePaymentForm({ ...values, amount: '1.234' }, limits).amount,
    ).toBe('payroll.validation.amountInvalid');
    expect(
      validatePaymentForm({ ...values, paidOn: '2026-09-30' }, limits).paidOn,
    ).toBe('payroll.validation.paidOnBeforePeriod');
    expect(
      validatePaymentForm({ ...values, paidOn: '2026-10-01' }, limits).paidOn,
    ).toBeUndefined();
    expect(
      validatePaymentForm({ ...values, reference: 'x'.repeat(101) }, limits)
        .reference,
    ).toBe('payroll.validation.referenceTooLong');
    expect(
      validatePaymentForm({ ...values, reference: '😀'.repeat(100) }, limits)
        .reference,
    ).toBeUndefined();
    expect(
      validatePaymentForm({ ...values, note: 'x'.repeat(1001) }, limits).note,
    ).toBe('payroll.validation.reasonTooLong');
    expect(paymentCreateBody({ ...values, note: '   ' }).note).toBeNull();
    expect(validateReversalReason('  ')).toBe(
      'payroll.validation.reasonRequired',
    );
    expect(validateReversalReason('x'.repeat(1001))).toBe(
      'payroll.validation.reasonTooLong',
    );
  });
});

/** The date input's value, which defaults to the center's today. */
function labelledToday(): string {
  return new Intl.DateTimeFormat('en-CA', {
    timeZone: TEST_USER.center_time_zone,
  }).format(new Date());
}
