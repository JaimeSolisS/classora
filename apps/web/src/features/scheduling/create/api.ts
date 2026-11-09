import type { components } from '@classora/api-client';
import { api, apiCall, ApiError } from '@/app/api';
import type { OptionsPage } from '@/components/ui/combobox';
import { fetchStudentOptions } from '@/features/students/api';

// The calls of "Schedule a session" (#48 F): the create, the suggested
// times after a conflict (#50 C1), the suggested tutors (#51 G), and the options of the form (active
// subjects, tutors and classrooms; the student search).

export type ClassSessionCreate = components['schemas']['ClassSessionCreate'];
export type ClassSessionCreated = components['schemas']['ClassSessionCreated'];
export type SchedulingConflict = components['schemas']['SchedulingConflict'];
export type SchedulingWarning = components['schemas']['SchedulingWarning'];
export type SlotSuggestionRequest =
  components['schemas']['SlotSuggestionRequest'];
export type SlotSuggestions = components['schemas']['SlotSuggestions'];
export type SuggestedSlot = components['schemas']['SuggestedSlot'];
export type TutorSuggestionList = components['schemas']['TutorSuggestionList'];
export type TutorSuggestion = components['schemas']['TutorSuggestion'];
export type SeriesTutorSuggestionQuery =
  components['schemas']['SeriesTutorSuggestionQuery'];
export type SeriesTutorSuggestionList =
  components['schemas']['SeriesTutorSuggestionList'];

/** The query of GET /api/v1/tutor-suggestions (#51 D1). */
export interface TutorSuggestionParams {
  subject_id: string;
  starts_at: string;
  ends_at: string;
  classroom_id?: string;
  /** At most this many items (1-50; the API's default is 10). */
  limit?: number;
}

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

/** Free times for the same session (read-only; the default window, #50 C1). */
export function suggestSlots(
  body: SlotSuggestionRequest,
): Promise<SlotSuggestions> {
  return apiCall(() =>
    api.POST('/api/v1/class-sessions/suggestions', { body }),
  );
}

/** The eligible tutors for a new slot, lightest week first (#51 G1). */
export function suggestTutors(
  query: TutorSuggestionParams,
  signal?: AbortSignal,
): Promise<TutorSuggestionList> {
  return apiCall(
    () => api.GET('/api/v1/tutor-suggestions', { params: { query }, signal }),
    signal,
  );
}

/**
 * The tutors free and available for every occurrence of a weekly series,
 * ranked at the first one (HF-05 C; read-only).
 */
export function suggestSeriesTutors(
  body: SeriesTutorSuggestionQuery,
  signal?: AbortSignal,
): Promise<SeriesTutorSuggestionList> {
  return apiCall(
    () => api.POST('/api/v1/tutor-suggestions/series', { body, signal }),
    signal,
  );
}

/** The conflicts of a `409 scheduling_conflict`, or null for any other error. */
export function conflictsOf(error: unknown): SchedulingConflict[] | null {
  if (!(error instanceof ApiError) || error.code !== 'scheduling_conflict') {
    return null;
  }
  const conflicts = error.problem?.conflicts;
  return Array.isArray(conflicts) ? conflicts : [];
}

/**
 * The student combobox's options (#545): the active students, or the
 * matches for `q` (S1–S3). Keeps dropping a row the API marks inactive.
 */
export async function searchStudents(
  q: string | null,
  signal: AbortSignal,
): Promise<OptionsPage<Option>> {
  const page = await fetchStudentOptions({ q, status: 'active', signal });
  const items = page.items
    .filter((student) => student.is_active)
    .map((student) => ({
      id: student.id,
      name: `${student.given_names} ${student.family_names}`,
    }));
  return {
    items,
    total: page.total - (page.items.length - items.length),
  };
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
