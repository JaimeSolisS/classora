import { ApiError } from '@/app/api';
import type { ReportState } from './parts';
import type { RangeField } from './rules';

// A report query's state for the views (M7-14b B6), and the server's range
// refusal (`422 report_range_invalid`, shown next to the field it names),
// shared by the financial report and the KPI page (M8-09b).

const RANGE_INVALID = 'report_range_invalid';

export function isRangeRefusal(error: unknown): error is ApiError {
  return error instanceof ApiError && error.code === RANGE_INVALID;
}

/** The field a `422 report_range_invalid` names (`to` if it names none). */
export function refusedField(error: ApiError): RangeField {
  const field = error.fieldErrors.find((item) => item.code === RANGE_INVALID)
    ?.path[1];
  return field === 'from' ? 'from' : 'to';
}

export function stateOf<T>(
  query: {
    data: T | undefined;
    isError: boolean;
    error: unknown;
    isFetching: boolean;
    refetch: () => unknown;
  },
  idle = false,
): ReportState<T> {
  if (idle) return { status: 'idle' };
  if (query.data !== undefined) {
    return {
      status: 'ready',
      data: query.data,
      refreshError: query.isError ? query.error : null,
      refreshing: query.isFetching,
      onRefresh: () => void query.refetch(),
    };
  }
  if (query.isError) {
    return {
      status: 'error',
      error: query.error,
      onRetry: () => void query.refetch(),
    };
  }
  return { status: 'loading' };
}
