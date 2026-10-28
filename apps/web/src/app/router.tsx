import { createBrowserRouter, Navigate, type RouteObject } from 'react-router';
import { AuthProvider } from '@/features/auth/AuthProvider';
import { LoginPage } from '@/features/auth/LoginPage';
import { ClassroomsPage } from '@/features/catalog/ClassroomsPage';
import { ClosuresPage } from '@/features/catalog/ClosuresPage';
import { LevelPage } from '@/features/catalog/LevelPage';
import { LevelsPage } from '@/features/catalog/LevelsPage';
import { PeriodsPage } from '@/features/catalog/PeriodsPage';
import { ChangePasswordPage } from '@/features/account/password/ChangePasswordPage';
import { InvitePage, ResetPage } from '@/features/auth/redeem/RedeemLinkPage';
import { RegisterPage } from '@/features/auth/register/RegisterPage';
import { SchedulingPoliciesPage } from '@/features/settings/SchedulingPoliciesPage';
import { SettingsPage } from '@/features/settings/SettingsPage';
import {
  CourseTemplateEditPage,
  NewCourseTemplatePage,
} from '@/features/courses/CourseTemplatePages';
import { CourseTemplatesPage } from '@/features/courses/CourseTemplatesPage';
import { FamiliesPage } from '@/features/families/FamiliesPage';
import { FamilyDetailPage } from '@/features/families/FamilyDetailPage';
import {
  EditFamilyPage,
  NewFamilyPage,
} from '@/features/families/FamilyFormPages';
import { GuardianDetailPage } from '@/features/guardians/GuardianDetailPage';
import {
  EditGuardianPage,
  NewGuardianPage,
} from '@/features/guardians/GuardianFormPages';
import { GuardiansPage } from '@/features/guardians/GuardiansPage';
import { APPROVAL_REVIEW_PERMISSIONS } from '@/features/approvals/api';
import { ApprovalsPage } from '@/features/approvals/ApprovalsPage';
import { PrivacyNoticePage } from '@/features/privacy-notice/PrivacyNoticePage';
import { StudentDetailPage } from '@/features/students/StudentDetailPage';
import {
  EditStudentPage,
  NewStudentPage,
} from '@/features/students/StudentFormPages';
import { StudentsPage } from '@/features/students/StudentsPage';
import { ScheduleSessionPage } from '@/features/scheduling/create/ScheduleSessionPage';
import { SessionPage } from '@/features/scheduling/session/SessionPage';
import { OffersPage } from '@/features/tutor-portal/offers/OffersPage';
import { TutorDetailPage } from '@/features/tutors/TutorDetailPage';
import { TutorsPage } from '@/features/tutors/TutorsPage';
import { InvitationsPage } from '@/features/users/invitations/InvitationsPage';
import { PasswordResetsPage } from '@/features/users/password-resets/PasswordResetsPage';
import { UserDetailPage } from '@/features/users/admin/UserDetailPage';
import { UsersPage } from '@/features/users/admin/UsersPage';
import { HomePage } from '@/pages/HomePage';
import { NotFoundPage } from '@/pages/NotFoundPage';
import { RootErrorPage } from '@/pages/RootErrorPage';
import { RouteErrorPage } from '@/pages/RouteErrorPage';
import { AppLayout } from './layout/AppLayout';
import { CHANGE_REQUESTS_ACCESS } from '@/features/change-requests/access';
import { SESSION_REQUESTS_ACCESS } from '@/features/family-portal/requests/access';
import { MY_SCHEDULE_ACCESS } from '@/features/my-schedule/access';
import { MY_HISTORY_ACCESS } from '@/features/academics/history/access';
import {
  ASSIGNMENT_FORM_ACCESS,
  ASSIGNMENT_ROUTE_ACCESS,
  MY_HOMEWORK_ACCESS,
} from '@/features/assignments/access';
import {
  LEARNING_PLAN_ROUTE_ACCESS,
  MY_LEARNING_PLANS_ACCESS,
} from '@/features/learning-plans/access';
import {
  MY_PROGRESS_REPORTS_ACCESS,
  PROGRESS_REPORT_ROUTE_ACCESS,
  PROGRESS_REPORT_START_ACCESS,
} from '@/features/progress-reports/access';
import {
  MY_PROGRESS_ACCESS,
  STUDENT_PROGRESS_ACCESS,
} from '@/features/progress/access';
import { COHORTS_ACCESS } from '@/features/courses/cohorts/access';
import { GROUPS_ACCESS } from '@/features/groups/access';
import { PRIVACY_NOTICE_ROUTE_ID, RouteGuard } from './RouteGuard';

// React Router in data mode. Server data is fetched with TanStack Query
// hooks, never with route loaders or actions (architecture §8, ISSUE-X4).
// No SPA route starts with /api or /health: the dev server proxies those to
// the API.
//
// Access (#28 F1, UX only: the API checks every request): the public pages
// (/login, /invite, /reset, /register) and the error elements are the only
// things outside the guard. Every other page goes
// under the guard route and needs a session; a page that needs a permission
// declares `handle: { permission: '…' }` (or `anyPermission: [...]`). A unit test fails if a page is
// added outside the guard. Until the user accepts the current privacy notice
// (#174), the guard shows /privacy-notice instead of any other page.
export const routes: RouteObject[] = [
  {
    id: 'root',
    path: '/',
    element: (
      <AuthProvider>
        <AppLayout />
      </AuthProvider>
    ),
    // Errors in the layout itself: rendered without the layout.
    errorElement: <RootErrorPage />,
    children: [
      // Public pages, outside the guard.
      {
        id: 'login',
        path: 'login',
        element: <LoginPage />,
        errorElement: <RouteErrorPage />,
      },
      {
        id: 'invite',
        path: 'invite',
        element: <InvitePage />,
        errorElement: <RouteErrorPage />,
      },
      {
        id: 'reset',
        path: 'reset',
        element: <ResetPage />,
        errorElement: <RouteErrorPage />,
      },
      {
        id: 'register',
        path: 'register',
        element: <RegisterPage />,
        errorElement: <RouteErrorPage />,
      },
      {
        id: 'guard',
        element: <RouteGuard />,
        // Errors in a page: rendered inside the layout.
        errorElement: <RouteErrorPage />,
        children: [
          { index: true, element: <HomePage /> },
          {
            path: 'users',
            element: <UsersPage />,
            handle: { permission: 'user.view' },
          },
          {
            path: 'users/:userId',
            element: <UserDetailPage />,
            handle: { permission: 'user.view' },
          },
          {
            path: 'invitations',
            element: <InvitationsPage />,
            handle: { permission: 'user.invite' },
          },
          {
            path: 'password-resets',
            element: <PasswordResetsPage />,
            handle: { permission: 'user.password_reset.issue' },
          },
          {
            path: 'approvals',
            element: <ApprovalsPage />,
            handle: { anyPermission: APPROVAL_REVIEW_PERMISSIONS },
          },
          // The former account-requests page (#169): bookmarks keep working
          // (#40 F2). /approvals checks the access.
          {
            path: 'registration-requests',
            element: (
              <Navigate to="/approvals?type=identity.registration" replace />
            ),
          },
          {
            path: 'catalog/education-levels',
            element: <LevelsPage />,
            handle: { permission: 'catalog.manage' },
          },
          {
            path: 'catalog/education-levels/:levelId',
            element: <LevelPage />,
            handle: { permission: 'catalog.manage' },
          },
          {
            path: 'catalog/periods',
            element: <PeriodsPage />,
            handle: { permission: 'catalog.manage' },
          },
          {
            path: 'catalog/closures',
            element: <ClosuresPage />,
            handle: { permission: 'catalog.manage' },
          },
          {
            path: 'catalog/classrooms',
            element: <ClassroomsPage />,
            handle: { permission: 'catalog.manage' },
          },
          {
            path: 'students',
            element: <StudentsPage />,
            handle: { permission: 'student.view' },
          },
          {
            path: 'students/new',
            element: <NewStudentPage />,
            handle: { permission: 'student.manage' },
          },
          {
            path: 'students/:studentId',
            element: <StudentDetailPage />,
            handle: { permission: 'student.view' },
          },
          {
            path: 'students/:studentId/edit',
            element: <EditStudentPage />,
            handle: { permission: 'student.manage' },
          },
          {
            path: 'guardians',
            element: <GuardiansPage />,
            handle: { permission: 'guardian.view' },
          },
          {
            path: 'guardians/new',
            element: <NewGuardianPage />,
            handle: { permission: 'family.manage' },
          },
          {
            path: 'guardians/:guardianId',
            element: <GuardianDetailPage />,
            handle: { permission: 'guardian.view' },
          },
          {
            path: 'guardians/:guardianId/edit',
            element: <EditGuardianPage />,
            handle: { permission: 'family.manage' },
          },
          {
            path: 'families',
            element: <FamiliesPage />,
            handle: { permission: 'guardian.view' },
          },
          {
            path: 'families/new',
            element: <NewFamilyPage />,
            handle: { permission: 'family.manage' },
          },
          {
            path: 'families/:familyId',
            element: <FamilyDetailPage />,
            handle: { permission: 'guardian.view' },
          },
          {
            path: 'families/:familyId/edit',
            element: <EditFamilyPage />,
            handle: { permission: 'family.manage' },
          },
          {
            path: 'tutors',
            element: <TutorsPage />,
            handle: { permission: 'tutor.view' },
          },
          {
            path: 'tutors/:userId',
            element: <TutorDetailPage />,
            handle: { permission: 'tutor.view' },
          },
          // The calendar (#57 D1, C4): code-split, so FullCalendar and the
          // Temporal polyfill are not in the entry chunk.
          {
            path: 'schedule',
            lazy: async () => ({
              Component: (await import('@/features/calendar/CalendarPage'))
                .CalendarPage,
            }),
            handle: { permission: 'schedule.view' },
          },
          {
            path: 'schedule/new',
            element: <ScheduleSessionPage />,
            handle: { permission: 'schedule.manage' },
          },
          // #301b A1: the classes that ended without a closeout
          // (code-split; linked from the Schedule page).
          {
            path: 'schedule/overdue-closeouts',
            lazy: async () => ({
              Component: (
                await import('@/features/scheduling/overdue/OverdueCloseoutsPage')
              ).OverdueCloseoutsPage,
            }),
            handle: { permission: 'teaching_hours.approve' },
          },
          // #52 H2: the session page (its offer panel needs schedule.assign).
          {
            path: 'schedule/sessions/:sessionId',
            element: <SessionPage />,
            handle: { permission: 'schedule.view' },
          },
          // #54b B1: a weekly series (code-split; its actions need
          // schedule.manage).
          {
            path: 'schedule/series/:seriesId',
            lazy: async () => ({
              Component: (
                await import('@/features/scheduling/series/SeriesPage')
              ).SeriesPage,
            }),
            handle: { permission: 'schedule.view' },
          },
          // #60 H1: the staff review of a family's session request
          // (code-split; linked from the inbox's "Review").
          {
            path: 'schedule/requests/:requestId',
            lazy: async () => ({
              Component: (
                await import('@/features/scheduling/request-review/RequestReviewPage')
              ).RequestReviewPage,
            }),
            handle: { permission: 'schedule.manage' },
          },
          // #62 G1: the staff review of a family's or tutor's change
          // request (code-split; linked from the inbox's "Review" and the
          // session page).
          {
            path: 'schedule/change-requests/:requestId',
            lazy: async () => ({
              Component: (
                await import('@/features/scheduling/change-requests/ChangeRequestReviewPage')
              ).ChangeRequestReviewPage,
            }),
            handle: { permission: 'schedule.manage' },
          },
          // #52 H1: the tutor's own offers (the API filters by caller).
          {
            path: 'offers',
            element: <OffersPage />,
            handle: { role: 'tutor' },
          },
          {
            path: 'courses',
            element: <CourseTemplatesPage />,
            handle: { permission: 'course.view' },
          },
          {
            path: 'courses/templates/new',
            element: <NewCourseTemplatePage />,
            handle: { permission: 'course.manage' },
          },
          {
            path: 'courses/templates/:templateId',
            element: <CourseTemplateEditPage />,
            handle: { permission: 'course.view' },
          },
          // Course cohorts (#243 A2, #246 B2, code-split): course.view
          // holders browse them, tutors, students and guardians their own
          // (the API decides); the create form needs schedule.manage.
          {
            path: 'courses/cohorts',
            lazy: async () => ({
              Component: (
                await import('@/features/courses/cohorts/CohortsPage')
              ).CohortsPage,
            }),
            handle: COHORTS_ACCESS,
          },
          {
            path: 'courses/cohorts/new',
            lazy: async () => ({
              Component: (
                await import('@/features/courses/cohorts/create/NewCohortPage')
              ).NewCohortPage,
            }),
            handle: { permission: 'schedule.manage' },
          },
          {
            path: 'courses/cohorts/:cohortId',
            lazy: async () => ({
              Component: (await import('@/features/courses/cohorts/CohortPage'))
                .CohortPage,
            }),
            handle: COHORTS_ACCESS,
          },
          {
            path: 'settings',
            element: <SettingsPage />,
            handle: { permission: 'settings.manage' },
          },
          {
            path: 'settings/scheduling-policies',
            element: <SchedulingPoliciesPage />,
            handle: { permission: 'settings.manage' },
          },
          // Tutoring groups (#234 A3, #246 B1, code-split): staff with
          // schedule.view, or students, guardians and tutors (their own
          // groups; the API decides). The create form needs schedule.manage.
          {
            path: 'groups',
            lazy: async () => ({
              Component: (await import('@/features/groups/GroupsPage'))
                .GroupsPage,
            }),
            handle: GROUPS_ACCESS,
          },
          {
            path: 'groups/new',
            lazy: async () => ({
              Component: (await import('@/features/groups/create/NewGroupPage'))
                .NewGroupPage,
            }),
            handle: { permission: 'schedule.manage' },
          },
          {
            path: 'groups/:groupId',
            lazy: async () => ({
              Component: (await import('@/features/groups/GroupPage'))
                .GroupPage,
            }),
            handle: GROUPS_ACCESS,
          },
          // Session requests (#59 F1, code-split): guardians and students.
          {
            path: 'requests',
            lazy: async () => ({
              Component: (
                await import('@/features/family-portal/requests/MyRequestsPage')
              ).MyRequestsPage,
            }),
            handle: SESSION_REQUESTS_ACCESS,
          },
          {
            path: 'requests/new',
            lazy: async () => ({
              Component: (
                await import('@/features/family-portal/requests/RequestSessionPage')
              ).RequestSessionPage,
            }),
            handle: SESSION_REQUESTS_ACCESS,
          },
          // #67 E1: My schedule (code-split, FullCalendar): tutors, students
          // and guardians; the API's record policies decide.
          {
            path: 'my-schedule',
            lazy: async () => ({
              Component: (await import('@/features/my-schedule/MySchedulePage'))
                .MySchedulePage,
            }),
            handle: MY_SCHEDULE_ACCESS,
          },
          // #76 G1: the tutor takes a session's attendance (code-split; the
          // API's record policy decides: the current tutor only).
          {
            path: 'my-schedule/sessions/:sessionId/attendance',
            lazy: async () => ({
              Component: (
                await import('@/features/tutor-portal/attendance/AttendancePage')
              ).AttendancePage,
            }),
            handle: { role: 'tutor' },
          },
          // #77 G1: the tutor closes out a session (code-split; the API's
          // record policy decides: the current tutor only).
          {
            path: 'my-schedule/sessions/:sessionId/closeout',
            lazy: async () => ({
              Component: (
                await import('@/features/tutor-portal/closeout/CloseoutPage')
              ).CloseoutPage,
            }),
            handle: { role: 'tutor' },
          },
          // #79 H1: the tutor's notes about a session's students
          // (code-split; the API's record policy decides: the current tutor).
          {
            path: 'my-schedule/sessions/:sessionId/notes',
            lazy: async () => ({
              Component: (
                await import('@/features/tutor-portal/notes/NotesPage')
              ).NotesPage,
            }),
            handle: { role: 'tutor' },
          },
          // #62b B1: My change requests (code-split): guardians, students
          // and tutors; the API's record policy decides.
          {
            path: 'change-requests',
            lazy: async () => ({
              Component: (
                await import('@/features/change-requests/ChangeRequestsPage')
              ).ChangeRequestsPage,
            }),
            handle: CHANGE_REQUESTS_ACCESS,
          },
          // #308 A1: evaluation templates (evaluation.manage), a student's
          // academic history (staff and tutors; every role holds
          // student.view, the API decides), the tutor's new-evaluation form,
          // one evaluation (tutors and evaluation.manage holders; the API's
          // record policy decides) and the family's history (students and
          // guardians). All code-split.
          {
            path: 'academics/evaluation-templates',
            lazy: async () => ({
              Component: (
                await import('@/features/academics/templates/TemplatesPage')
              ).TemplatesPage,
            }),
            handle: { permission: 'evaluation.manage' },
          },
          {
            path: 'academics/evaluation-templates/new',
            lazy: async () => ({
              Component: (
                await import('@/features/academics/templates/TemplateEditorPage')
              ).NewTemplatePage,
            }),
            handle: { permission: 'evaluation.manage' },
          },
          {
            path: 'academics/evaluation-templates/:templateId',
            lazy: async () => ({
              Component: (
                await import('@/features/academics/templates/TemplateEditorPage')
              ).TemplateEditPage,
            }),
            handle: { permission: 'evaluation.manage' },
          },
          {
            path: 'students/:studentId/history',
            lazy: async () => ({
              Component: (
                await import('@/features/academics/history/StudentHistoryPage')
              ).StudentHistoryPage,
            }),
            handle: { permission: 'student.view' },
          },
          {
            path: 'students/:studentId/evaluations/new',
            lazy: async () => ({
              Component: (
                await import('@/features/academics/evaluations/EvaluationFormPage')
              ).NewEvaluationPage,
            }),
            handle: { role: 'tutor' },
          },
          {
            path: 'evaluations/:evaluationId',
            lazy: async () => ({
              Component: (
                await import('@/features/academics/evaluations/EvaluationFormPage')
              ).EvaluationPage,
            }),
            handle: {
              anyOf: [{ role: 'tutor' }, { permission: 'evaluation.manage' }],
            },
          },
          {
            path: 'my-history',
            lazy: async () => ({
              Component: (
                await import('@/features/academics/history/MyHistoryPage')
              ).MyHistoryPage,
            }),
            handle: MY_HISTORY_ACCESS,
          },
          // #310 A1: learning plans. The staff and tutor URLs serve
          // learning_plan.manage holders and tutor-role users (the API's
          // plan record policy decides); a student or guardian who opens one
          // is sent to the family page (#308 QA D2), so their handle lets
          // the family roles through. The family pages serve students and
          // guardians.
          {
            path: 'students/:studentId/learning-plans',
            lazy: async () => ({
              Component: (
                await import('@/features/learning-plans/LearningPlansListPage')
              ).LearningPlansListPage,
            }),
            handle: LEARNING_PLAN_ROUTE_ACCESS,
          },
          {
            path: 'students/:studentId/learning-plans/new',
            lazy: async () => ({
              Component: (
                await import('@/features/learning-plans/LearningPlanEditorPage')
              ).NewLearningPlanPage,
            }),
            handle: LEARNING_PLAN_ROUTE_ACCESS,
          },
          {
            path: 'learning-plans/:planId',
            lazy: async () => ({
              Component: (
                await import('@/features/learning-plans/LearningPlanPage')
              ).LearningPlanPage,
            }),
            handle: LEARNING_PLAN_ROUTE_ACCESS,
          },
          {
            path: 'learning-plans/:planId/edit',
            lazy: async () => ({
              Component: (
                await import('@/features/learning-plans/LearningPlanEditorPage')
              ).EditLearningPlanPage,
            }),
            handle: LEARNING_PLAN_ROUTE_ACCESS,
          },
          {
            path: 'my-learning-plans',
            lazy: async () => ({
              Component: (
                await import('@/features/learning-plans/MyLearningPlansPage')
              ).MyLearningPlansPage,
            }),
            handle: MY_LEARNING_PLANS_ACCESS,
          },
          {
            path: 'my-learning-plans/:planId',
            lazy: async () => ({
              Component: (
                await import('@/features/learning-plans/MyLearningPlanPage')
              ).MyLearningPlanPage,
            }),
            handle: MY_LEARNING_PLANS_ACCESS,
          },
          // #313 A1: homework. The working URLs (the list, a roster, a
          // student's homework) serve tutors and assignment.review holders
          // (the API's record policy decides); a student or guardian who
          // opens one is sent to /my-homework (#308 QA D2, as #310), so
          // their handle lets the family roles through. The form is for
          // tutors only; the family pages serve students and guardians.
          {
            path: 'assignments',
            lazy: async () => ({
              Component: (
                await import('@/features/assignments/AssignmentsPage')
              ).AssignmentsPage,
            }),
            handle: ASSIGNMENT_ROUTE_ACCESS,
          },
          {
            path: 'assignments/new',
            lazy: async () => ({
              Component: (
                await import('@/features/assignments/AssignmentFormPage')
              ).NewAssignmentPage,
            }),
            handle: ASSIGNMENT_FORM_ACCESS,
          },
          {
            path: 'assignments/:assignmentId',
            lazy: async () => ({
              Component: (await import('@/features/assignments/RosterPage'))
                .RosterPage,
            }),
            handle: ASSIGNMENT_ROUTE_ACCESS,
          },
          {
            path: 'assignments/:assignmentId/edit',
            lazy: async () => ({
              Component: (
                await import('@/features/assignments/AssignmentFormPage')
              ).EditAssignmentPage,
            }),
            handle: ASSIGNMENT_FORM_ACCESS,
          },
          {
            path: 'students/:studentId/assignments',
            lazy: async () => ({
              Component: (
                await import('@/features/assignments/StudentAssignmentsPage')
              ).StudentAssignmentsPage,
            }),
            handle: ASSIGNMENT_ROUTE_ACCESS,
          },
          {
            path: 'students/:studentId/assignments/:assignmentId',
            lazy: async () => ({
              Component: (
                await import('@/features/assignments/StudentAssignmentsPage')
              ).StudentAssignmentPage,
            }),
            handle: ASSIGNMENT_ROUTE_ACCESS,
          },
          {
            path: 'my-homework',
            lazy: async () => ({
              Component: (await import('@/features/assignments/MyHomeworkPage'))
                .MyHomeworkPage,
            }),
            handle: MY_HOMEWORK_ACCESS,
          },
          {
            path: 'my-homework/:assignmentId',
            lazy: async () => ({
              Component: (
                await import('@/features/assignments/MyHomeworkDetailPage')
              ).MyHomeworkDetailPage,
            }),
            handle: MY_HOMEWORK_ACCESS,
          },
          // #315 A1: progress reports. The working URLs (the list and
          // review queue, a report, a student's reports, an approved report)
          // serve tutors and progress_report.review holders (the API's
          // record policy decides); a student or guardian who opens one is
          // sent to /my-progress-reports (#308 QA D2, as #310 and #313), so
          // their handle lets the family roles through. The start step is
          // for tutors only; the family pages serve students and guardians.
          {
            path: 'progress-reports',
            lazy: async () => ({
              Component: (
                await import('@/features/progress-reports/ReportsPage')
              ).ReportsPage,
            }),
            handle: PROGRESS_REPORT_ROUTE_ACCESS,
          },
          {
            path: 'progress-reports/:reportId',
            lazy: async () => ({
              Component: (
                await import('@/features/progress-reports/ReportPage')
              ).ReportPage,
            }),
            handle: PROGRESS_REPORT_ROUTE_ACCESS,
          },
          {
            path: 'students/:studentId/progress-reports',
            lazy: async () => ({
              Component: (
                await import('@/features/progress-reports/StudentReportsPage')
              ).StudentReportsPage,
            }),
            handle: PROGRESS_REPORT_ROUTE_ACCESS,
          },
          {
            path: 'students/:studentId/progress-reports/new',
            lazy: async () => ({
              Component: (
                await import('@/features/progress-reports/StartReportPage')
              ).StartReportPage,
            }),
            handle: PROGRESS_REPORT_START_ACCESS,
          },
          {
            path: 'students/:studentId/progress-reports/:reportId',
            lazy: async () => ({
              Component: (
                await import('@/features/progress-reports/StudentReportPage')
              ).StudentReportPage,
            }),
            handle: PROGRESS_REPORT_ROUTE_ACCESS,
          },
          {
            path: 'my-progress-reports',
            lazy: async () => ({
              Component: (
                await import('@/features/progress-reports/MyReportsPage')
              ).MyReportsPage,
            }),
            handle: MY_PROGRESS_REPORTS_ACCESS,
          },
          {
            path: 'my-progress-reports/:reportId',
            lazy: async () => ({
              Component: (
                await import('@/features/progress-reports/MyReportPage')
              ).MyReportPage,
            }),
            handle: MY_PROGRESS_REPORTS_ACCESS,
          },
          // #322 A2: the progress dashboards (code-split; Recharts stays in
          // their chunk, A3). The staff URL serves evaluation.manage holders
          // and tutors (the API's progress policy decides); a student or
          // guardian who opens it is sent to /my-progress (#308 QA D2, as
          // #310, #313 and #315), so its handle lets the family roles
          // through. The family page serves students and guardians.
          {
            path: 'students/:studentId/progress',
            lazy: async () => ({
              Component: (
                await import('@/features/progress/StudentProgressPage')
              ).StudentProgressPage,
            }),
            handle: STUDENT_PROGRESS_ACCESS,
          },
          {
            path: 'my-progress',
            lazy: async () => ({
              Component: (await import('@/features/progress/MyProgressPage'))
                .MyProgressPage,
            }),
            handle: MY_PROGRESS_ACCESS,
          },
          // The price catalog (#363 A1, code-split): billing.manage holders
          // (Owner, Administrator, Finance Staff).
          {
            path: 'billing/prices',
            lazy: async () => ({
              Component: (await import('@/features/billing/pricing/PricesPage'))
                .PricesPage,
            }),
            handle: { permission: 'billing.manage' },
          },
          {
            path: 'billing/prices/:priceId',
            lazy: async () => ({
              Component: (await import('@/features/billing/pricing/PricePage'))
                .PricePage,
            }),
            handle: { permission: 'billing.manage' },
          },
          // Any signed-in user (#30 K1): no permission.
          { path: 'account/password', element: <ChangePasswordPage /> },
          // Any signed-in user, before and after accepting (#174 G1).
          {
            id: PRIVACY_NOTICE_ROUTE_ID,
            path: 'privacy-notice',
            element: <PrivacyNoticePage />,
          },
          { path: '*', element: <NotFoundPage /> },
        ],
      },
    ],
  },
];

export function createAppRouter() {
  return createBrowserRouter(routes);
}
