import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { PAYMENT_METHODS_KEY } from '@/features/billing/accounts/api';
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
  click,
  fieldByLabel,
  labelled,
  openDialog,
  setFieldValue,
} from '@/test/dom';
import {
  buttonNamed,
  jsonResponse,
  renderApp,
  settleUntil,
  type RenderedApp,
} from '@/test/render-app';
import {
  METHODS_ALL_ACTIVE,
  METHODS_LAST_ACTIVE,
  METHODS_ONE_INACTIVE,
  SETTINGS,
  SETTINGS_WITH_MESSAGES,
} from './fixtures';

// M8-04b: the "Payments and receipts" tab (A1), the payment methods card
// (B1-B4: the confirmation, the reauthentication through apiCall, the last
// active method, the conflict, the shared list key), the receipt message
// card (C1-C3: the count, the changed fields only, null to clear, the shared
// settings cache, 422 and 409) and the General tab's "More settings" links
// per permission set (D1).

const PAGE = '/settings/payments';
const ME = 'GET /api/v1/auth/me';
const METHODS_GET = 'GET /api/v1/billing/payment-methods';
const SETTINGS_GET = 'GET /api/v1/business-settings';
const SETTINGS_PATCH = 'PATCH /api/v1/business-settings';
const REAUTH = 'POST /api/v1/auth/reauthenticate';
const patchOf = (code: string) =>
  `PATCH /api/v1/billing/payment-methods/${code}`;

/** An Owner: every permission the links need. */
const OWNER: SessionUserBody = {
  ...TEST_USER,
  permissions: [
    'settings.manage',
    'catalog.manage',
    'billing.view',
    'billing.manage',
  ],
};
/** An Administrator (the same permissions for these pages). */
const ADMINISTRATOR: SessionUserBody = {
  ...OWNER,
  roles: ['administrator'],
};
/** settings.manage without catalog or billing permissions. */
const SETTINGS_ONLY: SessionUserBody = {
  ...TEST_USER,
  roles: ['administrator'],
  permissions: ['settings.manage'],
};
/** Finance Staff: billing but no settings.manage. */
const FINANCE: SessionUserBody = {
  ...TEST_USER,
  roles: ['finance_staff'],
  permissions: ['billing.view', 'billing.manage'],
};

function stub(
  routes: Record<string, Responder | Responder[]> = {},
  user: SessionUserBody = OWNER,
): ApiStub {
  return stubApi({
    [ME]: meAs(user),
    [METHODS_GET]: () => jsonResponse(200, METHODS_ALL_ACTIVE),
    [SETTINGS_GET]: () => jsonResponse(200, SETTINGS),
    ...routes,
  });
}

const json = (body: unknown) => () => jsonResponse(200, body);

let app: RenderedApp | undefined;

const main = () => app!.container.querySelector('main')!;
const methodsCard = () =>
  [...main().querySelectorAll('section')].find(
    (section) => section.querySelector('h2')?.textContent === 'Payment methods',
  )!;
const messageCard = () =>
  [...main().querySelectorAll('section')].find(
    (section) => section.querySelector('h2')?.textContent === 'Receipt message',
  )!;
const rowOf = (name: string) =>
  [...methodsCard().querySelectorAll('li')].find(
    (li) => li.querySelector('.font-medium')?.textContent === name,
  )!;
const button = (name: string) => buttonNamed(main(), name);
const statusOf = (root: ParentNode) =>
  [...root.querySelectorAll('[role="status"]')]
    .map((s) => s.textContent)
    .join('|');
const alertsOf = (root: ParentNode) =>
  [...root.querySelectorAll('[role="alert"]')]
    .map((s) => s.textContent)
    .join('|');
const textarea = (label: string) =>
  labelled<HTMLTextAreaElement>(main(), label);
const describedText = (element: Element) =>
  (element.getAttribute('aria-describedby') ?? '')
    .split(' ')
    .map((id) => document.getElementById(id)?.textContent ?? '')
    .join('|');

async function open(path = PAGE) {
  app = await renderApp(path);
  await settleUntil(() => main().querySelector('h1'));
}

async function openMethods() {
  await open();
  await settleUntil(() => methodsCard()?.querySelector('li'));
}

async function confirmDeactivate(name: string) {
  await click(button(`Deactivate ${name}`));
  const dialog = openDialog();
  expect(dialog).not.toBeNull();
  await click(buttonNamed(dialog!, 'Deactivate'));
}

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

// --- A1: route and tabs ---------------------------------------------------

describe('route and tabs (A1)', () => {
  it('opens as the third Settings tab, titled "Payments and receipts"', async () => {
    stub();

    await open();

    const tabs = [
      ...main().querySelectorAll('nav[aria-label="Settings sections"] a'),
    ];
    expect(tabs.map((a) => a.textContent)).toEqual([
      'General',
      'Scheduling policies',
      'Payments and receipts',
    ]);
    expect(tabs[2]!.getAttribute('aria-current')).toBe('page');
    expect(main().querySelectorAll('h1')).toHaveLength(1);
    expect(main().querySelector('h1')?.textContent).toBe(
      'Payments and receipts',
    );
    expect(document.title).toBe('Payments and receipts · Classora');
  });

  it('without settings.manage the page is forbidden and nothing is fetched', async () => {
    const api = stub({}, FINANCE);

    await open();

    expect(main().querySelector('h1')?.textContent).toBe(
      "You don't have access to this page",
    );
    expect(api.calls(METHODS_GET)).toHaveLength(0);
    expect(api.calls(SETTINGS_GET)).toHaveLength(0);
  });
});

// --- B: payment methods ---------------------------------------------------

describe('the payment methods card (B1-B4)', () => {
  it('lists every method with its label, status and button (B1)', async () => {
    stub({ [METHODS_GET]: json(METHODS_ONE_INACTIVE) });

    await openMethods();

    const rows = [...methodsCard().querySelectorAll('li')];
    expect(rows.map((li) => li.textContent)).toEqual([
      'CashActiveDeactivate',
      'OtherInactiveActivate',
      'TransferActiveDeactivate',
    ]);
    expect(button('Deactivate Cash').disabled).toBe(false);
    expect(button('Activate Other').disabled).toBe(false);
  });

  it('an unknown code is shown as is', async () => {
    stub({
      [METHODS_GET]: json([
        ...METHODS_ALL_ACTIVE,
        { code: 'card', is_active: false, version: 1 },
      ]),
    });

    await openMethods();

    expect(rowOf('card').textContent).toBe('cardInactiveActivate');
    expect(button('Activate card')).toBeDefined();
  });

  it('deactivating asks first; cancelling sends nothing (B2)', async () => {
    const api = stub();
    await openMethods();

    await click(button('Deactivate Cash'));
    const dialog = openDialog()!;
    expect(dialog.textContent).toContain('Deactivate Cash?');
    expect(dialog.textContent).toContain(
      "New payments won't be able to use this method. Payments already recorded don't change.",
    );
    await click(buttonNamed(dialog, 'Cancel'));

    expect(openDialog()).toBeNull();
    expect(api.calls(patchOf('cash'))).toHaveLength(0);
  });

  it('a confirmed deactivation sends is_active and version, refetches and announces it (B2, B3)', async () => {
    const api = stub({
      [METHODS_GET]: [json(METHODS_ALL_ACTIVE), json(METHODS_ONE_INACTIVE)],
      [patchOf('other')]: json({ code: 'other', is_active: false, version: 2 }),
    });
    await openMethods();

    await confirmDeactivate('Other');
    await settleUntil(() => statusOf(methodsCard()) === 'Other deactivated.');

    expect(api.calls(patchOf('other')).map((c) => c.body)).toEqual([
      { is_active: false, version: 1 },
    ]);
    expect(api.calls(METHODS_GET)).toHaveLength(2);
    // The list the payment, refund, withdrawal, payroll and expense dialogs
    // read is the one refreshed.
    expect(app!.queryClient.getQueryData(PAYMENT_METHODS_KEY)).toEqual(
      METHODS_ONE_INACTIVE,
    );
    expect(rowOf('Other').textContent).toBe('OtherInactiveActivate');
  });

  it('activating sends the request with no confirmation (B2)', async () => {
    const api = stub({
      [METHODS_GET]: [json(METHODS_ONE_INACTIVE), json(METHODS_ALL_ACTIVE)],
      [patchOf('other')]: json({ code: 'other', is_active: true, version: 3 }),
    });
    await openMethods();

    await click(button('Activate Other'));
    await settleUntil(() => statusOf(methodsCard()) === 'Other activated.');

    expect(openDialog()).toBeNull();
    expect(api.calls(patchOf('other')).map((c) => c.body)).toEqual([
      { is_active: true, version: 2 },
    ]);
  });

  it('while the request runs the button is busy and the others refuse clicks (B3)', async () => {
    const api = stub({ [patchOf('other')]: [] });
    await openMethods();

    await confirmDeactivate('Other');

    const pressed = button('Deactivate Other');
    expect(pressed.getAttribute('aria-busy')).toBe('true');
    expect(pressed.getAttribute('aria-disabled')).toBe('true');
    const cash = button('Deactivate Cash');
    expect(cash.getAttribute('aria-disabled')).toBe('true');
    await click(cash);
    expect(openDialog()).toBeNull();
    expect(api.calls(patchOf('other'))).toHaveLength(1);
    expect(api.calls(patchOf('cash'))).toHaveLength(0);
  });

  it('reauth_required opens the password dialog and retries once', async () => {
    const api = stub({
      [METHODS_GET]: [json(METHODS_ALL_ACTIVE), json(METHODS_ONE_INACTIVE)],
      [patchOf('other')]: [
        () => problem(403, 'reauth_required'),
        json({ code: 'other', is_active: false, version: 2 }),
      ],
      [REAUTH]: json({ reauthenticated_until: '2026-11-05T12:05:00Z' }),
    });
    await openMethods();

    await confirmDeactivate('Other');
    await settleUntil(() => openDialog());
    const dialog = openDialog()!;
    setFieldValue(fieldByLabel(dialog, 'Password'), 'my-password');
    await click(buttonNamed(dialog, 'Confirm'));
    await settleUntil(() => statusOf(methodsCard()) === 'Other deactivated.');

    expect(api.calls(patchOf('other'))).toHaveLength(2);
  });

  it('cancelling the password dialog changes nothing and says why (B2)', async () => {
    const api = stub({
      [patchOf('other')]: () => problem(403, 'reauth_required'),
    });
    await openMethods();

    await confirmDeactivate('Other');
    await settleUntil(() => openDialog());
    await click(buttonNamed(openDialog()!, 'Cancel'));
    await settleUntil(() => alertsOf(methodsCard()) !== '');

    expect(api.calls(patchOf('other'))).toHaveLength(1);
    expect(alertsOf(methodsCard())).toContain(
      'Confirm your password to continue.',
    );
    expect(statusOf(methodsCard())).toBe('');
    expect(rowOf('Other').textContent).toBe('OtherActiveDeactivate');
    expect(button('Deactivate Other').getAttribute('aria-busy')).toBeNull();
  });

  it("the last active method's button is disabled with the reason (B4)", async () => {
    stub({ [METHODS_GET]: json(METHODS_LAST_ACTIVE) });

    await openMethods();

    const cash = button('Deactivate Cash');
    expect(cash.disabled).toBe(true);
    expect(describedText(cash)).toBe('At least one method must stay active.');
    expect(button('Activate Transfer').disabled).toBe(false);
  });

  it('payment_method_last_active shows under the row and reloads the list (B4)', async () => {
    const api = stub({
      [METHODS_GET]: [json(METHODS_ONE_INACTIVE), json(METHODS_LAST_ACTIVE)],
      [patchOf('transfer')]: () => problem(409, 'payment_method_last_active'),
    });
    await openMethods();

    await confirmDeactivate('Transfer');
    await settleUntil(() => api.calls(METHODS_GET).length === 2);

    expect(alertsOf(rowOf('Transfer'))).toBe(
      'At least one payment method must stay active.',
    );
    expect(button('Deactivate Cash').disabled).toBe(true);
  });

  it('version_conflict reloads the list and says so (B4, E3)', async () => {
    const api = stub({
      [METHODS_GET]: [json(METHODS_ALL_ACTIVE), json(METHODS_ONE_INACTIVE)],
      [patchOf('other')]: () => problem(409, 'version_conflict'),
    });
    await openMethods();

    await confirmDeactivate('Other');
    await settleUntil(() => api.calls(METHODS_GET).length === 2);
    await settleUntil(() => statusOf(methodsCard()) !== '');

    expect(statusOf(methodsCard())).toBe(
      'The payment methods changed and the list was refreshed. Check it and try again.',
    );
    expect(alertsOf(methodsCard())).toBe('');
    expect(rowOf('Other').textContent).toBe('OtherInactiveActivate');
  });

  it('a conflict whose reload fails keeps the list with a stale alert (E3)', async () => {
    stub({
      [METHODS_GET]: [
        json(METHODS_ALL_ACTIVE),
        // A 4xx is never retried by the query (a 5xx would be).
        () => problem(404, 'not_found'),
      ],
      [patchOf('other')]: () => problem(409, 'version_conflict'),
    });
    await openMethods();

    await confirmDeactivate('Other');
    await settleUntil(() => alertsOf(methodsCard()) !== '');

    expect(alertsOf(methodsCard())).toContain(
      "The payment methods changed and couldn't be reloaded; the list may be out of date.",
    );
    expect(rowOf('Other').textContent).toBe('OtherActiveDeactivate');
    expect(buttonNamed(methodsCard(), 'Reload')).toBeDefined();
  });

  it('other errors use the shared alert (B4)', async () => {
    stub({
      [patchOf('other')]: () => problem(500, 'internal_error', 'req-pm'),
    });
    await openMethods();

    await confirmDeactivate('Other');
    await settleUntil(() => alertsOf(methodsCard()) !== '');

    expect(alertsOf(methodsCard())).toContain('req-pm');
  });

  it('a failed list load shows the error with "Try again"', async () => {
    const api = stub({
      [METHODS_GET]: [
        () => problem(404, 'not_found'),
        json(METHODS_ALL_ACTIVE),
      ],
    });
    await open();
    await settleUntil(() => alertsOf(methodsCard()) !== '');

    await click(buttonNamed(methodsCard(), 'Try again'));
    await settleUntil(() => methodsCard().querySelector('li'));

    expect(api.calls(METHODS_GET).length).toBeGreaterThanOrEqual(2);
  });
});

// --- C: receipt message ---------------------------------------------------

describe('the receipt message card (C1-C3)', () => {
  async function openMessage() {
    await open();
    await settleUntil(() => messageCard()?.querySelector('textarea'));
  }

  it('two labelled textareas with the help text and a live count (C1)', async () => {
    stub({ [SETTINGS_GET]: json(SETTINGS_WITH_MESSAGES) });
    await openMessage();

    const es = textarea('Message in Spanish');
    expect(es.value).toBe(SETTINGS_WITH_MESSAGES.receipt_message_es);
    expect(describedText(es)).toBe(
      "Printed on new receipts in each receipt's language. Receipts already issued don't change.|47 of 500 characters",
    );
    setFieldValue(textarea('Message in English'), '  Gracias ');
    expect(describedText(textarea('Message in English'))).toContain(
      '7 of 500 characters',
    );
  });

  it('Save stays disabled until a message changes', async () => {
    stub({ [SETTINGS_GET]: json(SETTINGS_WITH_MESSAGES) });
    await openMessage();

    const save = button('Save message');
    expect(save.disabled).toBe(true);
    // Only whitespace around the stored text: nothing would change.
    setFieldValue(
      textarea('Message in English'),
      ' Thank you for your payment.\n',
    );
    expect(save.disabled).toBe(true);
    setFieldValue(textarea('Message in English'), 'Thanks!');
    expect(save.disabled).toBe(false);
  });

  it('sends the version and only the changed message, trimmed (C2)', async () => {
    const api = stub({
      [SETTINGS_PATCH]: json({
        ...SETTINGS,
        receipt_message_es: 'Gracias.',
        version: 2,
      }),
    });
    await openMessage();

    setFieldValue(textarea('Message in Spanish'), '  Gracias.\r\n ');
    await click(button('Save message'));
    await settleUntil(() => statusOf(messageCard()) === 'Saved.');

    expect(api.calls(SETTINGS_PATCH).map((c) => c.body)).toEqual([
      { version: 1, receipt_message_es: 'Gracias.' },
    ]);
  });

  it('an emptied message is sent as null (C2)', async () => {
    const api = stub({
      [SETTINGS_GET]: json(SETTINGS_WITH_MESSAGES),
      [SETTINGS_PATCH]: json({
        ...SETTINGS_WITH_MESSAGES,
        receipt_message_en: null,
        version: 4,
      }),
    });
    await openMessage();

    setFieldValue(textarea('Message in English'), '   ');
    await click(button('Save message'));
    await settleUntil(() => statusOf(messageCard()) === 'Saved.');

    expect(api.calls(SETTINGS_PATCH).map((c) => c.body)).toEqual([
      { version: 3, receipt_message_en: null },
    ]);
    expect(textarea('Message in English').value).toBe('');
    expect(button('Save message').disabled).toBe(true);
  });

  it('a save updates the shared settings cache and never asks for the password (C3)', async () => {
    const answer = { ...SETTINGS, receipt_message_en: 'Thanks.', version: 2 };
    const api = stub({
      // The General tab's refetch never answers: it shows the cache.
      [SETTINGS_GET]: [json(SETTINGS), () => new Promise<Response>(() => {})],
      [SETTINGS_PATCH]: json(answer),
    });
    await openMessage();

    setFieldValue(textarea('Message in English'), 'Thanks.');
    await click(button('Save message'));
    await settleUntil(() => statusOf(messageCard()) === 'Saved.');

    expect(app!.queryClient.getQueryData(['business-settings'])).toEqual(
      answer,
    );
    expect(api.calls(REAUTH)).toHaveLength(0);
    expect(openDialog()).toBeNull();
    expect(
      api
        .calls(SETTINGS_PATCH)
        .some((c) => Object.hasOwn(c.body as object, 'time_zone')),
    ).toBe(false);

    // The General tab then sends the new version.
    await click(
      [...main().querySelectorAll<HTMLAnchorElement>('nav a')].find(
        (a) => a.textContent === 'General',
      )!,
    );
    await settleUntil(
      () => main().querySelector('h1')?.textContent === 'Settings',
    );
    setFieldValue(fieldByLabel(main(), 'Center name'), 'Centro Norte');
    await click(button('Save'));
    await settleUntil(() => api.calls(SETTINGS_PATCH).length === 2);
    expect(api.calls(SETTINGS_PATCH)[1]!.body).toEqual({
      version: 2,
      center_name: 'Centro Norte',
    });
  });

  it('over 500 characters is refused on the field without a request', async () => {
    const api = stub();
    await openMessage();

    setFieldValue(textarea('Message in Spanish'), 'x'.repeat(501));
    await click(button('Save message'));

    const es = textarea('Message in Spanish');
    expect(es.getAttribute('aria-invalid')).toBe('true');
    expect(describedText(es)).toContain('Use at most 500 characters.');
    expect(describedText(es)).toContain('501 of 500 characters');
    expect(document.activeElement).toBe(es);
    expect(api.calls(SETTINGS_PATCH)).toHaveLength(0);
  });

  it('a 422 shows on its field (C3)', async () => {
    stub({
      [SETTINGS_PATCH]: () =>
        new Response(
          JSON.stringify({
            type: 'about:blank',
            title: 'Problem',
            status: 422,
            code: 'validation_error',
            request_id: 'req-422',
            errors: [
              {
                path: ['body', 'receipt_message_en'],
                code: 'value_error',
                message: 'Server message',
              },
            ],
          }),
          {
            status: 422,
            headers: { 'Content-Type': 'application/problem+json' },
          },
        ),
    });
    await openMessage();

    setFieldValue(textarea('Message in English'), 'Tab\there');
    await click(button('Save message'));
    await settleUntil(
      () =>
        textarea('Message in English').getAttribute('aria-invalid') === 'true',
    );

    expect(describedText(textarea('Message in English'))).toContain(
      'Use only text and line breaks.',
    );
    expect(
      textarea('Message in Spanish').getAttribute('aria-invalid'),
    ).toBeNull();
    expect(statusOf(messageCard())).toBe('');
  });

  it('version_conflict reloads the settings and says so (C3, E3)', async () => {
    const api = stub({
      [SETTINGS_GET]: [json(SETTINGS), json(SETTINGS_WITH_MESSAGES)],
      [SETTINGS_PATCH]: () => problem(409, 'version_conflict'),
    });
    await openMessage();

    setFieldValue(textarea('Message in Spanish'), 'Mío');
    await click(button('Save message'));
    await settleUntil(() => statusOf(messageCard()) !== '');

    expect(api.calls(SETTINGS_GET)).toHaveLength(2);
    expect(statusOf(messageCard())).toBe(
      'Someone else changed the settings. The saved message was loaded; make your change again.',
    );
    expect(textarea('Message in Spanish').value).toBe(
      SETTINGS_WITH_MESSAGES.receipt_message_es,
    );
  });

  it('a conflict whose reload fails keeps the text with a stale alert (E3)', async () => {
    stub({
      [SETTINGS_GET]: [json(SETTINGS), () => problem(404, 'not_found')],
      [SETTINGS_PATCH]: () => problem(409, 'version_conflict'),
    });
    await openMessage();

    setFieldValue(textarea('Message in Spanish'), 'Mío');
    await click(button('Save message'));
    await settleUntil(() => alertsOf(messageCard()) !== '');

    expect(alertsOf(messageCard())).toContain(
      "The settings changed and couldn't be reloaded; the message may be out of date.",
    );
    expect(textarea('Message in Spanish').value).toBe('Mío');
  });
});

// --- D1: More settings links ----------------------------------------------

describe('"More settings" on the General tab (D1)', () => {
  const links = () => {
    const section = [...main().querySelectorAll('section')].find(
      (s) => s.querySelector('h2')?.textContent === 'More settings',
    );
    return section
      ? [...section.querySelectorAll('a')].map((a) => [
          a.textContent,
          a.getAttribute('href'),
        ])
      : null;
  };

  it.each([
    ['an Owner', OWNER],
    ['an Administrator', ADMINISTRATOR],
  ])('%s sees every link', async (_name, user) => {
    stub({}, user);

    await open('/settings');

    expect(links()).toEqual([
      ['Academic periods', '/catalog/periods'],
      ['Prices', '/billing/prices'],
      ['Billing settings', '/billing/settings'],
      ['Packages', '/billing/package-products'],
    ]);
  });

  it('only the links the user may open are shown', async () => {
    stub(
      {},
      {
        ...SETTINGS_ONLY,
        permissions: ['settings.manage', 'billing.view'],
      },
    );

    await open('/settings');

    expect(links()).toEqual([['Billing settings', '/billing/settings']]);
  });

  it('with no link visible the section is not rendered', async () => {
    stub({}, SETTINGS_ONLY);

    await open('/settings');

    expect(main().querySelector('form')).not.toBeNull();
    expect(links()).toBeNull();
  });
});
