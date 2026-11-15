import type { BusinessSettings, BusinessSettingsUpdate } from './api';

// The receipt message rules (M8-04b C, #121 B2), as the API applies them:
// CRLF and CR become LF, the text is trimmed, an empty message is null, and
// at most 500 characters (code points, as Python counts them) remain.

export const RECEIPT_MESSAGE_MAX_LENGTH = 500;

export type MessageLanguage = 'es' | 'en';
export const MESSAGE_LANGUAGES: readonly MessageLanguage[] = ['es', 'en'];

export type MessageValues = Record<MessageLanguage, string>;

/** The value the API stores for `text`: normalized, or null when empty. */
export function normalizeMessage(text: string): string | null {
  const normalized = text.replace(/\r\n?/g, '\n').trim();
  return normalized === '' ? null : normalized;
}

/** The length the API checks: code points after normalizing. */
export function messageLength(text: string): number {
  return [...(normalizeMessage(text) ?? '')].length;
}

/** The stored messages as field values ('' for none). */
export function messageValues(settings: BusinessSettings): MessageValues {
  return {
    es: settings.receipt_message_es ?? '',
    en: settings.receipt_message_en ?? '',
  };
}

/** `version` plus the messages that differ from the stored ones. */
export function messageUpdate(
  settings: BusinessSettings,
  values: MessageValues,
): BusinessSettingsUpdate {
  const body: BusinessSettingsUpdate = { version: settings.version };
  const es = normalizeMessage(values.es);
  if (es !== (settings.receipt_message_es ?? null)) {
    body.receipt_message_es = es;
  }
  const en = normalizeMessage(values.en);
  if (en !== (settings.receipt_message_en ?? null)) {
    body.receipt_message_en = en;
  }
  return body;
}

/** Whether `values` would change what is stored. */
export function messagesChanged(
  settings: BusinessSettings,
  values: MessageValues,
): boolean {
  return Object.keys(messageUpdate(settings, values)).length > 1;
}
