import {
  createApiClient,
  type FieldError,
  type ProblemDetails,
} from '@classora/api-client';

// Central wrapper over the typed API client (_docs/api.md "Frontend data
// layer"). Query and mutation functions call `apiCall(() => api.GET(…))`:
// they get `data` back or an `ApiError`, and never handle `{ error }` or a
// rejected request by hand.

/** The app's client: same origin as the page (no API URL setting). */
export const api = createApiClient();

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

/**
 * Awaits one openapi-fetch call and returns its `data`, or throws an
 * `ApiError`. An abort is re-thrown unchanged, so TanStack Query treats it as
 * a cancellation. Never logs or exposes the response body.
 */
export async function apiCall<T>(
  call: () => Promise<ClientResult<T>>,
  signal?: AbortSignal,
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
