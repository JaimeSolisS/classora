import type { FieldError } from '@classora/api-client';
import type { ParseKeys } from 'i18next';
import { ApiError } from '@/app/api';
import type { ErrorMessageKey } from '@/app/errors';
import {
  localDateTimeToInstant,
  parseLocalDate,
  toLocalDate,
  toLocalTime,
} from '@/lib/time';
import type { ClassSessionDetail } from '../session/api';
import type {
  ClassSessionCreate,
  Option,
  SchedulingConflict,
  SlotSuggestionRequest,
  SubjectOption,
  SuggestedSlot,
  TutorSuggestionParams,
} from './api';

// The rules of "Schedule a session" (#48 F2, F3), without React: the form's
// values, the client checks (required fields, the end after the start on the
// same date), the request body (local date and times in the center zone →
// instants) and where the server's 422 field errors go. Every other rule is
// the server's.

export interface ScheduleValues {
  student: Option | null;
  subjectId: string;
  /** '' = "No tutor yet". */
  tutorId: string;
  /** '' = "No classroom". */
  classroomId: string;
  /** YYYY-MM-DD, in the center zone. */
  date: string;
  /** HH:MM, in the center zone. */
  start: string;
  /** HH:MM, in the center zone. */
  end: string;
}

export type ScheduleField =
  'student' | 'subject' | 'tutor' | 'classroom' | 'date' | 'start' | 'end';

/** A message: a `common` key, or an `errors` key prefixed with `errors:`. */
export type ScheduleMessage = ParseKeys<'common'> | `errors:${ErrorMessageKey}`;

export type ScheduleErrors = Partial<Record<ScheduleField, ScheduleMessage>>;

export const EMPTY_SCHEDULE: ScheduleValues = {
  student: null,
  subjectId: '',
  tutorId: '',
  classroomId: '',
  date: '',
  start: '',
  end: '',
};

/** The fields in form order (the first one with an error gets the focus). */
export const FIELD_ORDER: readonly ScheduleField[] = [
  'student',
  'subject',
  'tutor',
  'classroom',
  'date',
  'start',
  'end',
];

const TIME = /^([01]\d|2[0-3]):[0-5]\d$/;
const DATE = /^\d{4}-\d{2}-\d{2}$/;

/** The client checks: every required field, and the end after the start. */
export function validateSchedule(values: ScheduleValues): ScheduleErrors {
  const errors: ScheduleErrors = {};
  if (!values.student) {
    errors.student = 'scheduling.validation.studentRequired';
  }
  if (!values.subjectId) {
    errors.subject = 'scheduling.validation.subjectRequired';
  }
  if (!DATE.test(values.date)) {
    errors.date = 'scheduling.validation.dateRequired';
  }
  if (!TIME.test(values.start)) {
    errors.start = 'scheduling.validation.startRequired';
  }
  if (!TIME.test(values.end)) {
    errors.end = 'scheduling.validation.endRequired';
  } else if (TIME.test(values.start) && values.end <= values.start) {
    // HH:MM strings compare like the times they hold.
    errors.end = 'scheduling.validation.endAfterStart';
  }
  return errors;
}

/**
 * The POST body: the local date and times as instants in `timeZone`, with
 * the override when one is given (#49) and the cancelled session it makes up
 * in makeup mode (#436 B6).
 */
export function scheduleBody(
  values: ScheduleValues,
  timeZone: string,
  override?: SessionOverride,
  makeupOf?: string,
): ClassSessionCreate {
  return {
    ...(override ? { override } : {}),
    kind: 'individual',
    student_id: values.student?.id ?? '',
    subject_id: values.subjectId,
    tutor_user_id: values.tutorId || null,
    classroom_id: values.classroomId || null,
    starts_at: localDateTimeToInstant(values.date, values.start, timeZone),
    ends_at: localDateTimeToInstant(values.date, values.end, timeZone),
    ...(makeupOf ? { makeup_of_session_id: makeupOf } : {}),
  };
}

const SERVER_FIELDS: Record<string, ScheduleField> = {
  student_id: 'student',
  subject_id: 'subject',
  tutor_user_id: 'tutor',
  classroom_id: 'classroom',
  starts_at: 'start',
  ends_at: 'end',
  // A weekly series (#54b A3): the start date and time, and the duration.
  starts_on: 'date',
  start_time: 'start',
  duration_minutes: 'end',
};

/** The C2/C3 codes with their own `errors` key. */
export const FIELD_ERROR_CODES: ReadonlySet<string> = new Set([
  'student_unknown',
  'student_inactive',
  'subject_unknown',
  'subject_inactive',
  'tutor_unknown',
  'tutor_inactive',
  'classroom_unknown',
  'classroom_inactive',
  'naive_datetime',
  'not_whole_minutes',
  'ends_before_start',
  'duration_out_of_range',
  'crosses_local_date',
  'starts_on_in_past',
  'time_format',
]);

/** The server's 422 field errors on the form's fields (others are left out). */
export function serverErrors(
  fieldErrors: readonly FieldError[],
): ScheduleErrors {
  const errors: ScheduleErrors = {};
  for (const { path, code } of fieldErrors) {
    const field = SERVER_FIELDS[String(path[path.length - 1])];
    if (!field || errors[field]) {
      continue;
    }
    errors[field] = FIELD_ERROR_CODES.has(code)
      ? (`errors:${code}` as `errors:${ErrorMessageKey}`)
      : 'errors:validation_error';
  }
  return errors;
}

// --- Makeup mode (#436) -------------------------------------------------------

/** The URL parameter of makeup mode, the mode's only state (B9). */
export const MAKEUP_PARAM = 'makeupOf';

/** A makeup link: 44 px tall under 768 px. */
export const MAKEUP_LINK_CLASS =
  'inline-flex min-h-11 w-fit items-center text-sm underline underline-offset-4 outline-none focus-visible:ring-3 focus-visible:ring-ring md:min-h-0';

export function sessionPath(sessionId: string): string {
  return `/schedule/sessions/${encodeURIComponent(sessionId)}`;
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function isSessionId(value: string): boolean {
  return UUID.test(value);
}

/** The link's refusals, each with its own `errors` key (C1). */
const MAKEUP_ERROR_CODES: ReadonlySet<string> = new Set([
  'makeup_target_unknown',
  'makeup_target_not_cancelled',
  'makeup_target_not_individual',
  'makeup_not_allowed',
  'makeup_student_mismatch',
  'makeup_subject_mismatch',
  'makeup_window_passed',
]);

/** Why a session can't be made up from this page (B2). */
export type MakeupProblem =
  | 'unknown'
  | 'notCancelled'
  | 'notIndividual'
  | 'notAllowed'
  | 'alreadyScheduled'
  | 'windowPassed';

/**
 * Why `target` can't be made up, as of `readAt` (when it was read), or null.
 * Against the read's own time, so a deadline passing with the form open
 * leaves the form on screen and the server refuses the submit.
 */
export function makeupTargetProblem(
  target: ClassSessionDetail,
  readAt: number,
): MakeupProblem | null {
  if (target.state !== 'cancelled') {
    return 'notCancelled';
  }
  if (target.kind !== 'individual') {
    return 'notIndividual';
  }
  const { makeup } = target;
  if (!makeup?.allowed || !makeup.until) {
    return 'notAllowed';
  }
  if (makeup.session_id) {
    return 'alreadyScheduled';
  }
  if (Date.parse(makeup.until) < readAt) {
    return 'windowPassed';
  }
  return target.participants.length > 0 ? null : 'unknown';
}

/** The form's first values: the target's student and subject, nothing else. */
export function makeupValues(target: ClassSessionDetail): ScheduleValues {
  const student = target.participants[0];
  return {
    ...EMPTY_SCHEDULE,
    student: student
      ? { id: student.student_id, name: student.display_name }
      : null,
    subjectId: target.subject.id,
  };
}

// --- Pre-filled from the calendar (HF-02 C1) ----------------------------------

/** The URL parameters a calendar selection fills (`?date&start&end&tutor&room`). */
export const PREFILL_PARAMS = [
  'date',
  'start',
  'end',
  'tutor',
  'room',
] as const;

function isRealDate(value: string): boolean {
  try {
    parseLocalDate(value);
    return true;
  } catch {
    return false;
  }
}

/** The option's own id for a UUID in the URL, or '' (not a loaded option). */
function optionId(value: string | null, options: readonly Option[]): string {
  if (value === null || !UUID.test(value)) {
    return '';
  }
  const id = value.toLowerCase();
  return options.find((option) => option.id.toLowerCase() === id)?.id ?? '';
}

/**
 * The form's first values from the URL (C1): a real `date`, `HH:MM` `start`
 * and `end` (the end only after the start), and a `tutor` / `room` that is
 * one of the loaded active options. Anything else is ignored without an
 * error; the student and the subject stay empty. Null when nothing valid was
 * given (the plain form).
 */
export function prefillValues(
  params: URLSearchParams,
  options: { tutors: readonly Option[]; classrooms: readonly Option[] },
): ScheduleValues | null {
  const date = params.get('date') ?? '';
  const start = params.get('start') ?? '';
  const end = params.get('end') ?? '';
  const values: ScheduleValues = {
    ...EMPTY_SCHEDULE,
    date: isRealDate(date) ? date : '',
    start: TIME.test(start) ? start : '',
    tutorId: optionId(params.get('tutor'), options.tutors),
    classroomId: optionId(params.get('room'), options.classrooms),
  };
  // HH:MM strings compare like the times they hold.
  values.end = TIME.test(end) && values.start && end > values.start ? end : '';
  const filled =
    values.date ||
    values.start ||
    values.end ||
    values.tutorId ||
    values.classroomId;
  return filled ? values : null;
}

/**
 * "View in calendar" after a pre-filled save (C7, E8): the Week of the
 * session's date, with the tutor and room that were pre-filled.
 */
export function calendarWeekPath(
  date: string,
  filters: Pick<ScheduleValues, 'tutorId' | 'classroomId'>,
): string {
  const params = new URLSearchParams({ view: 'week', date });
  if (filters.tutorId) params.set('tutor', filters.tutorId);
  if (filters.classroomId) params.set('room', filters.classroomId);
  return `/schedule?${params.toString()}`;
}

/** Whether the target took a live makeup first (C2). */
export function isMakeupTaken(error: unknown): error is ApiError {
  return error instanceof ApiError && error.code === 'makeup_already_scheduled';
}

/** The live makeup of a `409 makeup_already_scheduled`, when it says (C2). */
export function takenMakeupId(error: unknown): string | null {
  if (!isMakeupTaken(error)) {
    return null;
  }
  const id: unknown = error.problem?.details?.class_session_id;
  return typeof id === 'string' ? id : null;
}

export interface SplitErrors {
  /** The errors shown on their fields. */
  fields: ScheduleErrors;
  /** The errors with no field to show them on: the page's alert. */
  page: ScheduleMessage[];
}

/**
 * The server's 422 errors by where they show: on their fields, or at page
 * level for the link (it isn't a field, C1) and, when `fixed`, the read-only
 * student and subject (a student archived since).
 */
export function splitServerErrors(
  fieldErrors: readonly FieldError[],
  fixed: boolean,
): SplitErrors {
  const fields = serverErrors(fieldErrors);
  const page: ScheduleMessage[] = [];
  if (fixed) {
    for (const field of ['student', 'subject'] as const) {
      const shown = fields[field];
      if (shown) {
        page.push(shown);
        delete fields[field];
      }
    }
  }
  for (const { path, code } of fieldErrors) {
    if (path[path.length - 1] === 'makeup_of_session_id') {
      page.push(
        MAKEUP_ERROR_CODES.has(code)
          ? (`errors:${code}` as `errors:${ErrorMessageKey}`)
          : 'errors:validation_error',
      );
    }
  }
  return { fields, page };
}

/** Subjects grouped by level, in the order given. */
export function subjectsByLevel(
  subjects: readonly SubjectOption[],
): [string, SubjectOption[]][] {
  const groups = new Map<string, SubjectOption[]>();
  for (const subject of subjects) {
    const group = groups.get(subject.levelName) ?? [];
    group.push(subject);
    groups.set(subject.levelName, group);
  }
  return [...groups.entries()];
}

/** The hard and soft codes the panel has a message for (#48 B3). */
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

/** Hard first, then the server's order (it is already sorted). */
export function orderedConflicts(
  conflicts: readonly SchedulingConflict[],
): SchedulingConflict[] {
  return [
    ...conflicts.filter((c) => c.severity === 'hard'),
    ...conflicts.filter((c) => c.severity !== 'hard'),
  ];
}

// --- Overrides (#49 F2, F3) ---------------------------------------------------

/** The override member of the create body. */
export type SessionOverride = NonNullable<ClassSessionCreate['override']>;

/** The most characters of an override reason, once trimmed. */
export const OVERRIDE_REASON_MAX_LENGTH = 1000;

/**
 * What the panel offers under the conflicts: the override button (a holder
 * of schedule.override, only soft conflicts), "fix the hard ones first" (a
 * holder, at least one hard conflict) or "only an Owner…" (anyone else).
 */
export type OverrideAction = 'override' | 'fixHardFirst' | 'notAllowed';

export function overrideAction(
  conflicts: readonly SchedulingConflict[],
  canOverride: boolean,
): OverrideAction {
  if (!canOverride) {
    return 'notAllowed';
  }
  return conflicts.some((c) => c.severity === 'hard')
    ? 'fixHardFirst'
    : 'override';
}

/** The soft codes to override, once each, in the server's order. */
export function overrideCodes(
  conflicts: readonly SchedulingConflict[],
): string[] {
  return [
    ...new Set(
      conflicts.filter((c) => c.severity === 'soft').map((c) => c.code),
    ),
  ];
}

export type ReasonMessage =
  'scheduling.override.reasonRequired' | 'scheduling.override.reasonTooLong';

/** The reason rule: 1-1000 characters once trimmed. */
export function validateOverrideReason(
  reason: string,
): ReasonMessage | undefined {
  const trimmed = reason.trim();
  if (!trimmed) {
    return 'scheduling.override.reasonRequired';
  }
  return trimmed.length > OVERRIDE_REASON_MAX_LENGTH
    ? 'scheduling.override.reasonTooLong'
    : undefined;
}

/** The override of every soft conflict shown, with the trimmed reason. */
export function sessionOverride(
  conflicts: readonly SchedulingConflict[],
  reason: string,
): SessionOverride {
  return { codes: overrideCodes(conflicts), reason: reason.trim() };
}

/** The server's 422 on the override's reason, or null for any other error. */
export function overrideReasonError(
  fieldErrors: readonly FieldError[],
): ReasonMessage | 'errors:validation_error' | null {
  const reason = fieldErrors.find(
    ({ path }) => path[1] === 'override' && path[2] === 'reason',
  );
  if (reason) {
    if (reason.code === 'reason_required') {
      return 'scheduling.override.reasonRequired';
    }
    return reason.code === 'reason_too_long'
      ? 'scheduling.override.reasonTooLong'
      : 'errors:validation_error';
  }
  return fieldErrors.some(({ path }) => path[1] === 'override')
    ? 'errors:validation_error'
    : null;
}

// --- Suggested times (#50 C1) -------------------------------------------------

/** The server's default number of suggested times (#50 E2). */
export const SUGGESTIONS_LIMIT = 5;

/**
 * The suggestions request: the form's values as the create sends them,
 * without an override and without a window (the server's default: the
 * requested date and the 6 days after it), with the default limit. The
 * generated type requires `limit` because it has a default.
 */
export function suggestionBody(
  values: ScheduleValues,
  timeZone: string,
): SlotSuggestionRequest {
  return { ...scheduleBody(values, timeZone), limit: SUGGESTIONS_LIMIT };
}

/** The form's date, start and end for a suggested slot, in `timeZone`. */
export function slotValues(
  slot: Pick<SuggestedSlot, 'starts_at' | 'ends_at'>,
  timeZone: string,
): Pick<ScheduleValues, 'date' | 'start' | 'end'> {
  return {
    date: toLocalDate(slot.starts_at, timeZone),
    start: toLocalTime(slot.starts_at, timeZone),
    end: toLocalTime(slot.ends_at, timeZone),
  };
}

// --- Suggested tutors (#51 G) -------------------------------------------------

/**
 * Whether "Suggest tutors" can ask: a subject, a date, a start and an end
 * after it (the same client checks as the submit, without the student).
 */
export function canSuggestTutors(values: ScheduleValues): boolean {
  const { subject, date, start, end } = validateSchedule(values);
  return !subject && !date && !start && !end;
}

/**
 * The query for the slot: the form's local date and times as instants in
 * `timeZone`, and the classroom when one is chosen.
 */
export function tutorSuggestionParams(
  values: ScheduleValues,
  timeZone: string,
): TutorSuggestionParams {
  const params: TutorSuggestionParams = {
    subject_id: values.subjectId,
    starts_at: localDateTimeToInstant(values.date, values.start, timeZone),
    ends_at: localDateTimeToInstant(values.date, values.end, timeZone),
  };
  if (values.classroomId) {
    params.classroom_id = values.classroomId;
  }
  return params;
}

/** The fields whose change makes shown tutor suggestions stale (G2). */
export const SLOT_FIELDS: ReadonlySet<keyof ScheduleValues> = new Set([
  'subjectId',
  'classroomId',
  'date',
  'start',
  'end',
]);

/** Whole hours and the minutes left, for "X h Y min". */
export function hoursAndMinutes(total: number): {
  hours: number;
  minutes: number;
} {
  return { hours: Math.floor(total / 60), minutes: total % 60 };
}
