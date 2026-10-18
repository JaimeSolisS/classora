import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import App from './App';

// Tell React this is a test environment so act() flushes updates.
(
  globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }
).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;

function statusText(): string | null {
  return container.querySelector('[role="status"]')?.textContent ?? null;
}

async function renderApp(): Promise<void> {
  await act(async () => {
    root.render(<App />);
    // Let the stubbed fetch and response.json() promises settle.
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe('App API status', () => {
  it('shows the product name and checks /health/live on the same origin', async () => {
    const fetchMock = vi.fn((request: Request) => {
      void request;
      return Promise.resolve(jsonResponse(200, { status: 'ok' }));
    });
    vi.stubGlobal('fetch', fetchMock);

    await renderApp();

    expect(container.querySelector('h1')?.textContent).toBe('Classora');
    expect(fetchMock).toHaveBeenCalledTimes(1);
    // The API client passes one Request object to fetch.
    const request = fetchMock.mock.calls[0]![0];
    const url = new URL(request.url);
    expect(request.method).toBe('GET');
    expect(url.pathname).toBe('/health/live');
    expect(url.origin).toBe(window.location.origin);
    expect(request.signal).toBeInstanceOf(AbortSignal);
  });

  it('shows "API: checking…" while the request is pending', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => new Promise<Response>(() => {})),
    );

    await renderApp();

    expect(statusText()).toBe('API: checking…');
  });

  it('shows "API: live" for 200 with {"status":"ok"}', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.resolve(jsonResponse(200, { status: 'ok' }))),
    );

    await renderApp();

    expect(statusText()).toBe('API: live');
  });

  it('shows "API: unreachable" for a 503 response', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.resolve(jsonResponse(503, { status: 'ok' }))),
    );

    await renderApp();

    expect(statusText()).toBe('API: unreachable');
  });

  it('shows "API: unreachable" for a 200 with an unexpected body', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.resolve(jsonResponse(200, { status: 'down' }))),
    );

    await renderApp();

    expect(statusText()).toBe('API: unreachable');
  });

  it('shows "API: unreachable" when fetch rejects (network error)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.reject(new TypeError('Failed to fetch'))),
    );

    await renderApp();

    expect(statusText()).toBe('API: unreachable');
  });

  it('shows "API: unreachable" when there is no response within 5 seconds', async () => {
    vi.useFakeTimers();
    vi.stubGlobal(
      'fetch',
      vi.fn(
        (request: Request) =>
          new Promise<Response>((_resolve, reject) => {
            request.signal.addEventListener('abort', () =>
              reject(new DOMException('Aborted', 'AbortError')),
            );
          }),
      ),
    );

    act(() => root.render(<App />));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(4999);
    });
    expect(statusText()).toBe('API: checking…');

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(statusText()).toBe('API: unreachable');
  });
});
