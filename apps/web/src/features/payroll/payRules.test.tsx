import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { problemWith } from '@/features/academics/testing';
import type { SessionUserBody } from '@/features/auth/session';
import { applyLanguage } from '@/i18n';
import { parseLocalDate } from '@/lib/time';
import {
  meAs,
  noContent,
  problem,
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
import type { PayRuleOverview } from './api';
import {
  LATE_SEED,
  MAKEUP_SEED,
  NO_SHOW_LATER,
  NO_SHOW_SEED,
  NO_SHOW_UPCOMING,
  RULES_SEEDED,
  RULES_VERSIONED,
  TODAY,
} from './fixtures';
import {
  initialPayRuleForm,
  latestPayRule,
  payRuleCreateBody,
  payRuleServerErrors,
  percentText,
  validatePayRuleForm,
} from './payRuleForm';

// #444 B: the sentences, versions and history, the dialog's body and every
// refusal, a colleague's version arriving in each order (QA #425 D1, D3), and
// no controls without payroll.manage.

const ME = 'GET /api/v1/auth/me';
const RULES = 'GET /api/v1/payroll/pay-rules';
const POST = 'POST /api/v1/payroll/pay-rules';
const del = (id: string) => `DELETE /api/v1/payroll/pay-rules/${id}`;

const user = (
  roles: SessionUserBody['roles'],
  permissions: SessionUserBody['permissions'],
): SessionUserBody => ({ ...TEST_USER, roles, permissions });
const FINANCE = user(['finance_staff'], ['payroll.view', 'payroll.manage']);
const VIEWER = user(['finance_staff'], ['payroll.view']);

function stub(
  routes: Record<string, Responder | Responder[]> = {},
  rules: PayRuleOverview = RULES_SEEDED,
  as: SessionUserBody = FINANCE,
): ApiStub {
  return stubApi({
    [ME]: meAs(as),
    [RULES]: () => jsonResponse(200, rules),
    ...routes,
  });
}

let app: RenderedApp | undefined;
const main = () => app!.container.querySelector('main')!;
const dialog = () => openDialog()!;
const block = (event: string) =>
  main().querySelector<HTMLElement>(`[data-pay-rule-event="${event}"]`)!;
const version = (id: string) =>
  main().querySelector<HTMLElement>(`[data-version-id="${id}"]`);
const newVersion = (event: string) =>
  block(event).querySelector<HTMLButtonElement>('button')!;
const mode = () => labelled<HTMLSelectElement>(dialog(), 'What it pays');
const percent = () =>
  labelled<HTMLInputElement>(dialog(), 'Percent of the class');
const amount = () => labelled<HTMLInputElement>(dialog(), 'Amount (MXN)');
const date = () => labelled<HTMLInputElement>(dialog(), 'Applies from');
const save = () => buttonNamed(dialog(), 'Publish version');

async function open() {
  app = await renderApp('/payroll/pay-rules');
  await settleUntil(() => main().querySelector('[data-pay-rule-event]'));
}

async function openDialogFor(event: string) {
  await open();
  await click(newVersion(event));
  await settleUntil(() => openDialog());
}

beforeEach(() => {
  applyLanguage('en');
});

afterEach(() => {
  app?.unmount();
  app = undefined;
  vi.unstubAllGlobals();
});

describe('the pay rules page (B1, B4)', () => {
  it('says each rule as a sentence, with its date', async () => {
    stub();
    await open();
    expect(version(LATE_SEED.id)?.textContent).toContain("Doesn't pay");
    expect(version(NO_SHOW_SEED.id)?.textContent).toContain(
      'Pays 100% of the class',
    );
    expect(version(MAKEUP_SEED.id)?.textContent).toContain('From ');
  });

  it('in Spanish too', async () => {
    applyLanguage('es');
    stub({}, RULES_VERSIONED);
    await open();
    expect(version(LATE_SEED.id)?.textContent).toContain('No paga');
    expect(block('no_show').textContent).toContain('Paga 12.5% de la clase');
    expect(version(NO_SHOW_UPCOMING.id)?.textContent).toContain('Paga $150.00');
  });

  it('shows the upcoming versions and a collapsed history', async () => {
    stub({}, RULES_VERSIONED);
    await open();
    expect(version(NO_SHOW_UPCOMING.id)?.dataset.versionStatus).toBe(
      'upcoming',
    );
    expect(version(NO_SHOW_SEED.id)).toBeNull();
    const toggle = buttonNamed(block('no_show'), 'Show 1 earlier version');
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
    await click(toggle);
    expect(version(NO_SHOW_SEED.id)?.dataset.versionStatus).toBe('past');
    expect(version(NO_SHOW_SEED.id)?.textContent).toContain(
      'Pays 100% of the class',
    );
    expect(
      buttonNamed(block('no_show'), 'Hide earlier versions').getAttribute(
        'aria-expanded',
      ),
    ).toBe('true');
  });

  it('says that a rule goes by the session date and what a late "none" means', async () => {
    stub();
    await open();
    expect(main().textContent).toContain(
      "A rule applies by the session's date and never changes past work.",
    );
    expect(
      block('late_cancellation').querySelector('[data-pay-rule-late-note]')
        ?.textContent,
    ).toContain('the tutor earns nothing for that slot');
    expect(
      block('late_cancellation').querySelector('[data-pay-rule-late-note]')
        ?.textContent,
    ).toContain("pending the owner's review");
    expect(
      block('makeup').querySelector('[data-pay-rule-late-note]'),
    ).toBeNull();
  });

  it('is read-only without payroll.manage', async () => {
    stub({}, RULES_VERSIONED, VIEWER);
    await open();
    await click(buttonNamed(block('no_show'), 'Show 1 earlier version'));
    const names = [...main().querySelectorAll('button')].map(
      (button) => button.textContent,
    );
    expect(names).toEqual(['Hide earlier versions']);
  });

  it('shows an error with a retry, then the rules', async () => {
    const api = stub({
      [RULES]: [
        () => problem(400, 'bad_request'),
        () => jsonResponse(200, RULES_SEEDED),
      ],
    });
    app = await renderApp('/payroll/pay-rules');
    await settleUntil(() => alerts().length > 0);
    await click(buttonNamed(main(), 'Try again'));
    await settleUntil(() => main().querySelector('[data-pay-rule-event]'));
    expect(api.calls(RULES)).toHaveLength(2);
  });
});

describe('the new version dialog (B2, B3, B5)', () => {
  it('uses the center today as the date min and prefills the latest', async () => {
    stub({}, RULES_VERSIONED);
    await openDialogFor('makeup');
    expect(date().min).toBe(TODAY);
    expect(date().value).toBe('');
    expect(mode().value).toBe('percent');
    expect(percent().value).toBe('100');
  });

  it('sends a percent with based_on_id and every field, null written out', async () => {
    const api = stub({ [POST]: () => jsonResponse(201, MAKEUP_SEED) });
    await openDialogFor('makeup');
    setFieldValue(percent(), '12.5');
    setFieldValue(date(), '2026-11-01');
    await click(save());
    await settleUntil(() => !openDialog());
    expect(api.calls(POST)[0]?.body).toEqual({
      based_on_id: MAKEUP_SEED.id,
      event: 'makeup',
      effective_from: '2026-11-01',
      mode: 'percent',
      percent: '12.5',
      amount: null,
    });
    expect(main().textContent).toContain('Version published.');
  });

  it('sends an amount as the typed string, and is based on the last upcoming', async () => {
    const api = stub(
      { [POST]: () => jsonResponse(201, NO_SHOW_LATER) },
      RULES_VERSIONED,
    );
    await openDialogFor('no_show');
    setFieldValue(mode(), 'fixed');
    setFieldValue(amount(), '150.10');
    setFieldValue(date(), '2027-02-01');
    await click(save());
    await settleUntil(() => !openDialog());
    expect(api.calls(POST)[0]?.body).toEqual({
      based_on_id: NO_SHOW_LATER.id,
      event: 'no_show',
      effective_from: '2027-02-01',
      mode: 'fixed',
      percent: null,
      amount: '150.10',
    });
  });

  it('sends "none" with both values null', async () => {
    const api = stub({ [POST]: () => jsonResponse(201, MAKEUP_SEED) });
    await openDialogFor('makeup');
    setFieldValue(mode(), 'none');
    setFieldValue(date(), TODAY);
    await click(save());
    await settleUntil(() => !openDialog());
    expect(api.calls(POST)[0]?.body).toMatchObject({
      mode: 'none',
      percent: null,
      amount: null,
    });
  });

  it('stops a form that would surely fail, with the focus on the field', async () => {
    const api = stub();
    await openDialogFor('makeup');
    setFieldValue(percent(), '1000.5');
    await click(save());
    await settle();
    expect(dialog().textContent).toContain('Choose a percent from 0 to 1000.');
    expect(dialog().textContent).toContain('Choose a date.');
    expect(document.activeElement).toBe(percent());
    expect(api.calls(POST)).toHaveLength(0);
  });

  it.each([
    [
      'percent_out_of_range',
      'percent',
      'Choose a percent from 0 to 1000.',
      'makeup',
    ],
    [
      'rate_precision',
      'percent',
      'A percentage has at most 4 decimals.',
      'makeup',
    ],
    [
      'pay_rule_values_mismatch',
      'percent',
      "This value doesn't match what the rule pays.",
      'makeup',
    ],
  ])('%s lands on the field', async (code, field, text, event) => {
    stub({
      [POST]: () =>
        problemWith(422, 'validation_error', {
          errors: [{ path: ['body', field], code }],
        }),
    });
    await openDialogFor(event);
    setFieldValue(date(), '2026-11-01');
    await click(save());
    await settleUntil(() => dialog().textContent?.includes(text));
    expect(percent().getAttribute('aria-invalid')).toBe('true');
    expect(percent().value).toBe('100');
    expect(date().value).toBe('2026-11-01');
  });

  it('never shows the coupon text for percent_out_of_range', async () => {
    stub({
      [POST]: () =>
        problemWith(422, 'validation_error', {
          errors: [{ path: ['body', 'percent'], code: 'percent_out_of_range' }],
        }),
    });
    await openDialogFor('makeup');
    setFieldValue(date(), '2026-11-01');
    await click(save());
    await settleUntil(() => dialog().textContent?.includes('0 to 1000'));
    expect(dialog().textContent).not.toContain('one hundred');
  });

  it('puts money_precision on the amount', async () => {
    stub({
      [POST]: () =>
        problemWith(422, 'validation_error', {
          errors: [{ path: ['body', 'amount'], code: 'money_precision' }],
        }),
    });
    await openDialogFor('makeup');
    setFieldValue(mode(), 'fixed');
    setFieldValue(amount(), '10.50');
    setFieldValue(date(), '2026-11-01');
    await click(save());
    await settleUntil(() =>
      dialog().textContent?.includes('An amount has at most 2 decimal places.'),
    );
  });

  it('puts invalid_effective_from on the date and keeps what was typed', async () => {
    stub({ [POST]: () => problem(422, 'invalid_effective_from') });
    await openDialogFor('makeup');
    setFieldValue(percent(), '50');
    setFieldValue(date(), '2026-11-01');
    await click(save());
    await settleUntil(() =>
      dialog().textContent?.includes('after every version of this event'),
    );
    expect(date().getAttribute('aria-invalid')).toBe('true');
    expect(percent().value).toBe('50');
  });

  it('shows an alert when the refusal is about a field the form hides', async () => {
    stub({
      [POST]: () =>
        problemWith(422, 'validation_error', {
          errors: [
            { path: ['body', 'percent'], code: 'pay_rule_values_mismatch' },
          ],
        }),
    });
    await openDialogFor('makeup');
    setFieldValue(mode(), 'none');
    setFieldValue(date(), '2026-11-01');
    await click(save());
    await settleUntil(() => alerts(dialog()).length > 0);
    expect(openDialog()).not.toBeNull();
  });

  describe.each([
    ['en', 'What it pays', 'Publish version', 'Someone else changed this rule'],
    ['es', 'Qué paga', 'Publicar versión', 'Alguien más cambió esta regla'],
  ])('version_conflict (%s)', (language, modeLabel, saveLabel, notice) => {
    const newer: PayRuleOverview = {
      ...RULES_SEEDED,
      makeup: { ...RULES_SEEDED.makeup, upcoming: [NO_SHOW_UPCOMING_MAKEUP()] },
    };

    it('reloads, shows the server values, and sends nothing by itself', async () => {
      applyLanguage(language as 'en' | 'es');
      const api = stub({
        [RULES]: [
          () => jsonResponse(200, RULES_SEEDED),
          () => jsonResponse(200, newer),
        ],
        [POST]: [
          () => problem(409, 'version_conflict'),
          () => jsonResponse(201, MAKEUP_SEED),
        ],
      });
      await open();
      await click(newVersion('makeup'));
      await settleUntil(() => openDialog());
      const modeField = () => labelled<HTMLSelectElement>(dialog(), modeLabel);
      const dateField = () =>
        dialog().querySelector<HTMLInputElement>('input[type="date"]')!;
      setFieldValue(modeField(), 'none');
      setFieldValue(dateField(), '2026-11-01');
      await click(buttonNamed(dialog(), saveLabel));
      await settleUntil(() =>
        dialog().querySelector('[data-pay-rule-reloaded]'),
      );

      const alert = dialog().querySelector('[data-pay-rule-reloaded]')!;
      expect(alert.textContent).toContain(notice);
      expect(alert.getAttribute('role')).toBe('alert');
      expect(modeField().value).toBe('fixed');
      expect(dateField().value).toBe('');
      expect(api.calls(POST)).toHaveLength(1);

      setFieldValue(dateField(), '2027-03-01');
      await click(buttonNamed(dialog(), saveLabel));
      await settleUntil(() => !openDialog());
      expect(api.calls(POST)[1]?.body).toEqual({
        based_on_id: newer.makeup.upcoming[0]!.id,
        event: 'makeup',
        effective_from: '2027-03-01',
        mode: 'fixed',
        percent: null,
        amount: '150.00',
      });
    });
  });

  // QA #425 D3: the refetch lands while the POST is pending, then the POST
  // fails. Ordered by hand: nothing here depends on timing.
  describe.each([
    ['409', () => problem(409, 'version_conflict')],
    ['500', () => problem(500, 'internal_error')],
  ])('a refetch during a save that ends in %s', (_name, refusal) => {
    it('keeps the snapshot during the save, then resets and asks again', async () => {
      const newer: PayRuleOverview = {
        ...RULES_SEEDED,
        makeup: {
          ...RULES_SEEDED.makeup,
          upcoming: [NO_SHOW_UPCOMING_MAKEUP()],
        },
      };
      let release: (response: Response) => void = () => {};
      const api = stub({
        [RULES]: [
          () => jsonResponse(200, RULES_SEEDED),
          () => jsonResponse(200, newer),
        ],
        [POST]: () =>
          new Promise<Response>((resolve) => {
            release = resolve;
          }),
      });
      await openDialogFor('makeup');
      setFieldValue(percent(), '75');
      setFieldValue(date(), '2026-11-01');
      await click(save());
      await settleUntil(() => api.calls(POST).length === 1);

      await app!.queryClient.refetchQueries({
        queryKey: ['payroll', 'pay-rules'],
      });
      await settle();
      expect(percent().value).toBe('75');
      expect(dialog().querySelector('[data-pay-rule-reloaded]')).toBeNull();

      release(refusal());
      await settleUntil(() =>
        dialog().querySelector('[data-pay-rule-reloaded]'),
      );
      expect(mode().value).toBe('fixed');
      expect(date().value).toBe('');
      expect(api.calls(POST)).toHaveLength(1);
      expect(alerts(dialog())).toHaveLength(1);
    });
  });

  it('a failed reload keeps the conflict with Reload, then recovers', async () => {
    const newer: PayRuleOverview = {
      ...RULES_SEEDED,
      makeup: { ...RULES_SEEDED.makeup, upcoming: [NO_SHOW_UPCOMING_MAKEUP()] },
    };
    stub({
      [RULES]: [
        () => jsonResponse(200, RULES_SEEDED),
        () => problem(400, 'bad_request'),
        () => jsonResponse(200, newer),
      ],
      [POST]: () => problem(409, 'version_conflict'),
    });
    await openDialogFor('makeup');
    setFieldValue(date(), '2026-11-01');
    await click(save());
    await settleUntil(() => {
      const retry = [...dialog().querySelectorAll('button')].find(
        (button) => button.textContent === 'Reload',
      );
      return retry && !retry.disabled;
    });
    await click(buttonNamed(dialog(), 'Reload'));
    await settleUntil(() => dialog().querySelector('[data-pay-rule-reloaded]'));
    expect(mode().value).toBe('fixed');
  });

  it('a refused client check keeps the reload notice', async () => {
    const newer: PayRuleOverview = {
      ...RULES_SEEDED,
      makeup: { ...RULES_SEEDED.makeup, upcoming: [NO_SHOW_UPCOMING_MAKEUP()] },
    };
    stub({
      [RULES]: [
        () => jsonResponse(200, RULES_SEEDED),
        () => jsonResponse(200, newer),
      ],
      [POST]: () => problem(409, 'version_conflict'),
    });
    await openDialogFor('makeup');
    setFieldValue(date(), '2026-11-01');
    await click(save());
    await settleUntil(() => dialog().querySelector('[data-pay-rule-reloaded]'));
    await click(save());
    await settle();
    expect(dialog().querySelector('[data-pay-rule-reloaded]')).not.toBeNull();
    expect(dialog().textContent).toContain('Choose a date.');
  });
});

/** A newer makeup version, as a colleague would publish it. */
function NO_SHOW_UPCOMING_MAKEUP() {
  return {
    ...NO_SHOW_UPCOMING,
    id: '73000000-0000-4000-8000-000000000009',
    event: 'makeup' as const,
  };
}

describe('delete (B2, B3)', () => {
  const deleteUpcoming = () =>
    version(NO_SHOW_UPCOMING.id)!.querySelector('button')!;

  it('asks, deletes and refreshes', async () => {
    const api = stub({
      [RULES]: [
        () => jsonResponse(200, RULES_VERSIONED),
        () => jsonResponse(200, RULES_SEEDED),
      ],
      [del(NO_SHOW_UPCOMING.id)]: noContent,
    });
    await open();
    await click(deleteUpcoming());
    await settleUntil(() => openDialog());
    expect(dialog().textContent).toContain('Pays $150.00');
    await click(buttonNamed(dialog(), 'Delete'));
    await settleUntil(() => !openDialog());
    expect(
      api.calls(`DELETE /api/v1/payroll/pay-rules/${NO_SHOW_UPCOMING.id}`),
    ).toHaveLength(1);
    expect(version(NO_SHOW_UPCOMING.id)).toBeNull();
    expect(main().textContent).toContain('Version deleted.');
  });

  it('offers delete only on an upcoming version', async () => {
    stub({}, RULES_VERSIONED);
    await open();
    expect(
      version(RULES_VERSIONED.no_show.current.id)!.querySelector('button'),
    ).toBeNull();
  });

  it.each([
    ['pay_rule_in_effect', 'This rule is in force or in the past'],
    ['session_pay_rule_in_use', 'A payroll line keeps this version'],
  ])('%s says why in the block and refreshes', async (code, text) => {
    const api = stub(
      { [del(NO_SHOW_UPCOMING.id)]: () => problem(409, code) },
      RULES_VERSIONED,
    );
    await open();
    await click(deleteUpcoming());
    await settleUntil(() => openDialog());
    await click(buttonNamed(dialog(), 'Delete'));
    await settleUntil(() => !openDialog());
    await settleUntil(() => block('no_show').textContent?.includes(text));
    expect(block('makeup').textContent).not.toContain(text);
    expect(api.calls(RULES).length).toBeGreaterThan(1);
  });

  it('a vanished version refreshes and says so', async () => {
    stub(
      { [del(NO_SHOW_UPCOMING.id)]: () => problem(404, 'not_found') },
      RULES_VERSIONED,
    );
    await open();
    await click(deleteUpcoming());
    await settleUntil(() => openDialog());
    await click(buttonNamed(dialog(), 'Delete'));
    await settleUntil(() => !openDialog());
    await settleUntil(() =>
      main().textContent?.includes('That version no longer exists.'),
    );
  });
});

describe('payRuleForm', () => {
  const today = parseLocalDate(TODAY);

  it.each([
    ['100.0000', '100'],
    ['12.5000', '12.5'],
    ['0.0000', '0'],
    ['1000.0000', '1000'],
    ['0.0100', '0.01'],
    ['100', '100'],
    ['20.0500', '20.05'],
  ])('percentText(%s) is %s', (value, expected) => {
    expect(percentText(value)).toBe(expected);
  });

  it.each([
    ['0', true],
    ['999.9999', true],
    ['1000', true],
    ['1000.0000', true],
    ['0001000', true],
    ['1000.0001', false],
    ['1001', false],
    ['10000', false],
  ])('percent %s in range: %s', (value, ok) => {
    const errors = validatePayRuleForm(
      { mode: 'percent', percent: value, amount: '', effectiveFrom: TODAY },
      today,
    );
    expect(errors.percent === undefined).toBe(ok);
  });

  it('accepts today, refuses yesterday', () => {
    const base = { mode: 'none', percent: '', amount: '' } as const;
    expect(
      validatePayRuleForm({ ...base, effectiveFrom: TODAY }, today),
    ).toEqual({});
    expect(
      validatePayRuleForm({ ...base, effectiveFrom: '2026-10-19' }, today)
        .effectiveFrom,
    ).toBe('payroll.payRules.validation.datePast');
  });

  it('checks only the value of the chosen mode', () => {
    const values = {
      mode: 'none',
      percent: 'x',
      amount: 'y',
      effectiveFrom: TODAY,
    } as const;
    expect(validatePayRuleForm(values, today)).toEqual({});
    expect(payRuleCreateBody(values, LATE_SEED)).toMatchObject({
      percent: null,
      amount: null,
    });
  });

  it('prefills from the latest version', () => {
    expect(latestPayRule(RULES_VERSIONED.no_show)).toBe(NO_SHOW_LATER);
    expect(latestPayRule(RULES_SEEDED.no_show)).toBe(NO_SHOW_SEED);
    expect(initialPayRuleForm(NO_SHOW_UPCOMING)).toEqual({
      mode: 'fixed',
      percent: '',
      amount: '150.00',
      effectiveFrom: '',
    });
  });

  it('leaves errors it cannot place to the caller', () => {
    expect(payRuleServerErrors(new Error('x'), 'none')).toBeNull();
  });
});
