import {
  attendance,
  ANA,
  BRUNO,
  SESSION_ID,
  ZONE,
} from '@/features/tutor-portal/attendance/fixtures';
import type { SessionAttendance } from '@/features/tutor-portal/attendance/api';
import type { FamilyLearningNote, SessionNote, StudentNotesPage } from './api';

// Session note data for tests and stories (#79 I6, I7): the #76 group
// session (Ana, Bruno), an internal note, a shared one, one unpublished by
// a reviewer with a reason, a read-only note of a former student, another
// tutor's note in the history, and a family note.

export { ANA, BRUNO, SESSION_ID, ZONE };

export const NOTE_ID = '00000000-0000-4000-8000-000000079a01';
export const SHARED_ID = '00000000-0000-4000-8000-000000079a02';
export const UNPUBLISHED_ID = '00000000-0000-4000-8000-000000079a03';
export const OTHER_SESSION_ID = '00000000-0000-4000-8000-000000079b01';

export const SESSION: SessionAttendance = attendance({
  state: 'completed',
  can_mark: false,
  block: 'attendance_locked',
});

export function note(overrides: Partial<SessionNote> = {}): SessionNote {
  return {
    id: NOTE_ID,
    version: 1,
    class_session_id: SESSION_ID,
    session_starts_at: '2026-11-09T16:00:00Z',
    subject: {
      id: '00000000-0000-4000-8000-000000076c01',
      name: 'Matemáticas',
    },
    student: { id: ANA, display_name: 'Ana Díaz' },
    author: { display_name: 'Carmen Ruiz' },
    is_own: true,
    body: 'Repasar fracciones equivalentes antes del examen.',
    visibility: 'internal',
    published_at: null,
    published_by: null,
    unpublished_at: null,
    unpublish_reason: null,
    edited: false,
    created_at: '2026-11-09T17:05:00Z',
    can_edit: true,
    can_publish: true,
    can_unpublish: false,
    can_delete: true,
    ...overrides,
  };
}

export const INTERNAL = note();

export const SHARED = note({
  id: SHARED_ID,
  version: 2,
  body: 'Ana participó mucho hoy. ¡Muy bien!',
  visibility: 'family',
  published_at: '2026-11-09T17:30:00Z',
  published_by: { display_name: 'Carmen Ruiz' },
  can_edit: false,
  can_publish: false,
  can_unpublish: true,
  can_delete: false,
});

export const UNPUBLISHED = note({
  id: UNPUBLISHED_ID,
  version: 3,
  body: 'Comentó un tema de salud en clase.',
  edited: true,
  unpublished_at: '2026-11-10T09:00:00Z',
  unpublish_reason: 'Contiene un dato médico.',
});

export const READ_ONLY = note({
  can_edit: false,
  can_publish: false,
  can_unpublish: false,
  can_delete: false,
});

export const SCRIPT = note({
  id: '00000000-0000-4000-8000-000000079a09',
  body: '<script>alert(1)</script>',
});

export function history(...items: SessionNote[]): StudentNotesPage {
  return { items, next_cursor: null };
}

export const EARLIER = note({
  id: '00000000-0000-4000-8000-000000079a05',
  class_session_id: OTHER_SESSION_ID,
  session_starts_at: '2026-11-02T16:00:00Z',
  author: { display_name: 'Luis Pérez' },
  is_own: false,
  body: 'Le cuesta la división larga.',
  can_edit: false,
  can_publish: false,
  can_delete: false,
});

export const FAMILY_NOTE: FamilyLearningNote = {
  id: SHARED_ID,
  class_session_id: SESSION_ID,
  session_starts_at: '2026-11-09T16:00:00Z',
  subject_name: 'Matemáticas',
  author: { display_name: 'Carmen Ruiz' },
  body: 'Ana participó mucho hoy. ¡Muy bien!',
  published_at: '2026-11-09T17:30:00Z',
};
