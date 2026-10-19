import { act } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { applyLanguage } from '@/i18n';
import {
  noContent,
  problem,
  stubSignedIn,
  stubSignedOut,
  type ApiStub,
  type Responder,
} from '@/test/api-stub';
import { alerts, click, fieldByLabel, typeInto } from '@/test/dom';
import {
  buttonNamed,
  jsonResponse,
  renderApp,
  type RenderedApp,
} from '@/test/render-app';

// #169 F1–F4 and H2: the public /register page, its link from /login, the
// signed-in notice, the form, server errors and the rule that the password
// is never retained. The password below is a low-entropy test value.

const SUBMIT = 'POST /api/v1/auth/registrations';
const PASSWORD = 'a-long-enough-password';

let app: RenderedApp | undefined;

const main = () => app!.container.querySelector('main')!;
const h1 = () => main().querySelector('h1')?.textContent;
const field = (label: string) => fieldByLabel(main(), label);
const form = () => main().querySelector('form');
const statusText = () =>
  main().querySelector('[role="status"]')?.textContent ?? '';
const linkNamed = (name: string) =>
  [...main().querySelectorAll('a')].find((a) => a.textContent === name);

function stubRegister(
  submit: Responder | Responder[] = () =>
    jsonResponse(202, { status: 'pending' }),
): ApiStub {
  return stubSignedOut({ [SUBMIT]: submit });
}

function fill({
  role = "I'm a parent or guardian",
  name = 'Rosa Díaz',
  username = 'Rosa.Diaz ',
  password = PASSWORD,
  confirm = password,
}: {
  role?: string | null;
  name?: string;
  username?: string;
  password?: string;
  confirm?: string;
} = {}) {
  if (role) {
    act(() => field(role).click());
  }
  typeInto(field('Your name'), name);
  typeInto(field('Username'), username);
  typeInto(field('Password'), password);
  typeInto(field('Confirm password'), confirm);
}

async function send() {
  await click(buttonNamed(main(), 'Send request'));
}

function cacheText(): string {
  const queries = app!.queryClient
    .getQueryCache()
    .getAll()
    .map((q) => ({ key: q.queryKey, data: q.state.data }));
  const mutations = app!.queryClient
    .getMutationCache()
    .getAll()
    .map((m) => ({ vars: m.state.variables, data: m.state.data }));
  return JSON.stringify({ queries, mutations });
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
  window.localStorage.clear();
  window.sessionStorage.clear();
});

describe('route and links (F1)', () => {
  it('is a public page with one h1, the title, the intro and a link back to /login', async () => {
    const api = stubRegister();

    app = await renderApp('/register');

    expect(app.location()).toBe('/register');
    expect(main().querySelectorAll('h1')).toHaveLength(1);
    expect(h1()).toBe('Request an account');
    expect(document.title).toBe('Request an account · Classora');
    expect(main().textContent).toContain(
      "Requesting an account doesn't enroll anyone in classes.",
    );
    expect(app.container.querySelector('header nav')).toBeNull();
    expect(api.calls(SUBMIT)).toHaveLength(0);

    await click(linkNamed('Back to sign in')!);
    expect(app.location()).toBe('/login');
  });

  it('the login page links to /register', async () => {
    stubRegister();
    app = await renderApp('/login');

    await click(linkNamed('Request an account')!);

    expect(app.location()).toBe('/register');
    expect(h1()).toBe('Request an account');
  });
});

describe('signed in (F2)', () => {
  it('shows the notice with a link home and makes no request', async () => {
    const api = stubSignedIn({
      [SUBMIT]: () => jsonResponse(202, { status: 'pending' }),
      'POST /api/v1/auth/logout': noContent,
    });

    app = await renderApp('/register');

    expect(h1()).toBe('Request an account');
    expect(main().textContent).toContain(
      "You're already signed in as Demo Owner.",
    );
    expect(form()).toBeNull();
    expect(api.calls(SUBMIT)).toHaveLength(0);
    await click(linkNamed('Go to home')!);
    expect(app.location()).toBe('/');
    expect(api.calls(SUBMIT)).toHaveLength(0);
  });
});

describe('the form (F3, H2)', () => {
  it('has a role fieldset with nothing chosen and labelled fields with the right autocomplete', async () => {
    stubRegister();
    app = await renderApp('/register');

    const fieldset = main().querySelector('fieldset')!;
    expect(fieldset.querySelector('legend')?.textContent).toBe('Who are you?');
    const radios = [
      ...fieldset.querySelectorAll<HTMLInputElement>('input[type="radio"]'),
    ];
    expect(radios.map((r) => r.value)).toEqual(['guardian', 'student']);
    expect(radios.every((r) => !r.checked)).toBe(true);
    expect(field("I'm a student").type).toBe('radio');
    expect(field('Your name').autocomplete).toBe('name');
    expect(field('Username').autocomplete).toBe('username');
    expect(field('Password').autocomplete).toBe('new-password');
    expect(field('Confirm password').autocomplete).toBe('new-password');
    expect(main().textContent).toContain(
      '3 to 64 characters: letters, digits, dot, hyphen or underscore.',
    );
    expect(form()!.getAttribute('method')).toBe('post');
    expect(form()!.noValidate).toBe(true);
  });

  it('validates without a request: role, name, username, length and match', async () => {
    const api = stubRegister();
    app = await renderApp('/register');

    await send();

    expect(main().textContent).toContain('Choose who you are.');
    expect(main().textContent).toContain('Enter your name.');
    expect(main().textContent).toContain('Enter a username.');
    expect(main().textContent).toContain('Enter a password.');
    expect(field("I'm a parent or guardian").getAttribute('aria-invalid')).toBe(
      'true',
    );
    expect(
      main().querySelector('fieldset')!.getAttribute('aria-describedby'),
    ).toBeTruthy();
    expect(document.activeElement).toBe(field("I'm a parent or guardian"));

    fill({ password: 'short', confirm: 'short' });
    await send();
    expect(main().textContent).toContain('Use at least 12 characters.');
    expect(field('Password').getAttribute('aria-invalid')).toBe('true');

    fill({ password: 'p'.repeat(129) });
    await send();
    expect(main().textContent).toContain('Use at most 128 characters.');

    fill({ confirm: 'something-else-entirely' });
    await send();
    expect(main().textContent).toContain("The passwords don't match.");
    expect(api.calls(SUBMIT)).toHaveLength(0);
  });

  it('sends the request with the chosen role and shows the confirmation', async () => {
    const api = stubRegister();
    app = await renderApp('/register');

    fill({ role: "I'm a student" });
    await send();

    const [call] = api.calls(SUBMIT);
    expect(call!.body).toEqual({
      username: 'Rosa.Diaz',
      display_name: 'Rosa Díaz',
      password: PASSWORD,
      role: 'student',
    });
    // A public route: the Origin check only, no CSRF token (#24).
    expect(form()).toBeNull();
    expect(statusText()).toBe(
      'Request sent. The center will confirm your identity. You can sign in with rosa.diaz once it is approved.',
    );
    expect(linkNamed('Back to sign in')?.getAttribute('href')).toBe('/login');
    expect(api.calls(SUBMIT)).toHaveLength(1);
  });

  it('sends one request on a double click and shows the spinner while pending', async () => {
    let answer: (response: Response) => void = () => {};
    const api = stubRegister(
      () =>
        new Promise<Response>((resolve) => {
          answer = resolve;
        }),
    );
    app = await renderApp('/register');
    fill();

    act(() => buttonNamed(main(), 'Send request').click());
    act(() => main().querySelector<HTMLButtonElement>('form button')!.click());
    await click(main().querySelector<HTMLButtonElement>('form button')!);

    expect(api.calls(SUBMIT)).toHaveLength(1);
    const button = main().querySelector<HTMLButtonElement>('form button')!;
    expect(button.disabled).toBe(true);
    expect(button.textContent).toContain('Sending…');
    await act(async () => {
      answer(jsonResponse(202, { status: 'pending' }));
      await Promise.resolve();
    });
  });
});

describe('server errors (F4)', () => {
  it.each([
    ['username_taken', 409, 'That username is already taken.'],
    [
      'username_invalid',
      422,
      'Use 3 to 64 characters: letters, digits, dot, hyphen or underscore.',
    ],
  ])('%s attaches to the username field', async (code, status, message) => {
    stubRegister(() => problem(status, code));
    app = await renderApp('/register');
    fill();

    await send();

    expect(field('Username').getAttribute('aria-invalid')).toBe('true');
    expect(main().textContent).toContain(message);
    expect(alerts(main())).toEqual([]);
    expect(document.activeElement).toBe(field('Username'));
    expect(form()).not.toBeNull();
  });

  it.each([
    ['password_too_short', 'The password must have at least 12 characters.'],
    ['password_too_long', 'The password is too long. Use a shorter one.'],
    [
      'password_equals_username',
      "The password can't be the same as the username.",
    ],
  ])('%s attaches to the password field', async (code, message) => {
    stubRegister(() => problem(422, code));
    app = await renderApp('/register');
    fill();

    await send();

    expect(field('Password').getAttribute('aria-invalid')).toBe('true');
    expect(main().textContent).toContain(message);
    expect(alerts(main())).toEqual([]);
    expect(document.activeElement).toBe(field('Password'));
  });

  it.each([
    [
      429,
      'rate_limited',
      'Too many attempts. Wait a few minutes and try again.',
    ],
    [
      500,
      'internal_error',
      'Something went wrong on the server. Please try again later.',
    ],
    [422, 'validation_error', 'Check the highlighted fields and try again.'],
  ])('%s %s shows in the alert', async (status, code, message) => {
    stubRegister(() => problem(status, code));
    app = await renderApp('/register');
    fill();

    await send();

    const [alert] = alerts(main());
    expect(alert).toContain(message);
    expect(form()).not.toBeNull();
    expect(statusText()).toBe('');
  });
});

describe('the password is never retained (F4)', () => {
  it('is in no query or mutation cache entry and no storage', async () => {
    stubRegister([
      () => problem(409, 'username_taken'),
      () => jsonResponse(202, { status: 'pending' }),
    ]);
    app = await renderApp('/register');
    fill();

    await send();
    typeInto(field('Username'), 'rosa.diaz2');
    await send();

    expect(form()).toBeNull();
    const text = cacheText();
    expect(text).not.toContain(PASSWORD);
    const storage = JSON.stringify({
      local: { ...window.localStorage },
      session: { ...window.sessionStorage },
    });
    expect(storage).not.toContain(PASSWORD);
  });
});

describe('language switch (H2)', () => {
  it('keeps typed values and the chosen role', async () => {
    const api = stubRegister();
    app = await renderApp('/register');
    fill({ role: "I'm a student" });

    act(() => applyLanguage('es'));

    expect(h1()).toBe('Solicitar una cuenta');
    expect(fieldByLabel(main(), 'Soy estudiante').checked).toBe(true);
    expect(fieldByLabel(main(), 'Tu nombre').value).toBe('Rosa Díaz');
    expect(fieldByLabel(main(), 'Contraseña').value).toBe(PASSWORD);
    await click(buttonNamed(main(), 'Enviar solicitud'));

    expect(api.calls(SUBMIT)[0]!.body).toMatchObject({ role: 'student' });
  });
});
