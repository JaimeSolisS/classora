import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { SessionUserBody } from '@/features/auth/session';
import { problemWith } from '@/features/academics/testing';
import { applyLanguage } from '@/i18n';
import {
  meAs,
  stubApi,
  TEST_USER,
  type ApiStub,
  type Responder,
} from '@/test/api-stub';
import { alerts, click, labelled, openDialog, setFieldValue } from '@/test/dom';
import {
  buttonNamed,
  jsonResponse,
  renderApp,
  settleUntil,
  type RenderedApp,
} from '@/test/render-app';
import type { CreditAllocated } from './api';
import {
  CHARGE_OVERDUE,
  CHARGE_PARTIAL,
  CHARGE_PENDING,
  CHARGES_PAGE,
  EMPTY_LEDGER_PAGE,
  EMPTY_PAYMENTS_PAGE,
  FAMILY_ID,
  OPEN_CHARGES,
  PAYMENT_METHODS,
  RECORDED,
  SUMMARY,
  SUMMARY_NO_CREDIT,
  TODAY,
} from './fixtures';

// #379 G1 (D, E): the record-payment dialog (the active methods, today by
// default, each allocation mode with its body, the oldest-first estimate,
// the chosen-charges editor's checks and "Fill", the Idempotency-Key
// header, the same key on a retry after a network error, a new key after a
// success or a change, nothing changed on the page before the 201, each
// D5 error) and the apply-credit dialog (each mode, the credit cap, each E2
// error with the reload).

const BASE = `/api/v1/families/${FAMILY_ID}/billing-account`;
const SUMMARY_ROUTE = `GET ${BASE}`;
const CHARGES = `GET ${BASE}/charges`;
const LEDGER = `GET ${BASE}/ledger`;
const PAYMENTS = `GET ${BASE}/payments`;
const RECORD = `POST ${BASE}/payments`;
const ALLOCATE = `POST ${BASE}/allocations`;
const METHODS = 'GET /api/v1/billing/payment-methods';
const ME = 'GET /api/v1/auth/me';

const FINANCE: SessionUserBody = {
  ...TEST_USER,
  roles: ['finance_staff'],
  permissions: ['guardian.view', 'billing.view', 'payment.record'],
};

/** The open-charges scans (status filter) and the list's pages. */
let openScans = 0;

function stub(
  routes: Record<string, Responder | Responder[]> = {},
  { summary = SUMMARY }: { summary?: typeof SUMMARY } = {},
): ApiStub {
  return stubApi({
    [ME]: meAs(FINANCE),
    [SUMMARY_ROUTE]: () => jsonResponse(200, summary),
    [CHARGES]: (request) => {
      const url = new URL(request.url);
      if (url.searchParams.get('limit') === '100') {
        openScans += 1;
        return jsonResponse(200, {
          items: OPEN_CHARGES,
          total: OPEN_CHARGES.length,
          limit: 100,
          offset: 0,
        });
      }
      return jsonResponse(200, CHARGES_PAGE);
    },
    [LEDGER]: () => jsonResponse(200, EMPTY_LEDGER_PAGE),
    [PAYMENTS]: () => jsonResponse(200, EMPTY_PAYMENTS_PAGE),
    [METHODS]: () => jsonResponse(200, PAYMENT_METHODS),
    ...routes,
  });
}

let app: RenderedApp | undefined;

const main = () => app!.container.querySelector('main')!;
const dialog = () => openDialog()!;
const status = () => main().querySelector('[role="status"]')?.textContent;
const choice = (root: ParentNode, text: string) => {
  const label = [...root.querySelectorAll('label')].find((candidate) =>
    candidate.textContent?.startsWith(text),
  );
  const found = label?.querySelector('input');
  if (!found) throw new Error(`No choice "${text}"`);
  return found;
};
const input = (label: string) => labelled<HTMLInputElement>(dialog(), label);
const select = (label: string) => labelled<HTMLSelectElement>(dialog(), label);
const line = (chargeId: string) =>
  dialog().querySelector<HTMLInputElement>(
    `[data-allocation-line="${chargeId}"] input`,
  )!;
const lineIds = () =>
  [...dialog().querySelectorAll('[data-allocation-line]')].map((row) =>
    row.getAttribute('data-allocation-line'),
  );
const keyOf = (call: { headers: Headers }) =>
  call.headers.get('Idempotency-Key');

async function renderAccount(api?: ApiStub) {
  app = await renderApp(`/billing/accounts/${FAMILY_ID}`);
  await settleUntil(() => main().querySelector('[data-charge-id]'));
  return api;
}

async function openRecord() {
  await click(buttonNamed(main(), 'Record payment'));
  await settleUntil(() => openDialog()?.querySelector('form'));
}

function fillPayment(amount = '2000') {
  setFieldValue(input('Amount'), amount);
  setFieldValue(select('Method'), 'cash');
}

async function submitRecord() {
  await click(buttonNamed(dialog(), 'Record payment'));
}

beforeEach(() => {
  applyLanguage('en');
  openScans = 0;
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

describe('the record dialog (D1, D2)', () => {
  it('offers the active methods and defaults to today, not later', async () => {
    stub();
    await renderAccount();
    await openRecord();

    expect(
      [...select('Method').options].map((option) => option.textContent),
    ).toEqual(['Choose an option', 'Cash', 'Transfer']);
    expect(input('Received on').value).toBe(TODAY);
    expect(input('Received on').max).toBe(TODAY);
    expect(choice(dialog(), 'Oldest first').checked).toBe(true);
  });

  it('records oldest first with an Idempotency-Key and announces the result', async () => {
    const api = stub({ [RECORD]: () => jsonResponse(201, RECORDED) });
    await renderAccount();
    await openRecord();
    fillPayment('2000');
    setFieldValue(input('Reference (optional)'), '  OXXO 1 ');

    // The estimate: the overdue charge, then the partially paid one.
    await settleUntil(() =>
      dialog().querySelector('[data-allocation-estimate]'),
    );
    expect(
      dialog().querySelector('[data-allocation-estimate]')?.textContent,
    ).toContain(
      'Estimate: $2,000.00 to 2 charges; $0.00 stays as account credit.',
    );
    expect(dialog().textContent).toContain(
      'Colegiatura septiembre · Ana López: $1,500.00',
    );
    expect(dialog().textContent).toContain(
      'Package purchase · Luis López: $500.00',
    );

    const summaries = api.calls(SUMMARY_ROUTE).length;
    await submitRecord();
    await settleUntil(() => openDialog() === null);

    const [call] = api.calls(RECORD);
    expect(call!.body).toEqual({
      amount: '2000',
      received_on: TODAY,
      payment_method_code: 'cash',
      reference: 'OXXO 1',
      payer_name: null,
      note: null,
      allocation: { mode: 'oldest_first' },
    });
    expect(keyOf(call!)).toMatch(/^[A-Za-z0-9_-]{16,128}$/);
    expect(status()).toBe(
      'Payment of $2,000.00 recorded: $2,000.00 applied to charges. Account credit is now $300.00.',
    );
    // #98 H1: the new receipt's number and its download link.
    const receipt = main().querySelector<HTMLAnchorElement>(
      `a[href$="/receipts/${RECORDED.receipt.id}/pdf"]`,
    )!;
    expect(receipt.getAttribute('href')).toBe(
      `${BASE}/receipts/${RECORDED.receipt.id}/pdf`,
    );
    expect(receipt.getAttribute('download')).toBe('R-2026-000124.pdf');
    expect(receipt.parentElement!.parentElement!.textContent).toBe(
      'Receipt: R-2026-000124Download receipt',
    );
    await settleUntil(() => api.calls(SUMMARY_ROUTE).length > summaries);
  });

  it('changes nothing before the server answers', async () => {
    let answer: (response: Response) => void = () => undefined;
    const api = stub({
      [RECORD]: () =>
        new Promise<Response>((resolve) => {
          answer = resolve;
        }),
    });
    await renderAccount();
    await openRecord();
    fillPayment();
    const summaries = api.calls(SUMMARY_ROUTE).length;
    const charges = api.calls(CHARGES).length;
    await submitRecord();
    await settleUntil(() => api.calls(RECORD).length === 1);

    expect(openDialog()).not.toBeNull();
    expect(dialog().textContent).toContain('Recording…');
    expect(api.calls(SUMMARY_ROUTE)).toHaveLength(summaries);
    expect(api.calls(CHARGES)).toHaveLength(charges);
    expect(status()).toBe('');

    answer(jsonResponse(201, RECORDED));
    await settleUntil(() => openDialog() === null);
    await settleUntil(() => api.calls(SUMMARY_ROUTE).length > summaries);
  });

  it('keeps a payment as credit', async () => {
    const api = stub({ [RECORD]: () => jsonResponse(201, RECORDED) });
    await renderAccount();
    await openRecord();
    fillPayment('100.5');
    await click(choice(dialog(), 'Keep as credit'));
    expect(dialog().textContent).toContain(
      'The whole payment stays as account credit.',
    );
    await submitRecord();
    await settleUntil(() => api.calls(RECORD).length === 1);
    expect(
      (api.calls(RECORD)[0]!.body as { allocation: unknown }).allocation,
    ).toEqual({ mode: 'none' });
  });

  it('allocates chosen charges, oldest due first, with Fill', async () => {
    const api = stub({ [RECORD]: () => jsonResponse(201, RECORDED) });
    await renderAccount();
    await openRecord();
    fillPayment('2500');
    await click(choice(dialog(), 'Choose charges'));
    await settleUntil(() => dialog().querySelector('[data-allocation-line]'));

    expect(lineIds()).toEqual([
      CHARGE_OVERDUE.id,
      CHARGE_PARTIAL.id,
      CHARGE_PENDING.id,
    ]);
    await click(buttonNamed(dialog(), 'Fill oldest first'));
    expect(line(CHARGE_OVERDUE.id).value).toBe('1500.00');
    expect(line(CHARGE_PARTIAL.id).value).toBe('800.00');
    expect(line(CHARGE_PENDING.id).value).toBe('200.00');
    expect(dialog().textContent).toContain(
      'Applied: $2,500.00 · Stays as account credit: $0.00',
    );

    setFieldValue(line(CHARGE_PARTIAL.id), '');
    setFieldValue(line(CHARGE_PENDING.id), '450');
    expect(dialog().textContent).toContain(
      'Applied: $1,950.00 · Stays as account credit: $550.00',
    );
    await submitRecord();
    await settleUntil(() => api.calls(RECORD).length === 1);
    expect(
      (api.calls(RECORD)[0]!.body as { allocation: unknown }).allocation,
    ).toEqual({
      mode: 'explicit',
      lines: [
        { charge_id: CHARGE_OVERDUE.id, amount: '1500.00' },
        { charge_id: CHARGE_PENDING.id, amount: '450.00' },
      ],
    });
  });

  it('blocks a row above its open amount, no rows, and a total above the payment', async () => {
    const api = stub();
    await renderAccount();
    await openRecord();
    fillPayment('1000');
    await click(choice(dialog(), 'Choose charges'));
    await settleUntil(() => dialog().querySelector('[data-allocation-line]'));

    await submitRecord();
    expect(dialog().textContent).toContain(
      'Enter an amount for at least one charge.',
    );

    setFieldValue(line(CHARGE_PARTIAL.id), '900');
    await submitRecord();
    expect(dialog().textContent).toContain(
      'This is more than what is open on the charge.',
    );
    expect(line(CHARGE_PARTIAL.id).getAttribute('aria-invalid')).toBe('true');

    setFieldValue(line(CHARGE_PARTIAL.id), '800');
    setFieldValue(line(CHARGE_PENDING.id), '450');
    expect(dialog().textContent).toContain(
      'That is $250.00 more than is available.',
    );
    await submitRecord();
    expect(dialog().textContent).toContain(
      'The amounts add up to more than the payment.',
    );
    expect(api.calls(RECORD)).toHaveLength(0);
  });

  it('checks the amount, the date and the method before sending', async () => {
    const api = stub();
    await renderAccount();
    await openRecord();
    setFieldValue(input('Received on'), '2026-10-21');
    await submitRecord();
    expect(dialog().textContent).toContain('Enter the amount.');
    expect(dialog().textContent).toContain("The date can't be after today.");
    expect(dialog().textContent).toContain('Choose the method.');

    setFieldValue(input('Amount'), '0');
    await submitRecord();
    expect(dialog().textContent).toContain('Enter an amount greater than 0.');
    setFieldValue(input('Amount'), '12.345');
    await submitRecord();
    expect(dialog().textContent).toContain(
      'Enter the amount in pesos, with up to 2 decimals.',
    );
    expect(api.calls(RECORD)).toHaveLength(0);
  });
});

describe('idempotent submission (D3)', () => {
  it('resends the same key after a network error, and a new one after a success', async () => {
    const api = stub({
      [RECORD]: [
        () => Promise.reject(new TypeError('Failed to fetch')),
        () => jsonResponse(201, RECORDED),
      ],
    });
    await renderAccount();
    await openRecord();
    fillPayment();
    await submitRecord();
    await settleUntil(() => alerts(dialog()).length > 0);
    expect(alerts(dialog()).join()).toContain("Can't reach the server");

    await submitRecord();
    await settleUntil(() => openDialog() === null);
    const [first, second] = api.calls(RECORD);
    expect(keyOf(second!)).toBe(keyOf(first!));

    // The same payment again is a new submission.
    await openRecord();
    fillPayment();
    await submitRecord();
    await settleUntil(() => api.calls(RECORD).length === 3);
    expect(keyOf(api.calls(RECORD)[2]!)).not.toBe(keyOf(first!));
  });

  it('uses a new key when the form changes after an error', async () => {
    const api = stub({ [RECORD]: () => problemWith(500, 'internal_error') });
    await renderAccount();
    await openRecord();
    fillPayment('2000');
    await submitRecord();
    await settleUntil(() => api.calls(RECORD).length >= 1);
    await settleUntil(() => alerts(dialog()).length > 0);
    const before = api.calls(RECORD).length;
    setFieldValue(input('Amount'), '2001');
    await submitRecord();
    await settleUntil(() => api.calls(RECORD).length > before);

    const calls = api.calls(RECORD);
    expect(keyOf(calls.at(-1)!)).not.toBe(keyOf(calls[0]!));
  });
});

describe('record errors (D5)', () => {
  it('puts 422 field errors on their fields and rows', async () => {
    stub({
      [RECORD]: () =>
        problemWith(422, 'validation_error', {
          errors: [
            { path: ['body', 'received_on'], code: 'received_on_in_future' },
            {
              path: ['body', 'payment_method_code'],
              code: 'payment_method_unavailable',
            },
            {
              path: ['body', 'allocation', 'lines', 1, 'amount'],
              code: 'amount_not_positive',
            },
          ],
        }),
    });
    await renderAccount();
    await openRecord();
    fillPayment('3000');
    await click(choice(dialog(), 'Choose charges'));
    await settleUntil(() => dialog().querySelector('[data-allocation-line]'));
    setFieldValue(line(CHARGE_OVERDUE.id), '100');
    setFieldValue(line(CHARGE_PENDING.id), '200');
    await submitRecord();
    await settleUntil(() =>
      dialog().textContent?.includes('This payment method'),
    );

    expect(input('Received on').getAttribute('aria-invalid')).toBe('true');
    expect(select('Method').getAttribute('aria-invalid')).toBe('true');
    // Line 1 of the body is the pending charge (rows in oldest-due order).
    expect(line(CHARGE_PENDING.id).getAttribute('aria-invalid')).toBe('true');
    expect(line(CHARGE_OVERDUE.id).getAttribute('aria-invalid')).toBeNull();
  });

  it('shows allocation_exceeds_open, marks the row and reloads the charges', async () => {
    stub({
      [RECORD]: () =>
        problemWith(409, 'allocation_exceeds_open', {
          details: { charge_id: CHARGE_PARTIAL.id, open: '300.00' },
        }),
    });
    await renderAccount();
    await openRecord();
    fillPayment('500');
    await click(choice(dialog(), 'Choose charges'));
    await settleUntil(() => dialog().querySelector('[data-allocation-line]'));
    const scans = openScans;
    setFieldValue(line(CHARGE_PARTIAL.id), '500');
    await submitRecord();
    await settleUntil(() => alerts(dialog()).length > 0);

    expect(alerts(dialog()).join()).toContain(
      'An amount is more than what is still open on that charge.',
    );
    expect(line(CHARGE_PARTIAL.id).getAttribute('aria-invalid')).toBe('true');
    await settleUntil(() => openScans > scans);
  });

  it.each([
    [422, 'idempotency_key_reused', 'This submission was already used'],
    [403, 'permission_denied', ''],
  ])('shows %s %s as an alert', async (code, problem, text) => {
    stub({ [RECORD]: () => problemWith(code, problem) });
    await renderAccount();
    await openRecord();
    fillPayment();
    await submitRecord();
    await settleUntil(() => alerts(dialog()).length > 0);
    if (text) expect(alerts(dialog()).join()).toContain(text);
    expect(openDialog()).not.toBeNull();
  });
});

describe('the apply-credit dialog (E)', () => {
  const ALLOCATED: CreditAllocated = {
    allocations: [],
    allocated_total: '300.00',
    account_credit_after: '0.00',
  };

  async function openCredit() {
    await click(buttonNamed(main(), 'Apply account credit'));
    await settleUntil(() => openDialog()?.querySelector('form'));
  }

  it('is offered only while the account has credit', async () => {
    stub({}, { summary: SUMMARY_NO_CREDIT });
    await renderAccount();
    expect(
      [...main().querySelectorAll('button')].map((b) => b.textContent),
    ).toEqual(expect.arrayContaining(['Record payment']));
    expect(
      [...main().querySelectorAll('button')].map((b) => b.textContent),
    ).not.toContain('Apply account credit');
  });

  it('applies the whole credit oldest first with a key', async () => {
    const api = stub({ [ALLOCATE]: () => jsonResponse(201, ALLOCATED) });
    await renderAccount();
    await openCredit();
    expect(dialog().textContent).toContain(
      'The family has $300.00 of account credit.',
    );
    await click(buttonNamed(dialog(), 'Apply credit'));
    await settleUntil(() => openDialog() === null);

    const [call] = api.calls(ALLOCATE);
    expect(call!.body).toEqual({ allocation: { mode: 'oldest_first' } });
    expect(keyOf(call!)).toMatch(/^[A-Za-z0-9_-]{16,128}$/);
    expect(status()).toBe(
      '$300.00 of account credit applied. Account credit is now $0.00.',
    );
  });

  it('caps the amount and the chosen charges by the credit', async () => {
    const api = stub({ [ALLOCATE]: () => jsonResponse(201, ALLOCATED) });
    await renderAccount();
    await openCredit();
    setFieldValue(input('Amount (optional)'), '300.01');
    await click(buttonNamed(dialog(), 'Apply credit'));
    expect(dialog().textContent).toContain(
      'This is more than the account credit.',
    );

    await click(choice(dialog(), 'Choose charges'));
    await settleUntil(() => dialog().querySelector('[data-allocation-line]'));
    await click(buttonNamed(dialog(), 'Fill oldest first'));
    expect(line(CHARGE_OVERDUE.id).value).toBe('300.00');
    setFieldValue(line(CHARGE_PENDING.id), '10');
    await click(buttonNamed(dialog(), 'Apply credit'));
    expect(dialog().textContent).toContain(
      'The amounts add up to more than the account credit.',
    );
    expect(api.calls(ALLOCATE)).toHaveLength(0);

    setFieldValue(line(CHARGE_PENDING.id), '');
    await click(buttonNamed(dialog(), 'Apply credit'));
    await settleUntil(() => api.calls(ALLOCATE).length === 1);
    expect(api.calls(ALLOCATE)[0]!.body).toEqual({
      allocation: {
        mode: 'explicit',
        lines: [{ charge_id: CHARGE_OVERDUE.id, amount: '300.00' }],
      },
    });
  });

  it.each([
    [
      'insufficient_account_credit',
      "That is more than the family's account credit.",
    ],
    ['nothing_to_allocate', 'There is no account credit or no open charge'],
    ['allocation_exceeds_open', 'An amount is more than what is still open'],
  ])('shows %s and reloads the summary and charges', async (code, text) => {
    const api = stub({ [ALLOCATE]: () => problemWith(409, code) });
    await renderAccount();
    await openCredit();
    const summaries = api.calls(SUMMARY_ROUTE).length;
    const scans = openScans;
    setFieldValue(input('Amount (optional)'), '100');
    await click(buttonNamed(dialog(), 'Apply credit'));
    await settleUntil(() => alerts(dialog()).length > 0);

    expect(alerts(dialog()).join()).toContain(text);
    await settleUntil(
      () => api.calls(SUMMARY_ROUTE).length > summaries && openScans > scans,
    );
  });
});
