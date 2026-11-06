import { ApiError } from '@/app/api';
import type { ReportState } from '../parts';
import { isRangeRefusal, stateOf } from '../reportState';

// A KPI section's state (M8-09b C7). On top of the report states: a `403`
// hides the section (a stale permission cache; the API checks every
// request), and a `422 report_range_invalid` asks for nothing to show (the
// page shows the error next to the field it names).

export type KpiState<T> = ReportState<T> | { status: 'hidden' };

export function kpiState<T>(query: {
  data: T | undefined;
  isError: boolean;
  error: unknown;
  isFetching: boolean;
  refetch: () => unknown;
}): KpiState<T> {
  if (query.error instanceof ApiError && query.error.status === 403) {
    return { status: 'hidden' };
  }
  return stateOf(query, isRangeRefusal(query.error));
}
