import { canAccess } from '@/features/auth/access';
import type { PermissionCode, SessionUser } from '@/features/auth/session';

// The "More settings" links (M8-04b D1): the central settings that live in
// their own areas (spec §9), each with the permission of its route's handle.

export interface MoreSettingsLink {
  to: string;
  permission: PermissionCode;
  labelKey:
    | 'settings.more.periods'
    | 'settings.more.prices'
    | 'settings.more.billing'
    | 'settings.more.packages';
  descriptionKey:
    | 'settings.more.periodsHelp'
    | 'settings.more.pricesHelp'
    | 'settings.more.billingHelp'
    | 'settings.more.packagesHelp';
}

export const MORE_SETTINGS_LINKS: readonly MoreSettingsLink[] = [
  {
    to: '/catalog/periods',
    permission: 'catalog.manage',
    labelKey: 'settings.more.periods',
    descriptionKey: 'settings.more.periodsHelp',
  },
  {
    to: '/billing/prices',
    permission: 'billing.manage',
    labelKey: 'settings.more.prices',
    descriptionKey: 'settings.more.pricesHelp',
  },
  {
    to: '/billing/settings',
    permission: 'billing.view',
    labelKey: 'settings.more.billing',
    descriptionKey: 'settings.more.billingHelp',
  },
  {
    to: '/billing/package-products',
    permission: 'billing.manage',
    labelKey: 'settings.more.packages',
    descriptionKey: 'settings.more.packagesHelp',
  },
];

/** The links `user` may open, in order. */
export function visibleMoreLinks(
  user: SessionUser | null | undefined,
): MoreSettingsLink[] {
  return MORE_SETTINGS_LINKS.filter((link) =>
    canAccess(user, { permission: link.permission }),
  );
}
