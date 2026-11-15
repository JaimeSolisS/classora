import { ApiError } from '@/app/api';
import { codeMessageKey } from '@/app/errors';
import { localDayBounds, parseLocalDate, type LocalDate } from '@/lib/time';
import type {
  CancellationOutcome,
  CancellationRule,
  CancellationRuleCreate,
  CancellationRuleOverview,
} from './api';

// The cancellation charges card's rules (#385), without React: the four
// classifications in display order, the latest version (the one a new
// version replaces), when each outcome of the version in force began, the
// "Schedule a change" form (prefilled from the latest version, the excused
// outcome sent unchanged, an optional date that starts at 00:00 in the
// center's zone and must be after today) and where a server error lands.

export type OutcomeField =
  'on_time_outcome' | 'late_outcome' | 'no_show_outcome' | 'excused_outcome';

/** The classifications in display order (#385 A2). */
export const OUTCOME_FIELDS: readonly OutcomeField[] = [
  'on_time_outcome',
  'late_outcome',
  'no_show_outcome',
  'excused_outcome',
];

/** The outcomes the dialog edits; `excused_outcome` is inert (#99 Q6). */
export const EDITABLE_FIELDS = [
  'on_time_outcome',
  'late_outcome',
  'no_show_outcome',
] as const satisfies readonly OutcomeField[];

export type EditableField = (typeof EDITABLE_FIELDS)[number];

/** The `billing.settings.cancellation.classes` key of each field. */
export const FIELD_KEYS = {
  on_time_outcome: 'onTime',
  late_outcome: 'late',
  no_show_outcome: 'noShow',
  excused_outcome: 'excused',
} as const satisfies Record<OutcomeField, string>;

export interface CancellationFormValues {
  on_time_outcome: CancellationOutcome;
  late_outcome: CancellationOutcome;
  no_show_outcome: CancellationOutcome;
  /** `YYYY-MM-DD` in the center's zone, or empty for "now". */
  effectiveDate: string;
}

/** A `billing.settings.cancellation.validation` key, or an `errors:` key. */
export type CancellationDateError =
  | 'billing.settings.cancellation.validation.dateInvalid'
  | 'billing.settings.cancellation.validation.dateAfterToday'
  | `errors:${string}`;

/** The latest version: the last upcoming one, else the one in force. */
export function latestRule(
  overview: CancellationRuleOverview,
): CancellationRule {
  return overview.upcoming.at(-1) ?? overview.current;
}

/**
 * When the in-force outcome of `field` began: the oldest version of the
 * unbroken run (from the one in force back through the history, newest
 * first) that has the same outcome. The excused outcome always reads
 * "credited" (#99 Q6), so its run is every version listed.
 */
export function outcomeSince(
  overview: CancellationRuleOverview,
  field: OutcomeField,
): string {
  const outcome = overview.current[field];
  let since = overview.current.effective_from;
  for (const rule of overview.history) {
    if (field !== 'excused_outcome' && rule[field] !== outcome) {
      break;
    }
    since = rule.effective_from;
  }
  return since;
}

export function initialCancellationForm(
  latest: CancellationRule,
): CancellationFormValues {
  return {
    on_time_outcome: latest.on_time_outcome,
    late_outcome: latest.late_outcome,
    no_show_outcome: latest.no_show_outcome,
    effectiveDate: '',
  };
}

function isRealDay(value: string): boolean {
  try {
    parseLocalDate(value);
    return true;
  } catch {
    return false;
  }
}

/** The date's error, or undefined (an empty date means "now"). */
export function validateCancellationForm(
  values: CancellationFormValues,
  today: LocalDate,
): CancellationDateError | undefined {
  const date = values.effectiveDate;
  if (date === '') {
    return undefined;
  }
  if (!isRealDay(date)) {
    return 'billing.settings.cancellation.validation.dateInvalid';
  }
  if (date <= today) {
    return 'billing.settings.cancellation.validation.dateAfterToday';
  }
  return undefined;
}

/**
 * The POST body: every outcome, `based_on_id` and, for a date, the start of
 * that day in the center's zone (never the browser's). `excused_outcome` is
 * the latest version's, unchanged.
 */
export function cancellationCreateBody(
  values: CancellationFormValues,
  latest: CancellationRule,
  timeZone: string,
): CancellationRuleCreate {
  const body: CancellationRuleCreate = {
    based_on_id: latest.id,
    excused_outcome: latest.excused_outcome,
    on_time_outcome: values.on_time_outcome,
    late_outcome: values.late_outcome,
    no_show_outcome: values.no_show_outcome,
  };
  if (values.effectiveDate !== '') {
    body.effective_from = localDayBounds(values.effectiveDate, timeZone).start;
  }
  return body;
}

export type CancellationFormField = EditableField | 'effectiveDate';
export type CancellationFieldErrors = Partial<
  Record<CancellationFormField, CancellationDateError>
>;

const SERVER_FIELDS: Record<string, CancellationFormField> = {
  on_time_outcome: 'on_time_outcome',
  late_outcome: 'late_outcome',
  no_show_outcome: 'no_show_outcome',
  effective_from: 'effectiveDate',
};

/**
 * A 422's messages by form field (`invalid_effective_from` lands on the
 * date), or null when none is about a field the form shows.
 */
export function cancellationServerErrors(
  error: unknown,
): CancellationFieldErrors | null {
  if (!(error instanceof ApiError) || error.status !== 422) {
    return null;
  }
  if (error.code === 'invalid_effective_from') {
    return { effectiveDate: 'errors:invalid_effective_from' };
  }
  const found: CancellationFieldErrors = {};
  for (const fieldError of error.fieldErrors) {
    const field =
      fieldError.path[0] === 'body'
        ? SERVER_FIELDS[String(fieldError.path[1])]
        : undefined;
    if (field !== undefined && found[field] === undefined) {
      found[field] =
        `errors:${codeMessageKey(fieldError.code) ?? 'validation_error'}`;
    }
  }
  return Object.keys(found).length > 0 ? found : null;
}

export function isApiCode(error: unknown, code: string): boolean {
  return error instanceof ApiError && error.code === code;
}

export function isNotFound(error: unknown): boolean {
  return error instanceof ApiError && error.status === 404;
}
