import type { components } from '@classora/api-client';
import type { QueryClient } from '@tanstack/react-query';
import { api, apiCall } from '@/app/api';
import { APPROVALS_QUERY_KEY } from '@/features/approvals/api';

// The progress report calls (#83, #315 A6): the working list and one report
// (R2, R3), the create, the narrative PATCH, the delete, the submit and the
// three decisions (each with the version the page read), and the approved
// reports of a student (R10, with the create hints of AM2) and one of them
// (R11). Query keys share one prefix.

export type ProgressReport = components['schemas']['ProgressReportView'];
export type ProgressReportPage = components['schemas']['ProgressReportPage'];
export type ProgressReportListItem =
  components['schemas']['ProgressReportListItem'];
export type ApprovedReportPage =
  components['schemas']['ProgressReportFamilyPage'];
export type ApprovedReportItem =
  components['schemas']['ProgressReportFamilyListItem'];
export type ApprovedReport = components['schemas']['ProgressReportFamilyView'];
export type ReportFacts = components['schemas']['ProgressReportFacts'];
export type ReportCreate = components['schemas']['ProgressReportCreate'];
export type ReportUpdate = components['schemas']['ProgressReportUpdate'];
export type ReportSubject = components['schemas']['ProgressReportSubject'];
export type ReportKind = components['schemas']['ReportKind'];
export type ReportStatus = components['schemas']['ReportStatus'];

export const REPORT_KINDS: readonly ReportKind[] = [
  'weekly',
  'monthly',
  'on_demand',
];

export const REPORT_STATUSES: readonly ReportStatus[] = [
  'draft',
  'submitted',
  'changes_requested',
  'approved_for_family',
  'retracted',
];

/** One page of a list (the API's maximum is 50; the pages ask for 20). */
export const PAGE_SIZE = 20;

/** The one query-key prefix of the progress report pages (A6). */
export const PROGRESS_REPORTS_KEY = ['progress-reports'] as const;

export interface WorkingFilters {
  status: ReportStatus | null;
  kind: ReportKind | null;
  studentId: string | null;
  mine: boolean;
}

export function workingListKey(filters: WorkingFilters) {
  return [...PROGRESS_REPORTS_KEY, 'working', filters] as const;
}

export function reportKey(reportId: string) {
  return [...PROGRESS_REPORTS_KEY, 'report', reportId] as const;
}

export function approvedListKey(studentId: string) {
  return [...PROGRESS_REPORTS_KEY, 'approved', studentId] as const;
}

/** The create hints of R10 (its first item only, AM2). */
export function createHintsKey(studentId: string) {
  return [...PROGRESS_REPORTS_KEY, 'hints', studentId] as const;
}

export function approvedReportKey(studentId: string, reportId: string) {
  return [...PROGRESS_REPORTS_KEY, 'approved-report', studentId, reportId];
}

/** After a write: every list refetches, and the approval inbox too. */
export async function invalidateReportLists(
  client: QueryClient,
): Promise<void> {
  await Promise.all([
    client.invalidateQueries({
      queryKey: [...PROGRESS_REPORTS_KEY, 'working'],
    }),
    client.invalidateQueries({
      queryKey: [...PROGRESS_REPORTS_KEY, 'approved'],
    }),
    client.invalidateQueries({ queryKey: [...PROGRESS_REPORTS_KEY, 'hints'] }),
    client.invalidateQueries({ queryKey: APPROVALS_QUERY_KEY }),
  ]);
}

export function fetchWorkingList(
  filters: WorkingFilters,
  cursor: string | null,
  signal?: AbortSignal,
): Promise<ProgressReportPage> {
  return apiCall(
    () =>
      api.GET('/api/v1/progress-reports', {
        params: {
          query: {
            ...(filters.status ? { status: filters.status } : {}),
            ...(filters.kind ? { kind: filters.kind } : {}),
            ...(filters.studentId ? { student_id: filters.studentId } : {}),
            ...(filters.mine ? { mine: true } : {}),
            ...(cursor ? { cursor } : {}),
            limit: PAGE_SIZE,
          },
        },
        signal,
      }),
    { signal },
  );
}

export function fetchReport(
  reportId: string,
  signal?: AbortSignal,
): Promise<ProgressReport> {
  return apiCall(
    () =>
      api.GET('/api/v1/progress-reports/{report_id}', {
        params: { path: { report_id: reportId } },
        signal,
      }),
    { signal },
  );
}

export function fetchApprovedList(
  studentId: string,
  cursor: string | null,
  limit: number,
  signal?: AbortSignal,
): Promise<ApprovedReportPage> {
  return apiCall(
    () =>
      api.GET('/api/v1/students/{student_id}/progress-reports', {
        params: {
          path: { student_id: studentId },
          query: { ...(cursor ? { cursor } : {}), limit },
        },
        signal,
      }),
    { signal },
  );
}

export function fetchApprovedReport(
  studentId: string,
  reportId: string,
  signal?: AbortSignal,
): Promise<ApprovedReport> {
  return apiCall(
    () =>
      api.GET('/api/v1/students/{student_id}/progress-reports/{report_id}', {
        params: { path: { student_id: studentId, report_id: reportId } },
        signal,
      }),
    { signal },
  );
}

export function createReport(body: ReportCreate): Promise<ProgressReport> {
  return apiCall(() => api.POST('/api/v1/progress-reports', { body }));
}

export function updateReport(
  reportId: string,
  body: ReportUpdate,
): Promise<ProgressReport> {
  return apiCall(() =>
    api.PATCH('/api/v1/progress-reports/{report_id}', {
      params: { path: { report_id: reportId } },
      body,
    }),
  );
}

export async function deleteReport(
  report: Pick<ProgressReport, 'id' | 'version'>,
): Promise<void> {
  await apiCall(() =>
    api.DELETE('/api/v1/progress-reports/{report_id}', {
      params: {
        path: { report_id: report.id },
        query: { version: report.version },
      },
    }),
  );
}

export function submitReport(
  report: Pick<ProgressReport, 'id' | 'version'>,
): Promise<ProgressReport> {
  return apiCall(() =>
    api.POST('/api/v1/progress-reports/{report_id}/submit', {
      params: { path: { report_id: report.id } },
      body: { version: report.version },
    }),
  );
}

export function approveReport(
  report: Pick<ProgressReport, 'id' | 'version'>,
): Promise<ProgressReport> {
  return apiCall(() =>
    api.POST('/api/v1/progress-reports/{report_id}/approve', {
      params: { path: { report_id: report.id } },
      body: { version: report.version },
    }),
  );
}

export function requestReportChanges(
  report: Pick<ProgressReport, 'id' | 'version'>,
  reason: string,
): Promise<ProgressReport> {
  return apiCall(() =>
    api.POST('/api/v1/progress-reports/{report_id}/request-changes', {
      params: { path: { report_id: report.id } },
      body: { version: report.version, reason },
    }),
  );
}

export function retractReport(
  report: Pick<ProgressReport, 'id' | 'version'>,
  reason: string,
): Promise<ProgressReport> {
  return apiCall(() =>
    api.POST('/api/v1/progress-reports/{report_id}/retract', {
      params: { path: { report_id: report.id } },
      body: { version: report.version, reason },
    }),
  );
}

// ------------------------------------------------------------------ paths

export function reportsPath(): string {
  return '/progress-reports';
}

export function reportPath(reportId: string): string {
  return `/progress-reports/${encodeURIComponent(reportId)}`;
}

export function studentReportsPath(studentId: string): string {
  return `/students/${encodeURIComponent(studentId)}/progress-reports`;
}

export function newReportPath(studentId: string): string {
  return `${studentReportsPath(studentId)}/new`;
}

export function studentReportPath(studentId: string, reportId: string): string {
  return `${studentReportsPath(studentId)}/${encodeURIComponent(reportId)}`;
}

export function myReportsPath(studentId?: string | null): string {
  return studentId
    ? `/my-progress-reports?student=${encodeURIComponent(studentId)}`
    : '/my-progress-reports';
}

export function myReportPath(
  reportId: string,
  studentId?: string | null,
): string {
  const path = `/my-progress-reports/${encodeURIComponent(reportId)}`;
  return studentId ? `${path}?student=${encodeURIComponent(studentId)}` : path;
}
