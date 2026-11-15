import { createApiClient } from '@classora/api-client';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { api } from '@/app/api';
import {
  clearCsrfToken,
  CSRF_HEADER,
  csrfMiddleware,
  getCsrfToken,
  setCsrfToken,
} from './csrf';

// A3/A4: the token lives in memory only and the one middleware adds it to
// unsafe methods, never to GET or HEAD.

const TOKEN = 'token-1234567890123456789012345678901234567890';

function capturingClient() {
  const fetchMock = vi.fn((request: Request) => {
    void request;
    return Promise.resolve(new Response(null, { status: 204 }));
  });
  const client = createApiClient({
    baseUrl: 'http://api.test',
    fetch: fetchMock,
  });
  client.use(csrfMiddleware);
  return { client, fetchMock };
}

async function headerFor(method: string): Promise<string | null> {
  const { client, fetchMock } = capturingClient();
  // Every method through the same client code path; the schema has no PUT,
  // PATCH or DELETE route yet, so the path is typed loosely here.
  const request = client.request as unknown as (
    method: string,
    path: string,
  ) => Promise<unknown>;
  await request(method.toLowerCase(), '/api/v1/auth/logout');
  return fetchMock.mock.calls[0]![0].headers.get(CSRF_HEADER);
}

afterEach(() => {
  clearCsrfToken();
  vi.unstubAllGlobals();
});

describe('csrf middleware', () => {
  it.each(['POST', 'PUT', 'PATCH', 'DELETE'])(
    'adds X-CSRF-Token to %s',
    async (method) => {
      setCsrfToken(TOKEN);
      expect(await headerFor(method)).toBe(TOKEN);
    },
  );

  it.each(['GET', 'HEAD'])('never adds it to %s', async (method) => {
    setCsrfToken(TOKEN);
    expect(await headerFor(method)).toBeNull();
  });

  it('adds nothing while no token is held', async () => {
    expect(getCsrfToken()).toBeNull();
    expect(await headerFor('POST')).toBeNull();
  });

  it('is registered once on the app client', async () => {
    const fetchMock = vi.fn((request: Request) => {
      void request;
      return Promise.resolve(new Response(null, { status: 204 }));
    });
    vi.stubGlobal('fetch', fetchMock);
    setCsrfToken(TOKEN);

    await api.POST('/api/v1/auth/logout');
    await api.GET('/api/v1/auth/me');

    const [post, get] = fetchMock.mock.calls.map(([request]) => request);
    expect(post!.headers.get(CSRF_HEADER)).toBe(TOKEN);
    expect(get!.headers.get(CSRF_HEADER)).toBeNull();
    // Exactly one header value: the middleware ran once.
    expect(post!.headers.get(CSRF_HEADER)?.split(',')).toHaveLength(1);
  });
});
