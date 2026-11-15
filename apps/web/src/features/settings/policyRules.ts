import { z } from '@/lib/forms';
import { localDayBounds, type LocalDate } from '@/lib/time';
import type {
  SchedulingPolicy,
  SchedulingPolicyCreate,
  SchedulingPolicyOverview,
} from './api';

// Pure helpers of the scheduling policy page (#61 F2): the form's Zod
// schema (mirroring the A1 ranges and D2), the form values of a version, the
// POST body and the rule fields that differ between two versions. UX only:
// the API validates every request.

/** The API's ranges (administration/settings_models.py), inclusive. */
export const NOTICE_HOURS_MIN = 0;
export const NOTICE_HOURS_MAX = 336;
export const RESPONSE_HOURS_MIN = 1;
export const RESPONSE_HOURS_MAX = 168;
export const MAKEUP_WINDOW_DAYS_MIN = 1;
export const MAKEUP_WINDOW_DAYS_MAX = 365;

export type NumberField =
  | 'bookingNoticeHours'
  | 'changeNoticeHours'
  | 'responseHours'
  | 'makeupWindowDays';
export type FlagField =
  'makeupAfterOnTime' | 'makeupAfterLate' | 'makeupAfterNoShow';
export type RuleField = NumberField | FlagField;

export interface PolicyFormValues {
  /** Whole numbers as typed. */
  bookingNoticeHours: string;
  changeNoticeHours: string;
  responseHours: string;
  makeupWindowDays: string;
  makeupAfterOnTime: boolean;
  makeupAfterLate: boolean;
  makeupAfterNoShow: boolean;
  takesEffect: 'now' | 'date';
  /** `YYYY-MM-DD` in the center zone, used with `takesEffect: 'date'`. */
  effectiveDate: string;
}

/** The API field of each rule, in display order. */
export const RULE_API_FIELDS = {
  changeNoticeHours: 'change_notice_hours',
  bookingNoticeHours: 'booking_notice_hours',
  responseHours: 'response_hours',
  makeupAfterOnTime: 'makeup_after_on_time',
  makeupAfterLate: 'makeup_after_late',
  makeupAfterNoShow: 'makeup_after_no_show',
  makeupWindowDays: 'makeup_window_days',
} as const satisfies Record<RuleField, keyof SchedulingPolicyCreate>;

export const RULE_FIELDS = Object.keys(RULE_API_FIELDS) as RuleField[];

export const NUMBER_RANGES: Record<NumberField, [number, number]> = {
  bookingNoticeHours: [NOTICE_HOURS_MIN, NOTICE_HOURS_MAX],
  changeNoticeHours: [NOTICE_HOURS_MIN, NOTICE_HOURS_MAX],
  responseHours: [RESPONSE_HOURS_MIN, RESPONSE_HOURS_MAX],
  makeupWindowDays: [MAKEUP_WINDOW_DAYS_MIN, MAKEUP_WINDOW_DAYS_MAX],
};

export type PolicyMessageKey =
  | 'settings.schedulingPolicies.validation.noticeRange'
  | 'settings.schedulingPolicies.validation.responseRange'
  | 'settings.schedulingPolicies.validation.windowRange'
  | 'settings.schedulingPolicies.validation.dateRequired'
  | 'settings.schedulingPolicies.validation.dateAfterToday';

const RANGE_MESSAGES: Record<NumberField, PolicyMessageKey> = {
  bookingNoticeHours: 'settings.schedulingPolicies.validation.noticeRange',
  changeNoticeHours: 'settings.schedulingPolicies.validation.noticeRange',
  responseHours: 'settings.schedulingPolicies.validation.responseRange',
  makeupWindowDays: 'settings.schedulingPolicies.validation.windowRange',
};

const WHOLE_NUMBER = /^\d{1,4}$/;
const LOCAL_DATE = /^\d{4}-\d{2}-\d{2}$/;

function wholeNumber(field: NumberField) {
  const [min, max] = NUMBER_RANGES[field];
  return z
    .string()
    .trim()
    .refine(
      (value) =>
        WHOLE_NUMBER.test(value) &&
        Number(value) >= min &&
        Number(value) <= max,
      { message: RANGE_MESSAGES[field] },
    );
}

/** The form's schema; `today` is the center's date (a "From date" must be
 * later). */
export function policyFormSchema(today: LocalDate) {
  return z
    .object({
      bookingNoticeHours: wholeNumber('bookingNoticeHours'),
      changeNoticeHours: wholeNumber('changeNoticeHours'),
      responseHours: wholeNumber('responseHours'),
      makeupWindowDays: wholeNumber('makeupWindowDays'),
      makeupAfterOnTime: z.boolean(),
      makeupAfterLate: z.boolean(),
      makeupAfterNoShow: z.boolean(),
      takesEffect: z.enum(['now', 'date']),
      effectiveDate: z.string(),
    })
    .superRefine((values, context) => {
      if (values.takesEffect !== 'date') {
        return;
      }
      if (!LOCAL_DATE.test(values.effectiveDate)) {
        context.addIssue({
          code: 'custom',
          path: ['effectiveDate'],
          message: 'settings.schedulingPolicies.validation.dateRequired',
        });
      } else if (values.effectiveDate <= today) {
        context.addIssue({
          code: 'custom',
          path: ['effectiveDate'],
          message: 'settings.schedulingPolicies.validation.dateAfterToday',
        });
      }
    });
}

/** The latest version: the last upcoming one, else the one in force. */
export function latestVersion(
  overview: SchedulingPolicyOverview,
): SchedulingPolicy {
  return overview.upcoming.at(-1) ?? overview.current;
}

/** The form's values, prefilled from `policy`. */
export function policyValues(policy: SchedulingPolicy): PolicyFormValues {
  return {
    bookingNoticeHours: String(policy.booking_notice_hours),
    changeNoticeHours: String(policy.change_notice_hours),
    responseHours: String(policy.response_hours),
    makeupWindowDays: String(policy.makeup_window_days),
    makeupAfterOnTime: policy.makeup_after_on_time,
    makeupAfterLate: policy.makeup_after_late,
    makeupAfterNoShow: policy.makeup_after_no_show,
    takesEffect: 'now',
    effectiveDate: '',
  };
}

function ruleValue(values: PolicyFormValues, field: RuleField) {
  const value = values[field];
  return typeof value === 'boolean' ? value : Number(value.trim());
}

/** True when a rule differs from `policy` (Save is disabled otherwise). */
export function rulesChanged(
  values: PolicyFormValues,
  policy: SchedulingPolicy,
): boolean {
  return RULE_FIELDS.some(
    (field) => ruleValue(values, field) !== policy[RULE_API_FIELDS[field]],
  );
}

/**
 * The POST body: every rule, `based_on_id` and, for "From date", the start
 * of that date in the center's zone (never the browser's).
 */
export function createBody(
  values: PolicyFormValues,
  basedOnId: string,
  timeZone: string,
): SchedulingPolicyCreate {
  const body: SchedulingPolicyCreate = {
    based_on_id: basedOnId,
    booking_notice_hours: Number(values.bookingNoticeHours.trim()),
    change_notice_hours: Number(values.changeNoticeHours.trim()),
    response_hours: Number(values.responseHours.trim()),
    makeup_window_days: Number(values.makeupWindowDays.trim()),
    makeup_after_on_time: values.makeupAfterOnTime,
    makeup_after_late: values.makeupAfterLate,
    makeup_after_no_show: values.makeupAfterNoShow,
  };
  if (values.takesEffect === 'date') {
    body.effective_from = localDayBounds(values.effectiveDate, timeZone).start;
  }
  return body;
}

/** The rule fields whose values differ between two versions. */
export function changedRules(
  before: SchedulingPolicy,
  after: SchedulingPolicy,
): RuleField[] {
  return RULE_FIELDS.filter(
    (field) => before[RULE_API_FIELDS[field]] !== after[RULE_API_FIELDS[field]],
  );
}

/** Maps an API field error's name to the form field. */
export const SERVER_FIELDS: Record<string, keyof PolicyFormValues> = {
  ...Object.fromEntries(
    RULE_FIELDS.map((field) => [RULE_API_FIELDS[field], field]),
  ),
  effective_from: 'effectiveDate',
};
