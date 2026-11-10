import type { FieldError } from '@classora/api-client';
import type { ParseKeys } from 'i18next';
import { ApiError } from '@/app/api';
import type { ErrorMessageKey } from '@/app/errors';
import {
  addDays,
  localDateTimeToInstant,
  parseLocalDate,
  toLocalDate,
  toLocalTime,
} from '@/lib/time';
import { changedFields, withoutErrors } from '@/lib/fieldErrors';
import { minutesOfDay, timeOfMinutes, TIME_OF_DAY } from '@/lib/timeOfDay';
import {
  durationMinutes,
  recurrenceBody,
  validateRecurrence,
  type RecurrenceValues,
} from '../series/recurrence';
import type { ClassSessionDetail } from '../session/api';
import type {
  ClassSessionCreate,
  CourseSessionCreate,
  GroupSessionCreate,
  Option,
  SchedulingConflict,
  SeriesTutorSuggestionQuery,
  SlotSuggestionRequest,
  SubjectOption,
  SuggestedSlot,
  TutorSuggestionList,
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
  | 'student'
  | 'subject'
  /** The group or cohort of a group or course session (HF-11b). */
  | 'owner'
  | 'tutor'
  | 'classroom'
  | 'date'
  | 'start'
  | 'end';

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
  'owner',
  'tutor',
  'classroom',
  'date',
  'start',
  'end',
];

const TIME = TIME_OF_DAY;
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

/** Whether the start or end field holds text that isn't a time (HF-13). */
export interface UnreadableTimes {
  start: boolean;
  end: boolean;
}

export const READABLE_TIMES: UnreadableTimes = { start: false, end: false };

/**
 * A missing start or end whose field holds text that can't be read as a
 * time says how to write one instead (HF-13, #598 AC4).
 */
export function withTimeFormat(
  errors: ScheduleErrors,
  unreadable: UnreadableTimes,
): ScheduleErrors {
  const next = { ...errors };
  if (
    unreadable.start &&
    next.start === 'scheduling.validation.startRequired'
  ) {
    next.start = 'scheduling.validation.timeFormat';
  }
  if (unreadable.end && next.end === 'scheduling.validation.endRequired') {
    next.end = 'scheduling.validation.timeFormat';
  }
  return next;
}

/**
 * The server's field errors once `field` changed (HF-13, #598 E4): its own
 * error goes; start and end go together (their codes concern the pair).
 */
export function clearServerError(
  errors: ScheduleErrors,
  field: ScheduleField,
): ScheduleErrors {
  return withoutErrors(errors, changedFields(field));
}

/** The field each value is shown on (its errors go when it changes). */
export const VALUE_FIELDS: Record<keyof ScheduleValues, ScheduleField> = {
  student: 'student',
  subjectId: 'subject',
  tutorId: 'tutor',
  classroomId: 'classroom',
  date: 'date',
  start: 'start',
  end: 'end',
};

/** A new start's default end: one hour (HF-13, #598 E3). */
export const DEFAULT_DURATION_MINUTES = 60;

/**
 * The end once the start changes from `oldStart` to `newStart` (HF-13,
 * #598 E3): an end after the old start moves with it (same duration); an
 * empty end, or one not after the old start, becomes the new start + 1 h.
 * An end that would pass 23:59, or a start that isn't a time, leaves the
 * end as it was. With no old start, an end already after the new start is
 * kept (the user typed the end first).
 */
export function endAfterStartChange(
  oldStart: string,
  newStart: string,
  end: string,
): string {
  const to = minutesOfDay(newStart);
  if (to === null) {
    return end;
  }
  const from = minutesOfDay(oldStart);
  const until = minutesOfDay(end);
  let next: string | null;
  if (until !== null && from !== null && until > from) {
    next = timeOfMinutes(to + (until - from));
  } else if (until !== null && from === null && until > to) {
    next = end;
  } else {
    next = timeOfMinutes(to + DEFAULT_DURATION_MINUTES);
  }
  return next ?? end;
}

/**
 * The end while the start is being edited (#598 QA D1): E3 from the start
 * and end as they were before the edit began, never from an in-between
 * value. Back to the old start (Escape) or a start that isn't a time (a
 * cleared field, `93`, `25:00`): the end as it was.
 */
export function endForStart(
  before: { start: string; end: string },
  newStart: string,
): string {
  return newStart === before.start
    ? before.end
    : endAfterStartChange(before.start, newStart, before.end);
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

// --- Suggested tutors while scheduling (HF-05b B, C) --------------------------

/** How long the inputs must stay unchanged before the panel loads (B2). */
export const TUTOR_SUGGESTIONS_DELAY_MS = 400;

/** The most tutors the panel asks for (B2; the API's maximum). */
export const TUTOR_SUGGESTIONS_LIMIT = 50;

/** The rows shown before "View all" (B3). */
export const TUTOR_SUGGESTIONS_SHOWN = 5;

/**
 * What the panel loads for, as a stable key, or null when it can't load
 * (B2): a subject, a date, a start and an end after it and, in series mode,
 * a valid rule. The tutor is never part of it, so choosing one never
 * reloads; the room and the repeats switch are.
 */
export function tutorSuggestionKey(
  values: ScheduleValues,
  repeats: boolean,
  recurrence: RecurrenceValues,
): string | null {
  if (!canSuggestTutors(values)) {
    return null;
  }
  const slot = [values.subjectId, values.classroomId, values.date];
  if (!repeats) {
    return JSON.stringify(['once', ...slot, values.start, values.end]);
  }
  if (Object.keys(validateRecurrence(recurrence, values.date)).length > 0) {
    return null;
  }
  return JSON.stringify([
    'series',
    ...slot,
    values.start,
    values.end,
    recurrenceBody(recurrence),
  ]);
}

/**
 * The body of POST /tutor-suggestions/series (B2): the timing members of
 * the series create (`seriesCreateBody`), the subject and the room, without
 * the student, the tutor and an override.
 */
export function seriesTutorSuggestionBody(
  values: ScheduleValues,
  recurrence: RecurrenceValues,
): SeriesTutorSuggestionQuery {
  return {
    subject_id: values.subjectId,
    classroom_id: values.classroomId || null,
    starts_on: values.date,
    start_time: values.start,
    duration_minutes: durationMinutes(values.start, values.end) ?? 0,
    recurrence: recurrenceBody(recurrence),
    limit: TUTOR_SUGGESTIONS_LIMIT,
  };
}

/** A one-off or series suggestion list (the series adds its occurrences). */
export type AnyTutorSuggestionList = TutorSuggestionList & {
  occurrence_count?: number;
};

/** Why the list is empty, from the HF-05 counts (B5). */
export type SuggestionEmptyState =
  | { kind: 'unknown' }
  | { kind: 'noOccurrences' }
  | { kind: 'noneQualified' }
  | {
      kind: 'noneFree';
      qualified: number;
      /** 0 = left out of the text. */
      busy: number;
      /** 0 = left out of the text. */
      unavailable: number;
      /** The series' occurrences; null for a one-off slot. */
      occurrences: number | null;
    };

export function suggestionEmptyState(
  result: AnyTutorSuggestionList,
): SuggestionEmptyState {
  const occurrences = result.occurrence_count ?? null;
  if (occurrences === 0) {
    return { kind: 'noOccurrences' };
  }
  if (result.qualified_count === undefined) {
    return { kind: 'unknown' };
  }
  if (result.qualified_count === 0) {
    return { kind: 'noneQualified' };
  }
  return {
    kind: 'noneFree',
    qualified: result.qualified_count,
    busy: result.busy_count ?? 0,
    unavailable: result.unavailable_count ?? 0,
    occurrences,
  };
}

/**
 * Whether the chosen tutor is missing from a loaded list that holds every
 * eligible tutor (B6): false without a tutor, or when more tutors are
 * eligible than the list holds (it can't tell).
 */
export function isChosenTutorUnsuggested(
  result: TutorSuggestionList,
  tutorId: string,
): boolean {
  if (!tutorId || result.eligible_count > result.items.length) {
    return false;
  }
  return !result.items.some((item) => item.tutor.id === tutorId);
}

/** One weekly window as the API returns it. */
export interface WeeklyWindow {
  weekday: number;
  starts_at: string;
  ends_at: string;
}

/**
 * The tutor's weekly windows as text (C1): by weekday (Monday first), then
 * start, each "<day> HH:MM–HH:MM", comma-separated; null without windows.
 * The times are the center-zone times as stored.
 */
export function weeklyAvailabilityText(
  windows: readonly WeeklyWindow[],
  weekdayName: (isoWeekday: number) => string,
): string | null {
  if (windows.length === 0) {
    return null;
  }
  return [...windows]
    .sort(
      (a, b) => a.weekday - b.weekday || a.starts_at.localeCompare(b.starts_at),
    )
    .map(
      (window) =>
        `${weekdayName(window.weekday)} ${window.starts_at}–${window.ends_at}`,
    )
    .join(', ');
}

/** The days "Find free times" searches: the date and the 6 after it (C2). */
export const FREE_TIMES_DAYS = 7;

/**
 * Whether "Find free times" can ask (C2): one-off mode, a tutor, and every
 * field the create needs.
 */
export function canFindFreeTimes(values: ScheduleValues): boolean {
  return (
    values.tutorId !== '' && Object.keys(validateSchedule(values)).length === 0
  );
}

/** The C2 request: the form's values, the 7-day window, 5 times at most. */
export function freeTimesBody(
  values: ScheduleValues,
  timeZone: string,
): SlotSuggestionRequest {
  return {
    ...suggestionBody(values, timeZone),
    search_from: values.date,
    search_to: addDays(values.date, FREE_TIMES_DAYS - 1),
  };
}

// --- Group and course sessions (HF-11b, #593) ---------------------------------

/** The type selector's choice ("Class type"); not in the URL (E5 a). */
export type SessionKindChoice = 'individual' | 'group' | 'course';

/** A group session or a course (cohort) session. */
export type OwnerKind = Exclude<SessionKindChoice, 'individual'>;

/** One option of the group or cohort picker: its name and subject. */
export interface OwnerOption {
  id: string;
  name: string;
  subjectName: string;
}

/** The chosen group or cohort, as the summary shows it. */
export interface OwnerSummary {
  id: string;
  name: string;
  subject: SubjectOption;
  /** A cohort's first and last session dates (YYYY-MM-DD); null: unknown. */
  firstSessionOn: string | null;
  lastSessionOn: string | null;
  /** The students enrolled now, by name. */
  students: Option[];
}

/**
 * The client checks of a group or course session: the group or cohort,
 * and the same date and time rules as an individual one.
 */
export function validateOwnerSchedule(
  values: ScheduleValues,
  kind: OwnerKind,
  ownerId: string | null,
): ScheduleErrors {
  const { date, start, end } = validateSchedule(values);
  const errors: ScheduleErrors = {};
  if (!ownerId) {
    errors.owner =
      kind === 'group'
        ? 'scheduling.create.owner.groupRequired'
        : 'scheduling.create.owner.courseRequired';
  }
  if (date) errors.date = date;
  if (start) errors.start = start;
  if (end) errors.end = end;
  return errors;
}

/** The POST body of a group or course session: no student and no subject. */
export function ownerSessionBody(
  kind: OwnerKind,
  ownerId: string,
  values: ScheduleValues,
  timeZone: string,
  override?: SessionOverride,
): GroupSessionCreate | CourseSessionCreate {
  const common = {
    ...(override ? { override } : {}),
    tutor_user_id: values.tutorId || null,
    classroom_id: values.classroomId || null,
    starts_at: localDateTimeToInstant(values.date, values.start, timeZone),
    ends_at: localDateTimeToInstant(values.date, values.end, timeZone),
  };
  return kind === 'group'
    ? { kind: 'group', group_id: ownerId, ...common }
    : { kind: 'course', cohort_id: ownerId, ...common };
}

const OWNER_PATHS: ReadonlySet<string> = new Set(['group_id', 'cohort_id']);

/**
 * The 422 field errors of a group or course session (#592): the owner's
 * codes on the picker, a date outside the cohort's on the date, the rest
 * as for an individual session.
 */
export function ownerServerErrors(
  fieldErrors: readonly FieldError[],
  kind: OwnerKind,
): ScheduleErrors {
  const own = ({ path, code }: FieldError) =>
    OWNER_PATHS.has(String(path[path.length - 1])) ||
    code === 'session_outside_cohort_dates';
  const errors = serverErrors(fieldErrors.filter((error) => !own(error)));
  for (const { path, code } of fieldErrors) {
    if (code === 'session_outside_cohort_dates') {
      errors.date ??= 'errors:session_outside_cohort_dates';
    } else if (OWNER_PATHS.has(String(path[path.length - 1]))) {
      errors.owner ??= ownerCodeMessage(code, kind);
    }
  }
  return errors;
}

function ownerCodeMessage(code: string, kind: OwnerKind): ScheduleMessage {
  switch (code) {
    case 'group_unknown':
      return 'errors:group_unknown';
    case 'cohort_unknown':
      return 'errors:cohort_unknown';
    case 'student_inactive':
      return kind === 'group'
        ? 'scheduling.create.owner.studentInactiveGroup'
        : 'scheduling.create.owner.studentInactiveCourse';
    case 'subject_inactive':
      return kind === 'group'
        ? 'scheduling.create.owner.subjectInactiveGroup'
        : 'scheduling.create.owner.subjectInactiveCourse';
    default:
      return 'errors:validation_error';
  }
}

/**
 * A refusal of a group or course session shown as an alert (#592): the
 * group or cohort has ended (the selection is cleared), or its roster
 * changed while scheduling (the roster is read again).
 */
export type OwnerRefusal = 'ended' | 'rosterChanged';

export function ownerRefusal(error: unknown): OwnerRefusal | null {
  if (!(error instanceof ApiError)) {
    return null;
  }
  if (error.code === 'group_ended' || error.code === 'cohort_ended') {
    return 'ended';
  }
  return error.code === 'roster_changed' ? 'rosterChanged' : null;
}

/** Whether the chosen room holds fewer people than the roster (a warning). */
export function isRoomTooSmall(
  capacity: number | undefined,
  enrolled: number,
): boolean {
  return capacity !== undefined && enrolled > capacity;
}
