import {
  useInfiniteQuery,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query';
import { Bell } from 'lucide-react';
import { useId, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router';
import { Button } from '@/components/ui/button';
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover';
import { useSession } from '@/features/auth/useSession';
import {
  applyRead,
  applyReadAll,
  fetchNotifications,
  fetchUnreadCount,
  loadedItems,
  markAllNotificationsRead,
  markNotificationRead,
  NOTIFICATION_POLL_MS,
  NOTIFICATIONS_LIST_KEY,
  UNREAD_COUNT_KEY,
  type NotificationItem,
} from './api';
import { badgeText, NOTIFICATION_LINKS } from './messages';
import {
  NotificationPanel,
  type NotificationListState,
} from './NotificationPanel';
import { useLiveNotifications, type LiveState } from './useLiveNotifications';

interface NotificationBellViewProps {
  count: number;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  headingId: string;
  children: ReactNode;
  /** The live stream's state (#42), exposed as `data-live` on the bell. */
  live?: LiveState;
}

// The bell button and its popover, without data (stories use it directly).
// The accessible name is "Notifications", plus "(3 unread)" when there are
// unread ones; the badge is visual only.
export function NotificationBellView({
  count,
  open,
  onOpenChange,
  headingId,
  children,
  live,
}: NotificationBellViewProps) {
  const { t } = useTranslation();
  const label =
    count > 0
      ? t('notifications.bellUnread', { unread: badgeText(count) })
      : t('notifications.bell');
  return (
    <Popover open={open} onOpenChange={onOpenChange}>
      <span
        data-slot="notification-bell"
        data-live={live}
        className="relative inline-flex"
      >
        <PopoverTrigger asChild>
          <Button
            type="button"
            variant="outline"
            size="icon"
            aria-label={label}
          >
            <Bell aria-hidden="true" />
          </Button>
        </PopoverTrigger>
        {/* Outside the button: its text would not match the button's name. */}
        {count > 0 ? (
          <span
            aria-hidden="true"
            data-slot="notification-badge"
            className="pointer-events-none absolute -top-1.5 -right-1.5 inline-flex h-5 min-w-5 items-center justify-center rounded-full bg-primary px-1 text-xs leading-none font-semibold text-primary-foreground"
          >
            {badgeText(count)}
          </span>
        ) : null}
      </span>
      <PopoverContent
        align="end"
        aria-labelledby={headingId}
        className="w-80 p-3"
      >
        {children}
      </PopoverContent>
    </Popover>
  );
}

// The header bell (#41 E1–E3), for a signed-in user who accepted the privacy
// notice. New notifications arrive live over the stream (#42,
// useLiveNotifications), which invalidates the count and the list. While the
// stream isn't open, the unread count is polled every NOTIFICATION_POLL_MS
// while the tab is visible (TanStack Query pauses the interval in a hidden
// tab); it is refetched on focus either way. The panel loads the newest
// notifications when it opens.
export function NotificationBell() {
  const session = useSession();
  const enabled =
    session.status === 'signedIn' && !session.user.privacy_notice_required;
  if (!enabled) {
    return null;
  }
  return <ConnectedBell />;
}

function ConnectedBell() {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const headingId = useId();
  const [open, setOpen] = useState(false);
  const [markingAll, setMarkingAll] = useState(false);
  const [actionError, setActionError] = useState<unknown>(null);
  const live = useLiveNotifications();

  const count = useQuery({
    queryKey: UNREAD_COUNT_KEY,
    queryFn: ({ signal }) => fetchUnreadCount(signal),
    // Polling is the fallback: off while the live stream is open.
    refetchInterval: live === 'open' ? false : NOTIFICATION_POLL_MS,
    refetchOnWindowFocus: true,
  });

  const list = useInfiniteQuery({
    queryKey: NOTIFICATIONS_LIST_KEY,
    queryFn: ({ pageParam, signal }) => fetchNotifications(pageParam, signal),
    initialPageParam: null as string | null,
    getNextPageParam: (page) => page.next_cursor,
    enabled: open,
  });

  function onOpenChange(next: boolean) {
    setOpen(next);
    setActionError(null);
    if (next) {
      // Always the newest when the panel opens.
      void queryClient.resetQueries({ queryKey: NOTIFICATIONS_LIST_KEY });
    }
  }

  async function onActivate(item: NotificationItem) {
    setActionError(null);
    const link = NOTIFICATION_LINKS[item.notification_type]?.(item.params);
    try {
      if (!item.read_at) {
        applyRead(queryClient, await markNotificationRead(item.id));
      }
    } catch (error) {
      setActionError(error);
      return;
    }
    if (link) {
      setOpen(false);
      void navigate(link);
    }
  }

  async function onMarkAll() {
    const newest = loadedItems(list.data)[0];
    if (!newest) {
      return;
    }
    setActionError(null);
    setMarkingAll(true);
    try {
      await markAllNotificationsRead(newest.id);
      applyReadAll(queryClient, new Date().toISOString());
    } catch (error) {
      setActionError(error);
    } finally {
      setMarkingAll(false);
    }
  }

  const state: NotificationListState =
    list.status === 'pending'
      ? { status: 'loading' }
      : list.status === 'error'
        ? {
            status: 'error',
            error: list.error,
            onRetry: () => void list.refetch(),
          }
        : {
            status: 'ready',
            items: loadedItems(list.data),
            hasMore: list.hasNextPage,
            loadingMore: list.isFetchingNextPage,
            onLoadMore: () => void list.fetchNextPage(),
          };

  return (
    <NotificationBellView
      count={count.data ?? 0}
      open={open}
      onOpenChange={onOpenChange}
      headingId={headingId}
      live={live}
    >
      <NotificationPanel
        headingId={headingId}
        list={state}
        onActivate={(item) => void onActivate(item)}
        onMarkAll={() => void onMarkAll()}
        markingAll={markingAll}
        actionError={actionError}
      />
    </NotificationBellView>
  );
}
