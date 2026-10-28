import type { UnbilledItem, UnbilledPage } from './api';

// Unbilled session fixtures (#366 E1, E2) for the tests and the stories.

const SUBJECT_ALGEBRA = {
  id: '30000000-0000-4000-8000-000000000001',
  name: 'Álgebra',
};

/** A student without a family (reason no_billing_account). */
export const ITEM_NO_FAMILY: UnbilledItem = {
  class_session_id: '60000000-0000-4000-8000-000000000001',
  // 10:00 in Mexico City.
  starts_at: '2026-10-05T16:00:00Z',
  ends_at: '2026-10-05T17:00:00Z',
  service_date: '2026-10-05',
  state: 'completed',
  scheduled_minutes: 60,
  subject: SUBJECT_ALGEBRA,
  student: { id: '40000000-0000-4000-8000-000000000001', name: 'Ana López' },
  education_level: {
    id: '20000000-0000-4000-8000-000000000002',
    name: 'Secundaria',
  },
  family: null,
  reason: 'no_billing_account',
};

/** A session with no price in effect on its date (reason no_price). */
export const ITEM_NO_PRICE: UnbilledItem = {
  class_session_id: '60000000-0000-4000-8000-000000000002',
  // 17:30 in Mexico City.
  starts_at: '2026-10-06T23:30:00Z',
  ends_at: '2026-10-07T00:15:00Z',
  service_date: '2026-10-06',
  state: 'confirmed',
  scheduled_minutes: 45,
  subject: { id: '30000000-0000-4000-8000-000000000002', name: 'Física' },
  student: { id: '40000000-0000-4000-8000-000000000002', name: 'Bruno Díaz' },
  education_level: null,
  family: { id: '50000000-0000-4000-8000-000000000001', name: 'Familia Díaz' },
  reason: 'no_price',
};

export const UNBILLED_PAGE: UnbilledPage = {
  items: [ITEM_NO_FAMILY, ITEM_NO_PRICE],
  total: 2,
  limit: 25,
  offset: 0,
};

export const EMPTY_UNBILLED_PAGE: UnbilledPage = {
  items: [],
  total: 0,
  limit: 25,
  offset: 0,
};
