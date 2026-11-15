// The dev server's proxy for the live-notification stream (#42 F2, QA D1).
//
// Vite's proxy pipes the API's response to the browser, but when the API
// aborts a response that already started (a restart cancels the open
// streams), it never ends the browser's connection: an EventSource would
// stay OPEN on a dead stream, with the bell's polling off. For this path
// only, the browser's connection is destroyed when the upstream response
// closes before it completed, or the proxy fails after the headers were
// sent. The browser then reconnects (with Last-Event-ID) or, when the API
// is still down, gets an error and falls back to polling.
//
// Typed structurally so this file needs no Node types (vite.config.ts and
// the unit tests both import it).

export const NOTIFICATION_STREAM_PATH = '/api/v1/notifications/stream';

interface Listenable {
  on(event: string, listener: (...args: never[]) => void): unknown;
}

interface UpstreamResponse extends Listenable {
  readonly complete: boolean;
}

interface ClientResponse {
  readonly headersSent?: boolean;
  readonly destroyed?: boolean;
  destroy(): unknown;
}

function isClientResponse(value: unknown): value is ClientResponse {
  return (
    typeof value === 'object' &&
    value !== null &&
    typeof (value as { destroy?: unknown }).destroy === 'function'
  );
}

function destroy(response: ClientResponse) {
  if (!response.destroyed) {
    response.destroy();
  }
}

function isListenable(value: unknown): value is Listenable {
  return (
    typeof value === 'object' &&
    value !== null &&
    typeof (value as { on?: unknown }).on === 'function'
  );
}

/**
 * The proxy's `configure` hook for {@link NOTIFICATION_STREAM_PATH}. Takes
 * `unknown` because Vite's `ProxyServer` type needs Node types.
 */
export function endStreamWithUpstream(proxy: unknown): void {
  if (!isListenable(proxy)) {
    throw new TypeError('the proxy must be an event emitter');
  }
  proxy.on(
    'proxyRes',
    (upstream: UpstreamResponse, _request: unknown, response: unknown) => {
      if (!isClientResponse(response)) {
        return;
      }
      upstream.on('close', () => {
        if (!upstream.complete) {
          destroy(response);
        }
      });
    },
  );
  proxy.on('error', (_error: unknown, _request: unknown, response: unknown) => {
    // Before the headers Vite answers the error itself; after them, only
    // closing the connection tells the browser.
    if (isClientResponse(response) && response.headersSent) {
      destroy(response);
    }
  });
}
