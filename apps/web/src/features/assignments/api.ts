import type { components } from '@classora/api-client';
import type { QueryClient } from '@tanstack/react-query';
import { api, apiCall } from '@/app/api';

// The homework calls (#82, #313 A5): the working list, one assignment with
// its roster, the create, the edit (AM1: an explicit null clears due_time /
// max_score), the delete, the submission (a student's own, or work the
// tutor records), the grade, and one student's homework list and item (the
// same closed shape for every audience). Plus the reads the form needs: a
// class's attendance (its subject, start and participants) and the tutor's
// students. The API's assignment record policy decides every call.

export type AssignmentList = components['schemas']['AssignmentList'];
export type AssignmentSummary = components['schemas']['AssignmentSummary'];
export type AssignmentView = components['schemas']['AssignmentView'];
export type AssignmentCounts = components['schemas']['AssignmentCounts'];
export type AssignmentCreate = components['schemas']['AssignmentCreate'];
export type AssignmentPatch = components['schemas']['AssignmentPatch'];
export type RosterEntry = components['schemas']['RosterEntry'];
export type SubmissionPut = components['schemas']['SubmissionPut'];
export type GradePut = components['schemas']['GradePut'];
export type StudentAssignment = components['schemas']['StudentAssignment'];
export type StudentAssignmentList =
  components['schemas']['StudentAssignmentList'];
export type StudentSubmission = components['schemas']['StudentSubmission'];
export type StateSummary = components['schemas']['AssignmentStateSummary'];
export type SubmissionState = RosterEntry['state'];
export type StudentListPage =
  components['schemas']['OffsetPage_StudentListItem_'];
export type SessionAttendance = components['schemas']['SessionAttendance'];

export const SUBMISSION_STATES: readonly SubmissionState[] = [
  'pending',
  'missing',
  'submitted',
  'graded',
];

export type DueFilter = 'upcoming' | 'past' | 'all';

/** Rows per page of the lists (the API default). */
export const PAGE_SIZE = 20;
/** Students per page of the recipient picker (the API maximum). */
export const PICKER_PAGE_SIZE = 100;

/** The one query-key prefix of the homework pages (A5). */
export const ASSIGNMENTS_KEY = ['assignments'] as const;

export function workingListKey(due: DueFilter, subject: string | null) {
  return [...ASSIGNMENTS_KEY, 'working', due, subject] as const;
}

export function assignmentKey(assignmentId: string) {
  return [...ASSIGNMENTS_KEY, 'detail', assignmentId] as const;
}

export function studentListKey(studentId: string, state: string | null) {
  return [...ASSIGNMENTS_KEY, 'student', studentId, 'list', state] as const;
}

export function studentItemKey(studentId: string, assignmentId: string) {
  return [
    ...ASSIGNMENTS_KEY,
    'student',
    studentId,
    'item',
    assignmentId,
  ] as const;
}

export function pickerKey(q: string) {
  return [...ASSIGNMENTS_KEY, 'picker', q] as const;
}

export function sessionKey(sessionId: string) {
  return [...ASSIGNMENTS_KEY, 'session', sessionId] as const;
}

/** After a write: every list (working and per student) refetches. */
export function invalidateAssignmentLists(client: QueryClient): Promise<void> {
  return Promise.all([
    client.invalidateQueries({ queryKey: [...ASSIGNMENTS_KEY, 'working'] }),
    client.invalidateQueries({
      queryKey: [...ASSIGNMENTS_KEY, 'student'],
      // The item a page shows is replaced by the write itself.
      predicate: (query) => query.queryKey[3] === 'list',
    }),
  ]).then(() => undefined);
}

export function fetchWorkingList(
  due: DueFilter,
  subject: string | null,
  cursor: string | null,
  signal?: AbortSignal,
): Promise<AssignmentList> {
  return apiCall(
    () =>
      api.GET('/api/v1/assignments', {
        params: {
          query: {
            limit: PAGE_SIZE,
            ...(due !== 'all' ? { due } : {}),
            ...(subject ? { subject_id: subject } : {}),
            ...(cursor ? { cursor } : {}),
          },
        },
        signal,
      }),
    { signal },
  );
}

export function fetchAssignment(
  assignmentId: string,
  signal?: AbortSignal,
): Promise<AssignmentView> {
  return apiCall(
    () =>
      api.GET('/api/v1/assignments/{assignment_id}', {
        params: { path: { assignment_id: assignmentId } },
        signal,
      }),
    { signal },
  );
}

export function createAssignment(
  body: AssignmentCreate,
): Promise<AssignmentView> {
  return apiCall(() => api.POST('/api/v1/assignments', { body }));
}

export function patchAssignment(
  assignmentId: string,
  body: AssignmentPatch,
): Promise<AssignmentView> {
  return apiCall(() =>
    api.PATCH('/api/v1/assignments/{assignment_id}', {
      params: { path: { assignment_id: assignmentId } },
      body,
    }),
  );
}

export async function deleteAssignment(
  assignment: Pick<AssignmentView, 'id' | 'version'>,
): Promise<void> {
  await apiCall(() =>
    api.DELETE('/api/v1/assignments/{assignment_id}', {
      params: {
        path: { assignment_id: assignment.id },
        query: { version: assignment.version },
      },
    }),
  );
}

export function putSubmission(
  assignmentId: string,
  studentId: string,
  body: SubmissionPut,
): Promise<StudentAssignment> {
  return apiCall(() =>
    api.PUT(
      '/api/v1/assignments/{assignment_id}/students/{student_id}/submission',
      {
        params: {
          path: { assignment_id: assignmentId, student_id: studentId },
        },
        body,
      },
    ),
  );
}

export function putGrade(
  assignmentId: string,
  studentId: string,
  body: GradePut,
): Promise<RosterEntry> {
  return apiCall(() =>
    api.PUT('/api/v1/assignments/{assignment_id}/students/{student_id}/grade', {
      params: {
        path: { assignment_id: assignmentId, student_id: studentId },
      },
      body,
    }),
  );
}

export function fetchStudentAssignments(
  studentId: string,
  state: SubmissionState | null,
  cursor: string | null,
  signal?: AbortSignal,
): Promise<StudentAssignmentList> {
  return apiCall(
    () =>
      api.GET('/api/v1/students/{student_id}/assignments', {
        params: {
          path: { student_id: studentId },
          query: {
            limit: PAGE_SIZE,
            ...(state ? { state } : {}),
            ...(cursor ? { cursor } : {}),
          },
        },
        signal,
      }),
    { signal },
  );
}

export function fetchStudentAssignment(
  studentId: string,
  assignmentId: string,
  signal?: AbortSignal,
): Promise<StudentAssignment> {
  return apiCall(
    () =>
      api.GET('/api/v1/students/{student_id}/assignments/{assignment_id}', {
        params: {
          path: { student_id: studentId, assignment_id: assignmentId },
        },
        signal,
      }),
    { signal },
  );
}

/** The tutor's active students matching `q` (one page of up to 100). */
export function fetchPickerStudents(
  q: string,
  signal?: AbortSignal,
): Promise<StudentListPage> {
  return apiCall(
    () =>
      api.GET('/api/v1/students', {
        params: {
          query: { ...(q ? { q } : {}), limit: PICKER_PAGE_SIZE, offset: 0 },
        },
        signal,
      }),
    { signal },
  );
}

/** A class of the tutor: its state, subject, start and participants. */
export function fetchSession(
  sessionId: string,
  signal?: AbortSignal,
): Promise<SessionAttendance> {
  return apiCall(
    () =>
      api.GET('/api/v1/class-sessions/{class_session_id}/attendance', {
        params: { path: { class_session_id: sessionId } },
        signal,
      }),
    { signal },
  );
}

// ------------------------------------------------------------------ paths

const enc = encodeURIComponent;

export function assignmentsPath(): string {
  return '/assignments';
}

export function newAssignmentPath(sessionId?: string | null): string {
  return sessionId
    ? `/assignments/new?sessionId=${enc(sessionId)}`
    : '/assignments/new';
}

export function rosterPath(assignmentId: string): string {
  return `/assignments/${enc(assignmentId)}`;
}

export function editAssignmentPath(assignmentId: string): string {
  return `${rosterPath(assignmentId)}/edit`;
}

export function studentAssignmentsPath(studentId: string): string {
  return `/students/${enc(studentId)}/assignments`;
}

export function studentAssignmentPath(
  studentId: string,
  assignmentId: string,
): string {
  return `${studentAssignmentsPath(studentId)}/${enc(assignmentId)}`;
}

export function myHomeworkPath(studentId?: string | null): string {
  return studentId ? `/my-homework?student=${enc(studentId)}` : '/my-homework';
}

export function myHomeworkItemPath(
  assignmentId: string,
  studentId?: string | null,
): string {
  const base = `/my-homework/${enc(assignmentId)}`;
  return studentId ? `${base}?student=${enc(studentId)}` : base;
}
