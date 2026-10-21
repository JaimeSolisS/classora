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
  series: null,
  session_count: 1,
  sessions_truncated: false,
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

// Series offers (#233 G1): a Monday/Wednesday series at 10:00 in Mexico
// City (16:00Z) from Monday 2031-01-06.

const SERIES_ID = '00000000-0000-4000-8000-0000000007c1';

/** `n` Monday/Wednesday sessions from 2031-01-06 (16:00Z, one hour). */
export function seriesSessions(
  n: number,
  state: 'offered' | 'confirmed' = 'offered',
): AssignmentOfferItem['sessions'] {
  return Array.from({ length: n }, (_, index) => {
    const week = Math.floor(index / 2);
    const day = 6 + week * 7 + (index % 2) * 2;
    const date = new Date(Date.UTC(2031, 0, day, 16));
    const end = new Date(date.getTime() + 60 * 60 * 1000);
    return {
      id: `00000000-0000-4000-8000-0000000008${String(index).padStart(2, '0')}`,
      state,
      starts_at: date.toISOString().replace('.000Z', 'Z'),
      ends_at: end.toISOString().replace('.000Z', 'Z'),
      subject: { name: 'Álgebra', education_level_name: 'Secundaria' },
      classroom: { name: 'Aula 1' },
      participants: [{ display_name: 'Ana López' }],
    };
  });
}

/** A pending open-ended series offer: 24 sessions so far, 10 listed. */
export const SERIES_OFFER: AssignmentOfferItem = {
  ...PENDING_OFFER,
  id: '00000000-0000-4000-8000-0000000007f5',
  scope: 'series',
  series: {
    id: SERIES_ID,
    kind: 'individual',
    subject: { name: 'Álgebra', education_level_name: 'Secundaria' },
    weekdays: [1, 3],
    interval: 1,
    start_time: '10:00',
    duration_minutes: 60,
    time_zone: 'America/Mexico_City',
    from_date: '2031-01-06',
    until: null,
    count: null,
    last_occurrence_on: null,
  },
  session_count: 24,
  sessions: seriesSessions(10),
  sessions_truncated: true,
};

/** A pending series of four sessions, all listed. */
export const SHORT_SERIES_OFFER: AssignmentOfferItem = {
  ...SERIES_OFFER,
  id: '00000000-0000-4000-8000-0000000007f6',
  series: {
    ...SERIES_OFFER.series!,
    count: 4,
    last_occurrence_on: '2031-01-15',
  },
  session_count: 4,
  sessions: seriesSessions(4),
  sessions_truncated: false,
};

export const ACCEPTED_SERIES_OFFER: AssignmentOfferItem = {
  ...SERIES_OFFER,
  id: '00000000-0000-4000-8000-0000000007f7',
  status: 'accepted',
  responded_at: '2031-01-03T11:00:00Z',
  sessions: seriesSessions(10, 'confirmed'),
};
