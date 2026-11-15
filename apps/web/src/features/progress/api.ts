import type { components } from '@classora/api-client';
import type { ExpectedErrorsMeta } from '@/app/queryClient';
import { api, apiCall } from '@/app/api';

// The progress dashboard calls (#322 B1; the #84 contract): a student's
// progress for a range (attendance, grades by month and subject, homework,
// learning plans, and the subjects of the unfiltered range, AM1) and the
// class log (cursor pages of 20, newest first), both with the page's range
// and subject. The API's record policy decides who reads what.

export type StudentProgress = components['schemas']['StudentProgress'];
export type ProgressSubject = components['schemas']['ProgressSubject'];
export type ProgressAttendanceMonth =
  components['schemas']['ProgressAttendanceMonth'];
export type ProgressGradeMonth = components['schemas']['ProgressGradeMonth'];
export type ProgressPlanItem = components['schemas']['ProgressPlanItem'];
export type SessionLogPage = components['schemas']['SessionLog'];
export type SessionLogItem = components['schemas']['SessionLogItem'];
export type SessionLogMark = components['schemas']['SessionLogMark'];
export type SessionLogKind = components['schemas']['SessionLogKind'];

/** The range and subject both reads take (center-local dates). */
export interface ProgressQuery {
  from: string;
  to: string;
  subject: string | null;
}

export const PROGRESS_KEY = ['progress'] as const;
export const LOG_PAGE_SIZE = 20;

export function progressKey(studentId: string, query: ProgressQuery) {
  return [...PROGRESS_KEY, 'summary', studentId, query] as const;
}

export function sessionLogKey(studentId: string, query: ProgressQuery) {
  return [...PROGRESS_KEY, 'log', studentId, query] as const;
}

/** The page shows these answers inline as designed states (#315 QA D1):
 * the query cache doesn't report them. Staff and tutors: a 404 (a former
 * tutor's progress, or no access) and the 422 range or subject refusals. */
export const STAFF_EXPECTED_META = {
  expectedStatuses: [404, 422],
} as const satisfies ExpectedErrorsMeta;

/** Families also see a 403 student_consent_required inline. */
export const FAMILY_EXPECTED_META = {
  expectedStatuses: [403, 404, 422],
} as const satisfies ExpectedErrorsMeta;

function rangeParams(query: ProgressQuery) {
  return {
    from: query.from,
    to: query.to,
    ...(query.subject ? { subject_id: query.subject } : {}),
  };
}

export function fetchStudentProgress(
  studentId: string,
  query: ProgressQuery,
  signal?: AbortSignal,
): Promise<StudentProgress> {
  return apiCall(
    () =>
      api.GET('/api/v1/students/{student_id}/progress', {
        params: {
          path: { student_id: studentId },
          query: rangeParams(query),
        },
        signal,
      }),
    { signal },
  );
}

export function fetchSessionLog(
  studentId: string,
  query: ProgressQuery,
  cursor: string | null,
  signal?: AbortSignal,
): Promise<SessionLogPage> {
  return apiCall(
    () =>
      api.GET('/api/v1/students/{student_id}/session-log', {
        params: {
          path: { student_id: studentId },
          query: {
            ...rangeParams(query),
            limit: LOG_PAGE_SIZE,
            ...(cursor ? { cursor } : {}),
          },
        },
        signal,
      }),
    { signal },
  );
}
