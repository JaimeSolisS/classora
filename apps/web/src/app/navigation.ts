import type { ParseKeys } from 'i18next';
import {
  ArchiveRestore,
  Banknote,
  BookOpen,
  BookUser,
  CalendarClock,
  CalendarDays,
  ChartColumn,
  ClipboardCheck,
  ClipboardList,
  Contact,
  CreditCard,
  FileClock,
  FileDown,
  FileText,
  FileUp,
  GitPullRequestArrow,
  GraduationCap,
  HandCoins,
  Handshake,
  House,
  KeyRound,
  Library,
  MailPlus,
  MessageSquareText,
  NotebookPen,
  NotebookText,
  Receipt,
  Route,
  School,
  ScrollText,
  Send,
  ServerCrash,
  Settings,
  TrendingUp,
  UserCog,
  Users,
  UsersRound,
  Wallet,
  type LucideIcon,
} from 'lucide-react';
import { APPROVAL_REVIEW_PERMISSIONS } from '@/features/approvals/api';
import type { AccessRequirement } from '@/features/auth/access';
import { BILLING_ACCESS } from '@/features/billing/access';
import { EXPENSE_CONFIGURE_ACCESS } from '@/features/finance/access';
import { KPI_PAGE_ACCESS } from '@/features/reporting/access';
import { IMPORTS_ACCESS } from '@/features/imports/access';
import { EXPORTS_ACCESS } from '@/features/exports/access';
import { CHANGE_REQUESTS_ACCESS } from '@/features/change-requests/access';
import { FAMILY_BILLING_ACCESS } from '@/features/family-portal/billing/access';
import { SESSION_REQUESTS_ACCESS } from '@/features/family-portal/requests/access';
import { MY_COURSES_ACCESS } from '@/features/courses/cohorts/access';
import { GROUPS_ACCESS } from '@/features/groups/access';
import { MY_SCHEDULE_ACCESS } from '@/features/my-schedule/access';
import { MY_PAY_ACCESS, PAYROLL_ANY_ACCESS } from '@/features/payroll/access';
import { MY_HISTORY_ACCESS } from '@/features/academics/history/access';
import { MY_LEARNING_PLANS_ACCESS } from '@/features/learning-plans/access';
import { MY_PROGRESS_ACCESS } from '@/features/progress/access';
import {
  MY_PROGRESS_REPORTS_ACCESS,
  PROGRESS_REPORT_WORK_ACCESS,
} from '@/features/progress-reports/access';
import { RECENTLY_DELETED_ACCESS } from '@/features/recently-deleted/access';
import {
  ASSIGNMENT_WORK_ACCESS,
  MY_HOMEWORK_ACCESS,
} from '@/features/assignments/access';

// The navigation items (#28 F4), shown in the sidebar (#546). AppLayout
// shows only the items the signed-in user may open (canAccess, the same check
// as the route guard).
// UX only: the API checks every request. Feature issues add their own items
// here, with the `permission` (or `anyPermission`, `role`, `anyRole` or
// `anyOf`) their route requires.

// Sidebar sections (#546 B2, E3), in display order. Each one is a contiguous
// run of NAV_ITEMS, so the sections keep the array's order.
export const NAV_SECTIONS = [
  'administration',
  'finance',
  'people',
  'academics',
  'mine',
] as const;
export type NavSection = (typeof NAV_SECTIONS)[number];

export interface NavItem extends AccessRequirement {
  to: string;
  /** Key in the `common` catalog. */
  labelKey: ParseKeys<'common'>;
  /** Matches only the exact path (the home item). */
  end?: boolean;
  /** The sidebar icon (#546 C1): aria-hidden, distinct per item. */
  icon: LucideIcon;
  /** The sidebar section (#546 B2); only Home has none. */
  section?: NavSection;
}

export const NAV_ITEMS: readonly NavItem[] = [
  { to: '/', labelKey: 'nav.home', icon: House, end: true },
  {
    to: '/users',
    labelKey: 'nav.users',
    icon: UserCog,
    section: 'administration',
    permission: 'user.view',
  },
  {
    to: '/invitations',
    labelKey: 'nav.invitations',
    icon: MailPlus,
    section: 'administration',
    permission: 'user.invite',
  },
  // CSV imports (M8-05b E1): holders of a permission of some import kind,
  // after Invitations. The page lists only the kinds they may import.
  {
    to: '/imports',
    labelKey: 'nav.imports',
    icon: FileUp,
    section: 'administration',
    ...IMPORTS_ACCESS,
  },
  // CSV/Excel exports (M8-07b E1): any staff role, after Imports. The page
  // offers only the datasets the user may export.
  {
    to: '/exports',
    labelKey: 'nav.exports',
    icon: FileDown,
    section: 'administration',
    ...EXPORTS_ACCESS,
  },
  {
    to: '/password-resets',
    labelKey: 'nav.passwordResets',
    icon: KeyRound,
    section: 'administration',
    permission: 'user.password_reset.issue',
  },
  // One review permission per approval type (#40 F1).
  {
    to: '/approvals',
    labelKey: 'nav.approvals',
    icon: ClipboardCheck,
    section: 'administration',
    anyPermission: APPROVAL_REVIEW_PERMISSIONS,
  },
  // The read-only audit log (M8-03 #120 D1): audit.view (Owner,
  // Administrator), after Approvals.
  {
    to: '/audit',
    labelKey: 'nav.audit',
    icon: ScrollText,
    section: 'administration',
    permission: 'audit.view',
  },
  // Failed background jobs (M8-11 #128 G1): jobs.manage (Owner,
  // Administrator), after the audit log.
  {
    to: '/jobs',
    labelKey: 'nav.jobs',
    icon: ServerCrash,
    section: 'administration',
    permission: 'jobs.manage',
  },
  {
    to: '/catalog/education-levels',
    labelKey: 'nav.catalog',
    icon: Library,
    section: 'administration',
    permission: 'catalog.manage',
  },
  {
    to: '/settings',
    labelKey: 'nav.settings',
    icon: Settings,
    section: 'administration',
    permission: 'settings.manage',
  },
  // Billing (#363 A2, #366 A2): billing.view or billing.manage holders.
  // /billing opens the first section of the Billing tabs they may open.
  {
    to: '/billing',
    labelKey: 'nav.billing',
    icon: Receipt,
    section: 'finance',
    ...BILLING_ACCESS,
  },
  // Payroll (#425 A2): payroll.view or payroll.manage holders, next to
  // Billing. /payroll opens the tutor list. #110b A2: a delegated
  // Administrator (payroll.finalize) too, for whom it opens the periods.
  {
    to: '/payroll',
    labelKey: 'nav.payroll',
    icon: Wallet,
    section: 'finance',
    ...PAYROLL_ANY_ACCESS,
  },
  // Expenses (M7-11b A2): expense.configure holders (Owner, Administrator,
  // Finance Staff), after Payroll. /expenses opens the first tab they may open.
  {
    to: '/expenses',
    labelKey: 'nav.expenses',
    icon: HandCoins,
    section: 'finance',
    ...EXPENSE_CONFIGURE_ACCESS,
  },
  // Reports (M7-14b A1, M8-09b A1): holders of finance.report.view or
  // schedule.view (Owner, Finance Staff, Administrator, Coordinator), after
  // Expenses. /reports opens the KPIs.
  {
    to: '/reports',
    labelKey: 'nav.reports',
    icon: ChartColumn,
    section: 'finance',
    ...KPI_PAGE_ACCESS,
  },
  // Every role holds student.view; the API filters the list (#33 I1).
  {
    to: '/students',
    labelKey: 'nav.students',
    icon: GraduationCap,
    section: 'people',
    permission: 'student.view',
  },
  // Guardians see their own profile only (#34 I1).
  {
    to: '/guardians',
    labelKey: 'nav.guardians',
    icon: Contact,
    section: 'people',
    permission: 'guardian.view',
  },
  // Guardians see only their own family (#172 H1; there is no family.view).
  {
    to: '/families',
    labelKey: 'nav.families',
    icon: Users,
    section: 'people',
    permission: 'guardian.view',
  },
  // Tutors see only their own profile (#43 E1).
  {
    to: '/tutors',
    labelKey: 'nav.tutors',
    icon: BookUser,
    section: 'people',
    permission: 'tutor.view',
  },
  // Tutoring groups (#234 A2, #246 B1): staff with schedule.view, and
  // students, guardians and tutors (their own groups).
  {
    to: '/groups',
    labelKey: 'nav.groups',
    icon: UsersRound,
    section: 'academics',
    ...GROUPS_ACCESS,
  },
  // Course templates (#64 G1): Finance Staff read them.
  {
    to: '/courses',
    labelKey: 'nav.courses',
    icon: BookOpen,
    section: 'academics',
    permission: 'course.view',
  },
  // My courses (#246 B2): the cohorts a tutor teaches or a student or
  // guardian takes (the staff "Courses" item keeps landing on templates).
  {
    to: '/courses/cohorts',
    labelKey: 'nav.myCourses',
    icon: School,
    section: 'academics',
    ...MY_COURSES_ACCESS,
  },
  // Evaluation templates (#308 A2): evaluation.manage holders (Owner,
  // Administrator, Academic Coordinator).
  {
    to: '/academics/evaluation-templates',
    labelKey: 'nav.evaluationTemplates',
    icon: ClipboardList,
    section: 'academics',
    permission: 'evaluation.manage',
  },
  // The calendar (#57 D1): every schedule.view holder (Coordinators and
  // Finance too). "Schedule a session" (/schedule/new) is linked from it.
  {
    to: '/schedule',
    labelKey: 'nav.schedule',
    icon: CalendarDays,
    section: 'academics',
    permission: 'schedule.view',
  },
  // My schedule (#67 E1): tutors, students and guardians (the API decides
  // whose schedule each may read). Staff-only users use /schedule.
  {
    to: '/my-schedule',
    labelKey: 'nav.mySchedule',
    icon: CalendarClock,
    section: 'academics',
    ...MY_SCHEDULE_ACCESS,
  },
  // Academic history (#308 A2): students and guardians (the API decides
  // whose history each may read). Staff and tutors open a student's
  // history from the student page and My schedule.
  {
    to: '/my-history',
    labelKey: 'nav.academicHistory',
    icon: FileClock,
    section: 'academics',
    ...MY_HISTORY_ACCESS,
  },
  // Learning plans (#310 A2): students and guardians (the API decides
  // whose plans each may read). Staff and tutors open a student's plans
  // from the student page and My schedule; no staff or tutor item.
  {
    to: '/my-learning-plans',
    labelKey: 'nav.learningPlans',
    icon: Route,
    section: 'academics',
    ...MY_LEARNING_PLANS_ACCESS,
  },
  // Assigned homework (#313 A2): tutors (the homework they set) and
  // assignment.review holders (every one; the API decides).
  {
    to: '/assignments',
    labelKey: 'nav.assignedHomework',
    icon: NotebookPen,
    section: 'academics',
    ...ASSIGNMENT_WORK_ACCESS,
  },
  // Homework (#313 A2): students and guardians (the API decides whose
  // homework each may read); students hand in work there.
  {
    to: '/my-homework',
    labelKey: 'nav.homework',
    icon: NotebookText,
    section: 'academics',
    ...MY_HOMEWORK_ACCESS,
  },
  // Progress reports (#315 A2): tutors (the reports they write) and
  // progress_report.review holders (the review queue; the API decides).
  {
    to: '/progress-reports',
    labelKey: 'nav.progressReports',
    icon: FileText,
    section: 'academics',
    ...PROGRESS_REPORT_WORK_ACCESS,
  },
  // Recently deleted (M8-12 #129 D1): the users who can delete an eligible
  // record (tutors, learning_plan.manage holders), after the tutor's
  // academic items. The API lists only the caller's own records.
  {
    to: '/recently-deleted',
    labelKey: 'nav.recentlyDeleted',
    icon: ArchiveRestore,
    section: 'academics',
    ...RECENTLY_DELETED_ACCESS,
  },
  // Tutor reports (#315 A2): students and guardians read the approved
  // reports (a different label, so a tutor who is also a guardian can tell
  // the two items apart).
  {
    to: '/my-progress-reports',
    labelKey: 'nav.tutorReports',
    icon: MessageSquareText,
    section: 'academics',
    ...MY_PROGRESS_REPORTS_ACCESS,
  },
  // Progress (#322 A4): students and guardians (per child; the API
  // decides). Staff and tutors open a student's progress from the student
  // page and My schedule; no staff or tutor item.
  {
    to: '/my-progress',
    labelKey: 'nav.progress',
    icon: TrendingUp,
    section: 'academics',
    ...MY_PROGRESS_ACCESS,
  },
  // The tutor's offers (#52 H1): tutors only (the API lists the caller's).
  {
    to: '/offers',
    labelKey: 'nav.offers',
    icon: Handshake,
    section: 'mine',
    role: 'tutor',
  },
  // My pay (#425 A2): tutors read their own terms and rates.
  {
    to: '/my-pay',
    labelKey: 'nav.myPay',
    icon: Banknote,
    section: 'mine',
    ...MY_PAY_ACCESS,
  },
  // Session requests (#59 F1): guardians and students (the API decides
  // which students).
  {
    to: '/requests',
    labelKey: 'nav.sessionRequests',
    icon: Send,
    section: 'mine',
    ...SESSION_REQUESTS_ACCESS,
  },
  // My change requests (#62b B1): guardians, students and tutors (the API
  // decides which requests each sees).
  {
    to: '/change-requests',
    labelKey: 'nav.changeRequests',
    icon: GitPullRequestArrow,
    section: 'mine',
    ...CHANGE_REQUESTS_ACCESS,
  },
  // Family billing (#388 A1): guardians only, after the other family
  // items. A different label from staff "Billing", so a staff user who is
  // also a guardian can tell the two apart (the #315 precedent).
  {
    to: '/my-billing',
    labelKey: 'nav.familyBilling',
    icon: CreditCard,
    section: 'mine',
    ...FAMILY_BILLING_ACCESS,
  },
];
