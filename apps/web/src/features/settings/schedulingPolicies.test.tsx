import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
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
import { alerts, click, fieldByLabel, openDialog, typeInto } from '@/test/dom';
import {
  buttonNamed,
  jsonResponse,
  renderApp,
  type RenderedApp,
} from '@/test/render-app';
import type { SchedulingPolicyOverview } from './api';
import {
  OVERVIEW_V1,
  OVERVIEW_WITH_UPCOMING,
  POLICY_UPCOMING,
  POLICY_V1,
  SETTINGS,
} from './fixtures';

// #61 F3 (and F1, F2): the Settings tab bar and the scheduling policies
// route behind settings.manage, the summary of the version in force, the
// POST body for "Immediately" and for "From date" (the start of the day in
// the session's center zone, not the browser's), Save disabled while
// nothing changed, the range checks, the delete confirmation and the three
// error messages.

const PAGE = '/settings/scheduling-policies';
const POLICIES_GET = 'GET /api/v1/scheduling-policies';
const POLICIES_POST = 'POST /api/v1/scheduling-policies';
const deleteRoute = (id: string) => `DELETE /api/v1/scheduling-policies/${id}`;
const ME = 'GET /api/v1/auth/me';

const MANAGER: SessionUserBody = {
  ...TEST_USER,
  permissions: ['user.view', 'catalog.manage', 'settings.manage'],
};
const COORDINATOR: SessionUserBody = {
  ...TEST_USER,
  roles: ['academic_coordinator'],
  permissions: ['student.view', 'student.manage'],
};

function stub(
  overview: SchedulingPolicyOverview | SchedulingPolicyOverview[] = OVERVIEW_V1,
  routes: Record<string, Responder | Responder[]> = {},
  user: SessionUserBody = MANAGER,
): ApiStub {
  const answers = (Array.isArray(overview) ? overview : [overview]).map(
    (body) => () => jsonResponse(200, body),
  );
  return stubApi({
    [ME]: meAs(user),
    [POLICIES_GET]: answers,
    'GET /api/v1/business-settings': () => jsonResponse(200, SETTINGS),
    ...routes,
  });
}

let app: RenderedApp | undefined;

const main = () => app!.container.querySelector('main')!;
const field = (label: string) => fieldByLabel(main(), label);
/** The control labelled `text` (a Radix checkbox is a button). */
const control = (text: string) => {
  const label = [...main().querySelectorAll('label')].find(
    (candidate) => candidate.textContent === text,
  )!;
  return document.getElementById(label.htmlFor)!;
};
const saveButton = () => buttonNamed(main(), 'Save');
const statusText = () =>
  [...main().querySelectorAll('[role="status"]')]
    .map((s) => s.textContent)
    .join('|');
const tabs = () =>
  [
    ...main().querySelectorAll<HTMLAnchorElement>(
      'nav[aria-label="Settings sections"] a',
    ),
  ].map((a) => [a.textContent, a.getAttribute('aria-current')]);

const created = () => jsonResponse(201, POLICY_UPCOMING);

beforeEach(() => {
  applyLanguage('en');
  vi.spyOn(console, 'error').mockImplementation(() => {});
});

afterEach(() => {
  app?.unmount();
  app = undefined;
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

// --- F1: tabs and route -------------------------------------------------------------

describe('tabs and access (F1)', () => {
  it('shows the tab bar and the page to a settings.manage holder', async () => {
    stub();

    app = await renderApp(PAGE);

    expect(tabs()).toEqual([
      ['General', null],
      ['Scheduling policies', 'page'],
    ]);
    expect(main().querySelectorAll('h1')).toHaveLength(1);
    expect(main().querySelector('h1')?.textContent).toBe('Scheduling policies');
    expect(document.title).toBe('Scheduling policies · Classora');
  });

  it('marks "General" on the general settings page', async () => {
    stub();

    app = await renderApp('/settings');

    expect(tabs()).toEqual([
      ['General', 'page'],
      ['Scheduling policies', null],
    ]);
    expect(main().querySelectorAll('h1')).toHaveLength(1);
  });

  it('without settings.manage the page is forbidden and nothing is read', async () => {
    const api = stub(OVERVIEW_V1, {}, COORDINATOR);

    app = await renderApp(PAGE);

    expect(main().querySelector('h1')?.textContent).toBe(
      "You don't have access to this page",
    );
    expect(main().querySelector('nav[aria-label="Settings sections"]')).toBe(
      null,
    );
    expect(api.calls(POLICIES_GET)).toHaveLength(0);
  });
});

// --- F2: the summary ----------------------------------------------------------------

describe('in force now (F2)', () => {
  it('states each rule of the current version as a sentence', async () => {
    stub();

    app = await renderApp(PAGE);

    const items = [...main().querySelectorAll('section li')].map(
      (li) => li.textContent,
    );
    expect(items.slice(0, 6)).toEqual([
      'Cancellations and reschedules less than 24 hours before the class are late.',
      'A cancellation at or after the start is a no-show.',
      'Tutors have 48 hours to answer an offer.',
      'Bookings need 24 hours of notice.',
      'Makeups: allowed after an on-time cancellation, within 30 days.',
      'Changes made by the tutor or the center are excused, and a makeup is allowed.',
    ]);
    expect(main().textContent).toContain('No upcoming changes.');
    expect(main().textContent).toContain('No earlier versions.');
  });

  it('lists upcoming changes with their changed values and the history', async () => {
    stub(OVERVIEW_WITH_UPCOMING);

    app = await renderApp(PAGE);

    const upcoming = main().querySelector(
      `[data-policy-id="${POLICY_UPCOMING.id}"]`,
    )!;
    expect(upcoming.textContent).toContain(
      'Cancellation notice: 24 hours → 12 hours',
    );
    const past = main().querySelector(`[data-policy-id="${POLICY_V1.id}"]`)!;
    expect(past.textContent).toContain('Offer response time: 48 hours');
    // Prefilled from the latest (upcoming) version.
    expect(field('Cancellation notice (hours)').value).toBe('12');
  });
});

// --- F3: the form -------------------------------------------------------------------

describe('publishing a version (F3)', () => {
  it('"Immediately" sends every rule and no effective_from', async () => {
    const api = stub([OVERVIEW_V1, OVERVIEW_WITH_UPCOMING], {
      [POLICIES_POST]: created,
    });
    app = await renderApp(PAGE);

    typeInto(field('Cancellation notice (hours)'), '12');
    await click(saveButton());

    expect(api.calls(POLICIES_POST).map((c) => c.body)).toEqual([
      {
        based_on_id: POLICY_V1.id,
        booking_notice_hours: 24,
        change_notice_hours: 12,
        response_hours: 48,
        makeup_window_days: 30,
        makeup_after_on_time: true,
        makeup_after_late: false,
        makeup_after_no_show: false,
      },
    ]);
    expect(statusText()).toContain('Saved.');
    expect(api.calls(POLICIES_GET).length).toBeGreaterThanOrEqual(2);
  });

  it('"From date" sends the start of that day in the session zone', async () => {
    const api = stubApi({
      [ME]: meAs({ ...MANAGER, center_time_zone: 'America/Cancun' }),
      [POLICIES_GET]: () => jsonResponse(200, OVERVIEW_V1),
      [POLICIES_POST]: created,
    });
    app = await renderApp(PAGE);

    await click(control('Allow a makeup after a no-show'));
    await click(field('From date'));
    typeInto(field('Start date'), '2099-12-01');
    await click(saveButton());

    const [call] = api.calls(POLICIES_POST);
    expect(call?.body).toMatchObject({
      based_on_id: POLICY_V1.id,
      makeup_after_no_show: true,
      // Cancún is UTC-5 all year: not the browser's zone, not Mexico City.
      effective_from: '2099-12-01T05:00:00.000Z',
    });
  });

  it('keeps Save disabled until a rule changes', async () => {
    stub();
    app = await renderApp(PAGE);

    expect(saveButton().disabled).toBe(true);
    typeInto(field('Makeup window (days)'), '31');
    expect(saveButton().disabled).toBe(false);
    typeInto(field('Makeup window (days)'), '30');
    expect(saveButton().disabled).toBe(true);
  });

  it.each([
    [
      'Cancellation notice (hours)',
      '337',
      'Enter a whole number from 0 to 336.',
    ],
    ['Booking notice (hours)', '-1', 'Enter a whole number from 0 to 336.'],
    ['Offer response time (hours)', '0', 'Enter a whole number from 1 to 168.'],
    ['Makeup window (days)', '366', 'Enter a whole number from 1 to 365.'],
  ])('checks the range of %s', async (label, value, message) => {
    const api = stub(OVERVIEW_V1, { [POLICIES_POST]: created });
    app = await renderApp(PAGE);

    typeInto(field(label), value);
    await click(saveButton());

    const input = field(label);
    expect(input.getAttribute('aria-invalid')).toBe('true');
    expect(
      document.getElementById(
        input.getAttribute('aria-describedby')!.split(' ').at(-1)!,
      )?.textContent,
    ).toBe(message);
    expect(api.calls(POLICIES_POST)).toHaveLength(0);
  });

  it('asks for a date after today', async () => {
    const api = stub(OVERVIEW_V1, { [POLICIES_POST]: created });
    app = await renderApp(PAGE);

    typeInto(field('Cancellation notice (hours)'), '12');
    await click(field('From date'));
    await click(saveButton());
    expect(main().textContent).toContain('Choose a date.');

    typeInto(field('Start date'), '2000-01-01');
    await click(saveButton());
    expect(main().textContent).toContain('Choose a date after today.');
    expect(api.calls(POLICIES_POST)).toHaveLength(0);
  });
});

// --- F2: deleting and errors --------------------------------------------------------

describe('deleting an upcoming change (F2)', () => {
  it('confirms, then deletes and announces it', async () => {
    const api = stub([OVERVIEW_WITH_UPCOMING, OVERVIEW_V1], {
      [deleteRoute(POLICY_UPCOMING.id)]: noContent,
    });
    app = await renderApp(PAGE);

    await click(buttonNamed(main(), 'Delete'));
    const dialog = openDialog()!;
    expect(dialog.textContent).toContain('Delete this change?');
    expect(api.calls(deleteRoute(POLICY_UPCOMING.id))).toHaveLength(0);

    await click(buttonNamed(dialog, 'Delete change'));

    expect(api.calls(deleteRoute(POLICY_UPCOMING.id))).toHaveLength(1);
    expect(openDialog()).toBeNull();
    expect(statusText()).toContain('Deleted.');
  });

  it('keeping the change sends nothing', async () => {
    const api = stub(OVERVIEW_WITH_UPCOMING);
    app = await renderApp(PAGE);

    await click(buttonNamed(main(), 'Delete'));
    await click(buttonNamed(openDialog()!, 'Cancel'));

    expect(openDialog()).toBeNull();
    expect(api.calls(deleteRoute(POLICY_UPCOMING.id))).toHaveLength(0);
  });
});

describe('error messages (F2)', () => {
  it('version_conflict offers "Reload"', async () => {
    const api = stub([OVERVIEW_V1, OVERVIEW_WITH_UPCOMING], {
      [POLICIES_POST]: () => problem(409, 'version_conflict'),
    });
    app = await renderApp(PAGE);

    typeInto(field('Cancellation notice (hours)'), '12');
    await click(saveButton());
    expect(alerts(main()).join(' ')).toContain(
      'Someone else changed this record.',
    );

    await click(buttonNamed(main(), 'Reload'));

    expect(api.calls(POLICIES_GET).length).toBeGreaterThanOrEqual(2);
    expect(field('Cancellation notice (hours)').value).toBe('12');
    expect(alerts(main())).toEqual([]);
  });

  it('invalid_effective_from shows in the alert region', async () => {
    stub(OVERVIEW_V1, {
      [POLICIES_POST]: () => problem(422, 'invalid_effective_from'),
    });
    app = await renderApp(PAGE);

    typeInto(field('Cancellation notice (hours)'), '12');
    await click(saveButton());

    expect(alerts(main()).join(' ')).toContain(
      'A new policy must take effect now or later, and after every upcoming change.',
    );
  });

  it('policy_version_in_effect shows in the alert region', async () => {
    stub(OVERVIEW_WITH_UPCOMING, {
      [deleteRoute(POLICY_UPCOMING.id)]: () =>
        problem(409, 'policy_version_in_effect'),
    });
    app = await renderApp(PAGE);

    await click(buttonNamed(main(), 'Delete'));
    await click(buttonNamed(openDialog()!, 'Delete change'));

    expect(alerts(main()).join(' ')).toContain(
      "This policy is already in effect, so it can't be deleted.",
    );
  });
});
