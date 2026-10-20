import {
  QueryClient,
  QueryClientProvider,
  type QueryKey,
} from '@tanstack/react-query';
import { useState, type ReactNode } from 'react';

// Storybook only: a fresh query client for a story whose component runs its
// own queries (the #34 search pickers). The query functions are story stubs,
// so nothing reaches the network. `data` seeds queries that would otherwise
// call the API (the header bell's unread count, #41): with `staleTime:
// Infinity` a seeded query never fetches.
export function StoryQueryClient({
  children,
  data = [],
}: {
  children: ReactNode;
  data?: ReadonlyArray<readonly [QueryKey, unknown]>;
}) {
  const [client] = useState(() => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: Infinity } },
    });
    for (const [key, value] of data) {
      queryClient.setQueryData(key, value);
    }
    return queryClient;
  });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
