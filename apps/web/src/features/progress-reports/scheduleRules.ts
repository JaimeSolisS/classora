import { ApiError } from '@/app/api';
import { z } from '@/lib/forms';
import { addDays, addMonths, type LocalDate } from '@/lib/time';
import type { ScheduleCadence, ScheduleCreate } from './api';
import { monthStart, weekStart } from './rules';

// The pure rules of the report schedule panel (#316 E3, E5): the first
// period choices (the current period and the next 11, in the center's
// calendar, from an injected `today`) and where a refused add or change
// lands.

/** How many first periods the add dialog offers. */
export const FIRST_PERIOD_CHOICES = 12;

/** The Monday of `today`'s week, or the 1st of its month. */
export function currentPeriodStart(
  cadence: ScheduleCadence,
  today: string,
): LocalDate {
  return cadence === 'weekly' ? weekStart(today) : monthStart(today);
}

/** The current period's start and the next 11 (weeks or months). */
export function firstPeriodOptions(
  cadence: ScheduleCadence,
  today: string,
): LocalDate[] {
  const first = currentPeriodStart(cadence, today);
  return Array.from({ length: FIRST_PERIOD_CHOICES }, (_, n) =>
    cadence === 'weekly' ? addDays(first, 7 * n) : addMonths(first, n),
  );
}

/** The add dialog's fields, by the API's body names. */
export type ScheduleField = 'author_user_id' | 'subject_id' | 'starts_on';

export const SCHEDULE_FIELDS: readonly ScheduleField[] = [
  'author_user_id',
  'subject_id',
  'starts_on',
];

/** What a refused add or change shows (E5). Field messages are
 * `errors:<code>`. */
export type ScheduleRefusal =
  | { kind: 'fields'; errors: Partial<Record<ScheduleField, string>> }
  | { kind: 'exists' }
  | { kind: 'conflict' }
  | { kind: 'other' };

export function classifyScheduleError(error: unknown): ScheduleRefusal {
  if (!(error instanceof ApiError)) {
    return { kind: 'other' };
  }
  if (error.code === 'progress_report_schedule_exists') {
    return { kind: 'exists' };
  }
  if (error.code === 'version_conflict') {
    return { kind: 'conflict' };
  }
  if (error.code === 'validation_error') {
    const errors: Partial<Record<ScheduleField, string>> = {};
    for (const item of error.fieldErrors) {
      const field = item.path.filter((part) => part !== 'body')[0];
      if (
        typeof field === 'string' &&
        (SCHEDULE_FIELDS as readonly string[]).includes(field) &&
        !errors[field as ScheduleField]
      ) {
        errors[field as ScheduleField] = `errors:${item.code}`;
      }
    }
    if (Object.keys(errors).length > 0) {
      return { kind: 'fields', errors };
    }
  }
  return { kind: 'other' };
}

// --------------------------------------------------------- the add form

/** The add dialog's form (React Hook Form + Zod; '' = all subjects). */
export const scheduleFormSchema = z.object({
  author: z.string().min(1, 'progressReports.schedule.errors.tutorRequired'),
  cadence: z.enum(['weekly', 'monthly']),
  subject: z.string(),
  firstPeriod: z
    .string()
    .min(1, 'progressReports.schedule.errors.firstPeriodRequired'),
});

export type ScheduleFormValues = z.infer<typeof scheduleFormSchema>;

/** `POST …/progress-report-schedules` from the form: "All subjects" sends
 * no subject_id. */
export function scheduleBody(values: ScheduleFormValues): ScheduleCreate {
  return {
    author_user_id: values.author,
    cadence: values.cadence,
    starts_on: values.firstPeriod,
    ...(values.subject ? { subject_id: values.subject } : {}),
  };
}
