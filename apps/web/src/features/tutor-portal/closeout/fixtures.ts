import {
  ANA,
  attendance,
  BRUNO,
  SESSION_ID,
} from '@/features/tutor-portal/attendance/fixtures';
import type { SessionAttendance } from '@/features/tutor-portal/attendance/api';
import type {
  CloseoutApprovalRevision,
  CloseoutCorrectionItem,
  SessionCloseoutRecord,
  SessionCloseoutView,
} from './api';

// Closeout data for tests and stories (#77 H6, H7): a started group session
// of two students (attendance unmarked, all marked, all absent), the
// submitted closeout (seen by the tutor, by a reviewer, by Finance with the
// summary hidden), not started, not closable, completed without a closeout
// and the staff view of a started session waiting for the tutor; #78: the
// review states (a reviewer on a submitted closeout, changes requested,
// approved with its history, a correction request under review, attendance
// changed since approval, Finance's redacted history) and the tutor's
// views of them; #305: the tutor's edit modes (attendance editable while
// changes are requested, locked after approval), the correction under
// review, changes requested on a correction (a long reason), a non-
// submitter without flags and the view after a resubmission.

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
    can_review: false,
    can_correct: false,
    can_resubmit: false,
    can_request_correction: false,
    // #301 (read by the web from #317).
    can_request_changes: false,
    can_submit_on_behalf: false,
    can_take_over: false,
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

const RECORD: SessionCloseoutRecord = {
  id: '00000000-0000-4000-8000-000000077a01',
  version: 1,
  status: 'submitted' as const,
  actual_minutes: 55,
  summary: SUMMARY,
  summary_hidden: false,
  submitted_by: { display_name: 'Carmen Ruiz' },
  submitted_at: '2026-11-09T17:05:00Z',
  approval_request_id: '00000000-0000-4000-8000-000000077b01',
  review: {
    status: 'submitted',
    reason: null,
    last_submitted_at: '2026-11-09T17:05:00Z',
    tutor_note: null,
  },
  approval: null,
  revisions: [],
  corrections: [],
  attendance_changed_since_approval: false,
  on_behalf: null,
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

// --- #78 review --------------------------------------------------------------

export const REVISION_1: CloseoutApprovalRevision = {
  id: '00000000-0000-4000-8000-000000078a01',
  revision: 1,
  approved_minutes: 55,
  approved_by: { display_name: 'Laura Méndez' },
  approved_at: '2026-11-10T15:00:00Z',
  source: 'review',
};

export const REVISION_2: CloseoutApprovalRevision = {
  id: '00000000-0000-4000-8000-000000078a02',
  revision: 2,
  approved_minutes: 50,
  approved_by: { display_name: 'Laura Méndez' },
  approved_at: '2026-11-11T15:00:00Z',
  source: 'staff_correction',
};

export const CORRECTION: CloseoutCorrectionItem = {
  id: '00000000-0000-4000-8000-000000078c01',
  fields: ['actual_minutes'],
  previous_minutes: 55,
  new_minutes: 50,
  reason: 'Terminó 5 minutos antes',
  corrected_by: { display_name: 'Laura Méndez' },
  created_at: '2026-11-11T15:00:00Z',
  approval_id: REVISION_2.id,
};

/** A reviewer on a submitted closeout: Approve, Request changes, Correct. */
export const REVIEWABLE = closeoutView({
  ...STAFF_SUBMITTED,
  can_review: true,
  // #301b E2: Request changes follows its own flag.
  can_request_changes: true,
  can_correct: true,
});

export const CHANGES_REQUESTED_RECORD: SessionCloseoutRecord = {
  ...RECORD,
  version: 2,
  status: 'changes_requested',
  review: {
    ...RECORD.review,
    status: 'changes_requested',
    reason: 'Añade la tarea asignada y los siguientes pasos con más detalle.',
  },
};

/** Staff after requesting changes: no actions (the tutor owns it). */
export const STAFF_CHANGES_REQUESTED = closeoutView({
  ...STAFF_SUBMITTED,
  closeout: CHANGES_REQUESTED_RECORD,
});

export const APPROVED_RECORD: SessionCloseoutRecord = {
  ...RECORD,
  version: 3,
  status: 'approved',
  actual_minutes: 50,
  review: { ...RECORD.review, status: 'approved' },
  approval: REVISION_2,
  revisions: [REVISION_2, REVISION_1],
  corrections: [CORRECTION],
};

/** A reviewer on an approved closeout with its history: Correct only. */
export const STAFF_APPROVED = closeoutView({
  ...STAFF_SUBMITTED,
  can_correct: true,
  closeout: APPROVED_RECORD,
});

/** A tutor's correction request under review (the approval in force). */
export const CORRECTION_SUBMITTED = closeoutView({
  ...REVIEWABLE,
  closeout: {
    ...APPROVED_RECORD,
    version: 4,
    status: 'submitted',
    actual_minutes: 45,
    review: {
      status: 'submitted',
      reason: null,
      last_submitted_at: '2026-11-12T15:00:00Z',
      tutor_note: 'Salimos 5 minutos antes por un simulacro.',
    },
  },
});

/** E6: a staff attendance correction after the approval. */
export const ATTENDANCE_CHANGED = closeoutView({
  ...STAFF_APPROVED,
  closeout: { ...APPROVED_RECORD, attendance_changed_since_approval: true },
});

/** Finance: minutes and metadata, no reasons or summary. */
export const FINANCE_APPROVED = closeoutView({
  ...STAFF_SUBMITTED,
  closeout: {
    ...APPROVED_RECORD,
    summary: null,
    summary_hidden: true,
    corrections: [{ ...CORRECTION, reason: null }],
  },
});

/** The tutor's read-only views (G4). */
export const TUTOR_CHANGES_REQUESTED = closeoutView({
  ...SUBMITTED,
  can_resubmit: true,
  closeout: CHANGES_REQUESTED_RECORD,
});

export const TUTOR_APPROVED = closeoutView({
  ...SUBMITTED,
  can_request_correction: true,
  closeout: APPROVED_RECORD,
});

// --- #305 tutor edit modes ---------------------------------------------------

/** The tutor's attendance while changes are requested (editable, AM1). */
export const ATTENDANCE_COMPLETED: SessionAttendance = {
  ...ATTENDANCE_MARKED,
  state: 'completed',
};

/** The tutor's attendance of an approved closeout (read-only). */
export const ATTENDANCE_LOCKED: SessionAttendance = {
  ...ATTENDANCE_COMPLETED,
  can_mark: false,
  block: 'attendance_locked',
};

/** The tutor's own correction request under review (no action). */
export const TUTOR_CORRECTION_SUBMITTED = closeoutView({
  ...SUBMITTED,
  closeout: CORRECTION_SUBMITTED.closeout,
});

/** Changes requested on a correction request: the approval stays in force. */
export const TUTOR_CHANGES_REQUESTED_ON_CORRECTION = closeoutView({
  ...SUBMITTED,
  can_resubmit: true,
  closeout: {
    ...APPROVED_RECORD,
    version: 5,
    status: 'changes_requested',
    actual_minutes: 45,
    review: {
      status: 'changes_requested',
      reason: `Indica el motivo del simulacro. ${'Explica con detalle. '.repeat(45)}`,
      last_submitted_at: '2026-11-12T15:00:00Z',
      tutor_note: 'Salimos 5 minutos antes por un simulacro.',
    },
  },
});

/** A staff member who also tutors, on changes requested: no flag. */
export const NOT_SUBMITTER_CHANGES_REQUESTED = closeoutView({
  ...TUTOR_CHANGES_REQUESTED,
  can_resubmit: false,
});

/** After a resubmission (58 min): under review again. */
export const TUTOR_RESUBMITTED = closeoutView({
  ...SUBMITTED,
  closeout: {
    ...CHANGES_REQUESTED_RECORD,
    version: 3,
    status: 'submitted',
    actual_minutes: 58,
    review: {
      ...CHANGES_REQUESTED_RECORD.review,
      status: 'submitted',
      last_submitted_at: '2026-11-10T15:00:00Z',
    },
  },
});

// --- #301b on behalf ------------------------------------------------------

export const ON_BEHALF = {
  by: { display_name: 'Laura Méndez' },
  reason: 'El tutor dejó el centro antes de enviar el cierre.',
  submitted_at: '2026-11-10T16:00:00Z',
};

/** The tutor's view of a closeout submitted on their behalf (E5). */
export const TUTOR_ON_BEHALF_SUBMITTED = closeoutView({
  ...SUBMITTED,
  closeout: {
    ...RECORD,
    submitted_at: ON_BEHALF.submitted_at,
    review: { ...RECORD.review, last_submitted_at: ON_BEHALF.submitted_at },
    on_behalf: ON_BEHALF,
  },
});

/** The tutor's view once it was approved (the correction request stays). */
export const TUTOR_ON_BEHALF_APPROVED = closeoutView({
  ...TUTOR_APPROVED,
  closeout: { ...APPROVED_RECORD, on_behalf: ON_BEHALF },
});
