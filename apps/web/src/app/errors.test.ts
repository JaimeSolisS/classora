import { afterEach, describe, expect, it, vi } from 'vitest';
import { i18n } from '@/i18n';
import { LANGUAGES } from '@/i18n/language';
import { ApiError } from './api';
import {
  errorMessage,
  errorMessageKey,
  errorRequestId,
  MAPPED_ERROR_CODES,
  reportError,
} from './errors';

// E1 (H4): every known code has its own key in both languages, unknown codes
// fall back by status, and the server's text never reaches the UI.

const API_CODES = [
  'validation_error',
  'not_found',
  'method_not_allowed',
  'internal_error',
  'database_unavailable',
  'invalid_cursor',
  'version_conflict',
  'authentication_required',
  'permission_denied',
  'invalid_credentials',
  'csrf_failed',
  'session_expired',
  'reauth_required',
  'reauth_failed',
  'rate_limited',
  'token_invalid',
  'username_taken',
  'username_invalid',
  'password_too_short',
  'password_too_long',
  'password_equals_username',
  'current_password_invalid',
  'user_not_active',
  'registration_already_decided',
  'last_active_owner',
  'user_state_transition_invalid',
  'delegation_forbidden',
  'permission_not_delegable',
  'delegation_target_invalid',
  'name_taken',
  'education_level_has_active_subjects',
  'education_level_inactive',
  'education_level_unavailable',
  'emergency_contact_unknown',
  'guardian_account_unavailable',
  'guardian_account_in_use',
  'guardian_unavailable',
  'guardian_already_linked',
  'family_member_conflict',
  'family_inactive',
  'privacy_notice_required',
  'privacy_notice_outdated',
  'student_profile_required',
  'guardian_consent_required',
  'student_account_unavailable',
  'student_account_in_use',
  'consent_method_invalid',
  'consent_guardian_invalid',
  'consent_not_applicable',
  'consent_already_given',
  'consent_already_revoked',
  'tz_change_requires_migration',
  'approval_transition_invalid',
  'approval_action_unsupported',
  'period_overlap',
  'too_many_streams',
  'qualification_exists',
  'subject_inactive',
  'subject_unknown',
  'duplicate_subject',
  'duplicate_title',
  'module_subject_not_in_template',
  'planned_sessions_exceed_total',
  'module_index_invalid',
  'item_unknown',
  'qualification_not_approved',
  'availability_change_stale',
  'availability_change_not_pending',
  'scheduling_conflict',
  'class_session_not_assignable',
  'class_session_not_offerable',
  'offer_not_pending',
  'offer_expired',
  'reassignment_still_needed',
  'invalid_effective_from',
  'policy_version_in_effect',
  'session_not_editable',
  'series_offer_pending',
  'series_ended',
  'session_not_in_conflict',
  'occurrence_started',
  'class_session_not_substitutable',
  'class_session_not_cancellable',
  'substitution_no_longer_valid',
  'group_not_found',
  'enrollment_not_found',
  'group_name_taken',
  'group_ended',
  'already_enrolled',
  'enrollment_not_active',
  'cohort_not_found',
  'cohort_name_taken',
  'template_unknown',
  'template_inactive',
  'subject_not_in_template',
  'too_many_occurrences',
  'cohort_ended',
  'student_consent_required',
  'slot_unavailable',
  'session_request_overlap',
  'session_request_limit_reached',
  'session_request_not_pending',
  'session_request_started',
  'session_request_review_route_required',
  'change_request_kind_unsupported',
  'class_session_not_changeable',
  'change_request_session_started',
  'change_request_pending',
  'change_request_not_pending',
  'change_request_session_changed',
  'change_request_review_route_required',
  'attendance_tutor_only',
  'attendance_not_recordable',
  'attendance_not_started',
  'attendance_locked',
  'attendance_corrected',
  'attendance_unchanged',
  'student_not_participant',
  'duplicate_student',
  'closeout_tutor_only',
  'closeout_already_submitted',
  'closeout_attendance_incomplete',
  'closeout_review_route_required',
  'closeout_not_submitted',
  'closeout_with_tutor',
  'closeout_under_review',
  'closeout_self_review',
  'closeout_unchanged',
  'session_not_closable',
  'session_not_started',
  'summary_required',
  'session_not_ended',
  'closeout_on_behalf_own_session',
  'closeout_submitted_on_behalf',
  'closeout_not_with_tutor',
  'note_tutor_only',
  'note_author_only',
  'note_read_only',
  'note_published',
  'note_already_published',
  'note_not_published',
  'session_not_notable',
  // #308 C8, B2: the evaluation and template codes.
  'evaluation_tutor_only',
  'evaluation_author_only',
  'evaluation_read_only',
  'duplicate_name',
  'session_not_evaluable',
  'evaluation_final',
  'evaluation_not_draft',
  'evaluation_not_final',
  'evaluation_voided',
  'evaluation_incomplete',
  'score_out_of_range',
  'score_precision',
  'decimal_invalid',
  'change_reason_required',
  'score_required',
  'reason_required',
  'evaluated_on_in_future',
  'evaluated_on_invalid',
  'subject_required',
  'subject_mismatch',
  'class_session_invalid',
  'course_assessment_unknown',
  'course_assessment_requires_course_session',
  'criterion_unknown',
  'duplicate_criterion',
  'criteria_required',
  'max_score_out_of_range',
  'kind_immutable',
  'too_long',
  'name_required',
  'title_required',
  // #310 C6, D6: the learning plan codes.
  'plan_subject_not_assigned',
  'plan_read_only',
  'plan_closed',
  'plan_not_draft',
  'plan_not_active',
  'active_plan_exists',
  'objectives_required',
  'milestones_required',
  'target_before_start',
  'due_on_invalid',
  'objective_unknown',
  'milestone_unknown',
  // #313 B6, C5, E6: the homework codes.
  'assignment_tutor_only',
  'assignment_creator_only',
  'assignment_read_only',
  'submission_student_only',
  'session_not_assignable',
  'submission_graded',
  'submission_locked',
  'submission_required',
  'assignment_has_submissions',
  'assignment_has_grades',
  'recipient_has_submission',
  'instructions_required',
  'duplicate_recipient',
  'recipients_required',
  'recipient_invalid',
  'due_in_past',
  'due_too_far',
  'time_has_offset',
  'null_not_allowed',
  'version_required',
  'url_invalid',
  'submission_empty',
  'score_not_allowed',
  'feedback_required',
  // #315 B6, C9, E4: the progress report codes.
  'progress_report_tutor_only',
  'report_subject_not_assigned',
  'progress_report_author_only',
  'progress_report_read_only',
  'progress_report_self_review',
  'progress_report_under_review',
  'progress_report_not_submitted',
  'progress_report_closed',
  'progress_report_exists',
  'progress_report_author_not_current',
  'progress_report_review_route_required',
  'period_invalid',
  'period_in_future',
  // #316 E5: the report schedule codes.
  'progress_report_schedule_exists',
  'schedule_author_invalid',
  'schedule_subject_not_assigned',
  'schedule_starts_on_invalid',
  'student_inactive',
  // #322 D1: the progress dashboard's range in the future.
  'range_in_future',
  // #363 E1: the money input codes (#87) and the price catalog codes (#89).
  'money_invalid',
  'money_precision',
  'money_out_of_range',
  'amount_negative',
  'service_price_exists',
  'price_effective_from_invalid',
  'price_version_in_effect',
  'price_version_in_use',
  'pricing_model_not_allowed',
  'scope_not_allowed',
  'scope_conflict',
  'duration_not_allowed',
  'duration_out_of_range',
  'level_not_allowed',
  'date_out_of_range',
  'education_level_unknown',
  'cohort_unknown',
  'group_unknown',
  'scope_subject_mismatch',
  // #367 F1: the discount and custom price codes (#95).
  'discount_name_exists',
  'discount_assignment_exists',
  'audience_not_assigned',
  'audience_not_allowed',
  'custom_price_overlap',
  'custom_price_effective_from_invalid',
  'custom_price_expiry_invalid',
  'custom_price_in_effect',
  'custom_price_in_use',
  'field_immutable',
  'value_required',
  'value_conflict',
  'value_not_null',
  'percent_invalid',
  'percent_out_of_range',
  'percent_precision',
  'amount_not_positive',
  'dates_required',
  'dates_order',
  'criteria_empty_list',
  'min_family_students_required',
  'min_family_students_not_allowed',
  'min_family_students_out_of_range',
  'target_required',
  'target_conflict',
  'family_unknown',
  'service_price_unknown',
  'student_unknown',
  'period_frequency_required',
  'period_frequency_not_allowed',
  'tuition_plan_price_invalid',
  'tuition_plan_enrollment_invalid',
  'tuition_plan_starts_on_invalid',
  'tuition_plan_ends_on_invalid',
  'tuition_pause_invalid',
  'tuition_resume_invalid',
  'student_has_no_family',
  'tuition_plan_overlap',
  'tuition_plan_enrollment_fee_charged',
  'tuition_plan_sessions_charged',
  'tuition_plan_ended',
  'tuition_plan_has_charges',
  'tuition_pause_overlap',
  'tuition_pause_not_open',
];
// Spellings confirmed with status_code_name() (apps/api/app/core/errors.py).
const STATUS_CODES = [
  'bad_request',
  'unauthorized',
  'forbidden',
  'conflict',
  'content_too_large',
  'too_many_requests',
];
const CLIENT_CODES = ['network_error', 'unexpected_response'];
const ALL_CODES = [...API_CODES, ...STATUS_CODES, ...CLIENT_CODES];
// #308: codes whose catalog key differs (i18next reads `_many` as a plural
// suffix, so `criteria_too_many` can't be a key).
const RENAMED_CODES: Record<string, string> = {
  criteria_too_many: 'criteria_limit',
  // #310: the learning plan limits.
  objectives_too_many: 'objectives_limit',
  milestones_too_many: 'milestones_limit',
  // #313: the homework recipient limit.
  recipients_too_many: 'recipients_limit',
};

afterEach(() => {
  vi.restoreAllMocks();
});

// #66 I3: cohort enrollment reuses the #56/#49 codes; each is listed once
// (the repeated-key check of the locale files is i18n/check.test.ts).
describe('cohort enrollment codes (#66 I3)', () => {
  it.each([
    'already_enrolled',
    'enrollment_not_found',
    'enrollment_not_active',
    'cohort_ended',
  ])('the API code %s is listed once', (code) => {
    expect(API_CODES.filter((c) => c === code)).toHaveLength(1);
    expect(MAPPED_ERROR_CODES.filter((c) => c === code)).toHaveLength(1);
  });

  it.each(['max_students_below_enrolled', 'reason_too_long'])(
    'the field code %s has one message and is no API code',
    (code) => {
      expect(API_CODES).not.toContain(code);
      for (const language of LANGUAGES) {
        expect(i18n.exists(code, { lng: language, ns: 'errors' })).toBe(true);
      }
    },
  );
});

describe('errorMessageKey', () => {
  it('maps exactly the listed codes', () => {
    expect([...MAPPED_ERROR_CODES].sort()).toEqual(
      [...ALL_CODES, ...Object.keys(RENAMED_CODES)].sort(),
    );
  });

  it.each(Object.entries(RENAMED_CODES))(
    'maps %s to the key %s',
    (code, key) => {
      expect(errorMessageKey(new ApiError({ code, status: 422 }))).toBe(key);
    },
  );

  it.each(ALL_CODES)('maps %s to its own key', (code) => {
    expect(errorMessageKey(new ApiError({ code, status: 400 }))).toBe(code);
  });

  it.each([
    [
      'an unknown 4xx code',
      new ApiError({ code: 'new_4xx', status: 418 }),
      'client_error',
    ],
    [
      'an unknown 5xx code',
      new ApiError({ code: 'new_5xx', status: 504 }),
      'server_error',
    ],
    [
      'the local-only demo code',
      new ApiError({ code: 'demo_job_already_queued', status: 409 }),
      'client_error',
    ],
    [
      'an unknown code without status',
      new ApiError({ code: 'odd' }),
      'generic',
    ],
    [
      'a prototype key as code',
      new ApiError({ code: 'constructor' }),
      'generic',
    ],
    ['an Error', new Error('boom'), 'generic'],
    ['a string', 'boom', 'generic'],
    ['undefined', undefined, 'generic'],
  ] as const)('falls back for %s', (_name, error, key) => {
    expect(errorMessageKey(error)).toBe(key);
  });
});

describe('error messages in both languages', () => {
  const KEYS = [
    ...ALL_CODES,
    ...Object.values(RENAMED_CODES),
    'client_error',
    'server_error',
    'generic',
  ];

  it.each(KEYS)(
    '%s has a real message in es and en, different per language',
    (key) => {
      const messages = LANGUAGES.map((language) =>
        i18n.getFixedT(language, 'errors')(key as 'generic'),
      );
      for (const message of messages) {
        expect(message.trim()).not.toBe('');
        expect(message).not.toBe(key);
        expect(message).not.toMatch(/errors:|\d{3}/);
      }
      // No key is intentionally identical in both languages.
      expect(messages[0]).not.toBe(messages[1]);
    },
  );

  it('resolves through the given t in the active language', () => {
    const error = new ApiError({ code: 'not_found', status: 404 });
    expect(errorMessage(error, i18n.getFixedT('es', 'errors'))).toBe(
      'No encontramos lo que buscas.',
    );
    expect(errorMessage(error, i18n.getFixedT('en', 'errors'))).toBe(
      "We couldn't find what you were looking for.",
    );
  });

  it('never returns the server detail or title', () => {
    for (const language of LANGUAGES) {
      const t = i18n.getFixedT(language, 'errors');
      for (const code of [...ALL_CODES, 'unknown_code']) {
        const error = new ApiError({
          code,
          status: 400,
          problem: {
            type: 'about:blank',
            title: 'SERVER TITLE',
            status: 400,
            code,
            request_id: 'req-1',
            detail: 'SERVER DETAIL',
          },
        });
        const message = errorMessage(error, t);
        expect(message).not.toContain('SERVER');
      }
    }
  });
});

describe('errorRequestId', () => {
  it('returns the request ID of an ApiError only', () => {
    expect(errorRequestId(new ApiError({ code: 'x', requestId: 'r1' }))).toBe(
      'r1',
    );
    expect(errorRequestId(new ApiError({ code: 'x' }))).toBeUndefined();
    expect(errorRequestId(new Error('x'))).toBeUndefined();
  });
});

describe('reportError', () => {
  it('logs an ApiError as code, status and request ID only', () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    reportError(
      new ApiError({
        code: 'not_found',
        status: 404,
        requestId: 'req-9',
        problem: {
          type: 'about:blank',
          title: 'Not Found',
          status: 404,
          code: 'not_found',
          request_id: 'req-9',
          detail: 'SERVER DETAIL',
        },
      }),
      { source: 'query' },
    );
    expect(spy).toHaveBeenCalledTimes(1);
    expect(spy).toHaveBeenCalledWith(
      '[classora]',
      { source: 'query' },
      { code: 'not_found', status: 404, requestId: 'req-9' },
    );
    expect(JSON.stringify(spy.mock.calls)).not.toContain('SERVER DETAIL');
  });
});

// #322 D1: the progress pages' range and subject refusals each have a
// message in both languages (range_too_long uses its own progress text:
// the catalog's range_too_long is the calendar's 45-day limit).
describe('the progress range messages (#322 D1)', () => {
  it.each([
    'range_invalid',
    'range_in_future',
    'progress_range_too_long',
    'subject_unknown',
  ])('%s has a message in both languages', (key) => {
    for (const language of LANGUAGES) {
      expect(i18n.exists(key, { lng: language, ns: 'errors' })).toBe(true);
    }
  });

  it('the progress period limit names 24 months, not the calendar limit', () => {
    expect(i18n.t('progress_range_too_long', { lng: 'en', ns: 'errors' })).toBe(
      'Choose a period of at most 24 months.',
    );
  });
});
