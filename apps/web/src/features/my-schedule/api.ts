import type { components } from '@classora/api-client';
import { api, apiCall } from '@/app/api';
import type { Instant } from '@/lib/time';

// The calls of "My schedule" (#67 E): the whose-schedule choices
// (GET /schedule/family-students), a tutor's schedule and a student's
// schedule. The API's record policies decide what each caller may read; the
// page only shows what it returns.

export type TutorScheduleItem = components['schemas']['TutorScheduleItem'];
export type TutorSchedule = components['schemas']['TutorSchedule'];
export type FamilyScheduleItem = components['schemas']['FamilyScheduleItem'];
export type FamilySchedule = components['schemas']['FamilySchedule'];
export type FamilyStudent = components['schemas']['FamilyScheduleStudent'];
export type FamilyStudents = components['schemas']['FamilyScheduleStudents'];

/** One item of either schedule, with who it is for. */
export type ScheduleEntry =
  | { audience: 'tutor'; item: TutorScheduleItem }
  | { audience: 'family'; item: FamilyScheduleItem };

export type ScheduleState = ScheduleEntry['item']['state'];

export interface RangeQuery {
  from: Instant;
  to: Instant;
}

export interface SchedulePage {
  entries: ScheduleEntry[];
  truncated: boolean;
}

export const MY_SCHEDULE_KEY = ['my-schedule'] as const;

export function fetchFamilyStudents(
  signal: AbortSignal,
): Promise<FamilyStudents> {
  return apiCall(
    () => api.GET('/api/v1/schedule/family-students', { signal }),
    { signal },
  );
}

export async function fetchTutorSchedule(
  userId: string,
  query: RangeQuery,
  signal: AbortSignal,
): Promise<SchedulePage> {
  const page = await apiCall(
    () =>
      api.GET('/api/v1/tutors/{user_id}/schedule', {
        params: { path: { user_id: userId }, query },
        signal,
      }),
    { signal },
  );
  return {
    entries: page.items.map((item) => ({ audience: 'tutor', item })),
    truncated: page.truncated,
  };
}

export async function fetchFamilySchedule(
  studentId: string,
  query: RangeQuery,
  signal: AbortSignal,
): Promise<SchedulePage> {
  const page = await apiCall(
    () =>
      api.GET('/api/v1/students/{student_id}/schedule', {
        params: { path: { student_id: studentId }, query },
        signal,
      }),
    { signal },
  );
  return {
    entries: page.items.map((item) => ({ audience: 'family', item })),
    truncated: page.truncated,
  };
}

/** The loaders the page needs (stubs in stories and tests). */
export interface MyScheduleLoaders {
  fetchFamilyStudents: typeof fetchFamilyStudents;
  fetchTutorSchedule: typeof fetchTutorSchedule;
  fetchFamilySchedule: typeof fetchFamilySchedule;
}

export const API_LOADERS: MyScheduleLoaders = {
  fetchFamilyStudents,
  fetchTutorSchedule,
  fetchFamilySchedule,
};
