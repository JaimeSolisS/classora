import { MutationCache, QueryCache, QueryClient } from '@tanstack/react-query';
import { ApiError, CLIENT_ERROR_CODES } from './api';
import { reportError } from './errors';

/** Retries after the first failure: 3 attempts in total. */
export const MAX_QUERY_RETRIES = 2;

/**
 * Query retry policy (spec §10 "safe retries"): retry only a network failure
 * or a 5xx response, at most MAX_QUERY_RETRIES times, with TanStack Query's
 * default exponential back-off. Never retry a 4xx, a cancellation or any
 * value that is not an ApiError (a programming error).
 */
export function shouldRetryQuery(
  failureCount: number,
  error: unknown,
): boolean {
  if (!(error instanceof ApiError)) {
    return false;
  }
  const { status } = error;
  const retryable =
    error.code === CLIENT_ERROR_CODES.network ||
    (status !== undefined && status >= 500 && status < 600);
  return retryable && failureCount < MAX_QUERY_RETRIES;
}

/**
 * A query's own expected outcomes (#315 QA D1): `meta: { expectedStatuses:
 * [404] }` marks HTTP statuses that the page shows as a designed state (a
 * family's "This report is no longer available."), so the cache doesn't
 * report them as errors. Every other failure is still reported.
 */
export interface ExpectedErrorsMeta {
  expectedStatuses?: readonly number[];
}

/** Whether `error` is one of the query's expected outcomes. */
export function isExpectedQueryError(error: unknown, meta: unknown): boolean {
  if (!(error instanceof ApiError) || error.status === undefined) {
    return false;
  }
  const statuses = (meta as ExpectedErrorsMeta | undefined)?.expectedStatuses;
  return Array.isArray(statuses) && statuses.includes(error.status);
}

/**
 * A new client with the app defaults. The app creates exactly one at
 * startup; each test creates its own.
 */
export function createQueryClient(): QueryClient {
  return new QueryClient({
    queryCache: new QueryCache({
      onError: (error, query) => {
        if (isExpectedQueryError(error, query.meta)) {
          return;
        }
        reportError(error, { source: 'query', queryKey: query.queryKey });
      },
    }),
    mutationCache: new MutationCache({
      onError: (error, _variables, _context, mutation) =>
        reportError(error, {
          source: 'mutation',
          mutationKey: mutation.options.mutationKey,
        }),
    }),
    defaultOptions: {
      queries: { retry: shouldRetryQuery },
      // Mutations never retry: a repeated write could apply twice. Financial
      // mutations are never optimistic (architecture §8).
      mutations: { retry: false },
    },
  });
}
