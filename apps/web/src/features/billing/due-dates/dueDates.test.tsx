import { act } from 'react';
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
  settle,
  settleUntil,
  type RenderedApp,
} from '@/test/render-app';
import {
  CHARGE_OVERDUE,
  CHARGE_PAID,
  CHARGE_PENDING,
  CHARGES_PAGE,
  FAMILY_ID,
  LEDGER_PAGE,
  PAYMENTS_PAGE,
  SUMMARY,
  SUMMARY_NO_ACCOUNT,
} from '../accounts/fixtures';
import { CHARGE_MOVED, DUE_DATE_CHANGES } from './fixtures';

// #382 F1 (C, D): the family's due-date offset on the account header (shown
// to billing.view holders, edited by billing.manage holders: bounds, the
// version sent, no optimistic update, 409/422/403), the "changed" marker
// and its tooltip, and "Change due date" (gating by permission and status,
// the history, the past-date warning, every D3 refusal, no optimistic
// update).

const BASE = `/api/v1/families/${FAMILY_ID}/billing-account`;
const SUMMARY_ROUTE = `GET ${BASE}`;
const PATCH_ROUTE = `PATCH ${BASE}`;
const CHARGES = `GET ${BASE}/charges`;
const LEDGER = `GET ${BASE}/ledger`;
const PAYMENTS = `GET ${BASE}/payments`;
const changesPath = (chargeId: string) =>
  `${BASE}/charges/${chargeId}/due-date-changes`;
const ME = 'GET /api/v1/auth/me';

const user = (
  roles: SessionUserBody['roles'],
  permissions: SessionUserBody['permissions'],
): SessionUserBody => ({ ...TEST_USER, roles, permissions });

const FINANCE = user(
  ['finance_staff'],
  [
    'student.view',
    'guardian.view',
    'billing.view',
    'billing.manage',
    'payment.record',
  ],
);
const VIEWER = user(['finance_staff'], ['guardian.view', 'billing.view']);

function stub(
  routes: Record<string, Responder | Responder[]> = {},
  { as = FINANCE }: { as?: SessionUserBody } = {},
): ApiStub {
  return stubApi({
    [ME]: meAs(as),
    [SUMMARY_ROUTE]: () => jsonResponse(200, SUMMARY),
    [CHARGES]: () => jsonResponse(200, CHARGES_PAGE),
    [LEDGER]: () => jsonResponse(200, LEDGER_PAGE),
    [PAYMENTS]: () => jsonResponse(200, PAYMENTS_PAGE),
    [`GET ${changesPath(CHARGE_OVERDUE.id)}`]: () =>
      jsonResponse(200, DUE_DATE_CHANGES),
    [`GET ${changesPath(CHARGE_PENDING.id)}`]: () => jsonResponse(200, []),
    ...routes,
  });
}

/** A response the test releases by hand (the pending state). */
function deferred() {
  let release: (response: Response) => void = () => {};
  const promise = new Promise<Response>((resolve) => {
    release = resolve;
  });
  return { responder: () => promise, release };
}

let app: RenderedApp | undefined;

const main = () => app!.container.querySelector('main')!;
const row = (chargeId: string) =>
  main().querySelector<HTMLTableRowElement>(`[data-charge-id="${chargeId}"]`)!;
const dialog = () => openDialog()!;
const offsetLine = () => main().querySelector('[data-due-offset]')?.textContent;
const status = () => main().querySelector('[role="status"]')?.textContent;

async function renderAccount() {
  app = await renderApp(`/billing/accounts/${FAMILY_ID}`);
  await settleUntil(
    () =>
      main().querySelector('[data-charge-id]') &&
      main().querySelector('[data-ledger-entry]') &&
      main().querySelector('[data-payment-id]'),
  );
}

const changeButton = (chargeId: string) =>
  [...row(chargeId).querySelectorAll('button')].find(
    (button) => button.textContent === 'Change due date',
  );

async function openChange(chargeId: string) {
  await click(changeButton(chargeId)!);
  await settleUntil(() => openDialog());
}

function fillChange(dueDate: string, reason: string) {
  setFieldValue(labelled<HTMLInputElement>(dialog(), 'New due date'), dueDate);
  setFieldValue(labelled<HTMLTextAreaElement>(dialog(), 'Reason'), reason);
}

async function submitDialog(name: string) {
  await click(buttonNamed(dialog(), name));
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

describe('the due-date offset (C)', () => {
  it('shows the offset to billing.view holders, without Edit', async () => {
    stub({}, { as: VIEWER });
    await renderAccount();

    expect(offsetLine()).toBe('Extra days to pay: 7 days');
    expect(
      [...main().querySelectorAll('button')].map((b) => b.textContent),
    ).not.toContain('Edit');
  });

  it('sends the version and the days, and changes nothing before the 200', async () => {
    const answer = deferred();
    const api = stub({ [PATCH_ROUTE]: answer.responder });
    await renderAccount();

    await click(buttonNamed(main(), 'Edit extra days to pay'));
    await settleUntil(() => openDialog());
    const field = labelled<HTMLInputElement>(dialog(), 'Extra days (0–60)');
    expect(field.value).toBe('7');
    expect(dialog().textContent).toContain(
      "Applies to charges posted from now on; existing due dates don't change.",
    );
    setFieldValue(field, '5');
    await submitDialog('Save');
    await settleUntil(() => api.calls(PATCH_ROUTE).length === 1);

    expect(api.calls(PATCH_ROUTE)[0]!.body).toEqual({
      version: 2,
      due_offset_days: 5,
    });
    // Pending: the dialog stays open and the page still shows 7.
    expect(openDialog()).not.toBeNull();
    expect(dialog().textContent).toContain('Saving…');
    expect(offsetLine()).toBe('Extra days to pay: 7 days');

    const summaries = api.calls(SUMMARY_ROUTE).length;
    api.set({
      [SUMMARY_ROUTE]: () =>
        jsonResponse(200, { ...SUMMARY, due_offset_days: 5, version: 3 }),
    });
    act(() => {
      answer.release(
        jsonResponse(200, {
          family_id: FAMILY_ID,
          account_id: SUMMARY.account_id,
          due_offset_days: 5,
          version: 3,
        }),
      );
    });
    await settleUntil(() => openDialog() === null);
    expect(status()).toBe('Extra days to pay set to 5 days.');
    await settleUntil(() => offsetLine() === 'Extra days to pay: 5 days');
    expect(api.calls(SUMMARY_ROUTE).length).toBeGreaterThan(summaries);
  });

  it.each([['61'], ['-1'], ['2.5'], ['']])(
    'refuses %j without a request',
    async (value) => {
      const api = stub();
      await renderAccount();
      await click(buttonNamed(main(), 'Edit extra days to pay'));
      await settleUntil(() => openDialog());

      const field = labelled<HTMLInputElement>(dialog(), 'Extra days (0–60)');
      setFieldValue(field, value);
      await submitDialog('Save');

      expect(dialog().textContent).toContain(
        'Enter a whole number from 0 to 60.',
      );
      expect(field.getAttribute('aria-invalid')).toBe('true');
      expect(api.calls(PATCH_ROUTE)).toHaveLength(0);
    },
  );

  it('sends version null for a family without an account', async () => {
    const api = stub({
      [SUMMARY_ROUTE]: () => jsonResponse(200, SUMMARY_NO_ACCOUNT),
      [CHARGES]: () =>
        jsonResponse(200, { items: [], total: 0, limit: 25, offset: 0 }),
      [LEDGER]: () => jsonResponse(200, { items: [], next_cursor: null }),
      [PAYMENTS]: () =>
        jsonResponse(200, { items: [], total: 0, limit: 25, offset: 0 }),
      [PATCH_ROUTE]: () =>
        jsonResponse(200, {
          family_id: FAMILY_ID,
          account_id: SUMMARY.account_id,
          due_offset_days: 0,
          version: 1,
        }),
    });
    app = await renderApp(`/billing/accounts/${FAMILY_ID}`);
    await settleUntil(() => offsetLine());
    expect(offsetLine()).toBe('Extra days to pay: 0 days');

    await click(buttonNamed(main(), 'Edit extra days to pay'));
    await settleUntil(() => openDialog());
    setFieldValue(
      labelled<HTMLInputElement>(dialog(), 'Extra days (0–60)'),
      '0',
    );
    await submitDialog('Save');
    await settleUntil(() => api.calls(PATCH_ROUTE).length === 1);
    expect(api.calls(PATCH_ROUTE)[0]!.body).toEqual({
      version: null,
      due_offset_days: 0,
    });
    await settleUntil(() => openDialog() === null);
  });

  it('says the account changed on 409 and refetches it; the next save sends the new version', async () => {
    const api = stub({
      [PATCH_ROUTE]: [
        () => problemWith(409, 'version_conflict'),
        () =>
          jsonResponse(200, {
            family_id: FAMILY_ID,
            account_id: SUMMARY.account_id,
            due_offset_days: 10,
            version: 4,
          }),
      ],
    });
    await renderAccount();
    await click(buttonNamed(main(), 'Edit extra days to pay'));
    await settleUntil(() => openDialog());
    const summaries = api.calls(SUMMARY_ROUTE).length;
    api.set({
      [SUMMARY_ROUTE]: () =>
        jsonResponse(200, { ...SUMMARY, due_offset_days: 3, version: 3 }),
    });

    setFieldValue(
      labelled<HTMLInputElement>(dialog(), 'Extra days (0–60)'),
      '10',
    );
    await submitDialog('Save');
    await settleUntil(() => alerts(dialog()).length > 0);
    expect(alerts(dialog())[0]).toContain(
      'This account changed. It was reloaded; check the value and save again.',
    );
    await settleUntil(() => api.calls(SUMMARY_ROUTE).length > summaries);
    await settleUntil(() => offsetLine() === 'Extra days to pay: 3 days');
    // What was typed is kept.
    expect(
      labelled<HTMLInputElement>(dialog(), 'Extra days (0–60)').value,
    ).toBe('10');

    await submitDialog('Save');
    await settleUntil(() => api.calls(PATCH_ROUTE).length === 2);
    expect(api.calls(PATCH_ROUTE)[1]!.body).toEqual({
      version: 3,
      due_offset_days: 10,
    });
  });

  it('puts a 422 on the field and a 403 in an alert', async () => {
    stub({
      [PATCH_ROUTE]: [
        () =>
          problemWith(422, 'validation_error', {
            errors: [
              { path: ['body', 'due_offset_days'], code: 'less_than_equal' },
            ],
          }),
        () => problemWith(403, 'permission_denied'),
      ],
    });
    await renderAccount();
    await click(buttonNamed(main(), 'Edit extra days to pay'));
    await settleUntil(() => openDialog());
    const field = labelled<HTMLInputElement>(dialog(), 'Extra days (0–60)');

    await submitDialog('Save');
    await settleUntil(() => field.getAttribute('aria-invalid') === 'true');
    expect(alerts(dialog())).toEqual([]);

    await submitDialog('Save');
    await settleUntil(() => alerts(dialog()).length > 0);
    expect(alerts(dialog())[0]).toContain(
      "You don't have permission to do this.",
    );
    expect(openDialog()).not.toBeNull();
  });
});

describe('the "changed" marker (D1)', () => {
  it('marks a moved due date, with the original in a tooltip', async () => {
    stub({
      [CHARGES]: () =>
        jsonResponse(200, {
          ...CHARGES_PAGE,
          items: [CHARGE_MOVED, CHARGE_PENDING],
        }),
    });
    await renderAccount();

    const marker = row(CHARGE_MOVED.id).querySelector<HTMLButtonElement>(
      '[data-due-date-changed]',
    )!;
    expect(marker.textContent).toBe('Changed. Original due date: Sep 1, 2026');
    expect(marker.querySelector('svg')?.getAttribute('aria-hidden')).toBe(
      'true',
    );
    expect(
      row(CHARGE_PENDING.id).querySelector('[data-due-date-changed]'),
    ).toBeNull();

    act(() => marker.focus());
    await settleUntil(() => document.querySelector('[role="tooltip"]'));
    expect(document.querySelector('[role="tooltip"]')?.textContent).toBe(
      'Original: Sep 1, 2026',
    );
  });
});

describe('changing a due date (D)', () => {
  it('offers the action to billing.manage holders on open charges only', async () => {
    stub();
    await renderAccount();
    for (const charge of [CHARGE_PENDING, CHARGE_OVERDUE]) {
      expect(changeButton(charge.id)?.getAttribute('aria-label')).toMatch(
        /^Change due date: /,
      );
    }
    expect(changeButton(CHARGE_PAID.id)).toBeUndefined();
    app!.unmount();

    stub({}, { as: VIEWER });
    await renderAccount();
    expect(main().textContent).not.toContain('Change due date');
    app = undefined;
  });

  it('shows the charge and its history, oldest first', async () => {
    stub({
      [CHARGES]: () =>
        jsonResponse(200, { ...CHARGES_PAGE, items: [CHARGE_MOVED] }),
    });
    await renderAccount();
    await openChange(CHARGE_MOVED.id);
    await settleUntil(() => dialog().querySelector('[data-due-date-change]'));

    const terms = [...dialog().querySelectorAll('dl > div')].map((item) => [
      item.querySelector('dt')?.textContent,
      item.querySelector('dd')?.textContent,
    ]);
    expect(terms).toEqual([
      ['Student', 'Ana López'],
      ['Kind', 'Tuition'],
      ['Description', 'Colegiatura septiembre'],
      ['Open', '$1,500.00'],
      ['Current due date', 'Sep 15, 2026'],
      ['Original due date', 'Sep 1, 2026'],
    ]);
    expect(labelled<HTMLInputElement>(dialog(), 'New due date').value).toBe(
      '2026-09-15',
    );
    const history = [
      ...dialog().querySelectorAll('[data-due-date-change]'),
    ].map((item) => item.textContent);
    expect(history).toEqual([
      'Sep 1, 2026 → Sep 10, 2026La familia pidió una semana más.Changed on Aug 28, 2026',
      'Sep 10, 2026 → Sep 15, 2026Acuerdo con dirección.Changed on Sep 9, 2026',
    ]);
  });

  it('shows the empty history and warns about a past date without blocking it', async () => {
    const api = stub({
      [`POST ${changesPath(CHARGE_PENDING.id)}`]: () =>
        jsonResponse(201, {
          ...DUE_DATE_CHANGES[0],
          charge_id: CHARGE_PENDING.id,
          previous_due_date: '2026-10-25',
          due_date: '2026-10-10',
          status: 'overdue',
        }),
    });
    await renderAccount();
    await openChange(CHARGE_PENDING.id);
    await settleUntil(() => dialog().textContent?.includes('No changes yet.'));
    expect(dialog().querySelector('[data-due-date-warning]')).toBeNull();

    fillChange('2026-10-10', 'Error de captura');
    const warning = dialog().querySelector('[data-due-date-warning]');
    expect(warning?.textContent).toBe(
      'This date is before today: this charge will be overdue.',
    );
    expect(
      labelled<HTMLInputElement>(dialog(), 'New due date').getAttribute(
        'aria-describedby',
      ),
    ).toContain(warning!.id);

    // Today itself is not "before today".
    fillChange('2026-10-20', 'Error de captura');
    expect(dialog().querySelector('[data-due-date-warning]')).toBeNull();

    fillChange('2026-10-10', '  Error de captura  ');
    await submitDialog('Change due date');
    await settleUntil(() => openDialog() === null);
    expect(
      api.calls(`POST ${changesPath(CHARGE_PENDING.id)}`)[0]!.body,
    ).toEqual({ due_date: '2026-10-10', reason: 'Error de captura' });
  });

  it('requires a reason before sending anything', async () => {
    const api = stub();
    await renderAccount();
    await openChange(CHARGE_PENDING.id);

    fillChange('2026-11-05', '   ');
    await submitDialog('Change due date');
    const reason = labelled<HTMLTextAreaElement>(dialog(), 'Reason');
    expect(reason.getAttribute('aria-invalid')).toBe('true');
    expect(dialog().textContent).toContain('Write the reason for the change.');
    expect(document.activeElement).toBe(reason);
    expect(dialog().textContent).toContain('0/500 characters');
    expect(api.calls(`POST ${changesPath(CHARGE_PENDING.id)}`)).toHaveLength(0);
  });

  it('changes nothing before the 201, then closes, says so and refetches', async () => {
    const answer = deferred();
    const post = `POST ${changesPath(CHARGE_PENDING.id)}`;
    const api = stub({ [post]: answer.responder });
    await renderAccount();
    await openChange(CHARGE_PENDING.id);

    fillChange('2026-11-05', 'La familia pidió más tiempo');
    await submitDialog('Change due date');
    await settleUntil(() => api.calls(post).length === 1);
    const charges = api.calls(CHARGES).length;
    const summaries = api.calls(SUMMARY_ROUTE).length;
    const ledgers = api.calls(LEDGER).length;
    await settle();
    expect(openDialog()).not.toBeNull();
    expect(dialog().textContent).toContain('Saving…');
    expect(row(CHARGE_PENDING.id).textContent).toContain('Oct 25, 2026');
    expect(api.calls(CHARGES)).toHaveLength(charges);

    act(() => {
      answer.release(
        jsonResponse(201, {
          id: '56000000-0000-4000-8000-000000000009',
          charge_id: CHARGE_PENDING.id,
          previous_due_date: '2026-10-25',
          due_date: '2026-11-05',
          reason: 'La familia pidió más tiempo',
          changed_by_user_id: TEST_USER.id,
          created_at: '2026-10-20T18:00:00Z',
          status: 'pending',
        }),
      );
    });
    await settleUntil(() => openDialog() === null);
    expect(status()).toBe('Due date changed to Nov 5, 2026; status: Pending.');
    await settleUntil(
      () =>
        api.calls(CHARGES).length > charges &&
        api.calls(SUMMARY_ROUTE).length > summaries &&
        api.calls(LEDGER).length > ledgers,
    );
  });

  it.each([
    [
      'charge_settled',
      409,
      "This charge is already paid or void, so its due date can't change. The charges were reloaded.",
    ],
    ['not_found', 404, "We couldn't find what you were looking for."],
  ] as const)(
    'shows %s and refetches the charges',
    async (code, httpStatus, message) => {
      const post = `POST ${changesPath(CHARGE_PENDING.id)}`;
      const api = stub({ [post]: () => problemWith(httpStatus, code) });
      await renderAccount();
      await openChange(CHARGE_PENDING.id);
      const charges = api.calls(CHARGES).length;

      fillChange('2026-11-05', 'Motivo');
      await submitDialog('Change due date');
      await settleUntil(() => alerts(dialog()).length > 0);
      expect(alerts(dialog())[0]).toContain(message);
      await settleUntil(() => api.calls(CHARGES).length > charges);
      // Nothing typed is lost.
      expect(labelled<HTMLTextAreaElement>(dialog(), 'Reason').value).toBe(
        'Motivo',
      );
    },
  );

  it('puts due_date_unchanged and a 422 on the fields, and a 403 in an alert', async () => {
    const post = `POST ${changesPath(CHARGE_PENDING.id)}`;
    const api = stub({
      [post]: [
        () =>
          problemWith(409, 'due_date_unchanged', {
            details: { due_date: '2026-10-25' },
          }),
        () =>
          problemWith(422, 'validation_error', {
            errors: [{ path: ['body', 'reason'], code: 'too_long' }],
          }),
        () => problemWith(403, 'permission_denied'),
      ],
    });
    await renderAccount();
    await openChange(CHARGE_PENDING.id);
    const date = labelled<HTMLInputElement>(dialog(), 'New due date');
    const reason = labelled<HTMLTextAreaElement>(dialog(), 'Reason');
    const charges = api.calls(CHARGES).length;

    setFieldValue(reason, 'Mismo día');
    await submitDialog('Change due date');
    await settleUntil(() => date.getAttribute('aria-invalid') === 'true');
    expect(dialog().textContent).toContain(
      'Choose a different date: this is already the due date.',
    );
    expect(alerts(dialog())).toEqual([]);

    fillChange('2026-11-05', 'Mismo día');
    await submitDialog('Change due date');
    await settleUntil(() => reason.getAttribute('aria-invalid') === 'true');
    expect(dialog().textContent).toContain('This text is too long.');
    expect(date.getAttribute('aria-invalid')).toBeNull();

    await submitDialog('Change due date');
    await settleUntil(() => alerts(dialog()).length > 0);
    expect(alerts(dialog())[0]).toContain(
      "You don't have permission to do this.",
    );
    expect(api.calls(post)).toHaveLength(3);
    expect(api.calls(CHARGES)).toHaveLength(charges);
    expect(openDialog()).not.toBeNull();
  });
});
