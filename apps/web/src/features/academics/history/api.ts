import type { components } from '@classora/api-client';
import type { QueryClient } from '@tanstack/react-query';
import { api, apiCall } from '@/app/api';
import { ACADEMICS_KEY } from '../keys';

// The academic history calls (#80 D9, D10; #308 E): one student's unified
// history (final, non-voided evaluations grouped by period and subject, the
// same closed shape for staff, tutors and families) and the working list of
// evaluations (staff and tutors: drafts of the author, finals, voided ones
// for staff; cursor pages of 20). The API's record policies decide.

export type AcademicHistory = components['schemas']['AcademicHistory'];
export type HistoryEntry = components['schemas']['HistoryEntry'];
/** A scored homework grade (#82 G1; rendered since #313 F1). */
export type AssignmentGradeEntry =
  components['schemas']['AssignmentGradeEntry'];
/** An entry this view renders: an evaluation or a homework grade. */
export type KnownEntry = HistoryEntry | AssignmentGradeEntry;
export type HistorySubjectGroup = components['schemas']['HistorySubjectGroup'];
export type HistoryGroup = components['schemas']['HistoryGroup'];
export type GroupBy = components['schemas']['GroupBy'];
export type WorkingPage = components['schemas']['CursorPage_EvaluationView_'];
export type WorkingStatus = 'draft' | 'final' | 'voided';

export interface HistoryQuery {
  group: GroupBy;
  subject: string | null;
  from: string | null;
  to: string | null;
}

export const HISTORY_KEY = [...ACADEMICS_KEY, 'history'] as const;
export const WORKING_KEY = [...ACADEMICS_KEY, 'working'] as const;
export const WORKING_PAGE_SIZE = 20;
/** The family page's picker (GET /schedule/family-students). */
export const FAMILY_STUDENTS_KEY = [
  ...ACADEMICS_KEY,
  'family-students',
] as const;

export function historyKey(studentId: string, query: HistoryQuery) {
  return [...HISTORY_KEY, studentId, query] as const;
}

export function workingKey(studentId: string, status: WorkingStatus | null) {
  return [...WORKING_KEY, studentId, status ?? 'all'] as const;
}

export function fetchAcademicHistory(
  studentId: string,
  query: HistoryQuery,
  signal?: AbortSignal,
): Promise<AcademicHistory> {
  return apiCall(
    () =>
      api.GET('/api/v1/students/{student_id}/academic-history', {
        params: {
          path: { student_id: studentId },
          query: {
            group_by: query.group,
            ...(query.subject ? { subject_id: query.subject } : {}),
            ...(query.from ? { from: query.from } : {}),
            ...(query.to ? { to: query.to } : {}),
          },
        },
        signal,
      }),
    { signal },
  );
}

export function fetchWorkingList(
  studentId: string,
  status: WorkingStatus | null,
  cursor: string | null,
  signal?: AbortSignal,
): Promise<WorkingPage> {
  return apiCall(
    () =>
      api.GET('/api/v1/students/{student_id}/evaluations', {
        params: {
          path: { student_id: studentId },
          query: {
            limit: WORKING_PAGE_SIZE,
            ...(status ? { status } : {}),
            ...(cursor ? { cursor } : {}),
          },
        },
        signal,
      }),
    { signal },
  );
}

/** After an evaluation write: the histories and working lists refetch. */
export function invalidateAcademicLists(client: QueryClient): Promise<void> {
  return Promise.all([
    client.invalidateQueries({ queryKey: HISTORY_KEY }),
    client.invalidateQueries({ queryKey: WORKING_KEY }),
  ]).then(() => undefined);
}
