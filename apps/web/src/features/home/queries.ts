import { useQueries, useQuery } from '@tanstack/react-query';
import { calendarQueryKey, fetchCalendar } from '@/features/calendar/api';
import {
  fetchFamilySchedule,
  fetchFamilyStudents,
  fetchTutorSchedule,
  MY_SCHEDULE_KEY,
  type FamilyStudent,
} from '@/features/my-schedule/api';
import { TEACHING } from '@/features/my-schedule/whose';
import type { Window } from './rules';

// The agenda reads of Home (HF-06 B1–B3), shared by the agenda sections
// and the pending items: one request per key, so the "needs attention"
// and "to close out" counts reuse the agenda's own response (C1). The keys
// are the calendar's and My schedule's (D6).

/** B1: the center's sessions, cancelled excluded by the API default. */
export function useStaffSessions(window: Window, enabled: boolean) {
  const query = { from: window.from, to: window.to };
  return useQuery({
    queryKey: calendarQueryKey(query),
    queryFn: ({ signal }) => fetchCalendar(query, signal),
    enabled,
  });
}

/** B2: the tutor's own schedule (the My schedule "teaching" key). */
export function useTutorSchedule(
  userId: string,
  window: Window,
  enabled: boolean,
) {
  const query = { from: window.from, to: window.to };
  return useQuery({
    queryKey: [...MY_SCHEDULE_KEY, 'schedule', TEACHING, query],
    queryFn: ({ signal }) => fetchTutorSchedule(userId, query, signal),
    enabled,
  });
}

/** B3: whose schedules a student or guardian may read. */
export function useFamilyStudents(enabled: boolean) {
  return useQuery({
    queryKey: [...MY_SCHEDULE_KEY, 'family-students'],
    queryFn: ({ signal }) => fetchFamilyStudents(signal),
    enabled,
  });
}

/** B3: the students Home reads (access allowed, at most `max`). */
export function readableStudents(
  students: readonly FamilyStudent[],
  max: number,
): FamilyStudent[] {
  return students
    .filter((student) => student.schedule_access === 'allowed')
    .slice(0, max);
}

/** B3: one schedule read per readable student (never a consent one). */
export function useFamilySchedules(
  students: readonly FamilyStudent[],
  window: Window,
) {
  const query = { from: window.from, to: window.to };
  return useQueries({
    queries: students.map((student) => ({
      queryKey: [...MY_SCHEDULE_KEY, 'schedule', student.student_id, query],
      queryFn: ({ signal }: { signal: AbortSignal }) =>
        fetchFamilySchedule(student.student_id, query, signal),
    })),
  });
}
