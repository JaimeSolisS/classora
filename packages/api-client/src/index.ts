// The typed Classora API client (spec §10 API contracts, _docs/api.md).
//
// The types come from src/generated/schema.ts, which `make api-client`
// generates from openapi.json; this file is the only hand-written runtime code.
// TanStack Query hooks are written by hand per feature on top of this client.
import createClient, { type Client, type ClientOptions } from 'openapi-fetch';
import type { components, paths } from './generated/schema';

export type { components, operations, paths } from './generated/schema';

/** RFC 9457 body of every API error (`application/problem+json`). */
export type ProblemDetails = components['schemas']['ProblemDetails'];
/** One item of `ProblemDetails.errors` (422 validation errors). */
export type FieldError = components['schemas']['FieldError'];

export type ApiClient = Client<paths>;

export interface ApiClientOptions {
  /**
   * Origin of the API. Defaults to the page's own origin: the SPA and the API
   * share one origin (no CORS, no API URL setting). Pass it outside a browser,
   * where a relative URL cannot build a `Request`.
   */
  baseUrl?: string;
  /** Defaults to `globalThis.fetch`, looked up on every call. */
  fetch?: ClientOptions['fetch'];
}

export function createApiClient(options: ApiClientOptions = {}): ApiClient {
  return createClient<paths>({
    baseUrl: options.baseUrl ?? currentOrigin(),
    fetch: options.fetch ?? ((request) => globalThis.fetch(request)),
  });
}

function currentOrigin(): string | undefined {
  return typeof location === 'undefined' ? undefined : location.origin;
}
