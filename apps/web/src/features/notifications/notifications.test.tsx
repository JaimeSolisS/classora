import { act } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { applyLanguage } from '@/i18n';
import type { SessionUserBody } from '@/features/auth/session';
import {
  meAs,
  problem,
  stubApi,
  stubSignedOut,
  TEST_USER,
  type ApiStub,
  type Responder,
} from '@/test/api-stub';
import { click, pressEscape } from '@/test/dom';
import {
  jsonResponse,
  renderApp,
  settle,
  type RenderedApp,
} from '@/test/render-app';
import {
  NOTIFICATION_POLL_MS,
  UNREAD_COUNT_KEY,
  type NotificationItem,
} from './api';
import { badgeText, notificationMessageKey } from './messages';

// #41 E1–E5: the header bell, its unread count (polled), the panel with the
// messages from i18n keys in es and en, mark one read, mark all as read,
// "Load more" and the empty and error states.

const COUNT = 'GET /api/v1/notifications/unread-count';
const LIST = 'GET /api/v1/notifications';
const READ_ALL = 'POST /api/v1/notifications/read-all';

const item = (
  id: string,
  overrides: Partial<NotificationItem> = {},
): NotificationItem => ({
  id,
  notification_type: 'dev.demo',
  params: { label: `Label ${id}` },
  created_at: '2026-10-20T15:30:00Z',
  read_at: null,
  ...overrides,
});

const NEWEST = item('00000000-0000-4000-8000-0000000000a3');
const UNKNOWN = item('00000000-0000-4000-8000-0000000000a2', {
  notification_type: 'billing.invoice_overdue',
  params: { invoice: 'F-1' },
});
const OLD_READ = item('00000000-0000-4000-8000-0000000000a1', {
  read_at: '2026-10-19T10:00:00Z',
  params: { label: 'Seen' },
});

const count = (n: number) => () => jsonResponse(200, { count: n });
const page = (items: NotificationItem[], next: string | null = null) =>
  jsonResponse(200, { items, next_cursor: next });

let app: RenderedApp | undefined;

function stub(
  routes: Record<string, Responder | Responder[]> = {},
  user: SessionUserBody = TEST_USER,
): ApiStub {
  return stubApi({
    'GET /api/v1/auth/me': meAs(user),
    [COUNT]: count(3),
    [LIST]: () => page([NEWEST, UNKNOWN, OLD_READ]),
    ...routes,
  });
}

const bell = () =>
  app!.container.querySelector<HTMLButtonElement>(
    'header button[aria-haspopup="dialog"]',
  );
const badge = () =>
  bell()?.parentElement?.querySelector('[data-slot="notification-badge"]')
    ?.textContent ?? null;
const panel = () =>
  document.querySelector<HTMLElement>('[data-slot="popover-content"]');
const itemButtons = () => [
  ...(panel()?.querySelectorAll<HTMLButtonElement>('li button') ?? []),
];
const texts = () => itemButtons().map((button) => button.textContent);
const panelButton = (name: string) => {
  const found = [...(panel()?.querySelectorAll('button') ?? [])].find(
    (button) => button.textContent === name,
  );
  if (!found) {
    throw new Error(`No panel button "${name}"`);
  }
  return found;
};

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
  vi.useRealTimers();
});

describe('the bell (E1, E2)', () => {
  it('shows the unread count in its name and badge', async () => {
    stub();

    app = await renderApp('/');

    expect(bell()?.getAttribute('aria-label')).toBe('Notifications (3 unread)');
    expect(badge()).toBe('3');
    expect(bell()?.getAttribute('aria-expanded')).toBe('false');
  });

  it('has no badge and a plain name without unread ones', async () => {
    stub({ [COUNT]: count(0) });

    app = await renderApp('/');

    expect(bell()?.getAttribute('aria-label')).toBe('Notifications');
    expect(badge()).toBeNull();
  });

  it('shows 99+ above 99, in Spanish too', async () => {
    applyLanguage('es');
    stub({ [COUNT]: count(120) });

    app = await renderApp('/');

    expect(badge()).toBe('99+');
    expect(bell()?.getAttribute('aria-label')).toBe(
      'Notificaciones (99+ sin leer)',
    );
    expect([0, 1, 99, 100].map(badgeText)).toEqual(['0', '1', '99', '99+']);
  });

  it('polls every 30 s and refetches on focus', async () => {
    stub();

    app = await renderApp('/');

    const query = app.queryClient
      .getQueryCache()
      .find({ queryKey: [...UNREAD_COUNT_KEY] });
    expect(NOTIFICATION_POLL_MS).toBe(30_000);
    expect(query?.observers).toHaveLength(1);
    expect(query?.observers[0]?.options.refetchInterval).toBe(30_000);
    expect(query?.observers[0]?.options.refetchOnWindowFocus).toBe(true);
  });

  it('asks again after the interval', async () => {
    const api = stub();
    vi.useFakeTimers({ shouldAdvanceTime: true });
    app = await renderApp('/');
    const before = api.calls(COUNT).length;
    expect(before).toBe(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(NOTIFICATION_POLL_MS);
    });
    await settle();

    expect(api.calls(COUNT).length).toBe(before + 1);
  });

  it('is not shown and never polls when signed out', async () => {
    const api = stubSignedOut({ [COUNT]: count(3) });

    app = await renderApp('/login');

    expect(bell()).toBeNull();
    expect(api.calls(COUNT)).toHaveLength(0);
    expect(
      app.queryClient.getQueryCache().find({ queryKey: [...UNREAD_COUNT_KEY] }),
    ).toBeUndefined();
  });

  it('is not shown before the privacy notice is accepted', async () => {
    const api = stub({}, { ...TEST_USER, privacy_notice_required: true });

    app = await renderApp('/');

    expect(bell()).toBeNull();
    expect(api.calls(COUNT)).toHaveLength(0);
  });
});

describe('the panel (E3, E4)', () => {
  it('opens with the newest 10 and renders messages from i18n keys', async () => {
    const api = stub();
    app = await renderApp('/');

    await click(bell()!);

    expect(bell()?.getAttribute('aria-expanded')).toBe('true');
    const heading = panel()?.querySelector('h2');
    expect(heading?.textContent).toBe('Notifications');
    expect(panel()?.getAttribute('aria-labelledby')).toBe(heading?.id);
    const [request] = api.calls(LIST);
    expect(request).toBeDefined();
    const url = new URL(
      api.fetch.mock.calls.find(
        ([r]) => new URL(r.url).pathname === '/api/v1/notifications',
      )![0].url,
    );
    expect(url.searchParams.get('limit')).toBe('10');
    expect(url.searchParams.has('cursor')).toBe(false);
    const [first, second, third] = itemButtons();
    expect(first?.textContent).toContain(
      `Demo notification: Label ${NEWEST.id}`,
    );
    expect(first?.textContent).toContain('Unread');
    expect(first?.querySelector('time')?.getAttribute('dateTime')).toBe(
      NEWEST.created_at,
    );
    // The center zone (America/Mexico_City, UTC-6): 15:30Z is 09:30.
    expect(first?.querySelector('time')?.textContent).toBe(
      'Oct 20, 2026, 9:30 AM',
    );
    expect(second?.textContent).toContain('You have a new notification.');
    expect(second?.textContent).not.toContain('billing');
    expect(third?.textContent).toContain('Demo notification: Seen');
    expect(third?.textContent).toContain('Read');
    expect(third?.textContent).not.toContain('Unread');
  });

  it('renders in Spanish and re-renders on a language switch', async () => {
    applyLanguage('es');
    stub();
    app = await renderApp('/');
    await click(bell()!);

    expect(texts()[0]).toContain(`Notificación de prueba: Label ${NEWEST.id}`);
    expect(texts()[0]).toContain('No leída');
    expect(texts()[1]).toContain('Tienes una notificación nueva.');
    expect(panel()?.querySelector('h2')?.textContent).toBe('Notificaciones');

    act(() => applyLanguage('en'));
    await settle();

    expect(texts()[0]).toContain(`Demo notification: Label ${NEWEST.id}`);
    expect(texts()[1]).toContain('You have a new notification.');
  });

  it('the message key replaces every dot', () => {
    expect(notificationMessageKey('staff.qualification_approved')).toBe(
      'notifications.types.staff_qualification_approved',
    );
    expect(notificationMessageKey('a.b.c')).toBe('notifications.types.a_b_c');
  });

  it('never lets a param change the language', async () => {
    stub({
      [LIST]: () =>
        page([item(NEWEST.id, { params: { label: 'Safe', lng: 'es' } })]),
    });
    app = await renderApp('/');
    await click(bell()!);

    expect(texts()[0]).toContain('Demo notification: Safe');
  });

  it('never lets a param skip the interpolation', async () => {
    stub({
      [LIST]: () =>
        page([
          item(NEWEST.id, {
            params: { label: 'Interpolated', skipInterpolation: true },
          }),
        ]),
    });
    app = await renderApp('/');
    await click(bell()!);

    expect(texts()[0]).toContain('Demo notification: Interpolated');
    expect(texts()[0]).not.toContain('{{label}}');
  });

  it('activating an unread item marks it read and lowers the count', async () => {
    const api = stub({
      [`POST /api/v1/notifications/${NEWEST.id}/read`]: () =>
        jsonResponse(200, { ...NEWEST, read_at: '2026-10-20T16:00:00Z' }),
      // After the read, the refreshed count stays pending: the cache shows 2.
      [COUNT]: [count(3), () => new Promise<Response>(() => {})],
    });
    app = await renderApp('/');
    await click(bell()!);

    await click(itemButtons()[0]!);

    expect(
      api.calls(`POST /api/v1/notifications/${NEWEST.id}/read`),
    ).toHaveLength(1);
    expect(texts()[0]).toContain('Read');
    expect(texts()[0]).not.toContain('Unread');
    expect(badge()).toBe('2');
    expect(app.queryClient.getQueryData([...UNREAD_COUNT_KEY])).toBe(2);
  });

  it('activating a read item sends nothing', async () => {
    const api = stub();
    app = await renderApp('/');
    await click(bell()!);

    await click(itemButtons()[2]!);

    expect(
      api.requests.filter(
        (r) => r.method === 'POST' && r.path.endsWith('/read'),
      ),
    ).toHaveLength(0);
  });

  it('"Mark all as read" sends the newest loaded id', async () => {
    const api = stub({
      [READ_ALL]: () => jsonResponse(200, { updated: 2 }),
    });
    app = await renderApp('/');
    await click(bell()!);
    api.set({ [COUNT]: count(0) });

    await click(panelButton('Mark all as read'));

    expect(api.calls(READ_ALL).map((r) => r.body)).toEqual([
      { up_to: NEWEST.id },
    ]);
    expect(texts().every((text) => !text?.includes('Unread'))).toBe(true);
    expect(badge()).toBeNull();
    expect(panelButton('Mark all as read').disabled).toBe(true);
  });

  it('"Load more" asks for the next page with the cursor', async () => {
    const api = stub({
      [LIST]: [() => page([NEWEST], 'cursor-1'), () => page([OLD_READ])],
    });
    app = await renderApp('/');
    await click(bell()!);
    expect(texts()).toHaveLength(1);

    await click(panelButton('Load more'));

    const urls = api.fetch.mock.calls
      .map(([r]) => new URL(r.url))
      .filter((url) => url.pathname === '/api/v1/notifications');
    expect(urls.at(-1)?.searchParams.get('cursor')).toBe('cursor-1');
    expect(texts()).toHaveLength(2);
    expect(() => panelButton('Load more')).toThrow();
  });

  it('shows the empty state', async () => {
    stub({ [COUNT]: count(0), [LIST]: () => page([]) });
    app = await renderApp('/');

    await click(bell()!);

    expect(panel()?.textContent).toContain('No notifications yet.');
    expect(() => panelButton('Mark all as read')).toThrow();
  });

  it('shows the loading state, then an error with a retry', async () => {
    const api = stub({
      [LIST]: [() => problem(403, 'permission_denied'), () => page([NEWEST])],
    });
    app = await renderApp('/');

    await click(bell()!);

    expect(panel()?.querySelector('[role="alert"]')).not.toBeNull();
    await click(panelButton('Try again'));
    expect(api.calls(LIST)).toHaveLength(2);
    expect(texts()).toHaveLength(1);
  });

  it('shows loading while the list is pending', async () => {
    stub({ [LIST]: () => new Promise<Response>(() => {}) });
    app = await renderApp('/');

    await click(bell()!);

    expect(panel()?.querySelector('[aria-busy="true"]')?.textContent).toContain(
      'Loading notifications…',
    );
  });

  it('Escape closes the panel and returns focus to the bell', async () => {
    stub();
    app = await renderApp('/');
    await click(bell()!);
    expect(panel()).not.toBeNull();

    await pressEscape();

    expect(panel()).toBeNull();
    expect(document.activeElement).toBe(bell());
  });
});
