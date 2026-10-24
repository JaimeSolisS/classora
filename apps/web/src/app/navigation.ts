import type { ParseKeys } from 'i18next';
import { APPROVAL_REVIEW_PERMISSIONS } from '@/features/approvals/api';
import type { AccessRequirement } from '@/features/auth/access';
import { SESSION_REQUESTS_ACCESS } from '@/features/family-portal/requests/access';
import { GROUPS_ACCESS } from '@/features/groups/access';
import { MY_SCHEDULE_ACCESS } from '@/features/my-schedule/access';

// The header navigation items (#28 F4). AppLayout shows only the items the
// signed-in user may open (canAccess, the same check as the route guard).
// UX only: the API checks every request. Feature issues add their own items
// here, with the `permission` (or `anyPermission`, `role`, `anyRole` or
// `anyOf`) their route requires.

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
  // One review permission per approval type (#40 F1).
  {
    to: '/approvals',
    labelKey: 'nav.approvals',
    anyPermission: APPROVAL_REVIEW_PERMISSIONS,
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
  // Tutors see only their own profile (#43 E1).
  { to: '/tutors', labelKey: 'nav.tutors', permission: 'tutor.view' },
  // Tutoring groups (#234 A2): staff with schedule.view, and students (their
  // own groups). Tutors and guardians: #67.
  { to: '/groups', labelKey: 'nav.groups', ...GROUPS_ACCESS },
  // Course templates (#64 G1): Finance Staff read them.
  { to: '/courses', labelKey: 'nav.courses', permission: 'course.view' },
  // The calendar (#57 D1): every schedule.view holder (Coordinators and
  // Finance too). "Schedule a session" (/schedule/new) is linked from it.
  { to: '/schedule', labelKey: 'nav.schedule', permission: 'schedule.view' },
  // My schedule (#67 E1): tutors, students and guardians (the API decides
  // whose schedule each may read). Staff-only users use /schedule.
  { to: '/my-schedule', labelKey: 'nav.mySchedule', ...MY_SCHEDULE_ACCESS },
  // The tutor's offers (#52 H1): tutors only (the API lists the caller's).
  { to: '/offers', labelKey: 'nav.offers', role: 'tutor' },
  // Session requests (#59 F1): guardians and students (the API decides
  // which students).
  {
    to: '/requests',
    labelKey: 'nav.sessionRequests',
    ...SESSION_REQUESTS_ACCESS,
  },
];
