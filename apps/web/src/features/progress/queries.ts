import { useInfiniteQuery, useQuery } from '@tanstack/react-query';
import type { ExpectedErrorsMeta } from '@/app/queryClient';
import {
  fetchSessionLog,
  fetchStudentProgress,
  progressKey,
  sessionLogKey,
  type ProgressQuery,
} from './api';
import type { LogState } from './ClassLog';

// The progress pages' reads (#322 B1): the progress summary and the class
// log, both with the page's range and subject. Neither retries a 4xx (the
// app default); `meta` lists the statuses the page shows inline.
// ProgressView is keyed by the student, so a placeholder is always the
// same student's previous range.

export function useStudentProgress(
  studentId: string,
  query: ProgressQuery,
  enabled: boolean,
  meta: ExpectedErrorsMeta,
) {
  return useQuery({
    queryKey: progressKey(studentId, query),
    queryFn: ({ signal }) => fetchStudentProgress(studentId, query, signal),
    enabled,
    meta: { ...meta },
    // The controls stay on screen (and keep focus) while a new range or
    // subject loads; the view is remounted per student, so this never
    // shows another student's data.
    placeholderData: (previous) => previous,
  });
}

export function useSessionLog(
  studentId: string,
  query: ProgressQuery,
  enabled: boolean,
  meta: ExpectedErrorsMeta,
) {
  const log = useInfiniteQuery({
    queryKey: sessionLogKey(studentId, query),
    queryFn: ({ pageParam, signal }) =>
      fetchSessionLog(studentId, query, pageParam, signal),
    initialPageParam: null as string | null,
    getNextPageParam: (page) => page.next_cursor ?? undefined,
    enabled,
    meta: { ...meta },
    placeholderData: (previous) => previous,
  });
  const state: LogState = {
    status: log.data ? 'ready' : log.isError ? 'error' : 'loading',
    error: log.error,
    items: log.data?.pages.flatMap((page) => page.items) ?? [],
    hasMore: log.hasNextPage,
    loadingMore: log.isFetchingNextPage,
    onRetry: () => void log.refetch(),
    onMore: () => log.fetchNextPage(),
  };
  return { log, state };
}
