import { useQuery } from '@tanstack/react-query';
import { BALANCES_KEY, fetchBalances } from '../api';
import {
  fetchCourseProfitability,
  fetchRevenueTrend,
  fetchRoomUtilization,
  fetchStudentRetention,
  fetchTutorPerformance,
  kpiKeys,
  type KpiRange,
  type TrendParams,
} from './api';

// One query hook per KPI section (M8-09b D1). The page and the sections
// (and #127's widgets) call the same hooks, so they share one request per
// key. A 403 (a stale permission cache) and a 422 report_range_invalid are
// expected: the section hides or the page shows the field error.

const EXPECTED = { expectedStatuses: [403, 422] } as const;

export function useRevenueTrend(params: TrendParams, enabled = true) {
  return useQuery({
    queryKey: kpiKeys.revenueTrend(params),
    queryFn: ({ signal }) => fetchRevenueTrend(params, signal),
    enabled,
    meta: EXPECTED,
  });
}

export function useCourseProfitability(range: KpiRange, enabled = true) {
  return useQuery({
    queryKey: kpiKeys.courseProfitability(range),
    queryFn: ({ signal }) => fetchCourseProfitability(range, signal),
    enabled,
    meta: EXPECTED,
  });
}

export function useRoomUtilization(range: KpiRange, enabled = true) {
  return useQuery({
    queryKey: kpiKeys.roomUtilization(range),
    queryFn: ({ signal }) => fetchRoomUtilization(range, signal),
    enabled,
    meta: EXPECTED,
  });
}

export function useStudentRetention(range: KpiRange, enabled = true) {
  return useQuery({
    queryKey: kpiKeys.studentRetention(range),
    queryFn: ({ signal }) => fetchStudentRetention(range, signal),
    enabled,
    meta: EXPECTED,
  });
}

export function useTutorPerformance(range: KpiRange, enabled = true) {
  return useQuery({
    queryKey: kpiKeys.tutorPerformance(range),
    queryFn: ({ signal }) => fetchTutorPerformance(range, signal),
    enabled,
    meta: EXPECTED,
  });
}

/** K3: today's balances (#117), the financial report's query. */
export function useKpiBalances(enabled = true) {
  return useQuery({
    queryKey: BALANCES_KEY,
    queryFn: ({ signal }) => fetchBalances(signal),
    enabled,
    meta: { expectedStatuses: [403] },
  });
}
