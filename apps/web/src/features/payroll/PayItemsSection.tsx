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
// the old rows, so the section says so, with a retry (D4). The types load only
// when the add dialog opens.

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

  const items = useMemo(
    () => list.data?.pages.flatMap((page) => page.items) ?? [],
    [list.data],
  );
  const state: PayItemsState = list.data
    ? {
        status: 'ready',
        items,
        hasMore: list.hasNextPage,
        loadingMore: list.isFetchingNextPage,
        moreError: list.isFetchNextPageError ? list.error : null,
        onMore: () => void list.fetchNextPage(),
        refreshError: list.isRefetchError ? list.error : null,
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

  /** Whether the list reloaded: invalidating never rejects. */
  async function refresh(): Promise<boolean> {
    const queryKey = payItemsKey(tutorUserId);
    await queryClient.invalidateQueries({ queryKey });
    return queryClient.getQueryState(queryKey)?.status !== 'error';
  }

  /** The item is gone. The section's notice covers a reload that failed. */
  async function refreshGone() {
    const reloaded = await refresh();
    setAnnouncement(
      reloaded
        ? t('payroll.payItems.gone')
        : t('payroll.payItems.goneNotReloaded'),
    );
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
    setAnnouncement(t('payroll.payItems.ended'));
  }

  async function remove(item: PayItem) {
    setAnnouncement('');
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
    setAnnouncement(t('payroll.payItems.deleted'));
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
                  setAnnouncement('');
                  setAdding(true);
                },
                onEnd: (item) => {
                  setAnnouncement('');
                  setEnding(item);
                },
                onDelete: remove,
              }
            : undefined
        }
        announcement={announcement}
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
              setAnnouncement(t('payroll.payItems.saved'));
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
