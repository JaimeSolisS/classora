import type { components } from '@classora/api-client';
import { api, apiCall } from '@/app/api';

// The privacy-notice calls (#174): the current notice (a query) and its
// acceptance (a direct call). Both routes work before the notice is
// accepted; every other route answers 403 privacy_notice_required until then.

export type PrivacyNotice = components['schemas']['PrivacyNoticeRead'];
export type PrivacyNoticeAccepted =
  components['schemas']['PrivacyNoticeAccepted'];

export const PRIVACY_NOTICE_QUERY_KEY = ['privacy-notice'] as const;

export function fetchPrivacyNotice(
  signal: AbortSignal,
): Promise<PrivacyNotice> {
  return apiCall(() => api.GET('/api/v1/privacy-notice', { signal }), signal);
}

/** Accepts `version`, the version the page showed (409 when not current). */
export function acceptPrivacyNotice(
  version: number,
): Promise<PrivacyNoticeAccepted> {
  return apiCall(() =>
    api.POST('/api/v1/privacy-notice/acceptance', { body: { version } }),
  );
}

/**
 * The notice text as paragraphs: plain text split on blank lines (no
 * Markdown, no HTML; React escapes it). Single line breaks stay inside a
 * paragraph.
 */
export function paragraphsOf(text: string): string[] {
  return text
    .split(/\r?\n[ \t]*\r?\n/)
    .map((paragraph) => paragraph.trim())
    .filter((paragraph) => paragraph.length > 0);
}
