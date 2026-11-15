import { act } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { applyLanguage } from '@/i18n';
import {
  meAs,
  stubApi,
  stubSignedOut,
  TEST_USER,
  type ApiStub,
} from '@/test/api-stub';
import {
  jsonResponse,
  renderApp,
  settle,
  type RenderedApp,
} from '@/test/render-app';
import { NOTIFICATION_POLL_MS, UNREAD_COUNT_KEY } from './api';
import {
  NOTIFICATION_STREAM_URL,
  STREAM_RETRY_MS,
} from './useLiveNotifications';

// #42 E1–E3: the live stream with a fake EventSource. Events invalidate the
// notification queries; polling is off while the stream is open and back
// (30 s) while it is connecting or closed; a closed stream is retried after
// 60 s; no stream signed out or before the privacy notice; unmount closes it.

const COUNT = 'GET /api/v1/notifications/unread-count';
const LIST = 'GET /api/v1/notifications';

class FakeEventSource {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSED = 2;
  static instances: FakeEventSource[] = [];

  readyState = FakeEventSource.CONNECTING;
  closed = false;
  private listeners = new Map<string, ((event: Event) => void)[]>();

  constructor(readonly url: string) {
    FakeEventSource.instances.push(this);
  }

  addEventListener(type: string, listener: (event: Event) => void) {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener]);
  }

  close() {
    this.closed = true;
    this.readyState = FakeEventSource.CLOSED;
  }

  emit(type: string) {
    act(() => {
      for (const listener of this.listeners.get(type) ?? []) {
        listener(new Event(type));
      }
    });
  }

  open() {
    this.readyState = FakeEventSource.OPEN;
    this.emit('open');
  }

  fail(readyState: number) {
    this.readyState = readyState;
    this.emit('error');
  }
}

let app: RenderedApp | undefined;

const sources = () => FakeEventSource.instances;
const latest = () => sources().at(-1)!;
const bellWrapper = () =>
  app!.container.querySelector<HTMLElement>('[data-slot="notification-bell"]');
const pollInterval = () =>
  app!.queryClient.getQueryCache().find({ queryKey: [...UNREAD_COUNT_KEY] })
    ?.observers[0]?.options.refetchInterval;

function stub(user = TEST_USER): ApiStub {
  return stubApi({
    'GET /api/v1/auth/me': meAs(user),
    [COUNT]: () => jsonResponse(200, { count: 1 }),
    [LIST]: () => jsonResponse(200, { items: [], next_cursor: null }),
  });
}

beforeEach(() => {
  applyLanguage('en');
  FakeEventSource.instances = [];
  vi.stubGlobal('EventSource', FakeEventSource);
  vi.spyOn(console, 'error').mockImplementation(() => {});
});

afterEach(() => {
  app?.unmount();
  app = undefined;
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe('the live stream (E1)', () => {
  it('opens one same-origin stream and turns polling off while open', async () => {
    stub();
    app = await renderApp('/');

    expect(sources()).toHaveLength(1);
    expect(latest().url).toBe(NOTIFICATION_STREAM_URL);
    expect(NOTIFICATION_STREAM_URL).toBe('/api/v1/notifications/stream');
    // Connecting: polling still runs.
    expect(bellWrapper()?.dataset.live).toBe('connecting');
    expect(pollInterval()).toBe(NOTIFICATION_POLL_MS);

    latest().open();

    expect(bellWrapper()?.dataset.live).toBe('open');
    expect(pollInterval()).toBe(false);
  });

  it.each(['notification', 'resync'])(
    'a %s event refetches the count',
    async (type) => {
      const api = stub();
      app = await renderApp('/');
      latest().open();
      await settle();
      const before = api.calls(COUNT).length;

      latest().emit(type);
      await settle();

      expect(api.calls(COUNT).length).toBe(before + 1);
    },
  );

  it('a notification event refetches the open panel’s list', async () => {
    const api = stub();
    app = await renderApp('/');
    latest().open();
    act(() => {
      app!.container
        .querySelector<HTMLButtonElement>(
          'header button[aria-haspopup="dialog"]',
        )!
        .click();
    });
    await settle();
    const before = api.calls(LIST).length;
    expect(before).toBeGreaterThan(0);

    latest().emit('notification');
    await settle();

    expect(api.calls(LIST).length).toBe(before + 1);
  });

  it('is never opened when signed out', async () => {
    stubSignedOut({});
    app = await renderApp('/login');

    expect(sources()).toHaveLength(0);
  });

  it('is never opened before the privacy notice is accepted', async () => {
    stub({ ...TEST_USER, privacy_notice_required: true });
    app = await renderApp('/');

    expect(sources()).toHaveLength(0);
  });

  it('is closed on unmount', async () => {
    stub();
    app = await renderApp('/');
    const source = latest();

    app.unmount();
    app = undefined;

    expect(source.closed).toBe(true);
  });
});

describe('the polling fallback (E2)', () => {
  it('polls while the browser reconnects by itself', async () => {
    stub();
    app = await renderApp('/');
    latest().open();

    latest().fail(FakeEventSource.CONNECTING);

    expect(bellWrapper()?.dataset.live).toBe('connecting');
    expect(pollInterval()).toBe(NOTIFICATION_POLL_MS);
    latest().open();
    expect(pollInterval()).toBe(false);
    expect(sources()).toHaveLength(1);
  });

  it('polls after a closed stream and tries a new one every 60 s', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    stub();
    app = await renderApp('/');
    const first = latest();

    first.fail(FakeEventSource.CLOSED);

    expect(first.closed).toBe(true);
    expect(bellWrapper()?.dataset.live).toBe('closed');
    expect(pollInterval()).toBe(NOTIFICATION_POLL_MS);
    expect(sources()).toHaveLength(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(STREAM_RETRY_MS);
    });

    expect(STREAM_RETRY_MS).toBe(60_000);
    expect(sources()).toHaveLength(2);
    latest().open();
    expect(pollInterval()).toBe(false);
  });

  it('only polls without EventSource', async () => {
    vi.stubGlobal('EventSource', undefined);
    stub();
    app = await renderApp('/');

    expect(sources()).toHaveLength(0);
    expect(bellWrapper()?.dataset.live).toBe('off');
    expect(pollInterval()).toBe(NOTIFICATION_POLL_MS);
  });
});
