import type { ParseKeys } from 'i18next';
import type { CourseTemplate, CourseTemplateWrite } from './api';

// Pure helpers of the course template editor (#64 G3, G6): the values the
// form edits, the checks (the API's rules, courses/templates.py: 1 to 10
// subjects, 1 to 50 modules with unique titles, a module's subject among the
// template's, planned sessions within the total, 0 to 30 assessments with
// unique titles) and the POST / PUT body: list order is position, kept rows
// send their `id`, new rows none, and an assessment names its module by
// its index in the body (`module_index`). UX only: the API validates every
// request.

export const NAME_MAX = 100;
export const TITLE_MAX = 200;
export const TEXT_MAX = 2000;
export const SUBJECTS_MAX = 10;
export const MODULES_MAX = 50;
export const ASSESSMENTS_MAX = 30;
export const SESSION_COUNT_MAX = 200;
export const SESSION_MINUTES_MIN = 15;
export const SESSION_MINUTES_MAX = 480;

export interface ModuleRow {
  /** A stable key of the row (assessments point at it). */
  key: string;
  /** The stored module's id; none for a new row. */
  id?: string;
  title: string;
  description: string;
  /** One of the template's subjects, or '' for none. */
  subjectId: string;
  /** Digits as typed, or '' for none. */
  plannedSessions: string;
  isRequired: boolean;
  materials: string;
}

export interface AssessmentRow {
  key: string;
  id?: string;
  title: string;
  /** The key of a module row, or '' for the whole course. */
  moduleKey: string;
  isRequired: boolean;
  /** Digits as typed, or '' for no minimum. */
  minScore: string;
}

export interface TemplateFormValues {
  name: string;
  description: string;
  subjectIds: string[];
  sessionCount: string;
  sessionMinutes: string;
  minAttendance: string;
  modules: ModuleRow[];
  assessments: AssessmentRow[];
}

export type MessageKey = ParseKeys<'common'>;

export type TemplateField =
  | 'name'
  | 'description'
  | 'subjects'
  | 'sessionCount'
  | 'sessionMinutes'
  | 'minAttendance'
  | 'modules'
  | 'assessments';
export type ModuleField =
  'title' | 'description' | 'subjectId' | 'plannedSessions' | 'materials';
export type AssessmentField = 'title' | 'moduleKey' | 'minScore';

export interface TemplateErrors {
  fields: Partial<Record<TemplateField, MessageKey>>;
  modules: Record<number, Partial<Record<ModuleField, MessageKey>>>;
  assessments: Record<number, Partial<Record<AssessmentField, MessageKey>>>;
}

export const NO_ERRORS: TemplateErrors = {
  fields: {},
  modules: {},
  assessments: {},
};

let lastKey = 0;
/** A new row key, unique on the page. */
export function newRowKey(): string {
  lastKey += 1;
  return `row-${lastKey}`;
}

export function newModuleRow(): ModuleRow {
  return {
    key: newRowKey(),
    title: '',
    description: '',
    subjectId: '',
    plannedSessions: '',
    isRequired: true,
    materials: '',
  };
}

export function newAssessmentRow(): AssessmentRow {
  return {
    key: newRowKey(),
    title: '',
    moduleKey: '',
    isRequired: true,
    minScore: '',
  };
}

export function emptyValues(): TemplateFormValues {
  return {
    name: '',
    description: '',
    subjectIds: [],
    sessionCount: '',
    sessionMinutes: '',
    minAttendance: '',
    modules: [newModuleRow()],
    assessments: [],
  };
}

const text = (value: number | null) => (value === null ? '' : String(value));

/** The form values of a stored template. */
export function templateValues(template: CourseTemplate): TemplateFormValues {
  const modules: ModuleRow[] = template.modules.map((module) => ({
    key: newRowKey(),
    id: module.id,
    title: module.title,
    description: module.description ?? '',
    subjectId: module.subject_id ?? '',
    plannedSessions: text(module.planned_sessions),
    isRequired: module.is_required,
    materials: module.materials ?? '',
  }));
  const keyOf = new Map(modules.map((row) => [row.id, row.key] as const));
  return {
    name: template.name,
    description: template.description ?? '',
    subjectIds: template.subjects.map((subject) => subject.id),
    sessionCount: String(template.session_count),
    sessionMinutes: String(template.session_minutes),
    minAttendance: text(template.min_attendance_percent),
    modules,
    assessments: template.assessments.map((item) => ({
      key: newRowKey(),
      id: item.id,
      title: item.title,
      moduleKey:
        item.module_id === null ? '' : (keyOf.get(item.module_id) ?? ''),
      isRequired: item.is_required,
      minScore: text(item.min_score_percent),
    })),
  };
}

/** `rows` with the row at `index` moved by `offset` (-1 up, +1 down). */
export function moveRow<T>(rows: readonly T[], index: number, offset: number) {
  const target = index + offset;
  if (target < 0 || target >= rows.length) {
    return [...rows];
  }
  const next = [...rows];
  const [row] = next.splice(index, 1);
  next.splice(target, 0, row as T);
  return next;
}

/** The values without the module at `index`; assessments that pointed at
 * it now cover the whole course. */
export function removeModule(
  values: TemplateFormValues,
  index: number,
): TemplateFormValues {
  const removed = values.modules[index];
  return {
    ...values,
    modules: values.modules.filter((_, at) => at !== index),
    assessments: values.assessments.map((item) =>
      removed && item.moduleKey === removed.key
        ? { ...item, moduleKey: '' }
        : item,
    ),
  };
}

/** A whole number from `low` to `high` typed as digits, or null. */
function wholeNumber(value: string, low: number, high: number): number | null {
  const trimmed = value.trim();
  if (!/^\d+$/.test(trimmed)) {
    return null;
  }
  const number = Number(trimmed);
  return number >= low && number <= high ? number : null;
}

const optionalNumber = (value: string) =>
  value.trim() === '' ? null : Number(value.trim());

const optionalText = (value: string) => value.trim() || null;

function duplicateIndexes(titles: string[]): number[] {
  const seen = new Set<string>();
  const found: number[] = [];
  titles.forEach((title, index) => {
    const key = title.trim().toLocaleLowerCase();
    if (key && seen.has(key)) {
      found.push(index);
    }
    seen.add(key);
  });
  return found;
}

const V = 'courses.templates.validation';

/** The checks of the whole document; empty `fields`, `modules` and
 * `assessments` when valid. */
export function validateTemplate(values: TemplateFormValues): TemplateErrors {
  const fields: TemplateErrors['fields'] = {};
  const modules: TemplateErrors['modules'] = {};
  const assessments: TemplateErrors['assessments'] = {};
  const moduleError = (index: number, field: ModuleField, key: MessageKey) => {
    modules[index] = { ...modules[index], [field]: key };
  };
  const assessmentError = (
    index: number,
    field: AssessmentField,
    key: MessageKey,
  ) => {
    assessments[index] = { ...assessments[index], [field]: key };
  };

  const name = values.name.trim();
  if (!name) {
    fields.name = `${V}.nameRequired`;
  } else if (name.length > NAME_MAX) {
    fields.name = `${V}.nameTooLong`;
  }
  if (values.description.trim().length > TEXT_MAX) {
    fields.description = `${V}.textTooLong`;
  }
  if (values.subjectIds.length === 0) {
    fields.subjects = `${V}.subjectsRequired`;
  } else if (values.subjectIds.length > SUBJECTS_MAX) {
    fields.subjects = `${V}.tooManySubjects`;
  }
  const sessionCount = wholeNumber(values.sessionCount, 1, SESSION_COUNT_MAX);
  if (sessionCount === null) {
    fields.sessionCount = `${V}.sessionCountRange`;
  }
  if (
    wholeNumber(
      values.sessionMinutes,
      SESSION_MINUTES_MIN,
      SESSION_MINUTES_MAX,
    ) === null
  ) {
    fields.sessionMinutes = `${V}.sessionMinutesRange`;
  }
  if (
    values.minAttendance.trim() !== '' &&
    wholeNumber(values.minAttendance, 1, 100) === null
  ) {
    fields.minAttendance = `${V}.percentRange`;
  }

  if (values.modules.length === 0) {
    fields.modules = `${V}.modulesRequired`;
  }
  const subjects = new Set(values.subjectIds);
  let planned = 0;
  let lastPlanned = -1;
  values.modules.forEach((row, index) => {
    const title = row.title.trim();
    if (!title) {
      moduleError(index, 'title', `${V}.titleRequired`);
    } else if (title.length > TITLE_MAX) {
      moduleError(index, 'title', `${V}.titleTooLong`);
    }
    if (row.description.trim().length > TEXT_MAX) {
      moduleError(index, 'description', `${V}.textTooLong`);
    }
    if (row.materials.trim().length > TEXT_MAX) {
      moduleError(index, 'materials', `${V}.textTooLong`);
    }
    if (row.subjectId && !subjects.has(row.subjectId)) {
      moduleError(index, 'subjectId', `${V}.moduleSubject`);
    }
    if (row.plannedSessions.trim() !== '') {
      const value = wholeNumber(row.plannedSessions, 1, SESSION_COUNT_MAX);
      if (value === null) {
        moduleError(index, 'plannedSessions', `${V}.plannedRange`);
      } else {
        planned += value;
        lastPlanned = index;
      }
    }
  });
  for (const index of duplicateIndexes(values.modules.map((r) => r.title))) {
    moduleError(index, 'title', `${V}.duplicateTitle`);
  }
  if (
    sessionCount !== null &&
    planned > sessionCount &&
    lastPlanned >= 0 &&
    !modules[lastPlanned]?.plannedSessions
  ) {
    moduleError(lastPlanned, 'plannedSessions', `${V}.plannedExceedTotal`);
  }

  const moduleKeys = new Set(values.modules.map((row) => row.key));
  values.assessments.forEach((row, index) => {
    const title = row.title.trim();
    if (!title) {
      assessmentError(index, 'title', `${V}.titleRequired`);
    } else if (title.length > TITLE_MAX) {
      assessmentError(index, 'title', `${V}.titleTooLong`);
    }
    if (row.moduleKey && !moduleKeys.has(row.moduleKey)) {
      assessmentError(index, 'moduleKey', `${V}.moduleIndexInvalid`);
    }
    if (
      row.minScore.trim() !== '' &&
      wholeNumber(row.minScore, 1, 100) === null
    ) {
      assessmentError(index, 'minScore', `${V}.percentRange`);
    }
  });
  for (const index of duplicateIndexes(
    values.assessments.map((r) => r.title),
  )) {
    assessmentError(index, 'title', `${V}.duplicateTitle`);
  }
  if (values.assessments.length > ASSESSMENTS_MAX) {
    fields.assessments = `${V}.tooManyAssessments`;
  }
  return { fields, modules, assessments };
}

export function hasErrors(errors: TemplateErrors): boolean {
  return (
    Object.keys(errors.fields).length > 0 ||
    Object.keys(errors.modules).length > 0 ||
    Object.keys(errors.assessments).length > 0
  );
}

/** The POST body of valid values (no ids). */
export function templateBody(values: TemplateFormValues): CourseTemplateWrite {
  const moduleIndex = new Map(
    values.modules.map((row, index) => [row.key, index] as const),
  );
  return {
    name: values.name.trim(),
    description: optionalText(values.description),
    subject_ids: values.subjectIds,
    session_count: Number(values.sessionCount.trim()),
    session_minutes: Number(values.sessionMinutes.trim()),
    min_attendance_percent: optionalNumber(values.minAttendance),
    modules: values.modules.map((row) => ({
      ...(row.id ? { id: row.id } : {}),
      title: row.title.trim(),
      description: optionalText(row.description),
      subject_id: row.subjectId || null,
      planned_sessions: optionalNumber(row.plannedSessions),
      is_required: row.isRequired,
      materials: optionalText(row.materials),
    })),
    assessments: values.assessments.map((row) => ({
      ...(row.id ? { id: row.id } : {}),
      title: row.title.trim(),
      module_index: row.moduleKey
        ? (moduleIndex.get(row.moduleKey) ?? null)
        : null,
      is_required: row.isRequired,
      min_score_percent: optionalNumber(row.minScore),
    })),
  };
}

const SERVER_FIELDS: Record<string, TemplateField> = {
  name: 'name',
  description: 'description',
  subject_ids: 'subjects',
  session_count: 'sessionCount',
  session_minutes: 'sessionMinutes',
  min_attendance_percent: 'minAttendance',
  modules: 'modules',
  assessments: 'assessments',
};
const SERVER_MODULE_FIELDS: Record<string, ModuleField> = {
  id: 'title',
  title: 'title',
  description: 'description',
  subject_id: 'subjectId',
  planned_sessions: 'plannedSessions',
  materials: 'materials',
};
const SERVER_ASSESSMENT_FIELDS: Record<string, AssessmentField> = {
  id: 'title',
  title: 'title',
  module_index: 'moduleKey',
  min_score_percent: 'minScore',
};
const SERVER_MESSAGES: Record<string, MessageKey> = {
  duplicate_subject: `${V}.duplicateSubject`,
  duplicate_title: `${V}.duplicateTitle`,
  module_subject_not_in_template: `${V}.moduleSubject`,
  planned_sessions_exceed_total: `${V}.plannedExceedTotal`,
  module_index_invalid: `${V}.moduleIndexInvalid`,
  subject_unknown: `${V}.subjectUnknown`,
  subject_inactive: `${V}.subjectInactive`,
  item_unknown: `${V}.itemUnknown`,
  too_short: `${V}.invalid`,
};

/** The form errors of a server 422's field errors (`NO_ERRORS` when none
 * is about a form field). `planned_sessions_exceed_total` goes to the last
 * module with planned sessions. */
export function serverErrors(
  fieldErrors: readonly { path: readonly (string | number)[]; code: string }[],
  values: TemplateFormValues,
): TemplateErrors {
  const errors: TemplateErrors = { fields: {}, modules: {}, assessments: {} };
  for (const { path, code } of fieldErrors) {
    if (path[0] !== 'body') {
      continue;
    }
    const [, name, index, field] = path;
    const message = SERVER_MESSAGES[code] ?? `${V}.invalid`;
    if (name === 'modules' && typeof index === 'number') {
      const target = SERVER_MODULE_FIELDS[String(field)] ?? 'title';
      errors.modules[index] = { ...errors.modules[index], [target]: message };
    } else if (name === 'assessments' && typeof index === 'number') {
      const target = SERVER_ASSESSMENT_FIELDS[String(field)] ?? 'title';
      errors.assessments[index] = {
        ...errors.assessments[index],
        [target]: message,
      };
    } else if (
      code === 'planned_sessions_exceed_total' &&
      values.modules.some((row) => row.plannedSessions.trim() !== '')
    ) {
      const last = values.modules.findLastIndex(
        (row) => row.plannedSessions.trim() !== '',
      );
      errors.modules[last] = {
        ...errors.modules[last],
        plannedSessions: message,
      };
    } else {
      const target = SERVER_FIELDS[String(name)];
      if (target) {
        errors.fields[target] = message;
      }
    }
  }
  return errors;
}

/** Minutes as whole hours and minutes, for "total". */
export function totalMinutes(values: TemplateFormValues): number | null {
  const count = wholeNumber(values.sessionCount, 1, SESSION_COUNT_MAX);
  const minutes = wholeNumber(
    values.sessionMinutes,
    SESSION_MINUTES_MIN,
    SESSION_MINUTES_MAX,
  );
  return count === null || minutes === null ? null : count * minutes;
}
