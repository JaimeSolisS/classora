import type { i18n as I18n, TFunction } from 'i18next';
import { toLanguage } from '@/i18n/language';
import { formatLocalDate } from '@/lib/time';
import type { NotificationItem } from './api';

// A notification is `{notification_type, params}` (ISSUE-X1): its message is
// the i18n key `notifications.types.<type with "." replaced by "_">`,
// interpolating `params`, so a language switch re-renders old ones too. A
// type the client doesn't know shows a generic message, never its code.
// An offer notification about a series (#233 E1: its params carry
// `class_series_id`) uses the type's `_series` message when there is one.

/** The i18n key of a type's message, for example `notifications.types.dev_demo`. */
export function notificationMessageKey(type: string): string {
  return `notifications.types.${type.replaceAll('.', '_')}`;
}

// i18next reads these names as options, not as values: a param with one of
// them would change the language or the namespace, so it is never passed.
const RESERVED_OPTIONS = new Set([
  'lng',
  'lngs',
  'ns',
  'context',
  'defaultValue',
  'returnObjects',
  'returnDetails',
  'joinArrays',
  'postProcess',
  'interpolation',
  'replace',
  'fallbackLng',
  'keySeparator',
  'nsSeparator',
  'ordinal',
  'skipInterpolation',
]);

/**
 * Params that hold an instant (RFC 3339, #52 offers): shown formatted in
 * the center zone when the caller passes a formatter.
 */
const INSTANT_PARAMS = new Set(['starts_at', 'respond_by']);

/** Params that hold a local date (`YYYY-MM-DD`, #233 series offers). */
const DATE_PARAMS = new Set(['from_date']);
const LOCAL_DATE = /^\d{4}-\d{2}-\d{2}$/;

function values(
  params: NotificationItem['params'],
  i18n: I18n,
  formatInstant?: (instant: string) => string,
): Record<string, unknown> {
  const language = toLanguage(i18n.resolvedLanguage);
  return Object.fromEntries(
    Object.entries(params)
      .filter(([name]) => !RESERVED_OPTIONS.has(name))
      .map(([name, value]) => {
        if (
          formatInstant &&
          INSTANT_PARAMS.has(name) &&
          typeof value === 'string' &&
          !Number.isNaN(Date.parse(value))
        ) {
          return [name, formatInstant(value)];
        }
        if (
          DATE_PARAMS.has(name) &&
          typeof value === 'string' &&
          LOCAL_DATE.test(value)
        ) {
          return [name, formatLocalDate(value, { language })];
        }
        return [name, value];
      }),
  );
}

/** Whether the params are about a series offer (#233 E1). */
function isSeries(params: NotificationItem['params']): boolean {
  return (
    typeof params.class_series_id === 'string' && params.class_series_id !== ''
  );
}

/** The translated message of one notification. */
export function notificationMessage(
  t: TFunction,
  i18n: I18n,
  item: Pick<NotificationItem, 'notification_type' | 'params'>,
  formatInstant?: (instant: string) => string,
): string {
  const base = notificationMessageKey(item.notification_type);
  const series = `${base}_series`;
  const key = isSeries(item.params) && i18n.exists(series) ? series : base;
  if (!i18n.exists(key)) {
    return t('notifications.unknown');
  }
  // A dynamic key: the typed t() can't check it, i18n.exists() did.
  return (t as unknown as (key: string, options: object) => string)(
    key,
    values(item.params, i18n, formatInstant),
  );
}

/** Who is reading the notification: its recipient, the signed-in user. */
export interface NotificationRecipient {
  userId: string;
}

/** The tutor's own qualifications section (#44). */
function ownQualifications(
  _params: NotificationItem['params'],
  recipient: NotificationRecipient,
): string {
  return `/tutors/${encodeURIComponent(recipient.userId)}#qualifications`;
}

/** The tutor's offers (#52 F3). */
function myOffers(): string {
  return '/offers';
}

/**
 * The staff session page of a declined or expired offer (#52 F3): the
 * offer's (first) session; a series offer that covered none goes to the
 * approval inbox (#233 E1; #54b may link the series page).
 */
function offeredSession(params: NotificationItem['params']): string {
  const id = params.class_session_id;
  if (typeof id === 'string' && id) {
    return `/schedule/sessions/${encodeURIComponent(id)}`;
  }
  return isSeries(params) ? '/approvals' : '/schedule';
}

/**
 * Where activating a notification of a type goes, from its params and its
 * recipient; a type without an entry only becomes read (for example
 * `scheduling.occurrence_skipped_closure`, #232, and #54's
 * `scheduling.session_rescheduled`, `scheduling.series_changed` and
 * `scheduling.series_cancelled`: their tutor, student and guardian
 * recipients have no session or series page yet, #54b / #67).
 */
export const NOTIFICATION_LINKS: Readonly<
  Record<
    string,
    (
      params: NotificationItem['params'],
      recipient: NotificationRecipient,
    ) => string
  >
> = {
  'staff.qualification_approved': ownQualifications,
  'staff.qualification_rejected': ownQualifications,
  'staff.qualification_revoked': ownQualifications,
  'scheduling.offer_received': myOffers,
  'scheduling.offer_declined': offeredSession,
  'scheduling.offer_expired': offeredSession,
};

/** The bell's badge text: the count, or "99+" above 99. */
export function badgeText(count: number): string {
  return count > 99 ? '99+' : String(count);
}
