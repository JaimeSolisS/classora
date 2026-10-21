import type { components } from '@classora/api-client';
import { api, apiCall, ApiError } from '@/app/api';

// The calls of "Schedule a session" (#48 F): the create, and the options of
// the form (active subjects, tutors and classrooms; the student search).

export type ClassSessionCreate = components['schemas']['ClassSessionCreate'];
export type ClassSessionCreated = components['schemas']['ClassSessionCreated'];
export type SchedulingConflict = components['schemas']['SchedulingConflict'];

/** One option of a select. */
export interface Option {
  id: string;
  name: string;
}

/** A subject option, with its level for the grouping. */
export interface SubjectOption extends Option {
  levelName: string;
}

/** The most options a select loads (one page). */
export const OPTIONS_LIMIT = 100;

export const SCHEDULE_OPTIONS_KEY = [
  'scheduling',
  'create',
  'options',
] as const;

export function createClassSession(
  body: ClassSessionCreate,
): Promise<ClassSessionCreated> {
  return apiCall(() => api.POST('/api/v1/class-sessions', { body }));
}

/** The conflicts of a `409 scheduling_conflict`, or null for any other error. */
export function conflictsOf(error: unknown): SchedulingConflict[] | null {
  if (!(error instanceof ApiError) || error.code !== 'scheduling_conflict') {
    return null;
  }
  const conflicts = error.problem?.conflicts;
  return Array.isArray(conflicts) ? conflicts : [];
}

/** Active students matching `q` (the picker shows up to 10). */
export async function searchStudents(
  q: string,
  signal: AbortSignal,
): Promise<Option[]> {
  const page = await apiCall(
    () =>
      api.GET('/api/v1/students', {
        params: { query: { q, status: 'active', limit: 10 } },
        signal,
      }),
    { signal },
  );
  return page.items
    .filter((student) => student.is_active)
    .map((student) => ({
      id: student.id,
      name: `${student.given_names} ${student.family_names}`,
    }));
}

/** The active subjects (one page), by level name, then name. */
export async function fetchSubjectOptions(
  signal: AbortSignal,
): Promise<SubjectOption[]> {
  const page = await apiCall(
    () =>
      api.GET('/api/v1/subjects', {
        params: { query: { status: 'active', limit: OPTIONS_LIMIT } },
        signal,
      }),
    { signal },
  );
  return page.items
    .filter((subject) => subject.is_active)
    .map((subject) => ({
      id: subject.id,
      name: subject.name,
      levelName: subject.education_level_name,
    }));
}

/** The active tutors (one page), by display name. */
export async function fetchTutorOptions(
  signal: AbortSignal,
): Promise<Option[]> {
  const page = await apiCall(
    () =>
      api.GET('/api/v1/tutors', {
        params: { query: { state: 'active', limit: OPTIONS_LIMIT } },
        signal,
      }),
    { signal },
  );
  return page.items
    .filter((tutor) => tutor.state === 'active')
    .map((tutor) => ({ id: tutor.user_id, name: tutor.display_name }));
}

/** The active classrooms (one page). */
export async function fetchClassroomOptions(
  signal: AbortSignal,
): Promise<Option[]> {
  const page = await apiCall(
    () =>
      api.GET('/api/v1/classrooms', {
        params: { query: { status: 'active', limit: OPTIONS_LIMIT } },
        signal,
      }),
    { signal },
  );
  return page.items
    .filter((room) => room.is_active)
    .map((room) => ({ id: room.id, name: room.name }));
}
