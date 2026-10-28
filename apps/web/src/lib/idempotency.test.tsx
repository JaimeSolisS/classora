import { createApiClient } from '@classora/api-client';
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { apiCall, setAuthHandler } from '@/app/api';
import {
  IDEMPOTENCY_HEADER,
  IdempotentSubmission,
  idempotencyHeaders,
  newIdempotencyKey,
  useIdempotentSubmission,
} from './idempotency';

// #88 F2: the web helper for financial submissions (ADR-0006 E6).

(
  globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }
).IS_REACT_ACT_ENVIRONMENT = true;

const KEY_FORMAT = /^[A-Za-z0-9_-]{16,128}$/;
const BODY = { amount: '1250.00', lines: [{ b: 1, a: 2 }], note: 'café' };

afterEach(() => {
  setAuthHandler(null);
});

describe('newIdempotencyKey', () => {
  it('matches the API key format and is new every time', () => {
    const keys = new Set(Array.from({ length: 50 }, newIdempotencyKey));

    expect(keys.size).toBe(50);
    for (const key of keys) {
      expect(key).toMatch(KEY_FORMAT);
    }
  });

  it('builds the header', () => {
    expect(IDEMPOTENCY_HEADER).toBe('Idempotency-Key');
    expect(idempotencyHeaders('k'.repeat(16))).toEqual({
      'Idempotency-Key': 'k'.repeat(16),
    });
  });
});

describe('IdempotentSubmission', () => {
  it('keeps the key for the same body, in any key order', () => {
    const submission = new IdempotentSubmission();

    const first = submission.keyFor(BODY);
    const reordered = submission.keyFor({
      note: 'café',
      lines: [{ a: 2, b: 1 }],
      amount: '1250.00',
    });

    expect(first).toMatch(KEY_FORMAT);
    expect(reordered).toBe(first);
    expect(submission.keyFor(structuredClone(BODY))).toBe(first);
  });

  it('gives a new key after a success, after reset and for a changed body', () => {
    const submission = new IdempotentSubmission();
    const first = submission.keyFor(BODY);

    submission.succeeded();
    const afterSuccess = submission.keyFor(BODY);
    submission.reset();
    const afterReset = submission.keyFor(BODY);
    const changed = submission.keyFor({ ...BODY, amount: '1250.01' });
    const reordered = submission.keyFor({ ...BODY, lines: [{ a: 1, b: 2 }] });

    const keys = [first, afterSuccess, afterReset, changed, reordered];
    expect(new Set(keys).size).toBe(keys.length);
  });

  it('treats array order as part of the body', () => {
    const submission = new IdempotentSubmission();

    const first = submission.keyFor({ ids: ['a', 'b'] });

    expect(submission.keyFor({ ids: ['b', 'a'] })).not.toBe(first);
  });

  it('never shares a key between two instances', () => {
    const one = new IdempotentSubmission();
    const two = new IdempotentSubmission();

    expect(one.keyFor(BODY)).not.toBe(two.keyFor(BODY));
  });
});

describe('with apiCall', () => {
  function csrfFailed(): Response {
    return new Response(
      JSON.stringify({
        type: 'about:blank',
        title: 'Forbidden',
        status: 403,
        code: 'csrf_failed',
        request_id: 'req-1',
      }),
      { status: 403, headers: { 'Content-Type': 'application/problem+json' } },
    );
  }

  it('resends the same key when apiCall retries after csrf_failed', async () => {
    const sent: (string | null)[] = [];
    const responses = [
      csrfFailed(),
      new Response(JSON.stringify({ status: 'ok' }), {
        status: 201,
        headers: { 'Content-Type': 'application/json' },
      }),
    ];
    const fetchMock = vi.fn((request: Request) => {
      sent.push(request.headers.get(IDEMPOTENCY_HEADER));
      return Promise.resolve(responses.shift()!);
    });
    const api = createApiClient({
      baseUrl: 'http://api.test',
      fetch: fetchMock,
    });
    setAuthHandler({
      sessionEnded: vi.fn(),
      refreshAfterCsrfFailure: vi.fn(() => Promise.resolve(true)),
      reauthenticate: vi.fn(() => Promise.resolve(false)),
      privacyNoticeRequired: vi.fn(),
    });
    const submission = new IdempotentSubmission();

    const key = submission.keyFor(BODY);
    await apiCall(() =>
      api.POST('/api/v1/auth/logout', { headers: idempotencyHeaders(key) }),
    );

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(sent).toEqual([key, key]);
  });

  it('gives the same key again after a network error', async () => {
    const api = createApiClient({
      baseUrl: 'http://api.test',
      fetch: () => Promise.reject(new TypeError('Failed to fetch')),
    });
    const submission = new IdempotentSubmission();

    const key = submission.keyFor(BODY);
    await expect(
      apiCall(() =>
        api.POST('/api/v1/auth/logout', { headers: idempotencyHeaders(key) }),
      ),
    ).rejects.toMatchObject({ code: 'network_error' });

    expect(submission.keyFor({ ...BODY })).toBe(key);
  });
});

describe('useIdempotentSubmission', () => {
  it('keeps one instance per mounted component', () => {
    const seen: IdempotentSubmission[] = [];
    function Form({ tick }: { tick: number }) {
      seen.push(useIdempotentSubmission());
      return <p>{tick}</p>;
    }
    const container = document.createElement('div');
    const root = createRoot(container);

    act(() => root.render(<Form tick={1} />));
    act(() => root.render(<Form tick={2} />));
    act(() => root.unmount());
    const other = createRoot(container);
    act(() => other.render(<Form tick={3} />));
    act(() => other.unmount());

    expect(seen.length).toBeGreaterThanOrEqual(3);
    expect(seen[1]).toBe(seen[0]);
    expect(seen.at(-1)).not.toBe(seen[0]);
  });
});
