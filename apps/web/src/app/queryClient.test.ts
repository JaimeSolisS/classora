import { MutationObserver, QueryObserver } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from './api';
import {
  createQueryClient,
  isExpectedQueryError,
  MAX_QUERY_RETRIES,
  shouldRetryQuery,
} from './queryClient';

// C2/C3 (H1/H2): queries retry only network failures and 5xx, at most twice;
// mutations never retry.

function httpError(status: number, code = 'some_code'): ApiError {
  return new ApiError({ code, status, requestId: `req-${status}` });
}

const networkError = () => new ApiError({ code: 'network_error' });

beforeEach(() => {
  // The caches report every failure; keep the output clean.
  vi.spyOn(console, 'error').mockImplementation(() => {});
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('shouldRetryQuery', () => {
  it.each([400, 401, 403, 404, 409, 422, 429])(
    'never retries a %i',
    (status) => {
      for (const count of [0, 1, 2, 3]) {
        expect(shouldRetryQuery(count, httpError(status))).toBe(false);
      }
    },
  );

  it.each([
    ['500', () => httpError(500, 'internal_error')],
    ['502', () => httpError(502, 'unexpected_response')],
    ['503', () => httpError(503, 'database_unavailable')],
    ['network_error', networkError],
  ])('retries a %s at counts 0 and 1, not at 2', (_name, make) => {
    expect(MAX_QUERY_RETRIES).toBe(2);
    expect(shouldRetryQuery(0, make())).toBe(true);
    expect(shouldRetryQuery(1, make())).toBe(true);
    expect(shouldRetryQuery(2, make())).toBe(false);
    expect(shouldRetryQuery(3, make())).toBe(false);
  });

  it.each([
    ['an Error', new Error('bug')],
    ['a TypeError', new TypeError('bug')],
    ['an abort', new DOMException('Aborted', 'AbortError')],
    ['a string', 'boom'],
    ['undefined', undefined],
  ])('never retries %s (not an ApiError)', (_name, error) => {
    expect(shouldRetryQuery(0, error)).toBe(false);
  });

  it('never retries an ApiError without a status that is not a network error', () => {
    expect(
      shouldRetryQuery(0, new ApiError({ code: 'unexpected_response' })),
    ).toBe(false);
  });
});

describe('createQueryClient queries', () => {
  async function runQuery(
    error: ApiError,
    meta?: Record<string, unknown>,
  ): Promise<number> {
    const client = createQueryClient();
    const queryFn = vi.fn(() => Promise.reject(error));
    const observer = new QueryObserver(client, {
      queryKey: ['test', error.status],
      queryFn,
      retryDelay: 0,
      meta,
    });
    const unsubscribe = observer.subscribe(() => {});
    await vi.waitFor(() =>
      expect(observer.getCurrentResult().status).toBe('error'),
    );
    unsubscribe();
    client.clear();
    return queryFn.mock.calls.length;
  }

  it('calls the query function once for a 404', async () => {
    expect(await runQuery(httpError(404, 'not_found'))).toBe(1);
  });

  it('calls the query function three times for a 503', async () => {
    expect(await runQuery(httpError(503, 'database_unavailable'))).toBe(3);
  });

  it('reports a failed query once, through reportError', async () => {
    await runQuery(httpError(404, 'not_found'));
    const reports = vi
      .mocked(console.error)
      .mock.calls.filter((call) => call[0] === '[classora]');
    expect(reports).toHaveLength(1);
    expect(reports[0]![1]).toMatchObject({ source: 'query' });
    expect(reports[0]![2]).toEqual({
      code: 'not_found',
      status: 404,
      requestId: 'req-404',
    });
  });
});

// #315 QA D1: a query whose meta lists a status as expected (a designed
// state such as "This report is no longer available.") isn't reported for
// it; any other failure of that query still is.
describe('expected query errors (#315 QA D1)', () => {
  const reports = () =>
    vi
      .mocked(console.error)
      .mock.calls.filter((call) => call[0] === '[classora]');

  async function run(error: ApiError, meta?: Record<string, unknown>) {
    const client = createQueryClient();
    const observer = new QueryObserver(client, {
      queryKey: ['expected', error.status],
      queryFn: () => Promise.reject(error),
      retry: false,
      meta,
    });
    const unsubscribe = observer.subscribe(() => {});
    await vi.waitFor(() =>
      expect(observer.getCurrentResult().status).toBe('error'),
    );
    unsubscribe();
    client.clear();
  }

  it('does not report an expected 404', async () => {
    await run(httpError(404, 'not_found'), { expectedStatuses: [404] });
    expect(reports()).toHaveLength(0);
  });

  it.each([
    ['a 500', 500, 'internal_error'],
    ['a 403', 403, 'student_consent_required'],
  ])('still reports %s on the same query', async (_name, status, code) => {
    await run(httpError(status, code), { expectedStatuses: [404] });
    expect(reports()).toHaveLength(1);
  });

  it('reports a 404 without the meta, and non-ApiErrors', async () => {
    await run(httpError(404, 'not_found'));
    expect(reports()).toHaveLength(1);
    expect(
      isExpectedQueryError(new Error('x'), { expectedStatuses: [404] }),
    ).toBe(false);
    expect(
      isExpectedQueryError(httpError(404), { expectedStatuses: 'x' }),
    ).toBe(false);
  });
});

describe('createQueryClient mutations', () => {
  it.each([
    ['a 503', () => httpError(503, 'database_unavailable')],
    ['a network error', networkError],
  ])('call mutationFn exactly once when it fails with %s', async (_n, make) => {
    const client = createQueryClient();
    const mutationFn = vi.fn(() => Promise.reject(make()));
    const observer = new MutationObserver(client, {
      mutationFn,
      retryDelay: 0,
    });

    await expect(observer.mutate()).rejects.toBeInstanceOf(ApiError);
    // Give a (wrong) retry the chance to run.
    await new Promise((resolve) => setTimeout(resolve, 20));

    expect(mutationFn).toHaveBeenCalledTimes(1);
    expect(observer.getCurrentResult().status).toBe('error');
    client.clear();
  });
});
