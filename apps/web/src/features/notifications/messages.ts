import type { i18n as I18n, TFunction } from 'i18next';
import type { NotificationItem } from './api';

// A notification is `{notification_type, params}` (ISSUE-X1): its message is
// the i18n key `notifications.types.<type with "." replaced by "_">`,
// interpolating `params`, so a language switch re-renders old ones too. A
// type the client doesn't know shows a generic message, never its code.

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

function values(
  params: NotificationItem['params'],
  formatInstant?: (instant: string) => string,
): Record<string, unknown> {
  return Object.fromEntries(
    Object.entries(params)
      .filter(([name]) => !RESERVED_OPTIONS.has(name))
      .map(([name, value]) =>
        formatInstant &&
        INSTANT_PARAMS.has(name) &&
        typeof value === 'string' &&
        !Number.isNaN(Date.parse(value))
          ? [name, formatInstant(value)]
          : [name, value],
      ),
  );
}

/** The translated message of one notification. */
export function notificationMessage(
  t: TFunction,
  i18n: I18n,
  item: Pick<NotificationItem, 'notification_type' | 'params'>,
  formatInstant?: (instant: string) => string,
): string {
  const key = notificationMessageKey(item.notification_type);
  if (!i18n.exists(key)) {
    return t('notifications.unknown');
  }
  // A dynamic key: the typed t() can't check it, i18n.exists() did.
  return (t as unknown as (key: string, options: object) => string)(
    key,
    values(item.params, formatInstant),
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

/** The staff session page of a declined or expired offer (#52 F3). */
function offeredSession(params: NotificationItem['params']): string {
  const id = params.class_session_id;
  return typeof id === 'string' && id
    ? `/schedule/sessions/${encodeURIComponent(id)}`
    : '/schedule';
}

/**
 * Where activating a notification of a type goes, from its params and its
 * recipient; a type without an entry only becomes read.
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
