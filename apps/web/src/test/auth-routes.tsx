import type { RouteObject } from 'react-router';
import { api } from '@/app/api';
import { AppLayout } from '@/app/layout/AppLayout';
import type { NavItem } from '@/app/navigation';
import { routes } from '@/app/router';
import { AuthProvider } from '@/features/auth/AuthProvider';

// Test-only routes for the auth tests (#28): the real route tree with extra
// pages under the guard (and, optionally, other navigation items), and a raw
// call to any path through the app's client. The pages: auth-pages.tsx.

type RawRequest = (
  method: string,
  path: string,
) => Promise<{ data?: unknown; error?: unknown; response: Response }>;

/** A call to any method and path through the app's client (and middleware). */
export function rawCall(method: string, path: string) {
  const request = api.request as unknown as RawRequest;
  return () => request(method.toLowerCase(), path);
}

/** The real route tree with `pages` added under the guard. */
export function routesWith(
  pages: RouteObject[],
  navItems?: readonly NavItem[],
): RouteObject[] {
  const [root] = routes;
  const children = root!.children!.map((child) =>
    child.id === 'guard'
      ? { ...child, children: [...pages, ...child.children!] }
      : child,
  );
  return [
    {
      ...root,
      ...(navItems
        ? {
            element: (
              <AuthProvider>
                <AppLayout navItems={navItems} />
              </AuthProvider>
            ),
          }
        : {}),
      children,
    } as RouteObject,
  ];
}
