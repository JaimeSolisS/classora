import {
  JOB_STATUSES,
  type ImportJob,
  type ImportJobStatus,
  type ImportListParams,
  type ImportRowParams,
  type ImportRowStatus,
} from './api';

// Pure helpers of the import pages (M8-05b): the URL parameters of the
// history (B4) and of the report's rows (C2), the row filters offered for
// a job status, and the job states.

/** The history parameters in `search`; anything invalid is ignored. */
export function historyParams(
  search: URLSearchParams,
  kinds: readonly string[],
): ImportListParams {
  const kind = search.get('historyKind') ?? '';
  const status = search.get('status') ?? '';
  const page = Number.parseInt(search.get('page') ?? '', 10);
  return {
    kind: kinds.includes(kind) ? kind : '',
    status: (JOB_STATUSES as readonly string[]).includes(status)
      ? (status as ImportJobStatus)
      : '',
    page: Number.isInteger(page) && page >= 1 ? page : 1,
  };
}

/** Whether the job was committed (its counts include the outcomes). */
export function showsOutcome(job: ImportJob): boolean {
  return (
    job.status === 'committing' ||
    job.status === 'committed' ||
    job.status === 'failed'
  );
}

const BEFORE_COMMIT: readonly ImportRowStatus[] = [
  'ready',
  'duplicate',
  'error',
];
const OUTCOMES: readonly ImportRowStatus[] = ['created', 'skipped', 'failed'];

/** The row filters offered for a job status (C2). */
export function rowFilters(
  status: ImportJobStatus,
): readonly ImportRowStatus[] {
  switch (status) {
    case 'staged':
      return BEFORE_COMMIT;
    case 'committed':
      return ['duplicate', 'error', ...OUTCOMES];
    case 'committing':
    case 'failed':
      return [...BEFORE_COMMIT, ...OUTCOMES];
    default:
      return [];
  }
}

export function rowParams(
  search: URLSearchParams,
  filters: readonly ImportRowStatus[],
): ImportRowParams {
  const status = search.get('rows') ?? '';
  const page = Number.parseInt(search.get('page') ?? '', 10);
  return {
    status: (filters as readonly string[]).includes(status)
      ? (status as ImportRowStatus)
      : '',
    page: Number.isInteger(page) && page >= 1 ? page : 1,
  };
}

export const isOpen = (job: ImportJob) =>
  job.status === 'staged' || job.status === 'failed';
