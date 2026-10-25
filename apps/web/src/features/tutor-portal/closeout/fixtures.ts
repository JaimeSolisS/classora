import {
  ANA,
  attendance,
  BRUNO,
  SESSION_ID,
} from '@/features/tutor-portal/attendance/fixtures';
import type { SessionAttendance } from '@/features/tutor-portal/attendance/api';
import type { SessionCloseoutView } from './api';

// Closeout data for tests and stories (#77 H6, H7): a started group session
// of two students (attendance unmarked, all marked, all absent), the
// submitted closeout (seen by the tutor, by a reviewer, by Finance with the
// summary hidden), not started, not closable, completed without a closeout
// and the staff view of a started session waiting for the tutor.

export { SESSION_ID, ZONE } from '@/features/tutor-portal/attendance/fixtures';

const MARKED = {
  marked_by: { display_name: 'Carmen Ruiz' },
  marked_at: '2026-11-09T16:10:00Z',
};

function item(id: string, name: string, status: 'present' | 'absent' | null) {
  return {
    student: { id, display_name: name },
    status,
    marked_by: status ? MARKED.marked_by : null,
    marked_at: status ? MARKED.marked_at : null,
    corrected: false,
    corrections: [],
  };
}

export const ATTENDANCE_UNMARKED: SessionAttendance = attendance({
  items: [item(ANA, 'Ana Díaz', 'present'), item(BRUNO, 'Bruno Gómez', null)],
});

export const ATTENDANCE_MARKED: SessionAttendance = attendance({
  version: 4,
  items: [
    item(ANA, 'Ana Díaz', 'present'),
    item(BRUNO, 'Bruno Gómez', 'present'),
  ],
});

export const ATTENDANCE_ABSENT: SessionAttendance = attendance({
  version: 4,
  items: [
    item(ANA, 'Ana Díaz', 'absent'),
    item(BRUNO, 'Bruno Gómez', 'absent'),
  ],
});

export const SUMMARY = {
  topics: 'Fracciones equivalentes',
  performance: 'Participaron y resolvieron los ejercicios',
  homework: 'Ejercicios 1 a 5',
  next_steps: 'Suma de fracciones',
};

export function closeoutView(
  overrides: Partial<SessionCloseoutView> = {},
): SessionCloseoutView {
  return {
    class_session_id: SESSION_ID,
    version: 4,
    kind: 'group',
    state: 'confirmed',
    // Monday 2026-11-09, 10:00-11:00 in Mexico City.
    starts_at: '2026-11-09T16:00:00Z',
    ends_at: '2026-11-09T17:00:00Z',
    scheduled_minutes: 60,
    subject: {
      id: '00000000-0000-4000-8000-000000076c01',
      name: 'Matemáticas',
      education_level_name: 'Secundaria',
    },
    tutor: { display_name: 'Carmen Ruiz' },
    can_submit: true,
    block: null,
    attendance: {
      total: 2,
      marked: 2,
      all_absent: false,
      missing_student_ids: [],
    },
    closeout: null,
    ...overrides,
  };
}

export const OPEN = closeoutView();

const RECORD = {
  id: '00000000-0000-4000-8000-000000077a01',
  status: 'submitted' as const,
  actual_minutes: 55,
  summary: SUMMARY,
  summary_hidden: false,
  submitted_by: { display_name: 'Carmen Ruiz' },
  submitted_at: '2026-11-09T17:05:00Z',
  approval_request_id: '00000000-0000-4000-8000-000000077b01',
};

export const SUBMITTED = closeoutView({
  version: 5,
  state: 'completed',
  can_submit: false,
  block: 'closeout_already_submitted',
  closeout: RECORD,
});

/** A reviewer (Owner, Administrator, Coordinator) on the session page. */
export const STAFF_SUBMITTED = closeoutView({
  ...SUBMITTED,
  block: 'closeout_tutor_only',
});

/** Finance: the minutes, not the summary (#77 E5). */
export const FINANCE_SUBMITTED = closeoutView({
  ...STAFF_SUBMITTED,
  closeout: { ...RECORD, summary: null, summary_hidden: true },
});

export const NOT_STARTED = closeoutView({
  can_submit: false,
  block: 'session_not_started',
});

export const NOT_CLOSABLE = closeoutView({
  state: 'cancelled',
  can_submit: false,
  block: 'session_not_closable',
});

/** A completed session without a closeout (#299 demo rows). */
export const NO_CLOSEOUT = closeoutView({
  state: 'completed',
  can_submit: false,
  block: 'closeout_already_submitted',
});

/** Staff on a started confirmed session. */
export const WAITING = closeoutView({
  can_submit: false,
  block: 'closeout_tutor_only',
});
