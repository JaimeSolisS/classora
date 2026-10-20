import { useTranslation } from 'react-i18next';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { Spinner } from '@/components/feedback/Spinner';
import { Button } from '@/components/ui/button';
import { useFormatters } from '@/lib/formatters';
import { cn } from '@/lib/utils';
import type { NotificationItem } from './api';
import { notificationMessage } from './messages';

export type NotificationListState =
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | {
      status: 'ready';
      items: NotificationItem[];
      hasMore: boolean;
      loadingMore: boolean;
      onLoadMore: () => void;
    };

interface NotificationPanelProps {
  headingId: string;
  list: NotificationListState;
  onActivate: (item: NotificationItem) => void;
  onMarkAll: () => void;
  markingAll?: boolean;
  /** A failed "mark read" or "mark all as read". */
  actionError?: unknown;
}

// The bell's panel (#41 E3): the heading, the newest notifications with their
// message, time (center zone) and an unread marker in text, "Load more",
// "Mark all as read", and the empty, loading and error states.
export function NotificationPanel({
  headingId,
  list,
  onActivate,
  onMarkAll,
  markingAll = false,
  actionError,
}: NotificationPanelProps) {
  const { t, i18n } = useTranslation();
  const { formatInstant } = useFormatters();
  const anyUnread =
    list.status === 'ready' && list.items.some((item) => !item.read_at);

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 id={headingId} className="text-base font-semibold">
          {t('notifications.title')}
        </h2>
        {list.status === 'ready' && list.items.length > 0 ? (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            disabled={!anyUnread || markingAll}
            onClick={onMarkAll}
          >
            {markingAll ? (
              <Spinner label={t('notifications.markingAll')} showLabel />
            ) : (
              t('notifications.markAll')
            )}
          </Button>
        ) : null}
      </div>
      {actionError ? <ErrorAlert error={actionError} /> : null}
      {list.status === 'loading' ? (
        <LoadingState rows={3} label={t('notifications.loading')} />
      ) : list.status === 'error' ? (
        <ErrorAlert error={list.error} onRetry={list.onRetry} />
      ) : list.items.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          {t('notifications.empty')}
        </p>
      ) : (
        <>
          <ul className="-mx-2 flex max-h-96 flex-col overflow-y-auto">
            {list.items.map((item) => {
              const unread = !item.read_at;
              return (
                <li key={item.id}>
                  <button
                    type="button"
                    data-unread={unread}
                    onClick={() => onActivate(item)}
                    className={cn(
                      'flex w-full min-h-11 flex-col items-start gap-0.5 rounded-md px-2 py-2 text-left text-sm outline-none hover:bg-muted focus-visible:ring-3 focus-visible:ring-ring',
                      unread && 'font-medium',
                    )}
                  >
                    <span className="flex w-full items-start gap-2">
                      {unread ? (
                        <span
                          aria-hidden="true"
                          className="mt-1.5 size-2 shrink-0 rounded-full bg-primary"
                        />
                      ) : null}
                      <span className="min-w-0 flex-1 break-words">
                        {notificationMessage(t, i18n, item)}
                      </span>
                    </span>
                    <span className="flex gap-2 text-xs font-normal text-muted-foreground">
                      <time dateTime={item.created_at}>
                        {formatInstant(item.created_at, 'dateTime')}
                      </time>
                      <span>
                        {unread
                          ? t('notifications.unread')
                          : t('notifications.read')}
                      </span>
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
          {list.hasMore ? (
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={list.loadingMore}
              onClick={list.onLoadMore}
            >
              {list.loadingMore ? (
                <Spinner label={t('notifications.loadingMore')} showLabel />
              ) : (
                t('notifications.loadMore')
              )}
            </Button>
          ) : null}
        </>
      )}
    </div>
  );
}
