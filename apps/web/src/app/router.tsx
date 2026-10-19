import { createBrowserRouter, type RouteObject } from 'react-router';
import { AuthProvider } from '@/features/auth/AuthProvider';
import { LoginPage } from '@/features/auth/LoginPage';
import { HomePage } from '@/pages/HomePage';
import { NotFoundPage } from '@/pages/NotFoundPage';
import { RootErrorPage } from '@/pages/RootErrorPage';
import { RouteErrorPage } from '@/pages/RouteErrorPage';
import { AppLayout } from './layout/AppLayout';
import { RouteGuard } from './RouteGuard';

// React Router in data mode. Server data is fetched with TanStack Query
// hooks, never with route loaders or actions (architecture §8, ISSUE-X4).
// No SPA route starts with /api or /health: the dev server proxies those to
// the API.
//
// Access (#28 F1, UX only: the API checks every request): /login and the
// error elements are the only things outside the guard. Every other page goes
// under the guard route and needs a session; a page that needs a permission
// declares `handle: { permission: '…' }`. A unit test fails if a page is
// added outside the guard.
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
      // Public: the only page outside the guard.
      {
        id: 'login',
        path: 'login',
        element: <LoginPage />,
        errorElement: <RouteErrorPage />,
      },
      {
        id: 'guard',
        element: <RouteGuard />,
        // Errors in a page: rendered inside the layout.
        errorElement: <RouteErrorPage />,
        children: [
          { index: true, element: <HomePage /> },
          { path: '*', element: <NotFoundPage /> },
        ],
      },
    ],
  },
];

export function createAppRouter() {
  return createBrowserRouter(routes);
}
