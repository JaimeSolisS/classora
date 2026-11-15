import { uuidParam } from '@/features/academics/history/urlState';
import type { AccountSummary, ChargeStatus } from './api';

// The portal's pure rules (#388 B1, D1, F1): the child filter and the
// charge filter in the URL, the charge filter's API statuses, and the
// "expires soon" mark of a package.

/** The charge filter (D1): open = pending, partially paid and overdue. */
export type ChargeFilter = 'open' | 'overdue' | 'paid' | 'all';
export const CHARGE_FILTERS: readonly ChargeFilter[] = [
  'open',
  'overdue',
  'paid',
  'all',
];

const FILTER_STATUSES: Record<ChargeFilter, ChargeStatus[]> = {
  open: ['pending', 'partially_paid', 'overdue'],
  overdue: ['overdue'],
  paid: ['paid'],
  all: [],
};

/** The API's repeatable `status` values of a filter (empty: every one). */
export function filterStatuses(filter: ChargeFilter): ChargeStatus[] {
  return [...FILTER_STATUSES[filter]];
}

export interface PortalView {
  /** `?student=`, as written (checked against the summary's students). */
  student: string | null;
  /** `?status=`; anything else is `open`. */
  status: ChargeFilter;
}

export function portalView(params: URLSearchParams): PortalView {
  const status = params.get('status');
  return {
    student: uuidParam(params.get('student')),
    status: CHARGE_FILTERS.includes(status as ChargeFilter)
      ? (status as ChargeFilter)
      : 'open',
  };
}

/** The URL of a view (the defaults are left out). */
export function portalViewParams(view: PortalView): URLSearchParams {
  const params = new URLSearchParams();
  if (view.student) params.set('student', view.student);
  if (view.status !== 'open') params.set('status', view.status);
  return params;
}

/**
 * The selected child (B1): `?student=` when it names one of the summary's
 * students, otherwise null ("All children"), so a foreign or unknown id is
 * never sent.
 */
export function selectedStudent(
  summary: Pick<AccountSummary, 'students'>,
  student: string | null,
): string | null {
  if (student === null) return null;
  return summary.students.some((item) => item.student.id === student)
    ? student
    : null;
}

/** Days before expiry that count as "Expires soon" (F1). */
export const EXPIRES_SOON_DAYS = 14;

function dayNumber(date: string): number {
  const [year, month, day] = date.split('-').map(Number) as [
    number,
    number,
    number,
  ];
  return Date.UTC(year, month - 1, day) / 86_400_000;
}

/** An active package that expires within 14 days of `today` (inclusive). */
export function expiresSoon(
  pkg: { status: string; expires_on: string },
  today: string,
): boolean {
  if (pkg.status !== 'active') return false;
  const days = dayNumber(pkg.expires_on) - dayNumber(today);
  return days >= 0 && days <= EXPIRES_SOON_DAYS;
}
