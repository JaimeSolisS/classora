import type { NotificationPreferenceItem } from './preferences-api';
import { NOTIFICATION_PREFERENCE_TYPES } from './preferenceTypes';

// Stories and tests only: the catalogues #119 gives a guardian, a tutor and
// the Owner (the roles of `test_notification_catalogue.py`), in the API's
// order (by code), with every optional type on unless muted.

type Audience = 'tutor' | 'family' | 'guardian' | 'staff';

const MANDATORY = new Set<string>([
  'scheduling.session_cancelled',
  'scheduling.session_tutor_changed',
  'scheduling.tutor_substituted',
  'scheduling.session_rescheduled',
  'scheduling.series_changed',
  'scheduling.series_cancelled',
  'scheduling.occurrence_skipped_closure',
]);

const FAMILY = new Set<string>([
  'academics.learning_note_published',
  'academics.evaluation_finalized',
  'academics.learning_plan_activated',
  'academics.learning_plan_completed',
  'academics.assignment_set',
  'academics.assignment_graded',
  'academics.progress_report_approved',
  'scheduling.session_request_confirmed',
  'scheduling.session_request_rejected',
  'scheduling.change_request_approved',
  'scheduling.change_request_rejected',
  'scheduling.session_cancelled',
  'scheduling.session_tutor_changed',
  'scheduling.session_rescheduled',
  'scheduling.series_changed',
  'scheduling.series_cancelled',
  'scheduling.occurrence_skipped_closure',
]);

const STAFF = new Set<string>([
  'scheduling.offer_declined',
  'scheduling.offer_expired',
  'scheduling.reassignment_needed',
  'administration.approval_assigned',
]);

function inAudience(code: string, audience: Audience): boolean {
  switch (audience) {
    case 'staff':
      return STAFF.has(code);
    case 'family':
      return FAMILY.has(code);
    case 'guardian':
      return FAMILY.has(code) || code === 'billing.payment_reminder';
    case 'tutor':
      return (
        !STAFF.has(code) &&
        code !== 'billing.payment_reminder' &&
        code !== 'scheduling.session_tutor_changed' &&
        (!FAMILY.has(code) ||
          MANDATORY.has(code) ||
          code === 'academics.progress_report_approved' ||
          code.startsWith('scheduling.change_request_'))
      );
  }
}

function catalogue(
  audience: Audience,
  muted: readonly string[] = [],
): NotificationPreferenceItem[] {
  return NOTIFICATION_PREFERENCE_TYPES.filter((code) =>
    inAudience(code, audience),
  )
    .toSorted()
    .map((code) => ({
      notification_type: code,
      mandatory: MANDATORY.has(code),
      enabled: !muted.includes(code),
    }));
}

/** demo.guardian: family types and payment reminders; assignments muted. */
export const GUARDIAN_CATALOGUE = catalogue('guardian', [
  'academics.assignment_set',
]);

/** demo.tutor: tutor types. */
export const TUTOR_CATALOGUE = catalogue('tutor', [
  'academics.progress_report_due',
]);

/** demo.owner: the staff types. */
export const OWNER_CATALOGUE = catalogue('staff');
