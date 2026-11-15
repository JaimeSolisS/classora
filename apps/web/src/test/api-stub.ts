import { vi, type Mock } from 'vitest';
import type { SessionUserBody } from '@/features/auth/session';
import { jsonResponse } from './render-app';

// Test helpers for the API behind a stubbed fetch (#28). The app's client
// calls fetch with one Request object; a route answers by method and path.
// Anything without a route stays pending forever (no network in tests).

/** A signed-in Owner-like user with every permission the tests need. */
export const TEST_USER: SessionUserBody = {
  id: '00000000-0000-4000-8000-000000000001',
  username: 'demo.owner',
  display_name: 'Demo Owner',
  roles: ['owner'],
  permissions: ['user.view', 'audit.view'],
  csrf_token: 'csrf-token-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
  privacy_notice_required: false,
  // Never stored (#36): the app sends the local values once (F3); the PATCH
  // stays pending unless a test answers it.
  preferences: { language: null, theme: null },
  center_time_zone: 'America/Mexico_City',
};

export const OTHER_USER: SessionUserBody = {
  id: '00000000-0000-4000-8000-000000000002',
  username: 'other.user',
  display_name: 'Other User',
  roles: ['tutor'],
  permissions: [],
  csrf_token: 'csrf-token-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
  privacy_notice_required: false,
  preferences: { language: null, theme: null },
  center_time_zone: 'America/Mexico_City',
};

export function problem(
  status: number,
  code: string,
  requestId = `req-${code}`,
): Response {
  return new Response(
    JSON.stringify({
      type: 'about:blank',
      title: 'Problem',
      status,
      code,
      request_id: requestId,
      detail: 'Server detail that must never reach the UI',
    }),
    { status, headers: { 'Content-Type': 'application/problem+json' } },
  );
}

export const noContent = () => new Response(null, { status: 204 });

export type Responder = (
  request: Request,
  body: unknown,
) => Response | Promise<Response>;

/** `'GET /api/v1/auth/me'` → a responder, or a queue of them (used in order; the last repeats). */
export type Routes = Record<string, Responder | Responder[]>;

export interface RecordedRequest {
  method: string;
  path: string;
  headers: Headers;
  body: unknown;
}

export interface ApiStub {
  fetch: Mock<(request: Request) => Promise<Response>>;
  requests: RecordedRequest[];
  /** Recorded requests for `'METHOD /path'`. */
  calls: (route: string) => RecordedRequest[];
  /** Replaces or adds routes. */
  set: (routes: Routes) => void;
}

const pending = () => new Promise<Response>(() => {});

/** Stubs fetch with routes; returns the mock and the recorded requests. */
export function stubApi(initial: Routes = {}): ApiStub {
  const routes: Record<string, Responder[]> = {};
  const used: Record<string, number> = {};
  const requests: RecordedRequest[] = [];
  const set = (more: Routes) => {
    for (const [key, value] of Object.entries(more)) {
      routes[key] = Array.isArray(value) ? value : [value];
      used[key] = 0;
    }
  };
  set(initial);
  const fetch = vi.fn(async (request: Request) => {
    const url = new URL(request.url);
    const key = `${request.method} ${url.pathname}`;
    const text = request.body ? await request.clone().text() : '';
    const body: unknown = text ? JSON.parse(text) : undefined;
    requests.push({
      method: request.method,
      path: url.pathname,
      headers: request.headers,
      body,
    });
    const queue = routes[key];
    if (!queue || queue.length === 0) {
      return pending();
    }
    const index = Math.min(used[key] ?? 0, queue.length - 1);
    used[key] = (used[key] ?? 0) + 1;
    return queue[index]!(request, body);
  });
  vi.stubGlobal('fetch', fetch);
  return {
    fetch,
    requests,
    calls: (route) => {
      const [method, path] = route.split(' ');
      return requests.filter((r) => r.method === method && r.path === path);
    },
    set,
  };
}

/** GET /auth/me answers 200 with `user`. */
export const meAs =
  (user: SessionUserBody = TEST_USER): Responder =>
  () =>
    jsonResponse(200, user);

/** Signed in as TEST_USER, plus any extra routes. */
export function stubSignedIn(extra: Routes = {}): ApiStub {
  return stubApi({ 'GET /api/v1/auth/me': meAs(), ...extra });
}

/** Signed out (/auth/me 401 authentication_required), plus extra routes. */
export function stubSignedOut(extra: Routes = {}): ApiStub {
  return stubApi({
    'GET /api/v1/auth/me': () => problem(401, 'authentication_required'),
    ...extra,
  });
}
