import type { TFunction } from 'i18next';
import { ApiError } from '@/app/api';
import { errorMessage } from '@/app/errors';
import type { ImportRowIssue } from './api';
import { MAX_FILE_BYTES } from './api';

// The import codes (M8-05b C4): the per-row codes of #122 B4 (plus the
// commit-time code of #122 D1) and of the #123 kinds (B2, B4, C2, C3), each
// with an `imports.rowCodes.*` label, and the messages of a refused file
// (B3). A failed schedules row also lists the conflict codes of its
// `scheduling_conflict` (#123 B4), labelled with the scheduling rule names
// (`scheduling.rules.*`). A row code is not a problem code: it never reaches
// the `errors` catalog. An unknown code shows "Invalid value" with the code
// (C2).

/** The row codes the API sends (pinned in imports.test.tsx). */
export const ROW_CODES = [
  'required',
  'invalid',
  'too_long',
  'invalid_date',
  'future_date',
  'invalid_email',
  'invalid_phone',
  'education_level_unknown',
  'education_level_unavailable',
  'username_invalid',
  'username_taken',
  'duplicate_existing',
  'duplicate_in_file',
  // #123: both new kinds name a student by the natural key.
  'student_unknown',
  'student_ambiguous',
  'student_inactive',
  // #123 B2, B4: schedules.
  'subject_unknown',
  'subject_inactive',
  'tutor_unknown',
  'tutor_inactive',
  'classroom_unknown',
  'classroom_inactive',
  'time_format',
  'duration_out_of_range',
  'weekdays_empty',
  'weekday_out_of_range',
  'weekdays_duplicate',
  'interval_out_of_range',
  'until_and_count',
  'count_out_of_range',
  'until_before_start',
  'no_occurrences',
  'starts_on_in_past',
  'crosses_local_date',
  'scheduling_conflict',
  'series_not_offerable',
  // #123 C2, C3: opening balances.
  'student_has_no_family',
  'student_not_in_family',
  'invalid_amount',
  'amount_not_positive',
  'date_out_of_range',
] as const;
export type RowCode = (typeof ROW_CODES)[number];

export function isRowCode(code: string): code is RowCode {
  return (ROW_CODES as readonly string[]).includes(code);
}

/**
 * The conflict codes a `scheduling_conflict` row lists (#123 B4: the hard
 * and soft codes of ADR-0005), labelled by `scheduling.rules.*`.
 */
export const CONFLICT_CODES = [
  'tutor_overlap',
  'room_overlap',
  'student_overlap',
  'room_capacity_exceeded',
  'tutor_unavailable',
  'room_unavailable',
  'center_closed',
  'outside_operating_hours',
  'tutor_not_qualified',
  'group_capacity_exceeded',
  'notice_window',
] as const;
export type ConflictCode = (typeof CONFLICT_CODES)[number];

export function isConflictCode(code: string): code is ConflictCode {
  return (CONFLICT_CODES as readonly string[]).includes(code);
}

/**
 * The `common` key of a row code's label: `imports.rowCodes.*`, or
 * `scheduling.rules.*` for a conflict code; undefined for an unknown code
 * (#130's inventory check calls this).
 */
export function rowCodeLabelKey(
  code: string,
):
  | `imports.rowCodes.${RowCode}`
  | `scheduling.rules.${ConflictCode}`
  | undefined {
  if (isRowCode(code)) {
    return `imports.rowCodes.${code}`;
  }
  if (isConflictCode(code)) {
    return `scheduling.rules.${code}`;
  }
  return undefined;
}

function codeText(code: string, t: TFunction): string {
  const key = rowCodeLabelKey(code);
  return key ? t(key) : t('imports.rowCodes.unknown', { code });
}

/** One error or warning: "column: message", or the message for the row. */
export function issueText(issue: ImportRowIssue, t: TFunction): string {
  const message = codeText(issue.code, t);
  return issue.column
    ? t('imports.rows.issue', { column: issue.column, message })
    : message;
}

/** The `import_file_invalid` reasons with their own message. */
export const FILE_INVALID_REASONS = [
  'encoding',
  'empty',
  'malformed',
  'duplicate_column',
  'missing_columns',
  'too_many_columns',
] as const;
type FileInvalidReason = (typeof FILE_INVALID_REASONS)[number];

function isFileInvalidReason(value: unknown): value is FileInvalidReason {
  return (FILE_INVALID_REASONS as readonly unknown[]).includes(value);
}

/** A details value as a number, or undefined. */
function numberDetail(error: ApiError, name: string): number | undefined {
  const value = error.problem?.details?.[name];
  return typeof value === 'number' ? value : undefined;
}

/** A details value as a list of strings, or an empty list. */
function stringsDetail(error: ApiError, name: string): string[] {
  const value = error.problem?.details?.[name];
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === 'string')
    : [];
}

/** The client-side refusal of a file over 2 MiB (E2), before sending. */
export function fileTooLargeMessage(t: TFunction): string {
  return t('imports.file.tooLarge', {
    max: Math.round(MAX_FILE_BYTES / (1024 * 1024)),
  });
}

/**
 * The message for a failed `POST /imports` (B3): the import codes with
 * their details (never a value, #122), else the code's general message.
 */
export function createErrorMessage(
  error: unknown,
  t: TFunction,
  tErrors: TFunction<'errors'>,
): string {
  if (error instanceof ApiError) {
    if (error.code === 'import_too_large') {
      return fileTooLargeMessage(t);
    }
    if (error.code === 'import_too_many_rows') {
      return t('imports.file.tooManyRows', {
        max: numberDetail(error, 'max') ?? 1000,
      });
    }
    if (error.code === 'import_file_invalid') {
      const reason = error.problem?.details?.reason;
      if (!isFileInvalidReason(reason)) {
        return t('imports.file.invalid.generic');
      }
      switch (reason) {
        case 'malformed': {
          const line = numberDetail(error, 'line');
          return line === undefined
            ? t('imports.file.invalid.malformed')
            : t('imports.file.invalid.malformedLine', { line });
        }
        case 'missing_columns':
          return t('imports.file.invalid.missing_columns', {
            columns: stringsDetail(error, 'columns').join(', '),
          });
        case 'too_many_columns':
          return t('imports.file.invalid.too_many_columns', {
            max: numberDetail(error, 'max') ?? 30,
          });
        default:
          return t(`imports.file.invalid.${reason}`);
      }
    }
  }
  return errorMessage(error, tErrors);
}
