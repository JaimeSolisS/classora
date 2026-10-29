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
  // Session notes (#79).
  ['note_tutor_only', 'note_tutor_only'],
  ['note_author_only', 'note_author_only'],
  ['note_read_only', 'note_read_only'],
  ['note_published', 'note_published'],
  ['note_already_published', 'note_already_published'],
  ['note_not_published', 'note_not_published'],
  ['session_not_notable', 'session_not_notable'],
  // Evaluations and templates (#80, #308 C8, B2), problem and field codes.
  ['evaluation_tutor_only', 'evaluation_tutor_only'],
  ['evaluation_author_only', 'evaluation_author_only'],
  ['evaluation_read_only', 'evaluation_read_only'],
  ['duplicate_name', 'duplicate_name'],
  ['session_not_evaluable', 'session_not_evaluable'],
  ['evaluation_final', 'evaluation_final'],
  ['evaluation_not_draft', 'evaluation_not_draft'],
  ['evaluation_not_final', 'evaluation_not_final'],
  ['evaluation_voided', 'evaluation_voided'],
  ['evaluation_incomplete', 'evaluation_incomplete'],
  ['score_out_of_range', 'score_out_of_range'],
  ['score_precision', 'score_precision'],
  ['decimal_invalid', 'decimal_invalid'],
  ['change_reason_required', 'change_reason_required'],
  ['score_required', 'score_required'],
  ['reason_required', 'reason_required'],
  ['evaluated_on_in_future', 'evaluated_on_in_future'],
  ['evaluated_on_invalid', 'evaluated_on_invalid'],
  ['subject_required', 'subject_required'],
  ['subject_mismatch', 'subject_mismatch'],
  ['class_session_invalid', 'class_session_invalid'],
  ['course_assessment_unknown', 'course_assessment_unknown'],
  [
    'course_assessment_requires_course_session',
    'course_assessment_requires_course_session',
  ],
  ['criterion_unknown', 'criterion_unknown'],
  ['duplicate_criterion', 'duplicate_criterion'],
  ['criteria_required', 'criteria_required'],
  // i18next reads a `_many` suffix as a plural form: its own key name.
  ['criteria_too_many', 'criteria_limit'],
  ['max_score_out_of_range', 'max_score_out_of_range'],
  ['kind_immutable', 'kind_immutable'],
  ['too_long', 'too_long'],
  ['name_required', 'name_required'],
  ['title_required', 'title_required'],
  // Learning plans (#81, #310 C6, D6), problem and field codes.
  ['plan_subject_not_assigned', 'plan_subject_not_assigned'],
  ['plan_read_only', 'plan_read_only'],
  ['plan_closed', 'plan_closed'],
  ['plan_not_draft', 'plan_not_draft'],
  ['plan_not_active', 'plan_not_active'],
  ['active_plan_exists', 'active_plan_exists'],
  ['objectives_required', 'objectives_required'],
  // i18next reads a `_many` suffix as a plural form: their own key names.
  ['objectives_too_many', 'objectives_limit'],
  ['milestones_required', 'milestones_required'],
  ['milestones_too_many', 'milestones_limit'],
  ['target_before_start', 'target_before_start'],
  ['due_on_invalid', 'due_on_invalid'],
  ['objective_unknown', 'objective_unknown'],
  ['milestone_unknown', 'milestone_unknown'],
  // Homework (#82, #313 B6, C5, E6), problem and field codes.
  ['assignment_tutor_only', 'assignment_tutor_only'],
  ['assignment_creator_only', 'assignment_creator_only'],
  ['assignment_read_only', 'assignment_read_only'],
  ['submission_student_only', 'submission_student_only'],
  ['session_not_assignable', 'session_not_assignable'],
  ['submission_graded', 'submission_graded'],
  ['submission_locked', 'submission_locked'],
  ['submission_required', 'submission_required'],
  ['assignment_has_submissions', 'assignment_has_submissions'],
  ['assignment_has_grades', 'assignment_has_grades'],
  ['recipient_has_submission', 'recipient_has_submission'],
  ['instructions_required', 'instructions_required'],
  ['duplicate_recipient', 'duplicate_recipient'],
  ['recipients_required', 'recipients_required'],
  // i18next reads a `_many` suffix as a plural form: its own key name.
  ['recipients_too_many', 'recipients_limit'],
  ['recipient_invalid', 'recipient_invalid'],
  ['due_in_past', 'due_in_past'],
  ['due_too_far', 'due_too_far'],
  ['time_has_offset', 'time_has_offset'],
  ['null_not_allowed', 'null_not_allowed'],
  ['version_required', 'version_required'],
  ['url_invalid', 'url_invalid'],
  ['submission_empty', 'submission_empty'],
  ['score_not_allowed', 'score_not_allowed'],
  ['feedback_required', 'feedback_required'],
  // Progress reports (#83, #315 B6, C9, E4), problem and field codes.
  ['progress_report_tutor_only', 'progress_report_tutor_only'],
  ['report_subject_not_assigned', 'report_subject_not_assigned'],
  ['progress_report_author_only', 'progress_report_author_only'],
  ['progress_report_read_only', 'progress_report_read_only'],
  ['progress_report_self_review', 'progress_report_self_review'],
  ['progress_report_under_review', 'progress_report_under_review'],
  ['progress_report_not_submitted', 'progress_report_not_submitted'],
  ['progress_report_closed', 'progress_report_closed'],
  ['progress_report_exists', 'progress_report_exists'],
  ['progress_report_author_not_current', 'progress_report_author_not_current'],
  [
    'progress_report_review_route_required',
    'progress_report_review_route_required',
  ],
  ['period_invalid', 'period_invalid'],
  ['period_in_future', 'period_in_future'],
  // Report schedules (#316 E5), problem and field codes.
  ['progress_report_schedule_exists', 'progress_report_schedule_exists'],
  ['schedule_author_invalid', 'schedule_author_invalid'],
  ['schedule_subject_not_assigned', 'schedule_subject_not_assigned'],
  ['schedule_starts_on_invalid', 'schedule_starts_on_invalid'],
  ['student_inactive', 'student_inactive'],
  // The progress dashboard's range refusal (#322 D1; #84 sends it as a
  // field code of validation_error, mapped here too).
  ['range_in_future', 'range_in_future'],
  // Money inputs (#87 codes, mapped by the first money field, #363 E1).
  ['money_invalid', 'money_invalid'],
  ['money_precision', 'money_precision'],
  ['money_out_of_range', 'money_out_of_range'],
  ['amount_negative', 'amount_negative'],
  // The price catalog (#89, #363 E1), problem and field codes
  // (subject_unknown, subject_inactive and education_level_inactive are
  // listed above).
  ['service_price_exists', 'service_price_exists'],
  ['price_effective_from_invalid', 'price_effective_from_invalid'],
  ['price_version_in_effect', 'price_version_in_effect'],
  ['price_version_in_use', 'price_version_in_use'],
  ['pricing_model_not_allowed', 'pricing_model_not_allowed'],
  ['scope_not_allowed', 'scope_not_allowed'],
  ['scope_conflict', 'scope_conflict'],
  ['duration_not_allowed', 'duration_not_allowed'],
  ['duration_out_of_range', 'duration_out_of_range'],
  ['level_not_allowed', 'level_not_allowed'],
  ['date_out_of_range', 'date_out_of_range'],
  ['education_level_unknown', 'education_level_unknown'],
  ['cohort_unknown', 'cohort_unknown'],
  ['group_unknown', 'group_unknown'],
  ['scope_subject_mismatch', 'scope_subject_mismatch'],
  // Discounts and custom prices (#95, #367 F1), problem and field codes
  // (criteria_too_many is listed above; subject_*, education_level_*,
  // money_*, amount_negative, pricing_model_not_allowed and
  // date_out_of_range with the price catalog).
  ['discount_name_exists', 'discount_name_exists'],
  ['discount_assignment_exists', 'discount_assignment_exists'],
  ['audience_not_assigned', 'audience_not_assigned'],
  ['audience_not_allowed', 'audience_not_allowed'],
  ['custom_price_overlap', 'custom_price_overlap'],
  [
    'custom_price_effective_from_invalid',
    'custom_price_effective_from_invalid',
  ],
  ['custom_price_expiry_invalid', 'custom_price_expiry_invalid'],
  ['custom_price_in_effect', 'custom_price_in_effect'],
  ['custom_price_in_use', 'custom_price_in_use'],
  ['field_immutable', 'field_immutable'],
  ['value_required', 'value_required'],
  ['value_conflict', 'value_conflict'],
  ['value_not_null', 'value_not_null'],
  ['percent_invalid', 'percent_invalid'],
  ['percent_out_of_range', 'percent_out_of_range'],
  ['percent_precision', 'percent_precision'],
  ['amount_not_positive', 'amount_not_positive'],
  ['dates_required', 'dates_required'],
  ['dates_order', 'dates_order'],
  ['criteria_empty_list', 'criteria_empty_list'],
  ['min_family_students_required', 'min_family_students_required'],
  ['min_family_students_not_allowed', 'min_family_students_not_allowed'],
  ['min_family_students_out_of_range', 'min_family_students_out_of_range'],
  ['target_required', 'target_required'],
  ['target_conflict', 'target_conflict'],
  ['family_unknown', 'family_unknown'],
  ['service_price_unknown', 'service_price_unknown'],
  ['student_unknown', 'student_unknown'],
  // Per-period prices and tuition plans (#93, #374 G1), problem and field
  // codes (student_unknown, student_inactive and date_out_of_range are
  // listed above).
  ['period_frequency_required', 'period_frequency_required'],
  ['period_frequency_not_allowed', 'period_frequency_not_allowed'],
  ['tuition_plan_price_invalid', 'tuition_plan_price_invalid'],
  ['tuition_plan_enrollment_invalid', 'tuition_plan_enrollment_invalid'],
  ['tuition_plan_starts_on_invalid', 'tuition_plan_starts_on_invalid'],
  ['tuition_plan_ends_on_invalid', 'tuition_plan_ends_on_invalid'],
  ['tuition_pause_invalid', 'tuition_pause_invalid'],
  ['tuition_resume_invalid', 'tuition_resume_invalid'],
  ['student_has_no_family', 'student_has_no_family'],
  ['tuition_plan_overlap', 'tuition_plan_overlap'],
  [
    'tuition_plan_enrollment_fee_charged',
    'tuition_plan_enrollment_fee_charged',
  ],
  ['tuition_plan_sessions_charged', 'tuition_plan_sessions_charged'],
  ['tuition_plan_ended', 'tuition_plan_ended'],
  ['tuition_plan_has_charges', 'tuition_plan_has_charges'],
  ['tuition_pause_overlap', 'tuition_pause_overlap'],
  ['tuition_pause_not_open', 'tuition_pause_not_open'],
  // Packages (#94, #377 F1), problem and field codes (student_unknown,
  // student_has_no_family, subject_*, money_*, amount_negative,
  // date_out_of_range and price_version_* are listed above).
  ['package_product_inactive', 'package_product_inactive'],
  ['package_not_offered', 'package_not_offered'],
  ['package_product_unknown', 'package_product_unknown'],
  ['quantity_out_of_range', 'quantity_out_of_range'],
  ['quantity_step', 'quantity_step'],
  ['validity_days_out_of_range', 'validity_days_out_of_range'],
  // Idempotency keys (#88; the first web consumer is the package sale,
  // #377 D2).
  ['idempotency_key_required', 'idempotency_key_required'],
  ['idempotency_key_invalid', 'idempotency_key_invalid'],
  ['idempotency_key_reused', 'idempotency_key_reused'],
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

/** The `errors` key of a server code, or undefined when it has none. */
export function codeMessageKey(code: string): ErrorMessageKey | undefined {
  return ERROR_CODE_KEYS.get(code);
}

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
