import type { components } from '@classora/api-client';
import { api, apiCall } from '@/app/api';
import { PICKER_LIMIT } from '@/features/guardians/api';
import { fullName } from '@/features/guardians/rules';
import type { ServicePrice } from '../pricing/api';

// The tuition plan calls (#93, #374): the list and one plan are queries;
// creating, editing, pausing, resuming and deleting a plan are direct calls
// after which the pages invalidate the plan queries. The create dialog's
// choices are read here too: active students (the #95b picker), a
// student's group enrollments and every per-period catalog price.

export type TuitionPlan = components['schemas']['TuitionPlanItem'];
export type TuitionPlanDetail = components['schemas']['TuitionPlanDetail'];
export type TuitionPlanPage =
  components['schemas']['OffsetPage_TuitionPlanItem_'];
export type TuitionPlanCreate = components['schemas']['TuitionPlanCreate'];
export type TuitionPlanUpdate = components['schemas']['TuitionPlanUpdate'];
export type TuitionPlanStatus = components['schemas']['TuitionPlanStatus'];
export type TuitionPause = components['schemas']['TuitionPauseItem'];
export type TuitionPauseCreate = components['schemas']['TuitionPauseCreate'];
export type TuitionResume = components['schemas']['TuitionResume'];
export type UpcomingPeriod = components['schemas']['TuitionUpcomingPeriod'];
export type PostedPeriod = components['schemas']['TuitionPostedPeriod'];
export type PeriodState = components['schemas']['PeriodState'];
export type ChargeStatus = components['schemas']['ChargeStatus'];
export type EligibleEnrollment = components['schemas']['EligibleEnrollment'];

export const PLAN_STATUSES: readonly TuitionPlanStatus[] = [
  'scheduled',
  'active',
  'paused',
  'ended',
];

/** Rows per page of the plan list (the API default). */
export const PLANS_PAGE_SIZE = 25;
const CATALOG_PAGE_SIZE = 100;
const CATALOG_MAX_PAGES = 100;

/** Every cached plan query (the list pages and each plan). */
export const TUITION_QUERY_KEY = ['billing', 'tuition-plans'] as const;
/** The create dialog's choices. */
export const TUITION_OPTIONS_KEY = ['billing', 'tuition-options'] as const;

export interface PlanListFilters {
  /** Empty: every status. */
  statuses: TuitionPlanStatus[];
  studentId: string | null;
  page: number;
}

export function plansListKey(filters: PlanListFilters) {
  return [...TUITION_QUERY_KEY, 'list', filters] as const;
}

export function planKey(planId: string) {
  return [...TUITION_QUERY_KEY, 'detail', planId] as const;
}

export function fetchPlans(
  filters: PlanListFilters,
  signal: AbortSignal,
): Promise<TuitionPlanPage> {
  return apiCall(
    () =>
      api.GET('/api/v1/billing/tuition-plans', {
        params: {
          query: {
            ...(filters.statuses.length > 0
              ? { status: filters.statuses }
              : {}),
            ...(filters.studentId ? { student_id: filters.studentId } : {}),
            limit: PLANS_PAGE_SIZE,
            offset: (filters.page - 1) * PLANS_PAGE_SIZE,
          },
        },
        signal,
      }),
    { signal },
  );
}

export function fetchPlan(
  planId: string,
  signal: AbortSignal,
): Promise<TuitionPlanDetail> {
  return apiCall(
    () =>
      api.GET('/api/v1/billing/tuition-plans/{tuition_plan_id}', {
        params: { path: { tuition_plan_id: planId } },
        signal,
      }),
    { signal },
  );
}

export function createPlan(
  body: TuitionPlanCreate,
): Promise<TuitionPlanDetail> {
  return apiCall(() => api.POST('/api/v1/billing/tuition-plans', { body }));
}

export function updatePlan(
  planId: string,
  body: TuitionPlanUpdate,
): Promise<TuitionPlanDetail> {
  return apiCall(() =>
    api.PATCH('/api/v1/billing/tuition-plans/{tuition_plan_id}', {
      params: { path: { tuition_plan_id: planId } },
      body,
    }),
  );
}

export async function deletePlan(
  planId: string,
  version: number,
): Promise<void> {
  await apiCall(() =>
    api.DELETE('/api/v1/billing/tuition-plans/{tuition_plan_id}', {
      params: { path: { tuition_plan_id: planId }, query: { version } },
    }),
  );
}

export function pausePlan(
  planId: string,
  body: TuitionPauseCreate,
): Promise<TuitionPlanDetail> {
  return apiCall(() =>
    api.POST('/api/v1/billing/tuition-plans/{tuition_plan_id}/pauses', {
      params: { path: { tuition_plan_id: planId } },
      body,
    }),
  );
}

export function resumePlan(
  planId: string,
  body: TuitionResume,
): Promise<TuitionPlanDetail> {
  return apiCall(() =>
    api.POST('/api/v1/billing/tuition-plans/{tuition_plan_id}/resume', {
      params: { path: { tuition_plan_id: planId } },
      body,
    }),
  );
}

// --------------------------------------------------------- the choices

export interface StudentOption {
  id: string;
  name: string;
}

/** Up to PICKER_LIMIT active students matching `q` (a plan needs one). */
export async function searchStudents(
  q: string,
  signal: AbortSignal,
): Promise<StudentOption[]> {
  const page = await apiCall(
    () =>
      api.GET('/api/v1/students', {
        params: { query: { q, status: 'active', limit: PICKER_LIMIT } },
        signal,
      }),
    { signal },
  );
  return page.items.map((student) => ({
    id: student.id,
    name: fullName(student),
  }));
}

/** A student by id, with its name (the list's deep link ?student_id=). */
export async function fetchStudentOption(
  studentId: string,
  signal: AbortSignal,
): Promise<StudentOption> {
  const student = await apiCall(
    () =>
      api.GET('/api/v1/students/{student_id}', {
        params: { path: { student_id: studentId } },
        signal,
      }),
    { signal },
  );
  return { id: student.id, name: fullName(student) };
}

export function studentOptionKey(studentId: string) {
  return [...TUITION_OPTIONS_KEY, 'student', studentId] as const;
}

/** The student's active group enrollments (billing.manage). */
export function fetchEligibleEnrollments(
  studentId: string,
  signal: AbortSignal,
): Promise<EligibleEnrollment[]> {
  return apiCall(
    () =>
      api.GET('/api/v1/billing/tuition-plans/eligible-enrollments', {
        params: { query: { student_id: studentId } },
        signal,
      }),
    { signal },
  );
}

export function eligibleEnrollmentsKey(studentId: string) {
  return [...TUITION_OPTIONS_KEY, 'enrollments', studentId] as const;
}

export const PER_PERIOD_PRICES_KEY = [
  ...TUITION_OPTIONS_KEY,
  'per-period-prices',
] as const;

/** Every per-period catalog price, in the catalog's order. */
export async function fetchPerPeriodPrices(
  signal: AbortSignal,
): Promise<ServicePrice[]> {
  const items: ServicePrice[] = [];
  for (let page = 0; page < CATALOG_MAX_PAGES; page += 1) {
    const result = await apiCall(
      () =>
        api.GET('/api/v1/billing/service-prices', {
          params: {
            query: {
              pricing_model: 'per_period',
              limit: CATALOG_PAGE_SIZE,
              offset: page * CATALOG_PAGE_SIZE,
            },
          },
          signal,
        }),
      { signal },
    );
    items.push(...result.items);
    if (result.items.length === 0 || items.length >= result.total) {
      break;
    }
  }
  return items.filter((item) => item.pricing_model === 'per_period');
}
