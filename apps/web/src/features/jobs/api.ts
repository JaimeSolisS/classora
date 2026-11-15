import type { components } from '@classora/api-client';
import { api, apiCall } from '@/app/api';
import { localDayBounds } from '@/lib/time';
import type { JobFilters } from './filters';

// The failed jobs calls (#128 D; GET /api/v1/jobs, GET /api/v1/jobs/tasks,
// POST /api/v1/jobs/{id}/retry), for jobs.manage holders (Owner,
// Administrator). The list is newest failure first in cursor pages. A job's
// arguments are never part of the API, so the page can't show them.

export type FailedJob = components['schemas']['FailedJobItem'];
export type FailedJobPage = components['schemas']['CursorPage_FailedJobItem_'];
export type JobTask = components['schemas']['JobTaskItem'];

/** Jobs per page (G3). */
export const JOBS_PAGE_SIZE = 25;

export const JOBS_QUERY_KEY = ['jobs'] as const;
export const JOB_TASKS_KEY = [...JOBS_QUERY_KEY, 'tasks'] as const;

export function jobsKey(filters: JobFilters, timeZone: string) {
  return [...JOBS_QUERY_KEY, 'list', { ...filters, timeZone }] as const;
}

/**
 * The tasks with a label (`jobs.tasks.*`, es and en): every task of the
 * API's retry classification (#128 C1). A task added later shows its code
 * until it gets a label.
 */
export const LABELLED_TASKS = [
  'identity:purge_auth_sessions',
  'idempotency:purge_keys',
  'ratelimit:purge_counters',
  'scheduling:expire_offers',
  'scheduling:materialize_series',
  'academics:create_progress_report_placeholders',
  'billing:post_session_charges',
  'billing:post_enrollment_charges',
  'billing:post_tuition_charges',
  'billing:expire_packages',
  'billing:send_charge_reminders',
  'billing:reconcile_session_charges',
  'payroll:open_periods',
  'payroll:post_adjustments',
  'finance:generate_recurring_expenses',
  'administration:run_imports',
  'reporting:run_exports',
  'administration:run_retention',
  'demo:succeed',
  'demo:fail',
  'demo:periodic',
] as const;
export type LabelledTask = (typeof LABELLED_TASKS)[number];

type TaskLabelKey<T extends string> = T extends `${infer M}:${infer N}`
  ? `jobs.tasks.${M}.${N}`
  : never;

export function isLabelledTask(taskName: string): taskName is LabelledTask {
  return (LABELLED_TASKS as readonly string[]).includes(taskName);
}

/**
 * The i18n key of a task's label: `jobs.tasks.<module>.<name>`. Task names
 * hold a `:`, which i18next reads as a namespace separator, so it never
 * reaches the key (#130's inventory check calls this).
 */
export function taskLabelKey<T extends LabelledTask>(
  taskName: T,
): TaskLabelKey<T> {
  return `jobs.tasks.${taskName.replace(':', '.')}` as TaskLabelKey<T>;
}

/**
 * The query of `filters`: the center-local "from" day starts the range and
 * the day after "to" ends it (exclusive).
 */
export function jobsQuery(filters: JobFilters, timeZone: string) {
  return {
    ...(filters.task ? { task: filters.task } : {}),
    ...(filters.status ? { status: filters.status } : {}),
    ...(filters.from
      ? { from: localDayBounds(filters.from, timeZone).start }
      : {}),
    ...(filters.to ? { to: localDayBounds(filters.to, timeZone).end } : {}),
  };
}

export function fetchJobs(
  filters: JobFilters,
  timeZone: string,
  cursor: string | null,
  signal?: AbortSignal,
): Promise<FailedJobPage> {
  return apiCall(
    () =>
      api.GET('/api/v1/jobs', {
        params: {
          query: {
            ...jobsQuery(filters, timeZone),
            limit: JOBS_PAGE_SIZE,
            ...(cursor ? { cursor } : {}),
          },
        },
        signal,
      }),
    { signal },
  );
}

export function fetchJobTasks(signal?: AbortSignal): Promise<JobTask[]> {
  return apiCall(() => api.GET('/api/v1/jobs/tasks', { signal }), {
    signal,
  }).then((body) => body.items);
}

export function retryJob(jobId: number): Promise<FailedJob> {
  return apiCall(() =>
    api.POST('/api/v1/jobs/{job_id}/retry', {
      params: { path: { job_id: jobId } },
    }),
  );
}
