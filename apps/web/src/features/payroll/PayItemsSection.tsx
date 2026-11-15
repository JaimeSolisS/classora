import {
  useInfiniteQuery,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query';
import { useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useLocation } from 'react-router';
import { ApiError } from '@/app/api';
import type { LocalDate } from '@/lib/time';
import {
  createPayItem,
  deletePayItem,
  endPayItem,
  fetchPayItems,
  fetchPayItemTypes,
  PAY_ITEM_TYPES_KEY,
  PAY_ITEMS_ANCHOR,
  payItemsKey,
  type PayItem,
  type PayItemCreate,
} from './api';
import { PayItemDialog, type PayItemTypeOptions } from './PayItemDialog';
import { PayItemEndDialog } from './PayItemEndDialog';
import { PayItemsView, type PayItemsState } from './PayItemsView';

// The items section of /payroll/tutors/:tutorUserId (#454 B). It lives outside
// CompensationView, which /my-pay shares: a tutor sees no items here (#113).
// "Load more" walks the API's offset; a change refreshes the items and nothing
// else (B8), and refetches every page already shown. Closing the add dialog
// after a failed attempt refreshes too (QA #454 D2). A refresh that fails keeps
// the old rows, so the section says so, with a retry, until a full refetch
// succeeds (D4, D5). The types load only when the add dialog opens.

export interface PayItemsSectionProps {
  tutorUserId: string;
  /** payroll.manage, and not a non-Owner's own pay. */
  canEdit: boolean;
  today: LocalDate;
}

export function PayItemsSection({
  tutorUserId,
  canEdit,
  today,
}: PayItemsSectionProps) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { hash } = useLocation();
  const [adding, setAdding] = useState(false);
  const [ending, setEnding] = useState<PayItem | null>(null);
  const [announcement, setAnnouncement] = useState('');
  // A gone item whose reload failed: said until a full refetch succeeds.
  const [goneNotReloaded, setGoneNotReloaded] = useState(false);
  // The list may be out of date: the error of a full refetch, kept until a
  // full refetch succeeds. The query's own status won't do, because "Load
  // more" resets it having read one page only (QA #454 D5).
  const [staleError, setStaleError] = useState<unknown>(null);
  const [focusRequest, setFocusRequest] = useState(0);
  // A failed add may still have been stored (a lost response): the list must
  // show it before anyone adds it again, however the dialog closes.
  const addFailed = useRef(false);

  const list = useInfiniteQuery({
    queryKey: payItemsKey(tutorUserId),
    queryFn: ({ pageParam, signal }) =>
      fetchPayItems(tutorUserId, pageParam, signal),
    initialPageParam: 0,
    getNextPageParam: (page) => {
      const next = page.offset + page.items.length;
      return page.items.length > 0 && next < page.total ? next : undefined;
    },
  });
  const typesQuery = useQuery({
    queryKey: PAY_ITEM_TYPES_KEY,
    queryFn: ({ signal }) => fetchPayItemTypes(signal),
    enabled: adding,
  });

  if (list.isRefetchError && staleError !== list.error) {
    setStaleError(list.error);
  }
  // Adjusting state while rendering. A query remembers what its last fetch
  // was, so a finished "Load more" is never taken for a full refetch.
  const lastFetch = queryClient.getQueryState(payItemsKey(tutorUserId));
  const fullRefetchDone =
    list.isSuccess && !list.isFetching && !lastFetch?.fetchMeta;
  if (staleError && fullRefetchDone) {
    setStaleError(null);
    if (goneNotReloaded) {
      announce(t('payroll.payItems.gone'));
    }
  }

  // Pages read at different times can repeat a row that moved across them.
  const items = useMemo(() => {
    const byId = new Map<string, PayItem>();
    for (const page of list.data?.pages ?? []) {
      for (const item of page.items) {
        byId.set(item.id, byId.get(item.id) ?? item);
      }
    }
    return [...byId.values()];
  }, [list.data]);
  const state: PayItemsState = list.data
    ? {
        status: 'ready',
        items,
        hasMore: list.hasNextPage,
        loadingMore: list.isFetchingNextPage,
        // Not while "Load more" runs again: its retry is pending (#458 D1).
        moreError:
          list.isFetchNextPageError && !list.isFetchingNextPage
            ? list.error
            : null,
        // A running refetch must finish, not be cancelled by this.
        onMore: () => void list.fetchNextPage({ cancelRefetch: false }),
        refreshError: staleError,
        onRefresh: () => void list.refetch(),
      }
    : list.isError
      ? {
          status: 'error',
          error: list.error,
          onRetry: () => void list.refetch(),
        }
      : { status: 'loading' };

  const typeOptions: PayItemTypeOptions = typesQuery.data
    ? { status: 'ready', types: typesQuery.data }
    : typesQuery.isError
      ? {
          status: 'error',
          error: typesQuery.error,
          onRetry: () => void typesQuery.refetch(),
        }
      : { status: 'loading' };

  function announce(text: string) {
    setGoneNotReloaded(false);
    setAnnouncement(text);
  }

  /** Whether the list reloaded: invalidating never rejects. */
  async function refresh(): Promise<boolean> {
    const queryKey = payItemsKey(tutorUserId);
    await queryClient.invalidateQueries({ queryKey });
    return queryClient.getQueryState(queryKey)?.status !== 'error';
  }

  /** The item is gone. The section's notice covers a reload that failed. */
  async function refreshGone() {
    const reloaded = await refresh();
    announce(reloaded ? t('payroll.payItems.gone') : '');
    setGoneNotReloaded(!reloaded);
  }

  async function add(body: PayItemCreate, idempotencyKey: string) {
    try {
      await createPayItem(tutorUserId, body, idempotencyKey);
    } catch (caught) {
      addFailed.current = true;
      throw caught;
    }
    addFailed.current = false;
    await refresh();
  }

  async function end(effectiveTo: string, base: PayItem) {
    try {
      await endPayItem(tutorUserId, base, effectiveTo);
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 404) {
        setEnding(null);
        await refreshGone();
        setFocusRequest((current) => current + 1);
        return;
      }
      throw caught;
    }
    await refresh();
    announce(t('payroll.payItems.ended'));
  }

  async function remove(item: PayItem) {
    announce('');
    try {
      await deletePayItem(tutorUserId, item.id);
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 404) {
        await refreshGone();
        return;
      }
      throw caught;
    }
    await refresh();
    announce(t('payroll.payItems.deleted'));
  }

  // The live row, so a colleague's version reaches an open dialog; the
  // snapshot if it went missing, so the dialog never closes by itself.
  const endingItem = ending
    ? (items.find((item) => item.id === ending.id) ?? ending)
    : null;

  return (
    <>
      <PayItemsView
        state={state}
        focusOnMount={hash === `#${PAY_ITEMS_ANCHOR}`}
        focusRequest={focusRequest}
        actions={
          canEdit
            ? {
                onAdd: () => {
                  announce('');
                  setAdding(true);
                },
                onEnd: (item) => {
                  announce('');
                  setEnding(item);
                },
                onDelete: remove,
              }
            : undefined
        }
        announcement={
          goneNotReloaded ? t('payroll.payItems.goneNotReloaded') : announcement
        }
      />
      {canEdit ? (
        <PayItemDialog
          open={adding}
          types={typeOptions}
          today={today}
          onSubmit={add}
          onReloadTypes={async () => {
            await typesQuery.refetch();
          }}
          onClose={(saved) => {
            setAdding(false);
            if (saved) {
              announce(t('payroll.payItems.saved'));
            }
            if (addFailed.current) {
              addFailed.current = false;
              void refresh();
            }
          }}
        />
      ) : null}
      {canEdit && endingItem ? (
        <PayItemEndDialog
          key={endingItem.id}
          open
          item={endingItem}
          today={today}
          onSubmit={end}
          onReload={async () => {
            await list.refetch();
          }}
          onClose={() => setEnding(null)}
        />
      ) : null}
    </>
  );
}
