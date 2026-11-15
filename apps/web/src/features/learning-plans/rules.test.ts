import { describe, expect, it } from 'vitest';
import { ApiError } from '@/app/api';
import { ACTIVE_PLAN, DRAFT_PLAN, FULL_PLAN, MANAGER_LIST } from './fixtures';
import {
  ACTIVITIES_MAX,
  activeSubjectIds,
  addMilestoneBlock,
  addObjectiveBlock,
  classifyRefusal,
  createBody,
  DUE_ON_MIN,
  emptyObjective,
  milestoneCount,
  milestoneNeedsConfirm,
  MILESTONE_TITLE_MAX,
  MILESTONES_PER_OBJECTIVE_MAX,
  MILESTONES_PER_PLAN_MAX,
  newPlanValues,
  notDoneCount,
  OBJECTIVE_DESCRIPTION_MAX,
  OBJECTIVE_TITLE_MAX,
  objectiveNeedsConfirm,
  OBJECTIVES_MAX,
  OBJECTIVES_MIN,
  MILESTONES_MIN,
  PLAN_DESCRIPTION_MAX,
  PLAN_LIST_MAX,
  PLAN_TITLE_MAX,
  placedFieldErrors,
  planFieldOf,
  planFormSchema,
  planValues,
  replaceBody,
  serverPlanMessage,
  type PlanFormValues,
} from './rules';

// #310 G1 (rules): the limits mirror #81 A3; the counters and the disabled
// adds; the editor state ↔ POST / PUT bodies (ids kept, new items without
// id, order = position); the server's field paths → form fields, nested
// indexes included; the refusals.

describe('limits (#81 A3)', () => {
  it('mirror the API', () => {
    expect({
      PLAN_TITLE_MAX,
      PLAN_DESCRIPTION_MAX,
      OBJECTIVES_MIN,
      OBJECTIVES_MAX,
      OBJECTIVE_TITLE_MAX,
      OBJECTIVE_DESCRIPTION_MAX,
      MILESTONES_MIN,
      MILESTONES_PER_OBJECTIVE_MAX,
      MILESTONES_PER_PLAN_MAX,
      MILESTONE_TITLE_MAX,
      ACTIVITIES_MAX,
      DUE_ON_MIN,
      PLAN_LIST_MAX,
    }).toEqual({
      PLAN_TITLE_MAX: 120,
      PLAN_DESCRIPTION_MAX: 2000,
      OBJECTIVES_MIN: 1,
      OBJECTIVES_MAX: 10,
      OBJECTIVE_TITLE_MAX: 200,
      OBJECTIVE_DESCRIPTION_MAX: 1000,
      MILESTONES_MIN: 1,
      MILESTONES_PER_OBJECTIVE_MAX: 10,
      MILESTONES_PER_PLAN_MAX: 50,
      MILESTONE_TITLE_MAX: 200,
      ACTIVITIES_MAX: 2000,
      DUE_ON_MIN: '2000-01-01',
      PLAN_LIST_MAX: 100,
    });
  });
});

describe('counters and disabled adds (D3)', () => {
  const objectives = (counts: number[]) =>
    counts.map((n) => ({ milestones: Array.from({ length: n }) }));

  it('counts the milestones of the plan', () => {
    expect(milestoneCount(objectives([1, 3, 2]))).toBe(6);
    expect(milestoneCount(planValues(FULL_PLAN).objectives)).toBe(50);
  });

  it('blocks "Add objective" at 10 objectives or 50 milestones', () => {
    expect(addObjectiveBlock(objectives([1]))).toBeNull();
    expect(addObjectiveBlock(objectives(Array<number>(9).fill(5)))).toBeNull();
    expect(addObjectiveBlock(objectives(Array<number>(10).fill(1)))).toBe(
      'objectives',
    );
    expect(addObjectiveBlock(objectives([10, 10, 10, 10, 10]))).toBe('plan');
    expect(addObjectiveBlock(objectives([10, 10, 10, 10, 9]))).toBeNull();
  });

  it('blocks "Add milestone" at 10 per objective or 50 per plan', () => {
    expect(addMilestoneBlock(1, 1)).toBeNull();
    expect(addMilestoneBlock(9, 49)).toBeNull();
    expect(addMilestoneBlock(10, 10)).toBe('objective');
    expect(addMilestoneBlock(4, 50)).toBe('plan');
    expect(addMilestoneBlock(10, 50)).toBe('objective');
  });

  it('a new objective brings one empty milestone', () => {
    expect(emptyObjective()).toEqual({
      serverId: null,
      title: '',
      description: '',
      milestones: [
        { serverId: null, title: '', activities: '', dueOn: '', status: null },
      ],
    });
  });

  it('asks before removing saved progress', () => {
    const values = planValues(ACTIVE_PLAN);
    expect(objectiveNeedsConfirm(values.objectives[0]!)).toBe(true);
    expect(objectiveNeedsConfirm(emptyObjective())).toBe(false);
    expect(
      objectiveNeedsConfirm({ ...emptyObjective(), serverId: 'o-1' }),
    ).toBe(false);
    expect(milestoneNeedsConfirm(values.objectives[0]!.milestones[0]!)).toBe(
      true,
    );
    // Saved but not done: removed without asking.
    expect(milestoneNeedsConfirm(values.objectives[0]!.milestones[1]!)).toBe(
      false,
    );
  });

  it('counts the milestones that are not done (Complete)', () => {
    expect(notDoneCount(ACTIVE_PLAN)).toBe(2);
  });

  it('marks the subjects with an active plan', () => {
    expect([...activeSubjectIds(MANAGER_LIST.items)]).toEqual([
      ACTIVE_PLAN.subject.id,
    ]);
  });
});

describe('the schema (D2-D4)', () => {
  const valid = (): PlanFormValues => ({
    ...newPlanValues('2026-11-12'),
    subjectId: 's-1',
    title: 'Plan',
    objectives: [
      {
        ...emptyObjective(),
        title: 'O',
        milestones: [{ ...emptyObjective().milestones[0]!, title: 'M' }],
      },
    ],
  });
  const issues = (values: PlanFormValues, creating = true) => {
    const result = planFormSchema(creating).safeParse(values);
    return result.success
      ? []
      : result.error.issues.map((issue) => [
          issue.path.join('.'),
          issue.message,
        ]);
  };

  it('accepts a valid plan', () => {
    expect(issues(valid())).toEqual([]);
  });

  it('requires the subject on a create only', () => {
    expect(issues({ ...valid(), subjectId: '' })).toEqual([
      ['subjectId', 'learningPlans.validation.subjectRequired'],
    ]);
    expect(issues({ ...valid(), subjectId: '' }, false)).toEqual([]);
  });

  it('checks the titles and lengths (trimmed)', () => {
    const values = valid();
    values.title = '   ';
    values.description = 'x'.repeat(2001);
    values.objectives[0]!.title = 'x'.repeat(201);
    values.objectives[0]!.description = ` ${'x'.repeat(1000)} `;
    values.objectives[0]!.milestones[0]!.title = '';
    values.objectives[0]!.milestones[0]!.activities = 'x'.repeat(2001);
    expect(issues(values)).toEqual([
      ['title', 'learningPlans.validation.titleRequired'],
      ['description', 'learningPlans.validation.descriptionTooLong'],
      ['objectives.0.title', 'learningPlans.validation.itemTitleTooLong'],
      [
        'objectives.0.milestones.0.title',
        'learningPlans.validation.titleRequired',
      ],
      [
        'objectives.0.milestones.0.activities',
        'learningPlans.validation.activitiesTooLong',
      ],
    ]);
    expect(issues({ ...valid(), title: 'x'.repeat(121) })).toEqual([
      ['title', 'learningPlans.validation.planTitleTooLong'],
    ]);
  });

  it('checks the dates: target_before_start, a due date from 2000', () => {
    expect(issues({ ...valid(), targetOn: '2026-11-11' })).toEqual([
      ['targetOn', 'learningPlans.validation.targetBeforeStart'],
    ]);
    expect(issues({ ...valid(), targetOn: '2026-11-12' })).toEqual([]);
    expect(issues({ ...valid(), startsOn: '' })).toEqual([
      ['startsOn', 'learningPlans.validation.startsOnRequired'],
    ]);
    const due = valid();
    due.objectives[0]!.milestones[0]!.dueOn = '1999-12-31';
    expect(issues(due)).toEqual([
      [
        'objectives.0.milestones.0.dueOn',
        'learningPlans.validation.dueOnInvalid',
      ],
    ]);
    due.objectives[0]!.milestones[0]!.dueOn = '2000-01-01';
    expect(issues(due)).toEqual([]);
    // Outside the plan's dates is allowed (#81 C2).
    due.objectives[0]!.milestones[0]!.dueOn = '2030-01-01';
    expect(issues(due)).toEqual([]);
  });

  it('checks the counts: 1-10 objectives, 1-10 milestones, 50 in all', () => {
    expect(issues({ ...valid(), objectives: [] })).toEqual([
      ['objectives', 'learningPlans.validation.objectivesRequired'],
    ]);
    const noMilestones = valid();
    noMilestones.objectives[0]!.milestones = [];
    expect(issues(noMilestones)).toEqual([
      [
        'objectives.0.milestones',
        'learningPlans.validation.milestonesRequired',
      ],
    ]);
    const full = planValues(FULL_PLAN);
    expect(issues(full, false)).toEqual([]);
    full.objectives[0]!.milestones.push({
      ...full.objectives[0]!.milestones[0]!,
      serverId: null,
    });
    expect(issues(full, false)).toEqual([
      ['objectives', 'learningPlans.validation.planMilestonesLimit'],
    ]);
  });
});

describe('bodies (D5)', () => {
  it('maps a stored plan to values, in position order', () => {
    const values = planValues({
      ...ACTIVE_PLAN,
      objectives: [...ACTIVE_PLAN.objectives].reverse(),
    });
    expect(values.objectives.map((o) => o.title)).toEqual([
      'Objetivo 1',
      'Objetivo 2',
    ]);
    expect(values.objectives[0]!.milestones[0]).toEqual({
      serverId: ACTIVE_PLAN.objectives[0]!.milestones[0]!.id,
      title: 'Lectura diaria',
      activities: '10 min al día',
      dueOn: '',
      status: 'done',
    });
    expect(values.targetOn).toBe('2027-01-29');
  });

  it('the POST body: trimmed, empty texts as null, in order', () => {
    const values = planValues(DRAFT_PLAN);
    values.title = '  Leer  ';
    values.targetOn = '';
    values.objectives[0]!.milestones[0]!.activities = '   ';
    expect(createBody(values)).toEqual({
      subject_id: DRAFT_PLAN.subject.id,
      title: 'Leer',
      description: 'Mejorar la comprensión lectora.\nY la velocidad.',
      starts_on: '2026-11-02',
      target_on: null,
      objectives: [
        {
          title: 'Objetivo 1',
          description: null,
          milestones: [
            { title: 'Lectura diaria', activities: null, due_on: null },
            { title: 'Resumen', activities: null, due_on: '2026-12-01' },
          ],
        },
        {
          title: 'Objetivo 2',
          description: null,
          milestones: [
            { title: 'Vocabulario', activities: null, due_on: null },
          ],
        },
      ],
    });
  });

  it('the PUT body keeps the ids of stored items, none for new ones', () => {
    const values = planValues(ACTIVE_PLAN);
    // Move objective 2 first, add a milestone to it, and a new objective.
    values.objectives = [values.objectives[1]!, values.objectives[0]!];
    values.objectives[0]!.milestones.push({
      serverId: null,
      title: 'Nuevo',
      activities: '',
      dueOn: '',
      status: null,
    });
    values.objectives.push({ ...emptyObjective(), title: 'Otro' });
    values.objectives[2]!.milestones[0]!.title = 'Hito nuevo';
    const body = replaceBody(values, 5);
    const [o1, o2, o3] = ACTIVE_PLAN.objectives.map((o) => o);
    expect(body.version).toBe(5);
    expect(body).not.toHaveProperty('subject_id');
    expect(body.objectives).toEqual([
      {
        id: o2!.id,
        title: 'Objetivo 2',
        description: null,
        milestones: [
          {
            id: o2!.milestones[0]!.id,
            title: 'Vocabulario',
            activities: null,
            due_on: null,
          },
          { title: 'Nuevo', activities: null, due_on: null },
        ],
      },
      {
        id: o1!.id,
        title: 'Objetivo 1',
        description: null,
        milestones: [
          {
            id: o1!.milestones[0]!.id,
            title: 'Lectura diaria',
            activities: '10 min al día',
            due_on: null,
          },
          {
            id: o1!.milestones[1]!.id,
            title: 'Resumen',
            activities: null,
            due_on: '2026-11-05',
          },
        ],
      },
      {
        title: 'Otro',
        description: null,
        milestones: [{ title: 'Hito nuevo', activities: null, due_on: null }],
      },
    ]);
    expect(o3).toBeUndefined();
    // New items carry no id key at all (not null).
    expect(Object.keys(body.objectives[2]!)).not.toContain('id');
    expect(Object.keys(body.objectives[0]!.milestones[1]!)).not.toContain('id');
  });
});

describe('server field errors by path (D6)', () => {
  it.each([
    [['body', 'title'], 'title'],
    [['body', 'description'], 'description'],
    [['body', 'subject_id'], 'subjectId'],
    [['body', 'starts_on'], 'startsOn'],
    [['body', 'target_on'], 'targetOn'],
    [['body', 'objectives'], 'objectives.root'],
    [['body', 'objectives', 2, 'title'], 'objectives.2.title'],
    [['body', 'objectives', 0, 'description'], 'objectives.0.description'],
    [['body', 'objectives', 3, 'milestones'], 'objectives.3.milestones.root'],
    [
      ['body', 'objectives', 2, 'milestones', 0, 'title'],
      'objectives.2.milestones.0.title',
    ],
    [
      ['body', 'objectives', 1, 'milestones', 4, 'activities'],
      'objectives.1.milestones.4.activities',
    ],
    [
      ['body', 'objectives', 0, 'milestones', 9, 'due_on'],
      'objectives.0.milestones.9.dueOn',
    ],
  ] as const)('%j → %s', (path, field) => {
    expect(planFieldOf({ path: [...path] })).toBe(field);
  });

  it.each([
    [['body', 'objectives', 0, 'id']],
    [['body', 'objectives', 0, 'milestones', 1, 'id']],
    [['query', 'version']],
    [['body', 'unknown']],
  ] as const)('%j names no field', (path) => {
    expect(planFieldOf({ path: [...path] })).toBeNull();
  });

  it.each([
    ['title_required', 'title', 'learningPlans.validation.titleRequired'],
    ['too_long', 'title', 'learningPlans.validation.planTitleTooLong'],
    ['too_long', 'description', 'learningPlans.validation.descriptionTooLong'],
    [
      'too_long',
      'objectives.0.title',
      'learningPlans.validation.itemTitleTooLong',
    ],
    [
      'too_long',
      'objectives.0.description',
      'learningPlans.validation.objectiveDescriptionTooLong',
    ],
    [
      'too_long',
      'objectives.0.milestones.1.title',
      'learningPlans.validation.itemTitleTooLong',
    ],
    [
      'too_long',
      'objectives.0.milestones.1.activities',
      'learningPlans.validation.activitiesTooLong',
    ],
    [
      'objectives_required',
      'objectives.root',
      'learningPlans.validation.objectivesRequired',
    ],
    [
      'objectives_too_many',
      'objectives.root',
      'learningPlans.validation.objectivesLimit',
    ],
    [
      'milestones_required',
      'objectives.0.milestones.root',
      'learningPlans.validation.milestonesRequired',
    ],
    [
      'milestones_too_many',
      'objectives.0.milestones.root',
      'learningPlans.validation.milestonesLimit',
    ],
    [
      'milestones_too_many',
      'objectives.root',
      'learningPlans.validation.planMilestonesLimit',
    ],
    [
      'target_before_start',
      'targetOn',
      'learningPlans.validation.targetBeforeStart',
    ],
    [
      'due_on_invalid',
      'objectives.0.milestones.0.dueOn',
      'learningPlans.validation.dueOnInvalid',
    ],
    ['subject_unknown', 'subjectId', 'learningPlans.validation.subjectUnknown'],
    ['something_else', 'title', 'errors:something_else'],
  ] as const)('%s on %s → %s', (code, field, message) => {
    expect(serverPlanMessage(code, field)).toBe(message);
  });

  it('places the errors of a refused save in the order sent', () => {
    const error = new ApiError({
      code: 'validation_error',
      status: 422,
      fieldErrors: [
        {
          path: ['body', 'objectives', 1, 'milestones', 0, 'title'],
          code: 'title_required',
          message: 'm',
        },
        {
          path: ['body', 'objectives', 0, 'id'],
          code: 'objective_unknown',
          message: 'm',
        },
        { path: ['body', 'title'], code: 'too_long', message: 'm' },
      ],
    });
    expect(placedFieldErrors(error)).toEqual([
      {
        field: 'objectives.1.milestones.0.title',
        message: 'learningPlans.validation.titleRequired',
      },
      { field: 'title', message: 'learningPlans.validation.planTitleTooLong' },
    ]);
    expect(placedFieldErrors(new Error('x'))).toEqual([]);
  });
});

describe('refusals (C6, D6)', () => {
  const api = (code: string, status: number, fieldCode?: string) =>
    new ApiError({
      code,
      status,
      fieldErrors: fieldCode
        ? [
            {
              path: ['body', 'objectives', 0, 'id'],
              code: fieldCode,
              message: 'm',
            },
          ]
        : [],
    });

  it.each([
    [api('version_conflict', 409), 'conflict'],
    [api('validation_error', 422, 'objective_unknown'), 'conflict'],
    [api('validation_error', 422, 'milestone_unknown'), 'conflict'],
    [api('active_plan_exists', 409), 'active_plan'],
    [api('plan_closed', 409), 'state'],
    [api('plan_not_draft', 409), 'state'],
    [api('plan_not_active', 409), 'state'],
    [api('plan_subject_not_assigned', 403), 'forbidden'],
    [api('plan_read_only', 403), 'forbidden'],
    [api('not_found', 404), 'missing'],
    [api('validation_error', 422, 'title_required'), 'other'],
    [new Error('boom'), 'other'],
  ] as const)('%#: → %s', (error, refusal) => {
    expect(classifyRefusal(error)).toBe(refusal);
  });

  it('a 404 on the progress PATCH is a missing milestone', () => {
    expect(classifyRefusal(api('not_found', 404), { milestone: true })).toBe(
      'milestone_missing',
    );
  });
});
