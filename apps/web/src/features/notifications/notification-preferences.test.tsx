import { act } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { applyLanguage, i18n } from '@/i18n';
import type { SessionUserBody } from '@/features/auth/session';
import {
  meAs,
  problem,
  stubApi,
  TEST_USER,
  type ApiStub,
  type Responder,
} from '@/test/api-stub';
import { alerts, click } from '@/test/dom';
import {
  buttonNamed,
  jsonResponse,
  renderApp,
  settleUntil,
  type RenderedApp,
} from '@/test/render-app';
import type { NotificationPreferenceItem } from './preferences-api';
import { GUARDIAN_CATALOGUE, OWNER_CATALOGUE } from './preferenceFixtures';
import {
  NOTIFICATION_PREFERENCE_TYPES,
  preferenceLabelKey,
} from './preferenceTypes';

// M8-02b E1: /account/notifications in the real App with a stubbed fetch:
// grouping and order, saving a toggle (E2), a failed save, mandatory types,
// a changed catalogue (B5), the unknown-type label, the user-menu and panel
// links, the privacy-notice gate (A3) and the D1 type pin.

const GET = 'GET /api/v1/notifications/preferences';
const PATCH = 'PATCH /api/v1/notifications/preferences';
const PATH = '/account/notifications';

const catalogue = (items: NotificationPreferenceItem[]) => () =>
  jsonResponse(200, { items });

/** The catalogue after one change, as the server answers it. */
function withChange(
  items: NotificationPreferenceItem[],
  type: string,
  enabled: boolean,
): NotificationPreferenceItem[] {
  return items.map((item) =>
    item.notification_type === type ? { ...item, enabled } : item,
  );
}

/** Answers a PATCH with `items` changed as asked. */
const applies =
  (items: NotificationPreferenceItem[]): Responder =>
  (_request, body) => {
    const [change] = (
      body as { items: { notification_type: string; enabled: boolean }[] }
    ).items;
    return jsonResponse(200, {
      items: withChange(items, change!.notification_type, change!.enabled),
    });
  };

let app: RenderedApp | undefined;

function stub(
  routes: Record<string, Responder | Responder[]> = {},
  user: SessionUserBody = TEST_USER,
): ApiStub {
  return stubApi({
    'GET /api/v1/auth/me': meAs(user),
    'GET /api/v1/notifications/unread-count': () =>
      jsonResponse(200, { count: 0 }),
    'GET /api/v1/notifications': () =>
      jsonResponse(200, { items: [], next_cursor: null }),
    [GET]: catalogue(GUARDIAN_CATALOGUE),
    ...routes,
  });
}

const main = () => app!.container.querySelector('main')!;
const headings = () =>
  [...main().querySelectorAll('h2')].map((h) => h.textContent);
const groupLabels = (heading: string) => {
  const section = [...main().querySelectorAll('section')].find(
    (candidate) => candidate.querySelector('h2')?.textContent === heading,
  );
  return [...(section?.querySelectorAll('li label') ?? [])].map(
    (label) => label.textContent,
  );
};
const checkbox = (type: string) =>
  main().querySelector<HTMLButtonElement>(
    `li[data-type="${type}"] [role="checkbox"]`,
  )!;
const status = () =>
  (main().querySelector('[role="status"]')?.textContent ?? '').trim();
const checked = (type: string) =>
  checkbox(type).getAttribute('aria-checked') === 'true';

async function openPage(path = PATH) {
  app = await renderApp(path);
  await settleUntil(() => main().querySelector('[role="checkbox"]'));
}

beforeEach(() => {
  applyLanguage('en');
  vi.spyOn(console, 'error').mockImplementation(() => {});
});

afterEach(() => {
  app?.unmount();
  app = undefined;
  applyLanguage('en');
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('the page (A1, B1, B2)', () => {
  it('groups the catalogue in the fixed order, sorted by label', async () => {
    stub();
    await openPage();

    expect(main().querySelector('h1')?.textContent).toBe(
      'Notification preferences',
    );
    expect(document.title).toBe('Notification preferences · Classora');
    expect(headings()).toEqual(['Schedule', 'Academics', 'Billing']);
    const schedule = groupLabels('Schedule');
    expect(schedule).toEqual(
      [...schedule].sort((a, b) => a.localeCompare(b, 'en')),
    );
    expect(schedule[0]).toBe('Change request approved');
    expect(groupLabels('Billing')).toEqual(['Payment reminders']);
  });

  it('sorts by the Spanish labels in Spanish', async () => {
    applyLanguage('es');
    stub();
    await openPage();

    expect(headings()).toEqual(['Agenda', 'Académico', 'Cobranza']);
    expect(groupLabels('Académico')).toEqual([
      'Evaluación finalizada',
      'Informe de progreso aprobado',
      'Nota de aprendizaje publicada',
      'Nueva tarea asignada',
      'Plan de aprendizaje activado',
      'Plan de aprendizaje completado',
      'Tarea calificada',
    ]);
  });

  it('puts every staff group in order for the Owner', async () => {
    stub({ [GET]: catalogue(OWNER_CATALOGUE) });
    await openPage();

    expect(headings()).toEqual(['Schedule', 'Administration', 'Exports']);
    expect(main().textContent).not.toContain(
      "Notices about changes to your classes can't be turned off.",
    );
  });

  it('shows the empty state', async () => {
    stub({ [GET]: catalogue([]) });
    app = await renderApp(PATH);
    await settleUntil(() =>
      main().textContent?.includes('There are no notifications to configure.'),
    );

    expect(main().querySelectorAll('[role="checkbox"]')).toHaveLength(0);
  });

  it('shows a load error with "Try again", which loads it', async () => {
    const api = stub({
      [GET]: [
        () => problem(403, 'permission_denied'),
        catalogue(OWNER_CATALOGUE),
      ],
    });
    app = await renderApp(PATH);
    await settleUntil(() => alerts(main()).length > 0);

    await click(buttonNamed(main(), 'Try again'));
    await settleUntil(() => main().querySelector('[role="checkbox"]'));

    expect(api.calls(GET)).toHaveLength(2);
    expect(alerts(main())).toEqual([]);
  });

  it('is unreachable before the privacy notice is accepted (A3)', async () => {
    const api = stub({}, { ...TEST_USER, privacy_notice_required: true });
    app = await renderApp(PATH);

    expect(app.location()).toBe(
      `/privacy-notice?next=${encodeURIComponent(PATH)}`,
    );
    expect(api.calls(GET)).toHaveLength(0);
  });
});

describe('saving (B5, E2)', () => {
  it('turns a type off and on with one-item PATCHes and announces "Saved."', async () => {
    let current = GUARDIAN_CATALOGUE;
    let release: (() => void) | undefined;
    const api = stub({
      [PATCH]: async (request, body) => {
        await new Promise<void>((resolve) => (release = resolve));
        const response = applies(current)(request, body) as Response;
        current = (
          (await response.clone().json()) as {
            items: NotificationPreferenceItem[];
          }
        ).items;
        return response;
      },
    });
    await openPage();
    const type = 'academics.learning_note_published';
    expect(checked(type)).toBe(true);

    await click(checkbox(type));

    // While it runs: the asked value, disabled; the others stay enabled.
    expect(checked(type)).toBe(false);
    expect(checkbox(type).disabled).toBe(true);
    expect(checkbox('academics.assignment_graded').disabled).toBe(false);
    act(() => release!());
    await settleUntil(() => !checkbox(type).disabled);

    expect(checked(type)).toBe(false);
    expect(status()).toBe('Saved.');

    await click(checkbox(type));
    act(() => release!());
    await settleUntil(() => !checkbox(type).disabled);

    expect(checked(type)).toBe(true);
    expect(status()).toBe('Saved.');
    expect(api.calls(PATCH).map((call) => call.body)).toEqual([
      { items: [{ notification_type: type, enabled: false }] },
      { items: [{ notification_type: type, enabled: true }] },
    ]);
    expect(api.calls(GET)).toHaveLength(1);
  });

  it('a failed save goes back to the saved value and "Try again" resends it', async () => {
    const type = 'billing.payment_reminder';
    const api = stub({
      [PATCH]: [
        () => problem(500, 'internal_error'),
        applies(GUARDIAN_CATALOGUE),
      ],
    });
    await openPage();

    await click(checkbox(type));
    await settleUntil(() => alerts(main()).length > 0);

    expect(checked(type)).toBe(true);
    expect(checkbox(type).disabled).toBe(false);
    expect(alerts(main())[0]).toContain("The change wasn't saved.");
    expect(status()).toBe('');

    await click(buttonNamed(main(), 'Try again'));
    await settleUntil(() => alerts(main()).length === 0);

    expect(checked(type)).toBe(false);
    expect(status()).toBe('Saved.');
    expect(api.calls(PATCH).map((call) => call.body)).toEqual([
      { items: [{ notification_type: type, enabled: false }] },
      { items: [{ notification_type: type, enabled: false }] },
    ]);
  });

  it('mandatory types are checked, locked and say "Required" (B4)', async () => {
    const api = stub();
    await openPage();
    const box = checkbox('scheduling.session_cancelled');

    expect(box.getAttribute('aria-checked')).toBe('true');
    expect(box.disabled).toBe(true);
    const note = document.getElementById(box.getAttribute('aria-describedby')!);
    expect(note?.textContent).toBe('Required');
    expect(main().textContent).toContain(
      "Notices about changes to your classes can't be turned off.",
    );
    // Optional ones have no description.
    expect(
      checkbox('billing.payment_reminder').hasAttribute('aria-describedby'),
    ).toBe(false);

    await click(box);

    expect(api.calls(PATCH)).toHaveLength(0);
  });

  it('a changed catalogue reloads and says so', async () => {
    const reloaded = GUARDIAN_CATALOGUE.filter(
      (item) => item.notification_type !== 'billing.payment_reminder',
    );
    const api = stub({
      [GET]: [catalogue(GUARDIAN_CATALOGUE), catalogue(reloaded)],
      [PATCH]: () => problem(422, 'notification_type_unknown'),
    });
    await openPage();

    await click(checkbox('billing.payment_reminder'));
    await settleUntil(() => status() === 'The list changed; it was reloaded.');

    expect(api.calls(GET)).toHaveLength(2);
    expect(headings()).toEqual(['Schedule', 'Academics']);
    expect(alerts(main())).toEqual([]);
  });

  it('a failed reload keeps the list with a stale alert, and "Reload" retries', async () => {
    const api = stub({
      [GET]: [
        catalogue(GUARDIAN_CATALOGUE),
        () => problem(403, 'permission_denied'),
        catalogue(GUARDIAN_CATALOGUE),
      ],
      [PATCH]: () => problem(422, 'notification_type_mandatory'),
    });
    await openPage();

    await click(checkbox('academics.assignment_graded'));
    await settleUntil(() => alerts(main()).length > 0);

    expect(alerts(main())[0]).toContain(
      "The list changed and couldn't be reloaded; it may be out of date.",
    );
    expect(status()).not.toBe('The list changed; it was reloaded.');
    expect(checked('academics.assignment_graded')).toBe(true);
    expect(headings()).toEqual(['Schedule', 'Academics', 'Billing']);

    await click(buttonNamed(main(), 'Reload'));
    await settleUntil(() => alerts(main()).length === 0);

    expect(api.calls(GET)).toHaveLength(3);
    expect(status()).toBe('The list changed; it was reloaded.');
  });

  it('an unknown type shows the generic label and its code (B3)', async () => {
    stub({
      [GET]: catalogue([
        ...OWNER_CATALOGUE,
        {
          notification_type: 'security.new_sign_in',
          mandatory: false,
          enabled: true,
        },
      ]),
    });
    await openPage();

    expect(headings()).toEqual([
      'Schedule',
      'Administration',
      'Exports',
      'Other',
    ]);
    expect(groupLabels('Other')).toEqual([
      'Other notification type security.new_sign_in',
    ]);
  });
});

describe('links (A2)', () => {
  it('the user menu links to the page after "Change password"', async () => {
    stub();
    app = await renderApp('/');

    const links = [...app.container.querySelectorAll('header a')].map(
      (a) => a.textContent,
    );
    const at = links.indexOf('Change password');
    expect(links[at + 1]).toBe('Notifications');
    const link = [...app.container.querySelectorAll('header a')].find(
      (a) => a.textContent === 'Notifications',
    ) as HTMLElement;
    expect(link.getAttribute('href')).toBe(PATH);

    await click(link);
    await settleUntil(() => main().querySelector('[role="checkbox"]'));

    expect(app.location()).toBe(PATH);
  });

  it('the bell panel links to the page and closes', async () => {
    stub();
    app = await renderApp('/');
    const bell = app.container.querySelector<HTMLButtonElement>(
      'header button[aria-haspopup="dialog"]',
    )!;

    await click(bell);
    const panel = () =>
      document.querySelector<HTMLElement>('[data-slot="popover-content"]');
    const link = [...(panel()?.querySelectorAll('a') ?? [])].find(
      (a) => a.textContent === 'Notification preferences',
    )!;
    expect(link.getAttribute('href')).toBe(PATH);

    await click(link);
    await settleUntil(() => main().querySelector('[role="checkbox"]'));

    expect(app.location()).toBe(PATH);
    expect(panel()).toBeNull();
    expect(bell.getAttribute('aria-expanded')).toBe('false');
  });
});

describe('the type pin (D1)', () => {
  // #119 A5 (apps/api/tests/unit/test_notification_catalogue.py PINNED):
  // a new API type must be added here, to NOTIFICATION_PREFERENCE_TYPES and
  // to notifications.preferences.types in es and en.
  const API_TYPES = [
    'academics.assignment_graded',
    'academics.assignment_set',
    'academics.assignment_submitted',
    'academics.closeout_approved',
    'academics.closeout_changes_requested',
    'academics.closeout_corrected',
    'academics.closeout_submitted_on_behalf',
    'academics.evaluation_finalized',
    'academics.evaluation_voided',
    'academics.learning_note_published',
    'academics.learning_note_unpublished',
    'academics.learning_plan_activated',
    'academics.learning_plan_completed',
    'academics.progress_report_approved',
    'academics.progress_report_changes_requested',
    'academics.progress_report_due',
    'academics.progress_report_retracted',
    'administration.approval_assigned',
    'billing.payment_reminder',
    'payroll.period_finalized',
    'reporting.export_failed',
    'reporting.export_ready',
    'scheduling.change_request_approved',
    'scheduling.change_request_rejected',
    'scheduling.occurrence_skipped_closure',
    'scheduling.offer_declined',
    'scheduling.offer_expired',
    'scheduling.offer_received',
    'scheduling.reassignment_needed',
    'scheduling.series_cancelled',
    'scheduling.series_changed',
    'scheduling.session_cancelled',
    'scheduling.session_request_confirmed',
    'scheduling.session_request_rejected',
    'scheduling.session_rescheduled',
    'scheduling.session_tutor_changed',
    'scheduling.tutor_substituted',
    'staff.availability_change_approved',
    'staff.availability_change_rejected',
    'staff.qualification_approved',
    'staff.qualification_rejected',
    'staff.qualification_revoked',
  ];

  it('lists the 42 API types, each once', () => {
    expect(API_TYPES).toHaveLength(42);
    expect([...NOTIFICATION_PREFERENCE_TYPES].sort()).toEqual(API_TYPES);
  });

  it('labels every type in es and en, and nothing else', () => {
    for (const language of ['es', 'en']) {
      for (const type of NOTIFICATION_PREFERENCE_TYPES) {
        expect(
          i18n.exists(preferenceLabelKey(type), { lng: language }),
          `${language}: ${type}`,
        ).toBe(true);
      }
      const labels = i18n.getResource(
        language,
        'common',
        'notifications.preferences.types',
      ) as Record<string, string>;
      expect(Object.keys(labels).sort()).toEqual(
        API_TYPES.map((type) => type.replaceAll('.', '_')).sort(),
      );
    }
  });
});
