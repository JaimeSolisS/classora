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
  ['registration_already_decided', 'registration_already_decided'],
  ['last_active_owner', 'last_active_owner'],
  ['user_state_transition_invalid', 'user_state_transition_invalid'],
  ['delegation_forbidden', 'delegation_forbidden'],
  ['permission_not_delegable', 'permission_not_delegable'],
  ['delegation_target_invalid', 'delegation_target_invalid'],
  ['name_taken', 'name_taken'],
  [
    'education_level_has_active_subjects',
    'education_level_has_active_subjects',
  ],
  ['education_level_inactive', 'education_level_inactive'],
  ['education_level_unavailable', 'education_level_unavailable'],
  ['emergency_contact_unknown', 'emergency_contact_unknown'],
  ['guardian_account_unavailable', 'guardian_account_unavailable'],
  ['guardian_account_in_use', 'guardian_account_in_use'],
  ['guardian_unavailable', 'guardian_unavailable'],
  ['guardian_already_linked', 'guardian_already_linked'],
  ['family_member_conflict', 'family_member_conflict'],
  ['family_inactive', 'family_inactive'],
  ['privacy_notice_required', 'privacy_notice_required'],
  ['privacy_notice_outdated', 'privacy_notice_outdated'],
  ['student_profile_required', 'student_profile_required'],
  ['guardian_consent_required', 'guardian_consent_required'],
  ['student_account_unavailable', 'student_account_unavailable'],
  ['student_account_in_use', 'student_account_in_use'],
  ['consent_method_invalid', 'consent_method_invalid'],
  ['consent_guardian_invalid', 'consent_guardian_invalid'],
  ['consent_not_applicable', 'consent_not_applicable'],
  ['consent_already_given', 'consent_already_given'],
  ['consent_already_revoked', 'consent_already_revoked'],
  ['tz_change_requires_migration', 'tz_change_requires_migration'],
  ['approval_transition_invalid', 'approval_transition_invalid'],
  ['approval_action_unsupported', 'approval_action_unsupported'],
  ['period_overlap', 'period_overlap'],
  ['too_many_streams', 'too_many_streams'],
  ['qualification_exists', 'qualification_exists'],
  ['subject_inactive', 'subject_inactive'],
  // Field-error codes of the course template rules (#64 F3).
  ['subject_unknown', 'subject_unknown'],
  ['duplicate_subject', 'duplicate_subject'],
  ['duplicate_title', 'duplicate_title'],
  ['module_subject_not_in_template', 'module_subject_not_in_template'],
  ['planned_sessions_exceed_total', 'planned_sessions_exceed_total'],
  ['module_index_invalid', 'module_index_invalid'],
  ['item_unknown', 'item_unknown'],
  ['qualification_not_approved', 'qualification_not_approved'],
  ['availability_change_stale', 'availability_change_stale'],
  ['availability_change_not_pending', 'availability_change_not_pending'],
  ['scheduling_conflict', 'scheduling_conflict'],
  ['class_session_not_assignable', 'class_session_not_assignable'],
  ['class_session_not_offerable', 'class_session_not_offerable'],
  ['offer_not_pending', 'offer_not_pending'],
  ['offer_expired', 'offer_expired'],
  ['reassignment_still_needed', 'reassignment_still_needed'],
  ['invalid_effective_from', 'invalid_effective_from'],
  ['policy_version_in_effect', 'policy_version_in_effect'],
  // Editing and cancelling series (#54).
  ['session_not_editable', 'session_not_editable'],
  ['series_offer_pending', 'series_offer_pending'],
  ['series_ended', 'series_ended'],
  // Substitutions and cancellations (#55).
  ['class_session_not_substitutable', 'class_session_not_substitutable'],
  ['class_session_not_cancellable', 'class_session_not_cancellable'],
  ['substitution_no_longer_valid', 'substitution_no_longer_valid'],
  // Overriding a conflict occurrence (#236).
  ['session_not_in_conflict', 'session_not_in_conflict'],
  ['occurrence_started', 'occurrence_started'],
  // Tutoring groups (#56, #234).
  ['group_not_found', 'group_not_found'],
  ['enrollment_not_found', 'enrollment_not_found'],
  ['group_name_taken', 'group_name_taken'],
  ['group_ended', 'group_ended'],
  ['already_enrolled', 'already_enrolled'],
  ['enrollment_not_active', 'enrollment_not_active'],
  // Course cohorts (#65).
  ['cohort_not_found', 'cohort_not_found'],
  ['cohort_name_taken', 'cohort_name_taken'],
  ['template_unknown', 'template_unknown'],
  ['template_inactive', 'template_inactive'],
  ['subject_not_in_template', 'subject_not_in_template'],
  ['too_many_occurrences', 'too_many_occurrences'],
  // Cohort enrollment (#66; the other enrollment codes are the #56 ones).
  ['cohort_ended', 'cohort_ended'],
  // Session requests by families (#59).
  ['student_consent_required', 'student_consent_required'],
  ['slot_unavailable', 'slot_unavailable'],
  ['session_request_overlap', 'session_request_overlap'],
  ['session_request_limit_reached', 'session_request_limit_reached'],
  ['session_request_not_pending', 'session_request_not_pending'],
  ['session_request_started', 'session_request_started'],
  [
    'session_request_review_route_required',
    'session_request_review_route_required',
  ],
  // Change requests by families and tutors (#62).
  ['change_request_kind_unsupported', 'change_request_kind_unsupported'],
  ['class_session_not_changeable', 'class_session_not_changeable'],
  ['change_request_session_started', 'change_request_session_started'],
  ['change_request_pending', 'change_request_pending'],
  ['change_request_not_pending', 'change_request_not_pending'],
  ['change_request_session_changed', 'change_request_session_changed'],
  [
    'change_request_review_route_required',
    'change_request_review_route_required',
  ],
  // Attendance (#76).
  ['attendance_tutor_only', 'attendance_tutor_only'],
  ['attendance_not_recordable', 'attendance_not_recordable'],
  ['attendance_not_started', 'attendance_not_started'],
  ['attendance_locked', 'attendance_locked'],
  ['attendance_corrected', 'attendance_corrected'],
  ['attendance_unchanged', 'attendance_unchanged'],
  ['student_not_participant', 'student_not_participant'],
  ['duplicate_student', 'duplicate_student'],
  // Session closeout (#77).
  ['closeout_tutor_only', 'closeout_tutor_only'],
  ['closeout_already_submitted', 'closeout_already_submitted'],
  ['closeout_attendance_incomplete', 'closeout_attendance_incomplete'],
  // Closeout review (#78).
  ['closeout_review_route_required', 'closeout_review_route_required'],
  ['closeout_not_submitted', 'closeout_not_submitted'],
  ['closeout_with_tutor', 'closeout_with_tutor'],
  ['closeout_under_review', 'closeout_under_review'],
  ['closeout_self_review', 'closeout_self_review'],
  ['closeout_unchanged', 'closeout_unchanged'],
  ['session_not_closable', 'session_not_closable'],
  ['session_not_started', 'session_not_started'],
  ['summary_required', 'summary_required'],
  // Closeout on behalf (#301).
  ['session_not_ended', 'session_not_ended'],
  ['closeout_on_behalf_own_session', 'closeout_on_behalf_own_session'],
  ['closeout_submitted_on_behalf', 'closeout_submitted_on_behalf'],
  ['closeout_not_with_tutor', 'closeout_not_with_tutor'],
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
