import { act } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
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
import { click, fieldByLabel, openDialog, typeInto } from '@/test/dom';
import {
  buttonNamed,
  jsonResponse,
  renderApp,
  type RenderedApp,
} from '@/test/render-app';
import type { BusinessSettings } from './api';
import { DEFAULT_HOURS, SETTINGS, SETTINGS_ELSEWHERE } from './fixtures';

// #37 H5 (and H1-H4): the Settings navigation item and route behind
// settings.manage, the PATCH bodies (a name change, the whole week for an
// hours change, a zone change), the reauthentication retry, the two 409
// messages, the cross-field hours check, "Saved." and the session refresh;
// H2: the student form's "today" follows the session's center zone.

const SETTINGS_GET = 'GET /api/v1/business-settings';
const SETTINGS_PATCH = 'PATCH /api/v1/business-settings';
const ME = 'GET /api/v1/auth/me';
const REAUTH = 'POST /api/v1/auth/reauthenticate';

/** An Owner or Administrator. */
const MANAGER: SessionUserBody = {
  ...TEST_USER,
  permissions: ['user.view', 'catalog.manage', 'settings.manage'],
};
/** A Coordinator: no settings.manage. */
const COORDINATOR: SessionUserBody = {
  ...TEST_USER,
  roles: ['academic_coordinator'],
  permissions: ['student.view', 'student.manage'],
};

function stub(
  user: SessionUserBody = MANAGER,
  routes: Record<string, Responder | Responder[]> = {},
): ApiStub {
  return stubApi({
    [ME]: meAs(user),
    [SETTINGS_GET]: () => jsonResponse(200, SETTINGS),
    ...routes,
  });
}

const saved = (body: BusinessSettings) => () => jsonResponse(200, body);

let app: RenderedApp | undefined;

const main = () => app!.container.querySelector('main')!;
const navItems = () =>
  [...app!.container.querySelectorAll('header nav a')].map(
    (a) => a.textContent,
  );
const field = (label: string) => fieldByLabel(main(), label);
const labelled = <T extends HTMLElement>(name: string) => {
  const found = main().querySelector<T>(`[aria-label="${name}"]`);
  if (!found) {
    throw new Error(`Nothing labelled "${name}"`);
  }
  return found;
};
const zoneSelect = () => {
  const label = [...main().querySelectorAll('label')].find(
    (l) => l.textContent === 'Center time zone',
  )!;
  return document.getElementById(label.htmlFor) as HTMLSelectElement;
};
const statusText = () =>
  [...main().querySelectorAll('[role="status"]')]
    .map((s) => s.textContent)
    .join('|');
const alertText = () =>
  [...main().querySelectorAll('[role="alert"]')]
    .map((s) => s.textContent)
    .join('|');

function choose(select: HTMLSelectElement, value: string) {
  act(() => {
    select.value = value;
    select.dispatchEvent(new Event('change', { bubbles: true }));
  });
}

async function save() {
  await click(buttonNamed(main(), 'Save'));
}

beforeEach(() => {
  applyLanguage('en');
  vi.spyOn(console, 'error').mockImplementation(() => {});
});

afterEach(() => {
  app?.unmount();
  app = undefined;
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

// --- H3: navigation and route -------------------------------------------------------

describe('navigation and access (H3)', () => {
  it('shows "Settings" after "Catalog" to a settings.manage holder', async () => {
    stub();

    app = await renderApp('/settings');

    const items = navItems();
    expect(items.indexOf('Settings')).toBe(items.indexOf('Catalog') + 1);
    expect(main().querySelectorAll('h1')).toHaveLength(1);
    expect(main().querySelector('h1')?.textContent).toBe('Settings');
    expect(document.title).toBe('Settings · Classora');
    expect(field('Center name').value).toBe('Classora');
  });

  it('without settings.manage there is no item and the page is forbidden', async () => {
    const api = stub(COORDINATOR);

    app = await renderApp('/settings');

    expect(navItems()).not.toContain('Settings');
    expect(main().querySelector('h1')?.textContent).toBe(
      "You don't have access to this page",
    );
    expect(api.calls(SETTINGS_GET)).toHaveLength(0);
  });
});

// --- H3, H4: the form ---------------------------------------------------------------

describe('the form (H3, H4)', () => {
  it('shows the stored values: seven days, closed ones disabled', async () => {
    stub();

    app = await renderApp('/settings');

    expect(labelled<HTMLInputElement>('Monday opening time').value).toBe(
      '09:00',
    );
    expect(labelled<HTMLInputElement>('Saturday closing time').value).toBe(
      '14:00',
    );
    const sunday = labelled<HTMLInputElement>('Sunday opening time');
    expect(sunday.disabled).toBe(true);
    expect(sunday.type).toBe('time');
    expect(sunday.step).toBe('900');
    expect(
      labelled<HTMLButtonElement>('Open on Sunday').getAttribute(
        'aria-checked',
      ),
    ).toBe('false');
    expect(zoneSelect().value).toBe('America/Mexico_City');
    expect(zoneSelect().options).toHaveLength(12);
  });

  it('offers the stored zone even when it is not one of Mexico', async () => {
    stub(MANAGER, { [SETTINGS_GET]: saved(SETTINGS_ELSEWHERE) });

    app = await renderApp('/settings');

    expect(zoneSelect().value).toBe('America/Bogota');
    expect(zoneSelect().options).toHaveLength(13);
    expect(field('Email').value).toBe('hola@centro.mx');
  });

  it('a name change sends the version and the name only', async () => {
    const api = stub(MANAGER, {
      [SETTINGS_PATCH]: saved({
        ...SETTINGS,
        center_name: 'Centro Norte',
        version: 2,
      }),
    });
    app = await renderApp('/settings');

    typeInto(field('Center name'), '  Centro Norte ');
    await save();

    expect(api.calls(SETTINGS_PATCH).map((c) => c.body)).toEqual([
      { version: 1, center_name: 'Centro Norte' },
    ]);
    expect(statusText()).toBe('Saved.');
  });

  it('emptied contact fields are sent as null', async () => {
    const api = stub(MANAGER, {
      [SETTINGS_GET]: saved(SETTINGS_ELSEWHERE),
      [SETTINGS_PATCH]: saved(SETTINGS_ELSEWHERE),
    });
    app = await renderApp('/settings');

    typeInto(field('Phone'), '');
    typeInto(field('Address'), '   ');
    await save();

    expect(api.calls(SETTINGS_PATCH).map((c) => c.body)).toEqual([
      { version: 4, contact_phone: null, address: null },
    ]);
  });

  it('an hours change sends the whole week', async () => {
    const api = stub(MANAGER, { [SETTINGS_PATCH]: saved(SETTINGS) });
    app = await renderApp('/settings');

    await click(labelled<HTMLButtonElement>('Open on Sunday'));
    const opens = labelled<HTMLInputElement>('Sunday opening time');
    expect(opens.disabled).toBe(false);
    typeInto(opens, '10:00');
    typeInto(labelled<HTMLInputElement>('Sunday closing time'), '13:00');
    await click(labelled<HTMLButtonElement>('Open on Saturday'));
    await save();

    expect(api.calls(SETTINGS_PATCH).map((c) => c.body)).toEqual([
      {
        version: 1,
        operating_hours: [
          ...DEFAULT_HOURS.slice(0, 5),
          { weekday: 7, opens_at: '10:00', closes_at: '13:00' },
        ],
      },
    ]);
  });

  it('a zone change sends the version and the zone only', async () => {
    const api = stub(MANAGER, {
      [SETTINGS_PATCH]: saved({
        ...SETTINGS,
        time_zone: 'America/Cancun',
        version: 2,
      }),
    });
    app = await renderApp('/settings');

    choose(zoneSelect(), 'America/Cancun');
    await save();

    expect(api.calls(SETTINGS_PATCH).map((c) => c.body)).toEqual([
      { version: 1, time_zone: 'America/Cancun' },
    ]);
  });

  it('after a save the settings and the session are fetched again', async () => {
    const api = stub(MANAGER, {
      [SETTINGS_PATCH]: saved({
        ...SETTINGS,
        time_zone: 'America/Cancun',
        version: 2,
      }),
    });
    app = await renderApp('/settings');
    const before = {
      me: api.calls(ME).length,
      settings: api.calls(SETTINGS_GET).length,
    };

    choose(zoneSelect(), 'America/Cancun');
    await save();

    expect(api.calls(ME).length).toBe(before.me + 1);
    expect(api.calls(SETTINGS_GET).length).toBe(before.settings + 1);
  });

  it('reauth_required opens the password dialog and retries once', async () => {
    const api = stub(MANAGER, {
      [SETTINGS_PATCH]: [
        () => problem(403, 'reauth_required'),
        saved({ ...SETTINGS, time_zone: 'America/Cancun', version: 2 }),
      ],
      [REAUTH]: () =>
        jsonResponse(200, { reauthenticated_until: '2026-10-18T12:05:00Z' }),
    });
    app = await renderApp('/settings');

    choose(zoneSelect(), 'America/Cancun');
    await save();
    const dialog = openDialog();
    expect(dialog).not.toBeNull();
    typeInto(fieldByLabel(dialog!, 'Password'), 'my-password');
    await click(buttonNamed(dialog!, 'Confirm'));

    expect(openDialog()).toBeNull();
    expect(api.calls(SETTINGS_PATCH).map((c) => c.body)).toEqual([
      { version: 1, time_zone: 'America/Cancun' },
      { version: 1, time_zone: 'America/Cancun' },
    ]);
    expect(statusText()).toBe('Saved.');
  });

  it('cancelling the password dialog keeps the zone and says why', async () => {
    const api = stub(MANAGER, {
      [SETTINGS_PATCH]: () => problem(403, 'reauth_required'),
    });
    app = await renderApp('/settings');

    choose(zoneSelect(), 'America/Cancun');
    await save();
    await click(buttonNamed(openDialog()!, 'Cancel'));

    expect(api.calls(SETTINGS_PATCH)).toHaveLength(1);
    expect(alertText()).toContain('Confirm your password to continue.');
    expect(statusText()).toBe('');
  });

  it('tz_change_requires_migration shows in the alert region', async () => {
    stub(MANAGER, {
      [SETTINGS_PATCH]: () => problem(409, 'tz_change_requires_migration'),
    });
    app = await renderApp('/settings');

    choose(zoneSelect(), 'America/Cancun');
    await save();

    expect(alertText()).toContain(
      "The time zone can't be changed because classes are already scheduled.",
    );
    expect(statusText()).toBe('');
  });

  it('version_conflict offers "Reload", which fetches the settings again', async () => {
    const api = stub(MANAGER, {
      [SETTINGS_GET]: [
        saved(SETTINGS),
        saved({ ...SETTINGS, center_name: 'Otro', version: 2 }),
      ],
      [SETTINGS_PATCH]: () => problem(409, 'version_conflict'),
    });
    app = await renderApp('/settings');

    typeInto(field('Center name'), 'Centro Norte');
    await save();
    await click(buttonNamed(main(), 'Reload'));

    expect(api.calls(SETTINGS_GET)).toHaveLength(2);
    expect(alertText()).toBe('');
    expect(field('Center name').value).toBe('Otro');
  });

  it('a closing time before the opening time is refused without a request', async () => {
    const api = stub();
    app = await renderApp('/settings');

    typeInto(labelled<HTMLInputElement>('Monday closing time'), '08:00');
    await save();

    const closes = labelled<HTMLInputElement>('Monday closing time');
    expect(closes.getAttribute('aria-invalid')).toBe('true');
    expect(
      document.getElementById(closes.getAttribute('aria-describedby')!)
        ?.textContent,
    ).toBe('Closing time must be after opening time.');
    expect(api.calls(SETTINGS_PATCH)).toHaveLength(0);
  });

  it('an open day needs both times', async () => {
    const api = stub();
    app = await renderApp('/settings');

    await click(labelled<HTMLButtonElement>('Open on Sunday'));
    await save();

    expect(
      labelled<HTMLInputElement>('Sunday opening time').getAttribute(
        'aria-invalid',
      ),
    ).toBe('true');
    expect(api.calls(SETTINGS_PATCH)).toHaveLength(0);
  });

  it('a failed load shows the error with "Try again"', async () => {
    const api = stub(MANAGER, {
      // A 4xx is never retried by the query (a 5xx would be).
      [SETTINGS_GET]: [() => problem(404, 'not_found'), saved(SETTINGS)],
    });
    app = await renderApp('/settings');

    await click(buttonNamed(main(), 'Try again'));

    expect(api.calls(SETTINGS_GET).length).toBeGreaterThanOrEqual(2);
    expect(field('Center name').value).toBe('Classora');
  });
});

// --- H2: the student form's "today" -------------------------------------------------

describe("the student form's today (H2)", () => {
  it.each([
    ['America/Cancun', '2026-10-18'],
    ['America/Mexico_City', '2026-10-17'],
  ])('with the center in %s, the birthdate max is %s', async (zone, today) => {
    vi.useFakeTimers({
      toFake: ['Date'],
      now: Date.parse('2026-10-18T05:30:00Z'),
    });
    stubApi({
      [ME]: meAs({
        ...TEST_USER,
        permissions: ['student.view', 'student.manage'],
        center_time_zone: zone,
      }),
      'GET /api/v1/education-levels': () =>
        jsonResponse(200, { items: [], total: 0, limit: 100, offset: 0 }),
    });

    app = await renderApp('/students/new');

    expect(field('Birthdate').max).toBe(today);
  });
});
