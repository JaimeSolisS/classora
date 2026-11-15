import type { components } from '@classora/api-client';
import { api, apiCall } from '@/app/api';

// The attendance calls (#76): the session's attendance (the current tutor or
// a schedule.view holder; anyone else 404), the tutor's marks and a staff
// correction (attendance.correct). The API's record policy decides; the
// pages only show what it returns. The staff session page reuses them.

export type SessionAttendance = components['schemas']['SessionAttendance'];
export type AttendanceItem = components['schemas']['AttendanceItem'];
export type AttendanceMark = components['schemas']['AttendanceMark'];
export type AttendanceMarks = components['schemas']['AttendanceMarks'];
export type AttendanceCorrectionCreate =
  components['schemas']['AttendanceCorrectionCreate'];
export type AttendanceCorrection =
  components['schemas']['AttendanceCorrectionItem'];
export type AttendanceStatus = AttendanceMark['status'];
export type AttendanceBlock = NonNullable<SessionAttendance['block']>;

export const ATTENDANCE_STATUSES: readonly AttendanceStatus[] = [
  'present',
  'late',
  'absent',
];

export const ATTENDANCE_KEY = ['attendance'] as const;

export function attendanceKey(sessionId: string) {
  return [...ATTENDANCE_KEY, sessionId] as const;
}

export function fetchAttendance(
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

export function markAttendance(
  sessionId: string,
  body: AttendanceMarks,
): Promise<SessionAttendance> {
  return apiCall(() =>
    api.PUT('/api/v1/class-sessions/{class_session_id}/attendance', {
      params: { path: { class_session_id: sessionId } },
      body,
    }),
  );
}

export function correctAttendance(
  sessionId: string,
  body: AttendanceCorrectionCreate,
): Promise<SessionAttendance> {
  return apiCall(() =>
    api.POST(
      '/api/v1/class-sessions/{class_session_id}/attendance/corrections',
      {
        params: { path: { class_session_id: sessionId } },
        body,
      },
    ),
  );
}

/** The tutor's attendance page of a session (G1). */
export function attendancePath(sessionId: string): string {
  return `/my-schedule/sessions/${sessionId}/attendance`;
}
