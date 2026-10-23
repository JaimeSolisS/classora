import type { components } from '@classora/api-client';
import { api, apiCall } from '@/app/api';

// Session requests by families (#59 F): the available times of a child (or
// the student themselves), the request, the caller's requests and the
// withdrawal. The API decides who may act for which student; the pages only
// show what it returns.

export type AvailableSlots = components['schemas']['AvailableSlots'];
export type AvailableDay = components['schemas']['AvailableDay'];
export type AvailableSlot = components['schemas']['AvailableSlot'];
export type SessionRequestDetail =
  components['schemas']['SessionRequestDetail'];
export type SessionRequestItem = components['schemas']['SessionRequestItem'];
/** The #21 offset page of the caller's requests. */
export type SessionRequestPage =
  components['schemas']['OffsetPage_SessionRequestItem_'];
export type SessionRequestStatus = SessionRequestDetail['status'];
export type RequestDuration = 30 | 45 | 60 | 90 | 120;

/** The lengths a family may request (minutes). */
export const DURATIONS: readonly RequestDuration[] = [30, 45, 60, 90, 120];

/** The note's maximum length once trimmed. */
export const NOTE_MAX_LENGTH = 500;

/** The most options or requests one list loads (one page). */
export const LIST_LIMIT = 100;

export const REQUESTS_KEY = ['family-portal', 'session-requests'] as const;
export const SLOTS_KEY = ['family-portal', 'available-slots'] as const;
export const OPTIONS_KEY = ['family-portal', 'request-options'] as const;

/** A student the caller may request for. */
export interface StudentChoice {
  id: string;
  name: string;
}

/** An active subject, with its level for the grouping. */
export interface SubjectChoice {
  id: string;
  name: string;
  levelName: string;
}

export interface SlotsQuery {
  studentId: string;
  subjectId: string;
  duration: RequestDuration;
  from: string;
  to: string;
}

/** The active students the caller sees: a guardian's linked children, or
 * the student's own profile (the API's record policy). */
export async function fetchStudentChoices(
  signal: AbortSignal,
): Promise<StudentChoice[]> {
  const page = await apiCall(
    () =>
      api.GET('/api/v1/students', {
        params: { query: { status: 'active', limit: LIST_LIMIT } },
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

/** The active subjects (one page). */
export async function fetchSubjectChoices(
  signal: AbortSignal,
): Promise<SubjectChoice[]> {
  const page = await apiCall(
    () =>
      api.GET('/api/v1/subjects', {
        params: { query: { status: 'active', limit: LIST_LIMIT } },
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

export function fetchSlots(
  query: SlotsQuery,
  signal?: AbortSignal,
): Promise<AvailableSlots> {
  return apiCall(
    () =>
      api.GET('/api/v1/available-slots', {
        params: {
          query: {
            student_id: query.studentId,
            subject_id: query.subjectId,
            duration_minutes: query.duration,
            from: query.from,
            to: query.to,
          },
        },
        signal,
      }),
    { signal },
  );
}

export interface NewRequest {
  studentId: string;
  subjectId: string;
  startsAt: string;
  duration: RequestDuration;
  note: string;
}

/** Sends a request; the note is trimmed and an empty one is left out. */
export function createSessionRequest(
  request: NewRequest,
): Promise<SessionRequestDetail> {
  const note = request.note.trim();
  return apiCall(() =>
    api.POST('/api/v1/session-requests', {
      body: {
        student_id: request.studentId,
        subject_id: request.subjectId,
        starts_at: request.startsAt,
        duration_minutes: request.duration,
        ...(note ? { note } : {}),
      },
    }),
  );
}

export function fetchSessionRequests(
  signal?: AbortSignal,
): Promise<SessionRequestPage> {
  return apiCall(
    () =>
      api.GET('/api/v1/session-requests', {
        params: { query: { status: 'all', limit: LIST_LIMIT } },
        signal,
      }),
    { signal },
  );
}

export function withdrawSessionRequest(
  request: Pick<SessionRequestItem, 'id' | 'version'>,
): Promise<SessionRequestDetail> {
  return apiCall(() =>
    api.POST('/api/v1/session-requests/{request_id}/withdraw', {
      params: { path: { request_id: request.id } },
      body: { version: request.version },
    }),
  );
}
