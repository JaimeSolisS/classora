import { CREATED, TUTORS, WARNINGS } from '../create/fixtures';
import type {
  AssignmentOfferCreated,
  AssignmentOfferItem,
  ClassSessionDetail,
} from './api';

// The staff session page's sample data (#52 H4): an unassigned session
// after a decline, its offer history, and the offer a 201 returns.

// The create's 201 as a detail (its `warnings` member is left out).
const DETAIL: ClassSessionDetail = (() => {
  const detail: Partial<typeof CREATED> = { ...CREATED };
  delete detail.warnings;
  return detail as ClassSessionDetail;
})();

export const OWNER = {
  id: '00000000-0000-4000-8000-0000000004a9',
  display_name: 'Demo Owner',
};

export const UNASSIGNED_SESSION: ClassSessionDetail = {
  ...DETAIL,
  state: 'unassigned',
  tutor: null,
  version: 3,
  pending_offer: null,
};

export const OFFERED_SESSION: ClassSessionDetail = {
  ...DETAIL,
  state: 'offered',
  version: 4,
};

export const DECLINED: AssignmentOfferItem = {
  id: '00000000-0000-4000-8000-0000000006f1',
  scope: 'session',
  status: 'declined',
  tutor: { id: TUTORS[0]!.id, display_name: 'Carmen Ruiz' },
  offered_at: '2026-10-21T12:00:00Z',
  offered_by: OWNER,
  respond_by: '2026-10-23T12:00:00Z',
  responded_at: '2026-10-21T15:30:00Z',
  decline_note: 'Tengo otra clase a esa hora.',
  sessions: [
    {
      id: CREATED.id,
      state: 'unassigned',
      starts_at: CREATED.starts_at,
      ends_at: CREATED.ends_at,
      subject: { name: 'Álgebra', education_level_name: 'Secundaria' },
      classroom: { name: 'Aula 1' },
      participants: [{ display_name: 'Ana López' }],
    },
  ],
};

export const NEW_OFFER: AssignmentOfferCreated = {
  offer: {
    ...DECLINED,
    id: '00000000-0000-4000-8000-0000000006f2',
    status: 'pending',
    tutor: { id: TUTORS[1]!.id, display_name: 'Diego Torres' },
    offered_at: '2026-10-22T09:00:00Z',
    respond_by: '2026-10-24T09:00:00Z',
    responded_at: null,
    decline_note: null,
  },
  warnings: WARNINGS,
};
