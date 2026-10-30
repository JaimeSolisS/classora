import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { problemWith } from '@/features/academics/testing';
import type { SessionUserBody } from '@/features/auth/session';
import { applyLanguage } from '@/i18n';
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
  settleUntil,
  type RenderedApp,
} from '@/test/render-app';
import type { CancellationRuleOverview } from './api';
import {
  CURRENT_RULE,
  RULES_SEEDED,
  RULES_WITH_UPCOMING,
  SEEDED_RULE,
  SETTINGS_OFF,
  UPCOMING_RULE,
} from './fixtures';

// #385 D1: the cancellation charges card on Billing → Settings: the rule in
// force (the excused row always credited, when each outcome began), the
// upcoming versions with and without billing.manage, the history toggle,
// "Schedule a change" (prefill from the latest version, excused sent
// unchanged, an empty date omitted, a date at 00:00 in the center's zone,
// success, 409 with Reload, 422 on the date and on a select), Delete
// (success, 409 already in force, 404), and loading, error and retry.

const RULES = 'GET /api/v1/billing/cancellation-rules';
const POST = 'POST /api/v1/billing/cancellation-rules';
const DELETE = `DELETE /api/v1/billing/cancellation-rules/${UPCOMING_RULE.id}`;
const ME = 'GET /api/v1/auth/me';

const user = (
  permissions: SessionUserBody['permissions'],
): SessionUserBody => ({ ...TEST_USER, roles: ['finance_staff'], permissions });

const FINANCE = user([
  'student.view',
  'guardian.view',
  'billing.view',
  'billing.manage',
]);
const VIEWER = user(['billing.view']);

function stub(
  routes: Record<string, Responder | Responder[]> = {},
  {
    rules = RULES_WITH_UPCOMING,
    as = FINANCE,
  }: { rules?: CancellationRuleOverview; as?: SessionUserBody } = {},
): ApiStub {
  return stubApi({
    [ME]: meAs(as),
    'GET /api/v1/billing/settings': () => jsonResponse(200, SETTINGS_OFF),
    [RULES]: () => jsonResponse(200, rules),
    ...routes,
  });
}

let app: RenderedApp | undefined;

const main = () => app!.container.querySelector('main')!;
const card = () => {
  const heading = [...main().querySelectorAll('h2')].find(
    (h) => h.textContent === 'Cancellation charges',
  );
  return heading!.closest('section')!;
};
const dialog = () => openDialog()!;
/** The in-force table's rows: [classification, outcome, since]. */
const inForce = () =>
  [...card().querySelectorAll('[data-cancellation-class]')].map((row) =>
    [...row.children].map((cell) => cell.textContent),
  );
const upcoming = () =>
  [...card().querySelectorAll('li[data-rule-id]')].map((li) => li.textContent);
const announced = () =>
  [...main().querySelectorAll('[role="status"]')]
    .map((s) => s.textContent)
    .join('|');
const select = (label: string) => labelled<HTMLSelectElement>(dialog(), label);
const dateInput = () => labelled<HTMLInputElement>(dialog(), 'Takes effect on');

async function renderCard() {
  app = await renderApp('/billing/settings');
  await settleUntil(
    () =>
      main().querySelector('[data-cancellation-class]') ??
      alerts(main()).length > 0,
  );
}

async function openSchedule() {
  await click(buttonNamed(card(), 'Schedule a change'));
  await settleUntil(() => openDialog()?.querySelector('form'));
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

// --- A: the card ------------------------------------------------------------

describe('the rule in force (A1, A2)', () => {
  it('shows the seeded rule, the fixed facts and no upcoming change', async () => {
    stub({}, { rules: RULES_SEEDED });
    await renderCard();

    expect(inForce()).toEqual([
      ['On time', 'Credited', 'Jan 1, 2000'],
      ['Late', 'Charged', 'Jan 1, 2000'],
      ['No-show', 'Charged', 'Jan 1, 2000'],
      [
        'Excused (center or tutor)',
        'Always credited (makeup allowed)',
        'Jan 1, 2000',
      ],
    ]);
    const text = card().textContent;
    expect(text).toContain(
      'A cancellation that allows a makeup (see the scheduling policies) is always credited, whatever the rule below.',
    );
    expect(text).toContain('A center closure always credits the class.');
    expect(text).toContain('A rescheduled class keeps its charge.');
    expect(text).toContain('No change is scheduled.');
    expect(text).toContain('No earlier version.');
  });

  it('dates each outcome from when it began, and the excused row is always credited', async () => {
    stub(
      {},
      {
        rules: {
          ...RULES_WITH_UPCOMING,
          current: { ...CURRENT_RULE, excused_outcome: 'charge' },
        },
      },
    );
    await renderCard();

    expect(inForce()).toEqual([
      ['On time', 'Credited', 'Jan 1, 2000'],
      ['Late', 'Charged', 'Jan 1, 2000'],
      ['No-show', 'Credited', 'Sep 1, 2026'],
      // The stored value is inert (#99 Q6): it always reads credited.
      [
        'Excused (center or tutor)',
        'Always credited (makeup allowed)',
        'Jan 1, 2000',
      ],
    ]);
  });
});

describe('upcoming versions (A3, A5)', () => {
  it('lists each with its date and four outcomes, and Delete for billing.manage', async () => {
    stub();
    await renderCard();

    expect(upcoming()).toEqual([
      'Takes effect Oct 21, 2036, 12:00 AM' +
        'On time: CreditedLate: CreditedNo-show: Credited' +
        'Excused (center or tutor): Always credited (makeup allowed)' +
        'Delete',
    ]);
    expect(
      buttonNamed(card(), 'Delete the change from Oct 21, 2036, 12:00 AM'),
    ).toBeTruthy();
    expect(buttonNamed(card(), 'Schedule a change')).toBeTruthy();
  });

  it('is read-only without billing.manage', async () => {
    const api = stub({}, { as: VIEWER });
    await renderCard();

    expect(upcoming()[0]).toContain('Takes effect Oct 21, 2036, 12:00 AM');
    expect(card().querySelectorAll('button[aria-expanded]')).toHaveLength(1);
    expect(
      [...card().querySelectorAll('button')].map((b) => b.textContent),
    ).toEqual(['Show earlier versions (1)']);
    expect(api.calls(POST)).toHaveLength(0);
  });
});

describe('history (A4)', () => {
  it('opens and closes the earlier versions', async () => {
    stub();
    await renderCard();

    const toggle = buttonNamed(card(), 'Show earlier versions (1)');
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
    expect(card().textContent).not.toContain('From Jan 1, 2000');

    await click(toggle);
    expect(toggle.getAttribute('aria-expanded')).toBe('true');
    expect(toggle.textContent).toBe('Hide earlier versions');
    const list = document.getElementById(toggle.getAttribute('aria-controls')!);
    expect(list?.textContent).toContain(
      'From Jan 1, 2000, 12:00 AM to Sep 1, 2026, 12:00 AM',
    );
    expect(list?.textContent).toContain('No-show: Charged');

    await click(toggle);
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
    expect(card().textContent).not.toContain('From Jan 1, 2000');
  });
});

describe('loading and errors', () => {
  it('shows a loading state while the rules load', async () => {
    stub({ [RULES]: () => new Promise<Response>(() => {}) });
    app = await renderApp('/billing/settings');
    await settleUntil(() => main().querySelector('[data-billing-status]'));

    expect(card().querySelector('[aria-busy="true"]')).not.toBeNull();
    expect(() => buttonNamed(card(), 'Schedule a change')).toThrow();
  });

  it('shows a failed load with Try again, which reloads', async () => {
    const api = stub({
      [RULES]: [
        () => problem(400, 'bad_request'),
        () => jsonResponse(200, RULES_SEEDED),
      ],
    });
    await renderCard();

    expect(alerts(card())).toHaveLength(1);
    await click(buttonNamed(card(), 'Try again'));
    await settleUntil(() => card().querySelector('[data-cancellation-class]'));

    expect(inForce()[1]).toEqual(['Late', 'Charged', 'Jan 1, 2000']);
    expect(api.calls(RULES)).toHaveLength(2);
  });
});

// --- B: schedule a change ---------------------------------------------------

describe('schedule a change (B1, B2, B4)', () => {
  it('prefills from the latest version and sends now (no date), excused unchanged', async () => {
    const latest = { ...UPCOMING_RULE, excused_outcome: 'charge' as const };
    const api = stub(
      { [POST]: () => jsonResponse(201, { ...latest, id: 'new' }) },
      { rules: { ...RULES_WITH_UPCOMING, upcoming: [latest] } },
    );
    await renderCard();
    await openSchedule();

    expect(dialog().textContent).toContain(
      'A change applies to cancellations requested from its date on; earlier ones keep the rule they were made under.',
    );
    expect(select('On time').value).toBe('credit');
    expect(select('Late').value).toBe('credit');
    expect(select('No-show').value).toBe('credit');
    expect(() => select('Excused (center or tutor)')).toThrow();
    expect(dateInput().value).toBe('');
    expect(dateInput().min).toBe('2026-10-21');

    setFieldValue(select('No-show'), 'charge');
    await click(buttonNamed(dialog(), 'Save'));
    await settleUntil(() => !openDialog());

    expect(api.calls(POST)[0]!.body).toEqual({
      based_on_id: UPCOMING_RULE.id,
      excused_outcome: 'charge',
      on_time_outcome: 'credit',
      late_outcome: 'credit',
      no_show_outcome: 'charge',
    });
    expect(announced()).toContain('Cancellation charges change scheduled.');
    await settleUntil(() => api.calls(RULES).length === 2);
  });

  it('prefills from the rule in force without upcoming, and sends a date at 00:00 in the center zone', async () => {
    const api = stub(
      { [POST]: () => jsonResponse(201, { ...SEEDED_RULE, id: 'new' }) },
      { rules: RULES_SEEDED },
    );
    await renderCard();
    await openSchedule();

    expect(select('Late').value).toBe('charge');
    setFieldValue(select('Late'), 'credit');
    setFieldValue(dateInput(), '2036-10-20');
    await click(buttonNamed(dialog(), 'Save'));
    await settleUntil(() => !openDialog());

    expect(api.calls(POST)[0]!.body).toEqual({
      based_on_id: SEEDED_RULE.id,
      excused_outcome: 'credit',
      on_time_outcome: 'credit',
      late_outcome: 'credit',
      no_show_outcome: 'charge',
      effective_from: '2036-10-20T06:00:00.000Z',
    });
  });

  it('refuses today as the date before sending', async () => {
    const api = stub({}, { rules: RULES_SEEDED });
    await renderCard();
    await openSchedule();

    setFieldValue(dateInput(), '2026-10-20');
    await click(buttonNamed(dialog(), 'Save'));

    expect(dialog().textContent).toContain('Choose a date after today.');
    expect(dateInput().getAttribute('aria-invalid')).toBe('true');
    expect(document.activeElement).toBe(dateInput());
    expect(api.calls(POST)).toHaveLength(0);
  });

  it('puts a 422 invalid_effective_from on the date and keeps the values', async () => {
    stub({ [POST]: () => problem(422, 'invalid_effective_from') });
    await renderCard();
    await openSchedule();

    setFieldValue(select('Late'), 'charge');
    await click(buttonNamed(dialog(), 'Save'));
    await settleUntil(() => dateInput().getAttribute('aria-invalid'));

    expect(dialog().textContent).toContain(
      'A new policy must take effect now or later, and after every upcoming change. Choose a later date.',
    );
    expect(dateInput().getAttribute('aria-describedby')).toContain('error');
    expect(select('Late').value).toBe('charge');
    expect(alerts(dialog())).toEqual([]);
  });

  it('puts another 422 field error on its select', async () => {
    stub({
      [POST]: () =>
        problemWith(422, 'validation_error', {
          errors: [
            { path: ['body', 'late_outcome'], code: 'validation_error' },
          ],
        }),
    });
    await renderCard();
    await openSchedule();

    await click(buttonNamed(dialog(), 'Save'));
    await settleUntil(() => select('Late').getAttribute('aria-invalid'));

    expect(select('Late').getAttribute('aria-describedby')).toBeTruthy();
    expect(dateInput().getAttribute('aria-invalid')).toBeNull();
  });

  it('shows a 409 with Reload; the next save is based on the new latest', async () => {
    const newer = {
      ...UPCOMING_RULE,
      id: '00000000-0000-4000-8000-0000000000c4',
    };
    const api = stub({
      [RULES]: [
        () => jsonResponse(200, RULES_WITH_UPCOMING),
        () =>
          jsonResponse(200, {
            ...RULES_WITH_UPCOMING,
            upcoming: [UPCOMING_RULE, newer],
          }),
      ],
      [POST]: [
        () => problem(409, 'version_conflict'),
        () => jsonResponse(201, { ...newer, id: 'new' }),
      ],
    });
    await renderCard();
    await openSchedule();

    setFieldValue(select('On time'), 'charge');
    await click(buttonNamed(dialog(), 'Save'));
    await settleUntil(() => alerts(dialog()).length > 0);

    expect(alerts(dialog()).join('')).toContain(
      'Someone else changed this record. Reload it and try again.',
    );
    expect(select('On time').value).toBe('charge');
    await click(buttonNamed(dialog(), 'Reload'));
    await settleUntil(() => api.calls(RULES).length === 2);
    expect(alerts(dialog())).toEqual([]);
    await click(buttonNamed(dialog(), 'Save'));
    await settleUntil(() => !openDialog());

    expect(api.calls(POST)[1]!.body).toMatchObject({
      based_on_id: newer.id,
      on_time_outcome: 'charge',
    });
  });

  it('Cancel closes without sending', async () => {
    const api = stub();
    await renderCard();
    await openSchedule();

    await click(buttonNamed(dialog(), 'Cancel'));

    expect(openDialog()).toBeNull();
    expect(api.calls(POST)).toHaveLength(0);
  });
});

// --- B3: delete an upcoming version -----------------------------------------

describe('delete an upcoming version (B3)', () => {
  async function deleteUpcoming() {
    await click(
      buttonNamed(card(), 'Delete the change from Oct 21, 2036, 12:00 AM'),
    );
    await settleUntil(() => openDialog());
    expect(dialog().textContent).toContain(
      'The change from Oct 21, 2036, 12:00 AM will not take effect.',
    );
    await click(buttonNamed(dialog(), 'Delete'));
    await settleUntil(() => !openDialog());
  }

  it('deletes after the confirmation, announces it and refreshes', async () => {
    const api = stub({
      [RULES]: [
        () => jsonResponse(200, RULES_WITH_UPCOMING),
        () => jsonResponse(200, { ...RULES_WITH_UPCOMING, upcoming: [] }),
      ],
      [DELETE]: () => noContent(),
    });
    await renderCard();
    await deleteUpcoming();
    await settleUntil(() => upcoming().length === 0);

    expect(api.calls(DELETE)).toHaveLength(1);
    expect(announced()).toContain('Scheduled change deleted.');
    expect(card().textContent).toContain('No change is scheduled.');
  });

  it('says a version already in force is, and refreshes', async () => {
    const api = stub({
      [RULES]: [
        () => jsonResponse(200, RULES_WITH_UPCOMING),
        () =>
          jsonResponse(200, {
            current: UPCOMING_RULE,
            upcoming: [],
            history: [CURRENT_RULE, SEEDED_RULE],
          }),
      ],
      [DELETE]: () => problem(409, 'policy_version_in_effect'),
    });
    await renderCard();
    await deleteUpcoming();
    await settleUntil(() => api.calls(RULES).length === 2);

    expect(alerts(card())).toEqual(['This version is already in force.']);
    expect(announced()).not.toContain('Scheduled change deleted.');
    expect(inForce()[1]).toEqual(['Late', 'Credited', 'Oct 21, 2036']);
  });

  it('refreshes silently when the version is already gone (404)', async () => {
    const api = stub({
      [RULES]: [
        () => jsonResponse(200, RULES_WITH_UPCOMING),
        () => jsonResponse(200, { ...RULES_WITH_UPCOMING, upcoming: [] }),
      ],
      [DELETE]: () => problem(404, 'not_found'),
    });
    await renderCard();
    await deleteUpcoming();
    await settleUntil(() => api.calls(RULES).length === 2);

    expect(alerts(card())).toEqual([]);
    expect(upcoming()).toEqual([]);
  });
});

// --- C: Spanish -------------------------------------------------------------

describe('Spanish (C1)', () => {
  it('names the card and the rule in Spanish', async () => {
    applyLanguage('es');
    stub({}, { rules: RULES_SEEDED });
    app = await renderApp('/billing/settings');
    await settleUntil(() => main().querySelector('[data-cancellation-class]'));

    const heading = [...main().querySelectorAll('h2')].map(
      (h) => h.textContent,
    );
    expect(heading).toContain('Cobro de cancelaciones');
    const rows = [...main().querySelectorAll('[data-cancellation-class]')].map(
      (row) => [...row.children].slice(0, 2).map((cell) => cell.textContent),
    );
    expect(rows).toEqual([
      ['A tiempo', 'Se acredita'],
      ['Tardía', 'Se cobra'],
      ['Inasistencia', 'Se cobra'],
      ['Justificada (centro o tutor)', 'Siempre se acredita (permite reponer)'],
    ]);
  });
});
