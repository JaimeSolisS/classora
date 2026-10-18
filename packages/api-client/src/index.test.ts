import { describe, expect, it, vi } from 'vitest';
import { createApiClient, type ProblemDetails } from './index';

const ORIGIN = 'http://api.test';

function respond(status: number, body: unknown, contentType: string) {
  return vi.fn((request: Request) => {
    void request;
    return Promise.resolve(
      new Response(JSON.stringify(body), {
        status,
        headers: { 'Content-Type': contentType },
      }),
    );
  });
}

describe('createApiClient', () => {
  it('sends one GET to /health/live on the configured origin', async () => {
    const fetch = respond(200, { status: 'ok' }, 'application/json');
    const client = createApiClient({ baseUrl: ORIGIN, fetch });

    const { data, error, response } = await client.GET('/health/live');

    expect(fetch).toHaveBeenCalledTimes(1);
    const request = fetch.mock.calls[0]![0];
    expect(request.method).toBe('GET');
    expect(request.url).toBe(`${ORIGIN}/health/live`);
    expect(response.status).toBe(200);
    expect(error).toBeUndefined();
    const status: 'ok' | undefined = data?.status;
    expect(status).toBe('ok');
    expect(data).toEqual({ status: 'ok' });
  });

  it('returns a 503 Problem Details body as a typed error', async () => {
    const problem: ProblemDetails = {
      type: 'about:blank',
      title: 'Service Unavailable',
      status: 503,
      code: 'database_unavailable',
      request_id: 'req-123',
      detail: 'Database is not reachable.',
    };
    const fetch = respond(503, problem, 'application/problem+json');
    const client = createApiClient({ baseUrl: ORIGIN, fetch });

    const { data, error, response } = await client.GET('/health/ready');

    expect(response.status).toBe(503);
    expect(data).toBeUndefined();
    const typed: ProblemDetails | undefined = error;
    expect(typed?.code).toBe('database_unavailable');
    expect(typed?.request_id).toBe('req-123');
    expect(typed).toEqual(problem);
  });

  it('looks up globalThis.fetch on every call when no fetch is given', async () => {
    const fetch = respond(200, { status: 'ok' }, 'application/json');
    const client = createApiClient({ baseUrl: ORIGIN });
    vi.stubGlobal('fetch', fetch);
    try {
      await client.GET('/health/live');
    } finally {
      vi.unstubAllGlobals();
    }

    expect(fetch).toHaveBeenCalledTimes(1);
  });
});
