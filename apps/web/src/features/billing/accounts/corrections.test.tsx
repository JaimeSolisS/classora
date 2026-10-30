import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { SessionUserBody } from '@/features/auth/session';
import { problemWith } from '@/features/academics/testing';
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
import {
  ADJUSTMENT_POSTED,
  CHARGE_DETAIL,
  CHARGE_OVERDUE,
  CHARGE_PACKAGE_DETAIL,
  CHARGE_PARTIAL,
  CHARGES_PAGE,
  EMPTY_LEDGER_PAGE,
  FAMILY_ID,
  LUIS,
  MANUAL_CHARGE_POSTED,
  OPEN_CHARGES,
  PAYMENT_APPLIED_DETAIL,
  PAYMENT_DETAIL,
  PAYMENT_ID,
  PAYMENT_METHODS,
  PAYMENT_REFUNDED_DETAIL,
  PAYMENT_REVERSED_DETAIL,
  PAYMENTS_PAGE,
  REFUNDED,
  REVERSED,
  SUMMARY,
  TODAY,
} from './fixtures';

// #386 F1 (A-D): the corrections on the family account page behind a
// stubbed fetch. Which buttons each permission shows (billing.manage,
// finance.refund.approve, neither); "Add charge" (validation, body,
// Idempotency-Key, the 422 on its field, a new key after a success); the
// charge detail (amounts, adjustments with their reason, allocations) and
// "Adjust" (hidden on a package purchase, a credit above the net blocked,
// the signed body, the password dialog and the retry with the SAME key, a
// cancelled password dialog, a network retry with the same key, a changed
// form with a new key, nothing changed before the 201, each 409 with the
// reload); the payment detail's reversal and refunds, "Reverse payment"
// and "Refund" (defaults, limits, bodies, each 409 and 422 code).

const BASE = `/api/v1/families/${FAMILY_ID}/billing-account`;
const SUMMARY_ROUTE = `GET ${BASE}`;
const CHARGES = `GET ${BASE}/charges`;
const CHARGE = (id: string) => `GET ${BASE}/charges/${id}`;
const ADD_CHARGE = `POST ${BASE}/charges`;
const ADJUST = (id: string) => `POST ${BASE}/charges/${id}/adjustments`;
const LEDGER = `GET ${BASE}/ledger`;
const PAYMENTS = `GET ${BASE}/payments`;
const PAYMENT = `GET ${BASE}/payments/${PAYMENT_ID}`;
const REVERSE = `POST ${BASE}/payments/${PAYMENT_ID}/reversal`;
const REFUND = `POST ${BASE}/payments/${PAYMENT_ID}/refunds`;
const METHODS = 'GET /api/v1/billing/payment-methods';
const REAUTH = 'POST /api/v1/auth/reauthenticate';
const ME = 'GET /api/v1/auth/me';

const user = (
  permissions: SessionUserBody['permissions'],
): SessionUserBody => ({
  ...TEST_USER,
  roles: ['owner'],
  permissions,
});
const OWNER = user([
  'billing.view',
  'billing.manage',
  'payment.record',
  'finance.refund.approve',
]);
const FINANCE = user(['billing.view', 'billing.manage', 'payment.record']);
const DELEGATED = user(['billing.view', 'finance.refund.approve']);
const VIEWER = user(['billing.view']);

function stub(
  routes: Record<string, Responder | Responder[]> = {},
  as: SessionUserBody = OWNER,
): ApiStub {
  return stubApi({
    [ME]: meAs(as),
    [SUMMARY_ROUTE]: () => jsonResponse(200, SUMMARY),
    [CHARGES]: (request) => {
      const url = new URL(request.url);
      if (url.searchParams.get('limit') === '100') {
        return jsonResponse(200, {
          items: OPEN_CHARGES,
          total: OPEN_CHARGES.length,
          limit: 100,
          offset: 0,
        });
      }
      return jsonResponse(200, CHARGES_PAGE);
    },
    [CHARGE(CHARGE_OVERDUE.id)]: () => jsonResponse(200, CHARGE_DETAIL),
    [CHARGE(CHARGE_PARTIAL.id)]: () => jsonResponse(200, CHARGE_PACKAGE_DETAIL),
    [LEDGER]: () => jsonResponse(200, EMPTY_LEDGER_PAGE),
    [PAYMENTS]: () => jsonResponse(200, PAYMENTS_PAGE),
    [PAYMENT]: () => jsonResponse(200, PAYMENT_DETAIL),
    [METHODS]: () => jsonResponse(200, PAYMENT_METHODS),
    [REAUTH]: () =>
      jsonResponse(200, { reauthenticated_until: '2026-10-20T18:05:00Z' }),
    ...routes,
  });
}

let app: RenderedApp | undefined;

const main = () => app!.container.querySelector('main')!;
const dialogs = () => [
  ...document.querySelectorAll<HTMLElement>('[role="dialog"]'),
];
const dialogTitled = (title: string) =>
  dialogs().find((d) => d.querySelector('h2')?.textContent === title) ?? null;
const titled = (title: string) => {
  const found = dialogTitled(title);
  if (!found) throw new Error(`No dialog "${title}"`);
  return found;
};
const status = () => main().querySelector('[role="status"]')?.textContent;
const statusIn = (title: string) =>
  titled(title).querySelector('[role="status"]')?.textContent;
const keyOf = (call: { headers: Headers }) =>
  call.headers.get('Idempotency-Key');
const buttonTexts = (root: ParentNode) =>
  [...root.querySelectorAll('button')].map((button) => button.textContent);
const radio = (root: ParentNode, text: string) => {
  const label = [...root.querySelectorAll('label')].find(
    (candidate) => candidate.textContent === text,
  );
  const found = label?.querySelector('input');
  if (!found) throw new Error(`No choice "${text}"`);
  return found;
};

async function renderAccount() {
  app = await renderApp(`/billing/accounts/${FAMILY_ID}`);
  await settleUntil(() => main().querySelector('[data-charge-id]'));
}

async function openCharge(chargeId: string) {
  const row = main().querySelector(`[data-charge-id="${chargeId}"]`)!;
  await click(buttonNamed(row, 'Details'));
  await settleUntil(() => dialogTitled('Charge')?.querySelector('dl'));
}

async function openAdjust() {
  await openCharge(CHARGE_OVERDUE.id);
  await click(buttonNamed(titled('Charge'), 'Adjust'));
  await settleUntil(() => dialogTitled('Adjust the charge'));
}

function fillAdjustment(amount: string, reason = 'Error de captura') {
  const dialog = titled('Adjust the charge');
  setFieldValue(labelled(dialog, 'Amount'), amount);
  setFieldValue(labelled(dialog, 'Reason'), reason);
}

async function saveAdjustment() {
  await click(buttonNamed(titled('Adjust the charge'), 'Save adjustment'));
}

async function openPayment() {
  const row = main().querySelector(`[data-payment-id="${PAYMENT_ID}"]`)!;
  await click(buttonNamed(row, 'View'));
  await settleUntil(() => dialogTitled('Payment')?.querySelector('dl'));
}

async function confirmPassword() {
  const dialog = titled('Confirm your password');
  setFieldValue(labelled<HTMLInputElement>(dialog, 'Password'), 'mi-clave');
  await click(buttonNamed(dialog, 'Confirm'));
}

beforeEach(() => {
  applyLanguage('en');
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

describe('who sees which correction (F1)', () => {
  it('billing.manage adds and adjusts charges, but neither reverses nor refunds', async () => {
    stub({}, FINANCE);
    await renderAccount();
    expect(buttonTexts(main())).toContain('Add charge');

    await openCharge(CHARGE_OVERDUE.id);
    expect(buttonTexts(titled('Charge'))).toContain('Adjust');
    await click(buttonNamed(titled('Charge'), 'Close'));

    await openPayment();
    const payment = titled('Payment');
    expect(buttonTexts(payment)).not.toContain('Reverse payment');
    expect(buttonTexts(payment)).not.toContain('Refund');
  });

  it('finance.refund.approve reverses and refunds, but neither adds nor adjusts', async () => {
    stub({}, DELEGATED);
    await renderAccount();
    expect(buttonTexts(main())).not.toContain('Add charge');

    await openCharge(CHARGE_OVERDUE.id);
    expect(buttonTexts(titled('Charge'))).not.toContain('Adjust');
    await click(buttonNamed(titled('Charge'), 'Close'));

    await openPayment();
    expect(buttonTexts(titled('Payment'))).toEqual(
      expect.arrayContaining(['Reverse payment', 'Refund']),
    );
  });

  it('billing.view alone reads the details without any correction', async () => {
    stub({}, VIEWER);
    await renderAccount();
    expect(buttonTexts(main())).not.toContain('Add charge');

    await openCharge(CHARGE_OVERDUE.id);
    const charge = titled('Charge');
    expect(buttonTexts(charge)).not.toContain('Adjust');
    await click(buttonNamed(charge, 'Close'));

    await openPayment();
    expect(buttonTexts(titled('Payment'))).not.toContain('Reverse payment');
    expect(buttonTexts(titled('Payment'))).not.toContain('Refund');
  });
});

describe('add a charge (A1)', () => {
  it('checks the form before sending anything', async () => {
    const api = stub();
    await renderAccount();
    await click(buttonNamed(main(), 'Add charge'));
    const dialog = titled('Add a charge');
    expect(labelled<HTMLInputElement>(dialog, 'Service date').value).toBe(
      TODAY,
    );
    expect(labelled<HTMLInputElement>(dialog, 'Due date').value).toBe(TODAY);
    expect(
      [...labelled<HTMLSelectElement>(dialog, 'Student').options].map(
        (option) => option.textContent,
      ),
    ).toEqual(['Choose an option', 'Ana López', 'Luis López']);

    setFieldValue(labelled(dialog, 'Amount'), '0');
    await click(buttonNamed(dialog, 'Add charge'));

    expect(dialog.textContent).toContain('Choose the student.');
    expect(dialog.textContent).toContain('Enter the description.');
    expect(dialog.textContent).toContain('Enter an amount greater than 0.');
    expect(labelled(dialog, 'Student').getAttribute('aria-invalid')).toBe(
      'true',
    );
    expect(api.calls(ADD_CHARGE)).toHaveLength(0);
  });

  it('posts the charge with a key, announces it and refreshes the account', async () => {
    const api = stub({
      [ADD_CHARGE]: () => jsonResponse(201, MANUAL_CHARGE_POSTED),
    });
    await renderAccount();
    await click(buttonNamed(main(), 'Add charge'));
    const dialog = titled('Add a charge');
    setFieldValue(labelled(dialog, 'Student'), LUIS.id);
    setFieldValue(
      labelled(dialog, 'Description'),
      '  Material de laboratorio ',
    );
    setFieldValue(labelled(dialog, 'Amount'), '350');
    // The due date follows the service date until it is changed.
    setFieldValue(labelled(dialog, 'Service date'), '2026-10-18');
    expect(labelled<HTMLInputElement>(dialog, 'Due date').value).toBe(
      '2026-10-18',
    );
    setFieldValue(labelled(dialog, 'Due date'), '2026-10-30');
    const summaries = api.calls(SUMMARY_ROUTE).length;
    await click(buttonNamed(dialog, 'Add charge'));
    await settleUntil(() => dialogTitled('Add a charge') === null);

    const [call] = api.calls(ADD_CHARGE);
    expect(call!.body).toEqual({
      student_id: LUIS.id,
      description: 'Material de laboratorio',
      amount: '350',
      service_date: '2026-10-18',
      due_date: '2026-10-30',
    });
    expect(keyOf(call!)).toMatch(/^[A-Za-z0-9_-]{16,128}$/);
    expect(api.calls(REAUTH)).toHaveLength(0);
    expect(status()).toBe('Charge of $350.00 added for Luis López.');
    await settleUntil(() => api.calls(SUMMARY_ROUTE).length > summaries);

    // The next charge is a new submission.
    await click(buttonNamed(main(), 'Add charge'));
    const again = titled('Add a charge');
    setFieldValue(labelled(again, 'Student'), LUIS.id);
    setFieldValue(labelled(again, 'Description'), 'Material de laboratorio');
    setFieldValue(labelled(again, 'Amount'), '350');
    await click(buttonNamed(again, 'Add charge'));
    await settleUntil(() => api.calls(ADD_CHARGE).length === 2);
    expect(keyOf(api.calls(ADD_CHARGE)[1]!)).not.toBe(keyOf(call!));
  });

  it('puts student_not_in_family on the student and keeps the form', async () => {
    stub({
      [ADD_CHARGE]: () =>
        problemWith(422, 'student_not_in_family', {
          errors: [
            { path: ['body', 'student_id'], code: 'student_not_in_family' },
          ],
        }),
    });
    await renderAccount();
    await click(buttonNamed(main(), 'Add charge'));
    const dialog = titled('Add a charge');
    setFieldValue(labelled(dialog, 'Student'), LUIS.id);
    setFieldValue(labelled(dialog, 'Description'), 'Material');
    setFieldValue(labelled(dialog, 'Amount'), '350');
    await click(buttonNamed(dialog, 'Add charge'));
    await settleUntil(() =>
      dialog.textContent?.includes(
        "This student isn't a current member of the family.",
      ),
    );
    expect(labelled(dialog, 'Student').getAttribute('aria-invalid')).toBe(
      'true',
    );
    expect(labelled<HTMLInputElement>(dialog, 'Description').value).toBe(
      'Material',
    );
  });
});

describe('the charge detail (A2)', () => {
  it('shows the amounts, the adjustments with their reason and the allocations', async () => {
    const api = stub();
    await renderAccount();
    await openCharge(CHARGE_OVERDUE.id);
    const dialog = titled('Charge');

    expect(api.calls(CHARGE(CHARGE_OVERDUE.id))).toHaveLength(1);
    const terms = Object.fromEntries(
      [...dialog.querySelectorAll('dl > div')].map((pair) => [
        pair.querySelector('dt')!.textContent,
        pair.querySelector('dd')!.textContent,
      ]),
    );
    expect(terms).toMatchObject({
      Amount: '$1,600.00',
      Adjustments: '−$100.00',
      Net: '$1,500.00',
      Paid: '$0.00',
      Open: '$1,500.00',
      Status: 'Overdue',
    });
    const adjustment = dialog.querySelector('[data-charge-adjustment]')!;
    expect(adjustment.textContent).toContain('Cancellation credit');
    expect(adjustment.textContent).toContain('−$100.00');
    expect(adjustment.textContent).toContain('Clase cancelada por el centro');
    const allocation = dialog.querySelector('[data-charge-allocation]')!;
    expect(allocation.textContent).toContain('$200.00');
    expect(allocation.textContent).toContain('Released');

    // "View payment" opens the payment it came from.
    await click(buttonNamed(allocation, 'View payment'));
    await settleUntil(() => dialogTitled('Payment')?.querySelector('dl'));
    expect(dialogTitled('Charge')).toBeNull();
  });

  it('hides "Adjust" on a package purchase and says where to cancel it', async () => {
    stub();
    await renderAccount();
    await openCharge(CHARGE_PARTIAL.id);
    const dialog = titled('Charge');
    expect(buttonTexts(dialog)).not.toContain('Adjust');
    expect(dialog.textContent).toContain(
      'A package purchase is corrected by cancelling the package in Billing → Packages.',
    );
  });
});

describe('adjust a charge (A3, D1-D3)', () => {
  it('shows the net and blocks a credit above it', async () => {
    const api = stub();
    await renderAccount();
    await openAdjust();
    const dialog = titled('Adjust the charge');
    expect(dialog.textContent).toContain('Current net: $1,500.00');
    expect(dialog.textContent).toContain(
      "You'll be asked to confirm your password.",
    );
    expect(radio(dialog, 'Credit (reduce)').checked).toBe(true);

    fillAdjustment('1500.01', '');
    await saveAdjustment();
    expect(dialog.textContent).toContain(
      "A credit can't be more than the charge's net.",
    );
    expect(dialog.textContent).toContain('Enter the reason.');
    expect(api.calls(ADJUST(CHARGE_OVERDUE.id))).toHaveLength(0);

    // A debit has no upper bound here.
    await click(radio(dialog, 'Debit (increase)'));
    fillAdjustment('1500.01');
    await saveAdjustment();
    await settleUntil(() => api.calls(ADJUST(CHARGE_OVERDUE.id)).length === 1);
    expect(api.calls(ADJUST(CHARGE_OVERDUE.id))[0]!.body).toEqual({
      amount: '1500.01',
      reason: 'Error de captura',
    });
  });

  it('asks for the password and retries once with the same key', async () => {
    const api = stub({
      [ADJUST(CHARGE_OVERDUE.id)]: [
        () => problem(403, 'reauth_required'),
        () => jsonResponse(201, ADJUSTMENT_POSTED),
      ],
    });
    await renderAccount();
    await openAdjust();
    fillAdjustment(' 250 ');
    const summaries = api.calls(SUMMARY_ROUTE).length;
    const details = api.calls(CHARGE(CHARGE_OVERDUE.id)).length;
    await saveAdjustment();
    await settleUntil(() => dialogTitled('Confirm your password'));
    // Nothing changed before the server confirmed.
    expect(api.calls(SUMMARY_ROUTE)).toHaveLength(summaries);
    expect(status()).toBe('');
    await confirmPassword();
    await settleUntil(() => dialogTitled('Adjust the charge') === null);

    const calls = api.calls(ADJUST(CHARGE_OVERDUE.id));
    expect(calls.map((call) => call.body)).toEqual([
      { amount: '-250', reason: 'Error de captura' },
      { amount: '-250', reason: 'Error de captura' },
    ]);
    expect(keyOf(calls[1]!)).toBe(keyOf(calls[0]!));
    expect(api.calls(REAUTH)).toHaveLength(1);
    const done =
      "Adjustment of −$250.00 saved. The charge's net is now $1,250.00.";
    expect(status()).toBe(done);
    expect(statusIn('Charge')).toBe(done);
    await settleUntil(
      () =>
        api.calls(SUMMARY_ROUTE).length > summaries &&
        api.calls(CHARGE(CHARGE_OVERDUE.id)).length > details,
    );
  });

  it('keeps the form without an error when the password dialog is cancelled', async () => {
    const api = stub({
      [ADJUST(CHARGE_OVERDUE.id)]: () => problem(403, 'reauth_required'),
    });
    await renderAccount();
    await openAdjust();
    fillAdjustment('250');
    await saveAdjustment();
    await settleUntil(() => dialogTitled('Confirm your password'));
    await click(buttonNamed(titled('Confirm your password'), 'Cancel'));
    await settleUntil(() => dialogTitled('Confirm your password') === null);

    const dialog = titled('Adjust the charge');
    expect(alerts(dialog)).toEqual([]);
    expect(labelled<HTMLInputElement>(dialog, 'Amount').value).toBe('250');
    expect(api.calls(ADJUST(CHARGE_OVERDUE.id))).toHaveLength(1);
    expect(status()).toBe('');
  });

  it('resends the same key after a network error and a new one for a changed form', async () => {
    const api = stub({
      [ADJUST(CHARGE_OVERDUE.id)]: [
        () => Promise.reject(new TypeError('Failed to fetch')),
        () => Promise.reject(new TypeError('Failed to fetch')),
        () => jsonResponse(201, ADJUSTMENT_POSTED),
      ],
    });
    await renderAccount();
    await openAdjust();
    fillAdjustment('250');
    await saveAdjustment();
    await settleUntil(() => alerts(titled('Adjust the charge')).length > 0);
    expect(alerts(titled('Adjust the charge')).join()).toContain(
      "Can't reach the server",
    );
    await saveAdjustment();
    await settleUntil(() => api.calls(ADJUST(CHARGE_OVERDUE.id)).length === 2);
    await settleUntil(() => alerts(titled('Adjust the charge')).length > 0);
    fillAdjustment('251');
    await saveAdjustment();
    await settleUntil(() => dialogTitled('Adjust the charge') === null);

    const [first, second, third] = api.calls(ADJUST(CHARGE_OVERDUE.id));
    expect(keyOf(second!)).toBe(keyOf(first!));
    expect(keyOf(third!)).not.toBe(keyOf(first!));
  });

  it.each([
    [
      'charge_net_negative',
      'This would take the charge below $0.00. The amounts were reloaded; check them and try again.',
    ],
    [
      'package_purchase_not_adjustable',
      "A package purchase can't be adjusted. Cancel the package in Billing → Packages instead.",
    ],
  ])('%s is an alert and reloads the charge', async (code, text) => {
    const api = stub({
      [ADJUST(CHARGE_OVERDUE.id)]: () => problemWith(409, code),
    });
    await renderAccount();
    await openAdjust();
    fillAdjustment('250');
    const details = api.calls(CHARGE(CHARGE_OVERDUE.id)).length;
    await saveAdjustment();
    await settleUntil(() => alerts(titled('Adjust the charge')).length > 0);
    expect(alerts(titled('Adjust the charge')).join()).toContain(text);
    await settleUntil(
      () => api.calls(CHARGE(CHARGE_OVERDUE.id)).length > details,
    );
  });

  it('shows the reloaded net in the open dialog after a 409 (QA D2)', async () => {
    const api = stub({
      [CHARGE(CHARGE_OVERDUE.id)]: [
        () => jsonResponse(200, CHARGE_DETAIL),
        () => jsonResponse(200, { ...CHARGE_DETAIL, net: '1250.00' }),
      ],
      [ADJUST(CHARGE_OVERDUE.id)]: () =>
        problemWith(409, 'charge_net_negative'),
    });
    await renderAccount();
    await openAdjust();
    fillAdjustment('1400');
    await saveAdjustment();
    const dialog = titled('Adjust the charge');
    await settleUntil(() =>
      dialog.textContent?.includes('Current net: $1,250.00'),
    );
    // The client cap now uses the reloaded net.
    await saveAdjustment();
    expect(dialog.textContent).toContain(
      "A credit can't be more than the charge's net.",
    );
    expect(api.calls(ADJUST(CHARGE_OVERDUE.id))).toHaveLength(1);
  });

  it('puts amount_zero on the amount and shows permission_denied as an alert', async () => {
    stub({
      [ADJUST(CHARGE_OVERDUE.id)]: [
        () =>
          problemWith(422, 'validation_error', {
            errors: [{ path: ['body', 'amount'], code: 'amount_zero' }],
          }),
        () => problemWith(403, 'permission_denied'),
      ],
    });
    await renderAccount();
    await openAdjust();
    fillAdjustment('250');
    await saveAdjustment();
    const dialog = titled('Adjust the charge');
    await settleUntil(() =>
      dialog.textContent?.includes('Enter an amount other than 0.'),
    );
    expect(labelled(dialog, 'Amount').getAttribute('aria-invalid')).toBe(
      'true',
    );

    fillAdjustment('260');
    await saveAdjustment();
    await settleUntil(() => alerts(dialog).length > 0);
    expect(alerts(dialog).join()).toContain(
      "You don't have permission to do this.",
    );
  });
});

describe('the payment detail (B1)', () => {
  it('shows a reversal with its reason and offers nothing', async () => {
    stub({ [PAYMENT]: () => jsonResponse(200, PAYMENT_REVERSED_DETAIL) });
    await renderAccount();
    await openPayment();
    const dialog = titled('Payment');
    expect(
      dialog.querySelector('[data-slot="payment-reversed-badge"]'),
    ).not.toBeNull();
    const reversal = dialog.querySelector('[data-payment-reversal]')!;
    expect(reversal.textContent).toContain('Reversed on');
    expect(reversal.textContent).toContain(
      'Transferencia rechazada por el banco',
    );
    expect(buttonTexts(dialog)).not.toContain('Reverse payment');
    expect(buttonTexts(dialog)).not.toContain('Refund');
  });

  it('lists the refunds and says why a refunded payment cannot be reversed', async () => {
    stub({ [PAYMENT]: () => jsonResponse(200, PAYMENT_REFUNDED_DETAIL) });
    await renderAccount();
    await openPayment();
    const dialog = titled('Payment');
    const refund = dialog.querySelector('[data-payment-refund]')!;
    expect(refund.textContent).toContain('$100.00');
    expect(refund.textContent).toContain('Cash');
    expect(refund.textContent).toContain('Caja 2');
    expect(refund.textContent).toContain('Pago de más');
    expect(buttonTexts(dialog)).not.toContain('Reverse payment');
    expect(dialog.textContent).toContain(
      "A payment with refunds can't be reversed.",
    );
    expect(buttonTexts(dialog)).toContain('Refund');
  });

  it('offers no refund when everything is applied', async () => {
    stub({ [PAYMENT]: () => jsonResponse(200, PAYMENT_APPLIED_DETAIL) });
    await renderAccount();
    await openPayment();
    expect(buttonTexts(titled('Payment'))).toContain('Reverse payment');
    expect(buttonTexts(titled('Payment'))).not.toContain('Refund');
  });

  it('marks a reversed payment in the list', async () => {
    stub({
      [PAYMENTS]: () =>
        jsonResponse(200, {
          ...PAYMENTS_PAGE,
          items: [
            { ...PAYMENTS_PAGE.items[0]!, reversed_at: '2026-10-15T17:00:00Z' },
          ],
        }),
    });
    await renderAccount();
    await settleUntil(() => main().querySelector('[data-payment-id]'));
    expect(
      main()
        .querySelector(`[data-payment-id="${PAYMENT_ID}"]`)!
        .querySelector('[data-slot="payment-reversed-badge"]'),
    ).not.toBeNull();
  });
});

describe('reverse a payment (B2)', () => {
  it('explains the effects, needs a reason and retries with the same key', async () => {
    const api = stub({
      [REVERSE]: [
        () => problem(403, 'reauth_required'),
        () => jsonResponse(201, REVERSED),
      ],
    });
    await renderAccount();
    await openPayment();
    await click(buttonNamed(titled('Payment'), 'Reverse payment'));
    const dialog = titled('Reverse the payment');
    expect(dialog.textContent).toContain(
      'Every charge it paid is released and reopens.',
    );
    expect(dialog.textContent).toContain(
      'To fix a mis-allocation, reverse the payment and record it again.',
    );
    await click(buttonNamed(dialog, 'Reverse payment'));
    expect(dialog.textContent).toContain('Enter the reason.');
    expect(api.calls(REVERSE)).toHaveLength(0);

    setFieldValue(labelled(dialog, 'Reason'), 'Transferencia rechazada');
    await click(buttonNamed(dialog, 'Reverse payment'));
    await settleUntil(() => dialogTitled('Confirm your password'));
    await confirmPassword();
    await settleUntil(() => dialogTitled('Reverse the payment') === null);

    const calls = api.calls(REVERSE);
    expect(calls.map((call) => call.body)).toEqual([
      { reason: 'Transferencia rechazada' },
      { reason: 'Transferencia rechazada' },
    ]);
    expect(keyOf(calls[1]!)).toBe(keyOf(calls[0]!));
    const done = 'Payment of $1,500.00 reversed. Account credit is now $0.00.';
    expect(status()).toBe(done);
    expect(statusIn('Payment')).toBe(done);
  });

  it.each([
    [
      'payment_already_reversed',
      'This payment was already reversed. The payment was reloaded.',
    ],
    [
      'payment_has_refunds',
      "A payment with refunds can't be reversed. The payment was reloaded.",
    ],
  ])('%s is an alert and reloads the payment', async (code, text) => {
    const api = stub({ [REVERSE]: () => problemWith(409, code) });
    await renderAccount();
    await openPayment();
    await click(buttonNamed(titled('Payment'), 'Reverse payment'));
    const dialog = titled('Reverse the payment');
    setFieldValue(labelled(dialog, 'Reason'), 'Duplicado');
    const reads = api.calls(PAYMENT).length;
    await click(buttonNamed(dialog, 'Reverse payment'));
    await settleUntil(() => alerts(dialog).length > 0);
    expect(alerts(dialog).join()).toContain(text);
    await settleUntil(() => api.calls(PAYMENT).length > reads);
  });
});

describe('refund a payment (B3)', () => {
  async function openRefund() {
    await renderAccount();
    await openPayment();
    await click(buttonNamed(titled('Payment'), 'Refund'));
    await settleUntil(() =>
      dialogTitled('Refund from the payment')?.querySelector('form'),
    );
    return titled('Refund from the payment');
  }

  it('starts from the unallocated amount and today, within the limits', async () => {
    const api = stub();
    const dialog = await openRefund();
    expect(labelled<HTMLInputElement>(dialog, 'Amount').value).toBe('300.00');
    const date = labelled<HTMLInputElement>(dialog, 'Refunded on');
    expect(date.value).toBe(TODAY);
    expect(date.min).toBe('2026-10-12');
    expect(date.max).toBe(TODAY);
    expect(
      [...labelled<HTMLSelectElement>(dialog, 'Method').options].map(
        (option) => option.textContent,
      ),
    ).toEqual(['Choose an option', 'Cash', 'Transfer']);
    expect(dialog.textContent).toContain("A refund can't be undone.");

    setFieldValue(labelled(dialog, 'Amount'), '300.01');
    setFieldValue(labelled(dialog, 'Refunded on'), '2026-10-11');
    await click(buttonNamed(dialog, 'Record refund'));
    expect(dialog.textContent).toContain(
      "This is more than what the payment hasn't applied.",
    );
    expect(dialog.textContent).toContain(
      "The refund date can't be before the day the payment was received.",
    );
    expect(dialog.textContent).toContain('Choose the method.');
    expect(dialog.textContent).toContain('Enter the reason.');
    expect(api.calls(REFUND)).toHaveLength(0);
  });

  it('posts the refund with the same key across the password dialog', async () => {
    const api = stub({
      [REFUND]: [
        () => problem(403, 'reauth_required'),
        () => jsonResponse(201, REFUNDED),
      ],
    });
    const dialog = await openRefund();
    setFieldValue(labelled(dialog, 'Method'), 'cash');
    setFieldValue(labelled(dialog, 'Reference (optional)'), '  ');
    setFieldValue(labelled(dialog, 'Reason'), ' Pago de más ');
    await click(buttonNamed(dialog, 'Record refund'));
    await settleUntil(() => dialogTitled('Confirm your password'));
    await confirmPassword();
    await settleUntil(() => dialogTitled('Refund from the payment') === null);

    const calls = api.calls(REFUND);
    const body = {
      amount: '300.00',
      refunded_on: TODAY,
      payment_method_code: 'cash',
      reference: null,
      reason: 'Pago de más',
    };
    expect(calls.map((call) => call.body)).toEqual([body, body]);
    expect(keyOf(calls[1]!)).toBe(keyOf(calls[0]!));
    expect(status()).toBe(
      'Refund of $300.00 recorded. Account credit is now $0.00.',
    );
  });

  it('puts the 422 date codes on the date', async () => {
    stub({
      [REFUND]: [
        () =>
          problemWith(422, 'refunded_on_in_future', {
            errors: [
              { path: ['body', 'refunded_on'], code: 'refunded_on_in_future' },
            ],
          }),
        () =>
          problemWith(422, 'refunded_on_before_payment', {
            errors: [
              {
                path: ['body', 'refunded_on'],
                code: 'refunded_on_before_payment',
              },
            ],
          }),
      ],
    });
    const dialog = await openRefund();
    setFieldValue(labelled(dialog, 'Method'), 'cash');
    setFieldValue(labelled(dialog, 'Reason'), 'Pago de más');
    await click(buttonNamed(dialog, 'Record refund'));
    await settleUntil(() =>
      dialog.textContent?.includes("The refund date can't be after today."),
    );
    expect(labelled(dialog, 'Refunded on').getAttribute('aria-invalid')).toBe(
      'true',
    );
    setFieldValue(labelled(dialog, 'Refunded on'), '2026-10-13');
    await click(buttonNamed(dialog, 'Record refund'));
    await settleUntil(() =>
      dialog.textContent?.includes(
        "The refund date can't be before the day the payment was received.",
      ),
    );
  });

  it('shows the reloaded unallocated amount after a 409 (QA D2)', async () => {
    const api = stub({
      [PAYMENT]: [
        () => jsonResponse(200, PAYMENT_DETAIL),
        () =>
          jsonResponse(200, {
            ...PAYMENT_DETAIL,
            unallocated_amount: '100.00',
          }),
      ],
      [REFUND]: () => problemWith(409, 'refund_exceeds_remainder'),
    });
    const dialog = await openRefund();
    setFieldValue(labelled(dialog, 'Method'), 'cash');
    setFieldValue(labelled(dialog, 'Reason'), 'Pago de más');
    await click(buttonNamed(dialog, 'Record refund'));
    await settleUntil(() => dialog.textContent?.includes('At most $100.00.'));
    expect(dialog.textContent).toContain("hasn't applied ($100.00)");
    await click(buttonNamed(dialog, 'Record refund'));
    expect(dialog.textContent).toContain(
      "This is more than what the payment hasn't applied.",
    );
    expect(api.calls(REFUND)).toHaveLength(1);
  });

  it.each([
    [
      'refund_exceeds_remainder',
      "That is more than what the payment hasn't applied. The payment was reloaded; check the amount and try again.",
    ],
    [
      'payment_reversed',
      "This payment was reversed, so it can't be refunded. The payment was reloaded.",
    ],
  ])('%s is an alert and reloads the payment', async (code, text) => {
    const api = stub({ [REFUND]: () => problemWith(409, code) });
    const dialog = await openRefund();
    setFieldValue(labelled(dialog, 'Method'), 'cash');
    setFieldValue(labelled(dialog, 'Reason'), 'Pago de más');
    const reads = api.calls(PAYMENT).length;
    await click(buttonNamed(dialog, 'Record refund'));
    await settleUntil(() => alerts(dialog).length > 0);
    expect(alerts(dialog).join()).toContain(text);
    await settleUntil(() => api.calls(PAYMENT).length > reads);
  });
});
