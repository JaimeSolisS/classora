import type { ParseKeys } from 'i18next';
import { APPROVAL_REVIEW_PERMISSIONS } from '@/features/approvals/api';
import type { AccessRequirement } from '@/features/auth/access';
import { CHANGE_REQUESTS_ACCESS } from '@/features/change-requests/access';
import { SESSION_REQUESTS_ACCESS } from '@/features/family-portal/requests/access';
import { MY_COURSES_ACCESS } from '@/features/courses/cohorts/access';
import { GROUPS_ACCESS } from '@/features/groups/access';
import { MY_SCHEDULE_ACCESS } from '@/features/my-schedule/access';
import { MY_HISTORY_ACCESS } from '@/features/academics/history/access';
import { MY_LEARNING_PLANS_ACCESS } from '@/features/learning-plans/access';
import {
  ASSIGNMENT_WORK_ACCESS,
  MY_HOMEWORK_ACCESS,
} from '@/features/assignments/access';

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
  // Tutoring groups (#234 A2, #246 B1): staff with schedule.view, and
  // students, guardians and tutors (their own groups).
  { to: '/groups', labelKey: 'nav.groups', ...GROUPS_ACCESS },
  // Course templates (#64 G1): Finance Staff read them.
  { to: '/courses', labelKey: 'nav.courses', permission: 'course.view' },
  // My courses (#246 B2): the cohorts a tutor teaches or a student or
  // guardian takes (the staff "Courses" item keeps landing on templates).
  { to: '/courses/cohorts', labelKey: 'nav.myCourses', ...MY_COURSES_ACCESS },
  // Evaluation templates (#308 A2): evaluation.manage holders (Owner,
  // Administrator, Academic Coordinator).
  {
    to: '/academics/evaluation-templates',
    labelKey: 'nav.evaluationTemplates',
    permission: 'evaluation.manage',
  },
  // The calendar (#57 D1): every schedule.view holder (Coordinators and
  // Finance too). "Schedule a session" (/schedule/new) is linked from it.
  { to: '/schedule', labelKey: 'nav.schedule', permission: 'schedule.view' },
  // My schedule (#67 E1): tutors, students and guardians (the API decides
  // whose schedule each may read). Staff-only users use /schedule.
  { to: '/my-schedule', labelKey: 'nav.mySchedule', ...MY_SCHEDULE_ACCESS },
  // Academic history (#308 A2): students and guardians (the API decides
  // whose history each may read). Staff and tutors open a student's
  // history from the student page and My schedule.
  {
    to: '/my-history',
    labelKey: 'nav.academicHistory',
    ...MY_HISTORY_ACCESS,
  },
  // Learning plans (#310 A2): students and guardians (the API decides
  // whose plans each may read). Staff and tutors open a student's plans
  // from the student page and My schedule; no staff or tutor item.
  {
    to: '/my-learning-plans',
    labelKey: 'nav.learningPlans',
    ...MY_LEARNING_PLANS_ACCESS,
  },
  // Assigned homework (#313 A2): tutors (the homework they set) and
  // assignment.review holders (every one; the API decides).
  {
    to: '/assignments',
    labelKey: 'nav.assignedHomework',
    ...ASSIGNMENT_WORK_ACCESS,
  },
  // Homework (#313 A2): students and guardians (the API decides whose
  // homework each may read); students hand in work there.
  { to: '/my-homework', labelKey: 'nav.homework', ...MY_HOMEWORK_ACCESS },
  // The tutor's offers (#52 H1): tutors only (the API lists the caller's).
  { to: '/offers', labelKey: 'nav.offers', role: 'tutor' },
  // Session requests (#59 F1): guardians and students (the API decides
  // which students).
  {
    to: '/requests',
    labelKey: 'nav.sessionRequests',
    ...SESSION_REQUESTS_ACCESS,
  },
  // My change requests (#62b B1): guardians, students and tutors (the API
  // decides which requests each sees).
  {
    to: '/change-requests',
    labelKey: 'nav.changeRequests',
    ...CHANGE_REQUESTS_ACCESS,
  },
];
