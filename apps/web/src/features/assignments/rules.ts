import { ApiError } from '@/app/api';
import {
  compareHundredths,
  formatHundredths,
  hundredthsOf,
  maxScoreError,
  parseScore,
  scoreError,
  type ScoreRangeError,
} from '@/lib/decimal';
import { z } from '@/lib/forms';
import { addDays } from '@/lib/time';
import {
  SUBMISSION_STATES,
  type AssignmentCreate,
  type AssignmentPatch,
  type AssignmentView,
  type GradePut,
  type RosterEntry,
  type SubmissionPut,
  type SubmissionState,
} from './api';

// The homework pages' pure rules (#313 A5, B, C, E): the limits (one set
// mirroring #82 A3, unit-tested against it), the link rule (C6, the same as
// the API's), the deadline text parts (E5, from local dates under an
// injectable today), the form's values and Zod schema, the POST and PATCH
// bodies (changed keys only; AM1: an explicit null clears the time or the
// max score), the server's field errors placed back on the form by `path`,
// and the refusals of each write sorted into what the page shows.

// ------------------------------------------------------------------ limits

export const TITLE_MAX = 200;
export const INSTRUCTIONS_MAX = 4000;
export const TEXT_MAX = 10000;
export const URL_MAX = 2000;
export const FEEDBACK_MAX = 4000;
export const REASON_MAX = 500;
export const RECIPIENTS_MIN = 1;
export const RECIPIENTS_MAX = 100;
export const DUE_MAX_DAYS = 365;
/** The max score's bounds (the API's 0.5 to 1000, as decimal strings). */
export const MAX_SCORE_MIN = '0.5';
export const MAX_SCORE_MAX = '1000';
/** The relative deadline text reaches this many days ahead (E5). */
export const RELATIVE_DAYS = 7;

// ------------------------------------------------------------------ links

/** Whitespace, control and format characters, and the backslash. */
const BAD_URL_CHARACTER = /[\s\p{Cc}\p{Cf}\p{Zl}\p{Zp}\\]/u;
const HTTP_URL = /^(https?):\/\/([^/?#]*)/i;
const AUTHORITY = /^(\[[0-9A-Fa-f:.]+\]|[^:[\]]+)(?::(\d*))?$/;

/**
 * A link the pages may render as an anchor (C6, the #82 A3 rule): absolute
 * http or https (any case), a host, no user-info, no whitespace or control
 * characters, a valid port, at most 2000 characters. Anything else renders
 * as plain text.
 */
export function isSafeHttpUrl(value: string): boolean {
  if (!value || value.length > URL_MAX || BAD_URL_CHARACTER.test(value)) {
    return false;
  }
  const match = HTTP_URL.exec(value);
  if (!match) {
    return false;
  }
  const authority = match[2]!;
  if (authority.includes('@')) {
    return false;
  }
  const host = AUTHORITY.exec(authority);
  if (!host) {
    return false;
  }
  const port = host[2];
  return !port || (port.length <= 5 && Number(port) <= 65535);
}

/** A `?state=` value, or null for all states. */
export function parseState(value: string | null): SubmissionState | null {
  return SUBMISSION_STATES.find((state) => state === value) ?? null;
}

// --------------------------------------------------------------- deadline

/** The calendar day number of a YYYY-MM-DD date (no zone involved). */
function dayNumber(localDate: string): number {
  const [year, month, day] = localDate.split('-').map(Number) as [
    number,
    number,
    number,
  ];
  return Math.round(Date.UTC(year, month - 1, day) / 86_400_000);
}

/** "16:00:00" or "16:00" → "16:00"; null stays null. */
export function wallTime(dueTime: string | null | undefined): string | null {
  return dueTime ? dueTime.slice(0, 5) : null;
}

export type RelativeDue =
  { kind: 'today' } | { kind: 'tomorrow' } | { kind: 'days'; days: number };

export interface DeadlineParts {
  /** The deadline's local date. */
  dueOn: string;
  /** Its local time (HH:MM), or null for the end of the day. */
  dueTime: string | null;
  /** A pending item due within 7 days: today, tomorrow or in n days. */
  relative: RelativeDue | null;
  /** Nothing handed in and the deadline passed. */
  missing: boolean;
  /** Handed in after the deadline. */
  late: boolean;
}

/**
 * The parts of an item's deadline text (E5), from its local due date and
 * the center's `today` (the caller passes `todayInZone(zone, now)`).
 */
export function deadlineParts(
  item: {
    due_on: string;
    due_time: string | null;
    state: SubmissionState;
    is_late: boolean;
  },
  today: string,
): DeadlineParts {
  const days = dayNumber(item.due_on) - dayNumber(today);
  let relative: RelativeDue | null = null;
  if (item.state === 'pending' && days >= 0 && days <= RELATIVE_DAYS) {
    relative =
      days === 0
        ? { kind: 'today' }
        : days === 1
          ? { kind: 'tomorrow' }
          : { kind: 'days', days };
  }
  return {
    dueOn: item.due_on,
    dueTime: wallTime(item.due_time),
    relative,
    missing: item.state === 'missing',
    late:
      item.is_late && (item.state === 'submitted' || item.state === 'graded'),
  };
}

// ------------------------------------------------------------------- form

export interface AssignmentFormValues {
  /** '' until chosen (create only; it never changes). */
  subjectId: string;
  title: string;
  instructions: string;
  /** A local date, or '' until chosen. */
  dueOn: string;
  /** HH:MM, or '' for the end of the day. */
  dueTime: string;
  /** As typed; '' for feedback only. */
  maxScore: string;
  studentIds: string[];
}

export function newFormValues(
  subjectId = '',
  studentIds: string[] = [],
): AssignmentFormValues {
  return {
    subjectId,
    title: '',
    instructions: '',
    dueOn: '',
    dueTime: '',
    maxScore: '',
    studentIds,
  };
}

/** A stored assignment as form values (its recipients from the roster). */
export function formValuesOf(assignment: AssignmentView): AssignmentFormValues {
  return {
    subjectId: assignment.subject.id,
    title: assignment.title,
    instructions: assignment.instructions,
    dueOn: assignment.due_on,
    dueTime: wallTime(assignment.due_time) ?? '',
    maxScore: assignment.max_score ?? '',
    studentIds: assignment.roster.map((row) => row.student.id),
  };
}

/** A recipient who handed in work can't be removed (B3). */
export function lockedRecipients(
  roster: readonly Pick<RosterEntry, 'student' | 'state'>[],
): Set<string> {
  return new Set(
    roster
      .filter((row) => row.state === 'submitted' || row.state === 'graded')
      .map((row) => row.student.id),
  );
}

/**
 * A field message: a `common` key (`assignments.validation.*`) or
 * `errors:<code>` for a server code shown with its `errors` message.
 */
export type FormMessage = string;

const V = 'assignments.validation';
const DATE = /^\d{4}-\d{2}-\d{2}$/;
const TIME = /^\d{2}:\d{2}$/;

export const MAX_SCORE_MESSAGES: Record<ScoreRangeError, FormMessage> = {
  invalid: `${V}.maxScoreInvalid`,
  comma: `${V}.decimalComma`,
  precision: `${V}.maxScorePrecision`,
  range: `${V}.maxScoreRange`,
};

export interface FormContext {
  creating: boolean;
  /** The center's today. */
  today: string;
  /** The center's wall-clock time now (HH:MM). */
  nowTime: string;
  /** The stored values (edit); the deadline is checked only when changed. */
  original: AssignmentFormValues | null;
  /** Grades were given: the max score can't change (B4). */
  maxScoreLocked: boolean;
}

/** The latest due date the API takes (today + 365). */
export function latestDueOn(today: string): string {
  return addDays(today, DUE_MAX_DAYS);
}

/** Whether the deadline differs from the stored one (always on a create). */
export function deadlineChanged(
  values: Pick<AssignmentFormValues, 'dueOn' | 'dueTime'>,
  original: AssignmentFormValues | null,
): boolean {
  return (
    original === null ||
    values.dueOn !== original.dueOn ||
    values.dueTime !== original.dueTime
  );
}

/** The form's schema (B2-B4). */
export function assignmentFormSchema(context: FormContext) {
  return z
    .object({
      subjectId: z.string(),
      title: z.string(),
      instructions: z.string(),
      dueOn: z.string(),
      dueTime: z.string(),
      maxScore: z.string(),
      studentIds: z.array(z.string()),
    })
    .superRefine((values, ctx) => {
      const issue = (path: keyof AssignmentFormValues, message: string) =>
        ctx.addIssue({ code: 'custom', path: [path], message });
      if (context.creating && values.subjectId === '') {
        issue('subjectId', `${V}.subjectRequired`);
      }
      const title = values.title.trim().length;
      if (title === 0) {
        issue('title', `${V}.titleRequired`);
      } else if (title > TITLE_MAX) {
        issue('title', `${V}.titleTooLong`);
      }
      const instructions = values.instructions.trim().length;
      if (instructions === 0) {
        issue('instructions', `${V}.instructionsRequired`);
      } else if (instructions > INSTRUCTIONS_MAX) {
        issue('instructions', `${V}.instructionsTooLong`);
      }
      if (values.dueTime !== '' && !TIME.test(values.dueTime)) {
        issue('dueTime', `${V}.timeInvalid`);
      }
      if (values.dueOn === '') {
        issue('dueOn', `${V}.dueRequired`);
      } else if (!DATE.test(values.dueOn)) {
        issue('dueOn', `${V}.dateInvalid`);
      } else if (deadlineChanged(values, context.original)) {
        if (values.dueOn < context.today) {
          issue('dueOn', `${V}.dueInPast`);
        } else if (values.dueOn > latestDueOn(context.today)) {
          issue('dueOn', `${V}.dueTooFar`);
        } else if (
          values.dueOn === context.today &&
          TIME.test(values.dueTime) &&
          values.dueTime <= context.nowTime
        ) {
          issue('dueTime', `${V}.dueTimePassed`);
        }
      }
      if (!context.maxScoreLocked && values.maxScore.trim() !== '') {
        const error = maxScoreError(values.maxScore);
        if (error) {
          issue('maxScore', MAX_SCORE_MESSAGES[error]);
        }
      }
      if (values.studentIds.length < RECIPIENTS_MIN) {
        issue('studentIds', `${V}.recipientsRequired`);
      } else if (values.studentIds.length > RECIPIENTS_MAX) {
        issue('studentIds', `${V}.recipientsLimit`);
      }
    });
}

/** A typed max score as the API's decimal string ("10" → "10.00"). */
export function normalizedScore(text: string): string {
  const parsed = parseScore(text);
  return parsed.ok ? formatHundredths(parsed.hundredths) : text.trim();
}

/** The create body (B5): `student_ids` always explicit. */
export function createBody(
  values: AssignmentFormValues,
  sessionId: string | null,
): AssignmentCreate {
  return {
    subject_id: values.subjectId,
    title: values.title.trim(),
    instructions: values.instructions.trim(),
    due_on: values.dueOn,
    ...(values.dueTime ? { due_time: values.dueTime } : {}),
    ...(values.maxScore.trim()
      ? { max_score: normalizedScore(values.maxScore) }
      : {}),
    ...(sessionId ? { class_session_id: sessionId } : {}),
    student_ids: [...values.studentIds],
  };
}

function sameScore(a: string, b: string): boolean {
  const left = a.trim();
  const right = b.trim();
  if (left === '' || right === '') {
    return left === right;
  }
  const x = hundredthsOf(left);
  const y = hundredthsOf(right);
  return x !== null && y !== null
    ? compareHundredths(x, y) === 0
    : left === right;
}

/**
 * The edit body (B4): the version plus only the changed keys. Clearing the
 * time or the max score sends an explicit null (AM1); added and removed
 * students go in `add_student_ids` / `remove_student_ids`.
 */
export function patchBody(
  values: AssignmentFormValues,
  original: AssignmentFormValues,
  version: number,
): AssignmentPatch {
  const body: AssignmentPatch = { version };
  const title = values.title.trim();
  if (title !== original.title.trim()) {
    body.title = title;
  }
  const instructions = values.instructions.trim();
  if (instructions !== original.instructions.trim()) {
    body.instructions = instructions;
  }
  if (values.dueOn !== original.dueOn) {
    body.due_on = values.dueOn;
  }
  if (values.dueTime !== original.dueTime) {
    body.due_time = values.dueTime === '' ? null : values.dueTime;
  }
  if (!sameScore(values.maxScore, original.maxScore)) {
    body.max_score =
      values.maxScore.trim() === '' ? null : normalizedScore(values.maxScore);
  }
  const before = new Set(original.studentIds);
  const after = new Set(values.studentIds);
  const added = values.studentIds.filter((id) => !before.has(id));
  const removed = original.studentIds.filter((id) => !after.has(id));
  if (added.length > 0) {
    body.add_student_ids = added;
  }
  if (removed.length > 0) {
    body.remove_student_ids = removed;
  }
  return body;
}

// ------------------------------------------------------ server field errors

export type FormField = keyof AssignmentFormValues;

export interface PlacedErrors {
  /** Field errors by form field, in the order sent. */
  fields: { field: FormField; message: FormMessage }[];
  /** Per-student messages on the recipient checkboxes. */
  students: Map<string, FormMessage>;
}

/** The student ids a problem names (`details.student_ids`). */
export function detailStudentIds(error: ApiError): string[] {
  const ids = error.problem?.details?.student_ids;
  return Array.isArray(ids) ? ids : typeof ids === 'string' ? [ids] : [];
}

const TOP_FIELDS: Record<string, FormField> = {
  subject_id: 'subjectId',
  title: 'title',
  instructions: 'instructions',
  due_on: 'dueOn',
  due_time: 'dueTime',
  max_score: 'maxScore',
  student_ids: 'studentIds',
  add_student_ids: 'studentIds',
  remove_student_ids: 'studentIds',
};

/** Our message for a server code on a field (else its `errors` text). */
export function serverFormMessage(code: string, field: FormField): FormMessage {
  switch (code) {
    case 'title_required':
      return `${V}.titleRequired`;
    case 'instructions_required':
      return `${V}.instructionsRequired`;
    case 'too_long':
      if (field === 'title') return `${V}.titleTooLong`;
      if (field === 'instructions') return `${V}.instructionsTooLong`;
      return 'errors:too_long';
    case 'due_in_past':
      return field === 'dueTime' ? `${V}.dueTimePassed` : `${V}.dueInPast`;
    case 'due_too_far':
      return `${V}.dueTooFar`;
    case 'max_score_out_of_range':
      return `${V}.maxScoreRange`;
    case 'score_precision':
      return field === 'maxScore'
        ? `${V}.maxScorePrecision`
        : 'errors:score_precision';
    case 'recipient_invalid':
      return `${V}.recipientInvalidSummary`;
    case 'recipients_required':
      return `${V}.recipientsRequired`;
    case 'recipients_too_many':
      return `${V}.recipientsLimit`;
    case 'subject_unknown':
      return `${V}.subjectUnknown`;
    default:
      return `errors:${code}`;
  }
}

/**
 * The form fields of a refused save (B6), from each field error's `path`
 * (`["body","title"]`): `recipient_invalid` also marks each named student;
 * `due_in_past` for today with a time lands on Due time.
 */
export function placeServerErrors(
  error: unknown,
  values: Pick<AssignmentFormValues, 'dueOn' | 'dueTime'>,
  today: string,
): PlacedErrors {
  const placed: PlacedErrors = { fields: [], students: new Map() };
  if (!(error instanceof ApiError)) {
    return placed;
  }
  for (const fieldError of error.fieldErrors) {
    const [where, key] = fieldError.path;
    if (where !== 'body' || typeof key !== 'string') {
      continue;
    }
    let field = TOP_FIELDS[key];
    if (!field) {
      continue;
    }
    if (
      field === 'dueOn' &&
      fieldError.code === 'due_in_past' &&
      values.dueOn === today &&
      values.dueTime !== ''
    ) {
      field = 'dueTime';
    }
    placed.fields.push({
      field,
      message: serverFormMessage(fieldError.code, field),
    });
    if (fieldError.code === 'recipient_invalid') {
      for (const id of detailStudentIds(error)) {
        placed.students.set(id, `${V}.recipientInvalid`);
      }
    }
  }
  return placed;
}

// ---------------------------------------------------------------- refusals

/** What a refused save of the form shows (B6). */
export type FormRefusal =
  /** "This homework changed elsewhere." + Reload. */
  | 'conflict'
  /** "Homework can't be linked to this class." + the link without it. */
  | 'session'
  /** assignment_has_grades: on Max score + Reload. */
  | 'grades'
  /** recipient_has_submission: on the student's checkbox + Reload. */
  | 'recipientSubmission'
  /** assignment_tutor_only / creator_only / read_only: message + Reload. */
  | 'forbidden'
  /** The assignment is gone (404): the not-found page. */
  | 'missing'
  /** Field errors to place. */
  | 'fields'
  /** Anything else: the error's own message. */
  | 'other';

export const FORBIDDEN_CODES = [
  'assignment_tutor_only',
  'assignment_creator_only',
  'assignment_read_only',
];
const SESSION_CODES = [
  'class_session_invalid',
  'subject_mismatch',
  'session_not_assignable',
];

export function classifyFormRefusal(error: unknown): FormRefusal {
  if (!(error instanceof ApiError)) {
    return 'other';
  }
  if (error.code === 'version_conflict') {
    return 'conflict';
  }
  if (
    SESSION_CODES.includes(error.code) ||
    error.fieldErrors.some((item) => SESSION_CODES.includes(item.code))
  ) {
    return 'session';
  }
  if (error.code === 'assignment_has_grades') {
    return 'grades';
  }
  if (error.code === 'recipient_has_submission') {
    return 'recipientSubmission';
  }
  if (FORBIDDEN_CODES.includes(error.code)) {
    return 'forbidden';
  }
  if (error.status === 404) {
    return 'missing';
  }
  if (error.fieldErrors.length > 0) {
    return 'fields';
  }
  return 'other';
}

/** What a refused roster write (grade, record, delete) shows (C2, C5). */
export type RosterRefusal =
  /** version_conflict / submission_required / has_submissions + Reload. */
  | 'reload'
  /** assignment_read_only / creator_only: the message + Reload. */
  | 'forbidden'
  /** The assignment or the row is gone: the not-found page. */
  | 'missing'
  /** Field errors on the dialog's fields. */
  | 'fields'
  | 'other';

const ROSTER_RELOAD_CODES = [
  'version_conflict',
  'version_required',
  'submission_required',
  'submission_graded',
  'assignment_has_submissions',
];

export function classifyRosterRefusal(error: unknown): RosterRefusal {
  if (!(error instanceof ApiError)) {
    return 'other';
  }
  if (ROSTER_RELOAD_CODES.includes(error.code)) {
    return 'reload';
  }
  if (FORBIDDEN_CODES.includes(error.code)) {
    return 'forbidden';
  }
  if (error.status === 404) {
    return 'missing';
  }
  if (error.fieldErrors.length > 0) {
    return 'fields';
  }
  return 'other';
}

/** What a refused submission shows (E6). */
export type SubmitRefusal =
  'locked' | 'graded' | 'conflict' | 'consent' | 'fields' | 'missing' | 'other';

export function classifySubmitRefusal(error: unknown): SubmitRefusal {
  if (!(error instanceof ApiError)) {
    return 'other';
  }
  if (error.code === 'submission_locked') {
    return 'locked';
  }
  if (error.code === 'submission_graded') {
    return 'graded';
  }
  if (error.code === 'version_conflict') {
    return 'conflict';
  }
  if (error.code === 'student_consent_required') {
    return 'consent';
  }
  if (
    error.fieldErrors.some((item) => item.code === 'version_required') ||
    error.code === 'version_required'
  ) {
    return 'conflict';
  }
  if (error.status === 404) {
    return 'missing';
  }
  if (error.fieldErrors.length > 0) {
    return 'fields';
  }
  return 'other';
}

// --------------------------------------------------------- dialogs, forms

const trimmedOrNull = (value: string): string | null => {
  const text = value.trim();
  return text === '' ? null : text;
};

export interface GradeInput {
  score: string;
  feedback: string;
  reason: string;
}

export type GradeField = keyof GradeInput;

/** The grade dialog's checks (C5): message keys by field. */
export function gradeErrors(
  input: GradeInput,
  { maxScore, regrade }: { maxScore: string | null; regrade: boolean },
): Partial<Record<GradeField, FormMessage>> {
  const errors: Partial<Record<GradeField, FormMessage>> = {};
  if (maxScore !== null) {
    if (input.score.trim() === '') {
      errors.score = `${V}.scoreRequired`;
    } else {
      const error = scoreError(input.score, maxScore);
      if (error) {
        errors.score =
          error === 'range'
            ? `${V}.scoreRange`
            : error === 'precision'
              ? `${V}.scorePrecision`
              : error === 'comma'
                ? `${V}.decimalComma`
                : `${V}.scoreInvalid`;
      }
    }
  } else if (input.feedback.trim() === '') {
    errors.feedback = `${V}.feedbackRequired`;
  }
  if (!errors.feedback && input.feedback.trim().length > FEEDBACK_MAX) {
    errors.feedback = `${V}.feedbackTooLong`;
  }
  if (regrade) {
    const reason = input.reason.trim().length;
    if (reason === 0) {
      errors.reason = `${V}.reasonRequired`;
    } else if (reason > REASON_MAX) {
      errors.reason = `${V}.reasonTooLong`;
    }
  }
  return errors;
}

/** The grade body (C5): `change_reason` on a re-grade only. */
export function gradeBody(
  input: GradeInput,
  {
    version,
    maxScore,
    regrade,
  }: { version: number; maxScore: string | null; regrade: boolean },
): GradePut {
  return {
    version,
    ...(maxScore !== null ? { score: normalizedScore(input.score) } : {}),
    feedback: trimmedOrNull(input.feedback),
    ...(regrade ? { change_reason: input.reason.trim() } : {}),
  };
}

/** A server field error of a grade, placed on the dialog's field. */
export function gradeServerErrors(
  error: unknown,
): Partial<Record<GradeField, FormMessage>> {
  const errors: Partial<Record<GradeField, FormMessage>> = {};
  if (!(error instanceof ApiError)) {
    return errors;
  }
  const fields: Record<string, GradeField> = {
    score: 'score',
    feedback: 'feedback',
    change_reason: 'reason',
  };
  for (const item of error.fieldErrors) {
    const key = item.path[1];
    const field = typeof key === 'string' ? fields[key] : undefined;
    if (field && !errors[field]) {
      errors[field] = `errors:${item.code}`;
    }
  }
  return errors;
}

export interface WorkInput {
  text: string;
  url: string;
}

export type WorkField = keyof WorkInput | 'root';

/**
 * The checks of handed-in work (E6, C4): the text at most 10000, the link
 * by the C6 rule; `requireOne` (the student's submission) needs one of them.
 */
export function workErrors(
  input: WorkInput,
  { requireOne }: { requireOne: boolean },
): Partial<Record<WorkField, FormMessage>> {
  const errors: Partial<Record<WorkField, FormMessage>> = {};
  const text = input.text.trim();
  const url = input.url.trim();
  if (text.length > TEXT_MAX) {
    errors.text = `${V}.textTooLong`;
  }
  if (url !== '' && !isSafeHttpUrl(url)) {
    errors.url = `${V}.urlInvalid`;
  }
  if (requireOne && text === '' && url === '') {
    errors.root = `${V}.workRequired`;
  }
  return errors;
}

/** The submission body: `version` once something was handed in. */
export function workBody(
  input: WorkInput,
  version: number | null,
): SubmissionPut {
  return {
    ...(version !== null ? { version } : {}),
    text: trimmedOrNull(input.text),
    url: trimmedOrNull(input.url),
  };
}

/** A server field error of a submission, placed on its field. */
export function workServerErrors(
  error: unknown,
): Partial<Record<WorkField, FormMessage>> {
  const errors: Partial<Record<WorkField, FormMessage>> = {};
  if (!(error instanceof ApiError)) {
    return errors;
  }
  for (const item of error.fieldErrors) {
    const key = item.path[1];
    const field: WorkField = key === 'text' || key === 'url' ? key : 'root';
    if (!errors[field]) {
      errors[field] =
        item.code === 'submission_empty'
          ? `${V}.workRequired`
          : item.code === 'url_invalid'
            ? `${V}.urlInvalid`
            : item.code === 'too_long' && field === 'text'
              ? `${V}.textTooLong`
              : `errors:${item.code}`;
    }
  }
  return errors;
}
