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
import { SESSION_REQUESTS_ACCESS } from '@/features/family-portal/requests/access';
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
          // Tutoring groups (#234 A3, code-split): staff with schedule.view,
          // or students (their own groups; the API decides). The create form
          // needs schedule.manage.
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
