import type { components } from '@classora/api-client';
import type { InfiniteData, QueryClient } from '@tanstack/react-query';
import { api, apiCall } from '@/app/api';

// The notification calls (#41 E). The unread count and the panel's list are
// queries; marking read is a direct call whose answer updates both caches.

export type NotificationItem = components['schemas']['NotificationItem'];
export type NotificationPage =
  components['schemas']['CursorPage_NotificationItem_'];

/** How often the bell asks for the unread count (E2), while the tab is visible. */
export const NOTIFICATION_POLL_MS = 30_000;

/** Notifications per page of the panel. */
export const NOTIFICATIONS_PAGE_SIZE = 10;

export const NOTIFICATIONS_QUERY_KEY = ['notifications'] as const;
export const UNREAD_COUNT_KEY = ['notifications', 'unread-count'] as const;
export const NOTIFICATIONS_LIST_KEY = ['notifications', 'list'] as const;

export type NotificationPages = InfiniteData<NotificationPage, string | null>;

export async function fetchUnreadCount(signal: AbortSignal): Promise<number> {
  const body = await apiCall(
    () => api.GET('/api/v1/notifications/unread-count', { signal }),
    { signal },
  );
  return body.count;
}

export function fetchNotifications(
  cursor: string | null,
  signal: AbortSignal,
): Promise<NotificationPage> {
  return apiCall(
    () =>
      api.GET('/api/v1/notifications', {
        params: {
          query: {
            limit: NOTIFICATIONS_PAGE_SIZE,
            ...(cursor ? { cursor } : {}),
          },
        },
        signal,
      }),
    { signal },
  );
}

export function markNotificationRead(id: string): Promise<NotificationItem> {
  return apiCall(() =>
    api.POST('/api/v1/notifications/{notification_id}/read', {
      params: { path: { notification_id: id } },
    }),
  );
}

/**
 * Marks the unread notifications up to `upTo` (the newest one the panel
 * shows) read; one that arrived meanwhile stays unread (C4).
 */
export async function markAllNotificationsRead(
  upTo: string | null,
): Promise<number> {
  const body = await apiCall(() =>
    api.POST('/api/v1/notifications/read-all', { body: { up_to: upTo } }),
  );
  return body.updated;
}

/** The loaded items, newest first. */
export function loadedItems(
  data: { pages: readonly NotificationPage[] } | undefined,
): NotificationItem[] {
  return data?.pages.flatMap((page) => page.items) ?? [];
}

function updateItems(
  queryClient: QueryClient,
  update: (item: NotificationItem) => NotificationItem,
) {
  queryClient.setQueryData<NotificationPages>(NOTIFICATIONS_LIST_KEY, (data) =>
    data
      ? {
          ...data,
          pages: data.pages.map((page) => ({
            ...page,
            items: page.items.map(update),
          })),
        }
      : data,
  );
}

/** One notification became read: the list shows it and the count drops. */
export function applyRead(queryClient: QueryClient, read: NotificationItem) {
  let wasUnread = false;
  updateItems(queryClient, (item) => {
    if (item.id !== read.id) {
      return item;
    }
    wasUnread = item.read_at === null;
    return read;
  });
  if (wasUnread) {
    queryClient.setQueryData<number>(UNREAD_COUNT_KEY, (count) =>
      count === undefined ? count : Math.max(0, count - 1),
    );
  }
  void queryClient.invalidateQueries({ queryKey: UNREAD_COUNT_KEY });
}

/** Every loaded notification became read (the server marked them too). */
export function applyReadAll(queryClient: QueryClient, readAt: string) {
  updateItems(queryClient, (item) =>
    item.read_at === null ? { ...item, read_at: readAt } : item,
  );
  void queryClient.invalidateQueries({ queryKey: UNREAD_COUNT_KEY });
}
