import { ApiError } from '@/app/api';
import { codeMessageKey } from '@/app/errors';
import type { LocalDate } from '@/lib/time';
import { tuitionPeriodEnd, type PeriodFrequency } from '@/lib/tuition';
import type { ServicePrice } from '../pricing/api';
import {
  PLAN_STATUSES,
  type EligibleEnrollment,
  type PlanListFilters,
  type StudentOption,
  type TuitionPause,
  type TuitionPlanCreate,
  type TuitionPlanDetail,
  type UpcomingPeriod,
} from './api';

// The tuition plan pages' client rules (#374 C-E): the list filters in the
// URL, the create dialog's checks, choices and body, where a server 422
// goes, which actions a plan offers and the dates each action may take,
// and how an upcoming period is shown. The server stays authoritative
// (#93): these only stop a request that would surely fail.

// ------------------------------------------------------------ list filters

function pageParam(value: string | null): number {
  const page = Number(value);
  return Number.isInteger(page) && page > 1 ? page : 1;
}

/** The list filters from the URL (?status=…&student_id=&page=). */
export function planListFilters(params: URLSearchParams): PlanListFilters {
  const wanted = new Set(params.getAll('status'));
  return {
    // In the API's order, each once; unknown values are dropped.
    statuses: PLAN_STATUSES.filter((status) => wanted.has(status)),
    studentId: params.get('student_id') || null,
    page: pageParam(params.get('page')),
  };
}

/** The URL search params of the list filters (defaults left out). */
export function planListParams(filters: PlanListFilters): URLSearchParams {
  const params = new URLSearchParams();
  for (const status of filters.statuses) params.append('status', status);
  if (filters.studentId) params.set('student_id', filters.studentId);
  if (filters.page > 1) params.set('page', String(filters.page));
  return params;
}

// ------------------------------------------------------- the create dialog

export type PlanKind = 'individual' | 'group';
export type PlanEnd = 'open' | 'periods';

export const PERIODS_MIN = 1;
export const PERIODS_MAX = 120;
export const DUE_OFFSET_MIN = 0;
export const DUE_OFFSET_MAX = 60;

export interface PlanFormValues {
  student: StudentOption | null;
  kind: PlanKind;
  /** A group enrollment id ('' for none); group plans only. */
  enrollmentId: string;
  /** The per-period catalog price's id. */
  priceId: string;
  startsOn: string;
  end: PlanEnd;
  /** As typed; "After N periods" only. */
  periods: string;
  /** As typed. */
  dueOffset: string;
}

export type PlanField =
  | 'student'
  | 'kind'
  | 'enrollmentId'
  | 'priceId'
  | 'startsOn'
  | 'periods'
  | 'dueOffset';

/** The form's fields in display order (the first invalid one is focused). */
export const PLAN_FIELD_ORDER: readonly PlanField[] = [
  'student',
  'kind',
  'enrollmentId',
  'priceId',
  'startsOn',
  'periods',
  'dueOffset',
];

/** A `common` key, or an `errors` key prefixed with `errors:`. */
export type PlanMessage = string;
export type PlanErrors = Partial<Record<PlanField, PlanMessage>>;

const V = 'billing.tuition.validation';

export function emptyPlanForm(today: LocalDate): PlanFormValues {
  return {
    student: null,
    kind: 'individual',
    enrollmentId: '',
    priceId: '',
    startsOn: today,
    end: 'open',
    periods: '12',
    dueOffset: '0',
  };
}

/**
 * The values after one field changed, kept consistent: a new student or
 * type clears the enrollment and the price (their choices depend on them),
 * a new enrollment clears the price.
 */
export function changePlanForm<K extends keyof PlanFormValues>(
  values: PlanFormValues,
  field: K,
  value: PlanFormValues[K],
): PlanFormValues {
  const next = { ...values, [field]: value };
  if (field === 'student' || field === 'kind') {
    next.enrollmentId = '';
    next.priceId = '';
  }
  if (field === 'enrollmentId') {
    next.priceId = '';
  }
  return next;
}

/** Why an enrollment can't take a plan, if it can't (#93 K6). */
export function enrollmentBlock(
  enrollment: EligibleEnrollment,
): 'has_plan' | 'has_fee_charge' | null {
  if (enrollment.has_plan) return 'has_plan';
  if (enrollment.has_fee_charge) return 'has_fee_charge';
  return null;
}

/**
 * The per-period prices a plan of this type can pin (#93 D3): individual
 * ones for an individual plan; for a group enrollment, group ones scoped to
 * its group or unscoped for its subject. None until an enrollment is
 * chosen for a group plan.
 */
export function pricesForPlan(
  prices: readonly ServicePrice[],
  kind: PlanKind,
  enrollment: EligibleEnrollment | null,
): ServicePrice[] {
  if (kind === 'individual') {
    return prices.filter((price) => price.class_type === 'individual');
  }
  if (!enrollment) return [];
  return prices.filter(
    (price) =>
      price.class_type === 'group' &&
      (price.group
        ? price.group.id === enrollment.group.id
        : price.subject.id === enrollment.subject.id),
  );
}

const WHOLE_NUMBER = /^\d+$/;
const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

function wholeIn(value: string, min: number, max: number): boolean {
  const text = value.trim();
  const number = Number(text);
  return WHOLE_NUMBER.test(text) && number >= min && number <= max;
}

export function validatePlanForm(values: PlanFormValues): PlanErrors {
  const errors: PlanErrors = {};
  if (!values.student) errors.student = `${V}.studentRequired`;
  if (values.kind === 'group' && !values.enrollmentId) {
    errors.enrollmentId = `${V}.enrollmentRequired`;
  }
  if (!values.priceId) errors.priceId = `${V}.priceRequired`;
  if (!ISO_DATE.test(values.startsOn)) errors.startsOn = `${V}.dateRequired`;
  if (
    values.end === 'periods' &&
    !wholeIn(values.periods, PERIODS_MIN, PERIODS_MAX)
  ) {
    errors.periods = `${V}.periodsRange`;
  }
  if (!wholeIn(values.dueOffset, DUE_OFFSET_MIN, DUE_OFFSET_MAX)) {
    errors.dueOffset = `${V}.dueOffsetRange`;
  }
  return errors;
}

/**
 * The plan's last day for "After N periods" (#374 D1: the web mirror of
 * the #93 K3 grid), or null while the start, the price or N isn't valid.
 */
export function planEndsOn(
  values: Pick<PlanFormValues, 'startsOn' | 'end' | 'periods'>,
  frequency: PeriodFrequency | null | undefined,
): LocalDate | null {
  if (
    values.end !== 'periods' ||
    !frequency ||
    !ISO_DATE.test(values.startsOn) ||
    !wholeIn(values.periods, PERIODS_MIN, PERIODS_MAX)
  ) {
    return null;
  }
  return tuitionPeriodEnd(
    values.startsOn,
    frequency,
    Number(values.periods.trim()),
  );
}

/** The POST body (call after validatePlanForm found nothing). */
export function planCreateBody(
  values: PlanFormValues,
  frequency: PeriodFrequency | null | undefined,
): TuitionPlanCreate {
  const endsOn = planEndsOn(values, frequency);
  return {
    student_id: values.student?.id ?? '',
    service_price_id: values.priceId,
    ...(values.kind === 'group' && values.enrollmentId
      ? { group_enrollment_id: values.enrollmentId }
      : {}),
    starts_on: values.startsOn,
    ...(endsOn ? { ends_on: endsOn } : {}),
    due_offset_days: Number(values.dueOffset.trim()),
  };
}

const BODY_FIELDS: Record<string, PlanField> = {
  student_id: 'student',
  service_price_id: 'priceId',
  group_enrollment_id: 'enrollmentId',
  starts_on: 'startsOn',
  ends_on: 'periods',
  due_offset_days: 'dueOffset',
};

/** A top-level 422 code's field when it comes without a field error. */
const CODE_FIELDS: Record<string, PlanField> = {
  tuition_plan_price_invalid: 'priceId',
  tuition_plan_enrollment_invalid: 'enrollmentId',
  tuition_plan_starts_on_invalid: 'startsOn',
  tuition_plan_ends_on_invalid: 'periods',
};

/** The message of a server field code: its `errors` text when it has one. */
export function serverMessage(code: string): PlanMessage {
  return `errors:${codeMessageKey(code) ?? 'validation_error'}`;
}

/**
 * A 422's field errors on the create form's fields (the first per field),
 * or null when none is about a field the form has.
 */
export function planServerErrors(error: unknown): PlanErrors | null {
  if (!(error instanceof ApiError) || error.status !== 422) {
    return null;
  }
  const errors: PlanErrors = {};
  for (const fieldError of error.fieldErrors) {
    const [where, key] = fieldError.path;
    const field = where === 'body' ? BODY_FIELDS[String(key)] : undefined;
    if (field && !errors[field]) {
      errors[field] = serverMessage(fieldError.code);
    }
  }
  // The top-level code's field only when no field error says where (QA O3).
  const byCode = CODE_FIELDS[error.code];
  if (byCode && Object.keys(errors).length === 0) {
    errors[byCode] = serverMessage(error.code);
  }
  return Object.keys(errors).length > 0 ? errors : null;
}

/**
 * A 422 of one of the plan page's dialogs on its fields (`fields` maps a
 * body key to the dialog's field), or null.
 */
export function dialogServerErrors<F extends string>(
  error: unknown,
  fields: Record<string, F>,
  codeField?: Record<string, F>,
): Partial<Record<F, PlanMessage>> | null {
  if (!(error instanceof ApiError) || error.status !== 422) {
    return null;
  }
  const errors: Partial<Record<F, PlanMessage>> = {};
  for (const fieldError of error.fieldErrors) {
    const [where, key] = fieldError.path;
    const field = where === 'body' ? fields[String(key)] : undefined;
    if (field && !errors[field]) {
      errors[field] = serverMessage(fieldError.code);
    }
  }
  const byCode = codeField?.[error.code];
  if (byCode && Object.keys(errors).length === 0) {
    errors[byCode] = serverMessage(error.code);
  }
  return Object.keys(errors).length > 0 ? errors : null;
}

// ------------------------------------------------------- the plan's periods

/** Whether `day` falls inside one of the pauses. */
export function inPause(day: string, pauses: readonly TuitionPause[]): boolean {
  return pauses.some(
    (pause) =>
      pause.starts_on <= day &&
      (pause.ends_on === null || day <= pause.ends_on),
  );
}

export type PeriodDisplay =
  | 'charged'
  | 'upcoming'
  | 'due'
  | 'due_unbilled'
  | 'paused'
  | 'after_end'
  | 'enrollment_ended'
  | 'below_floor';

/**
 * How an upcoming period is shown (#374 E2; the #93 merge notes): a period
 * with a charge is charged whatever its state (a first-day withdrawal
 * reports enrollment_ended with a charge); a period inside a pause is
 * paused even when the API reports a future one as not_due; a due period
 * without a charge is either posted by the next run or not charged for a
 * reason. A linked plan that has ended with periods still listed ended
 * through the enrollment's withdrawal (a plan's own end stops the list):
 * its uncharged periods are shown as enrollment ended, also the future
 * ones the API reports not_due (#374 QA D1, owner-delegated).
 */
export function periodDisplay(
  period: UpcomingPeriod,
  plan: Pick<TuitionPlanDetail, 'pauses' | 'status' | 'group_enrollment_id'>,
): PeriodDisplay {
  if (period.charge_id) return 'charged';
  if (period.state === 'after_end') return 'after_end';
  if (
    plan.status === 'ended' &&
    plan.group_enrollment_id !== null &&
    period.state !== 'below_floor' &&
    period.state !== 'due'
  ) {
    return 'enrollment_ended';
  }
  if (period.state === 'paused' || inPause(period.period_start, plan.pauses)) {
    return 'paused';
  }
  if (period.state === 'enrollment_ended') return 'enrollment_ended';
  if (period.state === 'below_floor') return 'below_floor';
  if (period.state === 'due') return period.reason ? 'due_unbilled' : 'due';
  return 'upcoming';
}

// ---------------------------------------------------- the plan's actions

/** The pause that is still open (until resumed), if any. */
export function openPause(plan: TuitionPlanDetail): TuitionPause | null {
  return plan.pauses.find((pause) => pause.ends_on === null) ?? null;
}

/** Nothing has been charged: the plan can be deleted. */
export function canDeletePlan(plan: TuitionPlanDetail): boolean {
  return plan.posted_through === null && plan.recent_periods.length === 0;
}

/**
 * The upcoming period starts a pause may begin on (#93 D7): not charged,
 * after the last charged period, outside every pause. None while a pause
 * is open (resume it first) or once the plan has ended.
 */
export function pauseStarts(plan: TuitionPlanDetail): UpcomingPeriod[] {
  if (plan.status === 'ended' || openPause(plan)) return [];
  return plan.upcoming_periods.filter(
    (period) =>
      period.charge_id === null &&
      period.state !== 'after_end' &&
      period.state !== 'enrollment_ended' &&
      (plan.posted_through === null ||
        period.period_start > plan.posted_through) &&
      !inPause(period.period_start, plan.pauses),
  );
}

/** A pause of the plan begins after `start` (an open pause would overlap it). */
export function pauseAfter(plan: TuitionPlanDetail, start: string): boolean {
  return plan.pauses.some((pause) => pause.starts_on > start);
}

/**
 * The period ends a pause from `start` may end on: from that period on,
 * up to the period before the next pause begins.
 */
export function pauseEnds(
  plan: TuitionPlanDetail,
  start: string,
): UpcomingPeriod[] {
  const ends: UpcomingPeriod[] = [];
  for (const period of plan.upcoming_periods) {
    if (period.period_start < start) continue;
    if (inPause(period.period_start, plan.pauses)) break;
    if (period.state === 'after_end') break;
    ends.push(period);
  }
  return ends;
}

/**
 * The period starts the open pause may be resumed on: not before the
 * pause's start (resuming on its start removes it).
 */
export function resumeStarts(plan: TuitionPlanDetail): UpcomingPeriod[] {
  const pause = openPause(plan);
  if (!pause) return [];
  return plan.upcoming_periods.filter(
    (period) =>
      period.period_start >= pause.starts_on && period.state !== 'after_end',
  );
}

/**
 * The period ends the plan may end on (#93 D5): an upcoming period's end
 * on or after the last charged period's end. Empty once it has ended.
 */
export function endChoices(plan: TuitionPlanDetail): UpcomingPeriod[] {
  if (plan.status === 'ended') return [];
  return plan.upcoming_periods.filter(
    (period) =>
      period.state !== 'after_end' &&
      (plan.posted_through === null ||
        period.period_end >= plan.posted_through),
  );
}

/** The plan can still be ended, shortened or made open-ended. */
export function canEndPlan(plan: TuitionPlanDetail): boolean {
  return (
    plan.status !== 'ended' &&
    (endChoices(plan).length > 0 || plan.ends_on !== null)
  );
}

export function validDueOffset(value: string): boolean {
  return wholeIn(value, DUE_OFFSET_MIN, DUE_OFFSET_MAX);
}
