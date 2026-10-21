import type { AssignmentOfferItem } from './api';

// Offers for stories and tests (#52 H4). "Now" is pinned to NOW; the
// sessions are on Monday 2031-01-06 in America/Mexico_City (UTC-6).

export const NOW = new Date('2031-01-03T12:00:00Z');

const TUTOR = {
  id: '00000000-0000-4000-8000-0000000007a1',
  display_name: 'Demo Tutor',
};
const OWNER = {
  id: '00000000-0000-4000-8000-0000000007a9',
  display_name: 'Demo Owner',
};

export const PENDING_OFFER: AssignmentOfferItem = {
  id: '00000000-0000-4000-8000-0000000007f1',
  scope: 'session',
  status: 'pending',
  tutor: TUTOR,
  offered_at: '2031-01-03T10:00:00Z',
  offered_by: OWNER,
  respond_by: '2031-01-05T10:00:00Z',
  responded_at: null,
  decline_note: null,
  sessions: [
    {
      id: '00000000-0000-4000-8000-0000000007e1',
      state: 'offered',
      starts_at: '2031-01-06T16:00:00Z',
      ends_at: '2031-01-06T17:00:00Z',
      subject: { name: 'Álgebra', education_level_name: 'Secundaria' },
      classroom: { name: 'Aula 1' },
      participants: [{ display_name: 'Ana López' }],
    },
  ],
};

/** Answer within 24 hours of NOW. */
export const DUE_SOON_OFFER: AssignmentOfferItem = {
  ...PENDING_OFFER,
  id: '00000000-0000-4000-8000-0000000007f2',
  respond_by: '2031-01-04T08:00:00Z',
  sessions: [
    {
      ...PENDING_OFFER.sessions[0]!,
      id: '00000000-0000-4000-8000-0000000007e2',
      starts_at: '2031-01-06T18:00:00Z',
      ends_at: '2031-01-06T19:30:00Z',
      subject: { name: 'Física', education_level_name: 'Preparatoria' },
      classroom: null,
      participants: [
        { display_name: 'Luis Pérez' },
        { display_name: 'María Gómez' },
      ],
    },
  ],
};

export const ACCEPTED_OFFER: AssignmentOfferItem = {
  ...PENDING_OFFER,
  id: '00000000-0000-4000-8000-0000000007f3',
  status: 'accepted',
  responded_at: '2031-01-03T11:00:00Z',
  sessions: [{ ...PENDING_OFFER.sessions[0]!, state: 'confirmed' }],
};

export const DECLINED_OFFER: AssignmentOfferItem = {
  ...DUE_SOON_OFFER,
  id: '00000000-0000-4000-8000-0000000007f4',
  status: 'declined',
  responded_at: '2031-01-03T11:30:00Z',
  decline_note: 'Tengo otra clase a esa hora.',
  sessions: [{ ...DUE_SOON_OFFER.sessions[0]!, state: 'unassigned' }],
};
