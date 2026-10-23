import type { FieldError } from '@classora/api-client';
import type { ParseKeys } from 'i18next';
import type { ErrorMessageKey } from '@/app/errors';
import { canAccess } from '@/features/auth/access';
import type { SessionUser } from '@/features/auth/session';
import type {
  Option,
  SchedulingConflict,
} from '@/features/scheduling/create/api';
import { FIELD_ERROR_CODES } from '@/features/scheduling/create/rules';
import {
  SOFT_CODES,
  seriesOverridePairs,
} from '@/features/scheduling/series/overrides';
import {
  durationMinutes,
  recurrenceBody,
  recurrenceServerErrors,
  validateRecurrence,
  type RecurrenceErrors,
  type RecurrenceValues,
} from '@/features/scheduling/series/recurrence';
import { addDays, localDateTimeToInstant } from '@/lib/time';
import type {
  GroupCreate,
  GroupDetail,
  GroupEnrollmentItem,
  GroupListFilters,
  GroupOverride,
  GroupStatusFilter,
  GroupUpdate,
  GroupWithdrawal,
} from './api';

// The rules of the tutoring group pages (#234), without React: the list's
// URL state, which view a user gets, which actions show, the roster split,
// the student's sessions window, the bodies of the writes (only changed
// fields for an edit; a blank withdrawal reason is no reason), the create
// form's client checks and request body (structured fields only, never
// RRULE text, ADR-0005 D4), where the server's 422s go, and which soft
// conflicts the create may override (E3: the merged #56 contract).

/** A message: a `common` key, or an `errors` key prefixed with `errors:`. */
export type GroupMessage = ParseKeys<'common'> | `errors:${ErrorMessageKey}`;

// --- The list (B1, B2) ---------------------------------------------------------

/** The API's limit on `q` (#56 D15). */
export const SEARCH_MAX_LENGTH = 120;

export const STATUS_FILTERS: readonly GroupStatusFilter[] = [
  'active',
  'ended',
  'all',
];

/** The filters in the URL; anything missing or unknown is the default. */
export function listFilters(search: URLSearchParams): GroupListFilters {
  const status = search.get('status') ?? '';
  return {
    q: (search.get('q') ?? '').trim().slice(0, SEARCH_MAX_LENGTH),
    subject: search.get('subject') ?? '',
    status: (STATUS_FILTERS as readonly string[]).includes(status)
      ? (status as GroupStatusFilter)
      : 'active',
  };
}

/** The URL query of `filters` (defaults left out; never a cursor). */
export function filterParams(filters: GroupListFilters): URLSearchParams {
  const params = new URLSearchParams();
  if (filters.q) {
    params.set('q', filters.q);
  }
  if (filters.subject) {
    params.set('subject', filters.subject);
  }
  if (filters.status !== 'active') {
    params.set('status', filters.status);
  }
  return params;
}

/** Whether any filter differs from the default ("Clear filters"). */
export function hasFilters(filters: GroupListFilters): boolean {
  return filterParams(filters).size > 0;
}

/** "Ended" in the list: no next session, and the filter can show ended ones. */
export function listItemEnded(
  item: { next_starts_at: string | null },
  status: GroupStatusFilter,
): boolean {
  return item.next_starts_at === null && status !== 'active';
}

// --- The view and the actions (A4, C7) -----------------------------------------

export type GroupView = 'staff' | 'student';

/**
 * The staff view for a schedule.view holder (a staff+student user too),
 * otherwise the student view. UX only: the API decides the data.
 */
export function groupView(user: SessionUser | null | undefined): GroupView {
  return canAccess(user, { permission: 'schedule.view' }) ? 'staff' : 'student';
}

/** A group has ended once no series still produces sessions (#56 G1). */
export function isGroupEnded(group: Pick<GroupDetail, 'current_series'>) {
  return group.current_series === null;
}

export interface GroupActions {
  enroll: boolean;
  withdraw: boolean;
  edit: boolean;
}

/** The actions a schedule.manage holder gets; "Enroll" not once ended. */
export function groupActions(
  group: Pick<GroupDetail, 'current_series'>,
  canManage: boolean,
): GroupActions {
  return {
    enroll: canManage && !isGroupEnded(group),
    withdraw: canManage,
    edit: canManage,
  };
}

/** The room limits the group below its maximum (C2, ADR-0005 D7). */
export function roomLimited(
  group: Pick<GroupDetail, 'effective_capacity' | 'max_students'>,
): boolean {
  return group.effective_capacity < group.max_students;
}

/** The active enrollments (as listed) and the withdrawn ones, newest first. */
export function rosterParts(roster: readonly GroupEnrollmentItem[]): {
  active: GroupEnrollmentItem[];
  withdrawn: GroupEnrollmentItem[];
} {
  return {
    active: roster.filter((item) => item.withdrawn_at === null),
    withdrawn: roster
      .filter((item) => item.withdrawn_at !== null)
      .sort((a, b) =>
        (b.withdrawn_at ?? '').localeCompare(a.withdrawn_at ?? ''),
      ),
  };
}

/** The ids of the students enrolled now (the enroll picker disables them). */
export function activeStudentIds(
  roster: readonly GroupEnrollmentItem[] | null,
): Set<string> {
  return new Set(
    (roster ?? [])
      .filter((item) => item.withdrawn_at === null)
      .map((item) => item.student.id),
  );
}

// --- The student's sessions (C4) -----------------------------------------------

/** The window's length; the API allows at most 45 days (#57 rule). */
export const WINDOW_DAYS = 28;

/**
 * The instants of the 4-week window that starts on the local date
 * `startDate` in the center zone: [its midnight, the midnight 28 days later).
 */
export function sessionsWindow(
  startDate: string,
  timeZone: string,
): { from: string; to: string } {
  return {
    from: localDateTimeToInstant(startDate, '00:00', timeZone),
    to: localDateTimeToInstant(
      addDays(startDate, WINDOW_DAYS),
      '00:00',
      timeZone,
    ),
  };
}

/** The start of the previous (-1) or next (+1) window. */
export function shiftWindow(startDate: string, direction: -1 | 1): string {
  return addDays(startDate, direction * WINDOW_DAYS);
}

// --- Withdraw (D3) ---------------------------------------------------------------

export const REASON_MAX_LENGTH = 1000;

/** The reason's length once trimmed. */
export function reasonLength(reason: string): number {
  return reason.trim().length;
}

/** The body: a blank reason is no reason. */
export function withdrawBody(version: number, reason: string): GroupWithdrawal {
  const trimmed = reason.trim();
  return trimmed ? { version, reason: trimmed } : { version };
}

// --- Edit (D4) ---------------------------------------------------------------------

export const NAME_MAX_LENGTH = 120;
export const MAX_STUDENTS = 500;

export interface EditValues {
  name: string;
  /** As typed. */
  max: string;
}

export type EditField = 'name' | 'max';
export type EditErrors = Partial<Record<EditField, GroupMessage>>;

export function editValuesOf(
  group: Pick<GroupDetail, 'name' | 'max_students'>,
): EditValues {
  return { name: group.name, max: String(group.max_students) };
}

const WHOLE = /^\d+$/;

function checkName(name: string): GroupMessage | undefined {
  const trimmed = name.trim();
  if (!trimmed) {
    return 'groups.validation.nameRequired';
  }
  return trimmed.length > NAME_MAX_LENGTH
    ? 'groups.validation.nameTooLong'
    : undefined;
}

function checkMax(max: string): GroupMessage | undefined {
  const text = max.trim();
  const n = Number(text);
  return WHOLE.test(text) && n >= 1 && n <= MAX_STUDENTS
    ? undefined
    : 'groups.validation.maxRange';
}

export function validateEdit(values: EditValues): EditErrors {
  const errors: EditErrors = {};
  const name = checkName(values.name);
  if (name) {
    errors.name = name;
  }
  const max = checkMax(values.max);
  if (max) {
    errors.max = max;
  }
  return errors;
}

/** The PATCH body: the version and only the fields that changed. */
export function updateBody(
  group: Pick<GroupDetail, 'name' | 'max_students' | 'version'>,
  values: EditValues,
): GroupUpdate {
  const body: GroupUpdate = { version: group.version };
  const name = values.name.trim();
  if (name !== group.name) {
    body.name = name;
  }
  const max = Number(values.max.trim());
  if (max !== group.max_students) {
    body.max_students = max;
  }
  return body;
}

/** Whether anything changed (the submit is disabled otherwise). */
export function hasChanges(
  group: Pick<GroupDetail, 'name' | 'max_students' | 'version'>,
  values: EditValues,
): boolean {
  return Object.keys(updateBody(group, values)).length > 1;
}

/** The server's 422/409 on the edit's fields. */
export function editServerErrors(error: {
  code?: string;
  fieldErrors: readonly FieldError[];
}): EditErrors {
  if (error.code === 'group_name_taken') {
    return { name: 'errors:group_name_taken' };
  }
  const errors: EditErrors = {};
  for (const { path, code } of error.fieldErrors) {
    const last = String(path[path.length - 1]);
    if (last === 'name' && !errors.name) {
      errors.name =
        code === 'name_too_long'
          ? 'groups.validation.nameTooLong'
          : 'groups.validation.nameRequired';
    } else if (last === 'max_students' && !errors.max) {
      errors.max =
        code === 'max_students_below_enrolled'
          ? 'groups.edit.belowEnrolled'
          : 'groups.validation.maxRange';
    } else if (code === 'no_changes' && !errors.name) {
      errors.name = 'errors:no_changes';
    }
  }
  return errors;
}

// --- Create (E1, E2) -----------------------------------------------------------

export interface GroupCreateValues {
  name: string;
  subjectId: string;
  /** As typed. */
  max: string;
  /** '' = "No tutor yet". */
  tutorId: string;
  /** '' = "No classroom". */
  classroomId: string;
  /** YYYY-MM-DD, in the center zone. */
  startsOn: string;
  /** HH:MM, in the center zone. */
  start: string;
  /** HH:MM, in the center zone. */
  end: string;
  students: Option[];
}

export type CreateField =
  | 'name'
  | 'subject'
  | 'max'
  | 'tutor'
  | 'classroom'
  | 'startsOn'
  | 'start'
  | 'end'
  | 'students';

export type CreateErrors = Partial<Record<CreateField, GroupMessage>>;

/** The fields in form order (the first one with an error gets the focus). */
export const CREATE_FIELD_ORDER: readonly CreateField[] = [
  'name',
  'subject',
  'max',
  'tutor',
  'classroom',
  'startsOn',
  'start',
  'end',
  'students',
];

export const EMPTY_GROUP: GroupCreateValues = {
  name: '',
  subjectId: '',
  max: '',
  tutorId: '',
  classroomId: '',
  startsOn: '',
  start: '',
  end: '',
  students: [],
};

const DATE = /^\d{4}-\d{2}-\d{2}$/;
const TIME = /^([01]\d|2[0-3]):[0-5]\d$/;

/**
 * The client checks (E2): every required field, the numbers in bounds, the
 * end after the start on the same date, and the weekly rule (#235: a
 * weekday, "Until" on or after "Starts on", …). Every other rule is the
 * server's.
 */
export function validateGroupCreate(
  values: GroupCreateValues,
  recurrence: RecurrenceValues,
): { errors: CreateErrors; recurrence: RecurrenceErrors } {
  const errors: CreateErrors = {};
  const name = checkName(values.name);
  if (name) {
    errors.name = name;
  }
  if (!values.subjectId) {
    errors.subject = 'scheduling.validation.subjectRequired';
  }
  const max = checkMax(values.max);
  if (max) {
    errors.max = max;
  }
  if (!DATE.test(values.startsOn)) {
    errors.startsOn = 'scheduling.validation.dateRequired';
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
  if (values.students.length > MAX_STUDENTS) {
    errors.students = 'groups.validation.studentsTooMany';
  }
  return {
    errors,
    recurrence: validateRecurrence(recurrence, values.startsOn),
  };
}

/**
 * The POST /groups body: the local start date and time in the center zone,
 * the duration from the start and end, the structured weekly rule and the
 * students' ids. Never RRULE text (ADR-0005 D4).
 */
export function groupCreateBody(
  values: GroupCreateValues,
  recurrence: RecurrenceValues,
  override?: GroupOverride,
): GroupCreate {
  return {
    ...(override ? { override } : {}),
    name: values.name.trim(),
    subject_id: values.subjectId,
    max_students: Number(values.max.trim()),
    tutor_user_id: values.tutorId || null,
    classroom_id: values.classroomId || null,
    starts_on: values.startsOn,
    start_time: values.start,
    duration_minutes: durationMinutes(values.start, values.end) ?? 0,
    recurrence: recurrenceBody(recurrence),
    student_ids: values.students.map((student) => student.id),
  };
}

const SERVER_FIELDS: Record<string, CreateField> = {
  name: 'name',
  subject_id: 'subject',
  max_students: 'max',
  tutor_user_id: 'tutor',
  classroom_id: 'classroom',
  starts_on: 'startsOn',
  start_time: 'start',
  duration_minutes: 'end',
  starts_at: 'start',
  ends_at: 'end',
  student_ids: 'students',
};

function createFieldMessage(field: CreateField, code: string): GroupMessage {
  if (field === 'name') {
    return code === 'name_too_long'
      ? 'groups.validation.nameTooLong'
      : 'groups.validation.nameRequired';
  }
  if (field === 'max') {
    return 'groups.validation.maxRange';
  }
  if (code === 'student_ids_duplicate') {
    return 'errors:student_ids_duplicate';
  }
  return FIELD_ERROR_CODES.has(code)
    ? (`errors:${code}` as `errors:${ErrorMessageKey}`)
    : 'errors:validation_error';
}

export interface CreateServerErrors {
  fields: CreateErrors;
  recurrence: RecurrenceErrors;
  /** By the student's index in `student_ids` (the chip's error). */
  students: Record<number, GroupMessage>;
}

/** Where the server's 422 (and 409 group_name_taken) go (E3). */
export function createServerErrors(error: {
  code?: string;
  fieldErrors: readonly FieldError[];
}): CreateServerErrors {
  const result: CreateServerErrors = {
    fields: {},
    recurrence: recurrenceServerErrors(error.fieldErrors),
    students: {},
  };
  if (error.code === 'group_name_taken') {
    result.fields.name = 'errors:group_name_taken';
    return result;
  }
  for (const { path, code } of error.fieldErrors) {
    if (path.includes('recurrence') || path.includes('override')) {
      continue;
    }
    const at = path.indexOf('student_ids');
    const index = at >= 0 ? path[at + 1] : undefined;
    if (typeof index === 'number') {
      result.students[index] ??= createFieldMessage('students', code);
      continue;
    }
    const field =
      at >= 0 ? 'students' : SERVER_FIELDS[String(path[path.length - 1])];
    if (field && !result.fields[field]) {
      result.fields[field] = createFieldMessage(field, code);
    }
  }
  return result;
}

/** Whether the server's errors put anything on the form. */
export function hasServerErrors(found: CreateServerErrors): boolean {
  return (
    Object.keys(found.fields).length > 0 ||
    Object.keys(found.recurrence).length > 0 ||
    Object.keys(found.students).length > 0
  );
}

// --- Overrides (E3, D2) --------------------------------------------------------

/** A conflict of the whole group, not of one occurrence. */
export function isGroupLevel(conflict: SchedulingConflict): boolean {
  return typeof conflict.details.occurrence_start !== 'string';
}

/**
 * The soft codes `POST /api/v1/groups` accepts in its `override` (the
 * merged #56 D2 contract): `codes` for the group-level
 * `group_capacity_exceeded`, `pairs` for every soft conflict of an
 * occurrence (the #236 series shape).
 */
export const GROUP_LEVEL_OVERRIDABLE: ReadonlySet<string> = new Set([
  'group_capacity_exceeded',
]);

/** Whether the group create can override this conflict (E3). */
export function createAccepts(conflict: SchedulingConflict): boolean {
  if (conflict.severity !== 'soft') {
    return false;
  }
  return isGroupLevel(conflict)
    ? GROUP_LEVEL_OVERRIDABLE.has(conflict.code)
    : SOFT_CODES.has(conflict.code);
}

/**
 * What the panel offers under a 409: the override button (a holder of
 * schedule.override, only soft conflicts, each one the write accepts),
 * "fix the hard ones first", "can't be overridden for groups yet" (a soft
 * code the write doesn't accept) or "only an Owner…" (anyone else).
 */
export type GroupOverrideAction =
  'override' | 'fixHardFirst' | 'notYet' | 'notAllowed';

export function groupOverrideAction(
  conflicts: readonly SchedulingConflict[],
  canOverride: boolean,
  accepts: (conflict: SchedulingConflict) => boolean,
): GroupOverrideAction {
  if (!canOverride) {
    return 'notAllowed';
  }
  if (conflicts.some((c) => c.severity === 'hard')) {
    return 'fixHardFirst';
  }
  return conflicts.every(accepts) ? 'override' : 'notYet';
}

/** The enroll accepts any soft code (the #49 shape, #56 E1). */
export function enrollAccepts(conflict: SchedulingConflict): boolean {
  return conflict.severity === 'soft' && SOFT_CODES.has(conflict.code);
}

/**
 * The create's `override`: `codes` for the group-level soft conflicts,
 * `pairs` for the occurrences' (instants exactly as sent), the trimmed
 * reason. An empty member is left out.
 */
export function groupCreateOverride(
  conflicts: readonly SchedulingConflict[],
  reason: string,
): GroupOverride {
  const codes = [
    ...new Set(
      conflicts
        .filter((c) => c.severity === 'soft' && isGroupLevel(c))
        .map((c) => c.code),
    ),
  ];
  const pairs = seriesOverridePairs(conflicts);
  return {
    ...(codes.length > 0 ? { codes } : {}),
    ...(pairs.length > 0 ? { pairs } : {}),
    reason: reason.trim(),
  };
}
