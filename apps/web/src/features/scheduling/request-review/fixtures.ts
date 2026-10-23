import { CREATED, TUTORS } from '../create/fixtures';
import type { SessionRequestConfirmed, SessionRequestReview } from './api';

// Staff review fixtures (#60): a pending request, its confirmed and
// rejected outcomes, and a started one that can only be rejected.

export const REQUEST_ID = '01d00000-0000-7000-8000-0000000000a1';

export const PENDING_REVIEW: SessionRequestReview = {
  id: REQUEST_ID,
  status: 'pending',
  version: 1,
  created_at: '2031-01-02T18:00:00Z',
  resolved_at: null,
  student: {
    id: '00000000-0000-4000-8000-0000000001a1',
    display_name: 'Ana López',
    is_active: true,
  },
  subject: {
    id: CREATED.subject.id,
    name: 'Álgebra',
    education_level_name: 'Secundaria',
    is_active: true,
  },
  starts_at: '2031-01-06T16:00:00Z',
  ends_at: '2031-01-06T17:00:00Z',
  duration_minutes: 60,
  note: 'Prefiere con la misma tutora de siempre.',
  requested_by: {
    id: '00000000-0000-4000-8000-0000000002b1',
    display_name: 'Marta López',
  },
  approval_request_id: '00000000-0000-4000-8000-0000000008a1',
  class_session_id: null,
  rejection_reason: null,
  can_decide: true,
};

export const CONFIRMED_REVIEW: SessionRequestReview = {
  ...PENDING_REVIEW,
  status: 'confirmed',
  version: 2,
  resolved_at: '2031-01-03T15:00:00Z',
  class_session_id: CREATED.id,
  can_decide: false,
};

export const REJECTED_REVIEW: SessionRequestReview = {
  ...PENDING_REVIEW,
  status: 'rejected',
  version: 2,
  resolved_at: '2031-01-03T15:00:00Z',
  rejection_reason: 'No hay tutores disponibles ese día.',
  can_decide: false,
};

export const STARTED_REVIEW: SessionRequestReview = {
  ...PENDING_REVIEW,
  can_decide: false,
};

export const CONFIRMED: SessionRequestConfirmed = {
  request: CONFIRMED_REVIEW,
  class_session: {
    ...CREATED,
    tutor: { id: TUTORS[0]!.id, display_name: 'Carmen Ruiz' },
  },
};
