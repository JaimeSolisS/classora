import { createBrowserRouter, type RouteObject } from 'react-router';
import { AuthProvider } from '@/features/auth/AuthProvider';
import { LoginPage } from '@/features/auth/LoginPage';
import { LevelPage } from '@/features/catalog/LevelPage';
import { LevelsPage } from '@/features/catalog/LevelsPage';
import { ChangePasswordPage } from '@/features/account/password/ChangePasswordPage';
import { InvitePage, ResetPage } from '@/features/auth/redeem/RedeemLinkPage';
import { RegisterPage } from '@/features/auth/register/RegisterPage';
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
import { PrivacyNoticePage } from '@/features/privacy-notice/PrivacyNoticePage';
import { StudentDetailPage } from '@/features/students/StudentDetailPage';
import {
  EditStudentPage,
  NewStudentPage,
} from '@/features/students/StudentFormPages';
import { StudentsPage } from '@/features/students/StudentsPage';
import { InvitationsPage } from '@/features/users/invitations/InvitationsPage';
import { PasswordResetsPage } from '@/features/users/password-resets/PasswordResetsPage';
import { RegistrationRequestsPage } from '@/features/users/registrations/RegistrationRequestsPage';
import { UserDetailPage } from '@/features/users/admin/UserDetailPage';
import { UsersPage } from '@/features/users/admin/UsersPage';
import { HomePage } from '@/pages/HomePage';
import { NotFoundPage } from '@/pages/NotFoundPage';
import { RootErrorPage } from '@/pages/RootErrorPage';
import { RouteErrorPage } from '@/pages/RouteErrorPage';
import { AppLayout } from './layout/AppLayout';
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
// declares `handle: { permission: '…' }`. A unit test fails if a page is
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
            path: 'registration-requests',
            element: <RegistrationRequestsPage />,
            handle: { permission: 'registration.review' },
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
