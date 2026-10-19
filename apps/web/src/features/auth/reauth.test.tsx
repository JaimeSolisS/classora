import { act } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { applyLanguage } from '@/i18n';
import {
  problem,
  stubSignedIn,
  TEST_USER,
  type Responder,
} from '@/test/api-stub';
import { ActionPage, QueryPage } from '@/test/auth-pages';
import { routesWith } from '@/test/auth-routes';
import {
  alerts,
  click,
  fieldByLabel,
  openDialog,
  pressEscape,
  typeInto,
} from '@/test/dom';
import {
  buttonNamed,
  jsonResponse,
  renderApp,
  settle,
  type RenderedApp,
} from '@/test/render-app';
import { CSRF_HEADER } from './csrf';

// D1–D6: the password dialog for 403 reauth_required.

const ACTION = 'POST /api/v1/test/action';
const REAUTH = 'POST /api/v1/auth/reauthenticate';
const PASSWORD = 'my-password-for-reauth';
const UNTIL = { reauthenticated_until: '2026-10-18T12:05:00Z' };

let app: RenderedApp | undefined;

const pages = routesWith([
  { path: 'action', element: <ActionPage /> },
  {
    path: 'two',
    element: (
      <>
        <QueryPage path="/api/v1/test/one" />
        <QueryPage path="/api/v1/test/two" />
      </>
    ),
  },
]);

const result = () =>
  app?.container.querySelector('[data-testid="result"]')?.textContent;
const dialogPassword = () => fieldByLabel(openDialog()!, 'Password');
const runButton = () => buttonNamed(app!.container, 'Run action');

async function runAction() {
  act(() => runButton().focus());
  await click(runButton());
}

async function confirm(password = PASSWORD) {
  typeInto(dialogPassword(), password);
  await click(buttonNamed(openDialog()!, 'Confirm'));
}

function stubAction(action: Responder[], reauth: Responder[]) {
  return stubSignedIn({ [ACTION]: action, [REAUTH]: reauth });
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

describe('the dialog (D1–D3)', () => {
  it('opens on reauth_required, sends the password with the token, closes and retries once', async () => {
    const stub = stubAction(
      [() => problem(403, 'reauth_required'), () => jsonResponse(200, {})],
      [() => jsonResponse(200, UNTIL)],
    );
    app = await renderApp('/action', pages);

    await runAction();

    const dialog = openDialog();
    expect(dialog).not.toBeNull();
    expect(dialog!.getAttribute('aria-labelledby')).toBeTruthy();
    expect(
      document.getElementById(dialog!.getAttribute('aria-labelledby')!)
        ?.textContent,
    ).toBe('Confirm your password');
    expect(dialog!.textContent).toContain(
      'For your security, enter your password again to continue.',
    );
    expect(document.activeElement).toBe(dialogPassword());
    expect(dialogPassword().getAttribute('autocomplete')).toBe(
      'current-password',
    );

    await confirm();

    const [reauth] = stub.calls(REAUTH);
    expect(reauth!.body).toEqual({ password: PASSWORD });
    expect(reauth!.headers.get(CSRF_HEADER)).toBe(TEST_USER.csrf_token);
    expect(openDialog()).toBeNull();
    expect(stub.calls(ACTION)).toHaveLength(2);
    expect(result()).toBe('done');
    // Focus goes back to where it was before the dialog opened.
    expect(document.activeElement).toBe(runButton());
  });

  it('validates an empty password without a request', async () => {
    const stub = stubAction([() => problem(403, 'reauth_required')], []);
    app = await renderApp('/action', pages);
    await runAction();

    await click(buttonNamed(openDialog()!, 'Confirm'));

    expect(stub.calls(REAUTH)).toHaveLength(0);
    expect(dialogPassword().getAttribute('aria-invalid')).toBe('true');
    const describedBy = dialogPassword().getAttribute('aria-describedby')!;
    expect(document.getElementById(describedBy)?.textContent).toBe(
      'Enter your password.',
    );
  });

  it('a second reauth_required after the retry does not reopen the dialog', async () => {
    const stub = stubAction(
      [() => problem(403, 'reauth_required')],
      [() => jsonResponse(200, UNTIL)],
    );
    app = await renderApp('/action', pages);
    await runAction();

    await confirm();

    expect(openDialog()).toBeNull();
    expect(stub.calls(ACTION)).toHaveLength(2);
    expect(stub.calls(REAUTH)).toHaveLength(1);
    expect(alerts(app.container)[0]).toContain(
      'Confirm your password to continue.',
    );
  });

  it('reauth_failed: the error in the dialog, field cleared and focused, still open', async () => {
    const stub = stubAction(
      [() => problem(403, 'reauth_required')],
      [() => problem(403, 'reauth_failed')],
    );
    app = await renderApp('/action', pages);
    await runAction();

    await confirm('wrong');

    expect(openDialog()).not.toBeNull();
    expect(alerts(openDialog()!)[0]).toContain('The password is incorrect.');
    expect(dialogPassword().value).toBe('');
    expect(document.activeElement).toBe(dialogPassword());
    expect(app.location()).toBe('/action');
    expect(stub.calls(ACTION)).toHaveLength(1);
  });

  it('rate_limited: the message in the dialog, no automatic retry', async () => {
    const stub = stubAction(
      [() => problem(403, 'reauth_required')],
      [() => problem(429, 'rate_limited')],
    );
    app = await renderApp('/action', pages);
    await runAction();

    await confirm();
    await settle();

    expect(alerts(openDialog()!)[0]).toContain('Too many attempts.');
    expect(stub.calls(REAUTH)).toHaveLength(1);
    expect(stub.calls(ACTION)).toHaveLength(1);
  });

  it('a network error shows in the dialog', async () => {
    stubAction(
      [() => problem(403, 'reauth_required')],
      [() => Promise.reject(new TypeError('Failed to fetch'))],
    );
    app = await renderApp('/action', pages);
    await runAction();

    await confirm();

    expect(alerts(openDialog()!)[0]).toContain("Can't reach the server.");
  });

  it('a 401 closes the dialog and ends the session (C1)', async () => {
    stubAction(
      [() => problem(403, 'reauth_required')],
      [() => problem(401, 'session_expired')],
    );
    app = await renderApp('/action', pages);
    await runAction();

    await confirm();

    expect(openDialog()).toBeNull();
    expect(app.location()).toBe('/login?next=%2Faction');
  });

  it('disables both buttons and shows the spinner while pending', async () => {
    stubAction(
      [() => problem(403, 'reauth_required')],
      [() => new Promise<Response>(() => {})],
    );
    app = await renderApp('/action', pages);
    await runAction();

    await confirm();

    const buttons = [
      ...openDialog()!.querySelectorAll<HTMLButtonElement>('form button'),
    ];
    expect(buttons.map((b) => b.disabled)).toEqual([true, true]);
    expect(openDialog()!.textContent).toContain('Confirming…');
  });

  it('without the auth provider, reauth_required reaches the caller unchanged', async () => {
    const { apiCall, ApiError } = await import('@/app/api');
    const error = await apiCall(() =>
      Promise.resolve({
        response: problem(403, 'reauth_required'),
        error: {
          type: 'about:blank',
          title: 'x',
          status: 403,
          code: 'reauth_required',
          request_id: 'r',
        },
      }),
    ).catch((caught: unknown) => caught);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as InstanceType<typeof ApiError>).code).toBe(
      'reauth_required',
    );
  });
});

describe('cancel (D4)', () => {
  it.each([
    ['Escape', () => pressEscape()],
    ['Cancel', () => click(buttonNamed(openDialog()!, 'Cancel'))],
    ['the close button', () => click(buttonNamed(openDialog()!, 'Close'))],
  ])(
    '%s rejects with reauth_required and gives focus back',
    async (_name, cancel) => {
      const stub = stubAction([() => problem(403, 'reauth_required')], []);
      app = await renderApp('/action', pages);
      await runAction();
      expect(openDialog()).not.toBeNull();

      await cancel();

      expect(openDialog()).toBeNull();
      expect(stub.calls(REAUTH)).toHaveLength(0);
      expect(stub.calls(ACTION)).toHaveLength(1);
      expect(alerts(app.container)[0]).toContain(
        'Confirm your password to continue.',
      );
      expect(document.activeElement).toBe(runButton());
    },
  );
});

describe('one dialog at a time (D5)', () => {
  it('two requests share one dialog and one reauthentication, each retried once', async () => {
    const stub = stubSignedIn({
      'GET /api/v1/test/one': [
        () => problem(403, 'reauth_required'),
        () => jsonResponse(200, {}),
      ],
      'GET /api/v1/test/two': [
        () => problem(403, 'reauth_required'),
        () => jsonResponse(200, {}),
      ],
      [REAUTH]: () => jsonResponse(200, UNTIL),
    });
    app = await renderApp('/two', pages);

    expect(document.querySelectorAll('[role="dialog"]')).toHaveLength(1);
    await confirm();

    expect(stub.calls(REAUTH)).toHaveLength(1);
    expect(stub.calls('GET /api/v1/test/one')).toHaveLength(2);
    expect(stub.calls('GET /api/v1/test/two')).toHaveLength(2);
    const states = [
      ...app.container.querySelectorAll('[data-testid="query"]'),
    ].map((p) => p.textContent);
    expect(states).toEqual(['loaded', 'loaded']);
  });

  it('a cancel rejects both', async () => {
    const stub = stubSignedIn({
      'GET /api/v1/test/one': () => problem(403, 'reauth_required'),
      'GET /api/v1/test/two': () => problem(403, 'reauth_required'),
    });
    app = await renderApp('/two', pages);

    await pressEscape();

    expect(stub.calls('GET /api/v1/test/one')).toHaveLength(1);
    expect(stub.calls('GET /api/v1/test/two')).toHaveLength(1);
    expect(alerts(app.container)).toHaveLength(2);
  });
});
