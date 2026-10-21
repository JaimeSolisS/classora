import type { FieldError } from '@classora/api-client';
import type { ParseKeys } from 'i18next';
import type { ErrorMessageKey } from '@/app/errors';
import { localDateTimeToInstant } from '@/lib/time';
import type {
  ClassSessionCreate,
  Option,
  SchedulingConflict,
  SubjectOption,
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

/** The POST body: the local date and times as instants in `timeZone`. */
export function scheduleBody(
  values: ScheduleValues,
  timeZone: string,
): ClassSessionCreate {
  return {
    kind: 'individual',
    student_id: values.student?.id ?? '',
    subject_id: values.subjectId,
    tutor_user_id: values.tutorId || null,
    classroom_id: values.classroomId || null,
    starts_at: localDateTimeToInstant(values.date, values.start, timeZone),
    ends_at: localDateTimeToInstant(values.date, values.end, timeZone),
  };
}

const SERVER_FIELDS: Record<string, ScheduleField> = {
  student_id: 'student',
  subject_id: 'subject',
  tutor_user_id: 'tutor',
  classroom_id: 'classroom',
  starts_at: 'start',
  ends_at: 'end',
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
