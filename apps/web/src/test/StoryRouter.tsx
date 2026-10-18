import { useState } from 'react';
import { createMemoryRouter, type RouteObject } from 'react-router';
import { RouterProvider } from 'react-router/dom';
import { ThemeContext } from '@/theme/use-theme';

interface StoryRouterProps {
  routes: RouteObject[];
  path?: string;
}

// Storybook only: renders routes in a memory router. The theme context is a
// fixed value, so the Storybook toolbar keeps control of the page theme (the
// real ThemeProvider would apply the stored or system theme).
export function StoryRouter({ routes, path = '/' }: StoryRouterProps) {
  const [router] = useState(() =>
    createMemoryRouter(routes, { initialEntries: [path] }),
  );
  return (
    <ThemeContext
      value={{
        choice: 'system',
        resolvedTheme: 'light',
        setChoice: () => {},
      }}
    >
      <RouterProvider router={router} />
    </ThemeContext>
  );
}
