import { ApiError } from '@/app/api';
import { formatHundredths, hundredthsOf } from '@/lib/decimal';
import { z } from '@/lib/forms';
import {
  addDays,
  isoWeekday,
  parseLocalDate,
  type LocalDate,
} from '@/lib/time';
import type {
  ProgressReport,
  ReportCreate,
  ReportKind,
  ReportStatus,
  ReportUpdate,
} from './api';
import { REPORT_KINDS, REPORT_STATUSES } from './api';

// The pure rules of the progress report pages (#315 A6): the limits (the
// same as #83 A3), the period rules of the start step (all in the center's
// calendar, from an injected `today`), the form ↔ body mapping, the status
// labels, where a refused request's errors land, the view mode from R3's
// flags, the self-review rule and the history built from R3's fields.

// ----------------------------------------------------------------- limits

export const SUMMARY_MAX = 4000;
export const SECTION_MAX = 2000;
export const REASON_MAX = 1000;
/** An on-demand period: at most 366 days, both ends included. */
export const ON_DEMAND_MAX_DAYS = 366;
export const FACT_EVALUATIONS_MAX = 50;
export const FACT_PLANS_MAX = 20;

export const NARRATIVE_FIELDS = [
  'summary',
  'strengths',
  'areas_for_improvement',
  'next_steps',
] as const;

export type NarrativeField = (typeof NARRATIVE_FIELDS)[number];

export const NARRATIVE_MAX: Readonly<Record<NarrativeField, number>> = {
  summary: SUMMARY_MAX,
  strengths: SECTION_MAX,
  areas_for_improvement: SECTION_MAX,
  next_steps: SECTION_MAX,
};

/** A message: a `common` key of our own rules, or `errors:<code>` for a
 * server code (shown with its `errors` text). */
export type ReportMessage = string;

/** A decimal string as two places ("80.1" → "80.10"), through
 * lib/decimal (never a float); the API's text when it isn't one. */
export function decimalText(value: string): string {
  const hundredths = hundredthsOf(value);
  return hundredths === null ? value : formatHundredths(hundredths);
}

// ---------------------------------------------------------------- periods

/** Whole days from `from` to `to` (calendar dates, no zone). */
export function daysBetween(from: string, to: string): number {
  const day = (value: string) => {
    const [y, m, d] = parseLocalDate(value).split('-').map(Number) as [
      number,
      number,
      number,
    ];
    return Date.UTC(y, m - 1, d);
  };
  return Math.round((day(to) - day(from)) / 86_400_000);
}

/** The ISO Monday of the week holding `date`. */
export function weekStart(date: string): LocalDate {
  return addDays(date, 1 - isoWeekday(date));
}

/** The Sunday that ends the week starting on `monday`. */
export function weekEnd(monday: string): LocalDate {
  return addDays(monday, 6);
}

/** "This week" and "Last week": a day in each (their Mondays). */
export function thisWeek(today: string): LocalDate {
  return weekStart(today);
}

export function lastWeek(today: string): LocalDate {
  return addDays(weekStart(today), -7);
}

/** The 1st of the month holding `date`. */
export function monthStart(date: string): LocalDate {
  return `${parseLocalDate(date).slice(0, 7)}-01` as LocalDate;
}

/** The monthly choices: the current month and the 12 before it, newest
 * first, each as its 1st. */
export function monthOptions(today: string): LocalDate[] {
  const [year, month] = parseLocalDate(today).split('-').map(Number) as [
    number,
    number,
  ];
  const options: LocalDate[] = [];
  for (let back = 0; back <= 12; back += 1) {
    const index = year * 12 + (month - 1) - back;
    const y = Math.floor(index / 12);
    const m = index - y * 12 + 1;
    options.push(`${y}-${String(m).padStart(2, '0')}-01` as LocalDate);
  }
  return options;
}

/** The on-demand default: the last 7 days, today included. */
export function onDemandDefault(today: string): {
  from: LocalDate;
  to: LocalDate;
} {
  return { from: addDays(today, -6), to: parseLocalDate(today) };
}

// -------------------------------------------------------- the start step

export interface StartValues {
  kind: ReportKind;
  /** Weekly: any day of the week. */
  weekDate: string;
  /** Monthly: the 1st of the month. */
  month: string;
  /** On demand. */
  from: string;
  to: string;
  /** '' = all subjects. */
  subjectId: string;
}

export type StartField = 'week' | 'month' | 'from' | 'to' | 'subject';

export type StartErrors = Partial<Record<StartField, ReportMessage>>;

/** The start form's first values (B2, B3): On demand, the last 7 days. */
export function startValues(today: string): StartValues {
  const { from, to } = onDemandDefault(today);
  return {
    kind: 'on_demand',
    weekDate: today,
    month: monthStart(today),
    from,
    to,
    subjectId: '',
  };
}

const DATE = /^\d{4}-\d{2}-\d{2}$/;

function isDate(value: string): boolean {
  if (!DATE.test(value)) {
    return false;
  }
  try {
    parseLocalDate(value);
    return true;
  } catch {
    return false;
  }
}

/** The start form's own checks (B3); empty when it may be sent. */
export function startErrors(values: StartValues, today: string): StartErrors {
  const errors: StartErrors = {};
  if (values.kind === 'weekly') {
    if (!isDate(values.weekDate)) {
      errors.week = 'progressReports.start.errors.dateRequired';
    } else if (weekStart(values.weekDate) > today) {
      errors.week = 'progressReports.start.errors.weekNotStarted';
    }
  } else if (values.kind === 'monthly') {
    if (!monthOptions(today).includes(values.month as LocalDate)) {
      errors.month = 'progressReports.start.errors.monthRequired';
    }
  } else {
    const fromOk = isDate(values.from);
    const toOk = isDate(values.to);
    if (!fromOk) {
      errors.from = 'progressReports.start.errors.dateRequired';
    } else if (values.from > today) {
      errors.from = 'progressReports.start.errors.fromFuture';
    }
    if (!toOk) {
      errors.to = 'progressReports.start.errors.dateRequired';
    } else if (fromOk && values.to < values.from) {
      errors.to = 'progressReports.start.errors.toBeforeFrom';
    } else if (
      fromOk &&
      daysBetween(values.from, values.to) > ON_DEMAND_MAX_DAYS - 1
    ) {
      errors.to = 'progressReports.start.errors.tooLong';
    }
  }
  return errors;
}

/** The fields in visual order (focus goes to the first invalid one). */
export const START_FIELD_ORDER: readonly StartField[] = [
  'week',
  'month',
  'from',
  'to',
  'subject',
];

/** `POST /progress-reports` (B5): weekly and monthly send no period_end
 * (the server derives it); "All subjects" sends no subject_id; no
 * narrative. */
export function startBody(
  values: StartValues,
  studentId: string,
): ReportCreate {
  const subject = values.subjectId ? { subject_id: values.subjectId } : {};
  if (values.kind === 'weekly') {
    return {
      student_id: studentId,
      kind: 'weekly',
      period_start: weekStart(values.weekDate),
      ...subject,
    };
  }
  if (values.kind === 'monthly') {
    return {
      student_id: studentId,
      kind: 'monthly',
      period_start: monthStart(values.month),
      ...subject,
    };
  }
  return {
    student_id: studentId,
    kind: 'on_demand',
    period_start: values.from,
    period_end: values.to,
    ...subject,
  };
}

/** What a refused start shows (B6). */
export type StartRefusal =
  | { kind: 'fields'; errors: StartErrors }
  | { kind: 'exists'; reportId: string | null }
  | { kind: 'missing' }
  | { kind: 'other' };

function pathField(path: readonly (string | number)[]): string | undefined {
  const parts = path.filter((part) => part !== 'body');
  return typeof parts[0] === 'string' ? parts[0] : undefined;
}

/** Sorts a refused `POST /progress-reports` (B6). */
export function classifyStartError(
  error: unknown,
  kind: ReportKind,
): StartRefusal {
  if (!(error instanceof ApiError)) {
    return { kind: 'other' };
  }
  if (error.code === 'progress_report_exists') {
    const details: unknown = error.problem?.details;
    const id =
      typeof details === 'object' && details !== null
        ? (details as Record<string, unknown>).report_id
        : undefined;
    return { kind: 'exists', reportId: typeof id === 'string' ? id : null };
  }
  if (error.code === 'report_subject_not_assigned') {
    return {
      kind: 'fields',
      errors: { subject: 'errors:report_subject_not_assigned' },
    };
  }
  if (error.status === 404) {
    return { kind: 'missing' };
  }
  const errors: StartErrors = {};
  for (const fieldError of error.fieldErrors) {
    const field = pathField(fieldError.path);
    const message = `errors:${fieldError.code}`;
    if (field === 'subject_id') {
      errors.subject ??= message;
    } else if (field === 'period_start' || field === 'period_end') {
      const target: StartField =
        kind === 'weekly'
          ? 'week'
          : kind === 'monthly'
            ? 'month'
            : field === 'period_start'
              ? 'from'
              : 'to';
      errors[target] ??= message;
    }
  }
  return Object.keys(errors).length > 0
    ? { kind: 'fields', errors }
    : { kind: 'other' };
}

// --------------------------------------------------------------- narrative

export type NarrativeValues = Record<NarrativeField, string>;

/** The form's values from a report (null → ''). */
export function narrativeValues(
  report: Pick<ProgressReport, NarrativeField>,
): NarrativeValues {
  return {
    summary: report.summary ?? '',
    strengths: report.strengths ?? '',
    areas_for_improvement: report.areas_for_improvement ?? '',
    next_steps: report.next_steps ?? '',
  };
}

/** The editor's schema (C2): each field at most its limit, trimmed (the
 * API stores trimmed text). The summary is required only to submit. */
function narrativeText(max: number) {
  return z.string().refine((value) => value.trim().length <= max, {
    message: 'progressReports.editor.tooLong',
  });
}

export const narrativeSchema = z.object({
  summary: narrativeText(SUMMARY_MAX),
  strengths: narrativeText(SECTION_MAX),
  areas_for_improvement: narrativeText(SECTION_MAX),
  next_steps: narrativeText(SECTION_MAX),
});

/** Whether the typed values differ from the stored ones (trimmed). */
export function narrativeDiffers(
  values: NarrativeValues,
  baseline: NarrativeValues,
): boolean {
  return NARRATIVE_FIELDS.some(
    (field) => values[field].trim() !== baseline[field].trim(),
  );
}

/** `PATCH` (C2): the version and only the changed keys, trimmed (a cleared
 * field is sent as ""); null when nothing changed. */
export function patchBody(
  values: NarrativeValues,
  baseline: NarrativeValues,
  version: number,
): ReportUpdate | null {
  const body: ReportUpdate = { version };
  let changed = false;
  for (const field of NARRATIVE_FIELDS) {
    const value = values[field].trim();
    if (value !== baseline[field].trim()) {
      body[field] = value;
      changed = true;
    }
  }
  return changed ? body : null;
}

/** The summary is blank: submitting is refused before any request (C3). */
export function summaryMissing(values: Pick<NarrativeValues, 'summary'>) {
  return values.summary.trim() === '';
}

// ---------------------------------------------------------------- statuses

/** The `common` key of a status label (C1). */
export function statusLabelKey(status: ReportStatus) {
  const key = {
    draft: 'draft',
    submitted: 'submitted',
    changes_requested: 'changesRequested',
    approved_for_family: 'approved',
    retracted: 'retracted',
  } as const;
  return `progressReports.status.${key[status]}` as const;
}

export function isStatus(value: string | null): value is ReportStatus {
  return (
    value !== null && (REPORT_STATUSES as readonly string[]).includes(value)
  );
}

export function isKind(value: string | null): value is ReportKind {
  return value !== null && (REPORT_KINDS as readonly string[]).includes(value);
}

// --------------------------------------------------------------- view mode

export type ViewMode = 'edit' | 'review' | 'read';

type Flags = Pick<
  ProgressReport,
  'status' | 'can_edit' | 'can_approve' | 'can_request_changes' | 'can_retract'
>;

/** C: edit (can_edit), review (a decision flag, or a reviewer reading a
 * non-draft report), or read. */
export function viewMode(report: Flags, { reviewer }: { reviewer: boolean }) {
  if (report.can_edit) {
    return 'edit' as const;
  }
  if (
    report.can_approve ||
    report.can_request_changes ||
    report.can_retract ||
    (reviewer && report.status !== 'draft')
  ) {
    return 'review' as const;
  }
  return 'read' as const;
}

/** E3 (#83 E4): a reviewer's own submitted report, unless they hold the
 * Owner role: the actions show disabled with the reason. */
export function selfReviewBlocked(
  report: Pick<ProgressReport, 'is_own' | 'status'>,
  { reviewer, owner }: { reviewer: boolean; owner: boolean },
): boolean {
  return reviewer && !owner && report.is_own && report.status === 'submitted';
}

/** C6: the author who no longer teaches the student (a draft or a report
 * sent back, every flag false). */
export function formerAuthor(
  report: Pick<
    ProgressReport,
    'is_own' | 'status' | 'can_edit' | 'can_submit' | 'can_delete'
  >,
): boolean {
  return (
    report.is_own &&
    (report.status === 'draft' || report.status === 'changes_requested') &&
    !report.can_edit &&
    !report.can_submit &&
    !report.can_delete
  );
}

// ----------------------------------------------------------------- history

export type HistoryEntry =
  | { kind: 'submitted'; at: string }
  | { kind: 'changes'; at: string; by: string; reason: string }
  | { kind: 'approved'; at: string; by: string }
  | { kind: 'retracted'; at: string; by: string; reason: string };

/** C8: the entries R3 has, in time order (#83 keeps no full timeline). */
export function historyEntries(
  report: Pick<
    ProgressReport,
    'submitted_at' | 'changes_requested' | 'approved' | 'retracted'
  >,
): HistoryEntry[] {
  const entries: HistoryEntry[] = [];
  if (report.submitted_at) {
    entries.push({ kind: 'submitted', at: report.submitted_at });
  }
  if (report.changes_requested) {
    entries.push({
      kind: 'changes',
      at: report.changes_requested.at,
      by: report.changes_requested.by.display_name,
      reason: report.changes_requested.reason,
    });
  }
  if (report.approved) {
    entries.push({
      kind: 'approved',
      at: report.approved.at,
      by: report.approved.by.display_name,
    });
  }
  if (report.retracted) {
    entries.push({
      kind: 'retracted',
      at: report.retracted.at,
      by: report.retracted.by.display_name,
      reason: report.retracted.reason,
    });
  }
  return entries.sort(
    (a, b) => new Date(a.at).getTime() - new Date(b.at).getTime(),
  );
}

// ---------------------------------------------------------------- refusals

/** What a refused write on the report page shows (C9, E4). */
export type ReportRefusal =
  /** Field errors: on their fields (focus on the first). */
  | { kind: 'fields'; errors: Partial<Record<NarrativeField, ReportMessage>> }
  /** version_conflict: "changed elsewhere" + Reload. */
  | { kind: 'conflict' }
  /** under_review / closed / not_submitted: message + Reload. */
  | { kind: 'state' }
  /** author_only / read_only / permission_denied: message + Reload. */
  | { kind: 'forbidden' }
  /** progress_report_self_review: the E3 text. */
  | { kind: 'selfReview' }
  /** progress_report_author_not_current: the E4 text. */
  | { kind: 'authorNotCurrent' }
  /** The report is gone (404): the page shows the not-found page. */
  | { kind: 'missing' }
  | { kind: 'other' };

export const STATE_CODES = [
  'progress_report_under_review',
  'progress_report_closed',
  'progress_report_not_submitted',
];
export const FORBIDDEN_CODES = [
  'progress_report_author_only',
  'progress_report_read_only',
  'permission_denied',
];

/** Sorts a refused write on the report page. */
export function classifyRefusal(error: unknown): ReportRefusal {
  if (!(error instanceof ApiError)) {
    return { kind: 'other' };
  }
  if (error.code === 'version_conflict') {
    return { kind: 'conflict' };
  }
  if (error.code === 'summary_required') {
    return {
      kind: 'fields',
      errors: { summary: 'progressReports.editor.summaryRequired' },
    };
  }
  if (STATE_CODES.includes(error.code)) {
    return { kind: 'state' };
  }
  if (error.code === 'progress_report_self_review') {
    return { kind: 'selfReview' };
  }
  if (error.code === 'progress_report_author_not_current') {
    return { kind: 'authorNotCurrent' };
  }
  if (FORBIDDEN_CODES.includes(error.code)) {
    return { kind: 'forbidden' };
  }
  if (error.status === 404) {
    return { kind: 'missing' };
  }
  const errors: Partial<Record<NarrativeField, ReportMessage>> = {};
  for (const fieldError of error.fieldErrors) {
    const field = pathField(fieldError.path);
    if (field && (NARRATIVE_FIELDS as readonly string[]).includes(field)) {
      errors[field as NarrativeField] ??= `errors:${fieldError.code}`;
    }
  }
  return Object.keys(errors).length > 0
    ? { kind: 'fields', errors }
    : { kind: 'other' };
}

/** A refused decision whose error belongs on the Reason field (E4). */
export function reasonFieldError(error: unknown): ReportMessage | null {
  if (!(error instanceof ApiError)) {
    return null;
  }
  if (error.code === 'reason_required') {
    return 'errors:reason_required';
  }
  const onReason = error.fieldErrors.find(
    (fieldError) => pathField(fieldError.path) === 'reason',
  );
  return onReason ? `errors:${onReason.code}` : null;
}

/** The Reason's own check: trimmed 1-1000 (E2). */
export function reasonError(reason: string): ReportMessage | null {
  const length = reason.trim().length;
  if (length === 0) {
    return 'errors:reason_required';
  }
  if (length > REASON_MAX) {
    return 'progressReports.editor.tooLong';
  }
  return null;
}

// ------------------------------------------------------- the working list

export interface ListFilterState {
  /** The status sent; null = every status. */
  status: ReportStatus | null;
  /** The select's value ('all' when no status). */
  statusValue: ReportStatus | 'all';
  kind: ReportKind | null;
  mine: boolean;
  /** Any filter differs from the audience's default. */
  filtered: boolean;
}

/**
 * E1: the filters from the URL. A reviewer's default is the queue
 * (`status=submitted`); a tutor's is every status. `status=all` is every
 * status; draft is offered to tutors only; `mine` only to a reviewer who is
 * also a tutor. Invalid values are ignored.
 */
export function listFilters(
  params: URLSearchParams,
  { reviewer, tutor }: { reviewer: boolean; tutor: boolean },
): ListFilterState {
  const rawStatus = params.get('status');
  const defaultStatus: ReportStatus | null = reviewer ? 'submitted' : null;
  let status: ReportStatus | null = defaultStatus;
  if (rawStatus === 'all') {
    status = null;
  } else if (isStatus(rawStatus) && (rawStatus !== 'draft' || tutor)) {
    status = rawStatus;
  }
  const rawKind = params.get('kind');
  const kind = isKind(rawKind) ? rawKind : null;
  const mine = reviewer && tutor && params.get('mine') === 'true';
  return {
    status,
    statusValue: status ?? 'all',
    kind,
    mine,
    filtered: status !== defaultStatus || kind !== null || mine,
  };
}
