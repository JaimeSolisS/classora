import type { FailedJob, JobTask } from './api';

// Failed jobs for the tests (#128): a session charge run that failed
// (retryable), a backfilled failure with no error class, a retried job with
// a SQLSTATE, and a job of a task with no label (not retryable).

export const CHARGE_RUN: FailedJob = {
  id: 41,
  task_name: 'billing:post_session_charges',
  queue_name: 'default',
  status: 'failed',
  attempts: 1,
  error_class: 'RuntimeError',
  error_code: null,
  sqlstate: null,
  deferred_at: '2026-11-04T18:00:00Z',
  failed_at: '2026-11-04T18:30:00Z',
  retried_count: 0,
  last_retried_at: null,
  retryable: true,
};

export const BACKFILLED: FailedJob = {
  ...CHARGE_RUN,
  id: 17,
  task_name: 'payroll:open_periods',
  error_class: null,
  deferred_at: null,
  failed_at: '2026-11-01T13:50:00Z',
};

export const RETRIED: FailedJob = {
  ...CHARGE_RUN,
  id: 12,
  task_name: 'finance:generate_recurring_expenses',
  attempts: 3,
  error_class: 'IntegrityError',
  sqlstate: '23505',
  failed_at: '2026-10-30T14:00:00Z',
  retried_count: 1,
  last_retried_at: '2026-10-30T13:00:00Z',
};

export const UNKNOWN_TASK: FailedJob = {
  ...CHARGE_RUN,
  id: 5,
  task_name: 'legacy:renamed_task',
  error_class: 'ChargeRunError',
  error_code: 'charge_run_failed',
  failed_at: '2026-10-20T14:00:00Z',
  retryable: false,
};

export const JOBS: FailedJob[] = [
  CHARGE_RUN,
  BACKFILLED,
  RETRIED,
  UNKNOWN_TASK,
];

export const TASKS: JobTask[] = [
  { task_name: 'billing:expire_packages', retryable: true },
  { task_name: 'billing:post_session_charges', retryable: true },
  { task_name: 'payroll:open_periods', retryable: true },
];

/** `n` retryable failures, newest first, one minute apart. */
export function manyJobs(n: number, firstId = 1000): FailedJob[] {
  return Array.from({ length: n }, (_, index) => ({
    ...CHARGE_RUN,
    id: firstId - index,
    failed_at: new Date(
      Date.parse(CHARGE_RUN.failed_at) - index * 60_000,
    ).toISOString(),
  }));
}
