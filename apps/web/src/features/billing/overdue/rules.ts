import {
  MIN_DAYS_OPTIONS,
  type MinDaysOverdue,
  type OverdueFamilyStatus,
  type OverdueFilters,
} from './api';

// The overdue page's URL state (#382 B3): `min_days_overdue` (1, 31, 61 or
// 91; 1 = any), `family_status` (all, active, inactive; all by default)
// and `page`. Anything else is ignored; defaults are left out of the URL.

const STATUSES: readonly OverdueFamilyStatus[] = ['all', 'active', 'inactive'];

function pageOf(value: string | null): number {
  const page = Number.parseInt(value ?? '', 10);
  return Number.isInteger(page) && page >= 1 ? page : 1;
}

export function overdueFilters(search: URLSearchParams): OverdueFilters {
  const minDays = Number(search.get('min_days_overdue'));
  const status = search.get('family_status');
  return {
    minDays: (MIN_DAYS_OPTIONS as readonly number[]).includes(minDays)
      ? (minDays as MinDaysOverdue)
      : 1,
    familyStatus: STATUSES.find((item) => item === status) ?? 'all',
    page: pageOf(search.get('page')),
  };
}

export function overdueParams(filters: OverdueFilters): URLSearchParams {
  const params = new URLSearchParams();
  if (filters.minDays !== 1) {
    params.set('min_days_overdue', String(filters.minDays));
  }
  if (filters.familyStatus !== 'all') {
    params.set('family_status', filters.familyStatus);
  }
  if (filters.page > 1) params.set('page', String(filters.page));
  return params;
}

/** The account page of a row, filtered to its overdue charges (B2). */
export function overdueAccountPath(familyId: string): string {
  return `/billing/accounts/${familyId}?status=overdue`;
}
