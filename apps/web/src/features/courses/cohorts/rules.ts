import type { FieldError } from '@classora/api-client';
import type { ParseKeys } from 'i18next';
import type { ErrorMessageKey } from '@/app/errors';
import type { SchedulingConflict } from '@/features/scheduling/create/api';
import { FIELD_ERROR_CODES } from '@/features/scheduling/create/rules';
import {
  SOFT_CODES,
  seriesOverridePairs,
} from '@/features/scheduling/series/overrides';
import {
  recurrenceServerErrors,
  validateRecurrence,
  type RecurrenceErrors,
  type RecurrenceValues,
} from '@/features/scheduling/series/recurrence';
import { respondByInstant } from '@/features/scheduling/session/rules';
import { addDays, localDateTimeToInstant } from '@/lib/time';
import type {
  CohortCreate,
  CohortDetail,
  CohortListFilters,
  CohortOverride,
  CohortSeries,
  CohortStatusFilter,
  CohortUpdate,
  TemplateOption,
} from './api';

// The rules of the cohort pages (#243), without React: the list's URL
// state, the tutor assignment shown and who may offer the series, the
// sessions window, the create form's client checks and request body
// (structured fields only: never RRULE text, the duration, `until` or
// `count`; the server derives the count, #65 E3), where the server's 422s
// go, which 409 conflicts the create may override, and the edit's PATCH
// (only changed fields).

/** A message: a `common` key, or an `errors` key prefixed with `errors:`. */
export type CohortMessage = ParseKeys<'common'> | `errors:${ErrorMessageKey}`;

// --- The list (B1) ---------------------------------------------------------------

/** The API's limit on `q`. */
export const SEARCH_MAX_LENGTH = 120;

export const STATUS_FILTERS: readonly CohortStatusFilter[] = [
  'current',
  'upcoming',
  'in_progress',
  'ended',
  'all',
];

export const DEFAULT_FILTERS: CohortListFilters = {
  q: '',
  template: '',
  subject: '',
  status: 'current',
};

/** The filters in the URL; anything missing or unknown is the default. */
export function listFilters(search: URLSearchParams): CohortListFilters {
  const status = search.get('status') ?? '';
  return {
    q: (search.get('q') ?? '').trim().slice(0, SEARCH_MAX_LENGTH),
    template: search.get('template') ?? '',
    subject: search.get('subject') ?? '',
    status: (STATUS_FILTERS as readonly string[]).includes(status)
      ? (status as CohortStatusFilter)
      : 'current',
  };
}

/** The URL query of `filters` (defaults left out; never a cursor). */
export function filterParams(filters: CohortListFilters): URLSearchParams {
  const params = new URLSearchParams();
  if (filters.q) {
    params.set('q', filters.q);
  }
  if (filters.template) {
    params.set('template', filters.template);
  }
  if (filters.subject) {
    params.set('subject', filters.subject);
  }
  if (filters.status !== 'current') {
    params.set('status', filters.status);
  }
  return params;
}

/** Whether any filter differs from the default ("Clear filters"). */
export function hasFilters(filters: CohortListFilters): boolean {
  return filterParams(filters).size > 0;
}

// --- The tutor assignment (B2, D4) ---------------------------------------------

export type AssignmentView =
  | { kind: 'accepted'; tutor: string }
  | { kind: 'pending'; tutor: string; respondBy: string | null }
  | { kind: 'none' };

/** The series' assignment as the pages show it. */
export function assignmentOf(series: CohortSeries | null): AssignmentView {
  const assignment = series?.assignment ?? null;
  if (!assignment) {
    return { kind: 'none' };
  }
  return assignment.status === 'pending'
    ? {
        kind: 'pending',
        tutor: assignment.tutor.display_name,
        respondBy: assignment.respond_by,
      }
    : { kind: 'accepted', tutor: assignment.tutor.display_name };
}

/**
 * "Offer to a tutor" (D4): a schedule.assign holder, while the cohort's
 * series has no tutor (neither pending nor accepted) and isn't over.
 */
export function canOfferSeries(
  series: CohortSeries | null,
  canAssign: boolean,
): boolean {
  return (
    canAssign &&
    series !== null &&
    series.assignment === null &&
    series.tutor === null &&
    !series.ended
  );
}

/**
 * The series offer's first local date: today, or the series' start when
 * it is later (the API's own default; never a date in the past).
 */
export function offerFromDate(
  series: Pick<CohortSeries, 'starts_on'>,
  today: string,
): string {
  return series.starts_on > today ? series.starts_on : today;
}

/** The room limits the cohort below its maximum (D1, ADR-0005 D7). */
export function roomLimited(
  cohort: Pick<CohortDetail, 'effective_capacity' | 'max_students'>,
): boolean {
  return cohort.effective_capacity < cohort.max_students;
}

// --- The sessions window (D3) --------------------------------------------------

/** The window's length; the API allows at most 45 days. */
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

// --- Edit (D5) ---------------------------------------------------------------------

export const NAME_MAX_LENGTH = 120;
export const MAX_STUDENTS = 500;

export interface EditValues {
  name: string;
  /** As typed. */
  max: string;
}

export type EditField = 'name' | 'max';
export type EditErrors = Partial<Record<EditField, CohortMessage>>;

export function editValuesOf(
  cohort: Pick<CohortDetail, 'name' | 'max_students'>,
): EditValues {
  return { name: cohort.name, max: String(cohort.max_students) };
}

const WHOLE = /^\d+$/;

function checkName(name: string): CohortMessage | undefined {
  const trimmed = name.trim();
  if (!trimmed) {
    return 'courses.cohorts.validation.nameRequired';
  }
  return trimmed.length > NAME_MAX_LENGTH
    ? 'courses.cohorts.validation.nameTooLong'
    : undefined;
}

function checkMax(max: string): CohortMessage | undefined {
  const text = max.trim();
  const n = Number(text);
  return WHOLE.test(text) && n >= 1 && n <= MAX_STUDENTS
    ? undefined
    : 'courses.cohorts.validation.maxRange';
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
  cohort: Pick<CohortDetail, 'name' | 'max_students' | 'version'>,
  values: EditValues,
): CohortUpdate {
  const body: CohortUpdate = { version: cohort.version };
  const name = values.name.trim();
  if (name !== cohort.name) {
    body.name = name;
  }
  const max = Number(values.max.trim());
  if (max !== cohort.max_students) {
    body.max_students = max;
  }
  return body;
}

/** Whether anything changed (Save is disabled otherwise: no `no_changes`). */
export function hasChanges(
  cohort: Pick<CohortDetail, 'name' | 'max_students' | 'version'>,
  values: EditValues,
): boolean {
  return Object.keys(updateBody(cohort, values)).length > 1;
}

/** The server's 422 and `409 cohort_name_taken` on the edit's fields. */
export function editServerErrors(error: {
  code?: string;
  fieldErrors: readonly FieldError[];
}): EditErrors {
  if (error.code === 'cohort_name_taken') {
    return { name: 'errors:cohort_name_taken' };
  }
  const errors: EditErrors = {};
  for (const { path, code } of error.fieldErrors) {
    const last = String(path[path.length - 1]);
    if (last === 'name' && !errors.name) {
      errors.name =
        code === 'name_too_long'
          ? 'courses.cohorts.validation.nameTooLong'
          : 'courses.cohorts.validation.nameRequired';
    } else if (last === 'max_students' && !errors.max) {
      errors.max = 'courses.cohorts.validation.maxRange';
    } else if (code === 'no_changes' && !errors.name) {
      errors.name = 'errors:no_changes';
    }
  }
  return errors;
}

// --- Create (C) --------------------------------------------------------------------

export interface CohortCreateValues {
  name: string;
  templateId: string;
  subjectId: string;
  /** As typed. */
  max: string;
  /** '' = "No classroom". */
  classroomId: string;
  /** '' = "No tutor yet". */
  tutorId: string;
  /** "Respond by" (with a tutor): YYYY-MM-DD in the center zone, or ''. */
  respondByDate: string;
  /** "Respond by" (with a tutor): HH:MM in the center zone, or ''. */
  respondByTime: string;
  /** YYYY-MM-DD, in the center zone. */
  startsOn: string;
  /** HH:MM, in the center zone. */
  start: string;
}

export type CreateField =
  | 'name'
  | 'template'
  | 'subject'
  | 'max'
  | 'classroom'
  | 'tutor'
  | 'respondBy'
  | 'startsOn'
  | 'start';

export type CreateErrors = Partial<Record<CreateField, CohortMessage>>;

/** The fields in form order (the first one with an error gets the focus). */
export const CREATE_FIELD_ORDER: readonly CreateField[] = [
  'name',
  'template',
  'subject',
  'max',
  'classroom',
  'tutor',
  'respondBy',
  'startsOn',
  'start',
];

export const EMPTY_COHORT: CohortCreateValues = {
  name: '',
  templateId: '',
  subjectId: '',
  max: '',
  classroomId: '',
  tutorId: '',
  respondByDate: '',
  respondByTime: '',
  startsOn: '',
  start: '',
};

const DATE = /^\d{4}-\d{2}-\d{2}$/;
const TIME = /^([01]\d|2[0-3]):[0-5]\d$/;

/** The subjects a template offers (C1): only its own. */
export function templateSubjects(
  template: TemplateOption | undefined,
): { id: string; name: string; levelName: string }[] {
  return (template?.subjects ?? [])
    .filter((subject) => subject.is_active)
    .map((subject) => ({
      id: subject.id,
      name: subject.name,
      levelName: subject.level_name,
    }));
}

/**
 * The values after choosing a template (C1): its only subject is
 * preselected; a subject it doesn't offer is cleared.
 */
export function withTemplate(
  values: CohortCreateValues,
  template: TemplateOption | undefined,
): CohortCreateValues {
  const subjects = templateSubjects(template);
  let subjectId = subjects.some((s) => s.id === values.subjectId)
    ? values.subjectId
    : '';
  if (subjects.length === 1) {
    subjectId = subjects[0]!.id;
  }
  return { ...values, templateId: template?.id ?? '', subjectId };
}

/**
 * The client checks (C2): every required field, the numbers in bounds, a
 * complete "Respond by" (only with a tutor), and the weekly rule (a
 * weekday, the interval). Every other rule is the server's.
 */
export function validateCohortCreate(
  values: CohortCreateValues,
  recurrence: RecurrenceValues,
): { errors: CreateErrors; recurrence: RecurrenceErrors } {
  const errors: CreateErrors = {};
  const name = checkName(values.name);
  if (name) {
    errors.name = name;
  }
  if (!values.templateId) {
    errors.template = 'courses.cohorts.validation.templateRequired';
  }
  if (!values.subjectId) {
    errors.subject = 'scheduling.validation.subjectRequired';
  }
  const max = checkMax(values.max);
  if (max) {
    errors.max = max;
  }
  if (values.tutorId) {
    const date = values.respondByDate.trim();
    const time = values.respondByTime.trim();
    if ((date || time) && !(DATE.test(date) && TIME.test(time))) {
      errors.respondBy = 'scheduling.offer.validation.deadlineIncomplete';
    }
  }
  if (!DATE.test(values.startsOn)) {
    errors.startsOn = 'scheduling.validation.dateRequired';
  }
  if (!TIME.test(values.start)) {
    errors.start = 'scheduling.validation.startRequired';
  }
  // The cohort has no "Ends" choice: only the weekdays and the interval.
  const rule = validateRecurrence(
    { ...recurrence, ends: 'never', until: '', count: '' },
    values.startsOn,
  );
  return { errors, recurrence: rule };
}

/**
 * The POST /cohorts body: the local start date and time in the center zone
 * and the weekdays and interval only. Never RRULE text, the duration (the
 * template's session length), `until` or `count` (the server derives it).
 */
export function cohortCreateBody(
  values: CohortCreateValues,
  recurrence: RecurrenceValues,
  timeZone: string,
  override?: CohortOverride,
): CohortCreate {
  const respondBy = values.tutorId
    ? respondByInstant(
        {
          deadlineDate: values.respondByDate,
          deadlineTime: values.respondByTime,
        },
        timeZone,
      )
    : null;
  return {
    ...(override ? { override } : {}),
    name: values.name.trim(),
    template_id: values.templateId,
    subject_id: values.subjectId,
    max_students: Number(values.max.trim()),
    tutor_user_id: values.tutorId || null,
    ...(respondBy ? { respond_by: respondBy } : {}),
    classroom_id: values.classroomId || null,
    starts_on: values.startsOn,
    start_time: values.start,
    recurrence: {
      weekdays: [...new Set(recurrence.weekdays)].sort((a, b) => a - b),
      interval: recurrence.interval,
    },
  };
}

/** The rule summary's fields (C2): the template's session count. */
export function cohortRule(
  recurrence: Pick<RecurrenceValues, 'weekdays' | 'interval'>,
  sessionCount: number,
) {
  return {
    weekdays: [...new Set(recurrence.weekdays)].sort((a, b) => a - b),
    interval: recurrence.interval,
    until: null,
    count: sessionCount,
  };
}

const SERVER_FIELDS: Record<string, CreateField> = {
  name: 'name',
  template_id: 'template',
  subject_id: 'subject',
  max_students: 'max',
  classroom_id: 'classroom',
  tutor_user_id: 'tutor',
  respond_by: 'respondBy',
  starts_on: 'startsOn',
  start_time: 'start',
};

/** The #65 H3 codes and the #233 deadline codes with their own message. */
const COHORT_FIELD_CODES: ReadonlySet<string> = new Set([
  'template_unknown',
  'template_inactive',
  'subject_not_in_template',
  'respond_by_requires_tutor',
  'respond_by_invalid',
]);

function createFieldMessage(field: CreateField, code: string): CohortMessage {
  if (field === 'name') {
    return code === 'name_too_long'
      ? 'courses.cohorts.validation.nameTooLong'
      : 'courses.cohorts.validation.nameRequired';
  }
  if (field === 'max') {
    return 'courses.cohorts.validation.maxRange';
  }
  return COHORT_FIELD_CODES.has(code) || FIELD_ERROR_CODES.has(code)
    ? (`errors:${code}` as `errors:${ErrorMessageKey}`)
    : 'errors:validation_error';
}

export interface CreateServerErrors {
  fields: CreateErrors;
  recurrence: RecurrenceErrors;
}

/**
 * Where the server's 422 (and `409 cohort_name_taken`) go (C4). An error
 * on `recurrence` itself (`too_many_occurrences`) goes on the weekdays:
 * the form has no "Ends" choice.
 */
export function createServerErrors(error: {
  code?: string;
  fieldErrors: readonly FieldError[];
}): CreateServerErrors {
  const result: CreateServerErrors = { fields: {}, recurrence: {} };
  if (error.code === 'cohort_name_taken') {
    result.fields.name = 'errors:cohort_name_taken';
    return result;
  }
  const rule = recurrenceServerErrors(
    error.fieldErrors.filter(
      ({ path }) => path[path.indexOf('recurrence') + 1] !== undefined,
    ),
  );
  for (const { path, code } of error.fieldErrors) {
    const at = path.indexOf('recurrence');
    if (at >= 0 && path[at + 1] === undefined && !rule.weekdays) {
      rule.weekdays =
        code === 'too_many_occurrences'
          ? 'errors:too_many_occurrences'
          : 'errors:validation_error';
    }
  }
  result.recurrence = rule;
  for (const { path, code } of error.fieldErrors) {
    if (path.includes('recurrence') || path.includes('override')) {
      continue;
    }
    const field = SERVER_FIELDS[String(path[path.length - 1])];
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
    Object.keys(found.recurrence).length > 0
  );
}

// --- Overrides (C4) ------------------------------------------------------------

/**
 * Whether `POST /api/v1/cohorts` can override this conflict: it takes the
 * #236 series shape (`pairs` of a soft code and its occurrence's start), so
 * only a soft conflict of one occurrence.
 */
export function createAccepts(conflict: SchedulingConflict): boolean {
  return (
    conflict.severity === 'soft' &&
    SOFT_CODES.has(conflict.code) &&
    typeof conflict.details.occurrence_start === 'string'
  );
}

/** The create's `override`: every soft pair, the trimmed reason. */
export function cohortCreateOverride(
  conflicts: readonly SchedulingConflict[],
  reason: string,
): CohortOverride {
  return { pairs: seriesOverridePairs(conflicts), reason: reason.trim() };
}
