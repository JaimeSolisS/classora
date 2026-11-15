import { describe, expect, it, vi } from 'vitest';
import { endStreamWithUpstream } from './dev-proxy';

// #42 QA D1: the dev proxy ends the browser's connection to the live stream
// when the API aborts its response (a restart), and leaves a response that
// ended normally alone. Fakes stand in for http-proxy's emitter and Node's
// responses (the web app has no Node types).

type Listener = (...args: never[]) => void;

class FakeEmitter {
  private listeners = new Map<string, Listener[]>();

  on(event: string, listener: Listener) {
    this.listeners.set(event, [...(this.listeners.get(event) ?? []), listener]);
    return this;
  }

  emit(event: string, ...args: unknown[]) {
    for (const listener of this.listeners.get(event) ?? []) {
      (listener as (...values: unknown[]) => void)(...args);
    }
  }
}

class FakeUpstream extends FakeEmitter {
  complete = false;
}

function clientResponse(headersSent = true) {
  const response = {
    headersSent,
    destroyed: false,
    destroy: vi.fn(() => {
      response.destroyed = true;
    }),
  };
  return response;
}

function proxied() {
  const proxy = new FakeEmitter();
  endStreamWithUpstream(proxy);
  const upstream = new FakeUpstream();
  const response = clientResponse();
  proxy.emit('proxyRes', upstream, {}, response);
  return { proxy, upstream, response };
}

describe('the live-stream dev proxy', () => {
  it('ends the browser connection when the upstream stream is aborted', () => {
    const { upstream, response } = proxied();

    upstream.emit('close');

    expect(response.destroy).toHaveBeenCalledTimes(1);
  });

  it('leaves a stream that ended normally alone', () => {
    const { upstream, response } = proxied();

    upstream.complete = true;
    upstream.emit('close');

    expect(response.destroy).not.toHaveBeenCalled();
  });

  it('ends the browser connection on a proxy error after the headers', () => {
    const proxy = new FakeEmitter();
    endStreamWithUpstream(proxy);
    const started = clientResponse(true);
    const notStarted = clientResponse(false);

    proxy.emit('error', new Error('socket hang up'), {}, started);
    proxy.emit('error', new Error('connect ECONNREFUSED'), {}, notStarted);

    expect(started.destroy).toHaveBeenCalledTimes(1);
    // Before the headers, Vite answers the error itself.
    expect(notStarted.destroy).not.toHaveBeenCalled();
  });

  it('needs an event emitter', () => {
    expect(() => endStreamWithUpstream({})).toThrow(TypeError);
  });
});
