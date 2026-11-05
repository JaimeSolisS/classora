import { canAccess, type AccessRequirement } from '@/features/auth/access';
import type { SessionUser } from '@/features/auth/session';

// What the Exports nav item, route and "Export" links require (M8-07b A1,
// A3, E1), kept apart from the pages so the router, the navigation and the
// list pages don't load them. It mirrors the API's rule (#124 A4,
// `reporting.exports.registry.may_export`): a staff role **and** the
// dataset's list-page permission. UX only: the API checks every request.

/** The staff roles (`identity.permissions.STAFF_ROLES`). */
export const EXPORT_STAFF_ROLES = [
  'owner',
  'administrator',
  'academic_coordinator',
  'finance_staff',
] as const;

/** The nav item and the /exports route (E1): any staff role. */
export const EXPORTS_ACCESS = {
  anyRole: EXPORT_STAFF_ROLES,
} as const satisfies AccessRequirement;

/** The datasets the web knows, in the API's registry order. */
export const EXPORT_DATASETS = [
  'students',
  'class_sessions',
  'expenses',
] as const;
export type ExportDatasetCode = (typeof EXPORT_DATASETS)[number];

export function isExportDataset(value: unknown): value is ExportDatasetCode {
  return (EXPORT_DATASETS as readonly unknown[]).includes(value);
}

/** Who may export each dataset: a staff role and the page's permission. */
export const EXPORT_DATASET_ACCESS = {
  students: { anyRole: EXPORT_STAFF_ROLES, permission: 'student.view' },
  class_sessions: { anyRole: EXPORT_STAFF_ROLES, permission: 'schedule.view' },
  expenses: { anyRole: EXPORT_STAFF_ROLES, permission: 'expense.view' },
} as const satisfies Record<ExportDatasetCode, AccessRequirement>;

/** Whether `user` may export `dataset` (#124 A4's table). */
export function mayExport(
  user: SessionUser | null | undefined,
  dataset: ExportDatasetCode,
): boolean {
  return canAccess(user, EXPORT_DATASET_ACCESS[dataset]);
}
