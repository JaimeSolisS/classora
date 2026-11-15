import { act } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { applyLanguage } from '@/i18n';
import {
  meAs,
  problem,
  stubApi,
  TEST_USER,
  type ApiStub,
} from '@/test/api-stub';
import { QueryPage } from '@/test/auth-pages';
import { routesWith } from '@/test/auth-routes';
import { alerts, click } from '@/test/dom';
import {
  buttonNamed,
  jsonResponse,
  renderApp,
  settle,
  type RenderedApp,
} from '@/test/render-app';
import { paragraphsOf } from './api';
import { NOTICE_V1, NOTICE_V1_ACCEPTED, NOTICE_V2 } from './fixtures';

// #174 G1–G4: the guard's redirect, the notice page, the acceptance, the
// 409 refetch, the reread state, the language switch and the apiCall
// refetch on 403 privacy_notice_required.

let app: RenderedApp | undefined;

const ME = 'GET /api/v1/auth/me';
const NOTICE = 'GET /api/v1/privacy-notice';
const ACCEPT = 'POST /api/v1/privacy-notice/acceptance';
const PENDING_USER = { ...TEST_USER, privacy_notice_required: true };

const h1 = () => app?.container.querySelector('main h1');
const nav = () => app?.container.querySelector('nav');
const mainText = () => app?.container.querySelector('main')?.textContent ?? '';
const statusText = () =>
  app?.container.querySelector('main [role="status"]')?.textContent ?? '';
const textRegion = () =>
  app?.container.querySelector<HTMLElement>('main [role="region"]');

function stubNotice(extra: Parameters<typeof stubApi>[0] = {}): ApiStub {
  return stubApi({
    [ME]: [meAs(PENDING_USER), meAs(TEST_USER)],
    [NOTICE]: () => jsonResponse(200, NOTICE_V1),
    ...extra,
  });
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
  applyLanguage('en');
});

describe('the guard (G1)', () => {
  it('sends every page to the notice with next, replacing the entry', async () => {
    stubNotice();

    app = await renderApp('/users?q=ana#top');

    expect(app.location()).toBe(
      '/privacy-notice?next=%2Fusers%3Fq%3Dana%23top',
    );
    expect(app.router.state.historyAction).toBe('REPLACE');
    expect(h1()?.textContent).toBe('Privacy notice');
    expect(document.activeElement).toBe(h1());
    expect(document.title).toBe('Privacy notice · Classora');
  });

  it('shows no navigation items on the notice, but the user menu', async () => {
    stubNotice();

    app = await renderApp('/');

    expect(nav()).toBeNull();
    expect(app.container.querySelector('header')?.textContent).toContain(
      'Sign out',
    );
  });

  it('does not redirect when the notice is accepted', async () => {
    stubApi({ [ME]: meAs(TEST_USER) });

    app = await renderApp('/');

    expect(app.location()).toBe('/');
    expect(nav()).not.toBeNull();
  });
});

describe('the page (G2)', () => {
  it('shows the version, the date and the text of the UI language as paragraphs', async () => {
    stubNotice();

    app = await renderApp('/privacy-notice');

    expect(mainText()).toContain('Version 1, published Oct 17, 2026');
    expect(mainText()).toContain(
      'Read the privacy notice. You must accept it to use Classora.',
    );
    const region = textRegion()!;
    expect(region.getAttribute('aria-label')).toBe('Privacy notice text');
    expect(region.tabIndex).toBe(0);
    expect([...region.querySelectorAll('p')].map((p) => p.textContent)).toEqual(
      paragraphsOf(NOTICE_V1.body_en),
    );
    expect(region.querySelector('p')?.textContent).toBe(
      'Draft pending legal review.',
    );
  });

  it('renders the text as plain text, never as HTML', async () => {
    stubNotice({
      [NOTICE]: () =>
        jsonResponse(200, {
          ...NOTICE_V1,
          body_en:
            '<b>bold</b> **not markdown**\n\n<img src=x onerror=alert(1)>',
        }),
    });

    app = await renderApp('/privacy-notice');

    const region = textRegion()!;
    expect(region.querySelector('b, img')).toBeNull();
    expect(region.textContent).toContain('<b>bold</b> **not markdown**');
  });

  it('switching the language shows the other text without a request', async () => {
    const stub = stubNotice();
    app = await renderApp('/privacy-notice');

    act(() => applyLanguage('es'));
    await settle();

    expect(h1()?.textContent).toBe('Aviso de privacidad');
    expect(textRegion()?.querySelector('p')?.textContent).toBe(
      'Borrador pendiente de revisión legal.',
    );
    expect(mainText()).toContain('Versión 1, publicada el 17 oct 2026');
    expect(stub.calls(NOTICE)).toHaveLength(1);
  });

  it('"I accept" sends the version shown, refetches the session and returns to next', async () => {
    const stub = stubNotice({
      [ACCEPT]: () =>
        jsonResponse(200, { version: 1, accepted_at: '2026-10-19T16:30:00Z' }),
    });
    app = await renderApp('/users?q=ana');
    expect(stub.calls(ME)).toHaveLength(1);

    await click(buttonNamed(app.container, 'I accept'));

    expect(stub.calls(ACCEPT).map((c) => c.body)).toEqual([{ version: 1 }]);
    expect(stub.calls(ACCEPT)[0]!.headers.get('X-CSRF-Token')).toBe(
      TEST_USER.csrf_token,
    );
    expect(stub.calls(ME)).toHaveLength(2);
    expect(app.location()).toBe('/users?q=ana');
    expect(app.router.state.historyAction).toBe('REPLACE');
  });

  it('an unsafe next falls back to /', async () => {
    stubNotice({
      [ACCEPT]: () =>
        jsonResponse(200, { version: 1, accepted_at: '2026-10-19T16:30:00Z' }),
    });
    app = await renderApp('/privacy-notice?next=%2F%2Fevil.example');

    await click(buttonNamed(app.container, 'I accept'));

    expect(app.location()).toBe('/');
  });

  it('409 refetches the notice and says it changed', async () => {
    const stub = stubNotice({
      [NOTICE]: [
        () => jsonResponse(200, NOTICE_V1),
        () => jsonResponse(200, NOTICE_V2),
      ],
      [ACCEPT]: [
        () => problem(409, 'privacy_notice_outdated'),
        () =>
          jsonResponse(200, {
            version: 2,
            accepted_at: '2026-11-02T16:00:00Z',
          }),
      ],
    });
    app = await renderApp('/privacy-notice');
    expect(statusText()).toBe('');

    await click(buttonNamed(app.container, 'I accept'));

    expect(stub.calls(NOTICE)).toHaveLength(2);
    expect(statusText()).toBe(
      'The notice changed. Please read the new version.',
    );
    expect(mainText()).toContain('Version 2');
    expect(textRegion()?.textContent).toContain('The new notice text.');
    expect(alerts(app.container)).toEqual([]);
    expect(app.location()).toBe('/privacy-notice');

    await click(buttonNamed(app.container, 'I accept'));

    expect(stub.calls(ACCEPT).map((c) => c.body)).toEqual([
      { version: 1 },
      { version: 2 },
    ]);
    expect(app.location()).toBe('/');
  });

  it('another error shows an alert and keeps the button', async () => {
    stubNotice({ [ACCEPT]: () => problem(500, 'internal_error') });
    app = await renderApp('/privacy-notice');

    await click(buttonNamed(app.container, 'I accept'));

    expect(alerts(app.container)).toHaveLength(1);
    expect(buttonNamed(app.container, 'I accept')).toBeTruthy();
    expect(app.location()).toBe('/privacy-notice');
  });

  it('a failed load shows the error with Try again', async () => {
    stubNotice({ [NOTICE]: () => problem(400, 'bad_request') });

    app = await renderApp('/privacy-notice');

    expect(alerts(app.container)).toHaveLength(1);
    expect(buttonNamed(app.container, 'Try again')).toBeTruthy();
  });

  it('once accepted, the menu link rereads the notice with its date and no button', async () => {
    const stub = stubApi({
      [ME]: meAs(TEST_USER),
      [NOTICE]: () => jsonResponse(200, NOTICE_V1_ACCEPTED),
    });
    app = await renderApp('/');
    const link = [...app.container.querySelectorAll('header a')].find(
      (a) => a.textContent === 'Privacy notice',
    ) as HTMLAnchorElement;

    await click(link);

    expect(app.location()).toBe('/privacy-notice');
    expect(mainText()).toContain('Accepted on Oct 19, 2026');
    expect(mainText()).not.toContain('You must accept it');
    expect(() => buttonNamed(app!.container, 'I accept')).toThrow();
    expect(stub.calls(ACCEPT)).toHaveLength(0);
  });
});

describe('apiCall (G3)', () => {
  const three = routesWith([
    {
      path: 'three',
      element: (
        <>
          <QueryPage path="/api/v1/test/one" />
          <QueryPage path="/api/v1/test/two" />
          <QueryPage path="/api/v1/test/three" />
        </>
      ),
    },
  ]);

  it('403 privacy_notice_required refetches the session once, then the guard redirects', async () => {
    const gated = () => problem(403, 'privacy_notice_required');
    const stub = stubApi({
      [ME]: [meAs(TEST_USER), meAs(PENDING_USER)],
      [NOTICE]: () => jsonResponse(200, NOTICE_V1),
      'GET /api/v1/test/one': gated,
      'GET /api/v1/test/two': gated,
      'GET /api/v1/test/three': gated,
    });

    app = await renderApp('/three', three);

    expect(stub.calls(ME)).toHaveLength(2);
    // Never retried.
    expect(stub.calls('GET /api/v1/test/one')).toHaveLength(1);
    expect(stub.calls('GET /api/v1/test/two')).toHaveLength(1);
    expect(stub.calls('GET /api/v1/test/three')).toHaveLength(1);
    expect(app.location()).toBe('/privacy-notice?next=%2Fthree');
  });

  it('a 403 permission_denied refetches nothing', async () => {
    const stub = stubApi({
      [ME]: meAs(TEST_USER),
      'GET /api/v1/test/one': () => problem(403, 'permission_denied'),
      'GET /api/v1/test/two': () => problem(403, 'permission_denied'),
      'GET /api/v1/test/three': () => problem(403, 'permission_denied'),
    });

    app = await renderApp('/three', three);

    expect(stub.calls(ME)).toHaveLength(1);
    expect(app.location()).toBe('/three');
  });
});

describe('paragraphsOf', () => {
  it('splits on blank lines, keeps single line breaks and drops empty parts', () => {
    expect(paragraphsOf('a\nb\n\n  \n\nc\r\n\r\nd\n')).toEqual([
      'a\nb',
      'c',
      'd',
    ]);
  });
});
