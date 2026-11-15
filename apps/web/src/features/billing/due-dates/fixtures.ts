import type { ChargeItem } from '../accounts/api';
import { CHARGE_OVERDUE } from '../accounts/fixtures';
import type { DueDateChange } from './api';

// Due-date fixtures (#382 F1, F2) for the tests and the stories, on the
// #379 account fixtures (the center's today is 2026-10-20).

export const STAFF_USER_ID = '00000000-0000-4000-8000-000000000001';

/** The overdue charge, its due date moved from 2026-09-01 to 2026-09-15. */
export const CHARGE_MOVED: ChargeItem = {
  ...CHARGE_OVERDUE,
  due_date: '2026-09-15',
  original_due_date: '2026-09-01',
};

export const DUE_DATE_CHANGES: DueDateChange[] = [
  {
    id: '56000000-0000-4000-8000-000000000001',
    charge_id: CHARGE_MOVED.id,
    previous_due_date: '2026-09-01',
    due_date: '2026-09-10',
    reason: 'La familia pidió una semana más.',
    changed_by_user_id: STAFF_USER_ID,
    created_at: '2026-08-28T17:00:00Z',
  },
  {
    id: '56000000-0000-4000-8000-000000000002',
    charge_id: CHARGE_MOVED.id,
    previous_due_date: '2026-09-10',
    due_date: '2026-09-15',
    reason: 'Acuerdo con dirección.',
    changed_by_user_id: STAFF_USER_ID,
    created_at: '2026-09-09T17:00:00Z',
  },
];
