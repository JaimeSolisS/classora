import type { components } from '@classora/api-client';
import {
  api,
  ApiError,
  apiCall,
  isSessionEnd,
  NO_AUTH_HANDLING,
} from '@/app/api';
import { clearCsrfToken, setCsrfToken } from './csrf';
import type { LoginNoticeState } from './loginNotice';

// The session state of the web app (#28 A): one TanStack query,
// ['auth', 'me'], that asks GET /api/v1/auth/me who is signed in.

/** The SessionUser body of login and /auth/me (with its csrf_token). */
export type SessionUserBody = components['schemas']['SessionUser'];
/** A permission code of the #20 catalog (generated from the API, F3). */
export type PermissionCode = components['schemas']['PermissionCode'];
/** A role code (generated from the API, F3). */
export type RoleCode = components['schemas']['RoleCode'];

/** The signed-in user as the app keeps it: never with the CSRF token (A3). */
export type SessionUser = Omit<SessionUserBody, 'csrf_token'>;

/**
 * Why the app is signed out. `session_expired` shows the notice on the login
 * page (B7); `signed_out` (the user pressed "Sign out") sends the guard to
 * /login without `next`. Absent for `authentication_required` and first
 * visits.
 */
export type SignedOutReason = 'session_expired' | 'signed_out';

/** The cached value of the session query. */
export type SessionData =
  | { user: SessionUser }
  | {
      user: null;
      reason?: SignedOutReason;
      /**
       * A login notice for the guard's redirect (#170 G4: after changing your
       * own roles); it goes into the router state, never the URL.
       */
      notice?: LoginNoticeState;
    };

export type Session =
  | { status: 'loading' }
  | { status: 'signedIn'; user: SessionUser }
  | { status: 'signedOut'; reason?: SignedOutReason; notice?: LoginNoticeState }
  | { status: 'error'; error: unknown };

export const SESSION_QUERY_KEY = ['auth', 'me'] as const;

/**
 * No polling (A2): the query has no refetchInterval, so an idle tab never
 * keeps its session alive past the idle limit (#25). It refetches on window
 * focus once older than this.
 */
export const SESSION_STALE_TIME_MS = 60_000;

/** Keeps the CSRF token and returns the user without it (A3). */
export function acceptSessionUser(body: SessionUserBody): SessionUser {
  setCsrfToken(body.csrf_token);
  return {
    id: body.id,
    username: body.username,
    display_name: body.display_name,
    roles: body.roles,
    permissions: body.permissions,
  };
}

/**
 * The session query function. Both 401 codes resolve to "signed out" (not a
 * query error, never retried); network errors and 5xx are thrown and follow
 * the query retry policy.
 */
export async function fetchSession(signal?: AbortSignal): Promise<SessionData> {
  try {
    const body = await apiCall(() => api.GET('/api/v1/auth/me', { signal }), {
      ...NO_AUTH_HANDLING,
      signal,
    });
    return { user: acceptSessionUser(body) };
  } catch (error) {
    if (error instanceof ApiError && isSessionEnd(error)) {
      clearCsrfToken();
      return error.code === 'session_expired'
        ? { user: null, reason: 'session_expired' }
        : { user: null };
    }
    throw error;
  }
}

/** The login path that returns to `location` after signing in. */
export function loginPath(location: {
  pathname: string;
  search: string;
  hash: string;
}): string {
  const next = `${location.pathname}${location.search}${location.hash}`;
  return `/login?${new URLSearchParams({ next }).toString()}`;
}
