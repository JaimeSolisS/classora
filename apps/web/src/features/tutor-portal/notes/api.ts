import type { components } from '@classora/api-client';
import { api, apiCall } from '@/app/api';

// The session note calls (#79): a session's notes (its current tutor or an
// academic_note.review holder; anyone else 404), a student's history, the
// tutor's writes (create, edit, share, stop sharing, delete; each write but
// create sends the note's version) and the family route (shared notes
// only). The API's record policies decide; the pages only show what they
// return. The staff session page and My schedule reuse these.

export type SessionNote = components['schemas']['SessionNoteView'];
export type SessionNoteList = components['schemas']['SessionNoteList'];
export type StudentNotesPage =
  components['schemas']['CursorPage_SessionNoteView_'];
export type FamilyLearningNote = components['schemas']['FamilyLearningNote'];
export type FamilyNotesPage =
  components['schemas']['CursorPage_FamilyLearningNote_'];

export const NOTES_KEY = ['notes'] as const;

export function sessionNotesKey(sessionId: string) {
  return [...NOTES_KEY, 'session', sessionId] as const;
}

export function studentNotesKey(studentId: string) {
  return [...NOTES_KEY, 'student', studentId] as const;
}

export function familyNotesKey(studentId: string, sessionId: string) {
  return [...NOTES_KEY, 'family', studentId, sessionId] as const;
}

export function fetchSessionNotes(
  sessionId: string,
  signal?: AbortSignal,
): Promise<SessionNoteList> {
  return apiCall(
    () =>
      api.GET('/api/v1/class-sessions/{class_session_id}/notes', {
        params: { path: { class_session_id: sessionId } },
        signal,
      }),
    { signal },
  );
}

export function fetchStudentNotes(
  studentId: string,
  cursor: string | null,
  signal?: AbortSignal,
): Promise<StudentNotesPage> {
  return apiCall(
    () =>
      api.GET('/api/v1/students/{student_id}/notes', {
        params: {
          path: { student_id: studentId },
          query: cursor ? { cursor } : {},
        },
        signal,
      }),
    { signal },
  );
}

export function fetchFamilyNotes(
  studentId: string,
  sessionId: string,
  signal?: AbortSignal,
): Promise<FamilyNotesPage> {
  return apiCall(
    () =>
      api.GET('/api/v1/students/{student_id}/learning-notes', {
        params: {
          path: { student_id: studentId },
          query: { class_session_id: sessionId },
        },
        signal,
      }),
    { signal },
  );
}

export function createNote(
  sessionId: string,
  studentId: string,
  body: string,
): Promise<SessionNote> {
  return apiCall(() =>
    api.POST('/api/v1/class-sessions/{class_session_id}/notes', {
      params: { path: { class_session_id: sessionId } },
      body: { student_id: studentId, body },
    }),
  );
}

export function editNote(
  note: SessionNote,
  body: string,
): Promise<SessionNote> {
  return apiCall(() =>
    api.PATCH('/api/v1/notes/{note_id}', {
      params: { path: { note_id: note.id } },
      body: { version: note.version, body },
    }),
  );
}

export function publishNote(note: SessionNote): Promise<SessionNote> {
  return apiCall(() =>
    api.POST('/api/v1/notes/{note_id}/publish', {
      params: { path: { note_id: note.id } },
      body: { version: note.version },
    }),
  );
}

export function unpublishNote(
  note: SessionNote,
  reason?: string,
): Promise<SessionNote> {
  return apiCall(() =>
    api.POST('/api/v1/notes/{note_id}/unpublish', {
      params: { path: { note_id: note.id } },
      body: reason
        ? { version: note.version, reason }
        : { version: note.version },
    }),
  );
}

export async function deleteNote(note: SessionNote): Promise<void> {
  await apiCall(() =>
    api.DELETE('/api/v1/notes/{note_id}', {
      params: { path: { note_id: note.id }, query: { version: note.version } },
    }),
  );
}

/** The tutor's notes page of a session (H1). */
export function notesPath(sessionId: string): string {
  return `/my-schedule/sessions/${sessionId}/notes`;
}
