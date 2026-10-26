import { useInfiniteQuery } from '@tanstack/react-query';
import {
  approvedListKey,
  fetchApprovedList,
  fetchWorkingList,
  PAGE_SIZE,
  workingListKey,
  type ApprovedReportItem,
  type ApprovedReportPage,
  type ProgressReportListItem,
  type WorkingFilters,
} from './api';
import type { PagedState } from './ReportCards';

// The paged list queries of the progress report pages (#315 E1, F2, F4):
// "Load more" follows next_cursor.

export function useWorkingList(filters: WorkingFilters, enabled = true) {
  const query = useInfiniteQuery({
    queryKey: workingListKey(filters),
    queryFn: ({ pageParam, signal }) =>
      fetchWorkingList(filters, pageParam, signal),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    retry: false,
    enabled,
    placeholderData: (previous) => previous,
  });
  const state: PagedState<ProgressReportListItem> = {
    status: query.data ? 'ready' : query.isError ? 'error' : 'loading',
    error: query.error,
    items: query.data?.pages.flatMap((page) => page.items) ?? [],
    hasMore: query.hasNextPage,
    loadingMore: query.isFetchingNextPage,
    onRetry: () => void query.refetch(),
    onMore: () => void query.fetchNextPage(),
  };
  return { query, state };
}

export function useApprovedList(studentId: string, enabled = true) {
  const query = useInfiniteQuery({
    queryKey: approvedListKey(studentId),
    queryFn: ({ pageParam, signal }) =>
      fetchApprovedList(studentId, pageParam, PAGE_SIZE, signal),
    initialPageParam: null as string | null,
    getNextPageParam: (last: ApprovedReportPage) =>
      last.next_cursor ?? undefined,
    retry: false,
    enabled,
  });
  const first = query.data?.pages[0];
  const state: PagedState<ApprovedReportItem> = {
    status: query.data ? 'ready' : query.isError ? 'error' : 'loading',
    error: query.error,
    items: query.data?.pages.flatMap((page) => page.items) ?? [],
    hasMore: query.hasNextPage,
    loadingMore: query.isFetchingNextPage,
    onRetry: () => void query.refetch(),
    onMore: () => void query.fetchNextPage(),
  };
  return { query, state, canCreate: first?.can_create ?? false };
}
