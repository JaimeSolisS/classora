import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { SessionUserBody } from '@/features/auth/session';
import { problemWith } from '@/features/academics/testing';
import {
  COHORT_ID,
  ENROLLED_ANA as COHORT_ANA,
  ROSTER_COHORT,
  WITHDRAWN_CARLA as COHORT_CARLA,
} from '@/features/courses/cohorts/fixtures';
import {
  ACTIVE_ANA,
  GROUP,
  GROUP_ID,
  SESSIONS,
  WITHDRAWN_CARLA,
  WITHDRAWN_CARLA_EARLIER,
} from '@/features/groups/fixtures';
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
  settle,
  settleUntil,
  type RenderedApp,
} from '@/test/render-app';
import {
  CHARGE_DETAIL,
  CHARGES_PAGE,
  EMPTY_LEDGER_PAGE,
  FAMILY_ID,
  PAYMENT_METHODS,
  PAYMENTS_PAGE,
  SUMMARY,
  TODAY,
} from '../accounts/fixtures';
import type { LocalDate } from '@/lib/time';
import type { PackagePage } from '../packages/api';
import {
  PACKAGE_ACTIVE,
  PACKAGE_CANCELLED,
  PACKAGE_EXPIRED,
} from '../packages/fixtures';
import type { WithdrawalPreview } from './api';
import {
  blockedPreview,
  CLOSEOUT_SESSION_ID,
  COHORT_PREVIEW,
  FUTURE_SESSION_ID,
  GROUP_CONFIRMED,
  GROUP_PREVIEW,
  GROUP_SETTLE_PREVIEW,
  PACKAGE_CONFIRMED,
  PACKAGE_PREVIEW,
  PACKAGE_PREVIEW_FRESH,
  PACKAGE_PREVIEW_HOURS,
  PACKAGE_PREVIEW_PAID,
  PURCHASE_CHARGE_ID,
  WITHDRAWAL_DETAIL,
  WITHDRAWAL_ID,
  WITHDRAWAL_ITEM,
  WITHDRAWAL_REFUND_DETAIL,
  WITHDRAWAL_REFUND_ITEM,
  WITHDRAWALS_PAGE,
} from './fixtures';
import {
  emptySettlement,
  estimateTotals,
  mergeStale,
  minutesAsHours,
  nothingToSettle,
  settlementBody,
  type SettlementValues,
} from './rules';

// #387 D1: the rules (hours, the override estimate, the body, the stale
// merge); then the pages. Packages: "Withdraw (unused units)" per
// permission and status, the "Withdrawn" filter, the panel (units, line,
// warning, totals), the account-credit confirm with the password dialog
// and the same key, a network retry with the same key, the stale flow (a
// new key), the override estimate, the required reason, Refund (disabled
// with nothing freed, its fields and body), each blocking state, the error
// mapping. Rosters: the two-step dialog (Owner; Administrator without
// refunds), the plain withdrawal (Coordinator), no settlement for Finance
// Staff, "Settle billing" on unsettled withdrawn rows (one request per
// row). The account page: the Withdrawals section and its drawer.

const PACKAGES = 'GET /api/v1/billing/packages';
const PREVIEW = 'GET /api/v1/billing/withdrawal-preview';
const LIST = 'GET /api/v1/billing/withdrawals';
const CREDIT = 'POST /api/v1/billing/withdrawals/account-credit';
const REFUND = 'POST /api/v1/billing/withdrawals/refund';
const METHODS = 'GET /api/v1/billing/payment-methods';
const REAUTH = 'POST /api/v1/auth/reauthenticate';
const ME = 'GET /api/v1/auth/me';
const GROUP_DETAIL = `GET /api/v1/groups/${GROUP_ID}`;
const GROUP_SESSIONS = `GET /api/v1/groups/${GROUP_ID}/sessions`;
const GROUP_WITHDRAW = `POST /api/v1/groups/${GROUP_ID}/enrollments/${ACTIVE_ANA.id}/withdraw`;
const COHORT_DETAIL = `GET /api/v1/cohorts/${COHORT_ID}`;
const COHORT_WITHDRAW = `POST /api/v1/cohorts/${COHORT_ID}/enrollments/${COHORT_ANA.id}/withdraw`;
const BASE = `/api/v1/families/${FAMILY_ID}/billing-account`;
const TODAY_DATE = TODAY as LocalDate;

const user = (
  roles: SessionUserBody['roles'],
  permissions: SessionUserBody['permissions'],
): SessionUserBody => ({ ...TEST_USER, roles, permissions });

const BILLING = [
  'billing.view',
  'billing.manage',
  'finance.refund.approve',
] as const;
const OWNER = user(
  ['owner'],
  [
    'course.view',
    'schedule.view',
    'schedule.manage',
    'student.view',
    ...BILLING,
  ],
);
/** Administrator, not delegated: no finance.refund.approve. */
const ADMIN = user(
  ['administrator'],
  [
    'schedule.view',
    'schedule.manage',
    'student.view',
    'billing.view',
    'billing.manage',
  ],
);
const COORDINATOR = user(
  ['academic_coordinator'],
  ['course.view', 'schedule.view', 'schedule.manage', 'student.view'],
);
const FINANCE = user(
  ['finance_staff'],
  ['schedule.view', 'student.view', 'billing.view', 'billing.manage'],
);
const REFUNDER = user(['owner'], ['billing.view', 'finance.refund.approve']);
const VIEWER = user(['finance_staff'], ['billing.view']);

/** Billing off: no line, no automatic credit, every total 0.00. */
const EMPTY_GROUP_PREVIEW: WithdrawalPreview = {
  ...GROUP_PREVIEW,
  lines: [],
  automatic_credits: [],
  totals: {
    credit: '0.00',
    reduces_debt: '0.00',
    refundable_cash: '0.00',
    automatic_credits: '0.00',
    account_credit_after_credit: '0.00',
  },
  warnings: [],
};
const NO_FAMILY_GROUP_PREVIEW: WithdrawalPreview = {
  ...EMPTY_GROUP_PREVIEW,
  family: null,
  billing_account_id: null,
  blocking: [{ code: 'student_has_no_family', details: {} }],
};

const PACKAGES_PAGE: PackagePage = {
  items: [PACKAGE_ACTIVE, PACKAGE_EXPIRED, PACKAGE_CANCELLED],
  total: 3,
  limit: 25,
  offset: 0,
};

let app: RenderedApp | undefined;
let packageUrls: URL[] = [];

const main = () => app!.container.querySelector('main')!;
const dialogTitled = (title: string) =>
  [...document.querySelectorAll<HTMLElement>('[role="dialog"]')].find(
    (d) => d.querySelector('h2')?.textContent === title,
  ) ?? null;
const keyOf = (call: { headers: Headers }) =>
  call.headers.get('Idempotency-Key');
const buttonTexts = (root: ParentNode) =>
  [...root.querySelectorAll('button')].map((button) => button.textContent);
const rowOf = (id: string) =>
  main().querySelector<HTMLTableRowElement>(`[data-package-id="${id}"]`)!;
const total = (root: ParentNode, key: string) =>
  root.querySelector(`[data-total="${key}"] dd`)?.textContent ?? '';

function previewOf(previews: Record<string, WithdrawalPreview>): Responder {
  return (request) => {
    const url = new URL(request.url);
    const id =
      url.searchParams.get('package_id') ??
      url.searchParams.get('group_enrollment_id') ??
      url.searchParams.get('cohort_enrollment_id') ??
      '';
    const found = previews[id];
    return found
      ? jsonResponse(200, found)
      : problem(404, 'withdrawal_subject_not_found');
  };
}

function stub(
  as: SessionUserBody,
  routes: Record<string, Responder | Responder[]> = {},
): ApiStub {
  return stubApi({
    [ME]: meAs(as),
    [PACKAGES]: (request) => {
      packageUrls.push(new URL(request.url));
      return jsonResponse(200, PACKAGES_PAGE);
    },
    [PREVIEW]: previewOf({ [PACKAGE_ACTIVE.id]: PACKAGE_PREVIEW }),
    [METHODS]: () => jsonResponse(200, PAYMENT_METHODS),
    [REAUTH]: () =>
      jsonResponse(200, { reauthenticated_until: '2026-10-20T18:05:00Z' }),
    ...routes,
  });
}

beforeEach(() => {
  applyLanguage('en');
  packageUrls = [];
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

// ------------------------------------------------------------- rules

describe('the settlement rules (A1, A4, A5, A6)', () => {
  const credit = { credit: true, refund: true, refundDisabled: false };

  it('shows minutes as h:mm', () => {
    expect(minutesAsHours(0)).toBe('0:00');
    expect(minutesAsHours(90)).toBe('1:30');
    expect(minutesAsHours(450)).toBe('7:30');
  });

  it('estimates the totals with an overridden line', () => {
    const plain = estimateTotals(GROUP_PREVIEW, {});
    expect(plain).toMatchObject({
      credit: 111250,
      reducesDebt: 80000,
      refundableCash: 61250,
      accountCreditAfter: 91250,
      estimated: false,
    });
    // The paid fee credited in full: all $500.00 of it is freed.
    const full = estimateTotals(GROUP_PREVIEW, {
      'e1000000-0000-4000-8000-000000000001': '500',
    });
    expect(full).toMatchObject({
      credit: 130000,
      reducesDebt: 80000,
      refundableCash: 80000,
      accountCreditAfter: 110000,
      estimated: true,
    });
  });

  it('sends only the changed lines, the version and the refund terms', () => {
    const values: SettlementValues = {
      ...emptySettlement(credit, TODAY_DATE, '  Se muda  '),
      outcome: 'refund',
      method: 'cash',
      reference: ' ',
      overrides: {
        'e1000000-0000-4000-8000-000000000001': '312.50',
      },
    };
    expect(settlementBody(values, GROUP_PREVIEW)).toEqual({
      subject: { group_enrollment_id: ACTIVE_ANA.id },
      version: ACTIVE_ANA.version,
      reason: 'Se muda',
      fingerprint: GROUP_PREVIEW.fingerprint,
      credit_overrides: [],
      refund: {
        refunded_on: '2026-10-20',
        payment_method_code: 'cash',
        reference: null,
      },
    });
    expect(
      settlementBody(
        {
          ...values,
          outcome: 'account_credit',
          overrides: { [PURCHASE_CHARGE_ID]: '1000' },
        },
        PACKAGE_PREVIEW,
      ),
    ).toEqual({
      subject: { package_id: PACKAGE_ACTIVE.id },
      reason: 'Se muda',
      fingerprint: PACKAGE_PREVIEW.fingerprint,
      credit_overrides: [{ charge_id: PURCHASE_CHARGE_ID, amount: '1000.00' }],
    });
  });

  it('knows when there is nothing to settle (QA D2)', () => {
    const noFamily = {
      ...GROUP_PREVIEW,
      family: null,
      blocking: [{ code: 'student_has_no_family' as const, details: {} }],
    };
    expect(nothingToSettle(noFamily)).toBe(true);
    expect(nothingToSettle(EMPTY_GROUP_PREVIEW)).toBe(true);
    expect(nothingToSettle(GROUP_PREVIEW)).toBe(false);
    for (const code of [
      'withdrawal_exists',
      'withdrawal_closeout_pending',
      'package_not_withdrawable',
      'withdrawal_spans_accounts',
    ] as const) {
      expect(nothingToSettle(blockedPreview(code))).toBe(false);
    }
    // A real blocking reason next to "no family" still needs staff action.
    expect(
      nothingToSettle({
        ...noFamily,
        blocking: [
          ...noFamily.blocking,
          { code: 'withdrawal_spans_accounts', details: {} },
        ],
      }),
    ).toBe(false);
  });

  it('drops the overrides of changed lines after a stale answer', () => {
    const values: SettlementValues = {
      ...emptySettlement(credit, TODAY_DATE, 'Motivo'),
      overrides: { [PURCHASE_CHARGE_ID]: '1000' },
    };
    const merged = mergeStale(PACKAGE_PREVIEW, PACKAGE_PREVIEW_FRESH, values);
    expect(merged.values.overrides).toEqual({});
    expect(merged.values.reason).toBe('Motivo');
    expect(merged.changed).toEqual(['credit', 'reduces_debt']);
    const same = mergeStale(PACKAGE_PREVIEW, PACKAGE_PREVIEW, values);
    expect(same.values.overrides).toEqual({ [PURCHASE_CHARGE_ID]: '1000' });
    expect(same.changed).toEqual([]);
  });
});

// ---------------------------------------------------------- packages

async function renderPackages() {
  app = await renderApp('/billing/packages');
  await settleUntil(() => main().querySelector('[data-package-id]'));
}

const packageDialog = () => dialogTitled('Withdraw unused units');

async function openWithdrawal() {
  await click(
    buttonNamed(
      rowOf(PACKAGE_ACTIVE.id),
      'Withdraw (unused units) Álgebra 10 sesiones of Ana López',
    ),
  );
  await settleUntil(() =>
    packageDialog()?.querySelector('[data-withdrawal-preview]'),
  );
  return packageDialog()!;
}

describe('withdraw a package (B3)', () => {
  it('shows the action on active rows for billing.manage or refund approvers', async () => {
    for (const as of [OWNER, FINANCE, REFUNDER]) {
      stub(as);
      await renderPackages();
      expect(buttonTexts(rowOf(PACKAGE_ACTIVE.id))).toContain(
        'Withdraw (unused units)',
      );
      for (const pkg of [PACKAGE_EXPIRED, PACKAGE_CANCELLED]) {
        expect(buttonTexts(rowOf(pkg.id))).not.toContain(
          'Withdraw (unused units)',
        );
      }
      expect(main().textContent).toContain(
        'Cancel = the purchase was entered by mistake; Withdraw = the family leaves.',
      );
      app!.unmount();
      app = undefined;
      vi.unstubAllGlobals();
    }
    stub(VIEWER);
    await renderPackages();
    expect(buttonTexts(rowOf(PACKAGE_ACTIVE.id))).not.toContain(
      'Withdraw (unused units)',
    );
  });

  it('filters by the Withdrawn status and shows its badge', async () => {
    stub(OWNER, {
      [PACKAGES]: (request) => {
        packageUrls.push(new URL(request.url));
        return jsonResponse(200, {
          ...PACKAGES_PAGE,
          items: [{ ...PACKAGE_EXPIRED, status: 'withdrawn' }],
        });
      },
    });
    await renderPackages();
    expect(rowOf(PACKAGE_EXPIRED.id).textContent).toContain('Withdrawn');
    const box = [...main().querySelectorAll('label')]
      .find((label) => label.textContent === 'Withdrawn')!
      .querySelector('input')!;
    await click(box);
    await settleUntil(() =>
      packageUrls.some((url) =>
        url.searchParams.getAll('status').includes('withdrawn'),
      ),
    );
  });

  it('shows the units, the line, the warning and the totals', async () => {
    stub(OWNER);
    await renderPackages();
    const dialog = await openWithdrawal();
    const summary = dialog.querySelector('[data-withdrawal-summary]')!;
    expect(summary.textContent).toContain('StudentAna López');
    expect(summary.textContent).toContain('FamilyFamilia López');
    expect(summary.textContent).toContain('PackageÁlgebra 10 sesiones');
    expect(summary.textContent).toContain('Granted10 sessions');
    expect(summary.textContent).toContain('Unused8 sessions');
    const line = dialog.querySelector('[data-withdrawal-line]')!;
    expect(line.textContent).toContain('$1,800.00');
    expect(line.textContent).toContain('8 of 10 sessions unused');
    expect(line.textContent).toContain('Unused share (pro rata)');
    expect(line.textContent).toContain('$1,440.00');
    expect(total(dialog, 'credit')).toBe('$1,440.00');
    expect(total(dialog, 'refundable_cash')).toBe('$0.00');
    const warning = dialog.querySelector(
      '[data-warning="future_covered_sessions"]',
    )!;
    expect(warning.textContent).toContain(
      '1 future session this package covers will be charged normally unless it is cancelled.',
    );
    expect(warning.querySelector('a')?.getAttribute('href')).toBe(
      `/schedule/sessions/${FUTURE_SESSION_ID}`,
    );
  });

  it('shows hours for an hours package', async () => {
    stub(OWNER, {
      [PREVIEW]: previewOf({ [PACKAGE_ACTIVE.id]: PACKAGE_PREVIEW_HOURS }),
    });
    await renderPackages();
    const dialog = await openWithdrawal();
    expect(
      dialog.querySelector('[data-withdrawal-summary]')!.textContent,
    ).toContain('Unused12 h 30 min');
    expect(
      dialog.querySelector('[data-withdrawal-line]')!.textContent,
    ).toContain('12 h 30 min of 20 h unused');
  });

  it('confirms account credit with the password and the same key, then announces it', async () => {
    const api = stub(OWNER, {
      [CREDIT]: [
        () => problem(403, 'reauth_required'),
        () => jsonResponse(201, PACKAGE_CONFIRMED),
      ],
    });
    await renderPackages();
    const dialog = await openWithdrawal();
    await click(buttonNamed(dialog, 'Confirm withdrawal'));
    expect(dialog.textContent).toContain('Enter the reason.');
    expect(api.calls(CREDIT)).toHaveLength(0);

    setFieldValue(labelled(dialog, 'Reason'), ' La familia se muda ');
    const lists = packageUrls.length;
    await click(buttonNamed(dialog, 'Confirm withdrawal'));
    await settleUntil(() => dialogTitled('Confirm your password'));
    // Nothing changed before the server confirmed.
    expect(packageUrls.length).toBe(lists);
    const reauth = dialogTitled('Confirm your password')!;
    setFieldValue(labelled(reauth, 'Password'), 'mi-clave');
    await click(buttonNamed(reauth, 'Confirm'));
    await settleUntil(() => packageDialog() === null);

    const calls = api.calls(CREDIT);
    const body = {
      subject: { package_id: PACKAGE_ACTIVE.id },
      reason: 'La familia se muda',
      fingerprint: PACKAGE_PREVIEW.fingerprint,
      credit_overrides: [],
    };
    expect(calls.map((call) => call.body)).toEqual([body, body]);
    expect(keyOf(calls[0]!)).toMatch(/^[0-9a-f-]{36}$/);
    expect(keyOf(calls[1]!)).toBe(keyOf(calls[0]!));
    expect(main().querySelector('[role="status"]')?.textContent).toBe(
      'Ana López withdrawn from Álgebra 10 sesiones: $1,440.00 credited, $0.00 refunded. Account credit is now $0.00.',
    );
    await settleUntil(() => packageUrls.length > lists);
  });

  it('resends the same key after a network error', async () => {
    const api = stub(OWNER, {
      [CREDIT]: [
        () => Promise.reject(new TypeError('Failed to fetch')),
        () => jsonResponse(201, PACKAGE_CONFIRMED),
      ],
    });
    await renderPackages();
    const dialog = await openWithdrawal();
    setFieldValue(labelled(dialog, 'Reason'), 'Se muda');
    await click(buttonNamed(dialog, 'Confirm withdrawal'));
    await settleUntil(() => alerts(dialog).length > 0);
    await click(buttonNamed(dialog, 'Confirm withdrawal'));
    await settleUntil(() => packageDialog() === null);
    const calls = api.calls(CREDIT);
    expect(calls).toHaveLength(2);
    expect(keyOf(calls[1]!)).toBe(keyOf(calls[0]!));
  });

  it('shows the fresh preview after a stale answer and confirms with a new key', async () => {
    const api = stub(OWNER, {
      [CREDIT]: [
        () =>
          new Response(
            JSON.stringify({
              type: 'about:blank',
              title: 'Conflict',
              status: 409,
              code: 'withdrawal_preview_stale',
              request_id: 'req-stale',
              details: { preview: PACKAGE_PREVIEW_FRESH },
            }),
            {
              status: 409,
              headers: { 'Content-Type': 'application/problem+json' },
            },
          ),
        () => jsonResponse(201, PACKAGE_CONFIRMED),
      ],
    });
    await renderPackages();
    const dialog = await openWithdrawal();
    await click(buttonNamed(dialog, 'Adjust lines'));
    setFieldValue(
      labelled(dialog, 'Credit for Package purchase Oct 1, 2026'),
      '1000',
    );
    expect(dialog.querySelector('[data-estimate]')?.textContent).toBe(
      'Estimate with your adjustments; the server decides.',
    );
    expect(total(dialog, 'credit')).toBe('$1,000.00');
    setFieldValue(labelled(dialog, 'Reason'), 'Se muda');
    await click(buttonNamed(dialog, 'Confirm withdrawal'));
    await settleUntil(() =>
      dialog.textContent?.includes(
        'Something changed; review and confirm again.',
      ),
    );
    // The fresh preview, its changed totals, the reason kept, the
    // override of the changed line dropped.
    expect(
      dialog.querySelector('[data-withdrawal-summary]')!.textContent,
    ).toContain('Unused7 sessions');
    expect(total(dialog, 'credit')).toBe('$1,260.00(changed)');
    expect(
      dialog
        .querySelector('[data-total="credit"]')
        ?.getAttribute('data-changed'),
    ).toBe('true');
    expect(labelled<HTMLTextAreaElement>(dialog, 'Reason').value).toBe(
      'Se muda',
    );
    expect(
      labelled<HTMLInputElement>(
        dialog,
        'Credit for Package purchase Oct 1, 2026',
      ).value,
    ).toBe('1260.00');
    expect(api.calls(PREVIEW)).toHaveLength(1);

    await click(buttonNamed(dialog, 'Confirm withdrawal'));
    await settleUntil(() => packageDialog() === null);
    const calls = api.calls(CREDIT);
    expect(calls).toHaveLength(2);
    expect((calls[1]!.body as { fingerprint: string }).fingerprint).toBe(
      PACKAGE_PREVIEW_FRESH.fingerprint,
    );
    expect(
      (calls[0]!.body as { credit_overrides: unknown[] }).credit_overrides,
    ).toEqual([{ charge_id: PURCHASE_CHARGE_ID, amount: '1000.00' }]);
    expect(
      (calls[1]!.body as { credit_overrides: unknown[] }).credit_overrides,
    ).toEqual([]);
    expect(keyOf(calls[1]!)).not.toBe(keyOf(calls[0]!));
  });

  it('blocks a credit above the net', async () => {
    const api = stub(OWNER);
    await renderPackages();
    const dialog = await openWithdrawal();
    await click(buttonNamed(dialog, 'Adjust lines'));
    setFieldValue(
      labelled(dialog, 'Credit for Package purchase Oct 1, 2026'),
      '1800.01',
    );
    setFieldValue(labelled(dialog, 'Reason'), 'Se muda');
    await click(buttonNamed(dialog, 'Confirm withdrawal'));
    expect(dialog.textContent).toContain(
      "The credit can't be more than the net ($1,800.00).",
    );
    expect(api.calls(CREDIT)).toHaveLength(0);
  });

  it('disables Refund when nothing paid is freed', async () => {
    stub(OWNER);
    await renderPackages();
    const dialog = await openWithdrawal();
    const refund = dialog.querySelector<HTMLInputElement>(
      'input[type="radio"][value="refund"]',
    )!;
    expect(refund.disabled).toBe(true);
    expect(dialog.textContent).toContain(
      "Nothing paid is freed, so there's nothing to refund.",
    );
    expect(
      dialog.querySelector<HTMLInputElement>(
        'input[type="radio"][value="account_credit"]',
      )!.checked,
    ).toBe(true);
  });

  it('refunds the freed cash with its date, method and reference', async () => {
    const api = stub(OWNER, {
      [PREVIEW]: previewOf({ [PACKAGE_ACTIVE.id]: PACKAGE_PREVIEW_PAID }),
      [REFUND]: () => jsonResponse(201, PACKAGE_CONFIRMED),
    });
    await renderPackages();
    const dialog = await openWithdrawal();
    await click(
      dialog.querySelector<HTMLInputElement>(
        'input[type="radio"][value="refund"]',
      )!,
    );
    expect(labelled<HTMLInputElement>(dialog, 'Refund date').value).toBe(
      '2026-10-20',
    );
    setFieldValue(labelled(dialog, 'Reason'), 'Se muda');
    await click(buttonNamed(dialog, 'Confirm withdrawal'));
    expect(dialog.textContent).toContain('Choose the method.');
    await settle();
    setFieldValue(labelled<HTMLSelectElement>(dialog, 'Method'), 'transfer');
    setFieldValue(labelled(dialog, 'Reference (optional)'), 'SPEI 123');
    await click(buttonNamed(dialog, 'Confirm withdrawal'));
    await settleUntil(() => api.calls(REFUND).length === 1);
    expect(api.calls(REFUND)[0]!.body).toEqual({
      subject: { package_id: PACKAGE_ACTIVE.id },
      reason: 'Se muda',
      fingerprint: PACKAGE_PREVIEW_PAID.fingerprint,
      credit_overrides: [],
      refund: {
        refunded_on: '2026-10-20',
        payment_method_code: 'transfer',
        reference: 'SPEI 123',
      },
    });
    expect(api.calls(CREDIT)).toHaveLength(0);
  });

  it('offers only Refund to a refund approver without billing.manage', async () => {
    stub(REFUNDER, {
      [PREVIEW]: previewOf({ [PACKAGE_ACTIVE.id]: PACKAGE_PREVIEW_PAID }),
    });
    await renderPackages();
    const dialog = await openWithdrawal();
    expect(
      dialog.querySelector('input[type="radio"][value="account_credit"]'),
    ).toBeNull();
    expect(
      dialog.querySelector<HTMLInputElement>(
        'input[type="radio"][value="refund"]',
      )!.checked,
    ).toBe(true);
  });

  it.each([
    ['withdrawal_exists', 'This was already settled.', 'See the withdrawal'],
    [
      'withdrawal_closeout_pending',
      'A session this package covered is waiting for its closeout approval.',
      'Closeout of session 1',
    ],
    [
      'package_not_withdrawable',
      "This package can't be withdrawn: it has expired.",
      null,
    ],
    [
      'student_has_no_family',
      "The student isn't in a family, so there's no account to credit.",
      "Open the student's family section",
    ],
    [
      'withdrawal_spans_accounts',
      'The charges are on two family accounts: use the corrections on the account page.',
      null,
    ],
  ] as const)('explains %s without a confirm', async (code, text, link) => {
    stub(OWNER, {
      [PREVIEW]: previewOf({ [PACKAGE_ACTIVE.id]: blockedPreview(code) }),
    });
    await renderPackages();
    const dialog = await openWithdrawal();
    const blocking = dialog.querySelector(`[data-blocking="${code}"]`)!;
    expect(blocking.textContent).toContain(text);
    expect(buttonTexts(dialog)).not.toContain('Confirm withdrawal');
    const anchor = blocking.querySelector('a');
    if (link === null) {
      expect(anchor).toBeNull();
    } else {
      expect(anchor?.textContent).toBe(link);
    }
    if (code === 'withdrawal_exists') {
      expect(anchor?.getAttribute('href')).toBe(
        `/billing/accounts/${PACKAGE_PREVIEW.family!.id}?withdrawal=${WITHDRAWAL_ID}`,
      );
    }
    if (code === 'withdrawal_closeout_pending') {
      expect(anchor?.getAttribute('href')).toBe(
        `/schedule/sessions/${CLOSEOUT_SESSION_ID}`,
      );
    }
  });

  it.each([
    [
      'package_not_withdrawable',
      'This package is cancelled, withdrawn, expired or used up. The settlement was reloaded.',
    ],
    [
      'withdrawal_exists',
      "This withdrawal was already settled. Open it from the family's account page.",
    ],
    [
      'withdrawal_closeout_pending',
      'A session this package covered is waiting for its closeout approval. Approve it first. The settlement was reloaded.',
    ],
    [
      'withdrawal_nothing_to_refund',
      "Nothing paid is freed by this withdrawal, so there's nothing to refund. Settle it as account credit.",
    ],
    [
      'withdrawal_spans_accounts',
      'The charges are on two family accounts. Use the corrections on the account page instead.',
    ],
    ['student_has_no_family', null],
    ['charge_net_negative', null],
  ] as const)(
    '%s is an alert and reads the preview again',
    async (code, text) => {
      const api = stub(OWNER, { [CREDIT]: () => problemWith(409, code) });
      await renderPackages();
      const dialog = await openWithdrawal();
      setFieldValue(labelled(dialog, 'Reason'), 'Se muda');
      const previews = api.calls(PREVIEW).length;
      await click(buttonNamed(dialog, 'Confirm withdrawal'));
      await settleUntil(() => alerts(dialog).length > 0);
      if (text) expect(alerts(dialog).join()).toContain(text);
      expect(alerts(dialog).join()).not.toContain('Server detail');
      await settleUntil(() => api.calls(PREVIEW).length > previews);
    },
  );

  it.each([
    [
      'credit_override_unknown_charge',
      "An adjusted line isn't part of this withdrawal any more. The settlement was reloaded.",
    ],
    [
      'withdrawal_subject_invalid',
      'Choose exactly one enrollment or package to withdraw.',
    ],
    ['payment_method_unavailable', null],
    ['refunded_on_in_future', "The refund date can't be after today."],
  ] as const)('%s (422) is an alert', async (code, text) => {
    stub(OWNER, { [CREDIT]: () => problemWith(422, code) });
    await renderPackages();
    const dialog = await openWithdrawal();
    setFieldValue(labelled(dialog, 'Reason'), 'Se muda');
    await click(buttonNamed(dialog, 'Confirm withdrawal'));
    await settleUntil(() => alerts(dialog).length > 0);
    if (text) expect(alerts(dialog).join()).toContain(text);
  });

  it('puts a 422 on the reason field', async () => {
    stub(OWNER, {
      [CREDIT]: () =>
        problemWith(422, 'validation_error', {
          errors: [{ path: ['body', 'reason'], code: 'string_too_long' }],
        }),
    });
    await renderPackages();
    const dialog = await openWithdrawal();
    setFieldValue(labelled(dialog, 'Reason'), 'Se muda');
    await click(buttonNamed(dialog, 'Confirm withdrawal'));
    await settleUntil(
      () => labelled(dialog, 'Reason').getAttribute('aria-invalid') === 'true',
    );
  });

  it('keeps the form when the password dialog is cancelled', async () => {
    stub(OWNER, { [CREDIT]: () => problem(403, 'reauth_required') });
    await renderPackages();
    const dialog = await openWithdrawal();
    setFieldValue(labelled(dialog, 'Reason'), 'Se muda');
    await click(buttonNamed(dialog, 'Confirm withdrawal'));
    await settleUntil(() => dialogTitled('Confirm your password'));
    await click(buttonNamed(dialogTitled('Confirm your password')!, 'Cancel'));
    await settleUntil(() => dialogTitled('Confirm your password') === null);
    expect(alerts(packageDialog()!)).toEqual([]);
    expect(
      labelled<HTMLTextAreaElement>(packageDialog()!, 'Reason').value,
    ).toBe('Se muda');
  });
});

// ------------------------------------------------------------ rosters

function withdrawalsOf(settled: readonly string[]): Responder {
  return (request) => {
    const url = new URL(request.url);
    const id =
      url.searchParams.get('group_enrollment_id') ??
      url.searchParams.get('cohort_enrollment_id');
    const items = id && settled.includes(id) ? [WITHDRAWAL_REFUND_ITEM] : [];
    return jsonResponse(200, {
      items,
      total: items.length,
      limit: 1,
      offset: 0,
    });
  };
}

function stubGroup(
  as: SessionUserBody,
  routes: Record<string, Responder | Responder[]> = {},
): ApiStub {
  return stub(as, {
    [GROUP_DETAIL]: () => jsonResponse(200, GROUP),
    [GROUP_SESSIONS]: () => jsonResponse(200, { items: SESSIONS }),
    [LIST]: withdrawalsOf([WITHDRAWN_CARLA_EARLIER.id]),
    [PREVIEW]: previewOf({
      [ACTIVE_ANA.id]: GROUP_PREVIEW,
      [WITHDRAWN_CARLA.id]: GROUP_SETTLE_PREVIEW,
    }),
    ...routes,
  });
}

async function renderGroup() {
  app = await renderApp(`/groups/${GROUP_ID}`);
  await settleUntil(() => main().querySelector('main h1, h1'));
  await settle();
}

const groupDialog = () => dialogTitled('Withdraw student');

describe('withdraw from a group roster (B1)', () => {
  it('asks for the reason, then shows the settlement and confirms both in one call', async () => {
    const api = stubGroup(OWNER, {
      [CREDIT]: () => jsonResponse(201, GROUP_CONFIRMED),
    });
    await renderGroup();
    await click(buttonNamed(main(), 'Withdraw Ana López'));
    await settleUntil(() => groupDialog());
    let dialog = groupDialog()!;
    expect(dialog.textContent).toContain(
      'Withdraw Ana López from Álgebra sábado?',
    );
    expect(dialog.textContent).toContain('Step 1 of 2');
    setFieldValue(labelled(dialog, 'Reason'), 'Cambio de horario');
    await click(buttonNamed(dialog, 'Next: billing settlement'));
    await settleUntil(() =>
      groupDialog()?.querySelector('[data-withdrawal-preview]'),
    );
    dialog = groupDialog()!;
    expect(dialog.textContent).toContain('Step 2 of 2');
    const summary = dialog.querySelector('[data-withdrawal-summary]')!;
    expect(summary.textContent).toContain('Consumed3 sessions (4:30 h)');
    expect(summary.textContent).toContain('Unused5 sessions (7:30 h)');
    expect(summary.textContent).toContain('GroupÁlgebra sábado');
    const lines = dialog.querySelectorAll('[data-withdrawal-line]');
    expect(lines).toHaveLength(2);
    expect(lines[0]!.textContent).toContain('5 of 8 sessions unused');
    expect(lines[1]!.textContent).toContain('Posted now');
    expect(lines[1]!.textContent).toContain(
      'Nothing delivered yet: full credit',
    );
    expect(
      dialog.querySelector('[data-automatic-credits] summary')?.textContent,
    ).toBe('2 automatic session credits: $300.00');
    expect(dialog.textContent).toContain(
      'Credited automatically, not part of this settlement.',
    );
    expect(total(dialog, 'reduces_debt')).toBe('$800.00');
    expect(total(dialog, 'refundable_cash')).toBe('$612.50');
    expect(labelled<HTMLTextAreaElement>(dialog, 'Reason').value).toBe(
      'Cambio de horario',
    );
    await click(buttonNamed(dialog, 'Confirm withdrawal'));
    await settleUntil(() => groupDialog() === null);
    expect(api.calls(CREDIT).map((call) => call.body)).toEqual([
      {
        subject: { group_enrollment_id: ACTIVE_ANA.id },
        version: ACTIVE_ANA.version,
        reason: 'Cambio de horario',
        fingerprint: GROUP_PREVIEW.fingerprint,
        credit_overrides: [],
      },
    ]);
    expect(api.calls(GROUP_WITHDRAW)).toHaveLength(0);
    expect(main().textContent).toContain(
      'Ana López withdrawn from Álgebra sábado: $1,112.50 credited, $0.00 refunded. Account credit is now $912.50.',
    );
  });

  it('offers no Refund to an Administrator without the delegation', async () => {
    stubGroup(ADMIN);
    await renderGroup();
    await click(buttonNamed(main(), 'Withdraw Ana López'));
    await settleUntil(() => groupDialog());
    await click(buttonNamed(groupDialog()!, 'Next: billing settlement'));
    await settleUntil(() =>
      groupDialog()?.querySelector('[data-withdrawal-preview]'),
    );
    const dialog = groupDialog()!;
    expect(
      dialog.querySelector('input[type="radio"][value="refund"]'),
    ).toBeNull();
    expect(
      dialog.querySelector<HTMLInputElement>(
        'input[type="radio"][value="account_credit"]',
      )!.checked,
    ).toBe(true);
  });

  it('keeps the plain withdrawal for a Coordinator, with no billing request', async () => {
    const api = stubGroup(COORDINATOR, {
      [GROUP_WITHDRAW]: () =>
        jsonResponse(200, {
          ...ACTIVE_ANA,
          withdrawn_at: '2031-01-01T00:00:00Z',
          withdrawn_by: ACTIVE_ANA.enrolled_by,
          version: 2,
        }),
    });
    await renderGroup();
    expect(buttonTexts(main())).not.toContain('Settle billing');
    await click(buttonNamed(main(), 'Withdraw Ana López'));
    const dialog = dialogTitled('Withdraw student')!;
    expect(dialog.textContent).not.toContain('Step 1 of 2');
    await click(buttonNamed(dialog, 'Withdraw'));
    await settleUntil(() => api.calls(GROUP_WITHDRAW).length === 1);
    expect(api.calls(LIST)).toHaveLength(0);
    expect(api.calls(PREVIEW)).toHaveLength(0);
  });

  it('gives Finance Staff no withdrawal or settlement on the roster', async () => {
    const api = stubGroup(FINANCE);
    await renderGroup();
    expect(buttonTexts(main())).not.toContain('Withdraw');
    expect(buttonTexts(main())).not.toContain('Settle billing');
    expect(api.calls(LIST)).toHaveLength(0);
  });

  it('offers "Settle billing" only on unsettled withdrawn rows, one request per row', async () => {
    const api = stubGroup(OWNER, {
      [CREDIT]: () => jsonResponse(201, GROUP_CONFIRMED),
    });
    await renderGroup();
    await settleUntil(() => api.calls(LIST).length === 2);
    await settle();
    const settles = [...main().querySelectorAll('button')].filter(
      (button) => button.textContent === 'Settle billing',
    );
    expect(settles.map((button) => button.getAttribute('aria-label'))).toEqual([
      `Settle billing of ${WITHDRAWN_CARLA.student.display_name}`,
    ]);
    // Re-renders send nothing more.
    await click(buttonNamed(main(), 'Withdraw Ana López'));
    await settleUntil(() => groupDialog());
    await click(buttonNamed(groupDialog()!, 'Keep enrolled'));
    await settle();
    expect(api.calls(LIST)).toHaveLength(2);

    await click(settles[0]!);
    await settleUntil(() =>
      dialogTitled('Settle billing')?.querySelector(
        '[data-withdrawal-preview]',
      ),
    );
    const dialog = dialogTitled('Settle billing')!;
    expect(dialog.textContent).not.toContain('Step 1 of 2');
    setFieldValue(labelled(dialog, 'Reason'), 'Liquidación pendiente');
    await click(buttonNamed(dialog, 'Confirm withdrawal'));
    await settleUntil(() => dialogTitled('Settle billing') === null);
    expect(api.calls(CREDIT)[0]!.body).toEqual({
      subject: { group_enrollment_id: WITHDRAWN_CARLA.id },
      reason: 'Liquidación pendiente',
      fingerprint: GROUP_SETTLE_PREVIEW.fingerprint,
      credit_overrides: [],
    });
    expect(main().textContent).toContain(
      'Billing of Carla Méndez in Álgebra sábado settled',
    );
  });
});

describe('withdraw without settlement when there is nothing to settle (QA D2)', () => {
  const withdrawn = {
    ...ACTIVE_ANA,
    withdrawn_at: '2031-01-01T00:00:00Z',
    withdrawn_by: ACTIVE_ANA.enrolled_by,
    version: 2,
  };

  async function toStepTwo(reason: string) {
    await click(buttonNamed(main(), 'Withdraw Ana López'));
    await settleUntil(() => groupDialog());
    setFieldValue(labelled(groupDialog()!, 'Reason'), reason);
    await click(buttonNamed(groupDialog()!, 'Next: billing settlement'));
  }

  it.each([
    [
      'a student without a family',
      NO_FAMILY_GROUP_PREVIEW,
      "The student isn't in a family, so there is no billing to settle.",
    ],
    [
      'an empty settlement (billing off)',
      EMPTY_GROUP_PREVIEW,
      'There is nothing to bill or credit for this enrollment.',
    ],
  ] as const)(
    'withdraws %s through the M4 route, without a password',
    async (_name, preview, text) => {
      const api = stubGroup(OWNER, {
        [PREVIEW]: previewOf({ [ACTIVE_ANA.id]: preview }),
        [GROUP_WITHDRAW]: () => jsonResponse(200, withdrawn),
      });
      await renderGroup();
      await toStepTwo(' Se mudó ');
      await settleUntil(() =>
        groupDialog()?.querySelector('[data-withdrawal-plain]'),
      );
      const dialog = groupDialog()!;
      expect(dialog.textContent).toContain(text);
      expect(buttonTexts(dialog)).not.toContain('Confirm withdrawal');
      await click(buttonNamed(dialog, 'Withdraw without settlement'));
      await settleUntil(() => groupDialog() === null);
      expect(api.calls(GROUP_WITHDRAW).map((call) => call.body)).toEqual([
        { version: ACTIVE_ANA.version, reason: 'Se mudó' },
      ]);
      expect(api.calls(CREDIT)).toHaveLength(0);
      expect(api.calls(REAUTH)).toHaveLength(0);
      expect(main().textContent).toContain('Ana López withdrawn.');
    },
  );

  it('keeps the dialog with the error when the plain withdrawal fails', async () => {
    stubGroup(OWNER, {
      [PREVIEW]: previewOf({ [ACTIVE_ANA.id]: NO_FAMILY_GROUP_PREVIEW }),
      [GROUP_WITHDRAW]: () => problem(409, 'enrollment_not_active'),
    });
    await renderGroup();
    await toStepTwo('');
    await settleUntil(() =>
      groupDialog()?.querySelector('[data-withdrawal-plain]'),
    );
    await click(buttonNamed(groupDialog()!, 'Withdraw without settlement'));
    await settleUntil(() => alerts(groupDialog()!).length > 0);
  });

  it('offers no plain withdrawal when a real blocking reason needs staff action', async () => {
    stubGroup(OWNER, {
      [PREVIEW]: previewOf({
        [ACTIVE_ANA.id]: {
          ...GROUP_PREVIEW,
          blocking: [{ code: 'withdrawal_spans_accounts', details: {} }],
        },
      }),
    });
    await renderGroup();
    await toStepTwo('');
    await settleUntil(() =>
      groupDialog()?.querySelector(
        '[data-blocking="withdrawal_spans_accounts"]',
      ),
    );
    expect(buttonTexts(groupDialog()!)).not.toContain(
      'Withdraw without settlement',
    );
  });

  it('withdraws a cohort student without a family the M4 way', async () => {
    const api = stub(OWNER, {
      [COHORT_DETAIL]: () => jsonResponse(200, ROSTER_COHORT),
      [`GET /api/v1/cohorts/${COHORT_ID}/sessions`]: () =>
        jsonResponse(200, { items: [] }),
      [LIST]: withdrawalsOf([]),
      [PREVIEW]: previewOf({
        [COHORT_ANA.id]: {
          ...NO_FAMILY_GROUP_PREVIEW,
          subject: { kind: 'cohort_enrollment', id: COHORT_ANA.id },
        },
      }),
      [COHORT_WITHDRAW]: () =>
        jsonResponse(200, {
          ...COHORT_ANA,
          withdrawn_at: '2031-01-01T00:00:00Z',
          withdrawn_by: COHORT_ANA.enrolled_by,
          version: 2,
        }),
    });
    app = await renderApp(`/courses/cohorts/${COHORT_ID}`);
    await settleUntil(() => main().querySelector('h1'));
    await settle();
    await click(buttonNamed(main(), 'Withdraw Ana López'));
    await settleUntil(() => dialogTitled('Withdraw student'));
    await click(
      buttonNamed(
        dialogTitled('Withdraw student')!,
        'Next: billing settlement',
      ),
    );
    await settleUntil(() =>
      dialogTitled('Withdraw student')?.querySelector(
        '[data-withdrawal-plain]',
      ),
    );
    await click(
      buttonNamed(
        dialogTitled('Withdraw student')!,
        'Withdraw without settlement',
      ),
    );
    await settleUntil(() => dialogTitled('Withdraw student') === null);
    expect(api.calls(COHORT_WITHDRAW).map((call) => call.body)).toEqual([
      { version: COHORT_ANA.version },
    ]);
    expect(api.calls(CREDIT)).toHaveLength(0);
    expect(main().textContent).toContain('Withdrew Ana López.');
  });
});

describe('withdraw from a cohort roster (B2)', () => {
  function stubCohort(as: SessionUserBody) {
    return stub(as, {
      [COHORT_DETAIL]: () => jsonResponse(200, ROSTER_COHORT),
      [`GET /api/v1/cohorts/${COHORT_ID}/sessions`]: () =>
        jsonResponse(200, { items: [] }),
      [LIST]: withdrawalsOf([]),
      [PREVIEW]: previewOf({ [COHORT_ANA.id]: COHORT_PREVIEW }),
      [CREDIT]: () => jsonResponse(201, GROUP_CONFIRMED),
    });
  }

  it('is two steps, and withdrawn rows get "Settle billing" when shown', async () => {
    const api = stubCohort(OWNER);
    app = await renderApp(`/courses/cohorts/${COHORT_ID}`);
    await settleUntil(() => main().querySelector('h1'));
    await settle();
    // The withdrawn rows are hidden: nothing is looked up.
    expect(api.calls(LIST)).toHaveLength(0);
    await click(buttonNamed(main(), 'Withdraw Ana López'));
    const title = 'Withdraw student';
    await settleUntil(() => dialogTitled(title));
    await click(buttonNamed(dialogTitled(title)!, 'Next: billing settlement'));
    await settleUntil(() =>
      dialogTitled(title)?.querySelector('[data-withdrawal-preview]'),
    );
    const dialog = dialogTitled(title)!;
    expect(
      dialog.querySelector('[data-withdrawal-summary]')!.textContent,
    ).toContain('CourseÁlgebra enero');
    setFieldValue(labelled(dialog, 'Reason'), 'Cambio de escuela');
    await click(buttonNamed(dialog, 'Confirm withdrawal'));
    await settleUntil(() => dialogTitled(title) === null);
    expect(api.calls(CREDIT)[0]!.body).toMatchObject({
      subject: { cohort_enrollment_id: COHORT_ANA.id },
      version: COHORT_ANA.version,
    });

    app.unmount();
    app = await renderApp(`/courses/cohorts/${COHORT_ID}?withdrawn=1`);
    await settleUntil(() => buttonTexts(main()).includes('Settle billing'));
    expect(
      buttonNamed(
        main(),
        `Settle billing of ${COHORT_CARLA.student.display_name}`,
      ),
    ).toBeTruthy();
  });

  it('keeps the plain withdrawal for a Coordinator', async () => {
    const api = stubCohort(COORDINATOR);
    app = await renderApp(`/courses/cohorts/${COHORT_ID}?withdrawn=1`);
    await settleUntil(() => main().querySelector('h1'));
    await settle();
    expect(buttonTexts(main())).not.toContain('Settle billing');
    await click(buttonNamed(main(), 'Withdraw Ana López'));
    await settle();
    expect(document.body.textContent).not.toContain('Step 1 of 2');
    expect(api.calls(LIST)).toHaveLength(0);
  });
});

// ------------------------------------------------------- account page

describe('the account page Withdrawals section (B4)', () => {
  function stubAccount(as: SessionUserBody = OWNER) {
    return stub(as, {
      [`GET ${BASE}`]: () => jsonResponse(200, SUMMARY),
      [`GET ${BASE}/charges`]: () => jsonResponse(200, CHARGES_PAGE),
      [`GET ${BASE}/charges/${PURCHASE_CHARGE_ID}`]: () =>
        jsonResponse(200, { ...CHARGE_DETAIL, id: PURCHASE_CHARGE_ID }),
      [`GET ${BASE}/ledger`]: () => jsonResponse(200, EMPTY_LEDGER_PAGE),
      [`GET ${BASE}/payments`]: () => jsonResponse(200, PAYMENTS_PAGE),
      [LIST]: () => jsonResponse(200, WITHDRAWALS_PAGE),
      [`GET /api/v1/billing/withdrawals/${WITHDRAWAL_ID}`]: () =>
        jsonResponse(200, WITHDRAWAL_DETAIL),
      [`GET /api/v1/billing/withdrawals/${WITHDRAWAL_REFUND_ITEM.id}`]: () =>
        jsonResponse(200, WITHDRAWAL_REFUND_DETAIL),
    });
  }

  const section = () =>
    [...main().querySelectorAll('section')].find(
      (s) => s.querySelector('h2')?.textContent === 'Withdrawals',
    )!;

  it('lists the family withdrawals and opens one with its snapshot', async () => {
    const api = stubAccount();
    app = await renderApp(`/billing/accounts/${FAMILY_ID}`);
    await settleUntil(() => section()?.querySelector('[data-withdrawal-id]'));
    const listed = api.calls(LIST);
    expect(listed).toHaveLength(1);
    const row = section().querySelector(
      `[data-withdrawal-id="${WITHDRAWAL_ID}"]`,
    )!;
    expect(
      [...row.querySelectorAll('td')]
        .slice(0, 6)
        .map((cell) => cell.textContent),
    ).toEqual([
      'Oct 20, 2026',
      'Ana López',
      'Package',
      'Account credit',
      '$1,440.00',
      '$0.00',
    ]);
    await click(
      buttonNamed(
        row,
        'Details of the withdrawal of Ana López on Oct 20, 2026',
      ),
    );
    await settleUntil(() =>
      dialogTitled('Withdrawal')?.querySelector('[data-withdrawal-detail]'),
    );
    const drawer = dialogTitled('Withdrawal')!;
    expect(drawer.querySelector('[data-withdrawal-reason]')?.textContent).toBe(
      WITHDRAWAL_ITEM.reason,
    );
    expect(drawer.textContent).toContain('Confirmedby you on Oct 20, 2026');
    expect(
      drawer.querySelector('[data-withdrawal-snapshot] [data-withdrawal-line]')
        ?.textContent,
    ).toContain('8 of 10 sessions unused');
    expect(buttonTexts(drawer)).not.toContain('Confirm withdrawal');
    await click(buttonNamed(drawer, 'View charge: Package purchase'));
    await settleUntil(() => dialogTitled('Charge'));
    expect(dialogTitled('Withdrawal')).toBeNull();
  });

  it('shows a refunded withdrawal by someone else and opens ?withdrawal=', async () => {
    stubAccount();
    app = await renderApp(
      `/billing/accounts/${FAMILY_ID}?withdrawal=${WITHDRAWAL_REFUND_ITEM.id}`,
    );
    await settleUntil(() =>
      dialogTitled('Withdrawal')?.querySelector('[data-withdrawal-detail]'),
    );
    const drawer = dialogTitled('Withdrawal')!;
    expect(drawer.textContent).toContain(
      'Confirmedby another staff member on Oct 20, 2026',
    );
    expect(drawer.querySelector('[data-withdrawal-refunds]')?.textContent).toBe(
      '$612.50 refunded in 1 refund; see it in the payment details.',
    );
    expect(drawer.querySelectorAll('[data-applied-charge]')).toHaveLength(1);
    expect(drawer.textContent).toContain('2 automatic session credits.');
  });
});
