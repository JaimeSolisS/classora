import type { i18n as I18n, TFunction } from 'i18next';
import { toLanguage } from '@/i18n/language';
import { formatLocalDate, toLocalDate } from '@/lib/time';
import type { NotificationItem } from './api';

// A notification is `{notification_type, params}` (ISSUE-X1): its message is
// the i18n key `notifications.types.<type with "." replaced by "_">`,
// interpolating `params`, so a language switch re-renders old ones too. A
// type the client doesn't know shows a generic message, never its code.
// An offer notification about a series (#233 E1: its params carry
// `class_series_id`) uses the type's `_series` message when there is one;
// a notification whose params carry a `kind` uses the type's `_<kind>`
// message when there is one (#55 F4: a substitution offer's
// `_substitution`; #62 F3: a change request's `_cancellation` /
// `_reschedule`), and `_<kind>_makeup` when the params name a
// `makeup_until` (#62: an approved cancellation that allows a makeup).

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
const INSTANT_PARAMS = new Set([
  'starts_at',
  'respond_by',
  'new_starts_at',
  'makeup_until',
  // #63 scheduling.reassignment_needed.
  'first_starts_at',
]);

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

/** The `_<kind>` variants of a message to try, most specific first (#55
 * F4 substitutions; #62 F3 change requests). */
function kindVariants(
  base: string,
  params: NotificationItem['params'],
): string[] {
  const kind = params.kind;
  if (typeof kind !== 'string' || !/^[a-z_]+$/.test(kind)) {
    return [];
  }
  const makeup =
    typeof params.makeup_until === 'string' && params.makeup_until !== '';
  return [...(makeup ? [`${base}_${kind}_makeup`] : []), `${base}_${kind}`];
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
  const key =
    isSeries(item.params) && i18n.exists(series)
      ? series
      : (kindVariants(base, item.params).find((variant) =>
          i18n.exists(variant),
        ) ?? base);
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
  /** The center's time zone (a link to a calendar date needs it). */
  timeZone?: string;
}

/** The tutor's own qualifications section (#44). */
function ownQualifications(
  _params: NotificationItem['params'],
  recipient: NotificationRecipient,
): string {
  return `/tutors/${encodeURIComponent(recipient.userId)}#qualifications`;
}

/** The family's session requests (#60 F3). */
function myRequests(): string {
  return '/requests';
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

/** The tutor's own availability (#63 F1: the decision of their change
 * request). */
function ownAvailability(
  _params: NotificationItem['params'],
  recipient: NotificationRecipient,
): string {
  return `/tutors/${encodeURIComponent(recipient.userId)}#availability`;
}

/** The calendar filtered by the tutor on the first flagged session's local
 * date (#63 F2). */
function reassignmentCalendar(
  params: NotificationItem['params'],
  recipient: NotificationRecipient,
): string {
  const search = new URLSearchParams();
  if (typeof params.tutor_user_id === 'string' && params.tutor_user_id) {
    search.set('tutor', params.tutor_user_id);
  }
  const first = params.first_starts_at;
  if (
    recipient.timeZone &&
    typeof first === 'string' &&
    !Number.isNaN(Date.parse(first))
  ) {
    search.set('date', toLocalDate(first, recipient.timeZone));
  }
  const query = search.toString();
  return query ? `/schedule?${query}` : '/schedule';
}

/**
 * Where activating a notification of a type goes, from its params and its
 * recipient; a type without an entry only becomes read (for example
 * `scheduling.occurrence_skipped_closure`, #232, and #54's
 * `scheduling.session_rescheduled`, `scheduling.series_changed` and
 * `scheduling.series_cancelled`: their tutor, student and guardian
 * recipients have no session or series page yet, #54b / #67; #55's
 * `scheduling.tutor_substituted`, `scheduling.session_tutor_changed` and
 * `scheduling.session_cancelled` for the same reason, until #67).
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
  // #60 F3: the requester's decided request, in My requests.
  'scheduling.session_request_confirmed': myRequests,
  'scheduling.session_request_rejected': myRequests,
  // #62 F3: no family or tutor page for change requests until #62b, so
  // scheduling.change_request_approved / _rejected only become read.
  // #63: the tutor's availability decisions and the staff's reassignment
  // notice.
  'staff.availability_change_approved': ownAvailability,
  'staff.availability_change_rejected': ownAvailability,
  'scheduling.reassignment_needed': reassignmentCalendar,
};

/** The bell's badge text: the count, or "99+" above 99. */
export function badgeText(count: number): string {
  return count > 99 ? '99+' : String(count);
}
