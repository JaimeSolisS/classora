import type { components } from '@classora/api-client';
import { api, apiCall } from '@/app/api';
import type { ReportBasis } from '../api';

// The KPI reads (#126, M8-09b; definitions in _docs/kpis.md). Read-only: no
// Idempotency-Key. K3 is the financial report's balances route (#117).

export type RevenueTrend = components['schemas']['RevenueTrend'];
export type TrendMonth = components['schemas']['TrendMonth'];
export type TrendFigures = components['schemas']['TrendFigures'];
export type CourseProfitability = components['schemas']['CourseProfitability'];
export type CohortProfitability = components['schemas']['CohortProfitability'];
export type CourseFigures = components['schemas']['CourseFigures'];
export type RoomUtilization = components['schemas']['RoomUtilization'];
export type RoomUtilizationRow = components['schemas']['RoomUtilizationRow'];
export type StudentRetention = components['schemas']['StudentRetention'];
export type ActiveMonth = components['schemas']['ActiveMonth'];
export type TutorPerformance = components['schemas']['TutorPerformance'];
export type TutorPerformanceRow = components['schemas']['TutorPerformanceRow'];
export type ActivityFigures = components['schemas']['ActivityFigures'];

/** A KPI's range: center-local dates, both included. */
export interface KpiRange {
  from: string;
  to: string;
}

export interface TrendParams extends KpiRange {
  basis: ReportBasis;
}

/** Every cached KPI query (the range and basis are in each key, #458). */
export const KPIS_KEY = ['reports', 'kpis'] as const;

export const kpiKeys = {
  revenueTrend: (params: TrendParams) =>
    [...KPIS_KEY, 'revenue-trend', params] as const,
  courseProfitability: (range: KpiRange) =>
    [...KPIS_KEY, 'course-profitability', range] as const,
  roomUtilization: (range: KpiRange) =>
    [...KPIS_KEY, 'room-utilization', range] as const,
  studentRetention: (range: KpiRange) =>
    [...KPIS_KEY, 'student-retention', range] as const,
  tutorPerformance: (range: KpiRange) =>
    [...KPIS_KEY, 'tutor-performance', range] as const,
};

export function fetchRevenueTrend(
  { basis, from, to }: TrendParams,
  signal: AbortSignal,
): Promise<RevenueTrend> {
  return apiCall(
    () =>
      api.GET('/api/v1/reports/kpis/revenue-trend', {
        params: { query: { basis, from, to } },
        signal,
      }),
    { signal },
  );
}

export function fetchCourseProfitability(
  { from, to }: KpiRange,
  signal: AbortSignal,
): Promise<CourseProfitability> {
  return apiCall(
    () =>
      api.GET('/api/v1/reports/kpis/course-profitability', {
        params: { query: { from, to } },
        signal,
      }),
    { signal },
  );
}

export function fetchRoomUtilization(
  { from, to }: KpiRange,
  signal: AbortSignal,
): Promise<RoomUtilization> {
  return apiCall(
    () =>
      api.GET('/api/v1/reports/kpis/room-utilization', {
        params: { query: { from, to } },
        signal,
      }),
    { signal },
  );
}

export function fetchStudentRetention(
  { from, to }: KpiRange,
  signal: AbortSignal,
): Promise<StudentRetention> {
  return apiCall(
    () =>
      api.GET('/api/v1/reports/kpis/student-retention', {
        params: { query: { from, to } },
        signal,
      }),
    { signal },
  );
}

export function fetchTutorPerformance(
  { from, to }: KpiRange,
  signal: AbortSignal,
): Promise<TutorPerformance> {
  return apiCall(
    () =>
      api.GET('/api/v1/reports/kpis/tutor-performance', {
        params: { query: { from, to } },
        signal,
      }),
    { signal },
  );
}
