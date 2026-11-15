import type { components } from '@classora/api-client';
import type { QueryClient } from '@tanstack/react-query';
import { api, apiCall } from '@/app/api';

// The learning plan calls (#81, #310 A5): a student's plan list (with the
// create hints, AM1), one plan, the create, the whole-content replace, the
// transition (activate, complete, discontinue), the milestone progress and
// the draft delete. The same closed shapes for staff, tutors and families;
// the API's plan record policy decides (#81 "Who").

export type LearningPlanList = components['schemas']['LearningPlanList'];
export type LearningPlanSummary = components['schemas']['LearningPlanSummary'];
export type LearningPlan = components['schemas']['LearningPlanView'];
export type LearningPlanObjective =
  components['schemas']['LearningPlanObjectiveView'];
export type LearningPlanMilestone =
  components['schemas']['LearningPlanMilestoneView'];
export type LearningPlanProgress =
  components['schemas']['LearningPlanProgress'];
export type LearningPlanSubject = components['schemas']['LearningPlanSubject'];
export type LearningPlanCreate = components['schemas']['LearningPlanCreate'];
export type LearningPlanReplace = components['schemas']['LearningPlanReplace'];
export type PlanStatus = components['schemas']['PlanStatus'];
export type PlanAction = components['schemas']['PlanAction'];
export type MilestoneStatus = components['schemas']['MilestoneStatus'];

export const PLAN_STATUSES: readonly PlanStatus[] = [
  'draft',
  'active',
  'completed',
  'discontinued',
];
export const MILESTONE_STATUSES: readonly MilestoneStatus[] = [
  'pending',
  'in_progress',
  'done',
];

/** The one query-key prefix of the learning plan pages (A5). */
export const LEARNING_PLANS_KEY = ['learning-plans'] as const;

export function planListKey(studentId: string) {
  return [...LEARNING_PLANS_KEY, 'list', studentId] as const;
}

export function planKey(planId: string) {
  return [...LEARNING_PLANS_KEY, 'detail', planId] as const;
}

/** After a write: the student's list (and every cached list) refetch. */
export function invalidatePlanLists(client: QueryClient): Promise<void> {
  return client.invalidateQueries({
    queryKey: [...LEARNING_PLANS_KEY, 'list'],
  });
}

export function fetchPlanList(
  studentId: string,
  signal?: AbortSignal,
): Promise<LearningPlanList> {
  return apiCall(
    () =>
      api.GET('/api/v1/students/{student_id}/learning-plans', {
        params: { path: { student_id: studentId } },
        signal,
      }),
    { signal },
  );
}

export function fetchPlan(
  planId: string,
  signal?: AbortSignal,
): Promise<LearningPlan> {
  return apiCall(
    () =>
      api.GET('/api/v1/learning-plans/{plan_id}', {
        params: { path: { plan_id: planId } },
        signal,
      }),
    { signal },
  );
}

export function createPlan(
  studentId: string,
  body: LearningPlanCreate,
): Promise<LearningPlan> {
  return apiCall(() =>
    api.POST('/api/v1/students/{student_id}/learning-plans', {
      params: { path: { student_id: studentId } },
      body,
    }),
  );
}

export function replacePlan(
  planId: string,
  body: LearningPlanReplace,
): Promise<LearningPlan> {
  return apiCall(() =>
    api.PUT('/api/v1/learning-plans/{plan_id}', {
      params: { path: { plan_id: planId } },
      body,
    }),
  );
}

export function transitionPlan(
  plan: Pick<LearningPlan, 'id' | 'version'>,
  action: PlanAction,
): Promise<LearningPlan> {
  return apiCall(() =>
    api.POST('/api/v1/learning-plans/{plan_id}/transition', {
      params: { path: { plan_id: plan.id } },
      body: { version: plan.version, action },
    }),
  );
}

export function setMilestoneStatus(
  milestone: Pick<LearningPlanMilestone, 'id' | 'version'>,
  status: MilestoneStatus,
): Promise<LearningPlan> {
  return apiCall(() =>
    api.PATCH('/api/v1/learning-plan-milestones/{milestone_id}', {
      params: { path: { milestone_id: milestone.id } },
      body: { version: milestone.version, status },
    }),
  );
}

export async function deletePlan(
  plan: Pick<LearningPlan, 'id' | 'version'>,
): Promise<void> {
  await apiCall(() =>
    api.DELETE('/api/v1/learning-plans/{plan_id}', {
      params: { path: { plan_id: plan.id }, query: { version: plan.version } },
    }),
  );
}

// ------------------------------------------------------------------ paths

export function studentPlansPath(studentId: string): string {
  return `/students/${encodeURIComponent(studentId)}/learning-plans`;
}

export function newPlanPath(studentId: string): string {
  return `${studentPlansPath(studentId)}/new`;
}

export function planPath(planId: string): string {
  return `/learning-plans/${encodeURIComponent(planId)}`;
}

export function editPlanPath(planId: string): string {
  return `${planPath(planId)}/edit`;
}

export function myPlansPath(studentId?: string | null): string {
  return studentId
    ? `/my-learning-plans?student=${encodeURIComponent(studentId)}`
    : '/my-learning-plans';
}

export function myPlanPath(planId: string): string {
  return `/my-learning-plans/${encodeURIComponent(planId)}`;
}
