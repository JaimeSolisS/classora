import type { FieldError } from '@classora/api-client';
import { ApiError } from '@/app/api';
import { z } from '@/lib/forms';
import type {
  LearningPlan,
  LearningPlanCreate,
  LearningPlanReplace,
  LearningPlanSummary,
  MilestoneStatus,
} from './api';

// The learning plan pages' pure rules (#310 A5, D2-D6): the limits (one
// constant set mirroring #81 A3, unit-tested against it), the editor's form
// values and Zod schema, the counters and the disabled adds, the POST and
// PUT bodies (ids kept for stored items, none for new ones, array order =
// position), the server's field errors mapped back onto the fields by their
// `path`, and the refusals of a write sorted into what the page shows.

// ------------------------------------------------------------------ limits

export const PLAN_TITLE_MAX = 120;
export const PLAN_DESCRIPTION_MAX = 2000;
export const OBJECTIVES_MIN = 1;
export const OBJECTIVES_MAX = 10;
export const OBJECTIVE_TITLE_MAX = 200;
export const OBJECTIVE_DESCRIPTION_MAX = 1000;
export const MILESTONES_MIN = 1;
export const MILESTONES_PER_OBJECTIVE_MAX = 10;
export const MILESTONES_PER_PLAN_MAX = 50;
export const MILESTONE_TITLE_MAX = 200;
export const ACTIVITIES_MAX = 2000;
/** The earliest due date the API accepts (due_on_invalid before it). */
export const DUE_ON_MIN = '2000-01-01';
/** The list's row cap (truncated beyond it). */
export const PLAN_LIST_MAX = 100;

// ------------------------------------------------------------------ values

export interface MilestoneValues {
  /** The stored milestone's id; null for a new one. */
  serverId: string | null;
  title: string;
  activities: string;
  /** '' or a local date. */
  dueOn: string;
  /** The stored progress (read-only in the editor); null for a new one. */
  status: MilestoneStatus | null;
}

export interface ObjectiveValues {
  /** The stored objective's id; null for a new one. */
  serverId: string | null;
  title: string;
  description: string;
  milestones: MilestoneValues[];
}

export interface PlanFormValues {
  /** '' until chosen (create only; a plan's subject never changes). */
  subjectId: string;
  title: string;
  description: string;
  /** A local date. */
  startsOn: string;
  /** '' or a local date. */
  targetOn: string;
  objectives: ObjectiveValues[];
}

export const EMPTY_MILESTONE: MilestoneValues = {
  serverId: null,
  title: '',
  activities: '',
  dueOn: '',
  status: null,
};

/** A new objective starts with one empty milestone (D3). */
export function emptyObjective(): ObjectiveValues {
  return {
    serverId: null,
    title: '',
    description: '',
    milestones: [{ ...EMPTY_MILESTONE }],
  };
}

/** A new plan: Starts on defaults to the center's today (D2). */
export function newPlanValues(today: string): PlanFormValues {
  return {
    subjectId: '',
    title: '',
    description: '',
    startsOn: today,
    targetOn: '',
    objectives: [emptyObjective()],
  };
}

/** A stored plan as form values (ids and progress kept). */
export function planValues(plan: LearningPlan): PlanFormValues {
  return {
    subjectId: plan.subject.id,
    title: plan.title,
    description: plan.description ?? '',
    startsOn: plan.starts_on,
    targetOn: plan.target_on ?? '',
    objectives: [...plan.objectives]
      .sort((a, b) => a.position - b.position)
      .map((objective) => ({
        serverId: objective.id,
        title: objective.title,
        description: objective.description ?? '',
        milestones: [...objective.milestones]
          .sort((a, b) => a.position - b.position)
          .map((milestone) => ({
            serverId: milestone.id,
            title: milestone.title,
            activities: milestone.activities ?? '',
            dueOn: milestone.due_on ?? '',
            status: milestone.status,
          })),
      })),
  };
}

// ---------------------------------------------------------------- counters

export function milestoneCount(
  objectives: readonly { milestones: readonly unknown[] }[],
): number {
  return objectives.reduce(
    (total, objective) => total + objective.milestones.length,
    0,
  );
}

/** Why "Add objective" is disabled, or null when it isn't. */
export function addObjectiveBlock(
  objectives: readonly { milestones: readonly unknown[] }[],
): 'objectives' | 'plan' | null {
  if (objectives.length >= OBJECTIVES_MAX) {
    return 'objectives';
  }
  // A new objective brings one milestone (D3).
  if (milestoneCount(objectives) >= MILESTONES_PER_PLAN_MAX) {
    return 'plan';
  }
  return null;
}

/** Why "Add milestone" on an objective is disabled, or null. */
export function addMilestoneBlock(
  objectiveMilestones: number,
  planMilestones: number,
): 'objective' | 'plan' | null {
  if (objectiveMilestones >= MILESTONES_PER_OBJECTIVE_MAX) {
    return 'objective';
  }
  if (planMilestones >= MILESTONES_PER_PLAN_MAX) {
    return 'plan';
  }
  return null;
}

/** Removing it loses recorded progress: ask first (D3). */
export function objectiveNeedsConfirm(objective: ObjectiveValues): boolean {
  return (
    objective.serverId !== null &&
    objective.milestones.some((milestone) => milestone.serverId !== null)
  );
}

export function milestoneNeedsConfirm(milestone: MilestoneValues): boolean {
  return milestone.serverId !== null && milestone.status === 'done';
}

// ------------------------------------------------------------------ schema

/**
 * A field message: a `common` key (`learningPlans.validation.*`) or
 * `errors:<code>` for a server code shown with its `errors` message.
 */
export type PlanMessage = string;

const V = 'learningPlans.validation';
const DATE = /^\d{4}-\d{2}-\d{2}$/;

const trimmed = (max: number, required: boolean, tooLong: string) =>
  z.string().superRefine((value, ctx) => {
    const length = value.trim().length;
    if (required && length === 0) {
      ctx.addIssue({ code: 'custom', message: `${V}.titleRequired` });
    } else if (length > max) {
      ctx.addIssue({ code: 'custom', message: tooLong });
    }
  });

const milestoneSchema = z.object({
  serverId: z.string().nullable(),
  title: trimmed(MILESTONE_TITLE_MAX, true, `${V}.itemTitleTooLong`),
  activities: trimmed(ACTIVITIES_MAX, false, `${V}.activitiesTooLong`),
  dueOn: z.string().superRefine((value, ctx) => {
    if (value === '') {
      return;
    }
    if (!DATE.test(value) || value < DUE_ON_MIN) {
      ctx.addIssue({ code: 'custom', message: `${V}.dueOnInvalid` });
    }
  }),
  status: z.enum(['pending', 'in_progress', 'done']).nullable(),
});

const objectiveSchema = z.object({
  serverId: z.string().nullable(),
  title: trimmed(OBJECTIVE_TITLE_MAX, true, `${V}.itemTitleTooLong`),
  description: trimmed(
    OBJECTIVE_DESCRIPTION_MAX,
    false,
    `${V}.objectiveDescriptionTooLong`,
  ),
  milestones: z
    .array(milestoneSchema)
    .min(MILESTONES_MIN, `${V}.milestonesRequired`)
    .max(MILESTONES_PER_OBJECTIVE_MAX, `${V}.milestonesLimit`),
});

/** The editor's schema (D2-D4). `creating` requires the subject. */
export function planFormSchema(creating: boolean) {
  return z
    .object({
      subjectId: z.string(),
      title: trimmed(PLAN_TITLE_MAX, true, `${V}.planTitleTooLong`),
      description: trimmed(
        PLAN_DESCRIPTION_MAX,
        false,
        `${V}.descriptionTooLong`,
      ),
      startsOn: z.string(),
      targetOn: z.string(),
      objectives: z
        .array(objectiveSchema)
        .min(OBJECTIVES_MIN, `${V}.objectivesRequired`)
        .max(OBJECTIVES_MAX, `${V}.objectivesLimit`),
    })
    .superRefine((values, ctx) => {
      if (creating && values.subjectId === '') {
        ctx.addIssue({
          code: 'custom',
          path: ['subjectId'],
          message: `${V}.subjectRequired`,
        });
      }
      if (!DATE.test(values.startsOn)) {
        ctx.addIssue({
          code: 'custom',
          path: ['startsOn'],
          message:
            values.startsOn === ''
              ? `${V}.startsOnRequired`
              : `${V}.dateInvalid`,
        });
      }
      if (values.targetOn !== '') {
        if (!DATE.test(values.targetOn)) {
          ctx.addIssue({
            code: 'custom',
            path: ['targetOn'],
            message: `${V}.dateInvalid`,
          });
        } else if (
          DATE.test(values.startsOn) &&
          values.targetOn < values.startsOn
        ) {
          ctx.addIssue({
            code: 'custom',
            path: ['targetOn'],
            message: `${V}.targetBeforeStart`,
          });
        }
      }
      if (milestoneCount(values.objectives) > MILESTONES_PER_PLAN_MAX) {
        ctx.addIssue({
          code: 'custom',
          path: ['objectives'],
          message: `${V}.planMilestonesLimit`,
        });
      }
    });
}

// ------------------------------------------------------------------ bodies

const orNull = (value: string) => {
  const text = value.trim();
  return text === '' ? null : text;
};

/** The create body (POST, D5): the objectives and milestones in order. */
export function createBody(values: PlanFormValues): LearningPlanCreate {
  return {
    subject_id: values.subjectId,
    title: values.title.trim(),
    description: orNull(values.description),
    starts_on: values.startsOn,
    target_on: values.targetOn === '' ? null : values.targetOn,
    objectives: values.objectives.map((objective) => ({
      title: objective.title.trim(),
      description: orNull(objective.description),
      milestones: objective.milestones.map((milestone) => ({
        title: milestone.title.trim(),
        activities: orNull(milestone.activities),
        due_on: milestone.dueOn === '' ? null : milestone.dueOn,
      })),
    })),
  };
}

/**
 * The replace body (PUT, D5): the plan's version, `id` for every stored
 * objective and milestone (so a milestone keeps its progress) and none for
 * new ones; the array order is the position. No subject (it never changes).
 */
export function replaceBody(
  values: PlanFormValues,
  version: number,
): LearningPlanReplace {
  return {
    version,
    title: values.title.trim(),
    description: orNull(values.description),
    starts_on: values.startsOn,
    target_on: values.targetOn === '' ? null : values.targetOn,
    objectives: values.objectives.map((objective) => ({
      ...(objective.serverId ? { id: objective.serverId } : {}),
      title: objective.title.trim(),
      description: orNull(objective.description),
      milestones: objective.milestones.map((milestone) => ({
        ...(milestone.serverId ? { id: milestone.serverId } : {}),
        title: milestone.title.trim(),
        activities: orNull(milestone.activities),
        due_on: milestone.dueOn === '' ? null : milestone.dueOn,
      })),
    })),
  };
}

// ------------------------------------------------------- server field errors

/** A field of the form a server error belongs to (RHF paths). */
export type PlanField =
  | 'subjectId'
  | 'title'
  | 'description'
  | 'startsOn'
  | 'targetOn'
  | 'objectives.root'
  | `objectives.${number}.title`
  | `objectives.${number}.description`
  | `objectives.${number}.milestones.root`
  | `objectives.${number}.milestones.${number}.title`
  | `objectives.${number}.milestones.${number}.activities`
  | `objectives.${number}.milestones.${number}.dueOn`;

const TOP: Record<string, PlanField> = {
  subject_id: 'subjectId',
  title: 'title',
  description: 'description',
  starts_on: 'startsOn',
  target_on: 'targetOn',
};
const MILESTONE_LEAF: Record<string, 'title' | 'activities' | 'dueOn'> = {
  title: 'title',
  activities: 'activities',
  due_on: 'dueOn',
};

/**
 * The form field of one server field error, from its `path` (#81: such as
 * `["body","objectives",2,"milestones",0,"title"]`), or null when it names
 * no field (an `id`: objective_unknown / milestone_unknown).
 */
export function planFieldOf(error: Pick<FieldError, 'path'>): PlanField | null {
  const [where, top, i, sub, j, leaf] = error.path;
  if (where !== 'body' || typeof top !== 'string') {
    return null;
  }
  if (top !== 'objectives') {
    return TOP[top] ?? null;
  }
  if (typeof i !== 'number') {
    return 'objectives.root';
  }
  if (sub === 'title' || sub === 'description') {
    return `objectives.${i}.${sub}`;
  }
  if (sub !== 'milestones') {
    return null;
  }
  if (typeof j !== 'number') {
    return `objectives.${i}.milestones.root`;
  }
  const field = typeof leaf === 'string' ? MILESTONE_LEAF[leaf] : undefined;
  return field ? `objectives.${i}.milestones.${j}.${field}` : null;
}

/** The message of a server field error on `field`: ours where we have one. */
export function serverPlanMessage(code: string, field: PlanField): PlanMessage {
  switch (code) {
    case 'title_required':
      return `${V}.titleRequired`;
    case 'too_long':
      if (field === 'title') return `${V}.planTitleTooLong`;
      if (field === 'description') return `${V}.descriptionTooLong`;
      if (field.endsWith('.activities')) return `${V}.activitiesTooLong`;
      if (field.endsWith('.title')) return `${V}.itemTitleTooLong`;
      if (field.endsWith('.description')) {
        return `${V}.objectiveDescriptionTooLong`;
      }
      return 'errors:too_long';
    case 'objectives_required':
      return `${V}.objectivesRequired`;
    case 'objectives_too_many':
      return `${V}.objectivesLimit`;
    case 'milestones_required':
      return `${V}.milestonesRequired`;
    case 'milestones_too_many':
      return field === 'objectives.root'
        ? `${V}.planMilestonesLimit`
        : `${V}.milestonesLimit`;
    case 'target_before_start':
      return `${V}.targetBeforeStart`;
    case 'due_on_invalid':
      return `${V}.dueOnInvalid`;
    case 'subject_unknown':
      return `${V}.subjectUnknown`;
    default:
      return `errors:${code}`;
  }
}

/** The fields of a refused save, in the order sent; empty when none maps. */
export function placedFieldErrors(
  error: unknown,
): { field: PlanField; message: PlanMessage }[] {
  if (!(error instanceof ApiError)) {
    return [];
  }
  const placed: { field: PlanField; message: PlanMessage }[] = [];
  for (const fieldError of error.fieldErrors) {
    const field = planFieldOf(fieldError);
    if (field) {
      placed.push({
        field,
        message: serverPlanMessage(fieldError.code, field),
      });
    }
  }
  return placed;
}

// ---------------------------------------------------------------- refusals

/** What a refused write shows (C6, D6). */
export type PlanRefusal =
  /** "This plan changed elsewhere." + Reload. */
  | 'conflict'
  /** "{subject} already has an active plan…" + the list link. */
  | 'active_plan'
  /** plan_closed / plan_not_draft / plan_not_active: message + Reload. */
  | 'state'
  /** plan_subject_not_assigned / plan_read_only: message + Reload. */
  | 'forbidden'
  /** The plan is gone (404): the page shows the not-found page. */
  | 'missing'
  /** A PATCH racing an edit that removed the milestone (404) + Reload. */
  | 'milestone_missing'
  /** Anything else: the error's own message. */
  | 'other';

export const STATE_CODES = ['plan_closed', 'plan_not_draft', 'plan_not_active'];
export const FORBIDDEN_CODES = ['plan_subject_not_assigned', 'plan_read_only'];
export const CONFLICT_CODES = [
  'version_conflict',
  'objective_unknown',
  'milestone_unknown',
];

/** Sorts a refused write; `milestone` for the progress PATCH. */
export function classifyRefusal(
  error: unknown,
  { milestone = false }: { milestone?: boolean } = {},
): PlanRefusal {
  if (!(error instanceof ApiError)) {
    return 'other';
  }
  if (CONFLICT_CODES.includes(error.code)) {
    return 'conflict';
  }
  if (
    error.code === 'validation_error' &&
    error.fieldErrors.some((fieldError) =>
      CONFLICT_CODES.includes(fieldError.code),
    )
  ) {
    return 'conflict';
  }
  if (error.code === 'active_plan_exists') {
    return 'active_plan';
  }
  if (STATE_CODES.includes(error.code)) {
    return 'state';
  }
  if (FORBIDDEN_CODES.includes(error.code)) {
    return 'forbidden';
  }
  if (error.status === 404) {
    return milestone ? 'milestone_missing' : 'missing';
  }
  return 'other';
}

// --------------------------------------------------------------- the list

/** The subjects that already have an active plan in a list (D1). */
export function activeSubjectIds(
  items: readonly Pick<LearningPlanSummary, 'status' | 'subject'>[],
): Set<string> {
  return new Set(
    items
      .filter((item) => item.status === 'active')
      .map((item) => item.subject.id),
  );
}

/** The milestones not done (the Complete dialog's count, C5). */
export function notDoneCount(plan: Pick<LearningPlan, 'progress'>): number {
  return plan.progress.total - plan.progress.done;
}
