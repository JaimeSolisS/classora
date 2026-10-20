import type { ParseKeys } from 'i18next';
import type { AccessRequirement } from '@/features/auth/access';

// The header navigation items (#28 F4). AppLayout shows only the items the
// signed-in user may open (canAccess, the same check as the route guard).
// UX only: the API checks every request. Feature issues add their own items
// here, with the `permission` their route requires.

export interface NavItem extends AccessRequirement {
  to: string;
  /** Key in the `common` catalog. */
  labelKey: ParseKeys<'common'>;
  /** Matches only the exact path (the home item). */
  end?: boolean;
}

export const NAV_ITEMS: readonly NavItem[] = [
  { to: '/', labelKey: 'nav.home', end: true },
  { to: '/users', labelKey: 'nav.users', permission: 'user.view' },
  {
    to: '/invitations',
    labelKey: 'nav.invitations',
    permission: 'user.invite',
  },
  {
    to: '/password-resets',
    labelKey: 'nav.passwordResets',
    permission: 'user.password_reset.issue',
  },
  {
    to: '/registration-requests',
    labelKey: 'nav.registrations',
    permission: 'registration.review',
  },
  {
    to: '/catalog/education-levels',
    labelKey: 'nav.catalog',
    permission: 'catalog.manage',
  },
  { to: '/settings', labelKey: 'nav.settings', permission: 'settings.manage' },
  // Every role holds student.view; the API filters the list (#33 I1).
  { to: '/students', labelKey: 'nav.students', permission: 'student.view' },
  // Guardians see their own profile only (#34 I1).
  { to: '/guardians', labelKey: 'nav.guardians', permission: 'guardian.view' },
  // Guardians see only their own family (#172 H1; there is no family.view).
  { to: '/families', labelKey: 'nav.families', permission: 'guardian.view' },
];
