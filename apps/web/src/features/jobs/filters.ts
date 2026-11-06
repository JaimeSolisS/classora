import { parseLocalDate } from '@/lib/time';

// The failed jobs page's filters live in the URL (#128 G2): ?task=…&status=…
// &from=YYYY-MM-DD&to=YYYY-MM-DD, so back/forward and links restore them.
// "Hasta" is inclusive here and exclusive in the API (the #120 pattern).

export const STATUS_FILTERS = [
  'failed',
  'any',
  'todo',
  'doing',
  'succeeded',
  'cancelled',
  'aborted',
] as const;
export type StatusFilter = (typeof STATUS_FILTERS)[number];

export interface JobFilters {
  task: string | null;
  /** `null` is the API default, `failed`. */
  status: StatusFilter | null;
  /** Center-local calendar dates. */
  from: string | null;
  to: string | null;
}

export const NO_FILTERS: JobFilters = {
  task: null,
  status: null,
  from: null,
  to: null,
};

const KEYS = [
  'task',
  'status',
  'from',
  'to',
] as const satisfies readonly (keyof JobFilters)[];

/** The API's task name pattern (`^[a-z_]+:[a-z_]+$`, at most 128). */
const TASK = /^[a-z_]+:[a-z_]+$/;

function isStatusFilter(value: string): value is StatusFilter {
  return (STATUS_FILTERS as readonly string[]).includes(value);
}

export function filtersFromParams(params: URLSearchParams): JobFilters {
  const read = (key: string) => {
    const value = params.get(key)?.trim();
    return value ? value : null;
  };
  const status = read('status');
  return {
    task: read('task'),
    // `failed` is the default: it is never kept in the URL. An unknown value
    // is kept as written; validateFilters refuses it (no request).
    status:
      status === null || status === 'failed' ? null : (status as StatusFilter),
    from: read('from'),
    to: read('to'),
  };
}

/** The URL query of `filters` (unset ones left out). */
export function paramsFor(filters: JobFilters): URLSearchParams {
  const params = new URLSearchParams();
  for (const key of KEYS) {
    const value = filters[key];
    if (value && !(key === 'status' && value === 'failed')) {
      params.set(key, value);
    }
  }
  return params;
}

export function isFiltered(filters: JobFilters): boolean {
  return KEYS.some((key) => filters[key] !== null);
}

function isLocalDate(value: string): boolean {
  try {
    parseLocalDate(value);
    return true;
  } catch {
    return false;
  }
}

/** What is wrong with a set of filters; empty when it can be sent. */
export interface FilterErrors {
  /** Not a task code (a hand-edited URL). */
  task?: 'invalid';
  /** Not a known status (a hand-edited URL). */
  status?: 'invalid';
  from?: 'invalid';
  to?: 'invalid' | 'beforeFrom';
}

export function validateFilters(filters: JobFilters): FilterErrors {
  const errors: FilterErrors = {};
  const { task, status, from, to } = filters;
  if (task !== null && (task.length > 128 || !TASK.test(task))) {
    errors.task = 'invalid';
  }
  if (status !== null && !isStatusFilter(status)) {
    errors.status = 'invalid';
  }
  const fromValid = from === null || isLocalDate(from);
  const toValid = to === null || isLocalDate(to);
  if (!fromValid) {
    errors.from = 'invalid';
  }
  if (!toValid) {
    errors.to = 'invalid';
  } else if (from !== null && to !== null && fromValid && to < from) {
    errors.to = 'beforeFrom';
  }
  return errors;
}

export function hasErrors(errors: FilterErrors): boolean {
  return Object.keys(errors).length > 0;
}
