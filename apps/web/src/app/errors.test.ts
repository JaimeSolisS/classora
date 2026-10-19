import { afterEach, describe, expect, it, vi } from 'vitest';
import { i18n } from '@/i18n';
import { LANGUAGES } from '@/i18n/language';
import { ApiError } from './api';
import {
  errorMessage,
  errorMessageKey,
  errorRequestId,
  MAPPED_ERROR_CODES,
  reportError,
} from './errors';

// E1 (H4): every known code has its own key in both languages, unknown codes
// fall back by status, and the server's text never reaches the UI.

const API_CODES = [
  'validation_error',
  'not_found',
  'method_not_allowed',
  'internal_error',
  'database_unavailable',
  'invalid_cursor',
  'version_conflict',
  'authentication_required',
  'permission_denied',
  'invalid_credentials',
  'csrf_failed',
];
// Spellings confirmed with status_code_name() (apps/api/app/core/errors.py).
const STATUS_CODES = [
  'bad_request',
  'unauthorized',
  'forbidden',
  'conflict',
  'content_too_large',
  'too_many_requests',
];
const CLIENT_CODES = ['network_error', 'unexpected_response'];
const ALL_CODES = [...API_CODES, ...STATUS_CODES, ...CLIENT_CODES];

afterEach(() => {
  vi.restoreAllMocks();
});

describe('errorMessageKey', () => {
  it('maps exactly the listed codes', () => {
    expect([...MAPPED_ERROR_CODES].sort()).toEqual([...ALL_CODES].sort());
  });

  it.each(ALL_CODES)('maps %s to its own key', (code) => {
    expect(errorMessageKey(new ApiError({ code, status: 400 }))).toBe(code);
  });

  it.each([
    [
      'an unknown 4xx code',
      new ApiError({ code: 'new_4xx', status: 418 }),
      'client_error',
    ],
    [
      'an unknown 5xx code',
      new ApiError({ code: 'new_5xx', status: 504 }),
      'server_error',
    ],
    [
      'the local-only demo code',
      new ApiError({ code: 'demo_job_already_queued', status: 409 }),
      'client_error',
    ],
    [
      'session_expired (M2-10)',
      new ApiError({ code: 'session_expired', status: 401 }),
      'client_error',
    ],
    [
      'an unknown code without status',
      new ApiError({ code: 'odd' }),
      'generic',
    ],
    [
      'a prototype key as code',
      new ApiError({ code: 'constructor' }),
      'generic',
    ],
    ['an Error', new Error('boom'), 'generic'],
    ['a string', 'boom', 'generic'],
    ['undefined', undefined, 'generic'],
  ] as const)('falls back for %s', (_name, error, key) => {
    expect(errorMessageKey(error)).toBe(key);
  });
});

describe('error messages in both languages', () => {
  const KEYS = [...ALL_CODES, 'client_error', 'server_error', 'generic'];

  it.each(KEYS)(
    '%s has a real message in es and en, different per language',
    (key) => {
      const messages = LANGUAGES.map((language) =>
        i18n.getFixedT(language, 'errors')(key as 'generic'),
      );
      for (const message of messages) {
        expect(message.trim()).not.toBe('');
        expect(message).not.toBe(key);
        expect(message).not.toMatch(/errors:|\d{3}/);
      }
      // No key is intentionally identical in both languages.
      expect(messages[0]).not.toBe(messages[1]);
    },
  );

  it('resolves through the given t in the active language', () => {
    const error = new ApiError({ code: 'not_found', status: 404 });
    expect(errorMessage(error, i18n.getFixedT('es', 'errors'))).toBe(
      'No encontramos lo que buscas.',
    );
    expect(errorMessage(error, i18n.getFixedT('en', 'errors'))).toBe(
      "We couldn't find what you were looking for.",
    );
  });

  it('never returns the server detail or title', () => {
    for (const language of LANGUAGES) {
      const t = i18n.getFixedT(language, 'errors');
      for (const code of [...ALL_CODES, 'unknown_code']) {
        const error = new ApiError({
          code,
          status: 400,
          problem: {
            type: 'about:blank',
            title: 'SERVER TITLE',
            status: 400,
            code,
            request_id: 'req-1',
            detail: 'SERVER DETAIL',
          },
        });
        const message = errorMessage(error, t);
        expect(message).not.toContain('SERVER');
      }
    }
  });
});

describe('errorRequestId', () => {
  it('returns the request ID of an ApiError only', () => {
    expect(errorRequestId(new ApiError({ code: 'x', requestId: 'r1' }))).toBe(
      'r1',
    );
    expect(errorRequestId(new ApiError({ code: 'x' }))).toBeUndefined();
    expect(errorRequestId(new Error('x'))).toBeUndefined();
  });
});

describe('reportError', () => {
  it('logs an ApiError as code, status and request ID only', () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    reportError(
      new ApiError({
        code: 'not_found',
        status: 404,
        requestId: 'req-9',
        problem: {
          type: 'about:blank',
          title: 'Not Found',
          status: 404,
          code: 'not_found',
          request_id: 'req-9',
          detail: 'SERVER DETAIL',
        },
      }),
      { source: 'query' },
    );
    expect(spy).toHaveBeenCalledTimes(1);
    expect(spy).toHaveBeenCalledWith(
      '[classora]',
      { source: 'query' },
      { code: 'not_found', status: 404, requestId: 'req-9' },
    );
    expect(JSON.stringify(spy.mock.calls)).not.toContain('SERVER DETAIL');
  });
});
