import type { OverdueCloseoutItem, OverdueCloseoutPage } from './api';

// Overdue closeouts for tests and stories (#301b H1, H2): two classes of
// Carmen Ruiz (a group and an individual one), one of a tutor who can't
// submit any more (a long subject name), oldest first.

export const ZONE = 'America/Mexico_City';
export const CARMEN_ID = '00000000-0000-4000-8000-0000000c4a01';
export const LEFT_ID = '00000000-0000-4000-8000-0000000c4a02';
/** The read time of the stories and tests (2 h after the newest end). */
export const FETCHED_AT = new Date('2026-11-10T19:00:00Z');

function item(
  id: string,
  overrides: Partial<OverdueCloseoutItem> = {},
): OverdueCloseoutItem {
  return {
    class_session_id: `00000000-0000-4000-8000-0000000c5${id}`,
    // Monday 2026-11-09, 10:00-11:00 in Mexico City.
    starts_at: '2026-11-09T16:00:00Z',
    ends_at: '2026-11-09T17:00:00Z',
    kind: 'group',
    scheduled_minutes: 60,
    subject: { name: 'Matemáticas', education_level_name: 'Secundaria' },
    tutor: {
      user_id: CARMEN_ID,
      display_name: 'Carmen Ruiz',
      can_submit: true,
    },
    participant_count: 3,
    attendance: { marked: 1, total: 3 },
    ...overrides,
  };
}

export const OVERDUE_ITEMS: OverdueCloseoutItem[] = [
  item('001'),
  item('002', {
    starts_at: '2026-11-10T14:00:00Z',
    ends_at: '2026-11-10T15:00:00Z',
    kind: 'individual',
    subject: {
      name: 'Comprensión lectora y redacción de textos académicos avanzados',
      education_level_name: 'Preparatoria',
    },
    tutor: { user_id: LEFT_ID, display_name: 'Diego Salas', can_submit: false },
    participant_count: 1,
    attendance: { marked: 0, total: 1 },
  }),
  item('003', {
    starts_at: '2026-11-10T16:00:00Z',
    ends_at: '2026-11-10T17:00:00Z',
    kind: 'individual',
    subject: { name: 'Física', education_level_name: 'Secundaria' },
    participant_count: 1,
    attendance: { marked: 1, total: 1 },
  }),
];

export function overduePage(
  items: OverdueCloseoutItem[] = OVERDUE_ITEMS,
  { total = items.length, offset = 0 } = {},
): OverdueCloseoutPage {
  return { items, total, limit: 25, offset };
}

/** 25 rows of a 30-row list (the first page). */
export const FULL_PAGE: OverdueCloseoutItem[] = Array.from(
  { length: 25 },
  (_, index) => item(String(100 + index)),
);
