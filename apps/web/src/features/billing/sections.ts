import type { ParseKeys } from 'i18next';
import { canAccess, type AccessRequirement } from '@/features/auth/access';
import type { SessionUser } from '@/features/auth/session';

// The Billing tabs' sections (#363 A2, #366 A1), in tab order, each with
// the access its route requires. Later billing pages (tuition plans,
// packages, discounts, payments) add their section here.

export interface BillingSection extends AccessRequirement {
  to: string;
  labelKey: ParseKeys<'common'>;
}

export const BILLING_SECTIONS: readonly BillingSection[] = [
  {
    to: '/billing/prices',
    labelKey: 'billing.tabs.prices',
    permission: 'billing.manage',
  },
  // #367 A2: discounts and custom prices, after Prices.
  {
    to: '/billing/discounts',
    labelKey: 'billing.tabs.discounts',
    permission: 'billing.manage',
  },
  {
    to: '/billing/custom-prices',
    labelKey: 'billing.tabs.customPrices',
    permission: 'billing.manage',
  },
  {
    to: '/billing/unbilled',
    labelKey: 'billing.tabs.unbilled',
    permission: 'billing.view',
  },
  {
    to: '/billing/settings',
    labelKey: 'billing.tabs.settings',
    permission: 'billing.view',
  },
];

/** The sections `user` may open, in tab order. */
export function billingSectionsFor(
  user: SessionUser | null | undefined,
): BillingSection[] {
  return BILLING_SECTIONS.filter((section) => canAccess(user, section));
}
