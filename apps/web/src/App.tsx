import { QueryClientProvider, type QueryClient } from '@tanstack/react-query';
import type { DataRouter } from 'react-router';
import { RouterProvider } from 'react-router/dom';
import { AppErrorBoundary } from '@/app/AppErrorBoundary';
import { ThemeProvider } from '@/theme/ThemeProvider';

interface AppProps {
  router: DataRouter;
  queryClient: QueryClient;
}

// Providers around the router. main.tsx creates the one router and query
// client; tests pass a memory router and a fresh client.
export default function App({ router, queryClient }: AppProps) {
  return (
    <AppErrorBoundary>
      <ThemeProvider>
        <QueryClientProvider client={queryClient}>
          <RouterProvider router={router} />
        </QueryClientProvider>
      </ThemeProvider>
    </AppErrorBoundary>
  );
}
