import type { components } from '@classora/api-client';
import { addDays, addMonths, parseInstant, type LocalDate } from '@/lib/time';
import type { ExportDatasetCode } from './access';
import type {
  ExportColumn,
  ExportFormat,
  ExportJob,
  ExportParameters,
} from './api';

// The request form's rules (M8-07b B1–B3) and the history's (C1), without
// React, so the tests and the page share them. The client checks mirror
// the API (#124 A3, E6, D5): `from ≤ to`, `to − from < 366` days (so 366
// days with both ends included), at least one column.

type ExpenseStatus = components['schemas']['ExpenseStatus'];
type SessionKind = components['schemas']['SessionKindCode'];
type StudentStatus = components['schemas']['StudentStatus'];

/** The longest range the API accepts: `to − from` must stay below it. */
export const MAX_RANGE_DAYS = 366;

const DAY_MS = 24 * 60 * 60 * 1000;

export const EXPENSE_STATUS_FILTERS: readonly ExpenseStatus[] = [
  'unpaid',
  'partially_paid',
  'overdue',
  'paid',
  'void',
];
export const SESSION_KIND_FILTERS: readonly SessionKind[] = [
  'individual',
  'group',
  'course',
];
export const STUDENT_STATUS_FILTERS: readonly StudentStatus[] = [
  'active',
  'inactive',
  'all',
];

/** One row of the column checklist, in the user's order. */
export interface ColumnChoice {
  code: string;
  checked: boolean;
}

export interface SessionFilters {
  from: string;
  to: string;
  tutor: { id: string; name: string } | null;
  student: { id: string; name: string } | null;
  classroom: string;
  kind: SessionKind | '';
  includeCancelled: boolean;
}

export interface ExpenseFilters {
  incurredFrom: string;
  incurredTo: string;
  statuses: ExpenseStatus[];
}

export interface StudentFilters {
  status: StudentStatus;
  educationLevel: string;
}

export interface ExportFormValues {
  dataset: ExportDatasetCode;
  format: ExportFormat;
  columns: ColumnChoice[];
  /** A checked groupable column, or '' for "no grouping". */
  groupBy: string;
  sessions: SessionFilters;
  expenses: ExpenseFilters;
  students: StudentFilters;
}

/** The fields a message can attach to (client checks and API problems). */
export type ExportField =
  'from' | 'to' | 'incurredFrom' | 'incurredTo' | 'columns';

export type ExportFieldErrors = Partial<
  Record<
    ExportField,
    'required' | 'rangeReversed' | 'rangeTooLong' | 'noColumns' | 'invalid'
  >
>;

/** The current month in the center's zone (B1's `class_sessions` default). */
export function currentMonth(today: LocalDate): { from: string; to: string } {
  const from = `${today.slice(0, 8)}01`;
  return { from, to: addDays(addMonths(from, 1), -1) };
}

/** Every column checked, in catalogue order. */
export function defaultColumns(
  columns: readonly ExportColumn[],
): ColumnChoice[] {
  return columns.map((column) => ({ code: column.code, checked: true }));
}

export function defaultValues(
  dataset: ExportDatasetCode,
  columns: readonly ExportColumn[],
  today: LocalDate,
  format: ExportFormat = 'csv',
): ExportFormValues {
  return {
    dataset,
    format,
    columns: defaultColumns(columns),
    groupBy: '',
    sessions: {
      ...currentMonth(today),
      tutor: null,
      student: null,
      classroom: '',
      kind: '',
      includeCancelled: false,
    },
    expenses: { incurredFrom: '', incurredTo: '', statuses: [] },
    students: { status: 'active', educationLevel: '' },
  };
}

/** Moves the column at `index` by `delta` (−1 up, +1 down), if it can. */
export function moveColumn(
  columns: readonly ColumnChoice[],
  index: number,
  delta: -1 | 1,
): ColumnChoice[] {
  const target = index + delta;
  if (index < 0 || index >= columns.length || target < 0) {
    return [...columns];
  }
  if (target >= columns.length) {
    return [...columns];
  }
  const next = [...columns];
  [next[index], next[target]] = [next[target]!, next[index]!];
  return next;
}

/** The checked groupable columns, in the user's order (B1 "group by"). */
export function groupOptions(
  choices: readonly ColumnChoice[],
  catalogue: readonly ExportColumn[],
): string[] {
  const groupable = new Set(
    catalogue.filter((column) => column.groupable).map((column) => column.code),
  );
  return choices
    .filter((choice) => choice.checked && groupable.has(choice.code))
    .map((choice) => choice.code);
}

/** The grouping to send: '' when the chosen column is no longer offered. */
export function effectiveGroupBy(
  values: ExportFormValues,
  catalogue: readonly ExportColumn[],
): string {
  return groupOptions(values.columns, catalogue).includes(values.groupBy)
    ? values.groupBy
    : '';
}

function daysBetween(from: string, to: string): number {
  return Math.round(
    (Date.parse(`${to}T00:00:00Z`) - Date.parse(`${from}T00:00:00Z`)) / DAY_MS,
  );
}

/** The API's range rule (#124 D5): reversed → at the end date, too long →
 * at the start date. */
function checkRange(
  errors: ExportFieldErrors,
  from: string,
  to: string,
  fields: { from: ExportField; to: ExportField },
) {
  const span = daysBetween(from, to);
  if (span < 0) {
    errors[fields.to] = 'rangeReversed';
  } else if (span >= MAX_RANGE_DAYS) {
    errors[fields.from] = 'rangeTooLong';
  }
}

/** B2: the client checks; an empty object means the form may be sent. */
export function validate(values: ExportFormValues): ExportFieldErrors {
  const errors: ExportFieldErrors = {};
  if (!values.columns.some((choice) => choice.checked)) {
    errors.columns = 'noColumns';
  }
  if (values.dataset === 'class_sessions') {
    const { from, to } = values.sessions;
    if (!from) errors.from = 'required';
    if (!to) errors.to = 'required';
    if (from && to) {
      checkRange(errors, from, to, { from: 'from', to: 'to' });
    }
  } else if (values.dataset === 'expenses') {
    const { incurredFrom, incurredTo } = values.expenses;
    if (incurredFrom && incurredTo) {
      checkRange(errors, incurredFrom, incurredTo, {
        from: 'incurredFrom',
        to: 'incurredTo',
      });
    }
  }
  return errors;
}

/** The API's filter names → the form's fields (422 problems, B2). */
export const SERVER_FILTER_FIELDS: Readonly<Record<string, ExportField>> = {
  from: 'from',
  to: 'to',
  incurred_from: 'incurredFrom',
  incurred_to: 'incurredTo',
};

/**
 * B3: the body. `columns` only when they differ from the default (every
 * column in catalogue order); `group_by` only when one is chosen.
 */
export function exportParameters(
  values: ExportFormValues,
  catalogue: readonly ExportColumn[],
): ExportParameters {
  const chosen = values.columns
    .filter((choice) => choice.checked)
    .map((choice) => choice.code);
  const isDefault =
    chosen.length === catalogue.length &&
    chosen.every((code, index) => code === catalogue[index]?.code);
  const groupBy = effectiveGroupBy(values, catalogue);
  // The keys in the API model's order (dataset, format, columns, group_by,
  // filters; each filter model's own order), so a saved setup's stored
  // parameters and this body are the same JSON text (M8-08 D3).
  const common = {
    format: values.format,
    ...(isDefault ? {} : { columns: chosen }),
    ...(groupBy ? { group_by: groupBy } : {}),
  };
  switch (values.dataset) {
    case 'class_sessions': {
      const s = values.sessions;
      return {
        dataset: 'class_sessions',
        ...common,
        filters: {
          from: s.from,
          to: s.to,
          ...(s.tutor ? { tutor_user_id: s.tutor.id } : {}),
          ...(s.student ? { student_id: s.student.id } : {}),
          ...(s.classroom ? { classroom_id: s.classroom } : {}),
          ...(s.kind ? { kind: s.kind } : {}),
          include_cancelled: s.includeCancelled,
        },
      } as ExportParameters;
    }
    case 'expenses': {
      const e = values.expenses;
      return {
        dataset: 'expenses',
        ...common,
        filters: {
          ...(e.statuses.length > 0
            ? {
                status: EXPENSE_STATUS_FILTERS.filter((status) =>
                  e.statuses.includes(status),
                ),
              }
            : {}),
          ...(e.incurredFrom ? { incurred_from: e.incurredFrom } : {}),
          ...(e.incurredTo ? { incurred_to: e.incurredTo } : {}),
        },
      } as ExportParameters;
    }
    case 'students': {
      const st = values.students;
      return {
        dataset: 'students',
        ...common,
        filters: {
          status: st.status,
          ...(st.educationLevel
            ? { education_level_id: st.educationLevel }
            : {}),
        },
      } as ExportParameters;
    }
  }
}

/**
 * M8-08 (#125 D2 "Cargar en el formulario"): the form values of saved
 * parameters, the reverse of {@link exportParameters}. The stored columns
 * come first, checked and in their order, then the others unchecked in
 * catalogue order. The parameters hold only a tutor's or student's id, so
 * the chosen person shows `personName`. `exportParameters` of the result
 * gives the same body back.
 */
export function valuesFromParameters(
  parameters: ExportParameters,
  catalogue: readonly ExportColumn[],
  today: LocalDate,
  personName: string,
): ExportFormValues {
  const values = defaultValues(
    parameters.dataset,
    catalogue,
    today,
    parameters.format,
  );
  const known = catalogue.map((column) => column.code);
  const chosen = (parameters.columns ?? known).filter((code: string) =>
    known.includes(code),
  );
  values.columns = [
    ...chosen.map((code: string) => ({ code, checked: true })),
    ...known
      .filter((code) => !chosen.includes(code))
      .map((code) => ({ code, checked: false })),
  ];
  values.groupBy = parameters.group_by ?? '';
  switch (parameters.dataset) {
    case 'class_sessions': {
      const f = parameters.filters;
      values.sessions = {
        from: f.from,
        to: f.to,
        tutor: f.tutor_user_id
          ? { id: f.tutor_user_id, name: personName }
          : null,
        student: f.student_id ? { id: f.student_id, name: personName } : null,
        classroom: f.classroom_id ?? '',
        kind: f.kind ?? '',
        includeCancelled: f.include_cancelled ?? false,
      };
      break;
    }
    case 'expenses': {
      const f = parameters.filters;
      values.expenses = {
        incurredFrom: f?.incurred_from ?? '',
        incurredTo: f?.incurred_to ?? '',
        statuses: [...(f?.status ?? [])],
      };
      break;
    }
    case 'students': {
      const f = parameters.filters;
      values.students = {
        status: f?.status ?? 'active',
        educationLevel: f?.education_level_id ?? '',
      };
      break;
    }
  }
  return values;
}

/** What the history shows for a job's file (C1, E2). */
export type ExportAvailability =
  | { kind: 'pending' }
  | { kind: 'download'; expiresAt: string }
  | { kind: 'expired' }
  | { kind: 'failed'; code: ExportJob['error_code'] };

/** A ready export is downloadable only before `expires_at` (E2). */
export function availability(job: ExportJob, now: Date): ExportAvailability {
  switch (job.status) {
    case 'queued':
      return { kind: 'pending' };
    case 'failed':
      return { kind: 'failed', code: job.error_code };
    case 'expired':
      return { kind: 'expired' };
    case 'ready':
      if (
        job.expires_at === null ||
        parseInstant(job.expires_at).getTime() <= now.getTime()
      ) {
        return { kind: 'expired' };
      }
      return { kind: 'download', expiresAt: job.expires_at };
  }
}

/** The time left until `expiresAt`, in whole hours, or minutes under one
 * hour (at least 1): "caduca en 5 h" (C1). */
export function timeLeft(
  expiresAt: string,
  now: Date,
): { hours: number } | { minutes: number } {
  const left = Math.max(0, parseInstant(expiresAt).getTime() - now.getTime());
  const minutes = Math.max(1, Math.floor(left / 60_000));
  return minutes >= 60 ? { hours: Math.floor(minutes / 60) } : { minutes };
}

/** A file size in the UI language: kB under 1 MiB (at least 0.1), then
 * MB, one decimal at most. */
export function formatBytes(bytes: number, language: string): string {
  const unit =
    bytes < 1024 * 1024
      ? { unit: 'kilobyte', value: Math.max(0.1, bytes / 1024) }
      : { unit: 'megabyte', value: bytes / (1024 * 1024) };
  return new Intl.NumberFormat(language, {
    style: 'unit',
    unit: unit.unit,
    unitDisplay: 'short',
    maximumFractionDigits: 1,
  }).format(unit.value);
}
