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
import type { PackagePage } from './api';
import {
  CANCELLED,
  FAMILY_LOPEZ,
  PACKAGE_ACTIVE,
  PACKAGE_CANCELLED,
  PACKAGE_EXHAUSTED,
  PACKAGE_EXPIRED,
  PURCHASE_CHARGE_DETAIL,
} from './fixtures';
import { proposedCreditCents } from './rules';

// #386 F1 (C1): "Cancel" on the sold list (billing.manage and an active
// package only), the "Cancelled" status filter and badge, the dialog's
// estimate from the purchase charge's net, the body (reason, optional
// credit), the password dialog with the same key, the credit cap, the
// 409s with the list reload, and the polite status afterwards.

const LIST = 'GET /api/v1/billing/packages';
const CANCEL = `POST /api/v1/billing/packages/${PACKAGE_ACTIVE.id}/cancellation`;
const CHARGE = `GET /api/v1/families/${FAMILY_LOPEZ.id}/billing-account/charges/${PACKAGE_ACTIVE.purchase_charge_id}`;
const REAUTH = 'POST /api/v1/auth/reauthenticate';
const ME = 'GET /api/v1/auth/me';

const user = (
  permissions: SessionUserBody['permissions'],
): SessionUserBody => ({
  ...TEST_USER,
  roles: ['finance_staff'],
  permissions,
});
const MANAGER = user(['billing.view', 'billing.manage']);
const VIEWER = user(['billing.view']);

const PAGE: PackagePage = {
  items: [
    PACKAGE_ACTIVE,
    PACKAGE_EXHAUSTED,
    PACKAGE_EXPIRED,
    PACKAGE_CANCELLED,
  ],
  total: 4,
  limit: 25,
  offset: 0,
};

let listUrls: URL[] = [];

function stub(
  routes: Record<string, Responder | Responder[]> = {},
  as: SessionUserBody = MANAGER,
): ApiStub {
  return stubApi({
    [ME]: meAs(as),
    [LIST]: (request) => {
      listUrls.push(new URL(request.url));
      return jsonResponse(200, PAGE);
    },
    [CHARGE]: () => jsonResponse(200, PURCHASE_CHARGE_DETAIL),
    [REAUTH]: () =>
      jsonResponse(200, { reauthenticated_until: '2026-10-20T18:05:00Z' }),
    ...routes,
  });
}

let app: RenderedApp | undefined;

const main = () => app!.container.querySelector('main')!;
const rowOf = (id: string) =>
  main().querySelector<HTMLTableRowElement>(`[data-package-id="${id}"]`)!;
const buttonTexts = (root: ParentNode) =>
  [...root.querySelectorAll('button')].map((button) => button.textContent);
const dialogTitled = (title: string) =>
  [...document.querySelectorAll<HTMLElement>('[role="dialog"]')].find(
    (d) => d.querySelector('h2')?.textContent === title,
  ) ?? null;
const cancelDialog = () => dialogTitled('Cancel the package')!;
const keyOf = (call: { headers: Headers }) =>
  call.headers.get('Idempotency-Key');

async function renderList() {
  app = await renderApp('/billing/packages');
  await settleUntil(() => main().querySelector('[data-package-id]'));
}

async function openCancel() {
  await click(buttonNamed(rowOf(PACKAGE_ACTIVE.id), 'Cancel'));
  await settleUntil(() => cancelDialog()?.querySelector('form'));
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

describe('the proposed credit (C1)', () => {
  it('is net × remaining ÷ granted, rounded half away from zero', () => {
    expect(proposedCreditCents(180000, 8, 10)).toBe(144000);
    expect(proposedCreditCents(100, 1, 3)).toBe(33);
    expect(proposedCreditCents(100, 1, 8)).toBe(13);
    expect(proposedCreditCents(100, 0, 8)).toBe(0);
  });
});

describe('cancel a package (C1)', () => {
  it('shows "Cancel" only on active packages, for billing.manage', async () => {
    stub();
    await renderList();
    expect(buttonTexts(rowOf(PACKAGE_ACTIVE.id))).toContain('Cancel');
    expect(
      buttonNamed(rowOf(PACKAGE_ACTIVE.id), 'Cancel').getAttribute(
        'aria-label',
      ),
    ).toBe('Cancel Álgebra 10 sesiones of Ana López');
    for (const pkg of [PACKAGE_EXHAUSTED, PACKAGE_EXPIRED, PACKAGE_CANCELLED]) {
      expect(buttonTexts(rowOf(pkg.id))).not.toContain('Cancel');
    }
    expect(rowOf(PACKAGE_CANCELLED.id).textContent).toContain('Cancelled');
  });

  it('shows no "Cancel" without billing.manage', async () => {
    stub({}, VIEWER);
    await renderList();
    expect(buttonTexts(rowOf(PACKAGE_ACTIVE.id))).not.toContain('Cancel');
  });

  it('filters by the Cancelled status', async () => {
    stub();
    await renderList();
    const box = [...main().querySelectorAll('label')]
      .find((label) => label.textContent === 'Cancelled')!
      .querySelector('input')!;
    await click(box);
    await settleUntil(() =>
      listUrls.some((url) =>
        url.searchParams.getAll('status').includes('cancelled'),
      ),
    );
  });

  it('estimates the credit, confirms the password and retries with the same key', async () => {
    const api = stub({
      [CANCEL]: [
        () => problem(403, 'reauth_required'),
        () => jsonResponse(201, CANCELLED),
      ],
    });
    await renderList();
    await openCancel();
    const dialog = cancelDialog();
    expect(dialog.textContent).toContain(
      'Units left: 8 sessions of 10 sessions.',
    );
    expect(dialog.textContent).toContain('Purchase charge net: $1,800.00.');
    expect(dialog.textContent).toContain('Proposed credit: $1,440.00.');
    expect(dialog.textContent).toContain(
      "Sessions this package covered but hadn't used yet will be charged normally.",
    );

    await click(buttonNamed(dialog, 'Cancel package'));
    expect(dialog.textContent).toContain('Enter the reason.');
    expect(api.calls(CANCEL)).toHaveLength(0);

    setFieldValue(labelled(dialog, 'Reason'), ' Se mudó de ciudad ');
    const lists = listUrls.length;
    await click(buttonNamed(dialog, 'Cancel package'));
    await settleUntil(() => dialogTitled('Confirm your password'));
    const reauth = dialogTitled('Confirm your password')!;
    setFieldValue(labelled(reauth, 'Password'), 'mi-clave');
    await click(buttonNamed(reauth, 'Confirm'));
    await settleUntil(() => dialogTitled('Cancel the package') === null);

    const calls = api.calls(CANCEL);
    expect(calls.map((call) => call.body)).toEqual([
      { reason: 'Se mudó de ciudad' },
      { reason: 'Se mudó de ciudad' },
    ]);
    expect(keyOf(calls[1]!)).toBe(keyOf(calls[0]!));
    expect(main().querySelector('[role="status"]')?.textContent).toBe(
      'Álgebra 10 sesiones of Ana López cancelled: $1,440.00 credited. Account credit is now $0.00.',
    );
    await settleUntil(() => listUrls.length > lists);
  });

  it('sends a credit override and blocks one above the net', async () => {
    const api = stub({ [CANCEL]: () => jsonResponse(201, CANCELLED) });
    await renderList();
    await openCancel();
    const dialog = cancelDialog();
    setFieldValue(labelled(dialog, 'Credit (optional)'), '1800.01');
    setFieldValue(labelled(dialog, 'Reason'), 'Cambio de plan');
    await click(buttonNamed(dialog, 'Cancel package'));
    expect(dialog.textContent).toContain(
      "The credit can't be more than the purchase charge's net.",
    );
    expect(api.calls(CANCEL)).toHaveLength(0);

    setFieldValue(labelled(dialog, 'Credit (optional)'), '0');
    await click(buttonNamed(dialog, 'Cancel package'));
    await settleUntil(() => api.calls(CANCEL).length === 1);
    expect(api.calls(CANCEL)[0]!.body).toEqual({
      reason: 'Cambio de plan',
      credit_amount: '0',
    });
  });

  it.each([
    [
      'package_not_cancellable',
      'This package is already cancelled, expired or used up. The list was reloaded.',
    ],
    [
      'charge_net_negative',
      'This would take the charge below $0.00. The amounts were reloaded; check them and try again.',
    ],
  ])('%s is an alert and reloads the list', async (code, text) => {
    stub({ [CANCEL]: () => problemWith(409, code) });
    await renderList();
    await openCancel();
    const dialog = cancelDialog();
    setFieldValue(labelled(dialog, 'Reason'), 'Cambio de plan');
    const lists = listUrls.length;
    await click(buttonNamed(dialog, 'Cancel package'));
    await settleUntil(() => alerts(dialog).length > 0);
    expect(alerts(dialog).join()).toContain(text);
    await settleUntil(() => listUrls.length > lists);
  });

  it('keeps the form without an error when the password dialog is cancelled', async () => {
    stub({ [CANCEL]: () => problem(403, 'reauth_required') });
    await renderList();
    await openCancel();
    const dialog = cancelDialog();
    setFieldValue(labelled(dialog, 'Reason'), 'Cambio de plan');
    await click(buttonNamed(dialog, 'Cancel package'));
    await settleUntil(() => dialogTitled('Confirm your password'));
    await click(buttonNamed(dialogTitled('Confirm your password')!, 'Cancel'));
    await settleUntil(() => dialogTitled('Confirm your password') === null);
    expect(alerts(cancelDialog())).toEqual([]);
    expect(labelled<HTMLTextAreaElement>(cancelDialog(), 'Reason').value).toBe(
      'Cambio de plan',
    );
  });
});
