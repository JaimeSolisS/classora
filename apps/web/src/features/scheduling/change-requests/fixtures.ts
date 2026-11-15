import { CLASSROOMS, CREATED, TUTORS } from '../create/fixtures';
import type { ChangeRequestApproved, ChangeRequestReview } from './api';

// Staff review fixtures (#62): a pending late cancellation by a guardian, a
// pending reschedule, an approved exception, a rejection, and a request
// whose session has started (it can only be rejected).

export const CHANGE_REQUEST_ID = '01d00000-0000-7000-8000-0000000000c1';
export const SESSION_ID = CREATED.id;

export const PENDING_CANCELLATION: ChangeRequestReview = {
  id: CHANGE_REQUEST_ID,
  kind: 'cancellation',
  status: 'pending',
  initiated_by: 'family',
  requested_by: {
    id: '00000000-0000-4000-8000-0000000002b1',
    display_name: 'Marta López',
  },
  student: {
    id: '00000000-0000-4000-8000-0000000001a1',
    display_name: 'Ana López',
  },
  subject: {
    id: CREATED.subject.id,
    name: 'Física',
    education_level_name: 'Preparatoria',
  },
  session: {
    id: SESSION_ID,
    state: 'confirmed',
    kind: 'individual',
    starts_at: '2031-01-07T16:00:00Z',
    ends_at: '2031-01-07T17:00:00Z',
    series_id: null,
    tutor: { id: TUTORS[0]!.id, display_name: 'Carmen Ruiz' },
    classroom: { id: CLASSROOMS[0]!.id, name: 'Aula 1' },
    version: 3,
  },
  session_starts_at: '2031-01-07T16:00:00Z',
  session_ends_at: '2031-01-07T17:00:00Z',
  proposed_starts_at: null,
  proposed_ends_at: null,
  note: 'Tenemos un viaje familiar.',
  policy: {
    id: '00000000-0000-4000-8000-0000000009a1',
    effective_from: '2000-01-01T00:00:00Z',
  },
  request_outcome: {
    classification: 'late',
    makeup_allowed: false,
    makeup_until: null,
  },
  final_outcome: null,
  outcome: {
    classification: 'late',
    makeup_allowed: false,
    makeup_until: null,
  },
  decision: null,
  decided_by: null,
  approval_request_id: '00000000-0000-4000-8000-0000000008c1',
  created_at: '2031-01-06T18:00:00Z',
  resolved_at: null,
  version: 1,
  can_decide: true,
};

export const PENDING_RESCHEDULE: ChangeRequestReview = {
  ...PENDING_CANCELLATION,
  kind: 'reschedule',
  proposed_starts_at: '2031-01-08T17:00:00Z',
  proposed_ends_at: '2031-01-08T18:00:00Z',
  request_outcome: {
    classification: 'on_time',
    makeup_allowed: false,
    makeup_until: null,
  },
  outcome: {
    classification: 'on_time',
    makeup_allowed: false,
    makeup_until: null,
  },
  note: null,
};

export const STARTED_CANCELLATION: ChangeRequestReview = {
  ...PENDING_CANCELLATION,
  can_decide: false,
};

const EXCUSED_WITH_MAKEUP = {
  classification: 'excused',
  makeup_allowed: true,
  makeup_until: '2031-02-06T16:00:00Z',
} as const;

export const APPROVED_EXCEPTION: ChangeRequestReview = {
  ...PENDING_CANCELLATION,
  status: 'approved',
  session: { ...PENDING_CANCELLATION.session, state: 'cancelled', version: 4 },
  final_outcome: EXCUSED_WITH_MAKEUP,
  outcome: EXCUSED_WITH_MAKEUP,
  decision: {
    exception: true,
    reason: 'Enfermedad con justificante médico.',
    decided_at: '2031-01-06T20:00:00Z',
  },
  decided_by: {
    id: '00000000-0000-4000-8000-0000000003d1',
    display_name: 'Laura Owner',
  },
  resolved_at: '2031-01-06T20:00:00Z',
  version: 2,
  can_decide: false,
};

export const REJECTED: ChangeRequestReview = {
  ...PENDING_CANCELLATION,
  status: 'rejected',
  decision: {
    exception: false,
    reason: 'La sesión ya tiene sustituto confirmado.',
    decided_at: '2031-01-06T20:00:00Z',
  },
  decided_by: APPROVED_EXCEPTION.decided_by,
  resolved_at: '2031-01-06T20:00:00Z',
  version: 2,
  can_decide: false,
};

export const APPROVED_RESULT: ChangeRequestApproved = {
  change_request: APPROVED_EXCEPTION,
  class_session: {
    ...CREATED,
    state: 'cancelled',
    cancellation: {
      cancelled_at: '2031-01-06T20:00:00Z',
      reason: 'family_request',
    },
  },
  warnings: [],
};
