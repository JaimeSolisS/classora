import type { ApiClient } from '@classora/api-client';

// The session's CSRF token (#24, ADR-0007 D9). It lives in this one module
// variable only: never in localStorage, sessionStorage, a cookie, the URL, a
// Zustand store, a log line or the query cache (the cached session user has
// no csrf_token). It is set from the login response and from every
// /auth/me response, and cleared on every sign-out.

let csrfToken: string | null = null;

export const CSRF_HEADER = 'X-CSRF-Token';

/** The methods the API checks for the token (#24). */
export const UNSAFE_METHODS: ReadonlySet<string> = new Set([
  'POST',
  'PUT',
  'PATCH',
  'DELETE',
]);

export function getCsrfToken(): string | null {
  return csrfToken;
}

export function setCsrfToken(token: string): void {
  csrfToken = token;
}

export function clearCsrfToken(): void {
  csrfToken = null;
}

type Middleware = Parameters<ApiClient['use']>[0];

/**
 * The one openapi-fetch middleware that adds `X-CSRF-Token` to POST, PUT,
 * PATCH and DELETE while a token is held. GET and HEAD never carry it.
 * Registered once, where the app's `api` client is created (src/app/api.ts).
 */
export const csrfMiddleware: Middleware = {
  onRequest({ request }) {
    const token = csrfToken;
    if (token !== null && UNSAFE_METHODS.has(request.method.toUpperCase())) {
      request.headers.set(CSRF_HEADER, token);
    }
    return request;
  },
};
