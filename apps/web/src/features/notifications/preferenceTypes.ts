import type { i18n as I18n, TFunction } from 'i18next';
import type { NotificationPreferenceItem } from './preferences-api';

// The notification preferences catalogue on the web (M8-02b B2, B3, D1).
// Every type the API can list (#119 A5, `test_notification_catalogue.py`),
// pinned so a new type fails `preferences.test.tsx` until it has a label in
// es and en (`notifications.preferences.types.<code with "." → "_">`).
export const NOTIFICATION_PREFERENCE_TYPES = [
  'academics.closeout_approved',
  'academics.closeout_corrected',
  'academics.closeout_changes_requested',
  'academics.closeout_submitted_on_behalf',
  'academics.learning_note_published',
  'academics.learning_note_unpublished',
  'academics.evaluation_finalized',
  'academics.evaluation_voided',
  'academics.learning_plan_activated',
  'academics.learning_plan_completed',
  'academics.assignment_set',
  'academics.assignment_graded',
  'academics.assignment_submitted',
  'academics.progress_report_approved',
  'academics.progress_report_changes_requested',
  'academics.progress_report_retracted',
  'academics.progress_report_due',
  'staff.qualification_approved',
  'staff.qualification_rejected',
  'staff.qualification_revoked',
  'staff.availability_change_approved',
  'staff.availability_change_rejected',
  'scheduling.offer_received',
  'scheduling.offer_declined',
  'scheduling.offer_expired',
  'scheduling.session_request_confirmed',
  'scheduling.session_request_rejected',
  'scheduling.change_request_approved',
  'scheduling.change_request_rejected',
  'scheduling.reassignment_needed',
  'scheduling.session_cancelled',
  'scheduling.session_tutor_changed',
  'scheduling.tutor_substituted',
  'scheduling.session_rescheduled',
  'scheduling.series_changed',
  'scheduling.series_cancelled',
  'scheduling.occurrence_skipped_closure',
  'billing.payment_reminder',
  'payroll.period_finalized',
  'administration.approval_assigned',
  // M8-07b D1 (#124 E9): the requester's export finished or failed.
  'reporting.export_ready',
  'reporting.export_failed',
] as const;

/** The groups, in page order (B2); a code with another prefix goes to "other". */
export const PREFERENCE_GROUPS = [
  'scheduling',
  'academics',
  'staff',
  'billing',
  'payroll',
  'administration',
  // M8-07b D1: the export notifications.
  'reporting',
] as const;

export type PreferenceGroupCode = (typeof PREFERENCE_GROUPS)[number] | 'other';

/** The i18n key of a type's label, for example `notifications.preferences.types.billing_payment_reminder`. */
export function preferenceLabelKey(type: string): string {
  return `notifications.preferences.types.${type.replaceAll('.', '_')}`;
}

/** The type's label, or `null` when the catalogs have none (B3 fallback). */
export function preferenceLabel(
  t: TFunction,
  i18n: I18n,
  type: string,
): string | null {
  const key = preferenceLabelKey(type);
  if (!i18n.exists(key)) {
    return null;
  }
  // A dynamic key: the typed t() can't check it, i18n.exists() did.
  return (t as unknown as (key: string) => string)(key);
}

function groupOf(type: string): PreferenceGroupCode {
  const prefix = type.split('.', 1)[0];
  return (PREFERENCE_GROUPS as readonly string[]).includes(prefix ?? '')
    ? (prefix as PreferenceGroupCode)
    : 'other';
}

export interface PreferenceRow {
  item: NotificationPreferenceItem;
  /** `null`: no label in the catalogs (the generic text and the code show). */
  label: string | null;
}

export interface PreferenceGroup {
  code: PreferenceGroupCode;
  rows: PreferenceRow[];
}

/**
 * The catalogue in page order: the groups of B2 (only the non-empty ones,
 * "other" last), and within a group by label in the current language (an
 * unlabelled type sorts by the generic text, then by code).
 */
export function groupPreferences(
  items: readonly NotificationPreferenceItem[],
  t: TFunction,
  i18n: I18n,
): PreferenceGroup[] {
  const fallback = t('notifications.preferences.unknownType');
  const collator = new Intl.Collator(i18n.language);
  const order: PreferenceGroupCode[] = [...PREFERENCE_GROUPS, 'other'];
  return order
    .map((code) => ({
      code,
      rows: items
        .filter((item) => groupOf(item.notification_type) === code)
        .map((item) => ({
          item,
          label: preferenceLabel(t, i18n, item.notification_type),
        }))
        .sort(
          (a, b) =>
            collator.compare(a.label ?? fallback, b.label ?? fallback) ||
            collator.compare(
              a.item.notification_type,
              b.item.notification_type,
            ),
        ),
    }))
    .filter((group) => group.rows.length > 0);
}
