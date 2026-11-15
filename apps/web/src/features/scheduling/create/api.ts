import type { components } from '@classora/api-client';
import { api, apiCall, ApiError } from '@/app/api';
import { COMBOBOX_PAGE_SIZE, type OptionsPage } from '@/components/ui/combobox';
import { fetchCohort } from '@/features/courses/cohorts/api';
import { fetchGroup } from '@/features/groups/api';
import { fetchStudentOptions } from '@/features/students/api';
import type { OwnerKind, OwnerOption, OwnerSummary } from './rules';

// The calls of "Schedule a session" (#48 F): the create, the suggested
// times after a conflict (#50 C1), the suggested tutors (#51 G), and the options of the form (active
// subjects, tutors and classrooms; the student search). A group or course
// session (HF-11b): the active groups and current cohorts, and the chosen
// one's subject, dates and roster.

export type ClassSessionCreate = components['schemas']['ClassSessionCreate'];
export type GroupSessionCreate = components['schemas']['GroupSessionCreate'];
export type CourseSessionCreate = components['schemas']['CourseSessionCreate'];
/** The create's body: an individual, group or course session (#592). */
export type ClassSessionCreateBody =
  ClassSessionCreate | GroupSessionCreate | CourseSessionCreate;
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
  /** The built-in General subject (HF-12): preselected on new sessions and
   * groups. */
  isGeneral?: boolean;
}

/** The most options a select loads (one page). */
export const OPTIONS_LIMIT = 100;

export const SCHEDULE_OPTIONS_KEY = [
  'scheduling',
  'create',
  'options',
] as const;

export function createClassSession(
  body: ClassSessionCreateBody,
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
      isGeneral: subject.is_general,
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

/** A cursor page as an options page: one more than loaded when there are more. */
function cursorTotal(page: { items: unknown[]; next_cursor?: string | null }) {
  return page.items.length + (page.next_cursor ? 1 : 0);
}

/** The group picker's options (HF-11b): the active groups, or the matches for `q`. */
export async function searchGroupOptions(
  q: string | null,
  signal: AbortSignal,
): Promise<OptionsPage<OwnerOption>> {
  const text = q?.trim() ?? '';
  const page = await apiCall(
    () =>
      api.GET('/api/v1/groups', {
        params: {
          query: {
            ...(text ? { q: text } : {}),
            status: 'active',
            limit: COMBOBOX_PAGE_SIZE,
          },
        },
        signal,
      }),
    { signal },
  );
  return {
    items: page.items.map((group) => ({
      id: group.id,
      name: group.name,
      subjectName: group.subject.name,
    })),
    total: cursorTotal(page),
  };
}

/** The cohort picker's options (HF-11b): the current cohorts, or the matches for `q`. */
export async function searchCohortOptions(
  q: string | null,
  signal: AbortSignal,
): Promise<OptionsPage<OwnerOption>> {
  const text = q?.trim() ?? '';
  const page = await apiCall(
    () =>
      api.GET('/api/v1/cohorts', {
        params: {
          query: {
            ...(text ? { q: text } : {}),
            status: 'current',
            limit: COMBOBOX_PAGE_SIZE,
          },
        },
        signal,
      }),
    { signal },
  );
  return {
    items: page.items.map((cohort) => ({
      id: cohort.id,
      name: cohort.name,
      subjectName: cohort.subject.name,
    })),
    total: cursorTotal(page),
  };
}

type RosterItem = {
  student: { id: string; display_name: string };
  withdrawn_at: string | null;
};

/** The students enrolled now, by name. */
function enrolledStudents(roster: readonly RosterItem[] | null | undefined) {
  return (roster ?? [])
    .filter((item) => item.withdrawn_at === null)
    .map((item) => ({ id: item.student.id, name: item.student.display_name }))
    .sort((a, b) => a.name.localeCompare(b.name));
}

/** The chosen group or cohort: its subject, a cohort's dates, the roster. */
export async function fetchOwnerSummary(
  kind: OwnerKind,
  id: string,
  signal: AbortSignal,
): Promise<OwnerSummary> {
  if (kind === 'group') {
    const group = await fetchGroup(id, signal);
    return {
      id: group.id,
      name: group.name,
      subject: {
        id: group.subject.id,
        name: group.subject.name,
        levelName: group.subject.level.name,
      },
      firstSessionOn: null,
      lastSessionOn: null,
      students: enrolledStudents(group.roster),
    };
  }
  const cohort = await fetchCohort(id, signal);
  return {
    id: cohort.id,
    name: cohort.name,
    subject: {
      id: cohort.subject.id,
      name: cohort.subject.name,
      levelName: cohort.subject.level.name,
    },
    firstSessionOn: cohort.first_session_on ?? null,
    lastSessionOn: cohort.last_session_on ?? null,
    students: enrolledStudents(cohort.roster),
  };
}
