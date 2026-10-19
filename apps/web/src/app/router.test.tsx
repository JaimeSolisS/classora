import { act } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { applyLanguage } from '@/i18n';
import { resetUiStore, useUiStore } from '@/stores/ui';
import { stubSignedIn } from '@/test/api-stub';
import { buttonNamed, renderApp, type RenderedApp } from '@/test/render-app';
import { routes } from './router';

// B/H7: route tree, layout, 404, skip link, guard, nav toggle and the header
// switchers. Signed in through a stubbed /auth/me (#28 I2): every page sits
// behind the guard.

let app: RenderedApp | undefined;

const FOCUSABLE =
  'a[href], button:not([disabled]), input, select, textarea, [tabindex]:not([tabindex="-1"])';

beforeEach(() => {
  applyLanguage('en');
  stubSignedIn();
});

afterEach(() => {
  app?.unmount();
  app = undefined;
  resetUiStore();
  vi.unstubAllGlobals();
  window.localStorage.clear();
});

describe('route tree', () => {
  it('is layout → login, invite and guard → home, invitations and 404, nothing else', () => {
    expect(routes).toHaveLength(1);
    const [root] = routes;
    expect(root!.path).toBe('/');
    expect(root!.errorElement).toBeTruthy();
    const [login, invite, guard] = root!.children!;
    expect(root!.children).toHaveLength(3);
    // /login and /invite are the only pages outside the guard (#28 B1, #29 J1).
    expect(login!.path).toBe('login');
    expect(login!.children).toBeUndefined();
    expect(invite!.path).toBe('invite');
    expect(invite!.children).toBeUndefined();
    expect(invite!.errorElement).toBeTruthy();
    expect(guard!.id).toBe('guard');
    expect(guard!.path).toBeUndefined();
    expect(guard!.errorElement).toBeTruthy();
    const children = guard!.children!;
    expect(children.map((r) => (r.index ? 'index' : r.path))).toEqual([
      'index',
      'invitations',
      '*',
    ]);
    expect(children[1]!.handle).toEqual({ permission: 'user.invite' });
    expect(
      JSON.stringify(routes, (key, value: unknown) =>
        key === 'element' || key === 'errorElement' ? undefined : value,
      ),
    ).not.toMatch(/"path":"\/?(api|health)/);
  });
});

describe('home', () => {
  it('renders the home page inside the layout with the title Classora', async () => {
    app = await renderApp('/');
    const { container } = app;

    expect(container.querySelector('header')).not.toBeNull();
    expect(container.querySelector('main#main h1')?.textContent).toBe(
      'Classora',
    );
    expect(document.title).toBe('Classora');
    const home = container.querySelector('nav a[href="/"]');
    expect(home?.textContent).toBe('Home');
    expect(home?.getAttribute('aria-current')).toBe('page');
    expect(container.querySelector('nav')?.getAttribute('aria-label')).toBe(
      'Main navigation',
    );
  });

  it('renders the guard route child for a signed-in user', async () => {
    app = await renderApp('/');
    expect(app.container.querySelector('[role="status"]')).not.toBeNull();
  });
});

describe('404', () => {
  it.each(['/does-not-exist', '/a/b/c'])(
    'renders the 404 page inside the layout for %s',
    async (path) => {
      app = await renderApp(path);
      const { container } = app;

      expect(container.querySelector('header nav')).not.toBeNull();
      const main = container.querySelector('main#main');
      expect(main?.querySelector('h1')?.textContent).toBe('Page not found');
      const back = main?.querySelector('a[href="/"]');
      expect(back?.textContent).toBe('Back to home');
      expect(document.title).toBe('Page not found · Classora');
    },
  );

  it('goes back home from the 404 link', async () => {
    app = await renderApp('/nope');
    const back =
      app.container.querySelector<HTMLAnchorElement>('main a[href="/"]');

    act(() => back?.click());

    expect(app.container.querySelector('main h1')?.textContent).toBe(
      'Classora',
    );
    expect(document.title).toBe('Classora');
  });
});

describe('skip link', () => {
  it('is the first focusable element and moves focus to #main', async () => {
    app = await renderApp('/');
    const { container } = app;
    const first = container.querySelector<HTMLAnchorElement>(FOCUSABLE);

    expect(first?.textContent).toBe('Skip to main content');
    expect(first?.getAttribute('href')).toBe('#main');
    const main = container.querySelector<HTMLElement>('main');
    expect(main?.id).toBe('main');
    expect(main?.getAttribute('tabindex')).toBe('-1');

    act(() => first?.click());
    expect(document.activeElement).toBe(main);
  });
});

describe('navigation toggle', () => {
  it('flips aria-expanded and the store navOpen', async () => {
    app = await renderApp('/');
    const toggle = buttonNamed(app.container, 'Menu');
    const controlled = toggle.getAttribute('aria-controls');
    expect(controlled).toBeTruthy();
    const region = app.container.querySelector(`#${controlled}`);
    expect(region?.querySelector('nav')).not.toBeNull();
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
    expect(region?.className).toMatch(/(^|\s)hidden(\s|$)/);

    act(() => toggle.click());
    expect(toggle.getAttribute('aria-expanded')).toBe('true');
    expect(useUiStore.getState().navOpen).toBe(true);
    expect(region?.className).not.toMatch(/(^|\s)hidden(\s|$)/);

    act(() => toggle.click());
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
    expect(useUiStore.getState().navOpen).toBe(false);
  });
});

describe('header switchers', () => {
  it('has the theme and language switchers in the header', async () => {
    app = await renderApp('/');
    const groups = [
      ...app.container.querySelectorAll('header [role="group"]'),
    ].map((g) => g.getAttribute('aria-label'));
    expect(groups).toEqual(['Theme', 'Language']);
  });

  it('switches shell, page text, title and <html lang> in place', async () => {
    app = await renderApp('/missing');
    const { container } = app;
    const main = container.querySelector('main');
    expect(container.textContent).toContain('Skip to main content');

    act(() => buttonNamed(container, 'Español').click());

    expect(document.documentElement.lang).toBe('es');
    expect(container.querySelector('main')).toBe(main);
    expect(container.textContent).toContain('Saltar al contenido principal');
    expect(container.querySelector('nav')?.getAttribute('aria-label')).toBe(
      'Navegación principal',
    );
    expect(container.querySelector('nav a')?.textContent).toBe('Inicio');
    expect(buttonNamed(container, 'Menú')).toBeTruthy();
    expect(main?.querySelector('h1')?.textContent).toBe('Página no encontrada');
    expect(document.title).toBe('Página no encontrada · Classora');

    act(() => buttonNamed(container, 'English').click());
    expect(document.documentElement.lang).toBe('en');
    expect(document.title).toBe('Page not found · Classora');
  });
});
