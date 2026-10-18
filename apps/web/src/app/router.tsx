import { createBrowserRouter, type RouteObject } from 'react-router';
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
export const routes: RouteObject[] = [
  {
    id: 'root',
    path: '/',
    element: <AppLayout />,
    // Errors in the layout itself: rendered without the layout.
    errorElement: <RootErrorPage />,
    children: [
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
