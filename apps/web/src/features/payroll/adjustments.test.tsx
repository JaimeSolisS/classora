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
  fieldByLabel,
  labelled,
  setFieldValue,
  typeInto,
} from '@/test/dom';
import {
  buttonNamed,
  jsonResponse,
  renderApp,
  settleUntil,
  type RenderedApp,
} from '@/test/render-app';
import { adjustmentCreateBody, validateAdjustmentForm } from './adjustmentForm';
import {
  HOURLY,
  RATE_CURRENT,
  SETTINGS_ON,
  TUTOR_ID,
  UNRESOLVED_BEFORE_FIRST,
  UNRESOLVED_NO_RATE,
} from './fixtures';
import {
  ADMIN_ID,
  EARLIER_PERIOD_ID,
  FINANCE_ID,
  FUTURE_PERIOD_ID,
  LINE_ADJ_FUTURE,
  LINE_ADJ_MANUAL,
  LINE_ADJUSTMENT,
  LINE_CARRY_IN,
  LINE_CARRY_OUT,
  LINE_HOURLY,
  OWNER_ID,
  PERIOD_FINALIZED,
  PERIOD_ID,
  PERIOD_NOT_ENDED,
  PERIOD_OPEN,
  PERIOD_PREPARED,
  review,
} from './periodFixtures';
import {
  absoluteAmount,
  adjustmentKindKey,
  canCarryForward,
  deleteUnderFinalizedOn,
} from './periodRules';
import { ApiError } from '@/app/api';
import type { LocalDate } from '@/lib/time';

// M7-08b F1: each adjustment kind on the lines, the three entry points of the
// manual dialog (success, every mapped refusal, the password then the same
// key, a lost answer), carry-forward (banner, dialog, refusals, the prepare
// refusal's offer), the rate delete refusal, and who sees what.

const ME = 'GET /api/v1/auth/me';
const SETTINGS = 'GET /api/v1/payroll/settings';
const LINES = `GET /api/v1/payroll/periods/${PERIOD_ID}/lines`;
const TARGET_LINES = `GET /api/v1/payroll/periods/${FUTURE_PERIOD_ID}/lines`;
const LIST = 'GET /api/v1/payroll/periods';
const PREPARE = `POST /api/v1/payroll/periods/${PERIOD_ID}/prepare`;
const RECALCULATE = `POST /api/v1/payroll/periods/${PERIOD_ID}/recalculate`;
const CARRY = `POST /api/v1/payroll/periods/${PERIOD_ID}/carry-forward`;
const ADJUST = `POST /api/v1/payroll/tutors/${TUTOR_ID}/adjustments`;
const UNRESOLVED = 'GET /api/v1/payroll/unresolved-work';
const COMPENSATION = `GET /api/v1/payroll/tutors/${TUTOR_ID}/compensation`;
const REAUTH = 'POST /api/v1/auth/reauthenticate';
// A finalized period shows its payments (M7-09b): every refresh refetches them.
const PAYMENTS = `GET /api/v1/payroll/periods/${PERIOD_ID}/payments`;

const user = (
  roles: SessionUserBody['roles'],
  permissions: SessionUserBody['permissions'],
  id = TEST_USER.id,
): SessionUserBody => ({ ...TEST_USER, id, roles, permissions });

const FINANCE = user(
  ['finance_staff'],
  ['payroll.view', 'payroll.manage', 'payroll.prepare', 'schedule.view'],
  FINANCE_ID,
);
const VIEWER = user(['finance_staff'], ['payroll.view'], FINANCE_ID);
const OWNER = user(
  ['owner'],
  ['payroll.view', 'payroll.manage', 'payroll.prepare', 'payroll.finalize'],
  OWNER_ID,
);
const ADMIN = user(['administrator'], ['payroll.finalize'], ADMIN_ID);
const FINANCE_TUTOR = user(
  ['finance_staff', 'tutor'],
  ['payroll.view', 'payroll.manage', 'payroll.prepare'],
  TUTOR_ID,
);
const OWNER_TUTOR = user(
  ['owner', 'tutor'],
  ['payroll.view', 'payroll.manage', 'payroll.prepare'],
  TUTOR_ID,
);

/** A negative open period that has ended (October 2026). */
const NEGATIVE = review(PERIOD_OPEN, [LINE_HOURLY, LINE_ADJ_MANUAL], '-300.00');
const FINALIZED = review(
  PERIOD_FINALIZED,
  [LINE_HOURLY, LINE_ADJUSTMENT],
  '600.00',
);
const POSTED = {
  id: '95000000-0000-4000-8000-000000000001',
  kind: 'manual',
  tutor_user_id: TUTOR_ID,
  target_period_id: FUTURE_PERIOD_ID,
  created_at: '2026-10-20T15:00:00Z',
};
const CARRIED = {
  period_id: PERIOD_ID,
  target_period_id: FUTURE_PERIOD_ID,
  adjustment_ids: [],
};
const lost = () => {
  throw new TypeError('Failed to fetch');
};

function stub(
  routes: Record<string, Responder | Responder[]> = {},
  as: SessionUserBody = FINANCE,
): ApiStub {
  return stubApi({
    [ME]: meAs(as),
    [SETTINGS]: () => jsonResponse(200, SETTINGS_ON),
    [LIST]: () =>
      jsonResponse(200, { items: [], total: 0, limit: 25, offset: 0 }),
    [LINES]: () => jsonResponse(200, FINALIZED),
    [TARGET_LINES]: () => jsonResponse(200, review(PERIOD_NOT_ENDED, [])),
    [PAYMENTS]: () => jsonResponse(200, []),
    [REAUTH]: () =>
      jsonResponse(200, { reauthenticated_until: '2026-10-19T18:05:00Z' }),
    ...routes,
  });
}

let app: RenderedApp | undefined;

const main = () => app!.container.querySelector('main')!;
const text = () => main().textContent ?? '';
const dialogTitled = (title: string) =>
  [...document.querySelectorAll<HTMLElement>('[role="dialog"]')].find(
    (dialog) => dialog.querySelector('h2')?.textContent === title,
  );
const adjustDialog = () =>
  document.querySelector<HTMLElement>('[data-adjustment-dialog]');
const carryDialog = () =>
  document.querySelector<HTMLElement>('[data-carry-dialog]');
const statusText = () =>
  [...main().querySelectorAll('[role="status"]')]
    .map((s) => s.textContent)
    .join('|');
const button = (name: string) =>
  [...main().querySelectorAll('button')].find(
    (candidate) => candidate.textContent === name,
  );
const keys = (api: ApiStub, route: string) =>
  api.calls(route).map((call) => call.headers.get('Idempotency-Key'));

async function openReview() {
  app = await renderApp(`/payroll/periods/${PERIOD_ID}`);
  await settleUntil(() => main().querySelector('[data-period-head]'));
}

function fill(
  dialog: HTMLElement,
  {
    direction = 'take_back',
    amount = '120',
    reason = 'Tarifa equivocada',
  } = {},
) {
  const radio = dialog.querySelector<HTMLInputElement>(
    `input[value="${direction}"]`,
  )!;
  radio.click();
  typeInto(fieldByLabel(dialog, 'Amount'), amount);
  setFieldValue(labelled<HTMLTextAreaElement>(dialog, 'Reason'), reason);
}

async function openCorrect() {
  await openReview();
  await click(
    main().querySelector<HTMLElement>(
      `[data-correct-line="${LINE_HOURLY.id}"]`,
    )!,
  );
  await settleUntil(() => adjustDialog());
}

async function save(dialog = adjustDialog()!) {
  await click(
    buttonNamed(
      dialog,
      dialog.querySelector('button[type="submit"]')!.textContent,
    ),
  );
}

beforeEach(() => {
  applyLanguage('en');
});

afterEach(() => {
  app?.unmount();
  app = undefined;
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('adjustment lines (A)', () => {
  const cells = (id: string) =>
    [
      ...main().querySelectorAll(
        `[data-period-lines-table] tr[data-line-id="${id}"] td`,
      ),
    ].map((cell) => cell.textContent);

  it('labels each kind, signs a take-back, shows the reason and the origin', async () => {
    stub({
      [LINES]: () =>
        jsonResponse(
          200,
          review(
            PERIOD_OPEN,
            [
              LINE_ADJUSTMENT,
              LINE_ADJ_MANUAL,
              LINE_CARRY_IN,
              LINE_CARRY_OUT,
              LINE_ADJ_FUTURE,
            ],
            '-60.00',
          ),
        ),
    });
    await openReview();

    expect(cells(LINE_ADJUSTMENT.id)).toEqual([
      'Sep 28, 2026',
      'Late approval',
      'Work on Sep 28, 2026IndividualOpen the origin period',
      '$50.00',
    ]);
    expect(cells(LINE_ADJ_MANUAL.id)).toEqual([
      '—',
      'Manual adjustment',
      'Reason: Tarifa equivocada en septiembre',
      '-$120.00',
    ]);
    // A carry-forward links each half to the other's period (D1).
    const carriedIn = main().querySelector(
      `tr[data-line-id="${LINE_CARRY_IN.id}"] a`,
    );
    expect(carriedIn?.getAttribute('href')).toBe(
      `/payroll/periods/${EARLIER_PERIOD_ID}`,
    );
    expect(cells(LINE_CARRY_IN.id)[1]).toBe('Carried balance');
    const carriedOut = main().querySelector(
      `tr[data-line-id="${LINE_CARRY_OUT.id}"] a`,
    );
    expect(carriedOut?.getAttribute('href')).toBe(
      `/payroll/periods?tutor=${TUTOR_ID}`,
    );
    expect(cells(LINE_ADJ_FUTURE.id)[1]).toBe('Adjustment');
    // A3: said once; an open period has no "Correct line".
    expect(main().querySelectorAll('[data-adjustments-note]')).toHaveLength(1);
    expect(main().querySelector('[data-correct-line]')).toBeNull();
  });

  it('labels in Spanish', async () => {
    applyLanguage('es');
    stub({
      [LINES]: () =>
        jsonResponse(200, review(PERIOD_OPEN, [LINE_ADJUSTMENT], '50.00')),
    });
    await openReview();
    expect(text()).toContain('Aprobación tardía');
  });

  it('the pure readers', () => {
    expect(adjustmentKindKey('hours_correction')).toBe('hours_correction');
    expect(adjustmentKindKey('other')).toBeNull();
    expect(absoluteAmount('-300.00')).toBe('300.00');
    expect(
      canCarryForward(PERIOD_OPEN, '-1.00', '2026-10-20' as LocalDate),
    ).toBe(true);
    expect(
      canCarryForward(PERIOD_OPEN, '-1.00', '2026-10-15' as LocalDate),
    ).toBe(false);
    expect(
      canCarryForward(PERIOD_OPEN, '0.00', '2026-10-20' as LocalDate),
    ).toBe(false);
    expect(
      canCarryForward(PERIOD_PREPARED, '-1.00', '2026-10-20' as LocalDate),
    ).toBe(false);
  });
});

describe('who sees the manual controls (Access)', () => {
  it.each([
    ['Finance', FINANCE, true],
    ['the Owner', OWNER, true],
    ['an Owner who is the tutor', OWNER_TUTOR, true],
    ['Finance who is the tutor', FINANCE_TUTOR, false],
    ['a delegated Administrator', ADMIN, false],
    ['a viewer', VIEWER, false],
  ])('%s: "Correct line" on a finalized period', async (_who, as, shown) => {
    stub({}, as);
    await openReview();
    expect(main().querySelector('[data-correct-line]') !== null).toBe(shown);
  });
});

describe('manual adjustment on a line (B)', () => {
  it('sends a signed amount, the line and a key; announces the target period', async () => {
    const api = stub({ [ADJUST]: () => jsonResponse(201, POSTED) });
    await openCorrect();
    const dialog = adjustDialog()!;
    expect(
      dialog.querySelector('[data-adjustment-reference]')?.textContent,
    ).toContain('A line of a finalized period');
    expect(dialog.querySelector('[data-reason-help]')?.textContent).toContain(
      'The tutor will see this reason on their payslip',
    );
    expect(dialog.querySelector('[data-stranded-warning]')).toBeNull();
    fill(dialog);
    await save();
    await settleUntil(() => !adjustDialog());

    const [call] = api.calls(ADJUST);
    expect(call!.body).toEqual({
      amount: '-120',
      reason: 'Tarifa equivocada',
      origin_line_id: LINE_HOURLY.id,
    });
    expect(call!.headers.get('Idempotency-Key')).toMatch(/^[0-9a-f-]{36}$/);
    await settleUntil(() => statusText().includes('Adjustment saved'));
    expect(statusText()).toContain(
      'It will apply in the period from Jan 1, 2099 to Jan 15, 2099.',
    );
    // E2: no amount in the live region.
    expect(statusText()).not.toMatch(/\$|120/);
    expect(api.calls(LINES).length).toBeGreaterThan(1);
  });

  it('checks the fields before sending', async () => {
    const api = stub();
    await openCorrect();
    await save();
    expect(adjustDialog()!.textContent).toContain(
      'Choose pay more or take back.',
    );
    expect(adjustDialog()!.textContent).toContain('Enter the amount.');
    expect(adjustDialog()!.textContent).toContain('Enter the reason.');
    expect(api.calls(ADJUST)).toHaveLength(0);
    expect(
      validateAdjustmentForm({
        direction: 'pay_more',
        amount: '0',
        reason: 'x',
      }).amount,
    ).toBe('payroll.validation.amountPositive');
    expect(
      validateAdjustmentForm({
        direction: 'pay_more',
        amount: '1',
        reason: 'a'.repeat(1001),
      }).reason,
    ).toBe('payroll.validation.reasonTooLong');
    expect(
      adjustmentCreateBody(
        { direction: 'pay_more', amount: ' 50.5 ', reason: ' r ' },
        {
          kind: 'session',
          sessionId: 's',
          serviceDate: '2026-10-01',
          reason: 'no_rate',
        },
      ),
    ).toEqual({ amount: '50.5', reason: 'r', class_session_id: 's' });
  });

  it('the password dialog, then the same body with the same key', async () => {
    const api = stub({
      [ADJUST]: [
        () => problem(403, 'reauth_required'),
        () => jsonResponse(201, POSTED),
      ],
    });
    await openCorrect();
    fill(adjustDialog()!);
    await save();
    const password = dialogTitled('Confirm your password')!;
    typeInto(fieldByLabel(password, 'Password'), 'my-own-password');
    await click(buttonNamed(password, 'Confirm'));
    await settleUntil(() => statusText().includes('Adjustment saved'));

    const [first, second] = api.calls(ADJUST);
    expect(second!.headers.get('Idempotency-Key')).toBe(
      first!.headers.get('Idempotency-Key'),
    );
    expect(second!.body).toEqual(first!.body);
  });

  it('a lost answer locks the fields and resends the same key; a new opening gets a new one', async () => {
    const api = stub({
      [ADJUST]: [lost, () => problem(503, 'database_unavailable'), lost],
    });
    await openCorrect();
    fill(adjustDialog()!);
    await save();
    await settleUntil(() => alerts(adjustDialog()!).length > 0);
    expect(alerts(adjustDialog()!).join()).toContain(
      "We don't know whether the adjustment was saved",
    );
    expect(fieldByLabel(adjustDialog()!, 'Amount').disabled).toBe(true);
    expect(
      labelled<HTMLTextAreaElement>(adjustDialog()!, 'Reason').disabled,
    ).toBe(true);

    await click(buttonNamed(adjustDialog()!, 'Send the same adjustment again'));
    await settleUntil(() => api.calls(ADJUST).length === 2);
    expect(keys(api, ADJUST)[1]).toBe(keys(api, ADJUST)[0]);

    await click(buttonNamed(adjustDialog()!, 'Cancel'));
    await settleUntil(() => !adjustDialog());
    expect(statusText()).toContain('The adjustment may have been saved');
    await click(
      main().querySelector<HTMLElement>(
        `[data-correct-line="${LINE_HOURLY.id}"]`,
      )!,
    );
    fill(adjustDialog()!);
    await save();
    await settleUntil(() => api.calls(ADJUST).length === 3);
    expect(keys(api, ADJUST)[2]).not.toBe(keys(api, ADJUST)[0]);
  });

  it.each([
    ['payroll_own_compensation', 403, 'Someone else must change your pay.'],
    [
      'payroll_line_not_finalized',
      409,
      "That line isn't in a finalized period",
    ],
    ['payroll_no_period', 409, 'This tutor has no payroll period yet'],
    ['idempotency_key_reused', 422, 'This submission was already used'],
    ['not_found', 404, ''],
  ])('%s shows in the alert', async (code, status, words) => {
    const api = stub({ [ADJUST]: () => problem(status, code) });
    await openCorrect();
    fill(adjustDialog()!);
    await save();
    await settleUntil(() => alerts(adjustDialog()!).length > 0);
    expect(alerts(adjustDialog()!).join()).toContain(words);
    expect(adjustDialog()).not.toBeNull();
    expect(api.calls(ADJUST)).toHaveLength(1);
  });

  it.each([
    ['amount', 'amount_zero', 'Amount', 'Enter an amount other than 0.'],
    ['amount', 'money_out_of_range', 'Amount', 'The amount is too large.'],
    ['reason', 'text_too_long', 'Reason', 'This text is too long.'],
    ['reason', 'text_invalid', 'Reason', 'This text has an invalid character.'],
  ])(
    'a 422 on %s (%s) goes on its field',
    async (field, code, _label, words) => {
      stub({
        [ADJUST]: () =>
          problemWith(422, 'validation_error', {
            errors: [{ path: ['body', field], code }],
          }),
      });
      await openCorrect();
      fill(adjustDialog()!);
      await save();
      await settleUntil(() => adjustDialog()!.textContent?.includes(words));
    },
  );

  it('a lone surrogate (text_invalid before the body) goes on the reason', async () => {
    stub({ [ADJUST]: () => problem(422, 'text_invalid') });
    await openCorrect();
    fill(adjustDialog()!);
    await save();
    await settleUntil(() =>
      adjustDialog()!.textContent?.includes(
        'This text has an invalid character',
      ),
    );
  });
});

describe('paying unresolved work by hand (D2)', () => {
  it('shows on a blocking item only, sends the session, and refreshes the list', async () => {
    const api = stub({
      [UNRESOLVED]: [
        () =>
          jsonResponse(200, {
            items: [UNRESOLVED_NO_RATE, UNRESOLVED_BEFORE_FIRST],
            next_cursor: null,
          }),
        () =>
          jsonResponse(200, {
            items: [UNRESOLVED_BEFORE_FIRST],
            next_cursor: null,
          }),
      ],
      [ADJUST]: () => jsonResponse(201, POSTED),
    });
    app = await renderApp('/payroll/unresolved');
    await settleUntil(() => main().querySelector('[data-unresolved-table]'));
    const buttons = () => [
      ...main().querySelectorAll<HTMLElement>(
        '[data-unresolved-table] [data-pay-by-hand]',
      ),
    ];
    expect(buttons().map((b) => b.dataset.payByHand)).toEqual([
      UNRESOLVED_NO_RATE.class_session_id,
    ]);
    await click(buttons()[0]!);
    await settleUntil(() => adjustDialog());
    expect(adjustDialog()!.textContent).toContain(
      'Unresolved work on Oct 15, 2026',
    );
    fill(adjustDialog()!, { direction: 'pay_more', amount: '250' });
    await save();
    await settleUntil(() => statusText().includes('Adjustment saved'));
    expect(api.calls(ADJUST)[0]!.body).toEqual({
      amount: '250',
      reason: 'Tarifa equivocada',
      class_session_id: UNRESOLVED_NO_RATE.class_session_id,
    });
    await settleUntil(() => buttons().length === 0);
  });

  it.each([
    ['payroll_work_settled', 'An adjustment already pays that work.'],
    ['payroll_work_not_unresolved', "That work isn't unresolved any more"],
    ['payroll_work_before_first_period', 'pay it with a one-time item'],
  ])('%s shows in the alert and refreshes the list', async (code, words) => {
    const api = stub({
      [UNRESOLVED]: () =>
        jsonResponse(200, { items: [UNRESOLVED_NO_RATE], next_cursor: null }),
      [ADJUST]: () => problem(409, code),
    });
    app = await renderApp('/payroll/unresolved');
    await settleUntil(() => main().querySelector('[data-pay-by-hand]'));
    await click(main().querySelector<HTMLElement>('[data-pay-by-hand]')!);
    fill(adjustDialog()!);
    await save();
    await settleUntil(() => alerts(adjustDialog()!).join().includes(words));
    await settleUntil(() => api.calls(UNRESOLVED).length === 2);
  });

  it('a failed refresh says the list may be out of date and disables the action', async () => {
    stub({
      [UNRESOLVED]: [
        () =>
          jsonResponse(200, { items: [UNRESOLVED_NO_RATE], next_cursor: null }),
        () => problem(503, 'database_unavailable'),
      ],
      [ADJUST]: () => problem(409, 'payroll_work_settled'),
    });
    app = await renderApp('/payroll/unresolved');
    await settleUntil(() => main().querySelector('[data-pay-by-hand]'));
    await click(main().querySelector<HTMLElement>('[data-pay-by-hand]')!);
    fill(adjustDialog()!);
    await save();
    await settleUntil(() => alerts(adjustDialog()!).length > 0);
    await click(buttonNamed(adjustDialog()!, 'Cancel'));
    await settleUntil(() => text().includes('may be out of date'));
    expect(
      main().querySelector<HTMLButtonElement>('[data-pay-by-hand]')!.disabled,
    ).toBe(true);
  });

  it('hides it from Finance on their own pay and from a viewer', async () => {
    stub(
      {
        [UNRESOLVED]: () =>
          jsonResponse(200, { items: [UNRESOLVED_NO_RATE], next_cursor: null }),
      },
      FINANCE_TUTOR,
    );
    app = await renderApp('/payroll/unresolved');
    await settleUntil(() => main().querySelector('[data-unresolved-table]'));
    expect(main().querySelector('[data-pay-by-hand]')).toBeNull();
  });
});

describe('free manual adjustment on the tutor page (B5) and the rate delete (D1)', () => {
  it('warns about the stranded one-time item, sends no reference', async () => {
    const api = stub({
      [COMPENSATION]: () => jsonResponse(200, HOURLY),
      [ADJUST]: () => jsonResponse(201, POSTED),
    });
    app = await renderApp(`/payroll/tutors/${TUTOR_ID}`);
    await settleUntil(() => button('Manual adjustment'));
    await click(button('Manual adjustment')!);
    await settleUntil(() => adjustDialog());
    const warning = adjustDialog()!.querySelector('[data-stranded-warning]');
    expect(warning?.textContent).toContain('first delete that waiting item');
    expect(warning?.textContent).toContain('paid twice');
    expect(adjustDialog()!.textContent).toContain('No reference');
    fill(adjustDialog()!, { direction: 'pay_more', amount: '600' });
    await save();
    await settleUntil(() => statusText().includes('Adjustment saved'));
    expect(api.calls(ADJUST)[0]!.body).toEqual({
      amount: '600',
      reason: 'Tarifa equivocada',
    });
  });

  it('is hidden from a viewer and from Finance on their own pay', async () => {
    stub({ [COMPENSATION]: () => jsonResponse(200, HOURLY) }, FINANCE_TUTOR);
    app = await renderApp(`/payroll/tutors/${TUTOR_ID}`);
    await settleUntil(() => main().querySelector('h1'));
    await settleUntil(() =>
      text().includes('Someone else must change your pay'),
    );
    expect(button('Manual adjustment')).toBeUndefined();
  });

  it('a delete under a finalized line names the date and the way out', async () => {
    stub({
      [COMPENSATION]: () => jsonResponse(200, HOURLY),
      [`DELETE /api/v1/payroll/tutors/${TUTOR_ID}/rates/${RATE_CURRENT.id}`]:
        () =>
          problemWith(409, 'compensation_rate_delete_under_finalized_line', {
            details: { latest_finalized_on: '2026-10-15' },
          }),
    });
    app = await renderApp(`/payroll/tutors/${TUTOR_ID}`);
    await settleUntil(() =>
      main().querySelector(`[data-version-id="${RATE_CURRENT.id}"]`),
    );
    await click(
      main()
        .querySelector(`[data-version-id="${RATE_CURRENT.id}"]`)!
        .querySelector<HTMLElement>('button')!,
    );
    const confirm = document.querySelector<HTMLElement>(
      '[role="alertdialog"], [role="dialog"]',
    )!;
    await click(buttonNamed(confirm, 'Delete'));
    await settleUntil(() => alerts(document).join().includes('Oct 15, 2026'));
    expect(alerts(document).join()).toContain(
      'Add a new version starting after Oct 15, 2026 and pay the difference with a manual adjustment.',
    );
  });

  it('reads the date only from its own code', () => {
    const error = new ApiError({
      code: 'compensation_rate_delete_under_finalized_line',
      status: 409,
      problem: {
        type: 'about:blank',
        title: 'Problem',
        status: 409,
        code: 'compensation_rate_delete_under_finalized_line',
        request_id: 'req-1',
        details: { latest_finalized_on: 'soon' },
      },
    });
    expect(deleteUnderFinalizedOn(error)).toBeNull();
  });
});

describe('carry-forward (C)', () => {
  it('banner on an ended negative open period; confirm names the amount; refreshes', async () => {
    const api = stub({
      [LINES]: [
        () => jsonResponse(200, NEGATIVE),
        () =>
          jsonResponse(
            200,
            review(PERIOD_OPEN, [LINE_HOURLY, LINE_CARRY_OUT], '0.00'),
          ),
      ],
      [CARRY]: () => jsonResponse(201, CARRIED),
    });
    await openReview();
    const banner = main().querySelector('[data-carry-banner]')!;
    expect(banner.textContent).toContain('later periods are waiting');
    await click(buttonNamed(banner, 'Carry the balance to the next period'));
    await settleUntil(() => carryDialog());
    expect(carryDialog()!.textContent).toContain('$300.00 of Ana Tutora');
    expect(carryDialog()!.textContent).toContain("isn't forgiven");
    await click(buttonNamed(carryDialog()!, 'Carry the balance'));
    await settleUntil(() => statusText().includes('Balance carried'));
    expect(carryDialog()).toBeNull();
    expect(keys(api, CARRY)[0]).toMatch(/^[0-9a-f-]{36}$/);
    expect(statusText()).not.toContain('$');
    expect(main().querySelector('[data-carry-banner]')).toBeNull();
  });

  it('a lost answer keeps the dialog, resends the same key, and closes once nothing is left', async () => {
    const api = stub({
      [LINES]: [
        () => jsonResponse(200, NEGATIVE),
        () => jsonResponse(200, NEGATIVE),
        () =>
          jsonResponse(
            200,
            review(PERIOD_OPEN, [LINE_HOURLY, LINE_CARRY_OUT], '0.00'),
          ),
      ],
      [CARRY]: [lost, lost],
    });
    await openReview();
    await click(button('Carry the balance to the next period')!);
    await click(buttonNamed(carryDialog()!, 'Carry the balance'));
    await settleUntil(() =>
      carryDialog()?.querySelector('[data-carry-unknown]'),
    );
    await settleUntil(() => api.calls(LINES).length === 2);
    await click(buttonNamed(carryDialog()!, 'Carry the balance'));
    await settleUntil(() => api.calls(CARRY).length === 2);
    expect(keys(api, CARRY)[1]).toBe(keys(api, CARRY)[0]);
    await settleUntil(() => !carryDialog());
    expect(statusText()).toContain('no longer has a negative balance');
  });

  it.each([
    ['payroll_total_not_negative', "isn't below zero any more"],
    ['payroll_period_not_open', "This period's status changed"],
    ['payroll_period_not_ended', "hasn't ended yet"],
  ])(
    '%s closes the dialog, refreshes and shows the refusal',
    async (code, words) => {
      const api = stub({
        [LINES]: () => jsonResponse(200, NEGATIVE),
        [CARRY]: () => problem(409, code),
      });
      await openReview();
      await click(button('Carry the balance to the next period')!);
      await click(buttonNamed(carryDialog()!, 'Carry the balance'));
      await settleUntil(() => alerts(main()).length > 0);
      expect(carryDialog()).toBeNull();
      expect(alerts(main()).join()).toContain(words);
      expect(api.calls(LINES)).toHaveLength(2);
    },
  );

  it("prepare's payroll_negative_total offers the carry and says why later periods wait", async () => {
    stub({
      [LINES]: () => jsonResponse(200, NEGATIVE),
      [PREPARE]: () => problem(409, 'payroll_negative_total'),
      [RECALCULATE]: () => jsonResponse(200, { id: PERIOD_ID, line_count: 2 }),
    });
    await openReview();
    await click(button('Prepare')!);
    await settleUntil(() => main().querySelector('[data-negative-refusal]'));
    const refusal = main().querySelector<HTMLElement>(
      '[data-negative-refusal]',
    )!;
    expect(refusal.textContent).toContain('later periods are waiting');
    await click(buttonNamed(refusal, 'Carry the balance to the next period'));
    await settleUntil(() => carryDialog());
  });

  it('no banner action without payroll.prepare, nor before the period ends', async () => {
    stub({ [LINES]: () => jsonResponse(200, NEGATIVE) }, VIEWER);
    await openReview();
    expect(main().querySelector('[data-carry-banner]')).toBeNull();
    expect(main().querySelector('[data-negative-total]')).not.toBeNull();
  });
});
