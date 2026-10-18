import { createApiClient } from '@classora/api-client';
import { describe, expect, it, vi } from 'vitest';
import { ApiError, apiCall, isProblemDetails } from './api';

// C4 / H3: the central helper over the real #12 client (openapi-fetch 0.17)
// with a mocked fetch.

const BASE_URL = 'http://api.test';

function clientReturning(response: Response | Promise<Response>) {
  const fetchMock = vi.fn((request: Request) => {
    void request;
    return Promise.resolve(response);
  });
  return { api: createApiClient({ baseUrl: BASE_URL, fetch: fetchMock }) };
}

function clientRejecting(error: Error) {
  return {
    api: createApiClient({
      baseUrl: BASE_URL,
      fetch: () => Promise.reject(error),
    }),
  };
}

function problemResponse(body: Record<string, unknown>): Response {
  return new Response(JSON.stringify(body), {
    status: body.status as number,
    headers: { 'Content-Type': 'application/problem+json' },
  });
}

async function failure(promise: Promise<unknown>): Promise<unknown> {
  try {
    await promise;
  } catch (error) {
    return error;
  }
  throw new Error('Expected the call to fail');
}

async function apiFailure(promise: Promise<unknown>): Promise<ApiError> {
  const error = await failure(promise);
  expect(error).toBeInstanceOf(ApiError);
  return error as ApiError;
}

const notFound = {
  type: 'about:blank',
  title: 'Not Found',
  status: 404,
  code: 'not_found',
  request_id: 'req-404',
  detail: 'Internal detail that must not reach the UI',
};

describe('apiCall', () => {
  it('returns data for a 2xx response', async () => {
    const { api } = clientReturning(
      new Response(JSON.stringify({ status: 'ok' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );

    await expect(apiCall(() => api.GET('/health/live'))).resolves.toEqual({
      status: 'ok',
    });
  });

  it('turns a Problem 404 into not_found with the body request ID', async () => {
    const { api } = clientReturning(problemResponse(notFound));

    const error = await apiFailure(apiCall(() => api.GET('/health/live')));

    expect(error.code).toBe('not_found');
    expect(error.status).toBe(404);
    expect(error.requestId).toBe('req-404');
    expect(error.fieldErrors).toEqual([]);
    expect(error.problem).toEqual(notFound);
    // The server's text is never used as the error message.
    expect(error.message).not.toContain('Internal detail');
    expect(error.message).not.toContain('Not Found');
  });

  it('exposes the field errors of a Problem 422', async () => {
    const errors = [
      { path: ['body', 'name'], code: 'missing', message: 'Field required' },
    ];
    const { api } = clientReturning(
      problemResponse({
        type: 'about:blank',
        title: 'Unprocessable Content',
        status: 422,
        code: 'validation_error',
        request_id: 'req-422',
        errors,
      }),
    );

    const error = await apiFailure(apiCall(() => api.GET('/health/live')));

    expect(error.code).toBe('validation_error');
    expect(error.status).toBe(422);
    expect(error.fieldErrors).toEqual(errors);
  });

  it('turns a 502 HTML page into unexpected_response with the header request ID', async () => {
    const { api } = clientReturning(
      new Response('<html><body>Bad Gateway</body></html>', {
        status: 502,
        headers: { 'Content-Type': 'text/html', 'X-Request-ID': 'req-502' },
      }),
    );

    const error = await apiFailure(apiCall(() => api.GET('/health/live')));

    expect(error.code).toBe('unexpected_response');
    expect(error.status).toBe(502);
    expect(error.requestId).toBe('req-502');
    expect(error.problem).toBeUndefined();
  });

  it('turns a non-2xx response with malformed JSON into unexpected_response', async () => {
    const { api } = clientReturning(
      new Response('{"code": "not_found",', {
        status: 500,
        headers: { 'Content-Type': 'application/problem+json' },
      }),
    );

    const error = await apiFailure(apiCall(() => api.GET('/health/live')));

    expect(error.code).toBe('unexpected_response');
    expect(error.status).toBe(500);
    expect(error.requestId).toBeUndefined();
  });

  it('turns a non-2xx response with an empty body into unexpected_response', async () => {
    const { api } = clientReturning(
      new Response(null, {
        status: 503,
        headers: { 'Content-Length': '0', 'X-Request-ID': 'req-empty' },
      }),
    );

    const error = await apiFailure(apiCall(() => api.GET('/health/live')));

    expect(error.code).toBe('unexpected_response');
    expect(error.status).toBe(503);
    expect(error.requestId).toBe('req-empty');
  });

  it('rejects a Problem body without a code (shape check)', async () => {
    const { code, ...withoutCode } = notFound;
    void code;
    const { api } = clientReturning(problemResponse(withoutCode));

    const error = await apiFailure(apiCall(() => api.GET('/health/live')));

    expect(error.code).toBe('unexpected_response');
    expect(error.status).toBe(404);
    expect(error.problem).toBeUndefined();
  });

  it('ignores a Problem-shaped body without the problem+json content type', async () => {
    const { api } = clientReturning(
      new Response(JSON.stringify(notFound), {
        status: 404,
        headers: { 'Content-Type': 'application/json' },
      }),
    );

    const error = await apiFailure(apiCall(() => api.GET('/health/live')));

    expect(error.code).toBe('unexpected_response');
  });

  it('turns a 200 with a non-JSON body into unexpected_response without a status', async () => {
    const { api } = clientReturning(
      new Response('<html>not json</html>', {
        status: 200,
        headers: { 'Content-Type': 'text/html' },
      }),
    );

    const error = await apiFailure(apiCall(() => api.GET('/health/live')));

    expect(error.code).toBe('unexpected_response');
    expect(error.status).toBeUndefined();
  });

  it('turns a rejected fetch (TypeError) into network_error', async () => {
    const { api } = clientRejecting(new TypeError('Failed to fetch'));

    const error = await apiFailure(apiCall(() => api.GET('/health/live')));

    expect(error.code).toBe('network_error');
    expect(error.status).toBeUndefined();
    expect(error.requestId).toBeUndefined();
  });

  it('re-throws an abort unchanged', async () => {
    const abort = new DOMException('Aborted', 'AbortError');
    const { api } = clientRejecting(abort);

    const error = await failure(apiCall(() => api.GET('/health/live')));

    expect(error).toBe(abort);
    expect(error).not.toBeInstanceOf(ApiError);
  });

  it('re-throws when the given signal is aborted, whatever the error', async () => {
    const controller = new AbortController();
    controller.abort();
    const reason = new TypeError('aborted fetch');
    const { api } = clientRejecting(reason);

    const error = await failure(
      apiCall(() => api.GET('/health/live'), controller.signal),
    );

    expect(error).toBe(reason);
  });

  it('re-throws any other rejection unchanged', async () => {
    const bug = new RangeError('programming error');
    const { api } = clientRejecting(bug);

    const error = await failure(apiCall(() => api.GET('/health/live')));

    expect(error).toBe(bug);
  });
});

describe('isProblemDetails', () => {
  it.each([
    ['a valid body', notFound, true],
    ['errors as an array', { ...notFound, errors: [] }, true],
    ['errors not an array', { ...notFound, errors: {} }, false],
    ['a numeric code', { ...notFound, code: 404 }, false],
    ['a string status', { ...notFound, status: '404' }, false],
    ['no request_id', { ...notFound, request_id: undefined }, false],
    ['no title', { ...notFound, title: undefined }, false],
    ['no type', { ...notFound, type: undefined }, false],
    ['null', null, false],
    ['an array', [notFound], false],
    ['a string', 'not_found', false],
  ])('%s → %s', (_name, body, expected) => {
    expect(isProblemDetails(body)).toBe(expected);
  });
});
