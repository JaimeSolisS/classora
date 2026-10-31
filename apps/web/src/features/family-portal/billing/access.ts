import type { AccessRequirement } from '@/features/auth/access';

// Who opens the family billing portal (#388 A1): guardians only (spec §6,
// ADR-0006 E4: no student or tutor billing page). UX only: the API's
// record policy (#364) decides which family's account each may read.
export const FAMILY_BILLING_ACCESS = {
  anyRole: ['guardian'],
} as const satisfies AccessRequirement;

/** The portal page, with an optional charge filter (#388 H1). */
export function familyBillingPath(
  status?: 'open' | 'overdue' | 'paid' | 'all',
): string {
  return status ? `/my-billing?status=${status}` : '/my-billing';
}
