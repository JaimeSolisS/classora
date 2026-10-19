import type { TFunction } from 'i18next';
import type errorsCatalog from '@/i18n/locales/es/errors.json';
import { ApiError } from './api';

// Central error handling (spec §10): maps any failed call to a translated
// message, and is the one place errors are reported.

/** A key of the `errors` namespace. */
export type ErrorMessageKey = keyof typeof errorsCatalog;

// Static map from Problem `code` (and client codes) to catalog keys, so a
// server code never reaches t() directly. When the API adds a code, add it
// here and add the key to both es and en errors.json.
const ERROR_CODE_KEYS: ReadonlyMap<string, ErrorMessageKey> = new Map<
  string,
  ErrorMessageKey
>([
  // Codes the API emits today (_docs/api.md "Codes in use today").
  ['validation_error', 'validation_error'],
  ['not_found', 'not_found'],
  ['method_not_allowed', 'method_not_allowed'],
  ['internal_error', 'internal_error'],
  ['database_unavailable', 'database_unavailable'],
  ['invalid_cursor', 'invalid_cursor'],
  ['version_conflict', 'version_conflict'],
  ['authentication_required', 'authentication_required'],
  ['permission_denied', 'permission_denied'],
  ['invalid_credentials', 'invalid_credentials'],
  ['csrf_failed', 'csrf_failed'],
  ['session_expired', 'session_expired'],
  ['reauth_required', 'reauth_required'],
  ['reauth_failed', 'reauth_failed'],
  ['rate_limited', 'rate_limited'],
  ['token_invalid', 'token_invalid'],
  ['username_taken', 'username_taken'],
  ['username_invalid', 'username_invalid'],
  ['password_too_short', 'password_too_short'],
  ['password_too_long', 'password_too_long'],
  ['password_equals_username', 'password_equals_username'],
  ['current_password_invalid', 'current_password_invalid'],
  ['user_not_active', 'user_not_active'],
  // Status-derived codes (status_code_name() in apps/api/app/core/errors.py).
  ['bad_request', 'bad_request'],
  ['unauthorized', 'unauthorized'],
  ['forbidden', 'forbidden'],
  ['conflict', 'conflict'],
  ['content_too_large', 'content_too_large'],
  ['too_many_requests', 'too_many_requests'],
  // Client codes (src/app/api.ts).
  ['network_error', 'network_error'],
  ['unexpected_response', 'unexpected_response'],
]);

/** The codes with their own message (exported for tests). */
export const MAPPED_ERROR_CODES: readonly string[] = [
  ...ERROR_CODE_KEYS.keys(),
];

/** The `errors` key for any thrown value. */
export function errorMessageKey(error: unknown): ErrorMessageKey {
  if (!(error instanceof ApiError)) {
    return 'generic';
  }
  const key = ERROR_CODE_KEYS.get(error.code);
  if (key) {
    return key;
  }
  const { status } = error;
  if (status !== undefined && status >= 400 && status < 500) {
    return 'client_error';
  }
  if (status !== undefined && status >= 500 && status < 600) {
    return 'server_error';
  }
  return 'generic';
}

/**
 * The translated message for any thrown value. Pass the `errors` namespace's
 * t (`useTranslation('errors').t`), so the text follows the active language.
 */
export function errorMessage(error: unknown, t: TFunction<'errors'>): string {
  return t(errorMessageKey(error));
}

/** The request ID to quote to support, when the error has one. */
export function errorRequestId(error: unknown): string | undefined {
  return error instanceof ApiError ? error.requestId : undefined;
}

export interface ErrorContext {
  /** Where the error was caught. */
  source: 'route' | 'root' | 'app' | 'query' | 'mutation';
  [key: string]: unknown;
}

/**
 * The single reporting seam for caught errors (error boundaries, query and
 * mutation caches). Today it only writes to the console; Sentry/OTel wiring
 * goes here later. Never logs the response body, `detail` or `title`.
 */
export function reportError(error: unknown, context: ErrorContext): void {
  if (error instanceof ApiError) {
    console.error('[classora]', context, {
      code: error.code,
      status: error.status,
      requestId: error.requestId,
    });
    return;
  }
  console.error('[classora]', context, error);
}
