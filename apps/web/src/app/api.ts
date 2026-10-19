import {
  createApiClient,
  type FieldError,
  type ProblemDetails,
} from '@classora/api-client';
import { csrfMiddleware } from '@/features/auth/csrf';

// Central wrapper over the typed API client (_docs/api.md "Frontend data
// layer"). Query and mutation functions call `apiCall(() => api.GET(…))`:
// they get `data` back or an `ApiError`, and never handle `{ error }` or a
// rejected request by hand. `apiCall` also ends the session on a 401,
// recovers once from `csrf_failed` and asks for the password once on
// `reauth_required` (through the auth handler below), so pages need no code
// of their own for those.

/** The app's client: same origin as the page (no API URL setting). */
export const api = createApiClient();
// The one place the CSRF header is added (#28 A4).
api.use(csrfMiddleware);

/** Error codes produced on the client, never sent by the API. */
export const CLIENT_ERROR_CODES = {
  network: 'network_error',
  unexpectedResponse: 'unexpected_response',
} as const;

interface ApiErrorInit {
  code: string;
  status?: number;
  requestId?: string;
  fieldErrors?: FieldError[];
  problem?: ProblemDetails;
}

/**
 * A failed API call. `code` is the Problem Details code (or a client code),
 * and `requestId` is the ID to quote to support. The server's `title` and
 * `detail` are never shown: the UI maps `code` to a translated message
 * (src/app/errors.ts).
 */
export class ApiError extends Error {
  readonly code: string;
  /** HTTP status, or undefined when no response was received. */
  readonly status: number | undefined;
  readonly requestId: string | undefined;
  /** Field errors of a 422 response; empty when there are none. */
  readonly fieldErrors: FieldError[];
  /** The body, only when it was valid Problem Details. */
  readonly problem: ProblemDetails | undefined;

  constructor(init: ApiErrorInit) {
    super(init.code);
    this.name = 'ApiError';
    this.code = init.code;
    this.status = init.status;
    this.requestId = init.requestId;
    this.fieldErrors = init.fieldErrors ?? [];
    this.problem = init.problem;
  }
}

const PROBLEM_CONTENT_TYPE = 'application/problem+json';

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/** Runtime shape check: the generated types are not validation. */
export function isProblemDetails(body: unknown): body is ProblemDetails {
  return (
    isRecord(body) &&
    typeof body.type === 'string' &&
    typeof body.title === 'string' &&
    typeof body.status === 'number' &&
    typeof body.code === 'string' &&
    typeof body.request_id === 'string' &&
    (body.errors === undefined || Array.isArray(body.errors))
  );
}

function mediaType(response: Response): string {
  return (response.headers.get('Content-Type') ?? '')
    .split(';')[0]!
    .trim()
    .toLowerCase();
}

/** Turns a non-2xx response (body already parsed by openapi-fetch) into an ApiError. */
export function errorFromResponse(response: Response, body: unknown): ApiError {
  if (mediaType(response) === PROBLEM_CONTENT_TYPE && isProblemDetails(body)) {
    return new ApiError({
      code: body.code,
      status: response.status,
      requestId: body.request_id,
      fieldErrors: body.errors ?? [],
      problem: body,
    });
  }
  // HTML from a proxy, an empty or text body, malformed JSON, or a Problem
  // body that fails the shape check.
  return new ApiError({
    code: CLIENT_ERROR_CODES.unexpectedResponse,
    status: response.status,
    requestId: response.headers.get('X-Request-ID') ?? undefined,
  });
}

function isAbort(error: unknown): boolean {
  return (
    typeof error === 'object' &&
    error !== null &&
    'name' in error &&
    error.name === 'AbortError'
  );
}

interface ClientResult<T> {
  data?: T;
  error?: unknown;
  response: Response;
}

/** The two 401 codes that mean "this browser has no valid session" (#25). */
export const SESSION_END_CODES: ReadonlySet<string> = new Set([
  'authentication_required',
  'session_expired',
]);

/** A 401 that ends the session (not `invalid_credentials`). */
export function isSessionEnd(error: unknown): boolean {
  return (
    error instanceof ApiError &&
    error.status === 401 &&
    SESSION_END_CODES.has(error.code)
  );
}

/**
 * What `apiCall` asks of the signed-in app. The auth provider
 * (src/features/auth/AuthProvider.tsx) registers itself while it is mounted;
 * without it, every `ApiError` reaches the caller unchanged.
 */
export interface AuthHandler {
  /** A 401 `authentication_required` or `session_expired` (#28 C1). */
  sessionEnded(error: ApiError): void;
  /**
   * After `403 csrf_failed`: refetch /auth/me (a new token). True when the
   * same user is still signed in and the request may be retried (#28 C3).
   */
  refreshAfterCsrfFailure(): Promise<boolean>;
  /**
   * After `403 reauth_required`: the password dialog. True when the user
   * confirmed their password and the request may be retried (#28 D).
   */
  reauthenticate(): Promise<boolean>;
}

let authHandler: AuthHandler | null = null;

/** Registers (or, with null, removes) the auth handler. */
export function setAuthHandler(handler: AuthHandler | null): void {
  authHandler = handler;
}

export interface ApiCallOptions {
  /** Cancels the call; an abort is re-thrown unchanged. */
  signal?: AbortSignal;
  /**
   * Whether a 401 `authentication_required`/`session_expired` ends the
   * session (C1). Off for the session query and logout, which handle their
   * own 401. Default true.
   */
  endSessionOn401?: boolean;
  /** Whether `csrf_failed` refetches /auth/me and retries once (C3). Default true. */
  retryCsrf?: boolean;
  /** Whether `reauth_required` opens the password dialog (D). Default true. */
  reauthenticate?: boolean;
}

/** For calls that must see every error as is (login, the session query). */
export const NO_AUTH_HANDLING: ApiCallOptions = {
  endSessionOn401: false,
  retryCsrf: false,
  reauthenticate: false,
};

/**
 * Awaits one openapi-fetch call and returns its `data`, or throws an
 * `ApiError`. An abort is re-thrown unchanged, so TanStack Query treats it as
 * a cancellation. Never logs or exposes the response body.
 *
 * With the auth provider mounted (#28): a 401 that ends the session notifies
 * it before the error is thrown; `csrf_failed` refetches /auth/me and calls
 * `call` again once (never when the user changed); `reauth_required` waits for
 * the password dialog and calls `call` again once. A second `csrf_failed` or
 * `reauth_required` reaches the caller.
 */
export async function apiCall<T>(
  call: () => Promise<ClientResult<T>>,
  signalOrOptions?: AbortSignal | ApiCallOptions,
): Promise<T> {
  const options: ApiCallOptions =
    signalOrOptions instanceof AbortSignal
      ? { signal: signalOrOptions }
      : (signalOrOptions ?? {});
  const {
    signal,
    endSessionOn401 = true,
    retryCsrf = true,
    reauthenticate = true,
  } = options;
  let csrfRetried = false;
  let reauthRetried = false;
  for (;;) {
    try {
      return await callOnce(call, signal);
    } catch (error) {
      const handler = authHandler;
      if (!(error instanceof ApiError) || handler === null) {
        throw error;
      }
      if (isSessionEnd(error)) {
        if (endSessionOn401) {
          handler.sessionEnded(error);
        }
        throw error;
      }
      if (
        error.code === 'csrf_failed' &&
        retryCsrf &&
        !csrfRetried &&
        !signal?.aborted
      ) {
        csrfRetried = true;
        if (await handler.refreshAfterCsrfFailure()) {
          continue;
        }
        throw error;
      }
      if (
        error.code === 'reauth_required' &&
        reauthenticate &&
        !reauthRetried &&
        !signal?.aborted
      ) {
        reauthRetried = true;
        if (await handler.reauthenticate()) {
          continue;
        }
        throw error;
      }
      throw error;
    }
  }
}

async function callOnce<T>(
  call: () => Promise<ClientResult<T>>,
  signal: AbortSignal | undefined,
): Promise<T> {
  let result: ClientResult<T>;
  try {
    result = await call();
  } catch (error) {
    if (isAbort(error) || signal?.aborted) {
      throw error;
    }
    if (error instanceof TypeError) {
      // The request never got a response: offline, DNS, connection refused.
      throw new ApiError({ code: CLIENT_ERROR_CODES.network });
    }
    if (error instanceof SyntaxError) {
      // A 2xx body that openapi-fetch could not parse as JSON.
      throw new ApiError({ code: CLIENT_ERROR_CODES.unexpectedResponse });
    }
    throw error;
  }
  const { response } = result;
  if (!response.ok) {
    throw errorFromResponse(response, result.error);
  }
  return result.data as T;
}
