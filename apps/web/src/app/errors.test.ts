import inventory from '@classora/api-client/i18n-inventory.json';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { i18n } from '@/i18n';
import { LANGUAGES } from '@/i18n/language';
import { ApiError } from './api';
import {
  errorMessage,
  errorMessageKey,
  errorRequestId,
  GENERIC_FIELD_CODES,
  LOCAL_ONLY_CODES,
  MAPPED_ERROR_CODES,
  reportError,
} from './errors';

// E1 (H4): every known code has its own key in both languages, unknown codes
// fall back by status, and the server's text never reaches the UI.

// #130 C1: the codes the API can send come from the generated inventory
// (packages/api-client/i18n-inventory.json, `make api-client`), not a hand
// list: every Problem code but the local-only ones, and every field code but
// Pydantic's generic ones.
const INVENTORY = inventory as Record<string, string[]>;
const API_CODES = [
  ...new Set([
    ...INVENTORY.problem_codes!.filter((c) => !LOCAL_ONLY_CODES.includes(c)),
    ...INVENTORY.field_codes!.filter((c) => !GENERIC_FIELD_CODES.includes(c)),
  ]),
];
// #308: codes whose catalog key differs (i18next reads `_many` as a plural
// suffix, so `criteria_too_many` can't be a key).
const RENAMED_CODES: Record<string, string> = {
  criteria_too_many: 'criteria_limit',
  // #310: the learning plan limits.
  objectives_too_many: 'objectives_limit',
  milestones_too_many: 'milestones_limit',
  // #313: the homework recipient limit.
  recipients_too_many: 'recipients_limit',
  // #386: a catalog key can't end in a plural suffix ("_zero").
  amount_zero: 'amount_zero_invalid',
  // #130: the conflict override's pair limit.
  pairs_too_many: 'pairs_limit',
};

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
const ALL_CODES = [...API_CODES, ...STATUS_CODES, ...CLIENT_CODES].filter(
  (code) => !(code in RENAMED_CODES),
);
afterEach(() => {
  vi.restoreAllMocks();
});

// #66 I3: cohort enrollment reuses the #56/#49 codes; each is listed once
// (the repeated-key check of the locale files is i18n/check.test.ts).
describe('cohort enrollment codes (#66 I3)', () => {
  it.each([
    'already_enrolled',
    'enrollment_not_found',
    'enrollment_not_active',
    'cohort_ended',
  ])('the API code %s is listed once', (code) => {
    expect(API_CODES.filter((c) => c === code)).toHaveLength(1);
    expect(MAPPED_ERROR_CODES.filter((c) => c === code)).toHaveLength(1);
  });

  it.each(['max_students_below_enrolled', 'reason_too_long'])(
    'the field code %s has one message',
    (code) => {
      expect(MAPPED_ERROR_CODES.filter((c) => c === code)).toHaveLength(1);
      for (const language of LANGUAGES) {
        expect(i18n.exists(code, { lng: language, ns: 'errors' })).toBe(true);
      }
    },
  );
});

describe('errorMessageKey', () => {
  it('maps exactly the listed codes', () => {
    expect([...MAPPED_ERROR_CODES].sort()).toEqual(
      [...ALL_CODES, ...Object.keys(RENAMED_CODES)].sort(),
    );
  });

  it.each(Object.entries(RENAMED_CODES))(
    'maps %s to the key %s',
    (code, key) => {
      expect(errorMessageKey(new ApiError({ code, status: 422 }))).toBe(key);
    },
  );

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

// Messages whose text names a numeric limit (series length, note length).
const LIMIT_MESSAGES = [
  'count_out_of_range',
  'decline_note_too_long',
  'reason_too_long',
];

describe('error messages in both languages', () => {
  const KEYS = [
    ...ALL_CODES,
    ...Object.values(RENAMED_CODES),
    'client_error',
    'server_error',
    'generic',
  ];

  it.each(KEYS)(
    '%s has a real message in es and en, different per language',
    (key) => {
      const messages = LANGUAGES.map((language) =>
        i18n.getFixedT(language, 'errors')(key as 'generic'),
      );
      for (const message of messages) {
        expect(message.trim()).not.toBe('');
        expect(message).not.toBe(key);
        expect(message).not.toMatch(/errors:/);
        // No status number; a few messages state a limit (#130 D2).
        if (!LIMIT_MESSAGES.includes(key)) {
          expect(message).not.toMatch(/\d{3}/);
        }
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

// #322 D1: the progress pages' range and subject refusals each have a
// message in both languages (range_too_long uses its own progress text:
// the catalog's range_too_long is the calendar's 45-day limit).
describe('the progress range messages (#322 D1)', () => {
  it.each([
    'range_invalid',
    'range_in_future',
    'progress_range_too_long',
    'subject_unknown',
  ])('%s has a message in both languages', (key) => {
    for (const language of LANGUAGES) {
      expect(i18n.exists(key, { lng: language, ns: 'errors' })).toBe(true);
    }
  });

  it('the progress period limit names 24 months, not the calendar limit', () => {
    expect(i18n.t('progress_range_too_long', { lng: 'en', ns: 'errors' })).toBe(
      'Choose a period of at most 24 months.',
    );
  });
});
